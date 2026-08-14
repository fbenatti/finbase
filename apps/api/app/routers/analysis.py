"""
Analysis Router — query the Data Lakehouse via Trino, PostgreSQL sources, and destination tables.

Endpoints:
  GET  /analysis/tables           → list available Iceberg tables (Bronze, Silver, Gold)
  POST /analysis/query            → run arbitrary SQL via Trino
  GET  /analysis/health           → health check for Trino/Nessie
  GET  /analysis/sources          → list connections with ingested data counts
  GET  /analysis/sources/{id}     → query ingested normalized data for a source
  GET  /analysis/destinations     → list destination tables with row counts
  GET  /analysis/destinations/{id}/data → query destination table data
"""

from __future__ import annotations

import json
import logging
import re
import os
import pandas as pd
from typing import Any, Literal
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from sqlalchemy import select, func, text, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.source_connection import SourceConnection
from app.models.ingested_blob import IngestedBlob
from app.models.pivot_view import PivotView
from app.models.dashboard import Dashboard, DashboardWidget
from app.services.iceberg_service import list_tables, get_catalog
from app.services.trino_service import execute as trino_execute, TrinoError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis", tags=["analysis"])


async def _analysis_physical_name(db: AsyncSession, table_id: int) -> str | None:
    """Resolve regular destinations and forecast planning pseudo-destinations."""
    row = (await db.execute(text(
        "SELECT physical_name FROM destination_table WHERE id = :id AND active = true"
    ), {"id": table_id})).first()
    if row:
        return str(row[0]).replace("-", "_").replace(".", "_")
    if table_id < 0:
        row = (await db.execute(text(
            "SELECT physical_name FROM destination_table WHERE id = :id AND active = true"
        ), {"id": abs(table_id)})).first()
        if row:
            return str(row[0]).replace("-", "_").replace(".", "_") + "_planning"
    return None


class QueryRequest(BaseModel):
    sql: str
    catalog: str = "iceberg"


class QueryResponse(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int


# ---------------------------------------------------------------------------
# Server-side Pivot (Trino + Iceberg only)
# ---------------------------------------------------------------------------
PivotAggregation = Literal["SUM", "COUNT", "COUNT_DISTINCT", "AVG", "MIN", "MAX"]


class PivotMeasure(BaseModel):
    column: str
    aggregation: PivotAggregation = "SUM"
    label: str | None = None


class PivotFilter(BaseModel):
    column: str
    values: list[str | int | float | bool] = []
    operator: Literal["IN", "NOT_IN", "CONTAINS", "NOT_CONTAINS"] = "IN"


class PivotSort(BaseModel):
    """Ordering directive for a pivot axis.

    ``column`` may be a schema dimension name or a measure alias ``m0``..``mk``
    (aliases follow the ordering of the ``measures`` list). ``direction`` is
    ``asc``/``desc``. Sorting by a measure alias ranks rows/columns by their
    aggregated value.
    """
    column: str
    direction: Literal["asc", "desc"] = "asc"


class PivotRequest(BaseModel):
    destination_table_id: int
    rows: list[str] = []
    columns: list[str] = []
    measures: list[PivotMeasure] = [PivotMeasure(column="EffectiveCost", aggregation="SUM")]
    filters: list[PivotFilter] = []
    row_limit: int = 500
    column_limit: int = 60
    include_totals: bool = True
    sort_rows: list[PivotSort] = []
    sort_columns: list[PivotSort] = []


class PivotUnionRequest(BaseModel):
    """Union two (or N) Gold/planning tables into a single pivotable view.

    ``destination_table_ids`` accepts regular destination ids and negative
    planning pseudo-ids. Each id is resolved via ``_analysis_physical_name``.
    An ``origem`` column (realizado/forecast) is injected so the user can break
    the result by source.
    """
    destination_table_ids: list[int]
    rows: list[str] = []
    columns: list[str] = []
    measures: list[PivotMeasure] = [PivotMeasure(column="EffectiveCost", aggregation="SUM")]
    filters: list[PivotFilter] = []
    row_limit: int = 500
    column_limit: int = 60
    include_totals: bool = True
    origin_column: str = "origem"
    sort_rows: list[PivotSort] = []
    sort_columns: list[PivotSort] = []


class PivotDrillDownRequest(PivotRequest):
    cell_filters: dict[str, str | int | float | bool | None] = {}
    limit: int = 100
    offset: int = 0


class PivotViewCreate(BaseModel):
    name: str
    destination_table_id: int
    config: dict[str, Any]


class PivotRecordRequest(BaseModel):
    destination_table_id: int
    columns: list[str] = []
    filters: list[PivotFilter] = []
    limit: int = 1000
    offset: int = 0


class LakehouseSearchRequest(BaseModel):
    destination_table_id: int
    term: str
    limit: int = 100
    offset: int = 0
    filters: list[PivotFilter] = []


ChartType = Literal["kpi", "line", "area", "bar", "stacked_bar", "pie", "donut", "heatmap", "map"]


class DashboardCreate(BaseModel):
    name: str
    pivot_view_id: int | None = None
    global_filters: dict[str, Any] = {}
    layout: dict[str, Any] = {}


class DashboardWidgetCreate(BaseModel):
    dashboard_id: int
    pivot_view_id: int | None = None
    title: str
    chart_type: ChartType = "line"
    config: dict[str, Any] = {}
    position: dict[str, Any] = {}


class WidgetQueryRequest(BaseModel):
    pivot_view_id: int
    chart_type: ChartType = "line"
    config: dict[str, Any] = {}
    global_filters: dict[str, Any] = {}
    source_ids: list[int] = []


class ExpectedViewRequest(BaseModel):
    director: str = "DIR DADOS E INTELIGENCIA ARTIFICIAL"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str | int | float | bool) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


async def _pivot_table_context(db: AsyncSession, destination_table_id: int) -> tuple[str, dict[str, str]]:
    safe_name = await _analysis_physical_name(db, destination_table_id)
    if not safe_name:
        raise HTTPException(status_code=404, detail="Tabela destino ativa não encontrada")
    try:
        schema = await trino_execute(f"DESCRIBE iceberg.gold.{safe_name}")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Trino é obrigatório para tabela dinâmica: {exc}")
    columns = {str(c.get("Column") or c.get("column_name") or c.get("column")): str(c.get("Type") or c.get("data_type") or "") for c in schema}
    if not columns:
        raise HTTPException(status_code=422, detail="A tabela Gold não possui schema disponível no Trino")
    return safe_name, columns


def _validate_pivot_request(req: PivotRequest, schema: dict[str, str]) -> tuple[list[str], list[str], list[PivotMeasure]]:
    dimensions = req.rows + req.columns
    names = [*dimensions, *(f.column for f in req.filters), *(m.column for m in req.measures)]
    invalid = sorted({name for name in names if name not in schema})
    if invalid:
        raise HTTPException(status_code=422, detail=f"Colunas inválidas para esta tabela: {', '.join(invalid)}")
    if len(set(req.rows)) != len(req.rows) or len(set(req.columns)) != len(req.columns):
        raise HTTPException(status_code=422, detail="Uma dimensão não pode ser repetida")
    if set(req.rows).intersection(req.columns):
        raise HTTPException(status_code=422, detail="Uma coluna não pode estar simultaneamente em Linhas e Colunas")
    if not req.measures:
        raise HTTPException(status_code=422, detail="Informe ao menos uma medida")
    for measure in req.measures:
        numeric_type = any(token in schema[measure.column].lower() for token in ("int", "double", "real", "decimal", "float", "bigint"))
        if measure.aggregation != "COUNT" and not numeric_type:
            raise HTTPException(status_code=422, detail=f"{measure.column} não é uma medida numérica")
    rows = list(req.rows)
    # Financial totals must never silently mix currencies. Currency is injected
    # into the row axis unless the user has explicitly constrained it.
    monetary = {"EffectiveCost", "BilledCost", "ListCost", "ContractedCost"}
    filtered = {f.column for f in req.filters if f.values}
    if any(m.column in monetary for m in req.measures) and "Currency" in schema and "Currency" not in dimensions and "Currency" not in filtered:
        rows.append("Currency")
    return rows, list(req.columns), req.measures


