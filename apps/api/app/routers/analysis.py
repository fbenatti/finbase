"""
Analysis Router — query the Data Lakehouse via Trino, PostgreSQL sources, and destination tables.

Endpoints:
  GET  /analysis/tables           → list available Iceberg tables (Bronze, Silver, Gold)
  POST /analysis/query            → run arbitrary SQL via Trino
  GET  /analysis/health           → health check for Trino and Iceberg catalog
  GET  /analysis/sources          → list connections with ingested data counts
  GET  /analysis/sources/{id}     → query ingested normalized data for a source
  GET  /analysis/destinations     → list destination tables with row counts
  GET  /analysis/destinations/{id}/data → query destination table data
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import os
import time
from datetime import datetime, timezone
import pandas as pd
from typing import Any, Literal
from fastapi import APIRouter, HTTPException, Query, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, func, text, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.source_connection import SourceConnection
from app.models.ingested_blob import IngestedBlob
from app.models.pivot_view import PivotView
from app.models.dashboard import Dashboard, DashboardWidget
from app.models.planning import FinancialType, PlanningEntry, PlanningVersion
from app.models.rules import Department
from app.services.iceberg_service import (
    list_tables,
    get_catalog,
    read_bronze,
    prune_stale_catalog_entries,
    remove_catalog_table,
)
from app.services.shared_cost import RATEIO_MEDIDA_ACTUAL, RATEIO_MEDIDA_FORECAST, RATEIO_TIPO, apply_rateio_facts
from app.services.trino_service import execute as trino_execute, TrinoCancelled, TrinoError, wait_until_ready
from app.services import lakehouse_layers as layers
from app.services.gold_materialization import GOLD

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis", tags=["analysis"])


class _ClientCancelScope:
    """Cancel Trino work when the browser disconnects or aborts the HTTP request."""

    def __init__(self, request: Request, *, timeout_s: float = 90, max_retries: int = 2):
        self.request = request
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.cancelled = asyncio.Event()
        self._watcher: asyncio.Task | None = None

    async def __aenter__(self) -> _ClientCancelScope:
        self._watcher = asyncio.create_task(_watch_disconnect(self.request, self.cancelled))
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.cancelled.set()
        if self._watcher:
            self._watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watcher

    @property
    def trino_kwargs(self) -> dict[str, Any]:
        return {"timeout_s": self.timeout_s, "max_retries": self.max_retries, "cancel_event": self.cancelled}


async def _watch_disconnect(request: Request, cancelled: asyncio.Event) -> None:
    while not cancelled.is_set():
        if await request.is_disconnected():
            cancelled.set()
            return
        await asyncio.sleep(0.05)


def _cancelled_http() -> HTTPException:
    return HTTPException(status_code=499, detail="Consulta cancelada")


async def _analysis_physical_name(db: AsyncSession, table_id: int) -> str | None:
    """Resolve regular destinations and forecast planning pseudo-destinations."""
    if is_shared_cost_pivot_id(table_id):
        base_id = shared_cost_base_destination_id(table_id)
        row = (await db.execute(text(
            "SELECT physical_name FROM destination_table WHERE id = :id AND active = true"
        ), {"id": base_id})).first()
        if row:
            return str(row[0]).replace("-", "_").replace(".", "_") + "_planning"
        return None
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


_STAGING_TABLE_RE = re.compile(
    r"^(?P<base>[a-z0-9_]+)_stg_(?P<timestamp>\d{16,20})$"
)


def _staging_table_parts(table_name: str) -> tuple[str, int] | None:
    """Return the generated staging base name and time_ns suffix."""
    match = _STAGING_TABLE_RE.fullmatch(table_name)
    if not match:
        return None
    return match.group("base"), int(match.group("timestamp"))


def _partition_kind(period_raw: str | None) -> str:
    if period_raw and re.fullmatch(r"\d{8}", period_raw):
        return "dia UTC de métrica"
    if period_raw and re.fullmatch(r"\d{6}", period_raw):
        return "mês de custo/licença"
    return "partição"


def _describe_iceberg_partition(partition: object) -> dict:
    """Split Iceberg partition metadata into period, connection and extras."""
    period_raw: str | None = None
    conn_raw: str | None = None
    subaccount: str | None = None
    subaccount_id: str | None = None
    values: list[str] = []

    if isinstance(partition, dict):
        raw_period = partition.get("_billing_period") or partition.get("snapshot_date") or partition.get("period")
        if raw_period is not None:
            period_raw = str(raw_period)
        if partition.get("source_connection_id") is not None:
            conn_raw = str(partition.get("source_connection_id"))
        for key in ("SubAccountId", "subaccountid"):
            if partition.get(key) not in (None, ""):
                subaccount_id = str(partition.get(key))
                break
        for key in ("SubAccountName", "subaccountname"):
            if partition.get(key) not in (None, ""):
                subaccount = str(partition.get(key))
                break
    elif isinstance(partition, (list, tuple)):
        values = [str(item) for item in partition if item is not None]
    elif partition is not None:
        text_value = str(partition).strip()
        if "/" in text_value:
            values = [part.strip() for part in text_value.split("/") if part.strip()]
        else:
            period_raw = text_value

    if values:
        period_raw = period_raw or values[0]
        if len(values) > 1 and values[1].isdigit():
            conn_raw = conn_raw or values[1]
            if len(values) > 2:
                subaccount_id = subaccount_id or values[2]
        elif len(values) > 1:
            subaccount = subaccount or values[1]

    conn_id: int | None = None
    if conn_raw and conn_raw.isdigit():
        conn_id = int(conn_raw)

    return {
        "period": period_raw,
        "period_kind": _partition_kind(period_raw),
        "source_connection_id": conn_id,
        "subaccount": subaccount,
        "subaccount_id": subaccount_id,
    }


async def _connection_labels(db: AsyncSession) -> dict[int, str]:
    rows = (await db.execute(text("SELECT id, code, name FROM source_connection"))).all()
    labels: dict[int, str] = {}
    for row in rows:
        code = (row.code or "").strip()
        name = (row.name or "").strip()
        labels[int(row.id)] = code or name or str(row.id)
    return labels


def _partition_meta_cache_key(layer: str, table_name: str) -> str:
    return f"{layer}:{table_name}"


def _partitions_from_inspect_frame(frame: pd.DataFrame, labels: dict[int, str]) -> list[dict]:
    partitions: list[dict] = []
    for row in frame.to_dict(orient="records"):
        partitions.append(_partition_payload(
            row.get("partition"),
            record_count=int(row.get("record_count") or 0),
            file_count=int(row.get("file_count") or 0),
            labels=labels,
        ))
    partitions.sort(key=lambda item: (str(item.get("period") or ""), item.get("source_connection_id") or 0))
    return partitions


def _partition_payload(partition: object, *, record_count: int, file_count: int, labels: dict[int, str]) -> dict:
    described = _describe_iceberg_partition(partition)
    conn_id = described["source_connection_id"]
    return {
        "period": described["period"],
        "period_kind": described["period_kind"],
        "source_connection_id": conn_id,
        "source_label": labels.get(conn_id) if conn_id is not None else None,
        "subaccount": described["subaccount"],
        "subaccount_id": described["subaccount_id"],
        "record_count": record_count,
        "file_count": file_count,
    }


async def _staging_base_has_active_reprocess(db: AsyncSession, base_name: str) -> bool:
    row = (await db.execute(text("""
        SELECT 1
          FROM etl_task t
          JOIN destination_table d ON d.id = t.destination_table_id
         WHERE t.task_type = 'reprocess'
           AND t.status IN ('QUEUED', 'RUNNING')
           AND replace(replace(d.physical_name, '-', '_'), '.', '_') = :base_name
         LIMIT 1
    """), {"base_name": base_name})).first()
    return row is not None


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
    operator: Literal["IN", "NOT_IN", "CONTAINS", "NOT_CONTAINS", "IS_NULL", "IS_NOT_NULL"] = "IN"


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
    An ``origem`` column (realizado/forecast) is available as a synthetic
    dimension so the user can break the result by source, but it is only
    grouped when requested in ``rows`` or ``columns``.
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


class PivotSourceDetailRequest(BaseModel):
    destination_table_id: int
    source_record_id: str | None = None
    source_blob_path: str | None = None
    source_row_offset: int | None = None


@router.post("/pivot/source-detail")
async def pivot_source_detail(req: PivotSourceDetailRequest, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Resolve a Silver row to its immutable Bronze source using provenance ID."""
    from app.models.destination import SourceDestinationMapping

    parts = (req.source_record_id or "").split(":")
    has_record_pointer = len(parts) == 3 and parts[0].isdigit() and re.fullmatch(r"-?\d+", parts[1]) and parts[2].isdigit()
    if not has_record_pointer:
        return {"available": False, "reason": "Esta linha ainda não tem source_record_id. Reprocesse a origem para gerar a linhagem."}
    connection_id, blob_id, row_offset = map(int, parts)
    connection = await db.get(SourceConnection, connection_id)
    if not connection:
        return {"available": False, "reason": "A conexão de origem desta linha não existe mais."}
    blob = await db.get(IngestedBlob, blob_id) if blob_id > 0 else None
    blob_path = blob.blob_path if blob else req.source_blob_path
    if blob_id > 0 and (not blob or blob.connection_id != connection_id):
        return {"available": False, "reason": "O arquivo de origem deste registro não está mais disponível."}
    if not blob_path:
        return {"available": False, "reason": "O arquivo de origem desta linha não está identificado; reprocese a origem para atualizar a linhagem."}
    if req.source_row_offset is not None and req.source_row_offset != row_offset:
        return {"available": False, "reason": "A posição da linha de origem não corresponde ao identificador de linhagem."}
    mappings = (await db.execute(select(SourceDestinationMapping).where(
        SourceDestinationMapping.source_connection_id == connection_id,
        SourceDestinationMapping.destination_table_id == req.destination_table_id,
        SourceDestinationMapping.active.is_(True),
    ).order_by(SourceDestinationMapping.priority, SourceDestinationMapping.id))).scalars().all()
    bronze_code = connection.code
    source_key = (connection.config_json or {}).get("source_key")
    if source_key == "resource_graph":
        from app.services.azure_source_registry import source_code as azure_source_code
        bronze_code = azure_source_code(connection.code, source_key)

    def read_origin_row() -> dict[str, Any] | None:
        from app.services import iceberg_service as iceberg
        batches = iceberg.read_bronze_batches(
            bronze_code, blob_path=blob_path,
            billing_period=blob.billing_period if blob else None,
            max_rows=row_offset + 1, batch_size=min(25_000, row_offset + 1),
        )
        remaining = row_offset
        for batch in batches:
            if remaining < len(batch):
                return json.loads(batch.iloc[remaining].to_json(date_format="iso"))
            remaining -= len(batch)
        return None

    origin_row = await asyncio.to_thread(read_origin_row)
    if origin_row is None:
        return {"available": False, "reason": "O source_record_id não encontrou a linha correspondente na Bronze."}
    active_pairs = []
    matched_source_fields: set[str] = set()
    for mapping in mappings:
        source_key = next((name for name in origin_row if name.lower() == mapping.source_column.lower()), None)
        if source_key:
            matched_source_fields.add(source_key)
            active_pairs.append({
                "source_key": source_key,
                "source_value": origin_row[source_key],
                "destination_key": mapping.destination_column,
            })
    excluded = {"_billing_period", "_blob_path", "_ingested_at", "_source_file", "source_record_id"}
    unmatched = {name: value for name, value in origin_row.items() if name not in matched_source_fields and name not in excluded and not name.startswith("_")}
    return {
        "available": True,
        "connection_name": connection.name,
        "blob_path": blob_path,
        "pairs": active_pairs,
        "source_unmatched": unmatched,
    }


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
    department_id: int | None = None
    director_view: bool = False
    year: int = 2026
    include_shared_cost: bool = False
    measure_column: str = "realizado"
    # Nome físico da Gold pré-agregada que alimenta a Visão Esperada.
    # Default: a regra visao-completa (dest_dm_cost_visao_completa).
    gold_table: str = "dest_dm_cost_visao_completa"
    # Escopo aditivo usado pelo dashboard executivo v2; os campos antigos do v1 permanecem iguais.
    month_from: str | None = None
    month_to: str | None = None
    department_ids: list[int] = Field(default_factory=list)
    dimension_filters: dict[str, list[str]] = Field(default_factory=dict)


class ExpectedViewPeriodDetailRequest(BaseModel):
    """Dinâmica de um mês: departamentos de todos os níveis dentro do escopo."""
    director: str = "DIR DADOS E INTELIGENCIA ARTIFICIAL"
    department_id: int | None = None
    period: str  # YYYYMM
    tipo: str = "realizado"  # realizado | forecast
    gold_table: str = "dest_dm_cost_visao_completa"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _pivot_dimension_sql(name: str, schema: dict[str, str]) -> str:
    """Use case-insensitive grouping for textual pivot dimensions."""
    column = _quote_ident(name)
    column_type = schema.get(name, "").lower()
    if any(token in column_type for token in ("varchar", "char", "string")):
        return f"lower(CAST({column} AS VARCHAR))"
    return column


def _quote_literal(value: str | int | float | bool) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


_SCHEMA_CACHE: dict[str, tuple[float, dict[str, str]]] = {}
_SCHEMA_CACHE_TTL_S = 90.0
_SCHEMA_LOCK = asyncio.Lock()
_SCHEMA_INFLIGHT: dict[str, asyncio.Task[tuple[str, dict[str, str]]]] = {}

# Partition trees in Analysis only need Iceberg metadata (manifest stats), not
# Postgres operational tables. Cache responses to avoid hammering Trino on
# every expand of large tables such as dest_modelo_focus.
_PARTITION_META_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_PARTITION_META_CACHE_TTL_S = float(os.getenv("ANALYSIS_PARTITION_CACHE_TTL_S", "120"))


def _schema_cache_key(namespace: str, safe_name: str) -> str:
    return f"{namespace}.{safe_name}"


