from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Integer, String, cast, or_, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.processing_log import ProcessingLog

router = APIRouter(prefix="/processing-logs", tags=["processing-logs"])


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
):
    filters = []
    if process_type:
        filters.append(ProcessingLog.process_type == process_type)
    if status:
        filters.append(ProcessingLog.status == status)
    if started_after:
        filters.append(ProcessingLog.started_at >= started_after)
    if started_before:
        filters.append(ProcessingLog.started_at <= started_before)
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
    from app.models.source_connection import SourceConnection
    query = select(ProcessingLog, SourceConnection.name).outerjoin(
        SourceConnection,
        SourceConnection.id == cast(ProcessingLog.source["connection_ids"][0].astext, Integer),
    )
    if search and search.strip():
        filters.append(SourceConnection.name.ilike(f"%{search.strip()}%"))
    filtered = query.where(*filters)
    total = (await db.execute(select(func.count()).select_from(filtered.subquery()))).scalar_one()
    result = (await db.execute(
        filtered.order_by(ProcessingLog.started_at.desc().nullslast(), ProcessingLog.id.desc())
        .offset(offset).limit(limit)
    )).all()
    rows = []
    for row, source_name in result:
        item = _serialize(row)
        source = item["source"]
        if source_name and source.get("connection_ids"):
            source["connection_names"] = [source_name]
        item["source"] = source
        rows.append(item)
    return {"items": rows, "total": total}


@router.get("/{log_id}")
async def get_processing_log(log_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(ProcessingLog, log_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Log de processamento não encontrado")
    return _serialize(row, include_details=True)
