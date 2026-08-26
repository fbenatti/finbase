"""Materialize one pre-aggregated Dimon mart (dest_dm_cost_<code>) from Gold tables.

A rule selects one or more Gold tables (dest_modelo_focus, *_planning, ...),
applies filters over the already-normalized Gold data and pre-aggregates by
analytic dimensions so dashboards read a thin, fast table.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from app.models.dimon_rule import DimonRule, DimonRuleSource
from app.services import iceberg_service as iceberg
from app.services.trino_service import execute

logger = logging.getLogger(__name__)

# As Dimons vivem em um namespace Iceberg próprio (iceberg.dimon.*), separado
# das Golds (iceberg.gold.*). O materializador cria/swap aqui; os dashboards e
# a API consultam iceberg.dimon.dest_dm_cost_*.
DIMON_NAMESPACE = "dimon"

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
    # for small naming differences: the Gold exposes departamento while the
    # planning mirror only keeps departamento_nivel_1..7. When a source unites
    # both tables, a filter on departamento must apply on the planning side
    # as departamento_nivel_1 so the union stays a single clean dataset.
    key = str(requested or "").strip().lower()
    if key == "departamento":
        candidate = schema.get("departamento_nivel_1")
        if candidate:
            return candidate
    if key.startswith("departamento_nivel_"):
        candidate = schema.get(key)
        if candidate:
            return candidate
    return None


def dimon_table_name(code: str) -> str:
    """dest_dm_cost_<dimon_code> - the original code is preserved in the rule;
    the physical table name uses an underscore-safe variant (hyphens would
    break unquoted Iceberg identifiers in Trino SQL)."""
    safe = normalize_code(code).replace("-", "_")
    return f"dest_dm_cost_{safe}"


def normalize_code(raw: str) -> str:
    """Lowercase, strip, and collapse unsafe characters for the table name."""
    value = str(raw or "").strip().lower()
    value = re.sub(r"[^a-z0-9_\-]+", "_", value).strip("_")
    return value or "dimon"


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
    return schema.get(str(name or "").lower())


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


def _filter_clause(schema: dict[str, str], conditions: list[dict[str, Any]]) -> str:
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
        col_sql = f"lower(CAST({_quote_ident(column)} AS VARCHAR))"
        if operator in {"EMPTY", "IS_EMPTY"}:
            clauses.append(f"({_quote_ident(column)} IS NULL OR TRIM(CAST({_quote_ident(column)} AS VARCHAR)) = '')")
        elif operator in {"NOT_EMPTY", "IS_NOT_EMPTY"}:
            clauses.append(f"({_quote_ident(column)} IS NOT NULL AND TRIM(CAST({_quote_ident(column)} AS VARCHAR)) <> '')")
        elif operator in {"CONTAINS", "~"} and values:
            likes = " OR ".join(f"{col_sql} LIKE {_quote_literal('%' + v.lower() + '%')}" for v in values)
            clauses.append(f"({likes})")
        elif operator in {"NOT_CONTAINS"} and values:
            likes = " AND ".join(f"{col_sql} NOT LIKE {_quote_literal('%' + v.lower() + '%')}" for v in values)
            clauses.append(f"({likes})")
        elif operator in {"NOT_IN", "!="} and values:
            literals = ", ".join(_quote_literal(v.lower()) for v in values)
            clauses.append(f"({col_sql} NOT IN ({literals}) OR {_quote_ident(column)} IS NULL)")
        elif operator in {"=", "=="} and values:
            clauses.append(f"{col_sql} = {_quote_literal(values[0].lower())}")
        elif values:
            literals = ", ".join(_quote_literal(v.lower()) for v in values)
            clauses.append(f"{col_sql} IN ({literals})")
    return " AND ".join(clauses) if clauses else "1=1"


def _source_clause(schema: dict[str, str], source: DimonRuleSource) -> str:
    conditions, missing = _normalized_conditions(schema, (source.filter_json or {}).get("conditions") or [])
    if missing:
        raise RuntimeError(f"Campos inexistentes na fonte {source.id} ({source.gold_table}): {', '.join(missing)}")
    return _filter_clause(schema, conditions)


def _period_column(schema: dict[str, str]) -> str | None:
    return _resolve_column(schema, "_billing_period") or _resolve_column(schema, "period")


def _numeric_columns(schema: dict[str, str]) -> set[str]:
    """Column names (lowercased) whose Trino type is numeric - excluded from GROUP BY."""
    return {
        name.lower() for name, typ in schema.items()
        if any(token in str(typ).lower() for token in ("int", "double", "real", "decimal", "float", "bigint"))
    }


async def load_gold_schema(gold_table: str) -> dict[str, str]:
    rows = await execute(f"DESCRIBE iceberg.gold.{gold_table}", timeout_s=30)
    return _schema_map(rows)


async def load_gold_schema_typed(gold_table: str) -> dict[str, dict[str, str]]:
    """DESCRIBE a Gold and keep the REAL column type (double, varchar, ...).

    load_gold_schema discards types (only name resolution), which made
    /dimon/gold-schema report type = column name and broke the
    numeric-measure filter in the pivot editor. This variant preserves it.
    """
    rows = await execute(f"DESCRIBE iceberg.gold.{gold_table}", timeout_s=30)
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
      FROM iceberg.gold.{gold_table}
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
# multi-million-row Golds, so the default keeps the drill-down dimensions that
# dashboards actually use.
_ANALYTIC_DIMENSIONS = (
    "_billing_period",
    "period",
    "tipo",
    "cloud_origem",
    "departamento",
    "departamento_nivel_1",
    "departamento_nivel_2",
    "departamento_nivel_3",
    "departamento_nivel_4",
    "departamento_nivel_5",
    "departamento_nivel_6",
    "departamento_nivel_7",
    "servicecategory",
    "servicename",
    "servicesubcategory",
    "skumeter",
    "subaccountname",
    "chargecategory",
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


def _side_select(gold: str, schema: dict[str, str], dims: list[str], measure_aliases: list[tuple[str, str]], percent: float, period_physical: dict[str, str]) -> str:
    # Build one side of the UNION ALL. measure_aliases is (measure, alias)
    # with each measure scaled by percent and projected as _m0, _m1...
    exprs: list[str] = []
    for dim in dims:
        key = dim.lower()
        if key in schema:
            exprs.append(f"{_quote_ident(schema[key])} AS {_quote_ident(dim)}")
        elif key in {"period", "_billing_period"} and period_physical.get(gold) and period_physical[gold] != dim:
            exprs.append(f"{_quote_ident(period_physical[gold])} AS {_quote_ident(dim)}")
        else:
            exprs.append(f"CAST(NULL AS VARCHAR) AS {_quote_ident(dim)}")
    for measure, alias in measure_aliases:
        measure_expr = _value_expr(schema, measure)
        scaled = f"({measure_expr}) * {percent}" if percent != 1.0 else measure_expr
        exprs.append(f"{scaled} AS {alias}")
    return ", ".join(exprs)


async def _materialize_rule(rule: DimonRule, *, run_id: int, progress: Any | None = None) -> int:
    """Build the pre-aggregated Dimon table for one rule and swap it atomically."""
    table = dimon_table_name(rule.code)
    schema_by_gold: dict[str, dict[str, str]] = {}
    for source in rule.sources or []:
        golds = list(getattr(source, "gold_tables", None) or [])
        if not golds and getattr(source, "gold_table", None):
            golds = [source.gold_table]
        for gold in golds:
            schema_by_gold[gold] = await load_gold_schema(gold)

    dims = _merged_dimensions(schema_by_gold, list(getattr(rule, "dimensions", None) or []))
    period_physical = {gold: (_period_column(schema) or "_billing_period") for gold, schema in schema_by_gold.items()}

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
            where = _source_clause(schema, source)
            percent = float(source.percent or 100) / 100.0
            select_exprs = _side_select(gold, schema, dims, measure_aliases, percent, period_physical)
            parts.append(f"SELECT {select_exprs} FROM iceberg.gold.{gold} WHERE {where}")

    if not parts:
        return 0
    union_sql = " UNION ALL ".join(f"({part})" for part in parts)
    group_by = ", ".join(_quote_ident(dim) for dim in dims)
    measure_sums = ", ".join(f"SUM({alias}) AS {_quote_ident(measure)}" for measure, alias in measure_aliases)
    full_sql = f"SELECT {group_by}, {measure_sums} FROM ({union_sql}) AS dimon_src GROUP BY {group_by}"

    staging = f"{table}_stg_{time.time_ns()}"
    period = period_physical.get((rule.sources or [])[0].gold_table) if rule.sources else None
    # Garante que iceberg.dimon.* exista antes do CREATE (namespace próprio das Dimons).
    iceberg.ensure_namespace(DIMON_NAMESPACE)
    create_sql = f"CREATE TABLE iceberg.{DIMON_NAMESPACE}.{staging} AS {full_sql}"
    if period:
        create_sql = f"CREATE TABLE iceberg.{DIMON_NAMESPACE}.{staging} WITH (partitioning = ARRAY['{period}']) AS {full_sql}"
    try:
        if progress:
            progress(0, 1, f"Agregando {rule.code} a partir de {len(rule.sources or [])} Gold(s)")
        await execute(create_sql, timeout_s=1800)
        result = await execute(f"SELECT COUNT(*) AS row_count FROM iceberg.{DIMON_NAMESPACE}.{staging}", timeout_s=180)
        rows = int((result[0] if result else {}).get("row_count") or 0)
        if rows:
            iceberg.swap_staged_table(table, staging, namespace=DIMON_NAMESPACE, known_row_count=rows)
        else:
            iceberg.cleanup_staged_table(staging, namespace=DIMON_NAMESPACE)
        return rows
    except Exception:
        try:
            iceberg.cleanup_staged_table(staging, namespace=DIMON_NAMESPACE)
        except Exception:
            pass
        raise


async def materialize_dimons(session: Any, *, run_id: int, progress: Any | None = None, rule_ids: list[int] | None = None) -> dict[str, Any]:
    from datetime import datetime, timezone
    from sqlalchemy.orm import selectinload

    query = session.query(DimonRule).options(selectinload(DimonRule.sources)).filter(DimonRule.active == True)  # noqa: E712
    if rule_ids:
        query = query.filter(DimonRule.id.in_(rule_ids))
    rules = query.all()

    details: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    written = 0
    for index, rule in enumerate(rules, start=1):
        if progress:
            progress(index - 1, len(rules), f"Materializando {rule.code}")
        try:
            rows = await _materialize_rule(rule, run_id=run_id, progress=progress)
            rule.materialization_table = dimon_table_name(rule.code)
            rule.materialization_rows = rows
            rule.materialization_status = "UPDATED" if rows else "EMPTY"
            rule.materialization_error = None
            rule.materialization_run_id = run_id
            rule.materialized_at = datetime.now(timezone.utc)
            details.append({"rule_id": rule.id, "code": rule.code, "table": dimon_table_name(rule.code), "rows": rows})
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
    return {"rules": len(rules), "rows": written, "details": details, "failed": failed}


def materialize_dimons_sync(session: Any, *, run_id: int, progress: Any | None = None, rule_ids: list[int] | None = None) -> dict[str, Any]:
    return asyncio.run(materialize_dimons(session, run_id=run_id, progress=progress, rule_ids=rule_ids))