def _local_iceberg_schema_sync(namespace: str, safe_name: str) -> dict[str, str]:
    from app.services import iceberg_service as iceberg

    table = iceberg._get_catalog().load_table(iceberg._table_id(namespace, safe_name))
    return {field.name: str(field.field_type) for field in table.schema().fields}


async def _local_iceberg_schema(namespace: str, safe_name: str) -> dict[str, str]:
    schema = await asyncio.to_thread(_local_iceberg_schema_sync, namespace, safe_name)
    if not schema:
        raise HTTPException(status_code=422, detail="A tabela não possui schema disponível no Iceberg local")
    return schema


def _local_latest_period_sync(namespace: str, table_name: str, period_col: str) -> str | None:
    from app.services import iceberg_service as iceberg

    table = iceberg._get_catalog().load_table(iceberg._table_id(namespace, table_name))
    try:
        frame = table.inspect.partitions().to_pandas()
        candidates = [column for column in frame.columns if column.lower() == period_col.lower()]
        if candidates:
            series = frame[candidates[0]].dropna().astype(str)
            if not series.empty:
                return series.max()
    except Exception:
        logger.debug("Iceberg inspect.partitions indisponível para %s.%s", namespace, table_name, exc_info=True)
    return None


async def _describe_layer_schema(safe_name: str, max_retries: int | None, namespace: str | None = None) -> dict[str, str]:
    # O detalhe vive na camada de detalhe (Silver). A resolução é central para
    # não repetir o bug de ler Gold enquanto o reprocess grava Silver.
    namespace = namespace or layers.detail_layer()
    cache_key = _schema_cache_key(namespace, safe_name)
    try:
        schema = await trino_execute(
            f"DESCRIBE iceberg.{namespace}.{safe_name}",
            timeout_s=20,
            max_retries=max_retries,
        )
        columns = {
            str(c.get("Column") or c.get("column_name") or c.get("column")): str(c.get("Type") or c.get("data_type") or "")
            for c in schema
        }
        if not columns:
            raise HTTPException(status_code=422, detail="A tabela não possui schema disponível no Trino")
        _SCHEMA_CACHE[cache_key] = (time.monotonic(), columns)
        return columns
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("DESCRIBE Trino falhou para %s (%s); usando schema Iceberg local", cache_key, exc)
        columns = await _local_iceberg_schema(namespace, safe_name)
        _SCHEMA_CACHE[cache_key] = (time.monotonic(), columns)
        return columns


async def _layer_from(
    db: AsyncSession,
    destination_table_id: int,
    table_name: str,
    schema: dict[str, str],
) -> str:
    """Alvo de leitura de detalhe. O overlay D-n está desabilitado; o forecast
    sobrescreve os dias sobrepostos. A camada vem do resolvedor central."""
    return f"iceberg.{layers.detail_layer()}.{table_name}"


# Forecast manual lives in the operational PostgreSQL database, not in
# Trino. It is exposed to the pivot through a synthetic pseudo-destination
# resolved by ``_pivot_table_context`` and served by ``_run_forecast_manual_pivot``.
FORECAST_MANUAL_PIVOT_ID = -1000000
SHARED_COST_PIVOT_ID_OFFSET = 2_000_000


def shared_cost_pivot_id(destination_id: int) -> int:
    return -(SHARED_COST_PIVOT_ID_OFFSET + abs(int(destination_id)))


def is_shared_cost_pivot_id(destination_id: int) -> bool:
    return int(destination_id) <= -(SHARED_COST_PIVOT_ID_OFFSET + 1)


def shared_cost_base_destination_id(destination_id: int) -> int:
    return -int(destination_id) - SHARED_COST_PIVOT_ID_OFFSET


_FORECAST_MANUAL_PIVOT_NAME = "forecast_manual_planning"
_FORECAST_MANUAL_SCHEMA = {
    "period": "varchar",
    "tipo": "varchar",
    "organograma": "varchar",
    "organograma_nivel_1": "varchar",
    "organograma_nivel_2": "varchar",
    "organograma_nivel_3": "varchar",
    "organograma_nivel_4": "varchar",
    "organograma_nivel_5": "varchar",
    "organograma_nivel_6": "varchar",
    "organograma_nivel_7": "varchar",
    "value": "double",
}


def _shared_cost_pivot_filters(
    destination_id: int,
    schema: dict[str, str],
    filters: list[PivotFilter],
) -> list[PivotFilter]:
    if not is_shared_cost_pivot_id(destination_id):
        return filters
    names = {name.lower(): name for name in schema}
    rateio_type = names.get("tipo")
    if not rateio_type:
        raise HTTPException(status_code=422, detail="A tabela não possui a coluna tipo para filtrar custo compartilhado")
    return [*filters, PivotFilter(column=rateio_type, values=["shared_cost"], operator="IN")]


async def _pivot_table_context(db: AsyncSession, destination_table_id: int, max_retries: int | None = None) -> tuple[str, dict[str, str]]:
    if destination_table_id == FORECAST_MANUAL_PIVOT_ID:
        return _FORECAST_MANUAL_PIVOT_NAME, dict(_FORECAST_MANUAL_SCHEMA)
    safe_name = await _analysis_physical_name(db, destination_table_id)
    if not safe_name:
        raise HTTPException(status_code=404, detail="Tabela destino ativa não encontrada")
    cache_key = _schema_cache_key(layers.detail_layer(), safe_name)
    cached = _SCHEMA_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < _SCHEMA_CACHE_TTL_S:
        return safe_name, cached[1]
    async with _SCHEMA_LOCK:
        cached = _SCHEMA_CACHE.get(cache_key)
        if cached and time.monotonic() - cached[0] < _SCHEMA_CACHE_TTL_S:
            return safe_name, cached[1]
        task = _SCHEMA_INFLIGHT.get(cache_key)
        if task is None or task.done():
            async def _load() -> dict[str, str]:
                return await _describe_layer_schema(safe_name, max_retries)

            task = asyncio.create_task(_load())
            _SCHEMA_INFLIGHT[cache_key] = task
    try:
        schema = await task
        return safe_name, schema
    except HTTPException:
        raise
    except Exception as exc:
        try:
            schema = await _local_iceberg_schema(layers.detail_layer(), safe_name)
            _SCHEMA_CACHE[cache_key] = (time.monotonic(), schema)
            return safe_name, schema
        except HTTPException:
            raise
        except Exception as local_exc:
            logger.warning("Schema Iceberg local também falhou para %s: %s", cache_key, local_exc)
        raise HTTPException(status_code=503, detail=f"Trino é obrigatório para tabela dinâmica: {exc}")
    finally:
        if _SCHEMA_INFLIGHT.get(cache_key) is task and task.done():
            _SCHEMA_INFLIGHT.pop(cache_key, None)


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
    """Accept UI-friendly casing while emitting the exact Trino identifiers.

    Planning tables expose the monthly bucket as ``period`` while Gold uses
    ``_billing_period``; both carry YYYYMM. A view saved against one spelling
    keeps working against the other by aliasing any period name onto the
    physical column the target table actually exposes (mirrors the union's
    ``period_source`` alignment).
    """
    by_lower = {name.lower(): name for name in schema}
    schema_period = _schema_period_column(schema)
    def canonical(name: str) -> str:
        exact = by_lower.get(name.lower())
        if exact:
            return exact
        if schema_period and name.lower() in _PERIOD_NAMES_LOWER:
            return schema_period
        return name
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
        if item.operator == "IS_NULL":
            clauses.append(f"{_quote_ident(item.column)} IS NULL")
            continue
        if item.operator == "IS_NOT_NULL":
            clauses.append(f"{_quote_ident(item.column)} IS NOT NULL")
            continue
        if item.values:
            if item.operator == "NOT_IN":
                vals = []
                for v in item.values:
                    sv = str(v).lower()
                    vals.extend([_quote_literal(sv), _quote_literal(sv.replace(" ", "-")), _quote_literal(sv.replace("-", " "))])
                vals_str = ', '.join(set(vals))
                clauses.append(f"({_quote_ident(item.column)} IS NULL OR lower(CAST({_quote_ident(item.column)} AS VARCHAR)) NOT IN ({vals_str}))")
            elif item.operator == "CONTAINS":
                clauses.append("(" + " OR ".join(f"lower(CAST({_quote_ident(item.column)} AS VARCHAR)) LIKE {_quote_literal('%' + str(v).lower() + '%')}" for v in item.values) + ")")
            elif item.operator == "NOT_CONTAINS":
                clauses.append("(" + " AND ".join(f"lower(CAST({_quote_ident(item.column)} AS VARCHAR)) NOT LIKE {_quote_literal('%' + str(v).lower() + '%')}" for v in item.values) + ")")
            else:
                vals = []
                for v in item.values:
                    sv = str(v).lower()
                    vals.extend([_quote_literal(sv), _quote_literal(sv.replace(" ", "-")), _quote_literal(sv.replace("-", " "))])
                vals_str = ', '.join(set(vals))
                clauses.append(f"lower(CAST({_quote_ident(item.column)} AS VARCHAR)) IN ({vals_str})")
    for column, value in (cell_filters or {}).items():
        if value is None:
            clauses.append(f"{_quote_ident(column)} IS NULL")
        else:
            clauses.append(f"lower(CAST({_quote_ident(column)} AS VARCHAR)) = {_quote_literal(str(value).lower())}")
    return " WHERE " + " AND ".join(clauses) if clauses else ""


_PERIOD_COLUMNS = ("_billing_period", "period", "forecast_month")
_PERIOD_NAMES_LOWER = {name.lower() for name in _PERIOD_COLUMNS}
_MEASURE_FALLBACKS = ("realizado", "value", "EffectiveCost", "BilledCost")


def _has_period_filter(filters: list[PivotFilter]) -> bool:
    return any(f.values and f.column.lower() in _PERIOD_NAMES_LOWER for f in filters)


def _period_on_axes(*axis_groups: list[str]) -> bool:
    names = {str(name).lower() for group in axis_groups for name in group}
    return bool(names & _PERIOD_NAMES_LOWER)


def _schema_period_column(schema: dict[str, str]) -> str | None:
    lowered = {name.lower(): name for name in schema}
    for candidate in _PERIOD_COLUMNS:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return None


def _filters_for_side(filters: list[PivotFilter], schema: dict[str, str]) -> list[PivotFilter]:
    """Keep filters this table can evaluate, remapping Gold/planning period names.

    Realized Gold filters on ``_billing_period``; planning uses ``period``.
    Without the remap, a union applied the auto month filter only on Gold and
    returned every forecast month.
    """
    applied: list[PivotFilter] = []
    for item in filters:
        if item.column in schema:
            applied.append(item)
            continue
        if item.column.lower() not in _PERIOD_NAMES_LOWER:
            continue
        physical = _schema_period_column(schema)
        if physical:
            applied.append(item.model_copy(update={"column": physical}))
    return applied


def _origin_from_cell_filters(
    cell_filters: dict[str, Any],
    origin_column: str = "origem",
) -> tuple[dict[str, Any], str | None]:
    """Split the synthetic union dimension from real Gold/planning predicates."""
    remaining: dict[str, Any] = {}
    origin_value: str | None = None
    wanted = origin_column.lower()
    for column, value in cell_filters.items():
        if str(column).lower() == wanted:
            origin_value = None if value is None else str(value)
        else:
            remaining[column] = value
    return remaining, origin_value


def _destination_id_for_origin(table_id: int, origin_value: str | None) -> int:
    """Map realizado/forecast cell labels onto Gold vs planning destination ids."""
    if not origin_value:
        return table_id
    label = origin_value.strip().lower()
    if label in {"forecast", "previsto", "planejado"}:
        base_id = shared_cost_base_destination_id(table_id) if is_shared_cost_pivot_id(table_id) else abs(table_id)
        return -base_id if base_id else table_id
    if label in {"custo compartilhado", "shared_cost"}:
        base_id = shared_cost_base_destination_id(table_id) if is_shared_cost_pivot_id(table_id) else abs(table_id)
        return shared_cost_pivot_id(base_id) if base_id else table_id
    if label in {"realizado", "actual", "gold"}:
        return abs(table_id)
    return table_id


def _cell_filters_for_side(cell_filters: dict[str, Any], schema: dict[str, str]) -> dict[str, Any]:
    """Keep cell predicates this table can evaluate, remapping period names."""
    mapped: dict[str, Any] = {}
    by_lower = {name.lower(): name for name in schema}
    for column, value in cell_filters.items():
        if column in schema:
            mapped[column] = value
            continue
        canonical = by_lower.get(str(column).lower())
        if canonical:
            mapped[canonical] = value
            continue
        if str(column).lower() in _PERIOD_NAMES_LOWER:
            physical = _schema_period_column(schema)
            if physical:
                mapped[physical] = value
    return mapped


def _measure_column_for_side(schema: dict[str, str], requested: str) -> str | None:
    """Resolve a pivot measure on a union side, including planning aliases."""
    if requested in schema:
        return requested
    lowered = {name.lower(): name for name in schema}
    if requested.lower() in lowered:
        return lowered[requested.lower()]
    for candidate in _MEASURE_FALLBACKS:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return None


async def _inject_latest_period_filter(
    namespace: str,
    table_name: str,
    schema: dict[str, str],
    filters: list[PivotFilter],
    trino_kwargs: dict[str, Any] | None = None,
    axis_names: list[str] | None = None,
) -> tuple[list[PivotFilter], str | None]:
    """Restrict unbounded pivots to the latest billing partition.

    The Gold FOCUS table holds ~25M rows (mostly Azure-TI). Pivot/search
    without a period filter forces Trino to scan every partition and often
    OOMs the 2 GB coordinator heap.

    When the user already put a period column on rows/columns, do not collapse
    the axis to a single month — that made realizado look like "current month
    only" while forecast still spanned ``period``.
    """
    period_col = _schema_period_column(schema)
    if not period_col or _has_period_filter(filters) or _period_on_axes(axis_names or []):
        return filters, None
    latest: str | None = None
    try:
        latest_rows = await trino_execute(
            f"SELECT MAX(CAST({_quote_ident(period_col)} AS VARCHAR)) AS latest "
            f"FROM iceberg.{namespace}.{table_name}",
            **({"timeout_s": 30, **trino_kwargs} if trino_kwargs else {"timeout_s": 30}),
        )
        latest = latest_rows[0].get("latest") if latest_rows else None
    except Exception as exc:
        logger.warning("MAX período via Trino falhou para %s.%s (%s); tentando Iceberg local", namespace, table_name, exc)
        latest = await asyncio.to_thread(_local_latest_period_sync, namespace, table_name, period_col)
    if not latest:
        return filters, None
    return [
        *filters,
        PivotFilter(column=period_col, values=[str(latest)], operator="IN"),
    ], str(latest)


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


