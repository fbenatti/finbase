"""Materializa um cubo Gold pré-agregado (dest_dm_cost_<code>) a partir da Silver.

Uma `gold_rule` seleciona uma ou mais tabelas **Silver** já normalizadas
(`resource_cost` para os cubos financeiros; `dest_modelo_focus` e o espelho
`dest_modelo_focus_planning` para os cubos organizacionais legados), aplica os
filtros declarados e pré-agrega nas dimensões analíticas para que a API e o
dashboard leiam uma tabela fina.

O docstring anterior chamava essas fontes de "Gold tables". A camada errada no
texto levou a supor que já existia um cruzamento que nunca foi feito; as fontes
são Silver detalhada, e o cruzamento é o que este materializador produz.

Toda publicação passa por `gold_governance.publish_gold_cube`, que registra o
manifesto (fontes, hash do SQL, linhas, totais, rollback) e troca atomicamente.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from app.models.destination import DestinationTable
from app.models.source_connection import SourceConnection
from app.models.gold_mart_rule import GoldMartRule, GoldMartRuleSource
from app.services import iceberg_service as iceberg
from app.services.trino_service import execute
from app.services import lakehouse_layers as layers

logger = logging.getLogger(__name__)

# Os gold cubes vivem no namespace canônico GOLD (hoje `gold`), separado
# das cubos Gold (iceberg.gold.*). O materializador cria/swap aqui; os dashboards e
# a API consultam {GOLD}.dest_dm_cost_*.
GOLD = layers.GOLD

_AI_AUXILIARY_GOLD_CUBES = {
    "ai_consumption_daily": "dest_dm_ai_consumption_daily",
    "ai_license_snapshot": "dest_dm_ai_license_snapshot",
    "copilot_usage_daily": "dest_dm_copilot_usage_daily",
    "copilot_license_snapshot": "dest_dm_copilot_license_snapshot",
    "copilot_user_period": "dest_dm_copilot_user_period",
}

_FOCUS_FIELD_ALIASES = {
    "projectname": "x_Project",
    "project": "x_Project",
    "reservationname": "source_reservation_name",
    # A planning (espelho de forecast) não expõe PricingCategory; o filtro de
    # classificação aplica-se pelo equivalente mais próximo (servicename).
    "pricingcategory": "servicename",
}


def _alias_for_missing(schema: dict[str, str], requested: str) -> str | None:
    # Resolve a filter field that a table lacks onto an equivalent column.
    # Gold and its *_planning mirror share the same normalized structure except
    # for small naming differences: the Gold exposes organograma (folha) while the
    # planning mirror only keeps organograma_nivel_1..7. When a source unites
    # both tables, a filter on organograma must apply on the planning side
    # as organograma_nivel_1 so the union stays a single clean dataset.
    key = str(requested or "").strip().lower()
    if key == "organograma":
        candidate = schema.get("organograma_nivel_1")
        if candidate:
            return candidate
    if key.startswith("organograma_nivel_"):
        candidate = schema.get(key)
        if candidate:
            return candidate
    return None


def rule_target_table(code: str) -> str:
    """dest_dm_cost_<gold_code> - the original code is preserved in the rule;
    the physical table name uses an underscore-safe variant (hyphens would
    break unquoted Iceberg identifiers in Trino SQL)."""
    normalized = normalize_code(code)
    if normalized in _AI_AUXILIARY_GOLD_CUBES:
        return _AI_AUXILIARY_GOLD_CUBES[normalized]
    safe = normalized.replace("-", "_")
    return f"dest_dm_cost_{safe}"


def normalize_code(raw: str) -> str:
    """Lowercase, strip, and collapse unsafe characters for the table name."""
    value = str(raw or "").strip().lower()
    value = re.sub(r"[^a-z0-9_\-]+", "_", value).strip("_")
    return value or "gold"


def _quote_ident(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _schema_map(rows: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for row in rows:
        name = str(row.get("Column") or row.get("column_name") or row.get("column") or "")
        if name:
            mapping[name.lower()] = name
    return mapping


def _resolve_column(schema: dict[str, str], name: str) -> str | None:
    key = str(name or "").lower()
    return schema.get(key)


def _normalized_conditions(schema: dict[str, str], raw_conditions: Any) -> tuple[list[dict[str, Any]], list[str]]:
    conditions = raw_conditions or []
    if isinstance(conditions, dict):
        conditions = [conditions]
    normalized, missing = [], []
    for condition in conditions:
        item = dict(condition)
        requested = str(item.get("column") or item.get("field") or "")
        alias = _FOCUS_FIELD_ALIASES.get(requested.lower())
        if requested.lower() not in schema and alias and alias.lower() in schema:
            item["column"] = alias
            item.pop("field", None)
        if str(item.get("column") or item.get("field") or "").lower() not in schema:
            fallback = _alias_for_missing(schema, requested)
            if fallback:
                item["column"] = fallback
                item.pop("field", None)
            else:
                missing.append(requested)
        normalized.append(item)
    return normalized, missing


def _filter_clause(schema: dict[str, str], conditions: list[dict[str, Any]], table_alias: str | None = None) -> str:
    clauses: list[str] = []
    for item in conditions or []:
        column = _resolve_column(schema, item.get("column") or item.get("field") or "")
        if not column:
            continue
        operator = str(item.get("operator") or item.get("op") or "IN").upper()
        values = item.get("values")
        if values is None and item.get("value") is not None:
            values = [item.get("value")]
        values = [str(v) for v in (values or []) if v is not None and str(v).strip() != ""]
        col_ref = f"{table_alias}.{_quote_ident(column)}" if table_alias else _quote_ident(column)
        col_sql = f"lower(CAST({col_ref} AS VARCHAR))"
        logic = str(item.get("logic") or "AND").upper()
        connector = logic if logic in {"AND", "OR"} else "AND"
        prefix = connector if clauses else ""
        if operator in {"EMPTY", "IS_EMPTY"}:
            clauses.append(f"{prefix} ({col_ref} IS NULL OR TRIM(CAST({col_ref} AS VARCHAR)) = '')")
        elif operator in {"NOT_EMPTY", "IS_NOT_EMPTY"}:
            clauses.append(f"{prefix} ({col_ref} IS NOT NULL AND TRIM(CAST({col_ref} AS VARCHAR)) <> '')")
        elif operator in {"CONTAINS", "~"} and values:
            likes = " OR ".join(f"{col_sql} LIKE {_quote_literal('%' + v.lower() + '%')}" for v in values)
            clauses.append(f"{prefix} ({likes})")
        elif operator in {"NOT_CONTAINS"} and values:
            likes = " AND ".join(f"{col_sql} NOT LIKE {_quote_literal('%' + v.lower() + '%')}" for v in values)
            clauses.append(f"{prefix} ({likes})")
        elif operator in {"NOT_IN", "!="} and values:
            literals = ", ".join(_quote_literal(v.lower()) for v in values)
            clauses.append(f"{prefix} ({col_sql} NOT IN ({literals}) OR {col_ref} IS NULL)")
        elif operator in {"=", "=="} and values:
            clauses.append(f"{prefix} {col_sql} = {_quote_literal(values[0].lower())}")
        elif values:
            literals = ", ".join(_quote_literal(v.lower()) for v in values)
            clauses.append(f"{prefix} {col_sql} IN ({literals})")
    return " ".join(clauses) if clauses else "1=1"


def _source_clause(schema: dict[str, str], source: GoldMartRuleSource, gold_table: str | None = None, table_alias: str | None = None) -> str:
    raw_filter = source.filter_json or {}
    raw_conditions = raw_filter if isinstance(raw_filter, list) else raw_filter.get("conditions", [])
    conditions, missing = _normalized_conditions(schema, raw_conditions)
    if missing:
        gold = gold_table or source.gold_table or "?"
        # Planning is a deliberately narrow Silver mirror (period, measure,
        # type, cloud and hierarchy).  A rule may also union it with FOCUS;
        # dimensions that only exist on FOCUS cannot be evaluated on planning.
        # Treat those predicates as unconstrained for the planning leg instead
        # of aborting the entire Gold cube.
        if str(gold).lower().endswith("_planning"):
            return "1=1"
        # Sugere colunas equivalentes que a Gold realmente possui, para o usuário
        # ajustar o filtro ou a regra (ex.: planning tem subaccountname, não subaccountid).
        existing = sorted({m.lower(): schema.get(m.lower()) for m in missing if m.lower() in schema})
        hints = [f"'{m}' existe como '{schema.get(m.lower())}'" for m in missing if m.lower() in schema]
        others = [f"'{m}' (a Gold tem: {', '.join(sorted(schema)[:6])}...)" for m in missing if m.lower() not in schema]
        detail = "; ".join(hints + others)
        raise RuntimeError(
            f"Filtro da regra não aplicável à Gold '{gold}' (fonte #{source.id}): "
            f"campo(s) {', '.join(missing)} não existe(m) nessa tabela. "
            f"O filtro vale para TODAS as cubos Gold unidas na fonte — se a coluna só existe na "
            f"FOCUS (ex.: subaccountid), a planning/espelho precisa dela ou o filtro deve "
            f"usar uma coluna comum (ex.: subaccountname). Detalhe: {detail}."
        )
    return _filter_clause(schema, conditions, table_alias=table_alias)


def _period_column(schema: dict[str, str]) -> str | None:
    return _resolve_column(schema, "_billing_period") or _resolve_column(schema, "period")


def _numeric_columns(schema: dict[str, str]) -> set[str]:
    """Column names (lowercased) whose Trino type is numeric - excluded from GROUP BY."""
    return {
        name.lower() for name, typ in schema.items()
        if any(token in str(typ).lower() for token in ("int", "double", "real", "decimal", "float", "bigint"))
    }


async def load_gold_schema(gold_table: str) -> dict[str, str]:
    rows = await execute(f"DESCRIBE iceberg.silver.{gold_table}", timeout_s=30)
    return _schema_map(rows)


async def load_gold_schema_typed(gold_table: str) -> dict[str, dict[str, str]]:
    """DESCRIBE a Gold and keep the REAL column type (double, varchar, ...).

    load_gold_schema discards types (only name resolution), which made
    /gold/gold-schema report type = column name and broke the
    numeric-measure filter in the pivot editor. This variant preserves it.
    """
    rows = await execute(f"DESCRIBE iceberg.silver.{gold_table}", timeout_s=30)
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        name = str(row.get("Column") or row.get("column_name") or row.get("column") or "")
        typ = str(row.get("Type") or row.get("type") or row.get("data_type") or "")
        if name:
            out[name.lower()] = {"name": name, "type": typ}
    return out


def _value_expr(schema: dict[str, str], value_column: str) -> str:
    col = _resolve_column(schema, value_column)
    if not col:
        # Measure absent in this source (e.g. planning has only 'realizado'):
        # contribute 0 instead of failing with an unresolvable column.
        return "CAST(0 AS DOUBLE)"
    return f"COALESCE(TRY_CAST({_quote_ident(col)} AS DOUBLE), 0)"


async def preview_source(*, gold_table: str, filter_json: dict[str, Any] | None, value_column: str = "realizado") -> dict[str, Any]:
    schema = await load_gold_schema(gold_table)
    value_col = value_column if value_column in {"realizado", "BilledCost", "EffectiveCost"} else "realizado"
    conditions = (filter_json or {}).get("conditions") or filter_json or []
    if isinstance(conditions, dict):
        conditions = [conditions]
    where = _filter_clause(schema, list(conditions))
    sql = f"""
      SELECT COUNT(*) AS row_count,
             SUM({_value_expr(schema, value_col)}) AS total_value
      FROM iceberg.silver.{gold_table}
      WHERE {where}
    """
    rows = await execute(sql, timeout_s=120)
    row = rows[0] if rows else {}
    return {
        "gold_table": gold_table,
        "row_count": int(row.get("row_count") or 0),
        "total_value": float(row.get("total_value") or 0),
        "value_column": value_col,
    }


# Analytic dimensions used by default when a rule does not declare its own.
# Grouping by high-cardinality columns (ResourceId, tags, custom_fields...)
# neither shrinks the table nor helps dashboards and explodes Trino memory on
# multi-million-row cubos Gold, so the default keeps the drill-down dimensions that
# dashboards actually use.
_ANALYTIC_DIMENSIONS = (
    "_billing_period",
    "period",
    "tipo",
    "cloud_origem",
    "organograma",
    "organograma_nivel_1",
    "organograma_nivel_2",
    "organograma_nivel_3",
    "organograma_nivel_4",
    "organograma_nivel_5",
    "organograma_nivel_6",
    "organograma_nivel_7",
    "servicecategory",
    "servicename",
    "servicesubcategory",
    "skumeter",
    "subaccountname",
    "centro_custo",
    "regionname",
    "chargecategory",
    # FOCUS marketplace/provider dimensions are part of the detailed Silver
    # schema and must remain available in the executive Gold cube as well.
    "marketplaceproductname",
    "marketplacebillingmodel",
    "serviceprovidername",
    "source_connection_id",
)


def _merged_lookup(schema_by_gold: dict[str, dict[str, str]]) -> list[tuple[str, str]]:
    """(lowercase name, physical name) union across sources, first source wins."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for schema in schema_by_gold.values():
        for physical in schema.values():
            key = physical.lower()
            if key not in seen:
                seen.add(key)
                out.append((key, physical))
    return out


