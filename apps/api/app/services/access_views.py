"""Fase 4: views por escopo no Trino/Iceberg (opção A do plano).

Cada escopo vira uma view `SELECT ... WHERE coluna IN (ids)`. A API consulta
as views e não a tabela base, então o filtro não depende do SQL que o cliente
envia. Os nomes passam por validação, e os valores são literais escapados.
"""
import re

_IDENT = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"identificador inseguro: {name!r}")
    return f'"{name}"'


def _literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def build_scope_view_sql(
    catalog: str,
    schema: str,
    base_table: str,
    view_name: str,
    column: str,
    values: list[str],
) -> str:
    """CREATE OR REPLACE VIEW com o filtro do escopo. Sem valores, a view não devolve linhas."""
    target = f"{_ident(catalog)}.{_ident(schema)}.{_ident(view_name)}"
    source = f"{_ident(catalog)}.{_ident(schema)}.{_ident(base_table)}"
    if not values:
        predicate = "FALSE"
    else:
        predicate = f"{_ident(column)} IN ({', '.join(_literal(v) for v in sorted(set(values)))})"
    return f"CREATE OR REPLACE VIEW {target} AS SELECT * FROM {source} WHERE {predicate}"


def view_name_for(base_table: str, scope_type: str, node_id: str) -> str:
    """Nome estável da view de um nó: <tabela>__<escopo>__<id>."""
    safe_node = re.sub(r"[^a-z0-9_]", "_", str(node_id).lower())
    name = f"{base_table}__{scope_type}__{safe_node}"
    if not _IDENT.match(name):
        raise ValueError(f"nome de view inseguro: {name!r}")
    return name