def _canonicalize_request(req: PivotRequest, schema: dict[str, str]) -> None:
    """Accept UI-friendly casing while emitting the exact Trino identifiers."""
    by_lower = {name.lower(): name for name in schema}
    def canonical(name: str) -> str:
        return by_lower.get(name.lower(), name)
    req.rows = [canonical(name) for name in req.rows]
    req.columns = [canonical(name) for name in req.columns]
    req.measures = [measure.model_copy(update={"column": canonical(measure.column)}) for measure in req.measures]
    req.filters = [filter_item.model_copy(update={"column": canonical(filter_item.column)}) for filter_item in req.filters]
    req.sort_rows = [
        spec.model_copy(update={"column": canonical(spec.column)}) if not spec.column.startswith("m") or not spec.column[1:].isdigit() else spec
        for spec in req.sort_rows
    ]
    req.sort_columns = [
        spec.model_copy(update={"column": canonical(spec.column)}) if not spec.column.startswith("m") or not spec.column[1:].isdigit() else spec
        for spec in req.sort_columns
    ]


def _where_clause(filters: list[PivotFilter], cell_filters: dict[str, Any] | None = None) -> str:
    clauses: list[str] = []
    for item in filters:
        if item.values:
            values = ', '.join(_quote_literal(v) for v in item.values)
            if item.operator == "NOT_IN":
                clauses.append(f"({_quote_ident(item.column)} IS NULL OR {_quote_ident(item.column)} NOT IN ({values}))")
            elif item.operator == "CONTAINS":
                clauses.append("(" + " OR ".join(f"lower(CAST({_quote_ident(item.column)} AS VARCHAR)) LIKE {_quote_literal('%' + str(v).lower() + '%')}" for v in item.values) + ")")
            elif item.operator == "NOT_CONTAINS":
                clauses.append("(" + " AND ".join(f"lower(CAST({_quote_ident(item.column)} AS VARCHAR)) NOT LIKE {_quote_literal('%' + str(v).lower() + '%')}" for v in item.values) + ")")
            else:
                clauses.append(f"{_quote_ident(item.column)} IN ({values})")
    for column, value in (cell_filters or {}).items():
        if value is None:
            clauses.append(f"{_quote_ident(column)} IS NULL")
        else:
            clauses.append(f"{_quote_ident(column)} = {_quote_literal(value)}")
    return " WHERE " + " AND ".join(clauses) if clauses else ""


def _measure_sql(measure: PivotMeasure, index: int) -> tuple[str, str]:
    alias = f"m{index}"
    column = _quote_ident(measure.column)
    expression = {
        "SUM": f"SUM(COALESCE(TRY_CAST({column} AS DOUBLE), 0))",
        "COUNT": "COUNT(*)",
        "COUNT_DISTINCT": f"COUNT(DISTINCT {column})",
        "AVG": f"AVG(TRY_CAST({column} AS DOUBLE))",
        "MIN": f"MIN({column})",
        "MAX": f"MAX({column})",
    }[measure.aggregation]
    return f"{expression} AS {alias}", alias


def _build_order_by(
    sort_specs: list[PivotSort],
    rows: list[str],
    columns: list[str],
    num_measures: int,
) -> str:
    """Build an ORDER BY clause from validated sort specs.

    ``sort_specs`` entries reference either a dimension present in ``rows``/
    ``columns`` (addressed by its alias ``d{index}``) or a measure alias
    ``m0..m{num_measures-1}``. Any unknown column is rejected with a 422 to
    prevent SQL injection. Returns an empty string when no sort is requested.
    """
    if not sort_specs:
        return ""
    # Row dimensions occupy d0..d{len(rows)-1}; column dimensions follow.
    dim_index = {name: i for i, name in enumerate([*rows, *columns])}
    valid_measure_aliases = {f"m{i}" for i in range(num_measures)}
    terms: list[str] = []
    for spec in sort_specs:
        column = spec.column
        direction = spec.direction
        sql_dir = "ASC" if direction == "asc" else "DESC"
        if column in dim_index:
            terms.append(f"d{dim_index[column]} {sql_dir}")
        elif column in valid_measure_aliases:
            terms.append(f"{column} {sql_dir}")
        else:
            raise HTTPException(
                status_code=422,
                detail=f"Coluna de ordenação inválida: {column}. Use uma dimensão (linha/coluna) ou um alias de medida m0..m{num_measures - 1}.",
            )
    return " ORDER BY " + ", ".join(terms)


@router.get("/pivot/metadata/{destination_table_id}")
async def pivot_metadata(destination_table_id: int, db: AsyncSession = Depends(get_db)):
    """Return the real Gold schema and sensible FinOps field suggestions."""
    _, schema = await _pivot_table_context(db, destination_table_id)
    numeric = [name for name, typ in schema.items() if any(t in typ.lower() for t in ("int", "double", "real", "decimal", "float", "bigint"))]
    dimensions = [name for name in schema if name not in numeric]
    recommended = [name for name in ["_billing_period", "departamento", "cloud_origem", "ChargeCategory", "ServiceName", "RegionName", "SubscriptionName", "Currency"] if name in schema]
    effective = next((name for name in numeric if name.lower() == "effectivecost"), None)
    billed = next((name for name in numeric if name.lower() == "billedcost"), None)
    return {
        "destination_table_id": destination_table_id,
        "columns": [{"name": name, "type": typ, "numeric": name in numeric} for name, typ in schema.items()],
        "dimensions": dimensions,
        "measures": numeric,
        "recommended_dimensions": recommended,
        "default_measure": effective or billed or (numeric[0] if numeric else None),
    }


@router.post("/pivot/search")
async def search_pivot_table(req: LakehouseSearchRequest, db: AsyncSession = Depends(get_db)):
    """Search a term across every textual column of a Gold table in Trino."""
    if not req.term.strip():
        raise HTTPException(status_code=422, detail="Informe um texto para pesquisar")
    table_name, schema = await _pivot_table_context(db, req.destination_table_id)
    filter_request = PivotRequest(destination_table_id=req.destination_table_id, filters=req.filters)
    _canonicalize_request(filter_request, schema)
    invalid_filters = [item.column for item in filter_request.filters if item.column not in schema]
    if invalid_filters:
        raise HTTPException(status_code=422, detail=f"Colunas de filtro inválidas: {', '.join(invalid_filters)}")
    filter_sql = _where_clause(filter_request.filters)
    text_columns = [name for name, typ in schema.items() if any(token in typ.lower() for token in ("varchar", "char", "json"))]
    if not text_columns:
        return {"columns": list(schema), "rows": [], "total": 0, "matched_columns": []}
    term = _quote_literal(f"%{req.term.strip().lower()}%")
    order_candidates = [name for name in ("chargeperiodstart", "billingperiodstart", "date", "_billing_period") if name in schema]
    order = f" ORDER BY TRY_CAST({_quote_ident(order_candidates[0])} AS TIMESTAMP) DESC" if order_candidates else ""
    limit = max(1, min(req.limit, 500))
    offset = max(0, req.offset)
    lowered = req.term.strip().lower()
    # Keep the search as one bounded scan. A sequence of SELECT * scans (one
    # per text column) exhausts Trino memory on wide multi-million-row tables.
    try:
        search_text = "concat_ws(' ', " + ", ".join(f"coalesce(CAST({_quote_ident(column)} AS VARCHAR), '')" for column in text_columns) + ")"
        search_condition = f"lower({search_text}) LIKE {term}"
        combined_where = f" WHERE {search_condition}" + (f" AND {filter_sql[7:]}" if filter_sql else "")
        sql = f"SELECT * FROM iceberg.gold.{table_name}{combined_where} LIMIT {limit}"
        # A wide Gold table may require a full scan. Keep the request bounded
        # but allow enough time for the server-side scan to finish instead of
        # turning a slow, valid search into a misleading 503.
        rows = await trino_execute(sql, timeout_s=180)
        matched = [[name for name in text_columns if lowered in str(row.get(name) or "").lower()] for row in rows]
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Falha na busca do Trino: {exc}")
    return {
        "columns": list(rows[0].keys()) if rows else list(schema),
        "rows": rows,
        "total": len(rows),
        "total_is_estimate": True,
        "limit": limit,
        "offset": offset,
        "matched_columns": matched,
        "searched_columns": text_columns,
    }