def _default_dimensions(schema_by_gold: dict[str, dict[str, str]]) -> list[str]:
    """Analytic dimensions present in at least one source (period first)."""
    lookup = {key: physical for key, physical in _merged_lookup(schema_by_gold)}
    dims: list[str] = []
    for logical in _ANALYTIC_DIMENSIONS:
        physical = lookup.get(logical.lower())
        if physical and physical not in dims:
            dims.append(physical)
    return dims


def _merged_dimensions(schema_by_gold: dict[str, dict[str, str]], configured: list[str] | None) -> list[str]:
    """Resolve the GROUP BY dimensions for a rule.

    ``configured`` (rule.dimensions) is honored when non-empty; otherwise the
    analytic default is used. Every dimension must resolve in at least one
    source; the period column is always placed first for cheap partitioning.
    """
    lookup = {key: physical for key, physical in _merged_lookup(schema_by_gold)}
    if configured:
        dims: list[str] = []
        for logical in configured:
            physical = lookup.get(str(logical).lower())
            if physical and physical not in dims:
                dims.append(physical)
    else:
        dims = _default_dimensions(schema_by_gold)
    period_name = None
    for schema in schema_by_gold.values():
        p = _period_column(schema)
        if p:
            period_name = p
            break
    if period_name:
        if period_name in dims:
            dims.remove(period_name)
        dims.insert(0, period_name)
    return dims


def _side_select(gold: str, schema: dict[str, str], dims: list[str], measure_aliases: list[tuple[str, str]], percent: float, period_physical: dict[str, str], centro_map: dict[str, str] | None = None, table_alias: str | None = None) -> str:
    # Build one side of the UNION ALL. measure_aliases is (measure, alias)
    # with each measure scaled by percent and projected as _m0, _m1...
    exprs: list[str] = []
    for dim in dims:
        key = dim.lower()
        resolved = _resolve_column(schema, dim)
        if resolved:
            ref = f"{table_alias}.{_quote_ident(resolved)}" if table_alias else _quote_ident(resolved)
            exprs.append(f"CAST({ref} AS VARCHAR) AS {_quote_ident(dim)}")
        elif key in {"period", "_billing_period"} and period_physical.get(gold) and period_physical[gold] != dim:
            ref = f"{table_alias}.{_quote_ident(period_physical[gold])}" if table_alias else _quote_ident(period_physical[gold])
            exprs.append(f"CAST({ref} AS VARCHAR) AS {_quote_ident(dim)}")
        elif key == "centro_custo" and centro_map and "subaccountname" in schema:
            # Fonte sem centro_custo (ex.: espelho de planning): herda o
            # centro de custo da subconta através do mapa derivado do Gold.
            exprs.append(f"CAST(cm.{_quote_ident(centro_map['cc'])} AS VARCHAR) AS {_quote_ident(dim)}")
        else:
            exprs.append(f"CAST(NULL AS VARCHAR) AS {_quote_ident(dim)}")
    for measure, alias in measure_aliases:
        physical = _resolve_column(schema, measure) or measure
        measure_expr = _value_expr(schema, measure).replace(_quote_ident(physical), f"{table_alias}.{_quote_ident(physical)}" if table_alias else _quote_ident(physical), 1)
        scaled = f"({measure_expr}) * {percent}" if percent != 1.0 else measure_expr
        exprs.append(f"{scaled} AS {alias}")
    return ", ".join(exprs)