def _dimension_order_by(row_count: int, column_count: int) -> str:
    """Order the outer pivot by positional aliases ``d0..dn``.

    After ``_apply_pivot_axis_limits`` the result set only exposes those
    aliases, so ``ORDER BY "organograma"`` 503s in Trino.
    """
    total = row_count + column_count
    if total <= 0:
        return ""
    return " ORDER BY " + ", ".join(f"d{i}" for i in range(total))


def _apply_pivot_axis_limits(
    aggregated_sql: str,
    rows: list[str],
    columns: list[str],
    num_measures: int,
    row_limit: int,
    column_limit: int,
    order_by: str,
) -> str:
    """Keep every billing period for the top row keys.

    A global ``LIMIT row_limit * column_limit`` on the grouped result filled up
    with early months / forecast keys and dropped realizado from January onward.
    """
    all_names = [*rows, *columns]
    period_aliases = [f"d{i}" for i, name in enumerate(all_names) if str(name).lower() in _PERIOD_NAMES_LOWER]
    other_aliases = [f"d{i}" for i, name in enumerate(all_names) if str(name).lower() not in _PERIOD_NAMES_LOWER]
    hard_cap = 30000
    if not all_names:
        return f"{aggregated_sql}{order_by} LIMIT {hard_cap}"

    rlim = max(1, min(int(row_limit or 500), 2000))
    clim = max(1, min(int(column_limit or 60), 120))
    measure0 = "m0" if num_measures else None
    ctes = [f"agg AS ({aggregated_sql})"]
    joins: list[str] = []
    if other_aliases:
        keys = ", ".join(other_aliases)
        # Rank by absolute impact so large credits/refunds (negative values)
        # are not silently dropped behind low positive spend in an unfiltered
        # pivot. Filtering to a specific refund dimension previously made the
        # same rows appear because they then competed only with each other.
        rank = f" ORDER BY SUM(ABS(COALESCE({measure0}, 0))) DESC" if measure0 else f" ORDER BY {keys}"
        ctes.append(f"row_keys AS (SELECT {keys} FROM agg GROUP BY {keys}{rank} LIMIT {rlim})")
        # Comparing each nullable dimension separately can make Trino's join
        # planner discard every row when deeper hierarchy levels are NULL.
        # A row-wise null-safe comparison preserves the composite key.
        agg_key = ", ".join(f"agg.{key}" for key in other_aliases)
        selected_key = ", ".join(f"row_keys.{key}" for key in other_aliases)
        cond = f"ROW({agg_key}) IS NOT DISTINCT FROM ROW({selected_key})"
        joins.append(f"JOIN row_keys ON {cond}")
    if period_aliases:
        keys = ", ".join(period_aliases)
        ctes.append(f"period_keys AS (SELECT {keys} FROM agg GROUP BY {keys} ORDER BY {keys} LIMIT {clim})")
        cond = " AND ".join(f"agg.{k} IS NOT DISTINCT FROM period_keys.{k}" for k in period_aliases)
        joins.append(f"JOIN period_keys ON {cond}")
    if not joins:
        return f"{aggregated_sql}{order_by} LIMIT {hard_cap}"
    return f"WITH {', '.join(ctes)} SELECT agg.* FROM agg {' '.join(joins)}{order_by} LIMIT {hard_cap}"


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


async def _declared_column_order(
    db: AsyncSession,
    destination_table_id: int,
    schema: dict[str, str],
) -> list[str]:
    """Order Gold columns from destination metadata, then remaining Iceberg fields."""
    from app.models.destination import DestinationColumn

    declared: list[str] = []
    dest_id = (
        shared_cost_base_destination_id(destination_table_id)
        if is_shared_cost_pivot_id(destination_table_id)
        else abs(destination_table_id)
    )
    if dest_id:
        result = await db.execute(
            select(DestinationColumn.column_name)
            .where(DestinationColumn.destination_table_id == dest_id)
            .order_by(DestinationColumn.position, DestinationColumn.id)
        )
        declared = [name for name in result.scalars().all() if name in schema]
    extras = [name for name in schema if name not in set(declared)]
    return [*declared, *extras]


@router.get("/pivot/metadata/{destination_table_id}")
async def pivot_metadata(destination_table_id: int, db: AsyncSession = Depends(get_db)):
    """Return the live Gold/Iceberg schema, ordered by destination column metadata."""
    _, schema = await _pivot_table_context(db, destination_table_id)
    ordered = await _declared_column_order(db, destination_table_id, schema)
    from app.models.rules import BusinessRule, ClassificationRule

    financial_rules = (await db.execute(select(BusinessRule).where(
        BusinessRule.active.is_(True),
        or_(BusinessRule.destination_table_id == destination_table_id,
            (BusinessRule.destination_table_id.is_(None) if destination_table_id == 1 else False)),
    ))).scalars().all()
    classification_rules = (await db.execute(select(ClassificationRule).where(
        ClassificationRule.active.is_(True),
        or_(ClassificationRule.destination_table_id == destination_table_id,
            (ClassificationRule.destination_table_id.is_(None) if destination_table_id == 1 else False)),
    ))).scalars().all()
    financial_columns: set[str] = set()
    for rule in financial_rules:
        if rule.target_column:
            financial_columns.add(str(rule.target_column).lower())
        for condition in rule.conditions or []:
            if isinstance(condition, dict) and condition.get("then_field"):
                financial_columns.add(str(condition["then_field"]).lower())
    if financial_rules:
        financial_columns.add("business_rule")
    org_columns = ({
        "organograma", "centro_custo", "id_orcamento", "ambiente", "custom_fields",
        *(f"organograma_nivel_{level}" for level in range(1, 8)),
    } if classification_rules else set())
    org_columns.update(
        str(rule.target_field).lower()
        for rule in classification_rules
        if rule.target_field
    )
    if classification_rules:
        org_columns.add("business_rule")
    numeric = [
        name for name in ordered
        if any(token in schema[name].lower() for token in ("int", "double", "real", "decimal", "float", "bigint"))
    ]
    numeric_set = set(numeric)
    dimensions = [name for name in ordered if name not in numeric_set]
    float_measures = [
        name for name in numeric
        if any(token in schema[name].lower() for token in ("double", "real", "decimal", "float"))
    ]
    return {
        "destination_table_id": destination_table_id,
        "columns": [{
            "name": name,
            "type": schema[name],
            "numeric": name in numeric_set,
            "enrichment": (
                "financial" if name.lower() in financial_columns
                else "organization" if name.lower() in org_columns
                else None
            ),
        } for name in ordered],
        "dimensions": dimensions,
        "measures": numeric,
        "recommended_dimensions": dimensions[:8],
        "default_measure": (float_measures[0] if float_measures else numeric[0]) if numeric else None,
    }


