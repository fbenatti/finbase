"""Fase 2 do RBAC: motor de decisão, filtro de linha, campos e regras de negócio.

Funções puras, sem banco.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app.core.access.legacy import access_operation, grants_from_legacy, legacy_scoped_rows  # noqa: E402
from app.core.access.policy import (  # noqa: E402
    BusinessRule,
    Condition,
    Grant,
    RuleCondition,
    ScopeValue,
    compile_postgres,
    expand_subtree,
    decide,
    decide_field,
    evaluate_business_rules,
    visible_fields,
)

HIER = "hierarchy"
BUDGET = "budget"


def screen(resource: str, op: str = "read", effect: str = "allow") -> Grant:
    return Grant("screen", resource, op, effect)


def row_acl(column: str, scope_type: str = HIER, op: str = "read", mode: str = "subtree",
            resource: str = "fact_cost") -> Grant:
    return Grant("table", resource, op, "allow", (Condition(column, scope_type, mode),))


# ---- decisão de tela e tabela ----

def test_sem_acl_nega_por_padrao():
    assert not decide([], "read", "screen", "gold").allowed


def test_allow_sem_condicao_libera_recurso_inteiro():
    d = decide([screen("gold")], "read", "screen", "gold")
    assert d.allowed and d.row_filter is None


def test_operacao_diferente_nao_libera():
    d = decide([screen("gold", "read")], "write", "screen", "gold")
    assert not d.allowed


def test_recurso_diferente_nao_libera():
    assert not decide([screen("gold")], "read", "screen", "planning").allowed


def test_wildcard_libera_qualquer_recurso_do_tipo():
    assert decide([screen("*")], "read", "screen", "planning").allowed


def test_deny_explicito_vence_allow():
    grants = [screen("gold"), screen("gold", effect="deny")]
    d = decide(grants, "read", "screen", "gold")
    assert not d.allowed and "explícita" in d.reason


def test_grant_inativo_nao_conta():
    inactive = Grant("screen", "gold", "read", active=False)
    assert not decide([inactive], "read", "screen", "gold").allowed


def test_tipo_de_recurso_invalido_levanta_erro():
    with pytest.raises(ValueError):
        decide([], "read", "banana", "gold")


def test_operacao_invalida_levanta_erro():
    with pytest.raises(ValueError):
        decide([], "update", "screen", "gold")


# ---- filtro de linha por escopo de hierarquia ----

def test_linha_so_do_nó_do_usuario():
    grants = [row_acl("hierarchy_path")]
    scopes = [ScopeValue(HIER, "TI/Infra")]
    d = decide(grants, "read", "table", "fact_cost", scopes)
    assert d.allowed and d.row_filter is not None
    (clause,) = d.row_filter.clauses
    (pred,) = clause
    assert pred.column == "hierarchy_path"
    assert pred.equal_to == ("TI/Infra",)
    assert pred.under == ("TI/Infra",)


def test_usuario_sem_escopo_do_tipo_e_negado():
    grants = [row_acl("hierarchy_path")]
    d = decide(grants, "read", "table", "fact_cost", [ScopeValue(BUDGET, "TI")])
    assert not d.allowed and "escopo" in d.reason


def test_escopo_exato_nao_vira_subarvore():
    grants = [row_acl("hierarchy_path", mode="exact")]
    scopes = [ScopeValue(HIER, "TI/Infra", match_mode="subtree")]
    (pred,) = decide(grants, "read", "table", "fact_cost", scopes).row_filter.clauses[0]
    assert pred.under == ()


def test_escopo_so_igualdade_do_usuario_nao_vira_subarvore():
    grants = [row_acl("hierarchy_path", mode="subtree")]
    scopes = [ScopeValue(HIER, "TI/Infra", match_mode="exact")]
    (pred,) = decide(grants, "read", "table", "fact_cost", scopes).row_filter.clauses[0]
    assert pred.under == ()
    assert pred.equal_to == ("TI/Infra",)


def test_acls_com_filtro_sao_somadas_com_or():
    grants = [row_acl("hierarchy_path"), row_acl("budget_unit", scope_type=BUDGET)]
    scopes = [ScopeValue(HIER, "TI"), ScopeValue(BUDGET, "UN-7")]
    d = decide(grants, "read", "table", "fact_cost", scopes)
    assert len(d.row_filter.clauses) == 2


def test_condicoes_da_mesma_acl_sao_somadas_com_and():
    grants = [Grant("table", "fact_cost", "read", "allow", (
        Condition("hierarchy_path", HIER),
        Condition("budget_unit", BUDGET, "exact"),
    ))]
    scopes = [ScopeValue(HIER, "TI"), ScopeValue(BUDGET, "UN-7")]
    d = decide(grants, "read", "table", "fact_cost", scopes)
    assert len(d.row_filter.clauses) == 1
    assert len(d.row_filter.clauses[0]) == 2


def test_acl_com_condicao_sem_escopo_nao_contribui_mas_outra_sim():
    grants = [row_acl("budget_unit", scope_type=BUDGET), row_acl("hierarchy_path")]
    d = decide(grants, "read", "table", "fact_cost", [ScopeValue(HIER, "TI")])
    assert d.allowed and len(d.row_filter.clauses) == 1


def test_acl_total_vence_acl_com_filtro():
    grants = [screen("fact_cost", resource="fact_cost") if False else
              Grant("table", "fact_cost", "read", "allow"), row_acl("hierarchy_path")]
    d = decide(grants, "read", "table", "fact_cost", [ScopeValue(HIER, "TI")])
    assert d.allowed and d.row_filter is None


# ---- campos ----

def test_campo_oculto_sai_da_leitura():
    grants = [Grant("field", "fact_cost.valor_fornecedor", "read", "deny"),
              Grant("field", "fact_cost.*", "read", "allow")]
    assert visible_fields(grants, "fact_cost", ["custo", "valor_fornecedor"]) == ["custo"]


def test_wildcard_de_campo_nao_vaza_para_outra_tabela():
    grants = [Grant("field", "fact_cost.*", "read", "allow")]
    assert not decide_field(grants, "read", "orcamento", "aprovado").allowed


def test_campo_so_escrita():
    grants = [Grant("field", "orcamento.aprovado", "write", "allow")]
    assert decide_field(grants, "write", "orcamento", "aprovado").allowed
    assert not decide_field(grants, "read", "orcamento", "aprovado").allowed


# ---- compilação para Postgres ----

def test_sem_filtro_vira_true():
    assert compile_postgres(None) == ("TRUE", {})


def test_sem_clausula_vira_false():
    from app.core.access.policy import RowFilter
    assert compile_postgres(RowFilter(())) == ("FALSE", {})


def test_filtro_subarvore_usa_parametros_e_nao_concatena_valor():
    grants = [row_acl("hierarchy_path")]
    d = decide(grants, "read", "table", "fact_cost", [ScopeValue(HIER, "TI'; DROP TABLE x;--")])
    sql, params = compile_postgres(d.row_filter)
    assert "DROP" not in sql
    assert "hierarchy_path" in sql
    assert any("DROP" in str(v) for v in params.values())


def test_nome_de_coluna_inseguro_e_rejeitado():
    grants = [row_acl("hierarchy_path; drop")]
    d = decide(grants, "read", "table", "fact_cost", [ScopeValue(HIER, "TI")])
    with pytest.raises(ValueError):
        compile_postgres(d.row_filter)


def test_sql_de_subarvore_inclui_igualdade_e_prefixo():
    grants = [row_acl("hierarchy_path")]
    d = decide(grants, "read", "table", "fact_cost", [ScopeValue(HIER, "TI/Infra")])
    sql, params = compile_postgres(d.row_filter)
    assert sql == "(((hierarchy_path = ANY(:acl_1) OR hierarchy_path LIKE ANY(:acl_1_sub))))"
    assert params == {"acl_1": ["TI/Infra"], "acl_1_sub": ["TI/Infra/%"]}


def test_sql_de_clausulas_multiplas_usa_or_entre_clausulas():
    grants = [row_acl("hierarchy_path"), row_acl("budget_unit", scope_type=BUDGET, mode="exact")]
    scopes = [ScopeValue(HIER, "TI"), ScopeValue(BUDGET, "UN-7")]
    sql, _ = compile_postgres(decide(grants, "read", "table", "fact_cost", scopes).row_filter)
    assert " OR " in sql and sql.count("(") >= 3


# ---- regras de negócio ----

def _rule_no_self_approve():
    return BusinessRule(
        code="orc_sem_autoaprovacao",
        event="before_write",
        resource="orcamento",
        conditions=(RuleCondition("editado_por_id", "same_actor"),),
        message="Quem editou não pode aprovar a própria alteração.",
    )


def test_regra_bloqueia_autoaprovacao():
    msg = evaluate_business_rules(
        [_rule_no_self_approve()], "before_write", "orcamento",
        {"editado_por_id": 7}, actor_id=7,
    )
    assert msg and "não pode aprovar" in msg


def test_regra_nao_bloqueia_outro_usuario():
    msg = evaluate_business_rules(
        [_rule_no_self_approve()], "before_write", "orcamento",
        {"editado_por_id": 7}, actor_id=8,
    )
    assert msg is None


def test_regra_so_vale_no_evento_certo():
    msg = evaluate_business_rules(
        [_rule_no_self_approve()], "after_write", "orcamento",
        {"editado_por_id": 7}, actor_id=7,
    )
    assert msg is None


def test_regra_inativa_nao_bloqueia():
    rule = BusinessRule("x", "before_write", "orcamento", (RuleCondition("a", "eq", 1),), "msg", active=False)
    assert evaluate_business_rules([rule], "before_write", "orcamento", {"a": 1}, 1) is None


def test_regra_menor_prioridade_avalia_primeiro():
    low = BusinessRule("a", "before_write", "orcamento", (RuleCondition("a", "eq", 1),), "primeira", priority=1)
    high = BusinessRule("b", "before_write", "orcamento", (RuleCondition("a", "eq", 1),), "segunda", priority=9)
    assert evaluate_business_rules([high, low], "before_write", "orcamento", {"a": 1}, 1) == "primeira"


def test_operador_invalido_levanta_erro():
    rule = BusinessRule("x", "before_write", "orcamento", (RuleCondition("a", "xpto", 1),), "m")
    with pytest.raises(ValueError):
        evaluate_business_rules([rule], "before_write", "orcamento", {"a": 1}, 1)


# ---- ponte com o RBAC legado ----

def test_update_legado_vira_write():
    assert access_operation("update") == "write"
    assert access_operation("read") == "read"


def test_acao_legada_desconhecida_levanta_erro():
    with pytest.raises(ValueError):
        access_operation("admin")


def test_legado_sem_escopo_vira_acl_de_tela():
    rows = [(1, "gold", "read", None), (1, "gold", "update", None)]
    grants = grants_from_legacy(rows)
    assert {(g.resource, g.operation) for g in grants} == {("gold", "read"), ("gold", "write")}
    assert all(g.resource_type == "screen" and g.effect == "allow" for g in grants)


def test_legado_com_escopo_textual_fica_para_revisao():
    rows = [(1, "gold", "read", "aba-x")]
    assert grants_from_legacy(rows) == []
    assert legacy_scoped_rows(rows) == [(1, "gold", "read", "aba-x")]


# ---- expansão de subárvore (hierarquia por id, enquanto não há caminho JSON) ----

def test_expande_subarvore_por_id_do_departamento():
    children = {"1": ["2", "3"], "2": ["4"], "3": [], "4": []}
    out = expand_subtree([ScopeValue(HIER, "2")], children)
    assert {s.value for s in out} == {"2", "4"}
    assert all(s.match_mode == "exact" for s in out)


def test_expansao_ignora_ciclo():
    children = {"1": ["2"], "2": ["1"]}
    out = expand_subtree([ScopeValue(HIER, "1")], children)
    assert {s.value for s in out} == {"1", "2"}


def test_escopo_exato_nao_e_expandido():
    out = expand_subtree([ScopeValue(HIER, "1", match_mode="exact")], {"1": ["2"]})
    assert [s.value for s in out] == ["1"]


def test_subarvore_expandida_gera_filtro_de_igualdade_por_id():
    children = {"10": ["11", "12"]}
    scopes = expand_subtree([ScopeValue(HIER, "10")], children)
    grants = [Grant("table", "fact_cost", "read", "allow", (Condition("department_id", HIER, "exact"),))]
    d = decide(grants, "read", "table", "fact_cost", scopes)
    sql, params = compile_postgres(d.row_filter)
    assert "LIKE" not in sql
    assert sorted(params["acl_1"]) == ["10", "11", "12"]