async def _materialize_rule(rule: GoldMartRule, *, run_id: int, progress: Any | None = None) -> int:
    """Build the pre-aggregated Gold table for one rule and swap it atomically."""
    table = rule_target_table(rule.code)

    # These five auxiliary gold cubes have provider-specific schemas and are
    # materialized by the same atomic publisher, but are still owned by the
    # Gold rule queue/UI (never by an external script or scheduler shortcut).
    ai_code = normalize_code(rule.code)
    if ai_code in _AI_AUXILIARY_GOLD_CUBES:
        from app.services.ai_usage_gold import refresh_ai_usage_gold_cubes
        if progress:
            progress(0, 1, f"Agregando {ai_code} a partir da Gold de IA")
        result = await refresh_ai_usage_gold_cubes(only_tables=[table])
        return int((result.get("tables") or {}).get(table) or 0)

    # SQL custom: o usuário fornece a query inteira. Ela deve gerar as
    # dimensões e medidas desejadas; nós apenas criamos a tabela no namespace
    # gold e fazemos o swap atômico.
    if getattr(rule, "sql_template", None) and str(rule.sql_template).strip():
        snapshot_mode = bool(getattr(rule, "snapshot_mode", False))
        snapshot_col = str(getattr(rule, "snapshot_col", "_snapshot_date") or "_snapshot_date")
        # A regra de saving por desligue depende da auxiliar mensal de VMs
        # (gold_mart._tmp_vm_monthly). O consumo DIÁRIO tem regra própria na Camada
        # Gold de Custo (consumo_diario_vm → dest_dm_cost_consumo_diario_vm).
        if rule.code == "saving_desligue_vm":
            if progress:
                progress(0, 1, "Preparando histórico mensal de VMs (saving por desligue)")
            try:
                await prepare_vm_monthly(progress=progress, sources=rule.sources)
            except Exception as prep_exc:
                logger.exception("Falha ao preparar auxiliar para %s", rule.code)
                raise
        staging = f"{table}_stg_{time.time_ns()}"
        iceberg.ensure_namespace(GOLD)
        # Em snapshot mode, cada execução adiciona uma cópia datada (append),
        # preservando o histórico para comparar dias (ex.: delta de tags).
        if snapshot_mode:
            create_sql = (
                f"CREATE TABLE iceberg.{GOLD}.{staging} AS "
                f"SELECT t.*, CAST(CURRENT_DATE AS VARCHAR) AS {_quote_ident(snapshot_col)} "
                f"FROM ({rule.sql_template.strip()}) t"
            )
        else:
            create_sql = f"CREATE TABLE iceberg.{GOLD}.{staging} AS {rule.sql_template.strip()}"
        try:
            if progress:
                progress(0, 1, f"Materializando {rule.code} via SQL custom (snapshot={snapshot_mode})")
            await execute(create_sql, timeout_s=1800)
            result = await execute(f"SELECT COUNT(*) AS row_count FROM iceberg.{GOLD}.{staging}", timeout_s=180)
            rows = int((result[0] if result else {}).get("row_count") or 0)
            if snapshot_mode:
                if rows:
                    snapshot_val = iceberg.staging_column_single_value(
                        staging, snapshot_col, namespace=GOLD
                    )
                    if iceberg.table_exists(table, namespace=GOLD):
                        iceberg.replace_staged_cube_snapshot_day(
                            table,
                            staging,
                            snapshot_col=snapshot_col,
                            snapshot_value=snapshot_val,
                            known_row_count=rows,
                        )
                    else:
                        iceberg.replace_staged_cube(
                            table, staging, namespace=GOLD, known_row_count=rows, purge_snapshots=False
                        )
                else:
                    iceberg.cleanup_staged_table(staging, namespace=GOLD)
                await _record_rule_manifest(rule, rows=rows, sql=rule.sql_template, run_id=run_id)
                return rows
            if rows:
                iceberg.replace_staged_cube(table, staging, namespace=GOLD, known_row_count=rows)
            else:
                iceberg.cleanup_staged_table(staging, namespace=GOLD)
            await _record_rule_manifest(rule, rows=rows, sql=rule.sql_template, run_id=run_id)
            return rows
        except Exception:
            try:
                iceberg.cleanup_staged_table(staging, namespace=GOLD)
            except Exception:
                pass
            raise

    schema_by_gold: dict[str, dict[str, str]] = {}
    for source in rule.sources or []:
        golds = list(getattr(source, "gold_tables", None) or [])
        if not golds and getattr(source, "gold_table", None):
            golds = [source.gold_table]
        for gold in golds:
            schema_by_gold[gold] = await load_gold_schema(gold)

    dims = _merged_dimensions(schema_by_gold, list(getattr(rule, "dimensions", None) or []))
    period_physical = {gold: (_period_column(schema) or "_billing_period") for gold, schema in schema_by_gold.items()}

    # Lookup de centro de custo por subconta: quando a Gold inclui a dimensão
    # centro_custo mas alguma fonte não a possui (ex.: espelho de planning),
    # o valor é herdado do Gold que tem a coluna, casando por subaccountname.
    centro_map: dict[str, str] | None = None
    if "centro_custo" in dims:
        for gold, schema in schema_by_gold.items():
            if "centro_custo" in schema and "subaccountname" in schema:
                centro_map = {"gold": gold, "cc": schema["centro_custo"], "sa": schema["subaccountname"]}
                break

    # Pivot-style measures: rule.measure_columns wins; else the first
    # source value_column; else realized.
    measures: list[str] = list(getattr(rule, "measure_columns", None) or [])
    if not measures:
        value_col = (rule.sources or [])[0].value_column
        if value_col in {"realizado", "BilledCost", "EffectiveCost"}:
            measures = [value_col]
        else:
            measures = ["realizado"]
    measure_aliases = [(m, f"_m{i}") for i, m in enumerate(measures)]

    parts: list[str] = []
    uses_centro_map = False
    for source in rule.sources or []:
        # A source may bind one or more Gold tables with the same structure;
        # the same filters/value/percent apply to each table and the results
        # are unioned into one clean dataset before aggregation.
        golds = list(getattr(source, "gold_tables", None) or [])
        if not golds and getattr(source, "gold_table", None):
            golds = [source.gold_table]
        for gold in golds:
            schema = schema_by_gold.get(gold)
            if schema is None:
                schema = await load_gold_schema(gold)
                schema_by_gold[gold] = schema
            percent = float(source.percent or 100) / 100.0
            use_map = (
                centro_map is not None
                and "centro_custo" not in schema
                and "subaccountname" in schema
                and centro_map["gold"] != gold
            )
            table_alias = "src" if use_map else None
            where = _source_clause(schema, source, gold_table=gold, table_alias=table_alias)
            select_exprs = _side_select(
                gold, schema, dims, measure_aliases, percent, period_physical,
                centro_map=centro_map if use_map else None,
                table_alias=table_alias,
            )
            if use_map:
                uses_centro_map = True
                sa = schema["subaccountname"]
                join = (
                    f"LEFT JOIN _gold_centro_map cm ON "
                    f"TRIM(lower(CAST(src.{_quote_ident(sa)} AS VARCHAR))) = "
                    f"TRIM(lower(CAST(cm.cc_subaccount AS VARCHAR)))"
                )
                parts.append(f"SELECT {select_exprs} FROM iceberg.silver.{gold} AS src {join} WHERE {where}")
            else:
                parts.append(f"SELECT {select_exprs} FROM iceberg.silver.{gold} WHERE {where}")

    if not parts:
        return 0
    union_sql = " UNION ALL ".join(f"({part})" for part in parts)
    group_by = ", ".join(_quote_ident(dim) for dim in dims)
    measure_sums = ", ".join(f"SUM({alias}) AS {_quote_ident(measure)}" for measure, alias in measure_aliases)
    if uses_centro_map:
        map_sql = (
            f"SELECT {_quote_ident(centro_map['sa'])} AS cc_subaccount, "
            f"max({_quote_ident(centro_map['cc'])}) AS {_quote_ident(centro_map['cc'])} "
            f"FROM iceberg.silver.{centro_map['gold']} "
            f"WHERE {_quote_ident(centro_map['cc'])} IS NOT NULL "
            f"AND TRIM(CAST({_quote_ident(centro_map['cc'])} AS VARCHAR)) <> '' "
            f"GROUP BY {_quote_ident(centro_map['sa'])}"
        )
        full_sql = (
            f"WITH _gold_centro_map AS ({map_sql}) "
            f"SELECT {group_by}, {measure_sums} FROM ({union_sql}) AS gold_src GROUP BY {group_by}"
        )
    else:
        full_sql = f"SELECT {group_by}, {measure_sums} FROM ({union_sql}) AS gold_src GROUP BY {group_by}"

    staging = f"{table}_stg_{time.time_ns()}"
    period = period_physical.get((rule.sources or [])[0].gold_table) if rule.sources else None
    # Garante que o namespace dos gold cubes exista antes do CREATE.
    iceberg.ensure_namespace(GOLD)
    create_sql = f"CREATE TABLE iceberg.{GOLD}.{staging} AS {full_sql}"
    if period:
        create_sql = f"CREATE TABLE iceberg.{GOLD}.{staging} WITH (partitioning = ARRAY['{period}']) AS {full_sql}"
    try:
        if progress:
            progress(0, 1, f"Agregando {rule.code} a partir de {len(rule.sources or [])} fonte(s) Silver")
        await execute(create_sql, timeout_s=1800)
        result = await execute(f"SELECT COUNT(*) AS row_count FROM iceberg.{GOLD}.{staging}", timeout_s=180)
        rows = int((result[0] if result else {}).get("row_count") or 0)
        if rows:
            iceberg.replace_staged_cube(table, staging, namespace=GOLD, known_row_count=rows)
        else:
            iceberg.cleanup_staged_table(staging, namespace=GOLD)
        await _record_rule_manifest(rule, rows=rows, sql=full_sql, run_id=run_id)
        return rows
    except Exception:
        try:
            iceberg.cleanup_staged_table(staging, namespace=GOLD)
        except Exception:
            pass
        raise


