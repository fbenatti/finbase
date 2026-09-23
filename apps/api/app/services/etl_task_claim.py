"""Atomic ETL queue claim and PostgreSQL advisory locks for worker concurrency."""

from __future__ import annotations

import zlib
from datetime import datetime, timedelta, timezone
import os

from sqlalchemy import Integer, exists, or_, select, text
from sqlalchemy.orm import Session, aliased

from app.models.etl_task import EtlTask

TASK_DEFER_SECONDS = 3

WORKER_TASK_TYPES: tuple[str, ...] = (
    "backup",
    "bronze_audit",
    "reprocess",
    "reclassification",
    "extraction",
    "forecast",
    "forecast_accuracy",
    "shared_cost",
    "ai_cost_relationship_reprocess",
    "gold_reprocess",
    "reservation_collection",
)

# These task types write an Iceberg Gold, Silver or planning table. Bronze
# extraction remains parallel, while the analytic warehouse has one writer.
_ANALYTIC_WRITER_TASK_TYPES = {
    "reprocess", "reclassification", "forecast", "forecast_accuracy",
    "shared_cost", "ai_cost_relationship_reprocess", "gold_reprocess",
}


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

    if task.task_type in _ANALYTIC_WRITER_TASK_TYPES:
        keys.append(_stable_lock_key("iceberg_analytic_writer"))

    if task.task_type in {"reprocess", "reclassification"} and task.destination_table_id:
        conn_ids = list(task.connection_ids or [])
        periods = [str(p) for p in (task.billing_periods or [])]
        if conn_ids and periods:
            for connection_id in conn_ids:
                for period in periods:
                    keys.append(_stable_lock_key(
                        "gold_slice", task.destination_table_id, connection_id, period,
                    ))
        elif task.destination_table_id:
            keys.append(_stable_lock_key("destination", task.destination_table_id))

    if task.task_type == "extraction" and task.connection_id:
        keys.append(_stable_lock_key("extraction", task.connection_id))

    if task.task_type in {"gold_reprocess", "shared_cost"}:
        keys.append(_stable_lock_key("singleton", task.task_type))

    return list(dict.fromkeys(keys))


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
    # A group child must not run before the physical Bronze audit that was
    # persisted for that exact group.  It stays queued rather than discovering
    # a missing file after earlier months were already transformed.
    audit = aliased(EtlTask)
    audit_id = EtlTask.result_json.op("->>")("bronze_audit_task_id")
    audit_ready = or_(
        # In inline mode validation is performed by the reprocess worker just
        # before staging the source/month unit; the separate audit task is
        # retained only for backwards-compatible history.
        (True if os.getenv("OPENFINOPS_INLINE_BRONZE_AUDIT", "1").lower() in {"1", "true", "yes", "on"} else False),
        audit_id.is_(None),
        exists(select(1).where(
            audit.id == audit_id.cast(Integer),
            audit.status == "COMPLETED",
        )),
    )
    now = datetime.now(timezone.utc)
    task = session.execute(
        select(EtlTask)
        .where(
            EtlTask.status == "QUEUED",
            EtlTask.task_type.in_(WORKER_TASK_TYPES),
            _queue_group_busy_clause(),
            audit_ready,
            or_(
                EtlTask.available_at.is_(None),
                EtlTask.available_at <= now,
            ),
        )
        .order_by(EtlTask.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if task is None:
        return None

    task.status = "RUNNING"
    task.phase = task.phase or "Worker assumiu a tarefa"
    task.started_at = now
    task.available_at = None
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


def terminate_stale_advisory_lock_holders(session: Session, *, max_age_seconds: int = 60) -> int:
    """Terminate abandoned PostgreSQL sessions holding our queue locks.

    Advisory locks live for the lifetime of the database session, rather than
    the task row.  Usually a dead worker's TCP connection is closed promptly,
    but an interrupted deployment can leave a pooled backend ``idle in
    transaction`` long enough to block the next worker.  Limit this recovery
    to the current database user/database and only sessions that both hold a
    granted advisory lock and have been idle in a transaction past the grace
    period.

    SQLite is used by some local/test installations and has no pg catalogues,
    so it is deliberately a no-op outside PostgreSQL.
    """
    if session.get_bind().dialect.name != "postgresql":
        return 0

    max_age_seconds = max(1, int(max_age_seconds))
    rows = session.execute(text("""
        WITH stale_lock_holders AS (
            SELECT DISTINCT activity.pid
              FROM pg_locks AS locks
              JOIN pg_stat_activity AS activity ON activity.pid = locks.pid
             WHERE locks.locktype = 'advisory'
               AND locks.granted
               AND activity.state = 'idle in transaction'
               AND activity.xact_start < now() - make_interval(secs => :max_age_seconds)
               AND activity.usename = current_user
               AND activity.datname = current_database()
               AND activity.pid <> pg_backend_pid()
        )
        SELECT pg_terminate_backend(pid)
          FROM stale_lock_holders
    """), {"max_age_seconds": max_age_seconds}).scalars().all()
    terminated = sum(bool(row) for row in rows)
    if terminated:
        session.commit()
    return terminated


def release_task_to_queue(
    session: Session,
    task: EtlTask,
    *,
    phase: str | None = None,
    defer_seconds: int = TASK_DEFER_SECONDS,
) -> None:
    task.status = "QUEUED"
    task.phase = phase or "Aguardando recurso exclusivo"
    task.started_at = None
    if defer_seconds > 0:
        task.available_at = datetime.now(timezone.utc) + timedelta(seconds=defer_seconds)
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
        if row.task_type in ("ai_cost_relationship_reprocess", "gold_reprocess"):
            row.status = "QUEUED"
            row.phase = "Reenfileirado após reinício do worker"
            row.finished_at = None
            continue
        if row.task_type == "backup":
            row.status = "FAILED"
            row.phase = "Worker reiniciado durante backup; tarefa encerrada"
            row.finished_at = datetime.now(timezone.utc)
            continue
        previous_phase = row.phase
        previous_blob = row.current_blob
        previous_result = dict(row.result_json or {})
        row.status = "FAILED"
        row.phase = (
            f"Worker reiniciado; tarefa encerrada (ultima fase: {previous_phase or 'desconhecida'})"
        )[:200]
        row.current_blob = previous_blob or "Worker reiniciado durante a execução"
        row.errors = list(row.errors or []) + ["Worker reiniciado durante a execução"]
        row.result_json = {
            **previous_result,
            "failure_kind": "worker_restart",
            "reconciled_at": datetime.now(timezone.utc).isoformat(),
            "previous_phase": previous_phase,
            "previous_current_blob": previous_blob,
        }
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
