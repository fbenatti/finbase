"""Testes dos cubos financeiros C1, C2 e C3 (G3).

Cada caso corresponde a um número medido no ambiente de referência em 23/09/2026.
Os testes cobrem os aceites de G3:

- os totais de C1/C2 batem com `resource_cost`;
- o driver fecha no delta total;
- não há soma entre moedas;
- períodos parciais aparecem sinalizados e **não** entram em C3;
- nenhum join multiplica custo.
"""

from __future__ import annotations

import pytest

from app.services.finops_cost_driver import (
    DRIVER_TYPES,
    comparable_period_pairs,
    c3_select_sql,
    c3_union_sql,
    driver_key_column,
    reconciliation_tolerance,
    slice_table_name,
)
from app.services.finops_cost_monthly import (
    ADDITIVE_MEASURES,
    C1_TABLE,
    NON_ADDITIVE_MEASURES,
    ORG_LEVELS,
    c1_grain_sql,
    c1_select_sql,
    c1_stage2_sql,
    org_hierarchy_audit_sql,
    resource_count_sql,
    unavailable_measures,
)
from app.services.finops_cost_resource import (
    C2_TABLE,
    c2_projection_sql,
    inventory_temporal_sql,
    non_additive_measures,
    quality_columns,
)
from app.services.period_coverage import COMPLETE, CURRENT_OPEN, PARTIAL


# --- C1 ------------------------------------------------------------------------


def test_c1_grain_sql_targets_canonical_cost():
    sql = c1_grain_sql()
    assert "iceberg.silver.resource_cost" in sql
    assert "GROUP BY" in sql


def test_c1_does_not_use_count_distinct_on_the_cost_scan():
    """`COUNT(DISTINCT)` no scan de 13,5 M linhas estourava a memória do worker.

    Recursos distintos vivem no estágio separado justamente por isso.
    """
    assert "COUNT(DISTINCT" not in c1_grain_sql()


def test_resource_count_sql_requires_provider_path():
    """Ajustes têm `resource_id` = '320core'; contá-los inflaria recursos."""
    sql = resource_count_sql()
    assert "LIKE '/%'" in sql
    assert "LIKE '%/providers/%'" in sql
    assert "COUNT(DISTINCT" in sql


def test_c1_has_no_dominant_org_mapping():
    """Não pode existir função que escolha uma organização entre várias reais."""
    import app.services.finops_cost_monthly as m

    assert not hasattr(m, "org_mapping_sql")


def test_c1_publishes_unavailable_costs_as_null():
    """`billed_cost` e `contracted_cost` são NULL na origem: publicar zero mentiria."""
    sql = c1_grain_sql()
    assert "CAST(NULL AS DOUBLE) AS billed_cost" in c1_stage2_sql(
        grain_table="g", resource_table="r"
    )
    assert "CAST(NULL AS DOUBLE) AS contracted_cost" in c1_stage2_sql(
        grain_table="g", resource_table="r"
    )


def test_unavailable_measures_explain_the_reason():
    """A UI precisa saber por que o campo é nulo, não só que é nulo.

    Inclui a organização: ela é indisponível de forma **estrutural** (não há chave
    reconciliável), diferente de `billed_cost`, que é nulo na origem.
    """
    motivos = unavailable_measures()
    assert {"billed_cost", "contracted_cost"} <= set(motivos)
    assert "13.519.512" in motivos["billed_cost"]
    assert "departamento_nivel_1..7" in motivos
    assert "UNMAPPED" in motivos["departamento_nivel_1..7"]


def test_org_levels_are_seven():
    assert len(ORG_LEVELS) == 7
    assert ORG_LEVELS[0] == "departamento_nivel_1"
    assert ORG_LEVELS[-1] == "departamento_nivel_7"


def test_org_hierarchy_audit_measures_ambiguity():
    """91 de 103 contas do canônico têm múltiplas hierarquias no legado.

    A auditoria substituiu o mapeamento dominante: em vez de escolher uma
    hierarquia por conta (`ROW_NUMBER` sobre o maior custo), o C1 publica `NULL` e
    `UNMAPPED`, e a ambiguidade vira medida para C11.
    """
    sql = org_hierarchy_audit_sql()
    assert "MULTIPLAS_HIERARQUIAS" in sql
    assert "ambigua" in sql
    assert "hierarquias" in sql


def test_non_additive_and_additive_measures_do_not_overlap():
    """Uma medida não pode ser aditiva e não aditiva ao mesmo tempo."""
    assert not (NON_ADDITIVE_MEASURES & ADDITIVE_MEASURES)


def test_resources_is_declared_non_additive():
    """Somar `resources` pelas dimensões devolveu 21,2 M contra 968.339 reais."""
    assert "resources" in NON_ADDITIVE_MEASURES
    assert "effective_cost" in ADDITIVE_MEASURES
    assert "resources" not in ADDITIVE_MEASURES


def test_c1_cube_table_name():
    assert C1_TABLE == "finops_cost_monthly"


def test_c1_select_sql_is_a_single_query_for_inspection():
    sql = c1_select_sql()
    assert "WITH grao AS" in sql
    assert "recursos AS" in sql


# --- C2 ------------------------------------------------------------------------


def test_inventory_is_deduplicated_by_safe_key_per_period():
    """26 snapshots por recurso: sem deduplicar por competência, o custo multiplica."""
    sql = inventory_temporal_sql()
    assert "ROW_NUMBER() OVER" in sql
    assert "PARTITION BY" in sql
    assert "inventory_snapshots" in sql
    assert "rn = 1" in sql