async def _record_rule_manifest(
    rule: GoldMartRule, *, rows: int, sql: str, run_id: int | None
) -> None:
    """Registra no manifesto a materialização governada de uma regra.

    Um único ponto para os três caminhos de `_materialize_rule` (SQL custom,
    snapshot mode e pivô), para que nenhum deles publique sem rastro.
    """
    from app.services.gold_governance import ManifestEntry, record_manifest, sql_fingerprint

    entry = ManifestEntry(
        cube_code=normalize_code(rule.code),
        physical_table=rule_target_table(rule.code),
        run_id=run_id,
        rule_id=getattr(rule, "id", None),
        rows_written=rows,
        sql_hash=sql_fingerprint(sql),
        status="SUCCESS" if rows else "PARTIAL",
        reason=None if rows else "sem linhas",
    )
    await record_manifest(entry)


async def materialize_gold_cubes(session: Any, *, run_id: int, progress: Any | None = None, rule_ids: list[int] | None = None) -> dict[str, Any]:
    from datetime import datetime, timezone
    from sqlalchemy.orm import selectinload

    query = session.query(GoldMartRule).options(selectinload(GoldMartRule.sources)).filter(GoldMartRule.active == True)  # noqa: E712
    if rule_ids:
        query = query.filter(GoldMartRule.id.in_(rule_ids))
    rules = query.all()

    # ``copilot_user_period`` is derived from the already aggregated Copilot
    # usage Gold.  Rebuilding only ``copilot_usage_daily`` left the dashboard
    # drill-down with a stale user/credit snapshot (for example, August after
    # September usage was loaded).  Model that dependency in the materializer
    # itself so every execution path — API, scheduler and worker chaining —
    # keeps both tables consistent.
    if rule_ids and any(normalize_code(rule.code) == "copilot_usage_daily" for rule in rules):
        user_period = (
            session.query(GoldMartRule)
            .options(selectinload(GoldMartRule.sources))
            .filter(
                GoldMartRule.active == True,  # noqa: E712
                GoldMartRule.code == "copilot_user_period",
            )
            .first()
        )
        if user_period and all(existing.id != user_period.id for existing in rules):
            rules.append(user_period)

    # R3: RUNNING órfão não pode ficar aberto para sempre. Um worker morto no meio
    # de um build deixa um RUNNING sem término; expirar antes de começar evita que
    # a leitura de "o que está materializando agora" minta.
    from app.services.gold_governance import expire_stale_running_manifests_sync

    try:
        expirados = expire_stale_running_manifests_sync()
        if expirados:
            logger.info("Manifestos RUNNING órfãos marcados ABANDONED: %d", expirados)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falha ao expirar RUNNING órfãos: %s", exc)

    details: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    written = 0

    # Regras com `materializer_key` têm construção multi-estágio e são resolvidas
    # pela allowlist `gold_materializers`. Elas saem do laço de pivô abaixo: passar
    # um cubo `finops_*` pelo caminho genérico publicaria uma tabela diferente da
    # que o nome promete.
    keyed = [r for r in rules if getattr(r, "materializer_key", None)]
    rules = [r for r in rules if not getattr(r, "materializer_key", None)]

    if keyed:
        from app.services.gold_materializers import (
            execution_order,
            is_registered,
            runner_for,
            spec_for,
        )

        chaves = [str(r.materializer_key) for r in keyed]
        invalidas = [c for c in chaves if not is_registered(c)]
        if invalidas:
            raise KeyError(
                "materializer_key fora da allowlist: " + ", ".join(sorted(set(invalidas)))
            )
        try:
            if progress:
                progress(0, len(chaves), "Materializando cubos multi-estágio")
            # Executa chave a chave (e não em lote) para que a falha de um cubo não
            # apague o resultado dos que já publicaram. A ordem continua sendo a de
            # dependência; o que muda é o registro.
            from app.services.gold_governance import cost_baseline

            financeiros = [
                c for c in chaves
                if is_registered(c) and spec_for(c).financial
            ]
            # Baseline medido **uma vez por run**: se cada materializador medisse a
            # origem na hora própria, uma carga intermediária mudaria o número entre
            # C1 e C2 e a comparação deixaria de provar a preservação.
            baseline = await cost_baseline() if financeiros else None
            estado: dict[str, dict[str, Any]] = {}
            erro: Exception | None = None
            for chave in execution_order(chaves):
                try:
                    estado[chave] = await runner_for(chave)(
                        run_id=run_id,
                        expected_total=(
                            baseline.total
                            if baseline is not None
                            else None
                        ),
                        baseline=baseline,
                        progress=progress,
                    )
                except Exception as exc:  # noqa: BLE001 — vira status da regra
                    erro = exc
                    break

            from app.services.gold_materializers import is_registered, spec_for

            def _afetada(regra: Any) -> bool:
                """Regra afetada pela falha: a que falhou ou a que depende dela.

                Marcar as **anteriores** como ERROR seria falso — elas publicaram. E
                marcar as posteriores independentes também seria: cobertura e C1 não
                dependem de C2.
                """
                if erro is None:
                    return False
                if str(regra.materializer_key) in estado:
                    return False
                return True

            for regra in keyed:
                chave = str(regra.materializer_key)
                resultado = estado.get(chave)
                regra.materialization_run_id = run_id
                regra.materialized_at = datetime.now(timezone.utc)
                # `rule_target_table` deriva `dest_dm_cost_<code>` e não vale para os
                # cubos `finops_*`, cuja tabela física é declarada na spec. Usar o
                # nome derivado registraria uma tabela que não existe.
                regra.materialization_table = spec_for(chave).physical_table
                if resultado is not None:
                    linhas = int(
                        resultado.get("rows")
                        or resultado.get("grain_rows")
                        or resultado.get("inventory_rows")
                        or 0
                    )
                    regra.materialization_rows = linhas
                    regra.materialization_status = "UPDATED" if linhas else "EMPTY"
                    regra.materialization_error = None
                    details.append(
                        {
                            "rule_id": regra.id,
                            "code": regra.code,
                            "table": regra.materialization_table,
                            "rows": linhas,
                            "materializer_key": chave,
                        }
                    )
                    written += linhas
                elif _afetada(regra):
                    regra.materialization_status = "ERROR"
                    regra.materialization_error = str(erro)[:2000]
                    failed.append(
                        {"rule_id": regra.id, "code": regra.code, "error": str(erro)[:500]}
                    )
            if erro is not None:
                session.commit()
                raise erro
        except Exception as exc:
            session.commit()
            raise

    for index, rule in enumerate(rules, start=1):
        if progress:
            progress(index - 1, len(rules), f"Materializando {rule.code}")
        try:
            rows = await _materialize_rule(rule, run_id=run_id, progress=progress)
            rule.materialization_table = rule_target_table(rule.code)
            rule.materialization_rows = rows
            rule.materialization_status = "UPDATED" if rows else "EMPTY"
            rule.materialization_error = None
            rule.materialization_run_id = run_id
            rule.materialized_at = datetime.now(timezone.utc)
            details.append({"rule_id": rule.id, "code": rule.code, "table": rule_target_table(rule.code), "rows": rows})
            written += rows
        except Exception as exc:
            failed.append({"rule_id": rule.id, "code": rule.code, "error": str(exc)})
            rule.materialization_status = "ERROR"
            rule.materialization_error = str(exc)
            rule.materialization_run_id = run_id
            rule.materialized_at = datetime.now(timezone.utc)
            details.append({"rule_id": rule.id, "code": rule.code, "rows": 0, "error": str(exc)})
        if progress:
            progress(index, len(rules), f"{rule.code} - {'publicada' if rule.materialization_status != 'ERROR' else 'com erro'}")
    session.commit()
    return {
        "rules": len(rules) + len(keyed),
        "rows": written,
        "details": details,
        "failed": failed,
        "materializer_keys": [str(r.materializer_key) for r in keyed],
    }