def _build_pivot_result(
    records: list[dict[str, Any]],
    rows: list[str],
    columns: list[str],
    measures: list[PivotMeasure],
    include_totals: bool,
    req_rows: list[str] | None = None,
    sort_rows: list[PivotSort] | None = None,
    sort_columns: list[PivotSort] | None = None,
) -> dict[str, Any]:
    """Shape aggregated Trino records into the PivotResult contract."""
    cells: list[dict[str, Any]] = []
    row_keys: dict[tuple[Any, ...], None] = {}
    col_keys: dict[tuple[Any, ...], None] = {}
    aliases = [f"m{i}" for i in range(len(measures))]
    for record in records:
        row_key = tuple(record.get(f"d{i}") for i in range(len(rows)))
        col_key = tuple(record.get(f"d{i + len(rows)}") for i in range(len(columns)))
        row_keys[row_key] = None
        col_keys[col_key] = None
        cells.append({"row": list(row_key), "column": list(col_key), "values": [record.get(alias) for alias in aliases]})
    # Totals are computed from the aggregation result for SUM/COUNT. Other
    # aggregations are intentionally left blank because summing them is wrong.
    totals: dict[str, Any] = {"rows": {}, "columns": {}, "grand": [None] * len(measures)}
    additive = [m.aggregation in {"SUM", "COUNT"} for m in measures]
    for cell in cells:
        rk, ck = tuple(cell["row"]), tuple(cell["column"])
        rtotal = totals["rows"].setdefault(json.dumps(rk, default=str), [0 if ok else None for ok in additive])
        ctotal = totals["columns"].setdefault(json.dumps(ck, default=str), [0 if ok else None for ok in additive])
        for index, value in enumerate(cell["values"]):
            if additive[index]:
                numeric_value = float(value or 0)
                rtotal[index] += numeric_value
                ctotal[index] += numeric_value

    ordered_row_keys = _order_keys(list(row_keys), rows + columns, records, sort_rows, axis="row", other_count=len(columns))
    ordered_col_keys = _order_keys(list(col_keys), rows + columns, records, sort_columns, axis="col", other_count=len(rows))

    # Recompute grand total (sum across all cells) once, independent of order.
    for cell in cells:
        for index, value in enumerate(cell["values"]):
            if additive[index]:
                totals["grand"][index] = float(totals["grand"][index] or 0) + float(value or 0)

    return {
        "rows": rows,
        "columns": columns,
        "measures": [{"column": m.column, "aggregation": m.aggregation, "label": m.label or f"{m.aggregation} {m.column}"} for m in measures],
        "row_keys": [list(key) for key in ordered_row_keys],
        "column_keys": [list(key) for key in ordered_col_keys],
        "cells": cells,
        "totals": totals if include_totals else None,
        "record_count": len(records),
        "currency_added": "Currency" in rows and "Currency" not in (req_rows or []),
    }


def _order_keys(
    keys: list[tuple[Any, ...]],
    dimensions: list[str],
    records: list[dict[str, Any]],
    sort_specs: list[PivotSort] | None,
    axis: str,
    other_count: int,
) -> list[tuple[Any, ...]]:
    """Reorder row/column keys according to sort specs.

    A sort spec referencing a dimension resolves to the position of that
    dimension within this axis's tuple; a measure alias sorts by the aggregated
    value of that measure across the opposite axis. When no spec is given (or
    the axis has no keys), preserves insertion order (which already reflects
    the SQL ORDER BY and, by extension, the original dimension order).
    """
    if not sort_specs or not keys:
        return keys
    # Row axis occupies dimensions[0..len(rows)-1]; column axis the tail.
    if axis == "row":
        axis_dims = dimensions[: len(dimensions) - other_count]
    else:
        axis_dims = dimensions[len(dimensions) - other_count:]

    def key_for_record(rec: dict, a: str) -> tuple[Any, ...]:
        if a == "row":
            return tuple(rec.get(f"d{i}") for i in range(len(axis_dims)))
        offset = len(dimensions) - other_count
        return tuple(rec.get(f"d{offset + i}") for i in range(other_count))

    def sort_value(key: tuple[Any, ...]) -> tuple:
        values: list[Any] = []
        for spec in sort_specs:
            column = spec.column
            if column.startswith("m") and column[1:].isdigit():
                mi = int(column[1:])
                total = 0.0
                for rec in records:
                    if key_for_record(rec, axis) == key:
                        total += float(rec.get(f"m{mi}") or 0)
                values.append(total)
            else:
                idx = axis_dims.index(column) if column in axis_dims else None
                values.append(key[idx] if idx is not None else None)
        return tuple(values)

    def cmp(a: tuple[Any, ...], b: tuple[Any, ...]) -> int:
        va = sort_value(a)
        vb = sort_value(b)
        for i, spec in enumerate(sort_specs):
            av, bv = va[i], vb[i]
            av_num = isinstance(av, (int, float)) and not isinstance(av, bool)
            bv_num = isinstance(bv, (int, float)) and not isinstance(bv, bool)
            if av_num and bv_num:
                delta = (av > bv) - (av < bv)
            else:
                delta = (str(av) > str(bv)) - (str(av) < str(bv))
            if delta != 0:
                return delta if spec.direction == "asc" else -delta
        return 0

    try:
        from functools import cmp_to_key
        return sorted(keys, key=cmp_to_key(cmp))
    except Exception:
        return keys


@router.post("/pivot")
async def run_pivot(req: PivotRequest, db: AsyncSession = Depends(get_db)):
    """Aggregate a Gold destination table in Trino; no raw data is sent to the browser."""
    table_name, schema = await _pivot_table_context(db, req.destination_table_id)
    _canonicalize_request(req, schema)
    rows, columns, measures = _validate_pivot_request(req, schema)
    dimensions = rows + columns
    select_dims = [f"{_quote_ident(name)} AS d{index}" for index, name in enumerate(dimensions)]
    measure_parts, aliases = zip(*[_measure_sql(measure, index) for index, measure in enumerate(measures)])
    where = _where_clause(req.filters)
    group_by = f" GROUP BY {', '.join(_quote_ident(name) for name in dimensions)}" if dimensions else ""
    if req.sort_rows:
        order_by = _build_order_by(req.sort_rows, rows, columns, len(measures))
    else:
        order_by = f" ORDER BY {', '.join(_quote_ident(name) for name in dimensions)}" if dimensions else ""
    # A bounded aggregated result is deliberate: it protects Trino and the UI.
    limit = max(1, min(req.row_limit * max(1, req.column_limit), 30000))
    sql = f"SELECT {', '.join([*select_dims, *measure_parts])} FROM iceberg.gold.{table_name}{where}{group_by}{order_by} LIMIT {limit}"
    try:
        records = await trino_execute(sql, timeout_s=90)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Falha ao executar pivot no Trino: {exc}")
    return _build_pivot_result(records, rows, columns, measures, req.include_totals, req.rows, req.sort_rows, req.sort_columns)


