from __future__ import annotations

from datetime import datetime
from typing import Any

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import String, cast, or_, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.etl_task import EtlTask
from app.models.processing_log import ProcessingLog
from app.services.trino_service import execute as trino_execute
from app.services.processing_logs import PROCESSING_LOG_CUBE, async_sync_etl_task_log

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
    # The operational log is authoritative in PostgreSQL. Use it directly for
    # this small, paginated interactive grid: Trino can queue analytical work
    # behind warehouse queries and otherwise delay even the first page. The
    # compact Gold cube remains available for analytics, while event details
    # are fetched from PostgreSQL only when a row is opened.
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
        filters.append(cast(ProcessingLog.source, String).ilike(f"%{int(connection_id)}%"))
    if destination_table_id is not None:
        filters.append(ProcessingLog.destination["destination_table_id"].as_string() == str(destination_table_id))
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
    task_ids = [row.etl_task_id for row in result if row.etl_task_id is not None]
    if task_ids:
        tasks = (await db.execute(select(EtlTask).where(EtlTask.id.in_(task_ids)))).scalars().all()
        tasks_by_id = {task.id: task for task in tasks}
        reconciled = False
        for log in result:
            task = tasks_by_id.get(log.etl_task_id)
            if task and (log.status != task.status or log.finished_at != task.finished_at):
                await async_sync_etl_task_log(db, task)
                reconciled = True
        if reconciled:
            await db.commit()
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
    if row.etl_task_id is not None:
        task = await db.get(EtlTask, row.etl_task_id)
        if task and (row.status != task.status or row.finished_at != task.finished_at):
            await async_sync_etl_task_log(db, task)
            await db.commit()
    return _serialize(row, include_details=True)
