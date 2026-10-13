"""Shared persistence helpers for the canonical processing log."""

import logging
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import httpx
import pandas as pd
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.etl_task import EtlTask
from app.models.processing_log import ProcessingLog


PROCESSING_LOG_CUBE = "dest_dm_processing_logs"
_SNAPSHOT_MAINTENANCE_INTERVAL_SECONDS = 15 * 60
_last_snapshot_maintenance_attempt = 0.0
_MAX_PROCESSING_LOG_SNAPSHOTS = 500
_MAX_PROCESSING_LOG_DELETE_FILES = 10_000
logger = logging.getLogger(__name__)


def _is_missing_processing_log_cube_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in (
        "table_not_found", "table not found", "does not exist", "not found in catalog",
    ))


async def _maintain_processing_log_cube_snapshots() -> bool:
    """Keep only the latest hour of snapshot history for the derived log cube."""
    from app.services.iceberg_service import _get_catalog
    from app.services.trino_service import TRINO_URL, TRINO_USER

    table = _get_catalog().load_table(("gold", PROCESSING_LOG_CUBE))
    if table.current_snapshot() is None or "_stg_" in str(table.location()):
        return False

    retention = "1h"
    headers = {
        "X-Trino-User": TRINO_USER,
        "X-Trino-Catalog": "iceberg",
        "X-Trino-Schema": "gold",
        "X-Trino-Session": (
            f"iceberg.expire_snapshots_min_retention={retention},"
            f"iceberg.remove_orphan_files_min_retention={retention}"
        ),
    }
    statements = (
        f"ALTER TABLE iceberg.gold.{PROCESSING_LOG_CUBE} SET PROPERTIES "
        "delete_after_commit_enabled = true, max_previous_versions = 10",
        f"ALTER TABLE iceberg.gold.{PROCESSING_LOG_CUBE} EXECUTE optimize",
        f"ALTER TABLE iceberg.gold.{PROCESSING_LOG_CUBE} "
        f"EXECUTE expire_snapshots(retention_threshold => '{retention}')",
        f"ALTER TABLE iceberg.gold.{PROCESSING_LOG_CUBE} "
        f"EXECUTE remove_orphan_files(retention_threshold => '{retention}')",
    )
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
        for sql in statements:
            response = await client.post(
                urljoin(TRINO_URL, "/v1/statement"), content=sql, headers=headers,
            )
            if response.status_code != 200:
                raise RuntimeError(f"Trino HTTP {response.status_code}: {response.text[:300]}")
            data = response.json()
            while True:
                if "error" in data:
                    raise RuntimeError(data["error"].get("message", "Trino query failed"))
                state = data.get("stats", {}).get("state")
                if state in ("FAILED", "CANCELED"):
                    raise RuntimeError(f"Trino maintenance state={state}")
                next_uri = data.get("nextUri")
                if not next_uri:
                    break
                response = await client.get(next_uri, headers=headers)
                response.raise_for_status()
                data = response.json()
    return True


async def _maybe_maintain_processing_log_cube_snapshots() -> None:
    global _last_snapshot_maintenance_attempt
    now = time.monotonic()
    if now - _last_snapshot_maintenance_attempt < _SNAPSHOT_MAINTENANCE_INTERVAL_SECONDS:
        return
    # Avoid launching overlapping maintenance attempts when a large orphan scan
    # takes longer than one publisher interval.
    _last_snapshot_maintenance_attempt = now
    try:
        maintained = await _maintain_processing_log_cube_snapshots()
        if not maintained:
            logger.warning("A manutenção de snapshots do cubo de logs não foi concluída")
    except Exception:
        logger.warning("Falha ao expirar snapshots do cubo de logs", exc_info=True)


def _processing_log_cube_needs_rebuild(expected_rows: int) -> bool:
    """Detect snapshot/delete-file churn and compact the small serving table."""
    from app.services.iceberg_service import _get_catalog

    try:
        table = _get_catalog().load_table(("gold", PROCESSING_LOG_CUBE))
        snapshot = table.current_snapshot()
        if snapshot is None:
            return False
        summary = snapshot.summary.additional_properties
        current_rows = int(summary.get("total-records") or 0)
        delete_files = int(summary.get("total-delete-files") or 0)
        return (
            len(table.snapshots()) > _MAX_PROCESSING_LOG_SNAPSHOTS
            or delete_files > _MAX_PROCESSING_LOG_DELETE_FILES
            or current_rows > max(expected_rows * 3, expected_rows + 10_000)
        )
    except Exception:
        return False