def materialize_golds_sync(session: Any, *, run_id: int, progress: Any | None = None, rule_ids: list[int] | None = None) -> dict[str, Any]:
    from app.database import run_async_isolated
    return run_async_isolated(materialize_gold_cubes(session, run_id=run_id, progress=progress, rule_ids=rule_ids))


# ---------------------------------------------------------------------------
# Histórico mensal de VMs (saving por desligue)
# ---------------------------------------------------------------------------
# A regra custom "saving_desligue_vm" depende de uma tabela agregada com o
# histórico mensal de horas/custo por VM real: extrai o nome da VM do ResourceId
# (a origem guarda o nome do PLANO em ResourceName), exclui pods efêmeros
# (ResourceIds hex UUID de Databricks/spot) e deixa só VMs clássicas de
# /virtualMachines/. A materialização lê essa auxiliar — leve — em vez de varrer
# 8 meses em uma query só.
#
# O nome era `_tmp_vm_monthly` dentro do namespace **Gold**, o que a colocava
# como contrato público: qualquer consumidor podia passar a lê-la e o cubo
# publicado dependia de uma tabela chamada "tmp". Além disso ela existia em dois
# namespaces (`gold` e `dimon`), com 66.570 linhas num e 0 no outro.
#
# Agora é uma auxiliar de detalhe na Silver, com nome que diz o que é. Não é
# cubo e não deve ser consumida por dashboard: quem consome é a regra que a
# constrói, no mesmo run.

VM_MONTHLY_TABLE = "vm_monthly_history"
VM_MONTHLY_LAYER = layers.SILVER
#: Nome legado, mantido só para limpeza. Não escrever nele.
LEGACY_VM_MONTHLY_TABLE = "_tmp_vm_monthly"
VM_MONTHLY_HISTORY_MONTHS = 8

async def _drop_legacy_vm_monthly() -> None:
    """Remove as cópias legadas da auxiliar de VM dos namespaces Gold e `dimon`.

    A tabela existia em `iceberg.gold` (66.570 linhas) e `iceberg.dimon`
    (0 linhas). Enquanto a cópia em Gold existisse, um consumidor que a
    encontrasse continuaria lendo um contrato público com nome de temporária.
    A remoção é defensiva: falha não derruba a materialização da auxiliar nova,
    porque a tabela publicada (`dest_dm_cost_saving_desligue_vm`) não depende dela.
    """
    for namespace in (GOLD, layers.LEGACY_GOLD, "dimon"):
        try:
            await execute(
                f"DROP TABLE IF EXISTS iceberg.{namespace}.{LEGACY_VM_MONTHLY_TABLE}",
                timeout_s=120,
            )
        except Exception as exc:  # noqa: BLE001 — limpeza não é caminho crítico
            logger.warning(
                "Não foi possível remover %s.%s: %s",
                namespace,
                LEGACY_VM_MONTHLY_TABLE,
                exc,
            )


async def prepare_vm_monthly(
    progress: Any | None = None,
    *,
    months: list[str] | None = None,
    sources: list[GoldMartRuleSource] | None = None,
) -> int:
    """Popula gold_mart._tmp_vm_monthly com histórico de VMs reais mês a mês.

    ``months`` opcional: lista explícita de períodos YYYYMM a coletar (ex. para
    reprocessar o histórico). Default: últimos ``VM_MONTHLY_HISTORY_MONTHS``
    meses a partir de hoje.

    Quando a regra Gold possui Origens configuradas, seus filtros declarativos
    (inclusive ``subaccountname``) são aplicados à Gold antes de montar o
    histórico. Assim o SQL customizado respeita os filtros editados no portal.
    """
    await execute(f"DROP TABLE IF EXISTS iceberg.{VM_MONTHLY_LAYER}.{VM_MONTHLY_TABLE}", timeout_s=120)
    await _drop_legacy_vm_monthly()
    await execute(f"""
    CREATE TABLE iceberg.{VM_MONTHLY_LAYER}.{VM_MONTHLY_TABLE} (
        resource_id VARCHAR,
        vm_name VARCHAR,
        connection_code VARCHAR,
        subscription_name VARCHAR,
        billing_period VARCHAR,
        total_hours DOUBLE,
        total_cost DOUBLE
    )
    """, timeout_s=120)

    if months is None:
        # Use periods actually present in Silver. A rolling calendar window
        # can silently exclude the only imported month (e.g. 202512 in DEV).
        available = await execute(
            "SELECT DISTINCT _billing_period AS p FROM iceberg.silver.dest_modelo_focus "
            "WHERE _billing_period IS NOT NULL ORDER BY 1",
            timeout_s=120,
        )
        months = [str(row.get("p")) for row in available if row.get("p")]

    source_filters: list[str] = []
    if sources:
        schema = await load_gold_schema("dest_modelo_focus")
        for source in sources:
            golds = list(getattr(source, "gold_tables", None) or [])
            if not golds and getattr(source, "gold_table", None):
                golds = [source.gold_table]
            if "dest_modelo_focus" in golds:
                source_filters.append(_source_clause(schema, source, gold_table="dest_modelo_focus"))
    configured_filter = (
        "AND (" + " OR ".join(f"({clause})" for clause in source_filters) + ")"
        if source_filters else ""
    )

    total = 0
    for period in months:
        if progress:
            progress(0, 1, f"Preparando VMs mensais {period}")
        # Remove o período antes de inserir (idempotente — evita duplicatas
        # se a preparação rodar mais de uma vez para o mesmo mês).
        await execute(
            f"DELETE FROM iceberg.{VM_MONTHLY_LAYER}.{VM_MONTHLY_TABLE} WHERE billing_period = '{period}'",
            timeout_s=300,
        )
        await execute(f"""
        INSERT INTO iceberg.{VM_MONTHLY_LAYER}.{VM_MONTHLY_TABLE}
        SELECT
            CAST(b."ResourceId" AS VARCHAR) AS resource_id,
            element_at(split(CAST(b."ResourceId" AS VARCHAR), '/'), -1) AS vm_name,
            b.cloud_origem AS connection_code,
            MAX(b."SubAccountName") AS subscription_name,
            b._billing_period AS billing_period,
            SUM(COALESCE(TRY_CAST(b."ConsumedQuantity" AS DOUBLE), 0)) AS total_hours,
            SUM(COALESCE(TRY_CAST(b."BilledCost" AS DOUBLE), 0)) AS total_cost
        FROM iceberg.silver.dest_modelo_focus b
        WHERE b._billing_period = '{period}'
          AND b.cloud_origem IN ('azure-ti', 'azure-4p')
          AND LOWER(CAST(b."ServiceSubcategory" AS VARCHAR)) LIKE '%virtual machines%'
          AND POSITION('virtualMachines' IN CAST(b."ResourceId" AS VARCHAR)) > 0
          AND NOT regexp_like(element_at(split(CAST(b."ResourceId" AS VARCHAR), '/'), -1), '^[0-9a-f]{{32}}$')
          {configured_filter}
        GROUP BY CAST(b."ResourceId" AS VARCHAR), b.cloud_origem, b._billing_period
        """, timeout_s=300)
        result = await execute(
            f"SELECT COUNT(*) AS c FROM iceberg.{VM_MONTHLY_LAYER}.{VM_MONTHLY_TABLE} WHERE billing_period = '{period}'",
            timeout_s=120,
        )
        rows = int((result[0] if result else {}).get("c") or 0)
        total += rows
        logger.info("VM monthly %s: %d linhas", period, rows)

    return total


# ---------------------------------------------------------------------------
# Materialização histórica mês a mês da Gold de saving por desligue
# ---------------------------------------------------------------------------
# IMPORTANTE: a query da regra "saving_desligue_vm" compara o mês alvo com os
# meses ANTERIORES (>= 60 dias de histórico) para classificar padrões de
# desligue programado. Por isso ela DEVE ser reprocessada MÊS A MÊS: cada
# execução materializa UM período alvo (o mês fechado), usando os meses
# anteriores como referência. Não é possível materializar vários meses de uma
# vez com a mesma query — o padrão de cada VM depende do histórico que a
# antecede.