@router.post("/pivot/union")
async def run_pivot_union(req: PivotUnionRequest, db: AsyncSession = Depends(get_db)):
    """Union two (or N) Gold destinations/pseudo-destinations into one pivot.

    Each table is resolved via ``_analysis_physical_name`` (negative ids are
    planning pseudo-destinations). The schemas differ (a Gold table has hundreds
    of columns, a planning table keeps only dimensions + period + tipo + the
    measure), so a plain ``UNION ALL`` would fail. Instead each side selects the
    same column set using ``CAST(NULL AS ...)`` for columns it lacks, and an
    origin column (``origem``) is injected to tell realized from forecast.
    """
    if not req.destination_table_ids:
        raise HTTPException(status_code=422, detail="Informe ao menos uma tabela para união")
    if len(req.destination_table_ids) < 2:
        raise HTTPException(status_code=422, detail="A união exige ao menos duas tabelas")

    # Resolve each side's physical name + schema (planning value column is
    # read dynamically via DESCRIBE, never hardcoded as `value`).
    sides: list[tuple[int, str, dict[str, str]]] = []
    for table_id in req.destination_table_ids:
        safe_name, schema = await _pivot_table_context(db, table_id)
        sides.append((table_id, safe_name, schema))

    # Validate against the union of every side's schema (not just the first),
    # plus the synthetic origin column. Realized Gold and planning tables expose
    # different columns (e.g. Gold has ``_billing_period`` while planning has
    # ``period``), so a dimension present on any one side must be accepted here;
    # the per-side projection below aligns the names via ``period_source`` or
    # contributes ``CAST(NULL AS ...)`` for sides that lack it.
    origin_name = req.origin_column
    merged_schema: dict[str, str] = {origin_name: "varchar"}
    for _table_id, _safe_name, schema in sides:
        for name, typ in schema.items():
            merged_schema.setdefault(name, typ)
    _canonicalize_request(req, merged_schema)  # type: ignore[arg-type]
    rows, columns, measures = _validate_pivot_request(req, merged_schema)  # type: ignore[arg-type]

    # Determine an origin label for each side. Planning pseudo-destinations
    # (negative id, or a name ending in `_planning`) map to "forecast".
    origin_values: list[str] = []
    for table_id, safe_name, _schema in sides:
        if table_id < 0 or safe_name.endswith("_planning"):
            origin_values.append("forecast")
        else:
            origin_values.append("realizado")

    # Column alignment: every side must project the exact same columns in the
    # same order with explicit `AS` aliases, so the outer aggregate can address
    # them uniformly. A side lacking a column contributes CAST(NULL AS ...).
    dimension_columns = [*rows, *columns]
    # The aligned subquery exposes columns under these names (dimensions by
    # their own name, measures via a stable alias m0..mk, plus `origem`).
    measure_aliases_inner = [f"u_m{i}" for i in range(len(measures))]

    def period_source(schema: dict[str, str], requested: str) -> str | None:
        """Resolve the physical period column a side exposes for a common axis.

        Realized Gold uses ``_billing_period`` (YYYYMM) while planning tables use
        ``period`` (YYYYMM); both carry the same monthly bucket. When a requested
        dimension is one of these names but this side only has the other, return
        the side's actual column so it can be aliased onto the shared name. This
        keeps realizado + forecast comparable month-over-month without renaming
        the underlying physical tables.
        """
        lowered = {name.lower(): name for name in schema}
        if requested.lower() in ("period", "_billing_period"):
            for candidate in ("_billing_period", "period", "billingperiodstart"):
                if candidate in lowered and lowered[candidate] != requested:
                    return lowered[candidate]
        return None

    def aligned_projection(schema: dict[str, str], origin: str) -> list[str]:
        """Return aligned SELECT expressions for one side of the UNION ALL.

        Every dimension and measure is given an explicit ``AS`` alias so the
        outer aggregate can address it uniformly, even when a side contributes
        CAST(NULL AS ...) for a column it lacks.
        """
        exprs: list[str] = []
        for name in dimension_columns:
            if name == origin_name:
                # Emitted explicitly as the final literal column below.
                continue
            if name in schema:
                exprs.append(f"{_quote_ident(name)} AS {_quote_ident(name)}")
            else:
                source = period_source(schema, name)
                if source is not None:
                    exprs.append(f"{_quote_ident(source)} AS {_quote_ident(name)}")
                else:
                    exprs.append(f"CAST(NULL AS VARCHAR) AS {_quote_ident(name)}")
        for i, measure in enumerate(measures):
            inner = measure_aliases_inner[i]
            if measure.column in schema:
                exprs.append(f"TRY_CAST({_quote_ident(measure.column)} AS DOUBLE) AS {inner}")
            else:
                exprs.append(f"CAST(NULL AS DOUBLE) AS {inner}")
        exprs.append(f"CAST({_quote_literal(origin)} AS VARCHAR) AS {origin_name}")
        return exprs

    # Filters are applied inside each side of the union against that side's own
    # schema. This keeps the outer aggregate over an already-filtered, aligned
    # column set and avoids referencing Gold-only columns that were not part of
    # the aligned axes. A side missing a filtered column simply skips that
    # filter (e.g. a planning table lacks most Gold dimensions).
    def side_filters(schema: dict[str, str]) -> list[PivotFilter]:
        return [f for f in req.filters if f.column in schema]  # type: ignore[union-attr]

    union_parts: list[str] = []
    for (_table_id, safe_name, schema), origin in zip(sides, origin_values):
        exprs = aligned_projection(schema, origin)
        filter_sql = _where_clause(side_filters(schema))
        union_parts.append(
            f"SELECT {', '.join(exprs)} FROM iceberg.gold.{safe_name}{filter_sql}"
        )

    # The outer query aliases the aligned columns back to d0..dn / m0..mk and
    # aggregates with the same measure SQL used by the single-table pivot.
    dim_aliases = [f"{_quote_ident(name)} AS d{i}" for i, name in enumerate(dimension_columns)]
    measure_aliases = []
    for i, measure in enumerate(measures):
        inner = _quote_ident(measure_aliases_inner[i])
        expression = {
            "SUM": f"SUM(COALESCE({inner}, 0))",
            "COUNT": "COUNT(*)",
            "COUNT_DISTINCT": f"COUNT(DISTINCT {inner})",
            "AVG": f"AVG({inner})",
            "MIN": f"MIN({inner})",
            "MAX": f"MAX({inner})",
        }[measure.aggregation]
        measure_aliases.append(f"{expression} AS m{i}")

    dimensions = rows + columns
    group_by = f" GROUP BY {', '.join(_quote_ident(name) for name in dimensions)}" if dimensions else ""
    if req.sort_rows:
        order_by = _build_order_by(req.sort_rows, rows, columns, len(measures))
    else:
        order_by = f" ORDER BY {', '.join(_quote_ident(name) for name in dimensions)}" if dimensions else ""
    limit = max(1, min(req.row_limit * max(1, req.column_limit), 30000))
    union_sql = " UNION ALL ".join(f"({part})" for part in union_parts)
    sql = (
        f"SELECT {', '.join([*dim_aliases, *measure_aliases])} "
        f"FROM ({union_sql}) AS u_src{group_by}{order_by} LIMIT {limit}"
    )

    try:
        records = await trino_execute(sql, timeout_s=120)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Falha ao executar união no Trino: {exc}")

    result = _build_pivot_result(records, rows, columns, measures, req.include_totals, req.rows, req.sort_rows, req.sort_columns)
    result["origin_column"] = origin_name
    result["origin_values"] = origin_values
    result["tables"] = [{"id": table_id, "physical_name": safe_name, "origin": origin}
                        for (table_id, safe_name, _schema), origin in zip(sides, origin_values)]
    return result


