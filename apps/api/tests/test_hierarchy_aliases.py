"""Aliases de nível: o papel de cada profundidade depende do ramo do organograma."""
from app.services.hierarchy_aliases import NodeInfo, RoleInfo, match_path, resolve_levels, tree_depth

ROLES = {
    1: RoleInfo(1, "segmento", "Segmento", "Segmentos"),
    2: RoleInfo(2, "modelo", "Modelo", "Modelos"),
    3: RoleInfo(3, "provedor", "Provedor", "Provedores"),
    4: RoleInfo(4, "cliente", "Cliente", "Clientes"),
    5: RoleInfo(5, "diretoria", "Diretoria", "Diretorias"),
    6: RoleInfo(6, "projeto", "Projeto", "Projetos"),
}

# Cloud > Showback > OCI > {Eicon, Hemobrás}; Cloud > Corporativo > Azure > TI > {DIR Dados}
NODES = [
    NodeInfo(1, None, "Cloud"),
    NodeInfo(2, 1, "Showback"),
    NodeInfo(3, 2, "OCI"),
    NodeInfo(4, 3, "Eicon"),
    NodeInfo(5, 3, "Hemobrás"),
    NodeInfo(6, 1, "Corporativo"),
    NodeInfo(7, 6, "Azure"),
    NodeInfo(8, 7, "TI"),
    NodeInfo(9, 8, "DIR Dados"),
]

GLOBAL = [(None, 1, 1), (None, 2, 2), (None, 3, 3), (None, 4, 4), (None, 5, 6)]


def _labels(result):
    return [level["label"] for level in result["levels"]]


def test_match_path_is_case_insensitive_and_stops_at_unknown_name():
    assert [n.id for n in match_path(["cloud", " SHOWBACK ", "OCI"], NODES)] == [1, 2, 3]
    assert [n.id for n in match_path(["Cloud", "Inexistente", "OCI"], NODES)] == [1]


def test_global_rules_apply_without_path():
    result = resolve_levels([], NODES, GLOBAL, ROLES, tree_depth(NODES))
    assert _labels(result)[:6] == ["Segmento", "Modelo", "Provedor", "Cliente", "Projeto", "Nível 6"]
    assert result["levels"][3]["label_plural"] == "Clientes"
    assert result["levels"][3]["source"] == "global"


def test_scope_rule_overrides_global_only_inside_its_branch():
    # No ramo Corporativo, o nível 4 é uma unidade e o 5 é Diretoria.
    rules = GLOBAL + [(6, 5, 5)]
    corporate = resolve_levels(["Cloud", "Corporativo", "Azure", "TI"], NODES, rules, ROLES, 5)
    showback = resolve_levels(["Cloud", "Showback", "OCI"], NODES, rules, ROLES, 5)
    assert corporate["levels"][4]["label"] == "Diretoria"
    assert corporate["levels"][4]["scope"] == "Corporativo"
    assert showback["levels"][4]["label"] == "Projeto"


def test_deepest_scope_wins():
    rules = GLOBAL + [(1, 4, 5), (2, 4, 4)]
    assert resolve_levels(["Cloud", "Showback"], NODES, rules, ROLES, 5)["levels"][3]["label"] == "Cliente"
    assert resolve_levels(["Cloud", "Corporativo"], NODES, rules, ROLES, 5)["levels"][3]["label"] == "Diretoria"


def test_rule_scoped_on_the_node_itself_labels_that_node():
    rules = GLOBAL + [(9, 5, 5)]
    result = resolve_levels(["Cloud", "Corporativo", "Azure", "TI", "DIR Dados"], NODES, rules, ROLES, 5)
    assert result["levels"][4]["label"] == "Diretoria"


def test_next_level_uses_unanimous_child_rules_and_flags_mixed():
    unanimous = GLOBAL + [(4, 4, 5), (5, 4, 5)]
    result = resolve_levels(["Cloud", "Showback", "OCI"], NODES, unanimous, ROLES, 5)
    assert result["levels"][3]["label"] == "Diretoria"
    assert result["levels"][3]["mixed"] is False

    mixed = GLOBAL + [(4, 4, 5)]
    result = resolve_levels(["Cloud", "Showback", "OCI"], NODES, mixed, ROLES, 5)
    assert result["levels"][3]["label"] == "Cliente"
    assert result["levels"][3]["mixed"] is True


def test_unknown_path_falls_back_to_global():
    result = resolve_levels(["Outra raiz"], NODES, GLOBAL, ROLES, 5)
    assert result["matched_depth"] == 0
    assert _labels(result)[:4] == ["Segmento", "Modelo", "Provedor", "Cliente"]