SAVING_GOLD_CODE = "saving_desligue_vm"

# Janela diária por recurso para validar ciclo de vida antes de classificar desligue.
DESLIGUE_LOOKBACK_DAYS = 30
DESLIGUE_EDGE_DAYS = 15

# Janela de validação diária (últimos N dias do mês fechado) para confirmar desligue.
DESLIGUE_VALIDATION_DAYS = 14
DESLIGUE_MIN_ACTIVE_DAYS = 5
DESLIGUE_MIN_ACTIVE_HOURS_PER_DAY = 4
DESLIGUE_MAX_UPTIME_PCT = 0.75
DESLIGUE_VALIDATION_CAPACITY_HOURS = DESLIGUE_VALIDATION_DAYS * 24

# Nós gerenciados de AKS (resource group MC_AKS*) não são candidatos a desligue isolado.
AKS_RESOURCE_GROUP_PATTERN = r"(?i)/resourcegroups/mc_aks"

# Padrões excluídos da lista de desligue (efêmeras, AKS gerenciado, sempre ligadas).
DESLIGUE_LIST_EXCLUDED_PADROES = (
    "CICLO_CURTO",
    "AKS_GERENCIADO",
    "LIGADA_24x7",
)

# Nomes conhecidos de recursos efêmeros (scans agentless etc.) — ocultos mesmo
# antes de reprocessar a Gold com a classificação CICLO_CURTO.
DESLIGUE_EPHEMERAL_NAME_PATTERNS = (
    r"(?i)^prismacloud-scan-",
)


def desligue_list_filter_sql(*, alias: str = "") -> str:
    """Predicado SQL para ocultar VMs efêmeras e 24×7 da lista de desligue."""
    prefix = f"{alias}." if alias else ""
    excluded = ", ".join(_quote_literal(p) for p in DESLIGUE_LIST_EXCLUDED_PADROES)
    padrao_filter = f"COALESCE({prefix}padrao, '') NOT IN ({excluded})"
    name_ref = f"COALESCE(CAST({prefix}resource_name AS VARCHAR), '')"
    ephemeral_filters = " AND ".join(
        f"NOT regexp_like({name_ref}, '{pat}')" for pat in DESLIGUE_EPHEMERAL_NAME_PATTERNS
    )
    return f"({padrao_filter}) AND ({ephemeral_filters})"


def _desligue_window_dates(period: str) -> tuple[str, str]:
    """Retorna (início, fim) ISO da janela de análise diária do período YYYYMM."""
    import calendar as _cal
    from datetime import date as _date, timedelta as _td

    y = int(period[:4])
    m = int(period[4:6])
    end = _date(y, m, _cal.monthrange(y, m)[1])
    start = end - _td(days=DESLIGUE_LOOKBACK_DAYS - 1)
    return start.isoformat(), end.isoformat()


def _desligue_validation_dates(period: str) -> tuple[str, str]:
    """Retorna (início, fim) ISO dos últimos DESLIGUE_VALIDATION_DAYS do mês YYYYMM."""
    import calendar as _cal
    from datetime import date as _date, timedelta as _td

    y = int(period[:4])
    m = int(period[4:6])
    end = _date(y, m, _cal.monthrange(y, m)[1])
    start = end - _td(days=DESLIGUE_VALIDATION_DAYS - 1)
    return start.isoformat(), end.isoformat()


def evaluate_daily_quality(
    daily_rows: list[dict[str, Any]],
    *,
    validation_start: str,
    validation_end: str,
    total_hours: float | None = None,
    baseline_hours: float | None = None,
) -> dict[str, Any]:
    """Calcula métricas de qualidade diária na janela de validação (14 dias)."""
    from datetime import date as _date

    vs = _date.fromisoformat(validation_start)
    ve = _date.fromisoformat(validation_end)
    window_hours = 0.0
    active_days = 0
    for row in daily_rows or []:
        day_raw = str(row.get("day") or "")[:10]
        if not day_raw:
            continue
        day = _date.fromisoformat(day_raw)
        if day < vs or day > ve:
            continue
        hours = float(row.get("hours") or 0)
        window_hours += hours
        if hours >= DESLIGUE_MIN_ACTIVE_HOURS_PER_DAY:
            active_days += 1
    window_uptime_pct = (
        window_hours / DESLIGUE_VALIDATION_CAPACITY_HOURS
        if DESLIGUE_VALIDATION_CAPACITY_HOURS
        else 0.0
    )
    monthly_uptime_pct = None
    if total_hours is not None and baseline_hours:
        monthly_uptime_pct = total_hours / baseline_hours
    return {
        "active_days": active_days,
        "window_hours": round(window_hours, 2),
        "window_uptime_pct": round(window_uptime_pct, 4),
        "monthly_uptime_pct": round(monthly_uptime_pct, 4) if monthly_uptime_pct is not None else None,
        "validation_start": validation_start,
        "validation_end": validation_end,
        "min_active_days": DESLIGUE_MIN_ACTIVE_DAYS,
        "min_active_hours_per_day": DESLIGUE_MIN_ACTIVE_HOURS_PER_DAY,
        "max_uptime_pct": DESLIGUE_MAX_UPTIME_PCT,
    }


def is_aks_managed_resource(resource_id: str | None) -> bool:
    return bool(re.search(AKS_RESOURCE_GROUP_PATTERN, str(resource_id or "")))


def evaluate_vm_lifecycle(
    first_seen: str | None,
    last_seen: str | None,
    window_start: str,
    window_end: str,
) -> tuple[bool, bool, str]:
    """Classifica ciclo de vida na janela diária (30d, folga 15d nas bordas)."""
    from datetime import date as _date

    if not first_seen or not last_seen:
        return True, True, "unknown"
    fs = _date.fromisoformat(str(first_seen)[:10])
    ls = _date.fromisoformat(str(last_seen)[:10])
    ws = _date.fromisoformat(window_start)
    we = _date.fromisoformat(window_end)
    edge_start = ws.toordinal() + DESLIGUE_EDGE_DAYS
    edge_end = we.toordinal() - DESLIGUE_EDGE_DAYS
    is_born = fs.toordinal() > ws.toordinal() and (
        fs.toordinal() <= edge_start
        or (we.toordinal() - fs.toordinal() + 1) < DESLIGUE_EDGE_DAYS
    )
    is_died = ls.toordinal() < edge_end
    if is_born:
        return True, False, "recently_born"
    if is_died:
        return False, True, "recently_died"
    return False, False, "ok"


def _lifecycle_sql_cte(
    window_start: str,
    window_end: str,
    *,
    validation_start: str | None = None,
    validation_end: str | None = None,
) -> str:
    """CTE lifecycle + métricas diárias de qualidade (Gold FOCUS)."""
    validation_start = validation_start or window_start
    validation_end = validation_end or window_end
    return f"""
        lifecycle AS (
            SELECT
                LOWER(TRIM(CAST(g."ResourceId" AS VARCHAR))) AS resource_key,
                MIN(CASE
                    WHEN COALESCE(TRY_CAST(g."ConsumedQuantity" AS DOUBLE), 0) > 0
                    THEN date_parse(CAST(g."ChargePeriodStart" AS VARCHAR), '%m/%d/%Y')
                END) AS first_seen,
                MAX(CASE
                    WHEN COALESCE(TRY_CAST(g."ConsumedQuantity" AS DOUBLE), 0) > 0
                    THEN date_parse(CAST(g."ChargePeriodStart" AS VARCHAR), '%m/%d/%Y')
                END) AS last_seen,
                COUNT(DISTINCT CASE
                    WHEN COALESCE(TRY_CAST(g."ConsumedQuantity" AS DOUBLE), 0) >= {DESLIGUE_MIN_ACTIVE_HOURS_PER_DAY}
                         AND date_parse(CAST(g."ChargePeriodStart" AS VARCHAR), '%m/%d/%Y')
                             BETWEEN DATE '{validation_start}' AND DATE '{validation_end}'
                    THEN date_format(
                        date_parse(CAST(g."ChargePeriodStart" AS VARCHAR), '%m/%d/%Y'),
                        '%Y-%m-%d'
                    )
                END) AS active_days,
                SUM(CASE
                    WHEN date_parse(CAST(g."ChargePeriodStart" AS VARCHAR), '%m/%d/%Y')
                         BETWEEN DATE '{validation_start}' AND DATE '{validation_end}'
                    THEN COALESCE(TRY_CAST(g."ConsumedQuantity" AS DOUBLE), 0)
                    ELSE 0
                END) AS window_hours
            FROM iceberg.silver.dest_modelo_focus g
            WHERE g."ChargePeriodStart" IS NOT NULL
              AND LOWER(CAST(g."ServiceSubcategory" AS VARCHAR)) LIKE '%virtual machines%'
              AND (g."ChargeCategory" = 'Usage' OR g."ChargeCategory" IS NULL)
              AND POSITION('/virtualMachines/' IN CAST(g."ResourceId" AS VARCHAR)) > 0
              AND date_parse(CAST(g."ChargePeriodStart" AS VARCHAR), '%m/%d/%Y')
                  BETWEEN DATE '{window_start}' AND DATE '{window_end}'
            GROUP BY 1
        )"""