def _trino_literal(value: Any, type_name: str) -> str:
    if value is None:
        return f"CAST(NULL AS {type_name})"
    if isinstance(value, datetime):
        escaped = value.isoformat().replace("'", "''")
        return f"CAST(from_iso8601_timestamp('{escaped}') AS TIMESTAMP(6) WITH TIME ZONE)"
    escaped = str(value).replace("'", "''")
    return f"CAST('{escaped}' AS {type_name})"


async def _merge_processing_log_batch(rows: list[dict[str, Any]], *, create: bool) -> None:
    from app.services.trino_service import execute

    columns = [
        ("id", "BIGINT"), ("etl_task_id", "BIGINT"),
        ("process_type", "VARCHAR"), ("status", "VARCHAR"),
        ("source_label", "VARCHAR"), ("destination_label", "VARCHAR"),
        ("partitions_label", "VARCHAR"), ("rows_source", "BIGINT"),
        ("rows_destination", "BIGINT"), ("started_at", "TIMESTAMP(6) WITH TIME ZONE"),
        ("finished_at", "TIMESTAMP(6) WITH TIME ZONE"), ("phase", "VARCHAR"),
        ("updated_at", "TIMESTAMP(6) WITH TIME ZONE"),
    ]
    value_rows = ["(" + ", ".join(
        _trino_literal(row[column], data_type) for column, data_type in columns
    ) + ")" for row in rows]
    names = ", ".join(name for name, _ in columns)
    source = f"(VALUES {', '.join(value_rows)}) AS incoming ({names})"
    if create:
        await execute(
            f"CREATE TABLE iceberg.gold.{PROCESSING_LOG_CUBE} AS SELECT {names} FROM {source}",
            timeout_s=180,
        )
        return
    assignments = ", ".join(f"{name} = incoming.{name}" for name, _ in columns if name != "id")
    insert_values = ", ".join(f"incoming.{name}" for name, _ in columns)
    changed_values = " OR ".join(
        f"target.{name} IS DISTINCT FROM incoming.{name}"
        for name, _ in columns if name != "id"
    )
    await execute(
        f"MERGE INTO iceberg.gold.{PROCESSING_LOG_CUBE} AS target USING {source} "
        "ON target.id = incoming.id "
        f"WHEN MATCHED AND ({changed_values}) THEN UPDATE SET {assignments} "
        f"WHEN NOT MATCHED THEN INSERT ({names}) VALUES ({insert_values})",
        timeout_s=180,
    )