@router.post("/pivot/records")
async def pivot_records(req: PivotRecordRequest, db: AsyncSession = Depends(get_db)):
    """Return raw (non-aggregated) Gold records for the browser to feed Perspective."""
    table_name, schema = await _pivot_table_context(db, req.destination_table_id)
    by_lower = {name.lower(): name for name in schema}
    canonical_columns = [by_lower.get(name.lower(), name) for name in req.columns]
    invalid = sorted({name for name in canonical_columns if name not in schema})
    if invalid:
        raise HTTPException(status_code=422, detail=f"Colunas inválidas para esta tabela: {', '.join(invalid)}")
    filter_items = [item.model_copy(update={"column": by_lower.get(item.column.lower(), item.column)}) for item in req.filters]
    invalid_filters = sorted({item.column for item in filter_items if item.column not in schema})
    if invalid_filters:
        raise HTTPException(status_code=422, detail=f"Colunas de filtro inválidas: {', '.join(invalid_filters)}")
    where = _where_clause(filter_items)
    limit = max(1, min(req.limit, 50000))
    offset = max(0, req.offset)
    if canonical_columns:
        select_expr = ", ".join(_quote_ident(name) for name in canonical_columns)
    else:
        select_expr = "*"
    # Without ORDER BY, Iceberg/Trino returns rows in an undefined file order,
    # which can make a LIMIT sample consist of a single billing period. Sort by
    # the best available temporal column so the browser sees a representative
    # slice across periods.
    order_candidates = [name for name in ("_billing_period", "period", "ChargePeriodStart", "BillingPeriodStart") if name in schema]
    order_by = f" ORDER BY {_quote_ident(order_candidates[0])} DESC" if order_candidates else ""
    sql = f"SELECT {select_expr} FROM iceberg.gold.{table_name}{where}{order_by} OFFSET {offset} LIMIT {limit}"
    try:
        rows = await trino_execute(sql, timeout_s=90)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Falha ao carregar registros no Trino: {exc}")
    return {
        "columns": list(rows[0].keys()) if rows else (canonical_columns if canonical_columns else list(schema)),
        "rows": rows,
        "total": len(rows),
        "limit": limit,
        "offset": offset,
    }


@router.post("/pivot/drill-down")
async def pivot_drill_down(req: PivotDrillDownRequest, db: AsyncSession = Depends(get_db)):
    table_name, schema = await _pivot_table_context(db, req.destination_table_id)
    _canonicalize_request(req, schema)
    req.cell_filters = {next((name for name in schema if name.lower() == column.lower()), column): value for column, value in req.cell_filters.items()}
    _validate_pivot_request(req, schema)
    invalid = [column for column in req.cell_filters if column not in schema]
    if invalid:
        raise HTTPException(status_code=422, detail=f"Filtros da célula inválidos: {', '.join(invalid)}")
    where = _where_clause(req.filters, req.cell_filters)
    limit = max(1, min(req.limit, 500))
    sql = f"SELECT * FROM iceberg.gold.{table_name}{where} OFFSET {max(0, req.offset)} LIMIT {limit}"
    count_sql = f"SELECT COUNT(*) AS total FROM iceberg.gold.{table_name}{where}"
    try:
        rows = await trino_execute(sql, timeout_s=90)
        counted = await trino_execute(count_sql, timeout_s=90)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Falha no drill-down do Trino: {exc}")
    return {"columns": list(rows[0].keys()) if rows else list(schema.keys()), "rows": rows, "total": int(counted[0]["total"]) if counted else 0, "limit": limit, "offset": req.offset}


@router.get("/pivot/views")
async def list_pivot_views(destination_table_id: int | None = Query(None), db: AsyncSession = Depends(get_db)):
    statement = select(PivotView).order_by(PivotView.updated_at.desc())
    if destination_table_id:
        statement = statement.where(PivotView.destination_table_id == destination_table_id)
    views = (await db.execute(statement)).scalars().all()
    return [{"id": v.id, "name": v.name, "destination_table_id": v.destination_table_id, "config": v.config_json, "created_at": v.created_at, "updated_at": v.updated_at} for v in views]


@router.post("/pivot/views", status_code=201)
async def save_pivot_view(body: PivotViewCreate, db: AsyncSession = Depends(get_db)):
    existing = (await db.execute(select(PivotView).where(PivotView.destination_table_id == body.destination_table_id, PivotView.name == body.name))).scalar_one_or_none()
    config = body.config
    if existing:
        existing.config_json = config
        await db.commit(); await db.refresh(existing)
        return {"id": existing.id, "name": existing.name, "destination_table_id": existing.destination_table_id, "config": existing.config_json}
    view = PivotView(name=body.name, destination_table_id=body.destination_table_id, config_json=config)
    db.add(view); await db.commit(); await db.refresh(view)
    return {"id": view.id, "name": view.name, "destination_table_id": view.destination_table_id, "config": view.config_json}


@router.delete("/pivot/views/{view_id}", status_code=204)
async def delete_pivot_view(view_id: int, db: AsyncSession = Depends(get_db)):
    view = await db.get(PivotView, view_id)
    if not view:
        raise HTTPException(status_code=404, detail="Visão pivot não encontrada")
    await db.delete(view); await db.commit()


# ---------------------------------------------------------------------------
# Saved visual dashboards
# ---------------------------------------------------------------------------
def _dashboard_payload(item: Dashboard) -> dict[str, Any]:
    return {
        "id": item.id, "name": item.name, "pivot_view_id": item.pivot_view_id,
        "global_filters": item.global_filters or {}, "layout": item.layout_json or {},
        "created_at": item.created_at, "updated_at": item.updated_at,
    }


def _widget_payload(item: DashboardWidget) -> dict[str, Any]:
    return {
        "id": item.id, "dashboard_id": item.dashboard_id, "pivot_view_id": item.pivot_view_id,
        "title": item.title, "chart_type": item.chart_type,
        "config": item.config_json or {}, "position": item.position_json or {},
        "created_at": item.created_at, "updated_at": item.updated_at,
    }


@router.get("/dashboards")
async def list_dashboards(db: AsyncSession = Depends(get_db)):
    dashboards = (await db.execute(select(Dashboard).order_by(Dashboard.updated_at.desc()))).scalars().all()
    return [{**_dashboard_payload(item), "widgets": [_widget_payload(widget) for widget in (
        await db.execute(select(DashboardWidget).where(DashboardWidget.dashboard_id == item.id).order_by(DashboardWidget.id))
    ).scalars().all()]} for item in dashboards]


@router.post("/dashboards", status_code=201)
async def create_dashboard(body: DashboardCreate, db: AsyncSession = Depends(get_db)):
    item = Dashboard(name=body.name.strip(), pivot_view_id=body.pivot_view_id,
                     global_filters=body.global_filters, layout_json=body.layout)
    db.add(item)
    try:
        await db.commit(); await db.refresh(item)
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Dashboard já existe ou é inválido: {exc}")
    return _dashboard_payload(item)


@router.put("/dashboards/{dashboard_id}")
async def update_dashboard(dashboard_id: int, body: DashboardCreate, db: AsyncSession = Depends(get_db)):
    item = await db.get(Dashboard, dashboard_id)
    if not item:
        raise HTTPException(status_code=404, detail="Dashboard não encontrado")
    item.name = body.name.strip(); item.pivot_view_id = body.pivot_view_id
    item.global_filters = body.global_filters; item.layout_json = body.layout
    await db.commit(); await db.refresh(item)
    return _dashboard_payload(item)


