from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, cast, or_, select, func
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
        from app.models.destination import DestinationTable
        from app.models.source_connection import SourceConnection
        filters.append(or_(
            ProcessingLog.process_type.ilike(needle),
            ProcessingLog.status.ilike(needle),
            ProcessingLog.phase.ilike(needle),
            ProcessingLog.error.ilike(needle),
            cast(ProcessingLog.source, String).ilike(needle),
            cast(ProcessingLog.destination, String).ilike(needle),
            ProcessingLog.source["connection_names"].astext.ilike(needle),
            ProcessingLog.destination["destination_name"].astext.ilike(needle),
        ))
    filtered = select(ProcessingLog).where(*filters)
    total = (await db.execute(select(func.count()).select_from(ProcessingLog).where(*filters))).scalar_one()
    result = (await db.execute(
        filtered.order_by(ProcessingLog.started_at.desc().nullslast(), ProcessingLog.id.desc())
        .offset(offset).limit(limit)
    )).scalars().all()
    from app.models.destination import DestinationTable
    from app.models.source_connection import SourceConnection
    connection_ids = sorted({int(cid) for row in result for cid in (row.source or {}).get("connection_ids", []) if str(cid).isdigit()})
    destination_ids = sorted({int(row.destination.get("destination_table_id")) for row in result if row.destination and str(row.destination.get("destination_table_id", "")).isdigit()})
    connections = (await db.execute(select(SourceConnection).where(SourceConnection.id.in_(connection_ids)))).scalars().all() if connection_ids else []
    destinations = (await db.execute(select(DestinationTable).where(DestinationTable.id.in_(destination_ids)))).scalars().all() if destination_ids else []
    connection_names = {connection.id: connection.name for connection in connections}
    destination_names = {destination.id: destination.name for destination in destinations}
    rows = []
    for row in result:
        item = _serialize(row)
        source = item["source"]
        source["connection_names"] = [connection_names.get(int(cid), f"Conexão #{cid}") for cid in source.get("connection_ids", [])]
        destination = item["destination"]
        destination_id = destination.get("destination_table_id")
        if destination_id is not None:
            destination["destination_name"] = destination_names.get(int(destination_id))
        item["source"] = source
        item["destination"] = destination
        rows.append(item)
    return {"items": rows, "total": total}


@router.get("/{log_id}")
async def get_processing_log(log_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(ProcessingLog, log_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Log de processamento não encontrado")
    return _serialize(row, include_details=True)