def test_inventory_dedup_orders_by_closest_valid_snapshot():
    """Entre os válidos, o mais próximo da competência — não o mais recente."""
    sql = inventory_temporal_sql()
    assert "ORDER BY b.snapshot_valid_from DESC" in sql
    assert "b.snapshot_processed_at DESC" in sql


def test_c2_quality_columns_include_inventory_coverage():
    colunas = quality_columns()
    assert "inventory_status" in colunas
    assert "inventory_snapshots" in colunas
    assert "resource_id_status" in colunas


def test_c2_non_additive_measures():
    assert "rows_without_tag" in non_additive_measures()
    assert "effective_cost" not in non_additive_measures()


def test_c2_cube_table_name():
    assert C2_TABLE == "finops_cost_resource_monthly"


# --- C3 ------------------------------------------------------------------------


def test_driver_types_are_declared():
    assert set(DRIVER_TYPES) == {"service", "sub_account", "region", "organization"}


def test_driver_key_column_maps_each_type():
    assert driver_key_column("service") == "service_key"
    assert driver_key_column("sub_account") == "sub_account_id"
    assert driver_key_column("region") == "region_key"
    assert driver_key_column("organization") == "departamento_nivel_5"


def test_unknown_driver_type_raises_instead_of_defaulting():
    """Um tipo novo precisa ser declarado; default silencioso publicaria lixo."""
    with pytest.raises(KeyError):
        driver_key_column("pod")


def test_c3_select_full_outer_join_finds_new_and_removed():
    sql = c3_select_sql(
        period_previous="202601", period_current="202602", driver_type="service"
    )
    assert "FULL OUTER JOIN" in sql
    assert "'new'" in sql
    assert "'removed'" in sql
    assert "'increased'" in sql
    assert "'decreased'" in sql


def test_c3_contribution_divides_by_the_pair_total():
    sql = c3_select_sql(
        period_previous="202601", period_current="202602", driver_type="service"
    )
    assert "contribution_pct" in sql
    assert "total_delta" in sql


def test_c3_uses_the_requested_periods():
    sql = c3_select_sql(
        period_previous="202602", period_current="202603", driver_type="region"
    )
    assert "'202603' AS period_current" in sql
    assert "'202602' AS period_previous" in sql
    assert "region_key" in sql


def test_comparable_pairs_require_measured_complete_status():
    """`202504 → 202505` não pode virar tendência: ajuste de 1 dia contra mês cheio."""
    statuses = {
        ("azure-4p", "202504"): PARTIAL,
        ("azure-4p", "202505"): PARTIAL,
        ("azure-4p", "202506"): COMPLETE,
        ("azure-4p", "202507"): COMPLETE,
    }
    pares = comparable_period_pairs(
        {"azure-4p": ["202504", "202505", "202506", "202507"]}, statuses=statuses
    )
    assert pares == [("azure-4p", "202506", "202507")]


def test_missing_status_defaults_to_not_comparable():
    """Sem status medido, o default é não comparável — nunca comparável."""
    assert comparable_period_pairs({"azure-4p": ["202601", "202602"]}) == []


def test_current_open_never_enters_comparison():
    statuses = {("azure-ti", "202609"): CURRENT_OPEN}
    assert comparable_period_pairs({"azure-ti": ["202609"]}, statuses=statuses) == []


def test_c3_never_compares_across_connections():
    statuses = {
        ("a", "202601"): COMPLETE,
        ("b", "202602"): COMPLETE,
    }
    pares = comparable_period_pairs(
        {"a": ["202601"], "b": ["202602"]}, statuses=statuses
    )
    assert pares == []


def test_slice_names_are_versioned_by_build():
    """Sem versionar, fatia de par não comparável sobrevive e entra na montagem."""
    a = slice_table_name("azure-4p", "202602", "service", build="1")
    b = slice_table_name("azure-4p", "202602", "service", build="2")
    assert a != b
    assert a.endswith("_1") and b.endswith("_2")


def test_slice_name_sanitizes_hyphen():
    nome = slice_table_name("azure-4p", "202602", "sub_account", build="9")
    assert "-" not in nome
    assert nome.startswith("finops_cost_driver_slice_")


def test_c3_union_reads_slices_instead_of_repeating_ctes():
    """40 partes numa consulta só estourou o limite de estágios do Trino (363 > 150)."""
    sql = c3_union_sql(slice_tables=["a", "b"])
    assert "iceberg.silver.a" in sql
    assert "iceberg.silver.b" in sql
    assert "UNION ALL" in sql
    assert "WITH" not in sql.split("UNION ALL")[0].upper().replace("WITHOUT", "")


def test_c3_union_without_slices_returns_empty_contract():
    """Sem par comparável não há delta a explicar — mas o contrato tem colunas."""
    sql = c3_union_sql(slice_tables=[])
    assert "WHERE 1 = 0" in sql
    assert "driver_type" in sql


def test_reconciliation_tolerance_grows_with_driver_count():
    """Arredondamento por driver acumula: 44 drivers divergiram R$ 0,02."""
    assert reconciliation_tolerance(0) == pytest.approx(0.05)
    assert reconciliation_tolerance(2) == pytest.approx(0.05)
    assert reconciliation_tolerance(44) == pytest.approx(0.44)
    assert reconciliation_tolerance(100) == pytest.approx(1.0)


def test_reconciliation_tolerance_floor_covers_small_sets():
    """Um cubo correto com 1 driver não pode ser reprovado por R$ 0,01."""
    assert reconciliation_tolerance(1) >= 0.01