@router.delete("/dashboards/{dashboard_id}", status_code=204)
async def delete_dashboard(dashboard_id: int, db: AsyncSession = Depends(get_db)):
    item = await db.get(Dashboard, dashboard_id)
    if not item:
        raise HTTPException(status_code=404, detail="Dashboard não encontrado")
    await db.delete(item); await db.commit()


@router.post("/dashboards/{dashboard_id}/widgets", status_code=201)
async def create_dashboard_widget(dashboard_id: int, body: DashboardWidgetCreate, db: AsyncSession = Depends(get_db)):
    if dashboard_id != body.dashboard_id or not await db.get(Dashboard, dashboard_id):
        raise HTTPException(status_code=404, detail="Dashboard não encontrado")
    item = DashboardWidget(dashboard_id=dashboard_id, pivot_view_id=body.pivot_view_id,
                           title=body.title.strip(), chart_type=body.chart_type,
                           config_json=body.config, position_json=body.position)
    db.add(item); await db.commit(); await db.refresh(item)
    return _widget_payload(item)


@router.put("/dashboards/{dashboard_id}/widgets/{widget_id}")
async def update_dashboard_widget(dashboard_id: int, widget_id: int, body: DashboardWidgetCreate, db: AsyncSession = Depends(get_db)):
    item = await db.get(DashboardWidget, widget_id)
    if not item or item.dashboard_id != dashboard_id:
        raise HTTPException(status_code=404, detail="Widget não encontrado")
    item.pivot_view_id = body.pivot_view_id; item.title = body.title.strip()
    item.chart_type = body.chart_type; item.config_json = body.config; item.position_json = body.position
    await db.commit(); await db.refresh(item)
    return _widget_payload(item)


@router.delete("/dashboards/{dashboard_id}/widgets/{widget_id}", status_code=204)
async def delete_dashboard_widget(dashboard_id: int, widget_id: int, db: AsyncSession = Depends(get_db)):
    item = await db.get(DashboardWidget, widget_id)
    if not item or item.dashboard_id != dashboard_id:
        raise HTTPException(status_code=404, detail="Widget não encontrado")
    await db.delete(item); await db.commit()


@router.post("/dashboard-widgets/query")
async def query_dashboard_widget(body: WidgetQueryRequest, db: AsyncSession = Depends(get_db)):
    view = await db.get(PivotView, body.pivot_view_id)
    if not view:
        raise HTTPException(status_code=404, detail="Visão pivot não encontrada")
    config = dict(view.config_json or {})
    config.update({k: v for k, v in body.config.items() if k in {"rows", "columns", "measures", "filters", "row_limit", "column_limit"}})
    request = PivotRequest.model_validate({"destination_table_id": view.destination_table_id, **config})
    for column, value in body.global_filters.items():
        values = value if isinstance(value, list) else [value]
        request.filters.append(PivotFilter(column=column, values=values, operator="IN"))
    source_ids = body.source_ids or config.get("source_ids") or [view.destination_table_id]
    if len(source_ids) > 1:
        rows = list(request.rows)
        if "origem" not in rows and "origem" not in request.columns:
            rows.append("origem")
        result = await run_pivot_union(PivotUnionRequest(
            destination_table_ids=source_ids, rows=rows, columns=request.columns,
            measures=request.measures, filters=request.filters, row_limit=request.row_limit,
            column_limit=request.column_limit, include_totals=True), db)
    else:
        result = await run_pivot(request, db)

    period_names = {"_billing_period", "period", "forecast_month"}
    all_dimensions = result["rows"] + result["columns"]
    x_index = next((i for i, name in enumerate(all_dimensions) if name.lower() in period_names), 0)
    origin_index = next((i for i, name in enumerate(all_dimensions) if name.lower() == "origem"), None)
    measure_index = int(body.config.get("measure_index", 0))
    measure_index = max(0, min(measure_index, len(result["measures"]) - 1))
    points: list[dict[str, Any]] = []
    for cell in result["cells"]:
        dimensions = list(cell["row"]) + list(cell["column"])
        points.append({"x": dimensions[x_index] if dimensions else "Total", "y": cell["values"][measure_index],
                       "series": dimensions[origin_index] if origin_index is not None else None})
    return {"chart_type": body.chart_type, "measure": result["measures"][measure_index],
            "x_dimension": all_dimensions[x_index] if all_dimensions else None,
            "series": points, "rows": result["rows"], "columns": result["columns"],
            "applied_filters": body.global_filters}


@router.post("/expected-view")
async def expected_view(body: ExpectedViewRequest, db: AsyncSession = Depends(get_db)):
    """First fixed, auditable view: actual consumption followed by forecast."""
    actual_name, actual_schema = await _pivot_table_context(db, 1)
    forecast_name, forecast_schema = await _pivot_table_context(db, -1)
    actual_columns = {name.lower(): name for name in actual_schema}
    forecast_columns = {name.lower(): name for name in forecast_schema}
    required_actual = {"_billing_period", "effectivecost", "departamento_nivel_1"}
    required_forecast = {"period", "realizado", "tipo", "departamento_nivel_1"}
    if not required_actual.issubset(actual_columns) or not required_forecast.issubset(forecast_columns):
        raise HTTPException(status_code=422, detail="As tabelas não possuem as colunas necessárias para a Visão Esperada")
    actual_period = _quote_ident(actual_columns["_billing_period"])
    actual_value = _quote_ident(actual_columns["effectivecost"])
    actual_director = _quote_ident(actual_columns["departamento_nivel_1"])
    forecast_period = _quote_ident(forecast_columns["period"])
    forecast_value = _quote_ident(forecast_columns["realizado"])
    forecast_type = _quote_ident(forecast_columns["tipo"])
    forecast_director = _quote_ident(forecast_columns["departamento_nivel_1"])
    director = _quote_literal(body.director)
    sql = f"""
    WITH actual AS (
      SELECT CAST({actual_period} AS VARCHAR) AS period,
             SUM(COALESCE(TRY_CAST({actual_value} AS DOUBLE), 0)) AS actual_value
      FROM iceberg.gold.{actual_name}
      WHERE {actual_director} = {director}
      GROUP BY 1
    ), forecast AS (
      SELECT CAST({forecast_period} AS VARCHAR) AS period,
             SUM(COALESCE(TRY_CAST({forecast_value} AS DOUBLE), 0)) AS forecast_value
      FROM iceberg.gold.{forecast_name}
      WHERE {forecast_director} = {director} AND lower(CAST({forecast_type} AS VARCHAR)) = 'forecast'
      GROUP BY 1
    ), periods AS (
      SELECT COALESCE(a.period, f.period) AS period, a.actual_value, f.forecast_value
      FROM actual a FULL OUTER JOIN forecast f ON a.period = f.period
    ), expected AS (
      SELECT period, actual_value, forecast_value,
             COALESCE(actual_value, forecast_value, 0) AS expected_value
      FROM periods
    )
    SELECT period, actual_value, forecast_value, expected_value,
           SUM(expected_value) OVER (ORDER BY period ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cumulative_value
    FROM expected
    ORDER BY period
    """
    try:
        rows = await trino_execute(sql, timeout_s=90)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Falha ao consultar a Visão Esperada: {exc}")
    return {"director": body.director, "rows": rows, "period_column": "period", "value_column": "cumulative_value"}


@router.get("/tables")
async def get_tables():
    """List all Iceberg tables across bronze, silver, gold layers."""
    result: dict[str, list[str]] = {}
    for layer in ("bronze", "silver", "gold"):
        try:
            tables = list_tables(layer)
            result[layer] = tables
        except Exception as e:
            result[layer] = [f"error: {e}"]
    return result


