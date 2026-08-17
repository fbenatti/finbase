"""
Scheduler Service — manages cron-based scheduled ETL tasks via APScheduler.

On startup, loads active schedules from the DB and registers them.
After ETL completes, auto-triggers destination table reprocess if mappings exist.
"""
import logging
import asyncio
import threading
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.base import JobLookupError

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None
_scheduler_thread: Optional[threading.Thread] = None
_loop: Optional[asyncio.AbstractEventLoop] = None


def _get_sync_session():
    """Lazy import to avoid circular deps."""
    from app.database import SyncSession
    return SyncSession()


def _run_etl_job(connection_id: int, schedule_id: int, schedule_name: str) -> None:
    """Run the ETL for a connection synchronously (called from APScheduler)."""
    from app.services.task_runner import _run_etl_sync

    logger.info("Scheduled ETL [%s] starting for connection %d", schedule_name, connection_id)

    # Create a minimal EtlTask record
    session = _get_sync_session()
    try:
        from app.models.etl_task import EtlTask
        task = EtlTask(connection_id=connection_id, status="RUNNING")
        session.add(task)
        session.flush()
        task_id = task.id

        # Update last_run_at on schedule
        from app.models.scheduled_task import ScheduledTask
        sched = session.get(ScheduledTask, schedule_id)
        if sched:
            sched.last_run_at = datetime.now(timezone.utc)
        session.commit()

        # Run the ETL synchronously
        _run_etl_sync(task_id, connection_id, None, None)

        session.refresh(task)

        # ── Auto-reprocess destination tables ──
        if sched and sched.destination_reprocess:
            _auto_reprocess_destinations(connection_id, session)
            # Gold was just refreshed; update any active savings immediately.
            from app.services.saving_service import refresh_actions
            refresh_actions(session)
            forecast_config_id = (sched.config_json or {}).get("forecast_config_id") if sched else None
            run_forecast_after = bool((sched.config_json or {}).get("forecast_after_ingestion")) if sched else False
            if run_forecast_after and forecast_config_id and (task.rows_raw or 0) > 0:
                _queue_forecast_run(int(forecast_config_id), task.id, session)

    except Exception as e:
        logger.error("Scheduled ETL [%s] failed: %s", schedule_name, e)
    finally:
        session.close()


def _auto_reprocess_destinations(connection_id: int, sync_session=None) -> int:
    """Reprocess destination tables for a connection after ETL completes.
    Only runs if active SourceDestinationMappings exist."""
    from app.models.destination import SourceDestinationMapping
    from sqlalchemy import select

    close_session = False
    session = sync_session
    if session is None:
        session = _get_sync_session()
        close_session = True

    try:
        # Discover which destination tables this connection maps to
        mapped_tables = session.execute(
            select(SourceDestinationMapping.destination_table_id)
            .where(
                SourceDestinationMapping.source_connection_id == connection_id,
                SourceDestinationMapping.active == True,
            )
            .distinct()
        ).scalars().all()

        if not mapped_tables:
            logger.info("No destination mappings for connection %d — skipping auto-reprocess", connection_id)
            return 0

        from app.routers.etl import _reprocess_destination_sync
        from app.services.task_runner import _inmemory_tasks as _gt

        total = 0
        for dt_id in mapped_tables:
            internal_task_id = -dt_id - (connection_id * 1000)
            _gt[internal_task_id] = {"status": "RUNNING", "total_blobs": 0, "processed_blobs": 0, "rows_destination": 0}
            res = _reprocess_destination_sync(internal_task_id, dt_id, [connection_id], [])
            if res["success"]:
                total += res["rows_written"]
                logger.info("Auto-reprocessed %d rows → dest_table_id=%d for connection %d",
                            res["rows_written"], dt_id, connection_id)
            else:
                logger.warning("Auto-reprocess failed for dest_table_id=%d: %s",
                               dt_id, res.get("error"))

        return total
    except Exception as e:
        logger.error("Auto-reprocess failed for connection %d: %s", connection_id, e)
        return 0
    finally:
        if close_session and session:
            session.close()


def _queue_forecast_run(config_id: int, source_task_id: int, session) -> int | None:
    """Queue a forecast only after a scheduled ETL ingested rows."""
    from app.models.forecast import ForecastConfig, ForecastRun
    from app.models.etl_task import EtlTask
    from sqlalchemy import select
    config = session.get(ForecastConfig, config_id)
    if not config or not config.active:
        return None
    active = session.execute(select(ForecastRun.id).where(ForecastRun.forecast_config_id == config_id, ForecastRun.status.in_(["QUEUED", "RUNNING"]))).scalar_one_or_none()
    if active:
        logger.info("Forecast %s already active; scheduled trigger skipped", active)
        return active
    run = ForecastRun(forecast_config_id=config_id, config_version=config.version, trigger_type="POST_INGESTION", source_etl_task_id=source_task_id, cutoff_date=datetime.now(timezone.utc).date().isoformat())
    session.add(run)
    session.flush()
    task = EtlTask(task_type="forecast", status="QUEUED", destination_table_id=config_id, result_json={"forecast_run_id": run.id, "source_etl_task_id": source_task_id}, errors=[])
    session.add(task)
    session.commit()
    logger.info("Queued forecast run %d after ETL task %d", run.id, source_task_id)
    return run.id