def _lifecycle_flags_sql(window_start: str, window_end: str) -> tuple[str, str]:
    """Expressões SQL para is_recently_born / is_recently_died."""
    born = f"""(
            lc.first_seen IS NULL
            OR (
                lc.first_seen > DATE '{window_start}'
                AND (
                    lc.first_seen <= DATE '{window_start}' + INTERVAL '{DESLIGUE_EDGE_DAYS}' DAY
                    OR date_diff('day', lc.first_seen, DATE '{window_end}') < {DESLIGUE_EDGE_DAYS}
                )
            )
        )"""
    died = f"(lc.last_seen IS NULL OR lc.last_seen < DATE '{window_end}' - INTERVAL '{DESLIGUE_EDGE_DAYS}' DAY)"
    return born, died


# Comentário injetado no topo do sql_template da regra (visível em Gold → editar).
SAVING_TEMPLATE_HEADER = """--
-- ⚠ QUERY DE REPROCESSAMENTO MÊS A MÊS (NÃO RODAR PARA VÁRIOS MESES DE UMA VEZ)
-- Esta regra classifica o PADRÃO de desligue de cada VM comparando o mês alvo
-- (o MÊS ANTERIOR ao atual = mês fechado) com os meses anteriores da tabela
-- auxiliar gold_mart._tmp_vm_monthly. Cada reprocess materializa APENAS UM período.
-- Para popular o histórico (ex.: jan/2026 → mês atual), reprocesse mês a mês
-- via /scheduled-shutdown/reprocess-history ou reprocesse a regra a cada mês
-- fechado. O padrão exige >= 3 meses de histórico (60+ dias) por VM.
-- Além disso, exige janela diária de 30 dias por recurso com folga de 15 dias
-- nas bordas (sem nascimento/morte recente na janela).
-- Confirmação de desligue exige 14 dias de consumo diário com >=5 dias materiais
-- (>=4h/dia), uptime <75% na janela e no mês, e exclui AKS gerenciado (MC_AKS*).
-- A Gold mantém todas as subscriptions para diagnóstico. Os cálculos do
-- dashboard só consideram DEV, HML/HMP, TEST, SANDBOX, QA, UAT, POC ou
-- STAGING; PROD/PRD/PRO ficam fora dos totais de saving (filtro na Origem Gold).
--


"""