async def publish_processing_log_cube(*, full_refresh: bool = False) -> dict[str, int]:
    """Replicate canonical operational logs to Gold without reading Silver.

    PostgreSQL remains authoritative. The Iceberg table is a compact serving
    cube for the paginated log grid; details stay in PostgreSQL and are fetched
    only when a user opens a row.
    """
    from app.database import SyncSession
    from app.services import iceberg_service as iceberg
    from app.services.trino_service import execute

    session = SyncSession()
    lock_acquired = False
    try:
        lock_acquired = bool(session.execute(text("SELECT pg_try_advisory_lock(734219405)")).scalar())
        if not lock_acquired:
            return {"rows_written": 0, "checkpoint": 0}
        expected_rows = int(session.scalar(select(func.count()).select_from(ProcessingLog)) or 0)
        needs_rebuild = _processing_log_cube_needs_rebuild(expected_rows)
        if needs_rebuild:
            logger.warning("Recriando cubo de logs após detectar excesso de snapshots/arquivos de delete")
            full_refresh = True
        else:
            # Run maintenance before any Trino read of the target. The old
            # order made maintenance unreachable when checkpoint reads timed
            # out on a delete-file-heavy table.
            await _maybe_maintain_processing_log_cube_snapshots()
        checkpoint_time: datetime | None = None
        checkpoint_id = 0
        if not full_refresh:
            try:
                rows = await execute(
                    f"SELECT updated_at AS checkpoint_time, id AS checkpoint_id "
                    f"FROM iceberg.gold.{PROCESSING_LOG_CUBE} "
                    "ORDER BY updated_at DESC, id DESC LIMIT 1",
                    timeout_s=30,
                )
                row = rows[0] if rows else {}
                checkpoint_id = int(row.get("checkpoint_id") or 0)
                value = row.get("checkpoint_time")
                checkpoint_time = datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
            except Exception as exc:
                if _is_missing_processing_log_cube_error(exc):
                    full_refresh = True
                else:
                    logger.warning(
                        "Falha transitória ao ler checkpoint do cubo de logs; "
                        "pulando este ciclo sem rebuild completo: %s",
                        exc,
                    )
                    return {"rows_written": 0, "checkpoint": checkpoint_id}

        query = select(ProcessingLog).order_by(ProcessingLog.updated_at, ProcessingLog.id)
        if checkpoint_time and not full_refresh:
            # Include the checkpoint timestamp to capture rows sharing the same
            # database precision; MERGE makes replaying that boundary idempotent.
            query = query.where(ProcessingLog.updated_at >= checkpoint_time)
        logs = session.execute(query).scalars().all()

        if not logs and not full_refresh:
            return {"rows_written": 0, "checkpoint": checkpoint_id}

        table_exists = False
        try:
            await execute(f"DESCRIBE iceberg.gold.{PROCESSING_LOG_CUBE}", timeout_s=30)
            table_exists = True
        except Exception:
            pass

        if full_refresh and not logs and not table_exists:
            await execute(
                f"CREATE TABLE iceberg.gold.{PROCESSING_LOG_CUBE} AS SELECT "
                "CAST(NULL AS BIGINT) AS id, CAST(NULL AS BIGINT) AS etl_task_id, "
                "CAST(NULL AS VARCHAR) AS process_type, CAST(NULL AS VARCHAR) AS status, "
                "CAST(NULL AS VARCHAR) AS source_label, CAST(NULL AS VARCHAR) AS destination_label, "
                "CAST(NULL AS VARCHAR) AS partitions_label, CAST(NULL AS BIGINT) AS rows_source, "
                "CAST(NULL AS BIGINT) AS rows_destination, "
                "CAST(NULL AS TIMESTAMP(6) WITH TIME ZONE) AS started_at, "
                "CAST(NULL AS TIMESTAMP(6) WITH TIME ZONE) AS finished_at, "
                "CAST(NULL AS VARCHAR) AS phase, "
                "CAST(NULL AS TIMESTAMP(6) WITH TIME ZONE) AS updated_at WHERE 1=0",
                timeout_s=60,
            )
            return {"rows_written": 0, "checkpoint": 0}

        payload = []
        for row in logs:
            source = row.source if isinstance(row.source, dict) else {"value": row.source}
            destination = row.destination if isinstance(row.destination, dict) else {"value": row.destination}
            source_label = ", ".join(source.get("connection_names") or []) or str(
                source.get("name") or source.get("kind") or source.get("value") or ""
            )
            destination_label = str(
                destination.get("destination_name") or destination.get("table")
                or destination.get("namespace") or destination.get("value") or ""
            )
            partitions = row.partitions or []
            payload.append({
                "id": row.id,
                "etl_task_id": row.etl_task_id,
                "process_type": row.process_type or "processing",
                "status": row.status,
                "source_label": source_label,
                "destination_label": destination_label,
                "partitions_label": ", ".join(str(value) for value in partitions),
                "rows_source": int((row.counts or {}).get("rows_source") or 0),
                "rows_destination": int((row.counts or {}).get("rows_destination") or 0),
                "started_at": row.started_at,
                "finished_at": row.finished_at,
                "phase": row.phase,
                "updated_at": row.updated_at,
            })

        if full_refresh and payload:
            # Publish a clean snapshot from the PostgreSQL source of truth.
            # Repeated MERGE operations against Iceberg create position-delete
            # files and snapshots; a staged atomic replacement bounds both.
            frame = pd.DataFrame(payload)
            import time as _time

            import pyarrow as pa
            from pyiceberg.io.pyarrow import pyarrow_to_schema

            staging = f"{PROCESSING_LOG_CUBE}_stg_{_time.time_ns()}"
            iceberg._ensure_namespace("gold")
            arrow = pa.Table.from_pandas(frame, preserve_index=False)
            staged_table = iceberg._get_catalog().create_table(
                ("gold", staging), schema=pyarrow_to_schema(arrow.schema),
            )
            staged_table.append(arrow)
            try:
                written = iceberg.replace_staged_cube(
                    PROCESSING_LOG_CUBE,
                    staging,
                    namespace="gold",
                    known_row_count=len(frame),
                )
            except Exception:
                iceberg.cleanup_staged_table(staging, namespace="gold")
                raise
            try:
                await _maintain_processing_log_cube_snapshots()
            except Exception:
                logger.exception("Falha ao compactar o cubo de logs após reconstruí-lo")
            return {"rows_written": written, "checkpoint": max(item["id"] for item in payload)}

        for offset in range(0, len(payload), 100):
            batch = payload[offset:offset + 100]
            await _merge_processing_log_batch(batch, create=not table_exists)
            table_exists = True
        checkpoint = max(item["id"] for item in payload) if payload else checkpoint_id
        return {"rows_written": len(payload), "checkpoint": checkpoint}
    finally:
        if lock_acquired:
            try:
                session.execute(text("SELECT pg_advisory_unlock(734219405)"))
            except Exception:
                pass
        session.close()