def _register_schedule(scheduler: AsyncIOScheduler, schedule_row, sync_session) -> bool:
    """Register a single schedule from DB into the APScheduler."""
    from app.models.scheduled_task import ScheduledTask
    from croniter import croniter

    try:
        trigger = CronTrigger.from_crontab(
            schedule_row.cron_expression,
            timezone=schedule_row.timezone,
        )

        job = scheduler.add_job(
            _run_etl_job,
            trigger=trigger,
            args=[schedule_row.connection_id, schedule_row.id, schedule_row.name],
            id=f"sched_{schedule_row.id}",
            name=schedule_row.name,
            replace_existing=True,
        )

        # Update next_run_at (safe access for APScheduler 3.x)
        try:
            schedule_row.next_run_at = job.next_run_time
        except AttributeError:
            schedule_row.next_run_at = getattr(job, 'next_run_time', None)
        sync_session.commit()

        next_str = str(schedule_row.next_run_at) if schedule_row.next_run_at else "N/A"
        logger.info("Registered schedule: %s (cron: %s, next: %s)",
                     schedule_row.name, schedule_row.cron_expression, next_str)
        return True
    except Exception as e:
        logger.error("Failed to register schedule %d (%s): %s",
                     schedule_row.id, schedule_row.name, e)
        return False


async def start_scheduler():
    """Start the APScheduler and load all active schedules from the DB."""
    global _scheduler, _loop

    # Ensure croniter is available
    try:
        import croniter  # noqa: F401
    except ImportError:
        import subprocess, sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", "croniter"])

    _scheduler = AsyncIOScheduler()
    _loop = asyncio.get_running_loop()

    session = _get_sync_session()
    try:
        from app.models.scheduled_task import ScheduledTask
        from sqlalchemy import select

        rows = session.execute(
            select(ScheduledTask).where(ScheduledTask.active == True)
        ).scalars().all()

        count = 0
        for row in rows:
            if _register_schedule(_scheduler, row, session):
                count += 1

        _scheduler.start()
        # Daily saving refresh is intentionally independent of ETL schedules: actions
        # that start today must progress even when a source has no new blob.
        _scheduler.add_job(_refresh_savings_job, "cron", hour=2, minute=15, id="savings_daily", replace_existing=True)
        logger.info("Scheduler started with %d active schedules", count)
    except Exception as e:
        logger.error("Failed to start scheduler: %s", e)
    finally:
        session.close()


def _refresh_savings_job() -> None:
    session = _get_sync_session()
    try:
        from app.services.saving_service import refresh_actions
        refresh_actions(session)
    except Exception as exc:
        logger.error("Daily saving refresh failed: %s", exc)
    finally:
        session.close()


async def stop_scheduler():
    """Stop the APScheduler gracefully."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped")


def add_job(schedule_row) -> bool:
    """Add a single job to the running scheduler."""
    global _scheduler, _loop
    if not _scheduler or not _loop:
        logger.warning("Scheduler not running — cannot add job")
        return False

    session = _get_sync_session()
    try:
        return _register_schedule(_scheduler, schedule_row, session)
    finally:
        session.close()


def remove_job(schedule_id: int):
    """Remove a job from the scheduler by schedule ID."""
    global _scheduler
    if not _scheduler:
        return
    try:
        _scheduler.remove_job(f"sched_{schedule_id}")
        logger.info("Removed scheduled job sched_%d", schedule_id)
    except JobLookupError:
        pass
    except Exception as e:
        logger.warning("Failed to remove job sched_%d: %s", schedule_id, e)


async def run_etl_and_reprocess(connection_id: int) -> int:
    """Run ETL + auto-reprocess (for manual trigger from API).
    Creates a QUEUED extraction task; the worker picks it up.
    Returns the task_id."""
    from sqlalchemy import insert
    from app.models.etl_task import EtlTask
    from app.database import async_session as as2

    async with as2() as db:
        new_id = await db.execute(
            insert(EtlTask).values(
                task_type="extraction",
                status="QUEUED",
                connection_id=connection_id,
                destination_table_id=None,
                connection_ids=[connection_id],
                billing_periods=[],
                total_blobs=0,
                processed_blobs=0,
                rows_raw=0,
                rows_normalized=0,
                rows_destination=0,
                errors=[],
            ).returning(EtlTask.id)
        )
        task_id = new_id.scalar_one()
        await db.commit()

    # Register in in-memory dict for immediate frontend visibility
    from app.services.task_runner import _inmemory_tasks
    _inmemory_tasks[task_id] = {
        "id": task_id,
        "connection_id": connection_id,
        "status": "QUEUED",
        "task_type": "extraction",
        "total_blobs": 0,
        "processed_blobs": 0,
        "rows_raw": 0,
        "rows_normalized": 0,
        "rows_destination": 0,
        "current_blob": None,
        "current_blob_size": 0,
        "download_bytes": 0,
        "downloaded_bytes_total": 0,
        "total_download_bytes": 0,
        "errors": [],
    }

    return task_id