async def materialize_saving_history(
    progress: Any | None = None,
    *,
    periods: list[str] | None = None,
    sources: list[GoldMartRuleSource] | None = None,
) -> dict[str, Any]:
    """Materializa a Gold de saving por desligue MÊS A MÊS.

    Prepara a tabela auxiliar UMA única vez (todos os meses do histórico) e,
    para cada período alvo, roda a query com o mês alvo e insere na Gold
    final. Assim a Gold acumula janeiro → mês atual com classificação por VM.

    Meses com menos de 3 meses de histórico (ex.: jan/2026, dez/2025) entram
    como ``POUCO_HISTORICO`` — são incluídos na Gold, mas não recebem o
    padrão ``DESLIGUE_PROGRAMADO`` (exige >= 3 meses / 60+ dias).

    ``periods`` opcional: lista YYYYMM a materializar (default: todos os
    disponíveis na Gold até o mês anterior ao atual).

    ``sources`` aplica os filtros declarativos das Origens configuradas na
    regra Gold, inclusive filtros por ``subaccountname``.
    """
    from datetime import date as _date
    today = _date.today()
    cur = f"{today.year:04d}{today.month:02d}"

    if periods is None:
        # Períodos disponíveis na Gold até o mês anterior ao atual.
        avail = await execute(
            "SELECT DISTINCT _billing_period FROM iceberg.silver.dest_modelo_focus ORDER BY 1",
            timeout_s=240,
        )
        periods = [str(r["_billing_period"]) for r in avail if str(r["_billing_period"]) < cur]

    # Drop da Gold final para recomeçar do zero.
    await execute(
        f"DROP TABLE IF EXISTS iceberg.{GOLD}.dest_dm_cost_{SAVING_GOLD_CODE}",
        timeout_s=120,
    )
    await execute(f"""
    CREATE TABLE iceberg.{GOLD}.dest_dm_cost_{SAVING_GOLD_CODE} (
        resource_id VARCHAR,
        resource_name VARCHAR,
        resource_type VARCHAR,
        connection_code VARCHAR,
        subscription_name VARCHAR,
        organograma_nivel_4 VARCHAR,
        organograma_nivel_5 VARCHAR,
        _billing_period VARCHAR,
        billing_period VARCHAR,
        total_hours DOUBLE,
        baseline_hours DOUBLE,
        saved_hours DOUBLE,
        saving_brl DOUBLE,
        effective_price_hour DOUBLE,
        age_days INTEGER,
        is_new_vm BOOLEAN,
        first_seen_day VARCHAR,
        last_seen_day VARCHAR,
        is_recently_born BOOLEAN,
        is_recently_died BOOLEAN,
        active_days INTEGER,
        window_hours DOUBLE,
        window_uptime_pct DOUBLE,
        monthly_uptime_pct DOUBLE,
        padrao VARCHAR,
        is_desligue_programado BOOLEAN
    )
    """, timeout_s=120)

    # Prepara a auxiliar UMA vez com todos os meses (histórico completo).
    if progress:
        progress(0, len(periods), "Preparando histórico mensal de VMs (uma única vez)")
    await prepare_vm_monthly(progress=None, months=periods, sources=sources)

    summary: dict[str, Any] = {"periods": [], "rows": {}}
    for i, target in enumerate(periods):
        if progress:
            progress(i, len(periods), f"Materializando saving por desligue {target}")

        # Roda a query com o mês alvo e insere na Gold final.
        baseline = _baseline_hours(target)
        window_start, window_end = _desligue_window_dates(target)
        validation_start, validation_end = _desligue_validation_dates(target)
        born_sql, died_sql = _lifecycle_flags_sql(window_start, window_end)
        lifecycle_cte = _lifecycle_sql_cte(
            window_start,
            window_end,
            validation_start=validation_start,
            validation_end=validation_end,
        )
        max_uptime = DESLIGUE_MAX_UPTIME_PCT
        min_active = DESLIGUE_MIN_ACTIVE_DAYS
        validation_capacity = DESLIGUE_VALIDATION_CAPACITY_HOURS
        sql = f"""
        WITH stats AS (
            SELECT
                resource_id,
                vm_name,
                connection_code,
                subscription_name,
                COUNT(DISTINCT billing_period) AS months_history,
                AVG(total_hours) AS avg_hours,
                STDDEV(total_hours) AS stddev_hours,
                MIN(billing_period) AS first_period,
                MAX(CASE WHEN billing_period = '{target}' THEN total_hours END) AS cur_hours,
                MAX(CASE WHEN billing_period = '{target}' THEN total_cost END) AS cur_cost
            FROM iceberg.{VM_MONTHLY_LAYER}.{VM_MONTHLY_TABLE}
            WHERE billing_period <= '{target}'
            GROUP BY resource_id, vm_name, connection_code, subscription_name
        ),
        {lifecycle_cte},
        gold_dept AS (
            SELECT
                LOWER(TRIM(g."ResourceId")) AS rid,
                MAX(g.organograma_nivel_4) AS n4,
                MAX(g.organograma_nivel_5) AS n5
            FROM iceberg.silver.dest_modelo_focus g
            JOIN stats s ON LOWER(TRIM(g."ResourceId")) = LOWER(TRIM(s.resource_id))
            WHERE g._billing_period = '{target}'
            GROUP BY 1
        ),
        classified AS (
            SELECT
                s.resource_id,
                s.vm_name AS resource_name,
                CASE WHEN POSITION('virtualMachineScaleSets' IN s.resource_id) > 0 THEN 'VMSS' ELSE 'VM' END AS resource_type,
                s.connection_code,
                s.subscription_name,
                COALESCE(g.n4, 'SEM DEPARTAMENTO') AS organograma_nivel_4,
                COALESCE(g.n5, 'SEM PROJETO') AS organograma_nivel_5,
                '{target}' AS _billing_period,
                '{target}' AS billing_period,
                s.cur_hours AS total_hours,
                {baseline} AS baseline_hours,
                GREATEST(0, {baseline} - s.cur_hours) AS saved_hours,
                GREATEST(0, {baseline} - s.cur_hours) * (s.cur_cost / NULLIF(s.cur_hours, 0)) AS saving_brl,
                s.cur_cost / NULLIF(s.cur_hours, 0) AS effective_price_hour,
                date_diff('day',
                    date_parse(s.first_period || '01', '%Y%m%d'),
                    date_parse('{target}' || '01', '%Y%m%d') + INTERVAL '1' MONTH - INTERVAL '1' DAY
                ) AS age_days,
                (s.first_period >= date_format(date_add('month', -2, date_parse('{target}' || '01', '%Y%m%d')), '%Y%m')) AS is_new_vm,
                date_format(lc.first_seen, '%Y-%m-%d') AS first_seen_day,
                date_format(lc.last_seen, '%Y-%m-%d') AS last_seen_day,
                {born_sql} AS is_recently_born,
                {died_sql} AS is_recently_died,
                COALESCE(lc.active_days, 0) AS active_days,
                COALESCE(lc.window_hours, 0) AS window_hours,
                COALESCE(lc.window_hours, 0) / {validation_capacity}.0 AS window_uptime_pct,
                s.cur_hours / NULLIF({baseline}.0, 0) AS monthly_uptime_pct,
                CASE
                    WHEN regexp_like(s.resource_id, '{AKS_RESOURCE_GROUP_PATTERN}') THEN 'AKS_GERENCIADO'
                    WHEN COALESCE(lc.active_days, 0) < {min_active} THEN 'CICLO_CURTO'
                    WHEN COALESCE(lc.window_hours, 0) / {validation_capacity}.0 >= {max_uptime}
                         OR s.cur_hours / NULLIF({baseline}.0, 0) >= {max_uptime}
                         OR s.avg_hours > 700 THEN 'LIGADA_24x7'
                    WHEN s.months_history < 3
                         OR {born_sql}
                         OR ({died_sql} AND COALESCE(lc.active_days, 0) < {min_active}) THEN 'POUCO_HISTORICO'
                    WHEN s.avg_hours BETWEEN 100 AND 450
                         AND s.stddev_hours < 80
                         AND COALESCE(lc.active_days, 0) >= {min_active}
                         AND COALESCE(lc.window_hours, 0) / {validation_capacity}.0 < {max_uptime}
                         AND s.cur_hours / NULLIF({baseline}.0, 0) < {max_uptime}
                         AND NOT ({born_sql} OR {died_sql}) THEN 'DESLIGUE_PROGRAMADO'
                    WHEN s.avg_hours < 50 AND s.stddev_hours < 50 THEN 'VM_DESLIGADA'
                    WHEN s.stddev_hours > 150 THEN 'USO_IRREGULAR'
                    ELSE 'ANALISAR'
                END AS padrao,
                CASE
                    WHEN regexp_like(s.resource_id, '{AKS_RESOURCE_GROUP_PATTERN}') THEN FALSE
                    WHEN COALESCE(lc.active_days, 0) < {min_active} THEN FALSE
                    WHEN COALESCE(lc.window_hours, 0) / {validation_capacity}.0 >= {max_uptime}
                         OR s.cur_hours / NULLIF({baseline}.0, 0) >= {max_uptime}
                         OR s.avg_hours > 700 THEN FALSE
                    WHEN s.months_history < 3
                         OR {born_sql}
                         OR ({died_sql} AND COALESCE(lc.active_days, 0) < {min_active}) THEN FALSE
                    WHEN s.avg_hours BETWEEN 100 AND 450
                         AND s.stddev_hours < 80
                         AND COALESCE(lc.active_days, 0) >= {min_active}
                         AND COALESCE(lc.window_hours, 0) / {validation_capacity}.0 < {max_uptime}
                         AND s.cur_hours / NULLIF({baseline}.0, 0) < {max_uptime}
                         AND NOT ({born_sql} OR {died_sql}) THEN TRUE
                    ELSE FALSE
                END AS is_desligue_programado
            FROM stats s
            LEFT JOIN lifecycle lc ON lc.resource_key = LOWER(TRIM(s.resource_id))
            LEFT JOIN gold_dept g ON g.rid = LOWER(TRIM(s.resource_id))
            WHERE s.cur_hours IS NOT NULL
        )
        SELECT * FROM classified
        WHERE {desligue_list_filter_sql()}
        """
        await execute(
            f"INSERT INTO iceberg.{GOLD}.dest_dm_cost_{SAVING_GOLD_CODE} {sql}",
            timeout_s=600,
        )
        result = await execute(
            f"SELECT COUNT(*) AS c FROM iceberg.{GOLD}.dest_dm_cost_{SAVING_GOLD_CODE} WHERE _billing_period = '{target}'",
            timeout_s=120,
        )
        rows = int((result[0] if result else {}).get("c") or 0)
        summary["periods"].append(target)
        summary["rows"][target] = rows
        logger.info("Saving desligue %s: %d linhas", target, rows)

    return summary


def _baseline_hours(period: str) -> int:
    """Baseline 24×7 do mês (dias do mês × 24)."""
    import calendar as _cal
    y = int(period[:4])
    m = int(period[4:6])
    return _cal.monthrange(y, m)[1] * 24


def enqueue_rule_materialization(session: Any, *, rule_ids: list[int] | None = None, source: str = "worker") -> Any | None:
    """Create a QUEUED ``gold_reprocess`` EtlTask for active rules (all or a subset).

    Used by the worker to chain the Gold materialization (visão completa =
    realizado + forecast + rateio) right after a forecast/reprocess completes.
    Skips silently when no active rule matches or a Gold task is already
    queued/running (the manual /gold/reprocess would return 409 there).

    ``session`` must be a sync SQLAlchemy session (worker SyncSession).
    The caller owns the commit.
    """
    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.models.etl_task import EtlTask
    from app.models.gold_mart_rule import GoldMartRule

    query = session.query(GoldMartRule).filter(GoldMartRule.active == True)  # noqa: E712
    if rule_ids:
        query = query.filter(GoldMartRule.id.in_(rule_ids))
    rules = query.all()
    if not rules:
        return None
    running = session.execute(
        select(EtlTask.id).where(
            EtlTask.task_type == "gold_reprocess",
            EtlTask.status.in_(["QUEUED", "RUNNING"]),
        )
    ).scalars().first()
    if running:
        return None
    now = datetime.now(timezone.utc)
    task = EtlTask(task_type="gold_reprocess", status="QUEUED", errors=[])
    task.phase = f"Na fila: materializar {len(rules)} cubo(s) Gold (após {source})"
    task.started_at = now
    task.total_blobs = len(rules)
    task.processed_blobs = 0
    task.rows_destination = 0
    task.result_json = {
        "event_log": [{"at": now.isoformat(), "level": "INFO", "message": task.phase}],
        "rule_ids": [rule.id for rule in rules],
        "chained_from": source,
    }
    session.add(task)
    session.flush()  # popula task.id para o phase do chamador
    return task


async def _run_keyed_rules(chaves: list[str], *, run_id: int | None = None, progress: Any | None = None) -> dict[str, Any]:
    """Executa as regras com materializer_key. Exposta para teste da fila.

    É o mesmo laço de `materialize_gold_cubes`, isolado para que o teste prove que
    o baseline é medido uma vez por run e passado a todos os financeiros.
    """
    from app.services.gold_governance import cost_baseline
    from app.services.gold_materializers import (
        execution_order,
        is_registered,
        runner_for,
        spec_for,
    )

    financeiros = [c for c in chaves if is_registered(c) and spec_for(c).financial]
    baseline = await cost_baseline() if financeiros else None
    resultados: dict[str, Any] = {}
    for chave in execution_order(chaves):
        resultados[chave] = await runner_for(chave)(
            run_id=run_id,
            expected_total=baseline.total if baseline is not None else None,
            baseline=baseline,
            progress=progress,
        )
    return resultados
