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
from app.services.iceberg_service import list_tables, get_catalog
from app.services.trino_service import execute as trino_execute, TrinoError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis", tags=["analysis"])


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


class PivotRequest(BaseModel):
    destination_table_id: int
    rows: list[str] = []
    columns: list[str] = []
    measures: list[PivotMeasure] = [PivotMeasure(column="EffectiveCost", aggregation="SUM")]
    filters: list[PivotFilter] = []
    row_limit: int = 500
    column_limit: int = 60
    include_totals: bool = True


class PivotDrillDownRequest(PivotRequest):
    cell_filters: dict[str, str | int | float | bool | None] = {}
    limit: int = 100
    offset: int = 0


class PivotViewCreate(BaseModel):
    name: str
    destination_table_id: int
    config: PivotRequest


class LakehouseSearchRequest(BaseModel):
    destination_table_id: int
    term: str
    limit: int = 100
    offset: int = 0
    filters: list[PivotFilter] = []


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str | int | float | bool) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


async def _pivot_table_context(db: AsyncSession, destination_table_id: int) -> tuple[str, dict[str, str]]:
    row = (await db.execute(text(
        "SELECT physical_name FROM destination_table WHERE id = :id AND active = true"
    ), {"id": destination_table_id})).first()
    if not row:
        raise HTTPException(status_code=404, detail="Tabela destino ativa não encontrada")
    safe_name = str(row[0]).replace("-", "_").replace(".", "_")
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


@router.get("/pivot/metadata/{destination_table_id}")
async def pivot_metadata(destination_table_id: int, db: AsyncSession = Depends(get_db)):
    """Return the real Gold schema and sensible FinOps field suggestions."""
    _, schema = await _pivot_table_context(db, destination_table_id)
    numeric = [name for name, typ in schema.items() if any(t in typ.lower() for t in ("int", "double", "real", "decimal", "float", "bigint"))]
    dimensions = [name for name in schema if name not in numeric]
    recommended = [name for name in ["_billing_period", "departamento", "projeto_nome", "cloud_origem", "ServiceName", "RegionName", "SubscriptionName", "Currency"] if name in schema]
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
    order_by = f" ORDER BY {', '.join(_quote_ident(name) for name in dimensions)}" if dimensions else ""
    # A bounded aggregated result is deliberate: it protects Trino and the UI.
    limit = max(1, min(req.row_limit * max(1, req.column_limit), 30000))
    sql = f"SELECT {', '.join([*select_dims, *measure_parts])} FROM iceberg.gold.{table_name}{where}{group_by}{order_by} LIMIT {limit}"
    try:
        records = await trino_execute(sql, timeout_s=90)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Falha ao executar pivot no Trino: {exc}")

    cells: list[dict[str, Any]] = []
    row_keys: dict[tuple[Any, ...], None] = {}
    col_keys: dict[tuple[Any, ...], None] = {}
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
                totals["grand"][index] = float(totals["grand"][index] or 0) + numeric_value
    return {
        "rows": rows,
        "columns": columns,
        "measures": [{"column": m.column, "aggregation": m.aggregation, "label": m.label or f"{m.aggregation} {m.column}"} for m in measures],
        "row_keys": [list(key) for key in row_keys],
        "column_keys": [list(key) for key in col_keys],
        "cells": cells,
        "totals": totals if req.include_totals else None,
        "record_count": len(records),
        "currency_added": "Currency" in rows and "Currency" not in req.rows,
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
    config = body.config.model_dump()
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
    return result


@router.get("/destinations/{table_id}/data")
async def query_destination_data(
    table_id: int,
    limit: int = Query(100, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Query data from a destination table (Iceberg/Parquet via Trino)."""
    phys = (await db.execute(
        text("SELECT physical_name FROM destination_table WHERE id = :tid"),
        {"tid": table_id}
    )).scalar()
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
