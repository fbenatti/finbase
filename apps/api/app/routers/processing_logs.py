from __future__ import annotations

from datetime import datetime
from typing import Any

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import String, cast, or_, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.processing_log import ProcessingLog
from app.services.trino_service import execute as trino_execute
from app.services.processing_logs import PROCESSING_LOG_CUBE

router = APIRouter(prefix="/processing-logs", tags=["processing-logs"])
logger = logging.getLogger(__name__)


def _serialize(row: ProcessingLog, *, include_details: bool = False) -> dict[str, Any]:
    result = {
        "id": row.id,
        "process_type": row.process_type,
        "status": row.status,
        "etl_task_id": row.etl_task_id,
        "source_kind": row.source_kind,
        "source": row.source or {},
        "destination": row.destination or {},
        "partitions": row.partitions or [],
        "parameters": row.parameters or {},
        "counts": row.counts or {},
        "phase": row.phase,
        "error": row.error,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
    }
    if include_details:
        result["details"] = row.details or {}
    return result


@router.get("")
async def list_processing_logs(
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: str | None = None,
    process_type: str | None = None,
    status: str | None = None,
    started_after: datetime | None = None,
    started_before: datetime | None = None,
    db: AsyncSession = Depends(get_db),
    # Existing consumers can adopt the shared history without rewriting their
    # data model; compatibility aliases keep the deployed UI contract stable.
    task_type: str | None = None,
    connection_id: int | None = None,
    destination_table_id: int | None = None,
):
    # The grid reads the compact serving cube. Full parameters and event history
    # remain in PostgreSQL and are fetched from the detail endpoint on demand.
    try:
        conditions = []
        if process_type := (process_type or task_type):
            conditions.append(f"process_type = '{process_type.replace(chr(39), chr(39) * 2)}'")
        if status:
            conditions.append(f"status = '{status.replace(chr(39), chr(39) * 2)}'")
        if started_after:
            conditions.append(f"started_at >= from_iso8601_timestamp('{started_after.isoformat()}')")
        if started_before:
            conditions.append(f"started_at <= from_iso8601_timestamp('{started_before.isoformat()}')")
        if connection_id is not None:
            conditions.append(f"source_label LIKE '%{int(connection_id)}%'")
        if destination_table_id is not None:
            conditions.append(f"destination_label LIKE '%{int(destination_table_id)}%'")
        if search and search.strip():
            needle = search.strip().replace("'", "''").lower()
            conditions.append(
                "(" + " OR ".join(
                    f"LOWER(COALESCE({column}, '')) LIKE '%{needle}%'"
                    for column in ("process_type", "status", "source_label", "destination_label", "partitions_label", "phase")
                ) + ")"
            )
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        total_rows = await trino_execute(
            f"SELECT COUNT(*) AS total FROM iceberg.gold.{PROCESSING_LOG_CUBE}{where}",
            # This is an interactive grid. A queued Trino query must not keep
            # the page waiting through the warehouse transport retry window;
            # fall back to the authoritative PostgreSQL log table promptly.
            timeout_s=8,
            max_retries=1,
        )
        rows = await trino_execute(
            f"SELECT id, etl_task_id, process_type, status, source_label, destination_label, partitions_label, "
            "rows_source, rows_destination, started_at, finished_at, phase "
            f"FROM iceberg.gold.{PROCESSING_LOG_CUBE}{where} "
            f"ORDER BY started_at DESC NULLS LAST, id DESC OFFSET {int(offset)} LIMIT {int(limit)}",
            timeout_s=8,
            max_retries=1,
        )
        return {
            "items": [{
                "id": int(row["id"]),
                "process_type": row.get("process_type") or "processing",
                "status": row.get("status") or "UNKNOWN",
                "etl_task_id": int(row["etl_task_id"]) if row.get("etl_task_id") is not None else None,
                "source_kind": None,
                "source": {"label": row.get("source_label") or ""},
                "destination": {"label": row.get("destination_label") or ""},
                "partitions": [row.get("partitions_label")] if row.get("partitions_label") else [],
                "parameters": {},
                "counts": {
                    "rows_source": int(row.get("rows_source") or 0),
                    "rows_destination": int(row.get("rows_destination") or 0),
                },
                "phase": row.get("phase"),
                "error": None,
                "started_at": row.get("started_at"),
                "finished_at": row.get("finished_at"),
            } for row in rows],
            "total": int((total_rows[0] if total_rows else {}).get("total") or 0),
            "source": "gold_cube",
        }
    except Exception:
        # Preserve availability while the first asynchronous materialization is
        # warming up or the analytics coordinator is temporarily unavailable.
        logger.exception("Falha ao consultar cubo de logs; usando fallback PostgreSQL")
        pass

    filters = []
    process_type = process_type or task_type
    if process_type:
        filters.append(ProcessingLog.process_type == process_type)
    if status:
        filters.append(ProcessingLog.status == status)
    if started_after:
        filters.append(ProcessingLog.started_at >= started_after)
    if started_before:
        filters.append(ProcessingLog.started_at <= started_before)
    if connection_id is not None:
        filters.append(ProcessingLog.source["connection_ids"].astext.contains(str(connection_id)))
    if destination_table_id is not None:
        filters.append(ProcessingLog.destination["destination_table_id"].astext == str(destination_table_id))
    if search and search.strip():
        needle = f"%{search.strip()}%"
        filters.append(or_(
            ProcessingLog.process_type.ilike(needle),
            ProcessingLog.status.ilike(needle),
            ProcessingLog.phase.ilike(needle),
            ProcessingLog.error.ilike(needle),
            cast(ProcessingLog.source, String).ilike(needle),
            cast(ProcessingLog.destination, String).ilike(needle),
        ))
    filtered = select(ProcessingLog).where(*filters)
    total = (await db.execute(select(func.count()).select_from(ProcessingLog).where(*filters))).scalar_one()
    result = (await db.execute(
        filtered.order_by(ProcessingLog.started_at.desc().nullslast(), ProcessingLog.id.desc())
        .offset(offset).limit(limit)
    )).scalars().all()
    return {"items": [_serialize(row) for row in result], "total": total}


@router.get("/cube/status")
async def processing_log_cube_status():
    try:
        rows = await trino_execute(
            f"SELECT COUNT(*) AS row_count, MAX(updated_at) AS updated_at "
            f"FROM iceberg.gold.{PROCESSING_LOG_CUBE}",
            timeout_s=15,
        )
        row = rows[0] if rows else {}
        return {"available": True, "table": PROCESSING_LOG_CUBE,
                "row_count": int(row.get("row_count") or 0), "updated_at": row.get("updated_at")}
    except Exception:
        return {"available": False, "table": PROCESSING_LOG_CUBE, "row_count": 0, "updated_at": None}


async def _rebuild_processing_log_cube() -> None:
    from app.services.processing_logs import publish_processing_log_cube

    try:
        await publish_processing_log_cube(full_refresh=True)
    except Exception:
        logger.exception("Falha ao reconstruir cubo operacional de logs")


@router.post("/cube/rebuild", status_code=202)
async def rebuild_processing_log_cube(background_tasks: BackgroundTasks):
    background_tasks.add_task(_rebuild_processing_log_cube)
    return {"status": "QUEUED", "table": PROCESSING_LOG_CUBE,
            "message": "Reconstrução do cubo operacional enfileirada."}


@router.get("/{log_id}")
async def get_processing_log(log_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(ProcessingLog, log_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Log de processamento não encontrado")
    return _serialize(row, include_details=True)
