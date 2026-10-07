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
    _enrich_task_context(session, source, destination)
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
    await _async_enrich_task_context(session, source, destination)
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


def _enrich_task_context(session: Session, source: dict[str, Any], destination: dict[str, Any]) -> None:
    from app.models.destination import DestinationTable
    from app.models.source_connection import SourceConnection

    ids = [int(value) for value in source.get("connection_ids", []) if str(value).isdigit()]
    if ids:
        connections = session.execute(select(SourceConnection).where(SourceConnection.id.in_(ids))).scalars().all()
        source["connection_names"] = [item.name for item in connections]
        source["connection_codes"] = [item.code for item in connections]
    destination_id = destination.get("destination_table_id")
    if destination_id:
        target = session.get(DestinationTable, int(destination_id))
        if target:
            destination["destination_name"] = target.name
            destination["physical_name"] = target.physical_name


async def _async_enrich_task_context(session: AsyncSession, source: dict[str, Any], destination: dict[str, Any]) -> None:
    from app.models.destination import DestinationTable
    from app.models.source_connection import SourceConnection

    ids = [int(value) for value in source.get("connection_ids", []) if str(value).isdigit()]
    if ids:
        connections = (await session.execute(select(SourceConnection).where(SourceConnection.id.in_(ids)))).scalars().all()
        source["connection_names"] = [item.name for item in connections]
        source["connection_codes"] = [item.code for item in connections]
    destination_id = destination.get("destination_table_id")
    if destination_id:
        target = await session.get(DestinationTable, int(destination_id))
        if target:
            destination["destination_name"] = target.name
            destination["physical_name"] = target.physical_name


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
    count = len(rows) + backfill_forecast_runs(session)
    return count + backfill_extraction_metadata(session)


def backfill_extraction_metadata(session: Session) -> int:
    """Attach durable connection names to extraction logs where available."""
    from app.models.source_connection import SourceConnection

    logs = session.execute(select(ProcessingLog).where(ProcessingLog.process_type == "extraction")).scalars().all()
    for log in logs:
        source = dict(log.source or {})
        ids = source.get("connection_ids") or []
        if not ids:
            continue
        connections = session.execute(select(SourceConnection).where(SourceConnection.id.in_(ids))).scalars().all()
        source["connection_names"] = [item.name for item in connections]
        log.source = source
    return len(logs)


def backfill_forecast_runs(session: Session) -> int:
    """Expose forecast-only runs alongside their optional worker task row."""
    from app.models.forecast import ForecastRun

    runs = session.execute(select(ForecastRun).order_by(ForecastRun.id)).scalars().all()
    for run in runs:
        if run.source_etl_task_id:
            # The linked task is the shared execution record; it carries the
            # stable task identity and the run is linked in the detail payload.
            task_log = session.execute(select(ProcessingLog).where(
                ProcessingLog.etl_task_id == run.source_etl_task_id
            )).scalar_one_or_none()
            if task_log:
                details = dict(task_log.details or {})
                details["forecast_run"] = {
                    "id": run.id, "config_snapshot": run.config_snapshot or {},
                    "result_table": run.result_table, "cutoff_date": run.cutoff_date,
                    "source_snapshot_id": run.source_snapshot_id,
                }
                task_log.details = details
                continue
        log = session.execute(select(ProcessingLog).where(
            ProcessingLog.etl_task_id.is_(None), ProcessingLog.process_type == "forecast",
            ProcessingLog.details["forecast_run_id"].astext == str(run.id)
        )).scalar_one_or_none()
        if log is None:
            log = ProcessingLog(process_type="forecast", details={"forecast_run_id": run.id})
            session.add(log)
        log.process_type = "forecast"
        log.status = run.status
        log.source_kind = "gold"
        log.source = {"kind": "gold", "snapshot_id": run.source_snapshot_id}
        log.destination = {"table": run.result_table, "physical_name": run.destination_physical_name}
        log.partitions = [run.cutoff_date] if run.cutoff_date else []
        log.parameters = run.config_snapshot or {}
        log.counts = {"items_total": run.total_series or 0, "items_processed": run.processed_series or 0, "rows_destination": run.rows_written or 0}
        log.phase = run.phase
        log.error = run.error
        log.details = {"forecast_run_id": run.id, "config_snapshot": run.config_snapshot or {}, "error_metrics": run.error_metrics or {}, "selected_models": run.selected_models or {}}
        log.started_at = run.started_at
        log.finished_at = run.finished_at
    return len(runs)


async def async_sync_forecast_run_log(session: Any, run: Any) -> ProcessingLog:
    """Synchronize a forecast run in the same async transaction as its writer."""
    if run.source_etl_task_id:
        task_log = (await session.execute(select(ProcessingLog).where(
            ProcessingLog.etl_task_id == run.source_etl_task_id
        ))).scalar_one_or_none()
        if task_log:
            details = dict(task_log.details or {})
            details["forecast_run"] = {
                "id": run.id, "config_snapshot": run.config_snapshot or {},
                "result_table": run.result_table, "cutoff_date": run.cutoff_date,
                "source_snapshot_id": run.source_snapshot_id,
            }
            task_log.details = details
            task_log.status = run.status
            task_log.phase = run.phase
            task_log.error = run.error
            task_log.finished_at = run.finished_at
            task_log.counts = {**(task_log.counts or {}), "rows_destination": run.rows_written or 0, "items_total": run.total_series or 0, "items_processed": run.processed_series or 0}
            return task_log
    log = (await session.execute(select(ProcessingLog).where(
        ProcessingLog.etl_task_id.is_(None), ProcessingLog.process_type == "forecast",
        ProcessingLog.details["forecast_run_id"].astext == str(run.id)
    ))).scalar_one_or_none()
    if log is None:
        log = ProcessingLog(process_type="forecast", details={"forecast_run_id": run.id})
        session.add(log)
    log.process_type = "forecast"
    log.status = run.status
    log.source_kind = "gold"
    log.source = {"kind": "gold", "snapshot_id": run.source_snapshot_id}
    log.destination = {"table": run.result_table, "physical_name": run.destination_physical_name}
    log.partitions = [run.cutoff_date] if run.cutoff_date else []
    log.parameters = run.config_snapshot or {}
    log.counts = {"items_total": run.total_series or 0, "items_processed": run.processed_series or 0, "rows_destination": run.rows_written or 0}
    log.phase = run.phase
    log.error = run.error
    log.details = {"forecast_run_id": run.id, "config_snapshot": run.config_snapshot or {}, "error_metrics": run.error_metrics or {}, "selected_models": run.selected_models or {}}
    log.started_at = run.started_at
    log.finished_at = run.finished_at
    return log
