"""Atomic ETL queue claim and PostgreSQL advisory locks for worker concurrency."""

from __future__ import annotations

import zlib
from datetime import datetime, timezone

from sqlalchemy import exists, or_, select, text
from sqlalchemy.orm import Session, aliased

from app.models.etl_task import EtlTask

WORKER_TASK_TYPES: tuple[str, ...] = (
    "reprocess",
    "reclassification",
    "extraction",
    "forecast",
    "forecast_accuracy",
    "shared_cost",
    "ai_cost_relationship_reprocess",
    "dimon_reprocess",
    "reservation_collection",
)


def _stable_lock_key(namespace: str, *parts: object) -> int:
    payload = namespace + "|" + "|".join(str(part) for part in parts)
    return zlib.crc32(payload.encode("utf-8")) & 0x7FFFFFFF


def task_resource_lock_keys(task: EtlTask) -> list[int]:
    """Return advisory-lock keys that must be held while executing *task*."""
    keys: list[int] = []
    payload = task.result_json if isinstance(task.result_json, dict) else {}
    queue_group = payload.get("queue_group")
    if queue_group:
        keys.append(_stable_lock_key("queue_group", queue_group))

    if task.task_type in {"reprocess", "reclassification"} and task.destination_table_id:
        conn_ids = list(task.connection_ids or [])
        periods = [str(p) for p in (task.billing_periods or [])]
        if conn_ids and periods:
            keys.append(
                _stable_lock_key(
                    "gold_slice",
                    task.task_type,
                    task.destination_table_id,
                    conn_ids[0],
                    periods[0],
                )
            )
        elif task.destination_table_id:
            keys.append(_stable_lock_key("destination", task.task_type, task.destination_table_id))

    if task.task_type == "extraction" and task.connection_id:
        keys.append(_stable_lock_key("extraction", task.connection_id))

    if task.task_type in {"dimon_reprocess", "shared_cost"}:
        keys.append(_stable_lock_key("singleton", task.task_type))

    return keys


def _queue_group_busy_clause():
    """Exclude QUEUED tasks whose queue_group already has a RUNNING sibling."""
    running = aliased(EtlTask)
    busy = exists(
        select(1).where(
            running.status == "RUNNING",
            running.result_json.op("->>")("queue_group").isnot(None),
            running.result_json.op("->>")("queue_group")
            == EtlTask.result_json.op("->>")("queue_group"),
        )
    )
    return or_(
        EtlTask.result_json.op("->>")("queue_group").is_(None),
        ~busy,
    )


def claim_next_etl_task(session: Session) -> EtlTask | None:
    """Claim one QUEUED task using ``FOR UPDATE SKIP LOCKED``."""
    task = session.execute(
        select(EtlTask)
        .where(
            EtlTask.status == "QUEUED",
            EtlTask.task_type.in_(WORKER_TASK_TYPES),
            _queue_group_busy_clause(),
        )
        .order_by(EtlTask.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if task is None:
        return None

    task.status = "RUNNING"
    task.phase = task.phase or "Worker assumiu a tarefa"
    task.started_at = datetime.now(timezone.utc)
    session.flush()
    return task


def try_acquire_task_resource_locks(session: Session, task: EtlTask) -> bool:
    """Acquire all advisory locks required by *task*; release on failure."""
    acquired: list[int] = []
    try:
        for key in task_resource_lock_keys(task):
            locked = session.execute(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": key},
            ).scalar()
            if not locked:
                for held in acquired:
                    session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": held})
                return False
            acquired.append(key)
        return True
    except Exception:
        for held in acquired:
            try:
                session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": held})
            except Exception:
                pass
        raise


def release_task_resource_locks(session: Session, task: EtlTask) -> None:
    for key in task_resource_lock_keys(task):
        try:
            session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
        except Exception:
            pass


def release_task_to_queue(session: Session, task: EtlTask, *, phase: str | None = None) -> None:
    task.status = "QUEUED"
    task.phase = phase or "Aguardando recurso exclusivo"
    task.started_at = None
    session.flush()


def reconcile_stale_running_tasks(session: Session) -> None:
    """Reconcile RUNNING rows after a supervisor restart."""
    from app.models.forecast import ForecastRun
    from app.models.source_connection import SourceConnection

    stale = session.execute(select(EtlTask).where(EtlTask.status == "RUNNING")).scalars().all()
    stale_forecast_run_ids: set[int] = set()
    for row in stale:
        if row.task_type == "extraction" and row.connection_id:
            conn = session.get(SourceConnection, row.connection_id)
            if conn and conn.connector_type == "azure_openai_ptu":
                row.status = "QUEUED"
                row.phase = "Reenfileirado após reinício do worker"
                row.finished_at = None
                continue
        if row.task_type == "reservation_collection":
            row.status = "QUEUED"
            row.phase = "Reenfileirado após reinício do worker"
            row.finished_at = None
            continue
        if row.task_type in ("ai_cost_relationship_reprocess", "dimon_reprocess"):
            row.status = "QUEUED"
            row.phase = "Reenfileirado após reinício do worker"
            row.finished_at = None
            continue
        row.status = "FAILED"
        row.phase = "Worker reiniciado; tarefa RUNNING anterior foi encerrada"
        row.finished_at = datetime.now(timezone.utc)
        if row.task_type == "forecast":
            run_id = (row.result_json or {}).get("forecast_run_id")
            if isinstance(run_id, int):
                stale_forecast_run_ids.add(run_id)

    if stale_forecast_run_ids:
        stale_runs = session.execute(
            select(ForecastRun).where(
                ForecastRun.id.in_(stale_forecast_run_ids),
                ForecastRun.status.in_(["QUEUED", "RUNNING"]),
            )
        ).scalars().all()
        for run in stale_runs:
            run.status = "FAILED"
            run.phase = "Worker reiniciado; execução anterior foi encerrada"
            run.error = "Worker reiniciado durante a execução do forecast"
            run.finished_at = datetime.now(timezone.utc)

    if stale:
        session.commit()