def _task_type(task: EtlTask) -> str:
    return str(task.task_type or "extraction")


def _task_context(task: EtlTask) -> tuple[dict[str, Any], dict[str, Any], list[Any], dict[str, Any]]:
    result = task.result_json if isinstance(task.result_json, dict) else {}
    source = result.get("source") or {}
    if not isinstance(source, dict):
        source = {"value": source}
    if not source and task.connection_ids:
        source = {"connection_ids": task.connection_ids}
    elif not source and task.connection_id:
        source = {"connection_ids": [task.connection_id]}
    destination = result.get("destination") or {}
    if not isinstance(destination, dict):
        destination = {"value": destination}
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
    if task.started_at is None and task.status == "QUEUED":
        task.started_at = datetime.now(timezone.utc)
    if log is None:
        log = ProcessingLog(etl_task_id=task.id, process_type=_task_type(task))
        session.add(log)
    source, destination, partitions, parameters = _task_context(task)
    _enrich_task_context(session, source, destination)
    result = task.result_json if isinstance(task.result_json, dict) else {}
    errors = task.errors if isinstance(task.errors, list) else []
    log.process_type = _task_type(task)
    log.updated_at = datetime.now(timezone.utc)
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
    if task.started_at is None and task.status == "QUEUED":
        task.started_at = datetime.now(timezone.utc)
    if log is None:
        log = ProcessingLog(etl_task_id=task.id, process_type=_task_type(task))
        session.add(log)
    source, destination, partitions, parameters = _task_context(task)
    await _async_enrich_task_context(session, source, destination)
    result = task.result_json if isinstance(task.result_json, dict) else {}
    errors = task.errors if isinstance(task.errors, list) else []
    log.process_type = _task_type(task)
    log.updated_at = datetime.now(timezone.utc)
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
    """Register missing task logs and repair snapshots that fell out of sync."""
    existing_task_ids = select(ProcessingLog.etl_task_id).where(ProcessingLog.etl_task_id.is_not(None))
    query = select(EtlTask).where(~EtlTask.id.in_(existing_task_ids)).order_by(EtlTask.id)
    if limit:
        query = query.limit(limit)
    rows = session.execute(query).scalars().all()
    for task in rows:
        sync_etl_task_log(session, task)
    stale_query = (
        select(EtlTask)
        .join(ProcessingLog, ProcessingLog.etl_task_id == EtlTask.id)
        .where(or_(ProcessingLog.status != EtlTask.status, ProcessingLog.finished_at != EtlTask.finished_at))
        .order_by(EtlTask.id)
    )
    if limit:
        stale_query = stale_query.limit(limit)
    stale_rows = session.execute(stale_query).scalars().all()
    for task in stale_rows:
        sync_etl_task_log(session, task)
    forecast_count = backfill_forecast_runs(session)
    return len(rows) + len(stale_rows) + forecast_count


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
                forecast_detail = {
                    "id": run.id, "config_snapshot": run.config_snapshot or {},
                    "result_table": run.result_table, "cutoff_date": run.cutoff_date,
                    "source_snapshot_id": run.source_snapshot_id,
                }
                if details.get("forecast_run") != forecast_detail:
                    details["forecast_run"] = forecast_detail
                    task_log.details = details
                    task_log.updated_at = datetime.now(timezone.utc)
                continue
        log = session.execute(select(ProcessingLog).where(
            ProcessingLog.etl_task_id.is_(None), ProcessingLog.process_type == "forecast",
            ProcessingLog.details["forecast_run_id"].as_string() == str(run.id)
        )).scalar_one_or_none()
        if log is None:
            log = ProcessingLog(process_type="forecast", details={"forecast_run_id": run.id})
            session.add(log)
        log.process_type = "forecast"
        log.updated_at = datetime.now(timezone.utc)
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
            task_log.updated_at = datetime.now(timezone.utc)
            task_log.status = run.status
            task_log.phase = run.phase
            task_log.error = run.error
            task_log.finished_at = run.finished_at
            task_log.counts = {**(task_log.counts or {}), "rows_destination": run.rows_written or 0, "items_total": run.total_series or 0, "items_processed": run.processed_series or 0}
            return task_log
    log = (await session.execute(select(ProcessingLog).where(
        ProcessingLog.etl_task_id.is_(None), ProcessingLog.process_type == "forecast",
        ProcessingLog.details["forecast_run_id"].as_string() == str(run.id)
    ))).scalar_one_or_none()
    if log is None:
        log = ProcessingLog(process_type="forecast", details={"forecast_run_id": run.id})
        session.add(log)
    log.process_type = "forecast"
    log.updated_at = datetime.now(timezone.utc)
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