@router.post("/query", response_model=QueryResponse)
async def run_query(req: QueryRequest):
    """
    Execute a SQL query against Trino + Iceberg.

    Examples:
      SELECT * FROM iceberg.bronze.raw_azure_4p WHERE _billing_period = '202504' LIMIT 10
      SELECT SUM("BilledCost") AS total_billed FROM iceberg.gold.dest_modelo_focus WHERE "_billing_period" = '202504'
    """
    try:
        rows = await trino_execute(req.sql, catalog=req.catalog)
    except TrinoError as e:
        # Gold may still be in the local PyIceberg catalog while Trino is
        # connected to Nessie/MinIO. Support the standard destination
        # aggregations through the same local Iceberg table until the data
        # catalog migration is complete.
        rows = _run_local_gold_query(req.sql)
        if rows is None:
            raise HTTPException(status_code=502, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=f"Trino unavailable: {e}")
    except Exception as e:
        logger.exception("Trino query failed")
        raise HTTPException(status_code=500, detail=str(e))

    columns = list(rows[0].keys()) if rows else []
    return QueryResponse(columns=columns, rows=rows, row_count=len(rows))


def _run_local_gold_query(sql: str) -> list[dict[str, Any]] | None:
    """Fallback for the supported Gold analysis query when Trino has no table."""
    normalized = " ".join(sql.split())
    if not re.search(r"from\s+iceberg\.gold\.dest_modelo_focus\b", normalized, re.I):
        return None

    generic = re.search(r"select\s+\*.*?limit\s+(\d+)", normalized, re.I)
    if generic:
        table = get_catalog().load_table(("gold", "dest_modelo_focus"))
        frame = table.scan().to_pandas().head(int(generic.group(1)))
        return json.loads(frame.to_json(orient="records", date_format="iso"))

    match = re.search(
        r"select\s+\"?ServiceName\"?\s*,\s*sum\(\s*\"?BilledCost\"?\s*\)\s+as\s+total_billed\s*,\s*count\(\s*\*\s*\)\s+as\s+rows.*?group\s+by\s+\"?ServiceName\"?.*?limit\s+(\d+)",
        normalized,
        re.I,
    )
    if not match:
        # Generic FinOps aggregation used by the predefined queries.
        generic = re.search(
            r"select\s+(.+?)\s*,\s*sum\(\s*\"?BilledCost\"?\s*\)\s+as\s+total_billed.*?count\(\s*\*\s*\).*?from\s+iceberg\.gold\.dest_modelo_focus\s+group\s+by\s+(.+?)(?:\s+order\s+by|\s+limit|$)",
            normalized,
            re.I,
        )
        if not generic:
            return None
        fields = [f.strip().strip('"') for f in generic.group(2).split(",")]
        allowed = {"departamento", "projeto_nome", "_billing_period", "RegionName", "cloud_origem", "ServiceName"}
        if not fields or any(f not in allowed for f in fields):
            return None
        table = get_catalog().load_table(("gold", "dest_modelo_focus"))
        needed = fields + ["BilledCost"]
        if "EffectiveCost" in normalized:
            needed.append("EffectiveCost")
        frame = table.scan().select(*needed).to_pandas()
        frame["BilledCost"] = pd.to_numeric(frame["BilledCost"], errors="coerce").fillna(0)
        grouped = frame.groupby(fields, dropna=False).agg(
            total_billed=("BilledCost", "sum"), rows=("BilledCost", "size")
        )
        if "EffectiveCost" in frame:
            frame["EffectiveCost"] = pd.to_numeric(frame["EffectiveCost"], errors="coerce").fillna(0)
            extra = frame.groupby(fields, dropna=False)["EffectiveCost"].sum().rename("total_effective")
            grouped = grouped.join(extra)
        return json.loads(grouped.reset_index().to_json(orient="records"))

    table = get_catalog().load_table(("gold", "dest_modelo_focus"))
    frame = table.scan().select("ServiceName", "BilledCost").to_pandas()
    frame["BilledCost"] = pd.to_numeric(frame["BilledCost"], errors="coerce").fillna(0)
    result = (
        frame.groupby("ServiceName", dropna=False)
        .agg(total_billed=("BilledCost", "sum"), rows=("BilledCost", "size"))
        .sort_values("total_billed", ascending=False)
        .head(int(match.group(1)))
        .reset_index()
    )
    result["ServiceName"] = result["ServiceName"].where(result["ServiceName"].notna(), None)
    return result.to_dict(orient="records")


# ---------------------------------------------------------------------------
# Pre-built analysis queries
# ---------------------------------------------------------------------------
@router.get("/summary")
async def get_summary(
    connection_code: str | None = Query(None),
    billing_period: str | None = Query(None),
    layer: str = Query("gold", regex="^(bronze|silver|gold)$"),
):
    """
    Return a summary of available data for analysis.
    When layer='gold', queries the active destination table.
    When layer='silver' or 'bronze', returns table row counts.
    """
    if layer == "gold":
        where = ["1=1"]
        if connection_code:
            where.append(f'"source_connection_id" = \'{connection_code}\'')
        if billing_period:
            where.append(f'"_billing_period" = \'{billing_period}\'')
        sql = f"""
            SELECT
                "cloud_origem" AS connection_code,
                "source_connection_id",
                "_billing_period" AS billing_period,
                COUNT(*) AS total_rows,
                SUM(COALESCE("BilledCost", 0)) AS total_billed,
                SUM(COALESCE("EffectiveCost", 0)) AS total_effective,
                COUNT(DISTINCT diretoria) AS num_diretorias,
                COUNT(DISTINCT "ServiceName") AS num_services
            FROM iceberg.gold.dest_modelo_focus
            WHERE {' AND '.join(where)}
            GROUP BY "cloud_origem", "source_connection_id", "_billing_period"
            ORDER BY "_billing_period" DESC, "cloud_origem"
        """
    else:
        # For bronze/silver, return row counts per table
        tables = list_tables(layer)
        rows: list[dict] = []
        for tbl in tables:
            try:
                result = await trino_execute(
                    f"SELECT COUNT(*) AS cnt FROM iceberg.{layer}.{tbl}"
                )
                rows.append({"layer": layer, "table": tbl, "row_count": result[0]["cnt"] if result else 0})
            except Exception:
                rows.append({"layer": layer, "table": tbl, "row_count": -1})
        return {"summary": rows}

    try:
        result = await trino_execute(sql)
        return {"summary": result}
    except TrinoError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/health")
async def health_check():
    """Check if Trino and Nessie are reachable."""
    status = {"trino": "unknown", "nessie": "unknown", "minio": "unknown"}

    # Test Trino
    try:
        rows = await trino_execute("SELECT 1 AS ok")
        status["trino"] = "ok" if rows and rows[0].get("ok") == 1 else "error"
    except Exception as e:
        status["trino"] = str(e)[:120]

    # Test Nessie catalog
    try:
        cat = get_catalog()
        cat.list_namespaces()
        status["nessie"] = "ok"
    except Exception as e:
        status["nessie"] = str(e)[:120]

    # MinIO is still part of the lakehouse stack even when the current local
    # JDBC catalog uses the filesystem warehouse. Report its real state rather
    # than leaving the frontend with a misleading "unknown" warning.
    try:
        import httpx
        minio_url = os.getenv("MINIO_URL", "http://localhost:9000").rstrip("/")
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{minio_url}/minio/health/live")
        status["minio"] = "ok" if response.status_code == 200 else f"HTTP {response.status_code}"
    except Exception as e:
        status["minio"] = str(e)[:120]

    return status


# ═══════════════════════════════════════════════════════════════════════
# Source ingested data (normalized cost lines)
# ═══════════════════════════════════════════════════════════════════════