@router.post("/pivot/search")
async def search_pivot_table(req: LakehouseSearchRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Search a term across every textual column of a Gold table in Trino."""
    if not req.term.strip():
        raise HTTPException(status_code=422, detail="Informe um texto para pesquisar")
    async with _ClientCancelScope(request, timeout_s=25) as scope:
        try:
            return await _search_pivot_table_core(req, db, scope.trino_kwargs)
        except TrinoCancelled:
            raise _cancelled_http()


async def _search_pivot_table_core(
    req: LakehouseSearchRequest,
    db: AsyncSession,
    trino_kwargs: dict[str, Any],
) -> dict[str, Any]:
    table_name, schema = await _pivot_table_context(db, req.destination_table_id)
    filter_request = PivotRequest(destination_table_id=req.destination_table_id, filters=req.filters)
    _canonicalize_request(filter_request, schema)
    filter_request.filters = _shared_cost_pivot_filters(req.destination_table_id, schema, filter_request.filters)
    invalid_filters = [item.column for item in filter_request.filters if item.column not in schema]
    if invalid_filters:
        raise HTTPException(status_code=422, detail=f"Colunas de filtro inválidas: {', '.join(invalid_filters)}")
    effective_filters, auto_period = await _inject_latest_period_filter(
        layers.detail_layer(), table_name, schema, filter_request.filters, trino_kwargs=trino_kwargs,
    )
    gold_from = await _layer_from(db, req.destination_table_id, table_name, schema)
    filter_sql = _where_clause(effective_filters)
    text_columns = [name for name, typ in schema.items() if any(token in typ.lower() for token in ("varchar", "char", "json"))]
    if not text_columns:
        return {"columns": list(schema), "rows": [], "total": 0, "matched_columns": []}
    term = _quote_literal(f"%{req.term.strip().lower()}%")
    order_candidates = [name for name in ("chargeperiodstart", "billingperiodstart", "date", "_billing_period") if name in schema]
    order = f" ORDER BY TRY_CAST({_quote_ident(order_candidates[0])} AS TIMESTAMP) DESC" if order_candidates else ""
    # Drill-down is intentionally paginated. Never allow a cell click to
    # materialize hundreds/thousands of rows in Trino or the browser.
    limit = max(1, min(req.limit, 100))
    offset = max(0, req.offset)
    lowered = req.term.strip().lower()
    # Search in bounded column groups.  Concatenating all 100+ text columns
    # and selecting ``*`` made Trino materialize a very wide page for every
    # scanned row, exceeding the per-node memory limit on the 25M-row Gold.
    # Groups preserve the all-text-column semantics while keeping each scan
    # narrow enough for the coordinator.
    try:
        rows: list[dict[str, Any]] = []
        # Search the columns users most commonly inspect first; the remaining
        # text columns are still covered while the request has a bounded
        # budget, so an absent term cannot monopolize the Trino coordinator.
        priority = [
            name for name in (
                "servicename", "resourcename", "tags", "custom_fields",
                "chargedescription", "invoicedetaildescription",
                "source_resource_group_name", "organograma",
                "organograma_nivel_1", "organograma_nivel_2",
                "organograma_nivel_3", "organograma_nivel_4",
                "organograma_nivel_5", "organograma_nivel_6",
                "organograma_nivel_7", "cloud_origem",
            ) if name in text_columns
        ]
        ordered_text_columns = priority + [name for name in text_columns if name not in priority]
        deadline = time.monotonic() + 25
        group_size = 16
        for start in range(0, len(ordered_text_columns), group_size):
            if time.monotonic() >= deadline:
                break
            group = ordered_text_columns[start:start + group_size]
            search_condition = "(" + " OR ".join(
                f"lower(CAST({_quote_ident(column)} AS VARCHAR)) LIKE {term}"
                for column in group
            ) + ")"
            where = f" WHERE {search_condition}" + (f" AND {filter_sql[7:]}" if filter_sql else "")
            selected = ", ".join(_quote_ident(column) for column in group)
            sql = f"SELECT {selected} FROM {gold_from}{where} LIMIT {limit - len(rows)}"
            try:
                group_rows = await trino_execute(
                    sql,
                    **({"timeout_s": min(20, max(5, int(deadline - time.monotonic()))), **trino_kwargs}),
                )
                rows.extend(group_rows)
            except TrinoCancelled:
                raise
            except Exception as group_error:
                logger.warning("Busca parcial interrompida no grupo %s: %s", group, group_error)
                break
            if len(rows) >= limit:
                break
        matched = [[name for name in row if name in text_columns and lowered in str(row.get(name) or "").lower()] for row in rows]
    except TrinoCancelled:
        raise
    except Exception as exc:
        logger.warning("Busca sem resultados após falha do Trino: %s", exc)
        rows = []
    response = {
        "columns": list(dict.fromkeys(name for row in rows for name in row)) if rows else list(schema),
        "rows": rows,
        "total": len(rows),
        "total_is_estimate": True,
        "limit": limit,
        "offset": offset,
        "matched_columns": matched,
        "searched_columns": text_columns,
    }
    if auto_period:
        response["period_auto_applied"] = auto_period
    return response


def _period_sort_tuple(value: Any) -> tuple:
    """Sort YYYYMM / YYYY-MM as calendar time, not first-seen insertion order."""
    text = str(value or "").strip()
    digits = re.sub(r"[^0-9]", "", text)
    if len(digits) >= 6:
        return (0, int(digits[:6]))
    return (1, text.lower())


def _chronological_axis_keys(keys: list[tuple[Any, ...]], dim_names: list[str]) -> list[tuple[Any, ...]]:
    period_idx = [i for i, name in enumerate(dim_names) if str(name).lower() in _PERIOD_NAMES_LOWER]
    if not period_idx or not keys:
        return keys
    return sorted(
        keys,
        key=lambda item: tuple(_period_sort_tuple(item[i] if i < len(item) else None) for i in period_idx),
    )


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
    if not sort_rows:
        ordered_row_keys = _chronological_axis_keys(ordered_row_keys, rows)
    if not sort_columns:
        ordered_col_keys = _chronological_axis_keys(ordered_col_keys, columns)

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
async def run_pivot(req: PivotRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Aggregate a Gold destination table in Trino; no raw data is sent to the browser."""
    async with _ClientCancelScope(request) as scope:
        try:
            return await _run_pivot_core(req, db, scope.trino_kwargs)
        except TrinoCancelled:
            raise _cancelled_http()


async def _fetch_realized_periods(trino_kwargs: dict[str, Any]) -> set[str]:
    """Return billing periods that already hold realized data.

    Reads the pre-aggregated Gold cube (dest_dm_cost_*) instead of scanning the
    31M-row Gold: the Gold carries tipo='realizado' with the same period
    grain, so DISTINCT on it is orders of magnitude cheaper.
    """
    try:
        rows = await trino_execute(
            "SELECT DISTINCT _billing_period FROM iceberg.{GOLD}.dest_dm_cost_visao_completa "
            "WHERE tipo = 'realizado'",
            **trino_kwargs,
        )
        return {str(row.get("_billing_period") or "") for row in rows if row.get("_billing_period")}
    except Exception:
        return set()


async def _run_forecast_manual_pivot_core(req: PivotRequest, db: AsyncSession, trino_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Serve the forecast-manual pseudo-destination from the operational DB.

    Only future periods are exposed: once a month has realized data in Gold,
    the manual forecast for that month is considered consumed and disappears.
    """
    schema = dict(_FORECAST_MANUAL_SCHEMA)
    _canonicalize_request(req, schema)
    rows, columns, measures = _validate_pivot_request(req, schema)
    dimensions = [*rows, *columns]
    for measure in measures:
        if measure.column != "value":
            raise HTTPException(422, "O pivot de forecast manual usa apenas a medida value")

    versions = (
        await db.execute(
            select(PlanningVersion)
            .join(FinancialType, FinancialType.id == PlanningVersion.financial_type_id)
            .where(
                FinancialType.code == "forecast-manual",
                PlanningVersion.is_current == True,
            )
            .order_by(PlanningVersion.year.desc())
        )
    ).scalars().all()
    if not versions:
        return _build_pivot_result([], rows, columns, measures, req.include_totals, req.rows, req.sort_rows, req.sort_columns)

    version_ids = [version.id for version in versions]
    entries = (
        await db.execute(
            select(PlanningEntry).where(
                PlanningEntry.planning_version_id.in_(version_ids),
                PlanningEntry.entity_type == "department",
            )
        )
    ).scalars().all()
    if not entries:
        return _build_pivot_result([], rows, columns, measures, req.include_totals, req.rows, req.sort_rows, req.sort_columns)

    departments = (await db.execute(select(Department).where(Department.active == True))).scalars().all()
    department_map = {department.id: department for department in departments}

    def levels_of(dept_id: int | None) -> list[str]:
        chain: list[str] = []
        current = department_map.get(dept_id) if dept_id else None
        seen: set[int] = set()
        while current and current.id not in seen:
            seen.add(current.id)
            chain.append(current.name or "")
            current = department_map.get(current.parent_id) if current.parent_id else None
        return list(reversed(chain))

    year_by_version = {version.id: int(version.year or 0) for version in versions}
    realized = await _fetch_realized_periods(trino_kwargs)
    grouped: dict[tuple[Any, ...], list[float]] = {}
    for entry in entries:
        month = int(entry.month or 0)
        if month < 1 or month > 12:
            continue
        entry_year = year_by_version.get(entry.planning_version_id, 0)
        if not entry_year:
            continue
        period = f"{entry_year:04d}{month:02d}"
        # Manual forecast is only meaningful for months that have not realized yet.
        if period in realized:
            continue
        value = float(entry.value or 0)
        if abs(value) < 1e-12:
            continue
        levels = levels_of(entry.department_id)
        record: dict[str, Any] = {
            "period": period,
            "tipo": "forecast-manual",
            "organograma": levels[-1] if levels else "",
        }
        for index, name in enumerate(levels, 1):
            record[f"organograma_nivel_{index}"] = name
        key = tuple(record.get(name) for name in dimensions)
        bucket = grouped.setdefault(key, [0.0] * len(measures))
        for measure_index, measure in enumerate(measures):
            bucket[measure_index] += value

    records: list[dict[str, Any]] = []
    for key, values in grouped.items():
        record: dict[str, Any] = {f"d{i}": key[i] for i in range(len(dimensions))}
        for measure_index, value in enumerate(values):
            record[f"m{measure_index}"] = value
        records.append(record)

    result = _build_pivot_result(records, rows, columns, measures, req.include_totals, req.rows, req.sort_rows, req.sort_columns)
    result["period_auto_applied"] = "Somente períodos futuros (sem realizado na Gold)"
    return result


async def _run_pivot_core(req: PivotRequest, db: AsyncSession, trino_kwargs: dict[str, Any]) -> dict[str, Any]:
    if req.destination_table_id == FORECAST_MANUAL_PIVOT_ID:
        return await _run_forecast_manual_pivot_core(req, db, trino_kwargs)
    table_name, schema = await _pivot_table_context(db, req.destination_table_id)
    req.filters = _shared_cost_pivot_filters(req.destination_table_id, schema, req.filters)
    _canonicalize_request(req, schema)
    # Planning pseudo-destinations hold the monthly forecast snapshot (tipo
    # forecast/realizado/rateio). A single-table pivot over them should behave
    # like the union's forecast side: only the forecast rows, unless the user
    # already constrained tipo themselves.
    if req.destination_table_id < 0 and not is_shared_cost_pivot_id(req.destination_table_id) and "tipo" in schema and not any(f.column == "tipo" for f in req.filters):
        req.filters.append(PivotFilter(column="tipo", values=["forecast"], operator="IN"))
    rows, columns, measures = _validate_pivot_request(req, schema)
    req.filters, auto_period = await _inject_latest_period_filter(
        layers.detail_layer(), table_name, schema, req.filters, trino_kwargs=trino_kwargs,
        axis_names=[*rows, *columns],
    )
    dimensions = rows + columns
    select_dims = [f"{_pivot_dimension_sql(name, schema)} AS d{index}" for index, name in enumerate(dimensions)]
    measure_parts, aliases = zip(*[_measure_sql(measure, index) for index, measure in enumerate(measures)])
    where = _where_clause(req.filters)
    group_by = f" GROUP BY {', '.join(str(index + 1) for index in range(len(dimensions)))}" if dimensions else ""
    if req.sort_rows:
        order_by = _build_order_by(req.sort_rows, rows, columns, len(measures))
    else:
        order_by = _dimension_order_by(len(rows), len(columns))
    # A bounded aggregated result is deliberate: it protects Trino and the UI.
    gold_from = await _layer_from(db, req.destination_table_id, table_name, schema)
    inner_sql = f"SELECT {', '.join([*select_dims, *measure_parts])} FROM {gold_from}{where}{group_by}"
    sql = _apply_pivot_axis_limits(
        inner_sql, rows, columns, len(measures), req.row_limit, req.column_limit, order_by,
    )
    try:
        records = await trino_execute(sql, **trino_kwargs)
    except TrinoCancelled:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Falha ao executar pivot no Trino: {exc}")
    result = _build_pivot_result(records, rows, columns, measures, req.include_totals, req.rows, req.sort_rows, req.sort_columns)
    if auto_period:
        result["period_auto_applied"] = auto_period
    return result


@router.post("/pivot/union")
async def run_pivot_union(req: PivotUnionRequest, request: Request, db: AsyncSession = Depends(get_db)):
    async with _ClientCancelScope(request, timeout_s=120) as scope:
        try:
            return await _run_pivot_union_core(req, db, scope.trino_kwargs)
        except TrinoCancelled:
            raise _cancelled_http()


async def _run_pivot_union_core(req: PivotUnionRequest, db: AsyncSession, trino_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Union two (or N) Gold destinations/pseudo-destinations into one pivot.

    Each table is resolved via ``_analysis_physical_name`` (negative ids are
    planning pseudo-destinations). The schemas differ (a Gold table has hundreds
    of columns, a planning table keeps only dimensions + period + tipo + the
    measure), so a plain ``UNION ALL`` would fail. Instead each side selects the
    same column set using ``CAST(NULL AS ...)`` for columns it lacks. A
    synthetic origin column (``origem``) is projected so the client can group
    by realizado vs forecast when that field is on the axes — never forced.
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
    auto_period: str | None = None
    if not _period_on_axes(rows, columns):
        for _table_id, safe_name, schema in sides:
            if _table_id >= 0 and not safe_name.endswith("_planning"):
                req.filters, auto_period = await _inject_latest_period_filter(
                    layers.detail_layer(), safe_name, schema, req.filters, trino_kwargs=trino_kwargs,
                    axis_names=[*rows, *columns],
                )
                break

    # Cost-sharing and forecast sources can both point at the planning table,
    # but retain independent origin labels and filters in the union.
    origin_values: list[str] = []
    for table_id, safe_name, _schema in sides:
        if is_shared_cost_pivot_id(table_id):
            origin_values.append("Custo compartilhado")
        elif table_id < 0 or safe_name.endswith("_planning"):
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
            source = _measure_column_for_side(schema, measure.column)
            if source:
                exprs.append(f"TRY_CAST({_quote_ident(source)} AS DOUBLE) AS {inner}")
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
        return _filters_for_side(req.filters, schema)

    union_parts: list[str] = []
    for (_table_id, safe_name, schema), origin in zip(sides, origin_values):
        exprs = aligned_projection(schema, origin)
        extra_filters = list(side_filters(schema))
        if origin == "forecast" and "tipo" in schema:
            extra_filters.append(PivotFilter(column="tipo", values=["forecast", "forecast-manual"], operator="IN"))
        elif origin == "Custo compartilhado":
            extra_filters = _shared_cost_pivot_filters(_table_id, schema, extra_filters)
        filter_sql = _where_clause(extra_filters)
        layer_from = await _layer_from(db, _table_id, safe_name, schema)
        union_parts.append(
            f"SELECT {', '.join(exprs)} FROM {layer_from}{filter_sql}"
        )

    # The outer query aliases the aligned columns back to d0..dn / m0..mk and
    # aggregates with the same measure SQL used by the single-table pivot.
    dim_aliases = [f"{_pivot_dimension_sql(name, merged_schema)} AS d{i}" for i, name in enumerate(dimension_columns)]
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
    group_by = f" GROUP BY {', '.join(str(index + 1) for index in range(len(dimensions)))}" if dimensions else ""
    if req.sort_rows:
        order_by = _build_order_by(req.sort_rows, rows, columns, len(measures))
    else:
        order_by = _dimension_order_by(len(rows), len(columns))
    union_sql = " UNION ALL ".join(f"({part})" for part in union_parts)
    inner_sql = (
        f"SELECT {', '.join([*dim_aliases, *measure_aliases])} "
        f"FROM ({union_sql}) AS u_src{group_by}"
    )
    sql = _apply_pivot_axis_limits(
        inner_sql, rows, columns, len(measures), req.row_limit, req.column_limit, order_by,
    )

    try:
        records = await trino_execute(sql, **trino_kwargs)
    except TrinoCancelled:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Falha ao executar união no Trino: {exc}")

    result = _build_pivot_result(records, rows, columns, measures, req.include_totals, req.rows, req.sort_rows, req.sort_columns)
    result["origin_column"] = origin_name
    result["origin_values"] = origin_values
    result["tables"] = [{"id": table_id, "physical_name": safe_name, "origin": origin}
                        for (table_id, safe_name, _schema), origin in zip(sides, origin_values)]
    if auto_period:
        result["period_auto_applied"] = auto_period
    return result


@router.post("/pivot/records")
async def pivot_records(req: PivotRecordRequest, request: Request, db: AsyncSession = Depends(get_db)):
    async with _ClientCancelScope(request) as scope:
        try:
            return await _pivot_records_core(req, db, scope.trino_kwargs)
        except TrinoCancelled:
            raise _cancelled_http()


async def _pivot_records_core(req: PivotRecordRequest, db: AsyncSession, trino_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return raw (non-aggregated) Gold records for the browser to feed Perspective."""
    table_name, schema = await _pivot_table_context(db, req.destination_table_id)
    by_lower = {name.lower(): name for name in schema}
    requested = [name for name in req.columns if name.lower() != "origem"]
    if requested:
        canonical_columns = [by_lower.get(name.lower(), name) for name in requested]
        invalid = sorted({name for name in canonical_columns if name not in schema})
        if invalid:
            raise HTTPException(status_code=422, detail=f"Colunas inválidas para esta tabela: {', '.join(invalid)}")
    else:
        canonical_columns = await _declared_column_order(db, req.destination_table_id, schema)
    filter_items = [item.model_copy(update={"column": by_lower.get(item.column.lower(), item.column)}) for item in req.filters]
    filter_items = _shared_cost_pivot_filters(req.destination_table_id, schema, filter_items)
    invalid_filters = sorted({item.column for item in filter_items if item.column not in schema})
    if invalid_filters:
        raise HTTPException(status_code=422, detail=f"Colunas de filtro inválidas: {', '.join(invalid_filters)}")
    filter_items, auto_period = await _inject_latest_period_filter(
        layers.detail_layer(), table_name, schema, filter_items, trino_kwargs=trino_kwargs,
    )
    where = _where_clause(filter_items)
    limit = max(1, min(req.limit, 50000))
    offset = max(0, req.offset)
    select_expr = ", ".join(_quote_ident(name) for name in canonical_columns) if canonical_columns else "*"
    # Do not sort the full Gold partition for a detail page.  The cell
    # filters already identify the slice; ORDER BY on a non-partitioned
    # 30M-row table forces Trino to materialize/sort everything before the
    # LIMIT and was killing the worker with exit 137 (OOM).
    order_by = ""
    gold_from = await _layer_from(db, req.destination_table_id, table_name, schema)
    sql = f"SELECT {select_expr} FROM {gold_from}{where}{order_by} OFFSET {offset} LIMIT {limit}"
    try:
        rows = await trino_execute(sql, **trino_kwargs)
    except TrinoCancelled:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Falha ao carregar registros no Trino: {exc}")
    response = {
        "columns": list(rows[0].keys()) if rows else (canonical_columns if canonical_columns else list(schema)),
        "rows": rows,
        "total": len(rows),
        "limit": limit,
        "offset": offset,
    }
    if auto_period:
        response["period_auto_applied"] = auto_period
    return response


@router.post("/pivot/drill-down")
async def pivot_drill_down(req: PivotDrillDownRequest, request: Request, db: AsyncSession = Depends(get_db)):
    async with _ClientCancelScope(request) as scope:
        try:
            return await _pivot_drill_down_core(req, db, scope.trino_kwargs)
        except TrinoCancelled:
            raise _cancelled_http()


async def _pivot_drill_down_core(req: PivotDrillDownRequest, db: AsyncSession, trino_kwargs: dict[str, Any]) -> dict[str, Any]:
    cell_filters, origin_value = _origin_from_cell_filters(req.cell_filters)
    table_id = _destination_id_for_origin(req.destination_table_id, origin_value)
    table_name, schema = await _pivot_table_context(db, table_id)
    req.filters = _filters_for_side(
        [item.model_copy(update={"column": next((name for name in schema if name.lower() == item.column.lower()), item.column)}) for item in req.filters],
        schema,
    )
    req.filters = _shared_cost_pivot_filters(table_id, schema, req.filters)
    cell_filters = _cell_filters_for_side(cell_filters, schema)
    period_in_cell = any(str(column).lower() in _PERIOD_NAMES_LOWER for column in cell_filters)
    auto_period: str | None = None
    if not period_in_cell:
        req.filters, auto_period = await _inject_latest_period_filter(
            layers.detail_layer(), table_name, schema, req.filters, trino_kwargs=trino_kwargs,
            axis_names=list(cell_filters),
        )
    where = _where_clause(req.filters, cell_filters)
    ordered = await _declared_column_order(db, table_id, schema)
    limit = max(1, min(req.limit, 100))
    offset = max(0, req.offset)
    select_expr = ", ".join(_quote_ident(name) for name in ordered) if ordered else "*"
    # Never sort the full Gold partition for a detail request. The page is
    # located by its key and limited below; sorting first can OOM the Trino
    # worker before LIMIT is applied.
    order_by = ""
    gold_from = await _layer_from(db, table_id, table_name, schema)
    # Locate a bounded page using only the stable source key first. Selecting
    # all ~150 Gold columns while scanning a large Parquet partition makes the
    # Trino worker decode far more data than the detail page needs and was
    # causing the worker (172.21.0.6) to be OOM-killed (exit 137).
    key_column = next((name for name in ordered if name.lower() == "source_record_id"), None)
    if key_column:
        key_sql = f"SELECT {_quote_ident(key_column)} FROM {gold_from}{where}{order_by} OFFSET {offset} LIMIT {limit}"
        try:
            key_rows = await trino_execute(key_sql, **trino_kwargs)
            keys = [row.get(key_column) for row in key_rows if row.get(key_column) is not None]
            if not keys:
                # Older GOLD loads may legitimately have a null
                # source_record_id.  In that case query the bounded slice
                # directly; do not interpret an empty key page as no data.
                sql = f"SELECT {select_expr} FROM {gold_from}{where}{order_by} OFFSET {offset} LIMIT {limit}"
                rows = await trino_execute(sql, **trino_kwargs)
            else:
                key_values = ", ".join(_quote_literal(str(value)) for value in dict.fromkeys(keys))
                detail_where = f"{where} AND {_quote_ident(key_column)} IN ({key_values})"
                sql = f"SELECT {select_expr} FROM {gold_from}{detail_where} LIMIT {limit}"
                rows = await trino_execute(sql, **trino_kwargs)
        except TrinoCancelled:
            raise
        except Exception as exc:
            logger.warning("Drill-down Trino falhou (%s); usando leitura local Iceberg", exc)
            rows = await asyncio.to_thread(_local_drilldown_rows, table_name, ordered, req, cell_filters, offset, limit)
    else:
        sql = f"SELECT {select_expr} FROM {gold_from}{where}{order_by} OFFSET {offset} LIMIT {limit}"
        try:
            rows = await trino_execute(sql, **trino_kwargs)
        except TrinoCancelled:
            raise
        except Exception as exc:
            logger.warning("Drill-down Trino falhou (%s); usando leitura local Iceberg", exc)
            rows = await asyncio.to_thread(_local_drilldown_rows, table_name, ordered, req, cell_filters, offset, limit)
    response = {
        "columns": ordered,
        "rows": rows,
        # Avoid a second unbounded COUNT(*) query: on large Gold partitions it
        # creates another Trino task and was the source of drill-down 503s.
        # Keep a conservative lower-bound/has-more estimate for pagination.
        "total": offset + len(rows) + (1 if len(rows) == limit else 0),
        "total_is_estimate": True,
        "limit": limit,
        "offset": offset,
    }
    if auto_period:
        response["period_auto_applied"] = auto_period
    return response


def _local_drilldown_rows(table_name: str, ordered: list[str], req: PivotDrillDownRequest,
                          cell_filters: dict[str, Any], offset: int, limit: int) -> list[dict[str, Any]]:
    """Bounded local fallback when a Trino worker is restarting/OOMing."""
    from app.services import iceberg_service as iceberg
    table = iceberg._get_catalog().load_table(iceberg._table_id(layers.detail_layer(), table_name))
    from pyiceberg.expressions import AlwaysTrue, And, EqualTo
    scan_filter = AlwaysTrue()
    partition_cols = set(_PERIOD_COLUMNS)
    for item in [*req.filters, *[PivotFilter(column=k, values=[v]) for k, v in cell_filters.items() if v is not None]]:
        if item.column in partition_cols and item.values:
            scan_filter = And(scan_filter, EqualTo(item.column, str(item.values[0])))
    wanted = list(dict.fromkeys([*ordered, *[f.column for f in req.filters], *cell_filters.keys()]))
    wanted = [column for column in wanted if column in {field.name for field in table.schema().fields}]
    matched = 0; emitted: list[dict[str, Any]] = []
    for batch in table.scan(row_filter=scan_filter, selected_fields=wanted).to_arrow_batch_reader():
        frame = batch.to_pandas()
        for item in req.filters:
            if item.column not in frame:
                continue
            if item.operator == "IS_NULL":
                frame = frame[frame[item.column].isna()]
                continue
            if item.operator == "IS_NOT_NULL":
                frame = frame[frame[item.column].notna()]
                continue
            if not item.values:
                continue
            values = [str(v).lower() for v in item.values]
            series = frame[item.column].fillna("").astype(str).str.lower()
            mask = series.str.contains(values[0], regex=False) if item.operator == "CONTAINS" else series.isin(values)
            frame = frame[mask]
        for column, value in cell_filters.items():
            if column in frame:
                frame = frame[frame[column].isna()] if value is None else frame[frame[column].fillna("").astype(str).str.lower() == str(value).lower()]
        if frame.empty:
            continue
        if matched + len(frame) <= offset:
            matched += len(frame); continue
        start = max(0, offset - matched)
        emitted.extend(frame.iloc[start:start + (limit - len(emitted))].to_dict("records"))
        matched += len(frame)
        if len(emitted) >= limit:
            break
    return emitted


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
async def query_dashboard_widget(body: WidgetQueryRequest, http_request: Request, db: AsyncSession = Depends(get_db)):
    view = await db.get(PivotView, body.pivot_view_id)
    if not view:
        raise HTTPException(status_code=404, detail="Visão pivot não encontrada")
    config = dict(view.config_json or {})
    config.update({k: v for k, v in body.config.items() if k in {"rows", "columns", "measures", "filters", "row_limit", "column_limit"}})
    pivot_request = PivotRequest.model_validate({"destination_table_id": view.destination_table_id, **config})
    for column, value in body.global_filters.items():
        values = value if isinstance(value, list) else [value]
        pivot_request.filters.append(PivotFilter(column=column, values=values, operator="IN"))
    source_ids = body.source_ids or config.get("source_ids") or [view.destination_table_id]
    async with _ClientCancelScope(http_request, timeout_s=120) as scope:
        try:
            if len(source_ids) > 1:
                rows = list(pivot_request.rows)
                if "origem" not in rows and "origem" not in pivot_request.columns:
                    rows.append("origem")
                result = await _run_pivot_union_core(PivotUnionRequest(
                    destination_table_ids=source_ids, rows=rows, columns=pivot_request.columns,
                    measures=pivot_request.measures, filters=pivot_request.filters, row_limit=pivot_request.row_limit,
                    column_limit=pivot_request.column_limit, include_totals=True), db, scope.trino_kwargs)
            else:
                # A single active source may be the planning pseudo-destination
                # (negative id). Pivot that table directly, not the Gold table the
                # view was saved against, so forecast-only widgets show forecast.
                pivot_request.destination_table_id = source_ids[0]
                result = await _run_pivot_core(pivot_request, db, scope.trino_kwargs)
        except TrinoCancelled:
            raise _cancelled_http()

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


_CLOUD_BRANCHES = frozenset({"Chargeback", "Showback"})
_CLOUD_PROVIDERS = frozenset({"GCP", "Azure", "AWS", "OCI", "IBM"})
_PROVIDER_CLOUD_PREFIX = {
    "GCP": "gcp",
    "Azure": "azure",
    "AWS": "aws",
    "OCI": "oci",
    "IBM": "ibm",
}


def _expected_view_cloud_filter(
    scope_path: list[str],
    selected: Department | None,
    columns: dict[str, str],
) -> str:
    """Push provider scope into Trino when the selected node is a cloud subtree."""
    if "cloud_origem" not in columns:
        return ""
    prefix = (selected.cloud_filter.strip().lower() if selected and selected.cloud_filter else "")
    if "mongo" in prefix:
        # cloud_origem real é mongodb-atlas-pro/enterprise; filtros legados como
        # "Atlas-MongoDB" não são prefixo válido e escondiam todo o gasto do nó.
        prefix = "mongodb"
    if not prefix and any("mongo" in part.lower() for part in scope_path):
        prefix = "mongodb"
    if not prefix and len(scope_path) >= 3 and scope_path[1] in _CLOUD_BRANCHES and scope_path[2] in _CLOUD_PROVIDERS:
        prefix = _PROVIDER_CLOUD_PREFIX.get(scope_path[2], scope_path[2].lower())
    if not prefix:
        return ""
    cloud_column = _quote_ident(columns["cloud_origem"])
    return f" AND lower(CAST({cloud_column} AS VARCHAR)) LIKE {_quote_literal(prefix + '%')}"


@router.post("/expected-view")
async def expected_view(body: ExpectedViewRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Executive view for any node of the seven-level organization tree."""
    async with _ClientCancelScope(request, timeout_s=120) as scope:
        try:
            return await _expected_view_core(body, db, scope.trino_kwargs)
        except TrinoCancelled:
            raise _cancelled_http()


async def _expected_view_period_detail_core(
    body: ExpectedViewPeriodDetailRequest,
    db: AsyncSession,
    interactive: dict[str, Any],
) -> dict[str, Any]:
    """Dinâmica de um mês: valor realizado ou forecast de cada departamento,
    de todos os níveis (1..7), dentro do escopo selecionado."""
    await wait_until_ready(timeout_s=20)
    table = body.gold_table.strip().replace('"', "")
    schema = await _describe_layer_schema(table, max_retries=2, namespace=GOLD)
    columns = {name.lower(): name for name in schema}
    for required in ("period", "realizado", "tipo", "organograma_nivel_1"):
        if required not in columns:
            raise HTTPException(status_code=422, detail=f"A tabela de cubo Gold não possui a coluna {required}")
    period_col = _quote_ident(columns.get("_billing_period") or columns["period"])
    value_col = _quote_ident(columns["realizado"])
    type_col = _quote_ident(columns["tipo"])

    departments = (await db.execute(select(Department).where(Department.active == True))).scalars().all()
    department_map = {department.id: department for department in departments}
    selected = department_map.get(body.department_id) if body.department_id is not None else None
    if body.department_id is not None and not selected:
        raise HTTPException(status_code=422, detail="Departamento selecionado não existe ou está inativo")
    if selected:
        scope_path: list[str] = []
        current: Department | None = selected
        seen: set[int] = set()
        while current and current.id not in seen:
            seen.add(current.id)
            scope_path.append((current.name or "").strip())
            current = department_map.get(current.parent_id) if current.parent_id else None
        scope_path.reverse()
    else:
        scope_path = [body.director]
    if len(scope_path) > 7:
        raise HTTPException(status_code=422, detail="A Visão Esperada suporta até 7 níveis")

    def scope_filter() -> str:
        checks: list[str] = []
        for level, name in enumerate(scope_path, start=1):
            key = f"organograma_nivel_{level}"
            if key not in columns:
                raise HTTPException(status_code=422, detail=f"A tabela não possui {key}")
            checks.append(f"TRIM(CAST({_quote_ident(columns[key])} AS VARCHAR)) = {_quote_literal(name.strip())}")
        return " AND ".join(checks)

    tipo = str(body.tipo or "realizado").lower()
    if tipo not in ("realizado", "forecast"):
        raise HTTPException(status_code=422, detail="tipo deve ser realizado ou forecast")
    if tipo == "realizado":
        tipo_sql = f"({type_col} IS NULL OR lower(CAST({type_col} AS VARCHAR)) IN ('realizado', ''))"
    else:
        tipo_sql = f"lower(CAST({type_col} AS VARCHAR)) = 'forecast'"

    level_cols = [f"organograma_nivel_{level}" for level in range(1, 8)]
    group_cols = [_quote_ident(columns[col]) for col in level_cols if col in columns]
    cloud_filter = _expected_view_cloud_filter(scope_path, selected, columns)
    sql = f"""
      SELECT {', '.join(group_cols)}, SUM(COALESCE(TRY_CAST({value_col} AS DOUBLE), 0)) AS value
      FROM iceberg.{GOLD}.{table}
      WHERE {scope_filter()}{cloud_filter}
        AND {period_col} = {_quote_literal(body.period)}
        AND {tipo_sql}
      GROUP BY {', '.join(group_cols)}
    """
    try:
        leaf_rows = await trino_execute(sql, **interactive)
    except TrinoCancelled:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Falha ao consultar a dinâmica do mês: {exc}")

    # Acumula o valor de cada folha em todos os ancestrais (rollup por nível).
    nodes: dict[tuple[str, ...], dict[str, Any]] = {}
    total = 0.0
    for row in leaf_rows:
        path: list[str] = []
        value = float(row.get("value") or 0)
        total += value
        for level in range(1, 8):
            name = str(row.get(f"organograma_nivel_{level}") or "").strip()
            if not name:
                break
            path.append(name)
            key = tuple(path)
            node = nodes.setdefault(key, {"nivel": level, "name": name, "path": list(path), "value": 0.0})
            node["value"] = float(node["value"] or 0) + value
    rows = sorted(nodes.values(), key=lambda node: (node["nivel"], -float(node["value"] or 0)))
    return {
        "period": body.period,
        "tipo": tipo,
        "scope_path": scope_path,
        "total": round(total, 2),
        "rows": [{**node, "value": round(float(node["value"] or 0), 2)} for node in rows],
    }


@router.post("/expected-view/period-detail")
async def expected_view_period_detail(body: ExpectedViewPeriodDetailRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Dinâmica de um mês selecionado (clique na coluna): departamentos de
    todos os níveis, realizado ou forecast, no escopo do departamento."""
    async with _ClientCancelScope(request, timeout_s=120) as scope:
        try:
            return await _expected_view_period_detail_core(body, db, scope.trino_kwargs)
        except TrinoCancelled:
            raise _cancelled_http()


async def _expected_view_core(
    body: ExpectedViewRequest,
    db: AsyncSession,
    interactive: dict[str, Any],
) -> dict[str, Any]:
    try:
        await wait_until_ready(timeout_s=20)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Trino está reiniciando: {exc}")
    # A Visão Esperada lê a Gold pré-agregada (dest_dm_cost_*): realizado,
    # forecast e rateio na mesma granularidade de departamento. Uma única
    # tabela pré-agregada substitui a varredura da Gold (31M linhas) e da planning.
    actual_name = forecast_name = body.gold_table.strip().replace('"', "")
    gold_schema = await _describe_layer_schema(actual_name, max_retries=2, namespace=GOLD)
    actual_schema = forecast_schema = gold_schema
    actual_columns = {name.lower(): name for name in actual_schema}
    forecast_columns = actual_columns
    measure_column = body.measure_column if body.measure_column in {"realizado", "realizado_cloud"} else "realizado"
    required_actual = {"_billing_period", measure_column, "organograma_nivel_1"}
    # Forecast planning publishes both measures for financial contracts. Use
    # the same measure selected for actuals; this keeps the dashboard switch
    # aligned across realized and forecast rows.
    required_forecast = {"period", measure_column, "tipo", "organograma_nivel_1", "rateio_medida"}
    missing_actual = sorted(required_actual - set(actual_columns))
    missing_forecast = sorted(required_forecast - set(forecast_columns))
    if missing_actual or missing_forecast:
        parts = []
        if missing_actual:
            parts.append(f"realizado: {', '.join(missing_actual)}")
        if missing_forecast:
            parts.append(f"forecast/planning: {', '.join(missing_forecast)}")
        raise HTTPException(
            status_code=422,
            detail=(
                "A tabela de cubo Gold não possui as colunas necessárias para a Visão Esperada "
                f"({actual_name}): {'; '.join(parts)}. "
                "Reprocesse o cubo visao-completa (dest_dm_cost_visao_completa)."
            ),
        )
    actual_period = _quote_ident(actual_columns.get("_billing_period") or actual_columns["period"])
    # ``realizado`` is the executive financial measure.  ``EffectiveCost`` is
    # a provider net-cost field and includes credit/return adjustments, which
    # can make a director's displayed realized spend negative.
    actual_value = _quote_ident(actual_columns[measure_column])
    forecast_period = _quote_ident(forecast_columns.get("period") or forecast_columns["_billing_period"])
    forecast_value = _quote_ident(forecast_columns[measure_column])
    forecast_type = _quote_ident(forecast_columns["tipo"])
    departments = (await db.execute(select(Department).where(Department.active == True))).scalars().all()
    department_map = {department.id: department for department in departments}
    requested_department_ids = list(dict.fromkeys(body.department_ids or ([] if body.department_id is None else [body.department_id])))
    if len(requested_department_ids) > 200:
        raise HTTPException(status_code=422, detail="Selecione até 200 nós do organograma")
    selected_departments = [department_map.get(node_id) for node_id in requested_department_ids] if (body.department_ids or not body.director_view) else []
    if any(department is None for department in selected_departments):
        raise HTTPException(status_code=422, detail="Um ou mais nós selecionados não existem ou estão inativos")
    selected_departments = [department for department in selected_departments if department is not None]
    selected = selected_departments[0] if selected_departments else None
    if body.department_id is not None and not body.director_view and not selected:
        raise HTTPException(status_code=422, detail="Departamento selecionado não existe ou está inativo")
    def department_path(department: Department) -> list[str]:
        scope_path: list[str] = []
        current: Department | None = department
        seen: set[int] = set()
        while current and current.id not in seen:
            seen.add(current.id); scope_path.append((current.name or "").strip())
            current = department_map.get(current.parent_id) if current.parent_id else None
        scope_path.reverse()
        if body.director_view and len(scope_path) >= 4:
            scope_path[2], scope_path[3] = scope_path[3], scope_path[2]
        return scope_path

    if selected:
        scope_path = department_path(selected)
    else:
        scope_path = [part.strip() for part in body.director.split(" > ") if part.strip()] if body.director_view else ([body.director] if body.director.strip() else [])
    if len(scope_path) > 7:
        raise HTTPException(status_code=422, detail="A Visão Esperada suporta até 7 níveis de departamento")
    scope_level_order = [1, 2, 4, 3, 5, 6] if body.director_view else list(range(1, len(scope_path) + 1))

    def scope_filter(columns: dict[str, str], path: list[str]) -> str:
        checks: list[str] = []
        # A project can be reached through Showback or Chargeback. At project
        # level the executive realized value is the consumption total, not
        # only the small chargeback allocation. Historical Gold snapshots
        # also contain both orders for the Azure/Diretoria levels.
        pairs = list(zip(scope_level_order, path))
        if len(path) >= 5 and path[1] in _CLOUD_BRANCHES:
            pairs = [(level, name) for level, name in pairs if level != 2]
            if body.director_view and "organograma_nivel_3" in columns and "organograma_nivel_4" in columns:
                first = _quote_literal(path[2])
                second = _quote_literal(path[3])
                level3 = _quote_ident(columns["organograma_nivel_3"])
                level4 = _quote_ident(columns["organograma_nivel_4"])
                checks.append(f"(({level3} = {first} AND {level4} = {second}) OR ({level3} = {second} AND {level4} = {first}))")
                pairs = [(level, name) for level, name in pairs if level not in (3, 4)]
        for level, name in pairs:
            key = f"organograma_nivel_{level}"
            if key not in columns:
                raise HTTPException(status_code=422, detail=f"A tabela não possui {key}; reprocese os dados após ajustar o organograma")
            checks.append(
                f"TRIM(CAST({_quote_ident(columns[key])} AS VARCHAR)) = {_quote_literal(name.strip())}"
            )
        return " AND ".join(checks)

    selected_paths = [(department_path(department), department) for department in selected_departments]
    if not selected_paths:
        selected_paths = [(scope_path, None)]
    actual_scope_options = [
        f"({scope_filter(actual_columns, path) or '1=1'}{_expected_view_cloud_filter(path, department, actual_columns)})"
        for path, department in selected_paths
    ]
    forecast_scope_options = [
        f"({scope_filter(forecast_columns, path) or '1=1'}{_expected_view_cloud_filter(path, department, forecast_columns)})"
        for path, department in selected_paths
    ]
    actual_scope_filter = " OR ".join(actual_scope_options)
    forecast_scope_filter = " OR ".join(forecast_scope_options)
    actual_cloud_filter = forecast_cloud_filter = ""
    # Column filter names are always resolved against the physical Gold schema.
    # A dimension missing from the cube cannot silently degrade into a global result.
    dimension_predicates: list[str] = []
    for requested_column, values in (body.dimension_filters or {}).items():
        if not values:
            continue
        actual_physical = actual_columns.get(requested_column.lower())
        forecast_physical = forecast_columns.get(requested_column.lower())
        if not actual_physical or not forecast_physical:
            raise HTTPException(status_code=422, detail=f"Filtro dimensional indisponível na Gold: {requested_column}")
        if len(values) > 500:
            raise HTTPException(status_code=422, detail=f"Selecione até 500 valores em {requested_column}")
        literals = ", ".join(_quote_literal(str(value).lower()) for value in values)
        dimension_predicates.append(f"lower(CAST({_quote_ident(actual_physical)} AS VARCHAR)) IN ({literals})")
    dimension_filter = " AND " + " AND ".join(dimension_predicates) if dimension_predicates else ""
    # year <= 0 = "todos os períodos": sem recorte de ano — traz todo o
    # realizado e todo o forecast disponível na Gold, inclusive meses futuros.
    if body.month_from and (len(body.month_from) != 6 or not body.month_from.isdigit()) or body.month_to and (len(body.month_to) != 6 or not body.month_to.isdigit()):
        raise HTTPException(status_code=422, detail="Período deve usar YYYYMM")
    if body.month_from and body.month_to and body.month_from > body.month_to:
        raise HTTPException(status_code=422, detail="Mês inicial deve ser anterior ou igual ao mês final")
    all_periods = body.year <= 0 and not body.month_from and not body.month_to
    year_start = _quote_literal(f"{max(body.year, 1):04d}01")
    year_end = _quote_literal(f"{max(body.year, 1) + 1:04d}01")
    actual_year_clause = "" if all_periods else (f" AND {actual_period} >= {_quote_literal(body.month_from)}" if body.month_from else f" AND {actual_period} >= {year_start}") + (f" AND {actual_period} <= {_quote_literal(body.month_to)}" if body.month_to else f" AND {actual_period} < {year_end}")
    forecast_year_clause = "" if all_periods else (f" AND {forecast_period} >= {_quote_literal(body.month_from)}" if body.month_from else f" AND {forecast_period} >= {year_start}") + (f" AND {forecast_period} <= {_quote_literal(body.month_to)}" if body.month_to else f" AND {forecast_period} < {year_end}")
    tipo_actual = ""
    if "tipo" in actual_columns:
        tipo_col = _quote_ident(actual_columns["tipo"])
        tipo_actual = f" AND ({tipo_col} IS NULL OR lower(CAST({tipo_col} AS VARCHAR)) IN ('realizado', ''))"
    try:
        cutoff_rows = await trino_execute(
            f"""
            SELECT MIN({forecast_period}) AS period
            FROM iceberg.{GOLD}.{forecast_name}
            WHERE ({forecast_scope_filter}){dimension_filter}
              {forecast_year_clause}
              AND lower(CAST({forecast_type} AS VARCHAR)) = 'forecast'
            """,
            **interactive,
        )
    except TrinoCancelled:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Falha ao consultar a Visão Esperada: {exc}")
    cutoff = str(cutoff_rows[0]["period"]) if cutoff_rows and cutoff_rows[0].get("period") is not None else None
    cutoff_lit = _quote_literal(cutoff) if cutoff else None
    # Realizado always comes from Gold for every collected month. A global
    # ``actual_period < MIN(forecast)`` cutoff hid recent loads (e.g. July
    # azure-marketplace) whenever another series already had forecast rows in
    # an earlier month.
    forecast_from = f" AND {forecast_period} >= {cutoff_lit}" if cutoff_lit else ""
    # A Visão Diretoria muda a ordem de navegação para 1 → 2 → 4 → 3 → 5 → 6.
    # Aplicar a exceção apenas no terceiro nível fazia o gráfico de drivers
    # pular para Cloud (nível 3) em escopos onde o próximo nó deveria ser a
    # Diretoria (nível 4). Fora dessa visão, a hierarquia natural continua
    # 1 → 2 → 3 → ...
    if body.director_view and len(scope_path) < len(scope_level_order):
        driver_level = scope_level_order[len(scope_path)]
    else:
        driver_level = min(len(scope_path) + 1, 7)
    driver_key = f"organograma_nivel_{driver_level}"
    # dict.get evaluates its default eagerly. At the root (empty scope_path)
    # the old fallback tried to resolve organograma_nivel_0 even when level 1
    # existed, causing the whole executive view request to fail with HTTP 500.
    fallback_driver_key = f"organograma_nivel_{max(1, len(scope_path))}"
    actual_driver_column = actual_columns.get(driver_key) or actual_columns.get(fallback_driver_key) or actual_columns.get("organograma_nivel_1")
    forecast_driver_column = forecast_columns.get(driver_key) or forecast_columns.get(fallback_driver_key) or forecast_columns.get("organograma_nivel_1")
    if not actual_driver_column or not forecast_driver_column:
        raise HTTPException(status_code=422, detail="A tabela Gold não possui uma coluna de organograma para detalhar os drivers")
    actual_driver = _quote_ident(actual_driver_column)
    forecast_driver = _quote_ident(forecast_driver_column)
    actual_from = f"iceberg.{GOLD}.{actual_name}"
    actual_sql = f"""
      SELECT {actual_period} AS period,
             COALESCE(NULLIF(TRIM(CAST({actual_driver} AS VARCHAR)), ''), 'Sem departamento filho') AS driver,
             SUM(COALESCE(TRY_CAST({actual_value} AS DOUBLE), 0)) AS actual_value
      FROM {actual_from}
      WHERE ({actual_scope_filter}){dimension_filter}
        {actual_year_clause}
        {tipo_actual}
      GROUP BY 1, 2
    """
    forecast_sql = f"""
      SELECT {forecast_period} AS period,
             COALESCE(NULLIF(TRIM(CAST({forecast_driver} AS VARCHAR)), ''), 'Sem departamento filho') AS driver,
             SUM(COALESCE(TRY_CAST({forecast_value} AS DOUBLE), 0)) AS forecast_value
      FROM iceberg.{GOLD}.{forecast_name}
      WHERE ({forecast_scope_filter}){dimension_filter}
        {forecast_year_clause}
        AND lower(CAST({forecast_type} AS VARCHAR)) = 'forecast'
        {forecast_from}
      GROUP BY 1, 2
    """
    try:
        # These scans are independent after the cutoff query. Running them in
        # parallel removes one full Trino round-trip from the dashboard path.
        actual_fact_rows, forecast_fact_rows = await asyncio.gather(
            trino_execute(actual_sql, **interactive),
            trino_execute(forecast_sql, **interactive),
        )
    except TrinoCancelled:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Falha ao consultar a Visão Esperada: {exc}")
    by_period_facts: dict[str, dict[str, Any]] = {}
    actual_by_driver: dict[str, float] = {}
    forecast_by_driver: dict[str, float] = {}
    shared_by_driver: dict[str, float] = {}
    for row in actual_fact_rows:
        period = str(row.get("period") or "")
        driver = str(row.get("driver") or "Sem departamento filho")
        value = float(row.get("actual_value") or 0)
        current = by_period_facts.setdefault(period, {"period": period, "actual_value": 0.0, "forecast_value": None})
        current["actual_value"] = float(current["actual_value"] or 0) + value
        actual_by_driver[driver] = actual_by_driver.get(driver, 0.0) + value
    for row in forecast_fact_rows:
        period = str(row.get("period") or "")
        driver = str(row.get("driver") or "Sem departamento filho")
        value = float(row.get("forecast_value") or 0)
        current = by_period_facts.setdefault(period, {"period": period, "actual_value": None, "forecast_value": 0.0})
        current["forecast_value"] = float(current.get("forecast_value") or 0) + value
    # A ancora aberta vem do proprio resultado do motor (realizado + forecast
    # na mesma competência). Ela pode ser setembro mesmo quando hoje já é outubro.
    open_forecast_period = max(
        (
            period for period, fact in by_period_facts.items()
            if fact.get("actual_value") is not None
            and fact.get("forecast_value") is not None
        ),
        default=None,
    )
    # Meses fechados mantêm forecast histórico para consulta, mas só a âncora
    # aberta e meses futuros entram na projeção dos drivers.
    for row in forecast_fact_rows:
        period = str(row.get("period") or "")
        if period == open_forecast_period or by_period_facts[period].get("actual_value") is None:
            driver = str(row.get("driver") or "Sem departamento filho")
            value = float(row.get("forecast_value") or 0)
            forecast_by_driver[driver] = forecast_by_driver.get(driver, 0.0) + value
    if body.include_shared_cost:
        medida_col = forecast_columns.get("rateio_medida")
        medida_sql = f", lower(CAST({_quote_ident(medida_col)} AS VARCHAR)) AS rateio_medida" if medida_col else ""
        rateio_sql = f"""
          SELECT {forecast_period} AS period,
                 COALESCE(NULLIF(TRIM(CAST({forecast_driver} AS VARCHAR)), ''), 'Sem departamento filho') AS driver
                 {medida_sql},
                 SUM(COALESCE(TRY_CAST({forecast_value} AS DOUBLE), 0)) AS value
          FROM iceberg.{GOLD}.{forecast_name}
          WHERE ({forecast_scope_filter}){dimension_filter}
            {forecast_year_clause}
            AND lower(CAST({forecast_type} AS VARCHAR)) = '{RATEIO_TIPO}'
          GROUP BY 1, 2{', 3' if medida_col else ''}
        """
        try:
            rateio_rows = await trino_execute(rateio_sql, **interactive)
        except TrinoCancelled:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Falha ao consultar o rateio de custo compartilhado: {exc}")
        actual_rateio: list[dict[str, Any]] = []
        forecast_rateio: list[dict[str, Any]] = []
        closed_periods = {
            period for period, fact in by_period_facts.items()
            if fact.get("actual_value") is not None
        }
        for row in rateio_rows:
            period = str(row.get("period") or "")
            medida = str(row.get("rateio_medida") or "").lower()
            fact = {"period": row.get("period"), "driver": row.get("driver"), "value": row.get("value")}
            # A medida calculada no rateio é a fonte da classificação. Um
            # projeto destinatário pode não ter consumo próprio no mês e ainda
            # assim receber rateio realizado.
            if medida == RATEIO_MEDIDA_ACTUAL:
                actual_rateio.append(fact)
            elif medida == RATEIO_MEDIDA_FORECAST:
                forecast_rateio.append(fact)
            elif period in closed_periods:
                actual_rateio.append(fact)
            else:
                forecast_rateio.append(fact)
        apply_rateio_facts(actual_rateio, by_period_facts, shared_by_driver, "shared_actual_value")
        apply_rateio_facts(forecast_rateio, by_period_facts, shared_by_driver, "shared_forecast_value")
    fact_rows = [by_period_facts[period] for period in sorted(by_period_facts)]
    budget_year = body.year if body.year > 0 else datetime.now().year
    if body.month_from and body.month_to:
        budget_years = list(range(int(body.month_from[:4]), int(body.month_to[:4]) + 1))
    elif body.month_from:
        budget_years = [int(body.month_from[:4])]
    elif body.month_to:
        budget_years = [int(body.month_to[:4])]
    else:
        budget_years = [budget_year]
    budget_rows = (await db.execute(
        select(PlanningEntry, PlanningVersion.year).join(PlanningVersion).join(FinancialType).where(
            FinancialType.code == "orcamento",
            PlanningVersion.year.in_(budget_years),
            PlanningVersion.is_current == True,
            PlanningEntry.entity_type == "department",
        )
    )).all() if not body.dimension_filters else []
    budget_by_period: dict[str, float] = {}
    budget_by_driver: dict[str, float] = {}
    children_by_parent: dict[int | None, list[Department]] = {}
    for department in departments:
        children_by_parent.setdefault(department.parent_id, []).append(department)
    selected_ids: set[int] = set()
    if selected_departments:
        pending = [department.id for department in selected_departments]
        while pending:
            current_id = pending.pop()
            if current_id in selected_ids:
                continue
            selected_ids.add(current_id)
            pending.extend(child.id for child in children_by_parent.get(current_id, []))

    def planning_scope_match(levels: list[str] | None) -> bool:
        if not levels:
            return False
        return all(
            level <= len(levels) and str(levels[level - 1]).strip() == name.strip()
            for level, name in zip(scope_level_order, scope_path)
        )

    def planning_driver(path: list[str]) -> str:
        if body.director_view and len(scope_path) < len(scope_level_order):
            next_level = scope_level_order[len(scope_path)]
            if next_level <= len(path):
                return str(path[next_level - 1])
        return str(path[len(scope_path)] if len(path) > len(scope_path) else (path[-1] if path else "Sem departamento filho"))

    for entry, entry_year in budget_rows:
        legacy_match = planning_scope_match(entry.department_levels)
        if (selected_departments and entry.department_id in selected_ids) or (not selected_departments and legacy_match):
            period = f"{entry_year:04d}{entry.month:02d}"
            if (body.month_from and period < body.month_from) or (body.month_to and period > body.month_to):
                continue
            budget_by_period[period] = budget_by_period.get(period, 0.0) + float(entry.value or 0)
            if selected_departments and entry.department_id:
                path = []
                cursor = department_map.get(entry.department_id)
                while cursor:
                    path.append(cursor.name)
                    cursor = department_map.get(cursor.parent_id) if cursor.parent_id else None
                path.reverse()
            else:
                path = entry.department_levels or []
            driver = planning_driver(path)
            budget_by_driver[driver] = budget_by_driver.get(driver, 0.0) + float(entry.value or 0)

    # Forecast manual is composed on top of the realized/forecast series using
    # the exact same aggregation rules as the orçamento (per department and its
    # descendants). Only future months surface here: periods that already have
    # realized data in Gold drop their manual forecast.
    forecast_manual_rows = (await db.execute(
        select(PlanningEntry, PlanningVersion.year).join(PlanningVersion).join(FinancialType).where(
            FinancialType.code == "forecast-manual",
            PlanningVersion.year.in_(budget_years),
            PlanningVersion.is_current == True,
            PlanningEntry.entity_type == "department",
        )
    )).all() if not body.dimension_filters else []
    realized_periods = await _fetch_realized_periods({})
    forecast_manual_by_period: dict[str, float] = {}
    forecast_manual_by_driver: dict[str, float] = {}
    for entry, entry_year in forecast_manual_rows:
        legacy_match = planning_scope_match(entry.department_levels)
        if (selected_departments and entry.department_id in selected_ids) or (not selected_departments and legacy_match):
            period = f"{entry_year:04d}{entry.month:02d}"
            if (body.month_from and period < body.month_from) or (body.month_to and period > body.month_to):
                continue
            if period in realized_periods:
                continue
            value = float(entry.value or 0)
            if abs(value) < 1e-12:
                continue
            forecast_manual_by_period[period] = forecast_manual_by_period.get(period, 0.0) + value
            if selected_departments and entry.department_id:
                path = []
                cursor = department_map.get(entry.department_id)
                while cursor:
                    path.append(cursor.name)
                    cursor = department_map.get(cursor.parent_id) if cursor.parent_id else None
                path.reverse()
            else:
                path = entry.department_levels or []
            driver = planning_driver(path)
            forecast_manual_by_driver[driver] = forecast_manual_by_driver.get(driver, 0.0) + value
    by_period = {str(row["period"]): dict(row) for row in fact_rows}
    for period in budget_by_period:
        by_period.setdefault(period, {"period": period, "actual_value": None, "forecast_value": None})
    # Forecast manual may exist for months without any Gold forecast (e.g. the
    # tail of a year beyond the planning horizon) — surface those periods too.
    for period in forecast_manual_by_period:
        by_period.setdefault(period, {"period": period, "actual_value": None, "forecast_value": None})
    rows: list[dict[str, Any]] = []
    cumulative = 0.0
    for period in sorted(by_period):
        row = by_period[period]
        actual = row.get("actual_value")
        forecast = row.get("forecast_value")
        shared = float(row.get("shared_value") or 0)
        received = float(row.get("shared_received") or 0)
        given = float(row.get("shared_given") or 0)
        budget = budget_by_period.get(period, 0.0)
        forecast_manual = forecast_manual_by_period.get(period, 0.0)
        # Realizado from Gold wins for closed/collected months; forecast fills
        # open and future months from the planning table. Shared cost is a
        # separate stack on top of that base, not mixed into Gold. Manual
        # forecast composes on top of the future forecast as its own layer.
        base = actual if actual is not None else (forecast if forecast is not None else budget)
        # Open month: the planning forecast is the remaining-days projection
        # and never overlaps partial realized. Its period is determined by the
        # forecast cutoff, not the server calendar.
        if actual is not None and forecast is not None and period == open_forecast_period:
            base = float(base) + float(forecast)
        expected = float(base or 0) + forecast_manual + shared
        cumulative += expected
        rows.append({
            "period": period,
            "actual_value": actual,
            "forecast_value": forecast,
            "forecast_manual_value": forecast_manual if abs(forecast_manual) >= 1e-12 else 0.0,
            "shared_value": shared if abs(shared) >= 1e-12 else 0.0,
            "shared_received": received if abs(received) >= 1e-12 else 0.0,
            "shared_given": given if abs(given) >= 1e-12 else 0.0,
            "shared_actual_received": float(row.get("shared_actual_received") or 0.0),
            "shared_actual_given": float(row.get("shared_actual_given") or 0.0),
            "shared_forecast_received": float(row.get("shared_forecast_received") or 0.0),
            "shared_forecast_given": float(row.get("shared_forecast_given") or 0.0),
            "budget_value": budget,
            "expected_value": expected,
            "cumulative_value": cumulative,
        })
    composed_forecast_by_driver = {
        name: actual_by_driver.get(name, 0.0) + forecast_by_driver.get(name, 0.0)
        + forecast_manual_by_driver.get(name, 0.0) + shared_by_driver.get(name, 0.0)
        for name in set(actual_by_driver) | set(forecast_by_driver) | set(forecast_manual_by_driver) | set(shared_by_driver)
    }
    drivers = [
        {"name": name, "budget_value": budget_by_driver.get(name, 0.0),
         "actual_value": actual_by_driver.get(name, 0.0),
         "shared_value": shared_by_driver.get(name, 0.0),
         "forecast_manual_value": forecast_manual_by_driver.get(name, 0.0),
         "forecast_value": composed_forecast_by_driver.get(name, 0.0),
         "variance": budget_by_driver.get(name, 0.0) - composed_forecast_by_driver.get(name, 0.0)}
        for name in sorted(set(budget_by_driver) | set(composed_forecast_by_driver))
    ]
    return {"director": " > ".join(scope_path), "department_id": selected.id if selected else None, "budget_available": not bool(body.dimension_filters), "rows": rows, "drivers": drivers,
            "period_column": "period", "value_column": "cumulative_value"}


@router.get("/tables")
async def get_tables():
    """List all Iceberg tables across bronze, silver, gold and gold layers."""
    result: dict[str, list[str]] = {}
    for layer in ("bronze", "silver", "gold"):
        try:
            prune_stale_catalog_entries(layer)
            # Temporary publication tables are implementation details, not
            # datasets. Only hide the exact generated *_stg_<time_ns> shape.
            tables = [name for name in list_tables(layer) if _staging_table_parts(str(name)) is None]
            if layer == "gold":
                tables = [name for name in tables if name.startswith("dest_dm_")]
            result[layer] = tables
        except Exception as e:
            result[layer] = [f"error: {e}"]
    return result


@router.get("/tables/{layer}/{table_name}/partitions")
async def get_table_partitions(layer: str, table_name: str, db: AsyncSession = Depends(get_db)):
    """Return partition metadata for one catalog table (loaded on demand)."""
    if layer not in {"bronze", "silver", "gold"} or not re.fullmatch(r"[a-zA-Z0-9_]+", table_name):
        raise HTTPException(status_code=422, detail="Camada ou nome de tabela inválido.")
    cache_key = _partition_meta_cache_key(layer, table_name)
    cached = _PARTITION_META_CACHE.get(cache_key)
    if cached and (time.monotonic() - cached[0]) < _PARTITION_META_CACHE_TTL_S:
        return cached[1]
    prune_stale_catalog_entries(layer)
    if table_name not in set(list_tables(layer)):
        raise HTTPException(status_code=404, detail="Tabela não encontrada no catálogo.")
    qualified = f'iceberg.{layer}."{table_name}$partitions"'
    catalog_table = None
    labels = await _connection_labels(db)
    try:
        catalog_table = get_catalog().load_table((layer, table_name))
        if not catalog_table.spec().fields:
            # The logical monthly period for these slices is encoded in the
            # physical name; asking Trino for $partitions only adds a failing
            # round trip and made the UI appear to hang.
            frame = await asyncio.to_thread(lambda: catalog_table.inspect.partitions().to_pandas())
            payload = {
                "layer": layer, "table_name": table_name, "partitioned": False,
                "partitions": [], "record_count": int(frame["record_count"].sum()) if "record_count" in frame else 0,
                "file_count": int(frame["file_count"].sum()) if "file_count" in frame else 0,
                "source": "pyiceberg",
            }
            _PARTITION_META_CACHE[cache_key] = (time.monotonic(), payload)
            return payload
        try:
            frame = await asyncio.to_thread(lambda: catalog_table.inspect.partitions().to_pandas())
            payload = {
                "layer": layer,
                "table_name": table_name,
                "partitioned": True,
                "partitions": _partitions_from_inspect_frame(frame, labels),
                "source": "pyiceberg",
            }
            _PARTITION_META_CACHE[cache_key] = (time.monotonic(), payload)
            return payload
        except Exception as inspect_error:
            logger.debug(
                "PyIceberg partition inspect failed for %s.%s, trying Trino: %s",
                layer,
                table_name,
                inspect_error,
            )
    except Exception as metadata_error:
        logger.debug("Could not inspect Iceberg spec for %s.%s before Trino: %s", layer, table_name, metadata_error)
    try:
        rows = await trino_execute(
            f"SELECT partition, record_count, file_count FROM {qualified}",
            timeout_s=30, max_retries=1,
        )
        partitions = []
        for row in rows:
            partitions.append(_partition_payload(
                row.get("partition"),
                record_count=int(row.get("record_count") or 0),
                file_count=int(row.get("file_count") or 0),
                labels=labels,
            ))
        partitions.sort(key=lambda item: (str(item.get("period") or ""), item.get("source_connection_id") or 0))
        payload = {
            "layer": layer,
            "table_name": table_name,
            "partitioned": True,
            "partitions": partitions,
            "source": "trino",
        }
        _PARTITION_META_CACHE[cache_key] = (time.monotonic(), payload)
        return payload
    except Exception as trino_error:
        logger.debug("Trino partition metadata unavailable for %s.%s: %s", layer, table_name, trino_error)
        try:
            table = catalog_table or get_catalog().load_table((layer, table_name))
            frame = await asyncio.to_thread(lambda: table.inspect.partitions().to_pandas())
            if not table.spec().fields:
                payload = {
                    "layer": layer, "table_name": table_name, "partitioned": False,
                    "partitions": [], "record_count": int(frame["record_count"].sum()) if "record_count" in frame else 0,
                    "file_count": int(frame["file_count"].sum()) if "file_count" in frame else 0,
                    "source": "pyiceberg",
                }
                _PARTITION_META_CACHE[cache_key] = (time.monotonic(), payload)
                return payload
            logger.warning("Using PyIceberg partition fallback for %s.%s: %s", layer, table_name, trino_error)
            payload = {
                "layer": layer,
                "table_name": table_name,
                "partitioned": True,
                "partitions": _partitions_from_inspect_frame(frame, labels),
                "source": "pyiceberg",
            }
            _PARTITION_META_CACHE[cache_key] = (time.monotonic(), payload)
            return payload
        except Exception as fallback_error:
            logger.warning(
                "Partition metadata unavailable for %s.%s; pruning catalog entry: %s",
                layer,
                table_name,
                fallback_error,
            )
            remove_catalog_table(layer, table_name)
            raise HTTPException(
                status_code=404,
                detail="Tabela removida do catálogo (dados físicos indisponíveis).",
            ) from fallback_error


@router.get("/staging-tables")
async def get_staging_tables(db: AsyncSession = Depends(get_db)):
    """List generated Gold staging tables and protect active reprocesses."""
    prune_stale_catalog_entries(layers.detail_layer())
    catalog = get_catalog()
    rows: list[dict[str, Any]] = []
    for table_name in sorted(list_tables(layers.detail_layer())):
        parts = _staging_table_parts(str(table_name))
        if not parts:
            continue
        base_name, timestamp_ns = parts
        active = await _staging_base_has_active_reprocess(db, base_name)
        row_count: int | None = None
        try:
            table = catalog.load_table((layers.detail_layer(), table_name))
            row_count = sum(task.file.record_count for task in table.scan().plan_files())
        except Exception as exc:
            logger.warning("Could not inspect staging table gold.%s: %s", table_name, exc)
        rows.append({
            "table_name": table_name,
            "base_table": base_name,
            "created_at": datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=timezone.utc).isoformat(),
            "row_count": row_count,
            "protected": active,
            "status": "ACTIVE_REPROCESS" if active else "ORPHANED",
        })
    return {"tables": rows}


@router.delete("/staging-tables/{table_name}")
async def delete_staging_table(table_name: str, db: AsyncSession = Depends(get_db)):
    """Drop only a system-generated, inactive Silver staging table.

    The catalog can change between listing a staging table and the user's
    deletion request (for example, the scheduled orphan cleanup may win that
    race). Treat deletion as idempotent and let the catalog cleanup resolve
    both live entries and stale physical/catalog remnants.
    """
    parts = _staging_table_parts(table_name)
    if not parts:
        raise HTTPException(status_code=422, detail="Nome de tabela staging inválido")
    base_name, _ = parts
    if await _staging_base_has_active_reprocess(db, base_name):
        raise HTTPException(
            status_code=409,
            detail="A tabela pertence a um destino com reprocessamento ativo e não pode ser excluída",
        )
    try:
        # Use the Iceberg catalog path used by the list endpoint. A Trino DROP
        # can report "table not found" when its metadata view lags the catalog,
        # even though the table is visible to PyIceberg. This cleanup also
        # preserves data locations still referenced by another table.
        remove_catalog_table(layers.detail_layer(), table_name)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Falha ao excluir staging no Iceberg: {exc}")
    return {"deleted": True, "table_name": table_name}


@router.post("/query", response_model=QueryResponse)
async def run_query(req: QueryRequest, request: Request):
    """
    Execute a SQL query against Trino + Iceberg.

    Examples:
      SELECT * FROM iceberg.bronze.raw_azure_4p WHERE _billing_period = '202504' LIMIT 10
      SELECT SUM("BilledCost") AS total_billed FROM iceberg.silver.dest_modelo_focus WHERE "_billing_period" = '202504'
    """
    async with _ClientCancelScope(request) as scope:
        try:
            rows = await trino_execute(req.sql, catalog=req.catalog, **scope.trino_kwargs)
        except TrinoCancelled:
            raise _cancelled_http()
        except TrinoError as e:
            rows = _run_local_bronze_star_query(req.sql, str(e))
            if rows is not None:
                columns = list(rows[0].keys()) if rows else []
                return QueryResponse(columns=columns, rows=rows, row_count=len(rows))
            # O detalhe pode estar no catálogo PyIceberg local enquanto o Trino
            # lê pelo catálogo PostgreSQL. Suporta as agregações padrão do
            # destino pela mesma tabela Iceberg local.
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
    if not re.search(rf"from\s+iceberg\.{layers.detail_layer()}\.dest_modelo_focus\b", normalized, re.I):
        return None

    generic = re.search(r"select\s+\*.*?limit\s+(\d+)", normalized, re.I)
    if generic:
        table = get_catalog().load_table((layers.detail_layer(), "dest_modelo_focus"))
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
            rf"select\s+(.+?)\s*,\s*sum\(\s*\"?BilledCost\"?\s*\)\s+as\s+total_billed.*?count\(\s*\*\s*\).*?from\s+iceberg\.{layers.detail_layer()}\.dest_modelo_focus\s+group\s+by\s+(.+?)(?:\s+order\s+by|\s+limit|$)",
            normalized,
            re.I,
        )
        if not generic:
            return None
        fields = [f.strip().strip('"') for f in generic.group(2).split(",")]
        allowed = {"organograma", "projeto_nome", "_billing_period", "RegionName", "cloud_origem", "ServiceName"}
        if not fields or any(f not in allowed for f in fields):
            return None
        table = get_catalog().load_table((layers.detail_layer(), "dest_modelo_focus"))
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

    table = get_catalog().load_table((layers.detail_layer(), "dest_modelo_focus"))
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


def _run_local_bronze_star_query(sql: str, trino_error: str) -> list[dict[str, Any]] | None:
    """Read a small raw-Bronze preview when Trino cannot project a wide schema.

    Trino 483 can fail with ``Invalid position ... in block with 0 positions``
    when selecting every column from an evolved wide Iceberg table. The
    columns and Parquet files remain valid; PyIceberg reads them by field ID.
    """
    if "Invalid position" not in trino_error:
        return None
    match = re.fullmatch(
        r'\s*select\s+\*\s+from\s+iceberg\.bronze\.(raw_[a-z0-9_]+)\s+limit\s+(\d+)\s*;?\s*',
        sql,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    limit = int(match.group(2))
    if limit < 1 or limit > 1_000:
        return None
    table_code = match.group(1).removeprefix("raw_")
    frame = read_bronze(table_code, max_rows=limit)
    return json.loads(frame.to_json(orient="records", date_format="iso"))


# ---------------------------------------------------------------------------
# Pre-built analysis queries
# ---------------------------------------------------------------------------
@router.get("/summary")
async def get_summary(
    connection_code: str | None = Query(None),
    billing_period: str | None = Query(None),
    layer: str = Query(layers.detail_layer(), regex="^(bronze|silver|gold)$"),
):
    """
    Return a summary of available data for analysis.
    When layer='gold', queries the active destination table.
    When layer='silver' or 'bronze', returns table row counts.
    """
    if layer == layers.detail_layer():
        where = ["1=1"]
        if connection_code:
            if connection_code.isdigit():
                where.append(f'"source_connection_id" = {int(connection_code)}')
            else:
                where.append(f'"cloud_origem" = \'{connection_code}\'')
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
                COUNT(DISTINCT "organograma_nivel_1") AS num_diretorias,
                COUNT(DISTINCT "ServiceName") AS num_services
            FROM iceberg.{layers.detail_layer()}.dest_modelo_focus
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
        result = await trino_execute(sql, timeout_s=180)
        return {"summary": result}
    except TrinoError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/health")
async def health_check():
    """Check if Trino and the PostgreSQL-backed Iceberg catalog are reachable."""
    status = {"trino": "unknown", "iceberg_catalog": "unknown"}

    # Test Trino
    try:
        rows = await trino_execute("SELECT 1 AS ok")
        status["trino"] = "ok" if rows and rows[0].get("ok") == 1 else "error"
    except Exception as e:
        status["trino"] = str(e)[:120]

    # Test PostgreSQL-backed Iceberg catalog.
    try:
        cat = get_catalog()
        cat.list_namespaces()
        status["iceberg_catalog"] = "ok"
    except Exception as e:
        status["iceberg_catalog"] = str(e)[:120]

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
        table_name = f"silver.{safe_name}"
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
                f"SELECT COUNT(*) AS cnt FROM iceberg.silver.{planning_name}"
            )
            planning_rows = planning_count[0]["cnt"] if planning_count else 0
            planning_periods = await trino_execute(
                f"SELECT period, COUNT(*) AS cnt FROM iceberg.silver.{planning_name} "
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
                    "analysis_kind": "forecast",
                    "base_destination_id": int(tid),
                })
        except Exception:
            pass

        # Rateio rows share the monthly planning table with forecast. Expose
        # them separately so this pivot source stays independent of forecast.
        try:
            planning_schema = await _describe_layer_schema(planning_name, None, namespace=layers.detail_layer())
            columns = {name.lower(): name for name in planning_schema}
            period_col = columns.get("period")
            tipo_col = columns.get("tipo")
            if period_col and tipo_col:
                where = f"lower(CAST({_quote_ident(tipo_col)} AS VARCHAR)) = 'shared_cost'"
                shared_count = await trino_execute(
                    f"SELECT COUNT(*) AS cnt FROM iceberg.{layers.detail_layer()}.{planning_name} WHERE {where}"
                )
                shared_rows = int((shared_count[0] or {}).get("cnt") or 0) if shared_count else 0
                if shared_rows:
                    shared_period_rows = await trino_execute(
                        f"SELECT {_quote_ident(period_col)} AS period, COUNT(*) AS cnt "
                        f"FROM iceberg.{layers.detail_layer()}.{planning_name} WHERE {where} "
                        f"GROUP BY 1 ORDER BY 1 DESC LIMIT 20"
                    )
                    result.append({
                        "id": shared_cost_pivot_id(int(tid)),
                        "name": f"Custo compartilhado · {tname}",
                        "physical_name": planning_name,
                        "description": "Linhas de rateio de consumo (tipo shared_cost)",
                        "row_count": shared_rows,
                        "periods": [{"period": str(p["period"]), "count": p["cnt"]} for p in (shared_period_rows or [])],
                        "analysis_only": True,
                        "analysis_kind": "shared_cost",
                        "base_destination_id": int(tid),
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

    table_name = f"silver.{phys.replace('-', '_').replace('.', '_')}"
    safe_name = phys.replace("-", "_").replace(".", "_")

    # Read the record count from Iceberg metadata.  A full COUNT(*) over Gold
    # competes with dashboard queries and is unnecessary for a paginated
    # preview; ``total-records`` is updated on every atomic publication.
    total = 0
    try:
        from app.services.iceberg_service import get_catalog
        snapshot = get_catalog().load_table(table_name).current_snapshot()
        total = int((snapshot.summary or {}).get("total-records", 0)) if snapshot else 0
    except Exception:
        # Metadata can be unavailable during a catalog transition. Keep this
        # bounded so an analysis preview cannot starve the dashboard.
        try:
            cnt_result = await trino_execute(f"SELECT COUNT(*) AS cnt FROM iceberg.{table_name}", timeout_s=20)
            total = cnt_result[0]["cnt"] if cnt_result else 0
        except Exception:
            pass

    # Query data via Trino
    rows = []
    columns: list[str] = []
    try:
        trino_rows = await trino_execute(
            # Never sort the full Gold table merely to render the first page.
            # The old ORDER BY 1 forced a global sort of millions of records,
            # blocking Trino and causing unrelated dashboard requests to 503.
            f"SELECT * FROM iceberg.{table_name} OFFSET {offset} LIMIT {limit}",
            timeout_s=30,
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
