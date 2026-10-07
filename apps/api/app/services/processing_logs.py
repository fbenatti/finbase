"""Shared persistence helpers for the canonical processing log."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.etl_task import EtlTask
from app.models.processing_log import ProcessingLog


def _task_type(task: EtlTask) -> str:
    return str(task.task_type or "processing")


def _task_context(task: EtlTask) -> tuple[dict[str, Any], dict[str, Any], list[Any], dict[str, Any]]:
    result = task.result_json if isinstance(task.result_json, dict) else {}
    source = result.get("source") or {}
    if not source and task.connection_ids:
        source = {"connection_ids": task.connection_ids}
    elif not source and task.connection_id:
        source = {"connection_ids": [task.connection_id]}
    destination = result.get("destination") or {}
    if not destination:
        destination = {
            key: result[key]
            for key in ("table", "planning_table", "result_table", "destination_table_id")
            if result.get(key) is not None
        }
        if task.destination_table_id is not None:
            destination.setdefault("destination_table_id", task.destination_table_id)
    partitions = result.get("partitions") or result.get("billing_periods") or task.billing_periods or []
    parameters = result.get("parameters") or {
        key: result[key]
        for key in ("rule_ids", "config_id", "months", "replace_existing", "total_months")
        if result.get(key) is not None
    }
    if task.task_type == "forecast":
        source = source or {"kind": "gold", "snapshot_id": result.get("source_snapshot_id")}
        destination = destination or {"table": result.get("result_table")}
    elif task.task_type == "forecast_accuracy":
        source = source or {"kind": "gold", "destination_table_id": task.destination_table_id}
        destination = destination or {"namespace": "forecast", "kind": "accuracy_snapshots"}
    elif task.task_type == "shared_cost":
        source = source or {"kind": "shared_cost_configuration"}
        destination = destination or {"table": result.get("planning_table") or result.get("table") or "planning"}
    elif task.task_type in ("gold_reprocess", "ai_cost_relationship_reprocess"):
        source = source or {"kind": "silver_and_gold", "rules": result.get("rule_ids")}
        destination = destination or {"namespace": "iceberg.gold", "tables": result.get("tables") or []}
    return source, destination, list(partitions or []), parameters


def sync_etl_task_log(session: Session, task: EtlTask) -> ProcessingLog:
    """Upsert a canonical snapshot from the existing ETL task row."""
    log = session.execute(
        select(ProcessingLog).where(ProcessingLog.etl_task_id == task.id)
    ).scalar_one_or_none()
    if log is None:
        log = ProcessingLog(etl_task_id=task.id)
        session.add(log)
    source, destination, partitions, parameters = _task_context(task)
    result = task.result_json if isinstance(task.result_json, dict) else {}
    errors = task.errors if isinstance(task.errors, list) else []
    log.process_type = _task_type(task)
    log.status = task.status
    log.source_kind = result.get("source_kind") or ("connection" if source else None)
    log.source = source
    log.destination = destination
    log.partitions = partitions
    log.parameters = parameters
    log.counts = {
        "rows_source": task.rows_raw or 0,
        "rows_normalized": task.rows_normalized or 0,
        "rows_destination": task.rows_destination or 0,
        "items_total": task.total_blobs or 0,
        "items_processed": task.processed_blobs or 0,
    }
    log.phase = task.phase or task.current_blob
    log.error = (result.get("error") or (errors[0] if errors else None))
    log.details = {
        "etl_task_id": task.id,
        "event_log": result.get("event_log") or [],
        "errors": errors,
        "result": result,
    }
    if task.started_at is not None:
        log.started_at = task.started_at
    log.finished_at = task.finished_at
    return log


async def async_sync_etl_task_log(session: AsyncSession, task: EtlTask) -> ProcessingLog:
    """AsyncSession counterpart used by queueing/API endpoints."""
    log = (await session.execute(
        select(ProcessingLog).where(ProcessingLog.etl_task_id == task.id)
    )).scalar_one_or_none()
    if log is None:
        log = ProcessingLog(etl_task_id=task.id)
        session.add(log)
    source, destination, partitions, parameters = _task_context(task)
    result = task.result_json if isinstance(task.result_json, dict) else {}
    errors = task.errors if isinstance(task.errors, list) else []
    log.process_type = _task_type(task)
    log.status = task.status
    log.source_kind = result.get("source_kind") or ("connection" if source else None)
    log.source = source
    log.destination = destination
    log.partitions = partitions
    log.parameters = parameters
    log.counts = {
        "rows_source": task.rows_raw or 0,
        "rows_normalized": task.rows_normalized or 0,
        "rows_destination": task.rows_destination or 0,
        "items_total": task.total_blobs or 0,
        "items_processed": task.processed_blobs or 0,
    }
    log.phase = task.phase or task.current_blob
    log.error = result.get("error") or (errors[0] if errors else None)
    log.details = {"etl_task_id": task.id, "event_log": result.get("event_log") or [], "errors": errors, "result": result}
    if task.started_at is not None:
        log.started_at = task.started_at
    log.finished_at = task.finished_at
    return log


def record_processing_event(
    session: Session,
    task_id: int,
    *,
    level: str,
    message: str,
    details: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> None:
    """Append one event to the ETL task and canonical log in the same transaction."""
    task = session.get(EtlTask, task_id)
    if task is None:
        return
    result = dict(task.result_json or {})
    events = list(result.get("event_log") or [])
    event = {"at": (at or datetime.now(timezone.utc)).isoformat(), "level": level, "message": message}
    if details:
        event["details"] = details
    events.append(event)
    result["event_log"] = events[-500:]
    task.result_json = result
    sync_etl_task_log(session, task)


def backfill_processing_logs(session: Session, *, limit: int | None = None) -> int:
    """Idempotently register legacy task rows so existing history is queryable."""
    query = select(EtlTask).order_by(EtlTask.id)
    if limit:
        query = query.limit(limit)
    rows = session.execute(query).scalars().all()
    for task in rows:
        sync_etl_task_log(session, task)
    return len(rows)