@router.get("/sources")
async def list_sources_data(db: AsyncSession = Depends(get_db)):
    """List all source connections with their ingested data row counts."""
    sources = []
    conns = (await db.execute(select(SourceConnection))).scalars().all()
    for conn in conns:
        cnt = (await db.execute(
            select(func.coalesce(func.sum(IngestedBlob.row_count), 0))
            .where(IngestedBlob.connection_id == conn.id)
        )).scalar() or 0
        sources.append({
            "id": conn.id,
            "name": conn.name,
            "code": conn.code,
            "connector_type": conn.connector_type,
            "cloud_name": conn.cloud_name,
            "row_count": cnt,
        })
    return sorted(sources, key=lambda s: s["row_count"], reverse=True)


@router.get("/sources/{connection_id}/data")
async def query_source_data(
    connection_id: int,
    limit: int = Query(100, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    billing_period: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Query normalized data for a specific source connection."""
    conn = await db.get(SourceConnection, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    conditions = [IngestedBlob.connection_id == connection_id]
    if billing_period:
        conditions.append(IngestedBlob.billing_period == billing_period)
    blobs = (await db.execute(
        select(IngestedBlob).where(and_(*conditions))
        .order_by(IngestedBlob.ingested_at.desc())
    )).scalars().all()
    total = sum((b.row_count or 0) for b in blobs)
    items = [{"blob_path": b.blob_path, "billing_period": b.billing_period,
              "row_count": b.row_count or 0, "status": b.status}
             for b in blobs[offset:offset + limit]]

    return {
        "connection_id": connection_id,
        "connection_name": conn.name,
        "connection_code": conn.code,
        "total": total,
        "limit": limit,
        "offset": offset,
        "rows": items,
        "periods": [{"period": b.billing_period, "count": b.row_count or 0} for b in blobs],
    }


# ═══════════════════════════════════════════════════════════════════════
# Destination table data (classified + business rules in Iceberg/Parquet)
# ═══════════════════════════════════════════════════════════════════════

@router.get("/destinations")
async def list_destinations_data(db: AsyncSession = Depends(get_db)):
    """List destination tables with row counts (Trino with PyIceberg fallback)."""
    tables = (await db.execute(
        text("SELECT id, name, physical_name, description FROM destination_table WHERE active = true ORDER BY id")
    )).all()

    result = []
    for t in tables:
        tid, tname, phys, desc = t[0], t[1], t[2], t[3]
        safe_name = phys.replace("-", "_").replace(".", "_")
        table_name = f"gold.{safe_name}"
        row_cnt = 0
        periods: list[dict] = []
        try:
            trino_result = await trino_execute(f"SELECT COUNT(*) AS cnt FROM iceberg.{table_name}")
            row_cnt = trino_result[0]["cnt"] if trino_result else 0
            pr = await trino_execute(
                f"SELECT _billing_period, COUNT(*) AS cnt FROM iceberg.{table_name} "
                f"GROUP BY _billing_period ORDER BY _billing_period DESC LIMIT 20"
            )
            periods = [{"period": str(p["_billing_period"]), "count": p["cnt"]} for p in (pr or [])]
        except Exception:
            # Trino down — fall back to PyIceberg (lightweight: scan only billing_period)
            try:
                from app.services.iceberg_service import get_catalog
                cat = get_catalog()
                tbl = cat.load_table(table_name)
                arrow_table = tbl.scan().select("_billing_period").to_arrow()
                row_cnt = len(arrow_table)
                # Count by billing period
                import pyarrow.compute as pc
                bp_col = arrow_table.column("_billing_period")
                counted = pc.value_counts(bp_col)
                for i in range(len(counted)):
                    periods.append({
                        "period": str(counted.field(0)[i].as_py()),
                        "count": counted.field(1)[i].as_py(),
                    })
            except Exception:
                row_cnt = 0

        result.append({
            "id": tid,
            "name": tname,
            "physical_name": phys,
            "description": desc,
            "row_count": row_cnt,
            "periods": periods,
        })

        # Forecast is deliberately stored separately from the realized Gold.
        # Expose it as an analysis-only pseudo-destination using a negative ID;
        # it remains configuration-free and never becomes a reprocess target.
        planning_name = f"{safe_name}_planning"
        try:
            planning_count = await trino_execute(
                f"SELECT COUNT(*) AS cnt FROM iceberg.gold.{planning_name}"
            )
            planning_rows = planning_count[0]["cnt"] if planning_count else 0
            planning_periods = await trino_execute(
                f"SELECT period, COUNT(*) AS cnt FROM iceberg.gold.{planning_name} "
                "GROUP BY period ORDER BY period DESC LIMIT 20"
            )
            planning_period_list = [
                {"period": str(p["period"]), "count": p["cnt"]}
                for p in (planning_periods or [])
            ]
            if planning_rows:
                result.append({
                    "id": -int(tid),
                    "name": f"{tname} — Forecast / Planning",
                    "physical_name": planning_name,
                    "description": "Projeções mensais: realizado e forecast",
                    "row_count": planning_rows,
                    "periods": planning_period_list,
                    "analysis_only": True,
                })
        except Exception:
            pass
    return result


@router.get("/destinations/{table_id}/data")
async def query_destination_data(
    table_id: int,
    limit: int = Query(100, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Query data from a destination table (Iceberg/Parquet via Trino)."""
    phys = await _analysis_physical_name(db, table_id)
    if not phys:
        raise HTTPException(status_code=404, detail="Destination table not found")

    table_name = f"gold.{phys.replace('-', '_').replace('.', '_')}"
    safe_name = phys.replace("-", "_").replace(".", "_")

    # Count
    total = 0
    try:
        cnt_result = await trino_execute(f"SELECT COUNT(*) AS cnt FROM iceberg.{table_name}")
        total = cnt_result[0]["cnt"] if cnt_result else 0
    except Exception:
        pass

    # Query data via Trino
    rows = []
    columns: list[str] = []
    try:
        trino_rows = await trino_execute(
            f"SELECT * FROM iceberg.{table_name} ORDER BY 1 DESC OFFSET {offset} LIMIT {limit}"
        )
        if trino_rows:
            columns = list(trino_rows[0].keys())
            # Convert non-serializable values
            for row in trino_rows:
                clean = {}
                for k, v in row.items():
                    if hasattr(v, "isoformat"):
                        clean[k] = v.isoformat()
                    elif isinstance(v, (dict, list)):
                        clean[k] = json.dumps(v, default=str)
                    else:
                        clean[k] = v
                rows.append(clean)
    except Exception:
        logger.info("Trino unavailable for %s — falling back to PyIceberg", safe_name)
        # Fallback: PyIceberg direct scan for the requested page
        try:
            from app.services.iceberg_service import get_catalog
            cat = get_catalog()
            tid_ice = f"gold.{safe_name}"
            tbl = cat.load_table(tid_ice)
            # Count from billing_period only (lightweight)
            total = len(tbl.scan().select("_billing_period").to_arrow())
            # Read requested page (full columns)
            if total > 0 and offset < total:
                sliced = tbl.scan().to_arrow().slice(offset, limit)
                columns = sliced.schema.names
                for i in range(len(sliced)):
                    clean = {}
                    for col in columns:
                        val = sliced.column(col)[i].as_py()
                        if hasattr(val, "isoformat"):
                            clean[col] = val.isoformat()
                        elif isinstance(val, (dict, list)):
                            clean[col] = json.dumps(val, default=str)
                        else:
                            clean[col] = val
                    rows.append(clean)
        except Exception as fallback_err:
            logger.warning("PyIceberg fallback failed for %s: %s", safe_name, fallback_err)

    return {
        "table_id": table_id,
        "physical_name": phys,
        "iceberg_table": table_name,
        "total": total,
        "limit": limit,
        "offset": offset,
        "columns": columns,
        "rows": rows,
    }
