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


def _queue_extraction_sync(connection_id: int, session, *, phase: str | None = None,
                          result_json: dict | None = None) -> int:
    """Insert a QUEUED extraction task for the worker to pick up (sync session).

    Returns the new task id. The worker executes the full ETL pipeline,
    including operational collectors (PTU, Automation, Resource Graph).
    """
    from sqlalchemy import insert
    from app.models.etl_task import EtlTask
    new_id = session.execute(
        insert(EtlTask).values(
            task_type="extraction",
            status="QUEUED",
            connection_id=connection_id,
            destination_table_id=None,
            connection_ids=[connection_id],
            billing_periods=[],
            selected_blobs=[],
            total_blobs=0,
            processed_blobs=0,
            rows_raw=0,
            rows_normalized=0,
            rows_destination=0,
            errors=[],
            phase=phase,
            result_json=result_json,
        ).returning(EtlTask.id)
    )
    task_id = new_id.scalar_one()

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
        "active_downloads": 0,
        "errors": [],
    }
    return task_id


def _run_etl_job(connection_ids, schedule_id: int, schedule_name: str) -> None:
    """Queue ETL extraction for one or more connections (called from APScheduler).

    ``connection_ids`` may be a single int (legacy schedules) or a list of
    connection ids (multi-connection schedules such as "all cloud sources").
    Tasks are enqueued as QUEUED and executed by the worker process, which
    already handles every connector type (incl. PTU/Automation/Resource Graph).
    """
    if isinstance(connection_ids, int):
        connection_ids = [connection_ids]

    logger.info("Scheduled ETL [%s] queuing extraction for connections %s", schedule_name, connection_ids)

    session = _get_sync_session()
    try:
        from app.models.scheduled_task import ScheduledTask
        sched = session.get(ScheduledTask, schedule_id)
        if sched:
            sched.last_run_at = datetime.now(timezone.utc)
        session.commit()

        queued = []
        for connection_id in connection_ids:
            task_id = _queue_extraction_sync(
                connection_id, session,
                phase=f"Agendado: {schedule_name}",
                result_json={"schedule_id": schedule_id, "schedule_name": schedule_name},
            )
            queued.append(task_id)
        session.commit()
        logger.info("Scheduled ETL [%s] queued tasks %s", schedule_name, queued)
    except Exception as e:
        logger.error("Scheduled ETL [%s] failed: %s", schedule_name, e)
        session.rollback()
    finally:
        session.close()


def _queue_ptu_collections(*, hours: int, lookback_note: str) -> None:
    from sqlalchemy import insert, select
    from app.database import SyncSession
    from app.models.source_connection import SourceConnection
    from app.models.etl_task import EtlTask

    session = SyncSession()
    try:
        conns = session.execute(select(SourceConnection).where(SourceConnection.connector_type == "azure_openai_ptu")).scalars().all()
        queued = 0
        for conn in conns:
            active = session.execute(
                select(EtlTask.id).where(
                    EtlTask.connection_id == conn.id,
                    EtlTask.task_type == "extraction",
                    EtlTask.status.in_(["QUEUED", "RUNNING"]),
                ).limit(1)
            ).scalar_one_or_none()
            if active:
                logger.info("Azure PTU collection skipped for connection %d — task %s already active", conn.id, active)
                continue
            session.execute(insert(EtlTask).values(
                task_type="extraction",
                status="QUEUED",
                connection_id=conn.id,
                connection_ids=[conn.id],
                billing_periods=[],
                selected_blobs=[],
                total_blobs=0,
                processed_blobs=0,
                rows_raw=0,
                rows_normalized=0,
                rows_destination=0,
                errors=[],
                phase=lookback_note,
                result_json={"lookback_hours": hours},
            ))
            queued += 1
        session.commit()
        logger.info("Queued Azure PTU collection for %d/%d connections (%s, hours=%s)", queued, len(conns), lookback_note, hours)
    except Exception as exc:
        logger.warning("Azure PTU schedule queue failed: %s", exc)
        session.rollback()
    finally:
        session.close()


def _azure_ptu_hourly_job() -> None:
    _queue_ptu_collections(hours=3, lookback_note="Azure PTU hourly lookback 3h")


def _azure_ptu_daily_job() -> None:
    _queue_ptu_collections(hours=168, lookback_note="Azure PTU daily re-read 7d")


def _azure_ptu_dimon_daily_job() -> None:
    """Compacta a Gold horária depois da coleta diária para os dashboards."""
    try:
        import asyncio
        from app.services.azure_ptu_dimon import refresh_azure_ptu_dimon

        result = asyncio.run(refresh_azure_ptu_dimon(days=400))
        logger.info("Azure PTU Dimon refreshed: %s", result)
    except Exception as exc:
        logger.warning("Azure PTU Dimon refresh failed: %s", exc, exc_info=True)


def _ai_usage_dimon_daily_job() -> None:
    """Enfileira as Dimons auxiliares; a publicação pertence ao escritor Dimon."""
    session = _get_sync_session()
    try:
        from app.models.dimon_rule import DimonRule
        from app.services.dimon_materialization import enqueue_dimons_task

        codes = {
            "ai_consumption_daily", "ai_license_snapshot", "copilot_usage_daily",
            "copilot_license_snapshot", "copilot_user_period",
        }
        rules = session.query(DimonRule).filter(DimonRule.active == True, DimonRule.code.in_(codes)).all()  # noqa: E712
        task = enqueue_dimons_task(session, rule_ids=[rule.id for rule in rules], source="ai_usage_daily")
        session.commit()
        logger.info("AI usage Dimons queued in Dimon writer: task=%s rules=%d", getattr(task, "id", None), len(rules))
    except Exception as exc:
        logger.warning("AI usage Dimons refresh failed: %s", exc, exc_info=True)
        session.rollback()
    finally:
        session.close()


def _azure_ptu_eventhub_job() -> None:
    try:
        from app.services.azure_ptu_eventhub import consume_all_ptu_eventhubs
        results = consume_all_ptu_eventhubs()
        logger.info("Azure PTU Event Hub poll: %d connections", len(results))
    except Exception as exc:
        logger.warning("Azure PTU Event Hub poll failed: %s", exc)


def _oci_operational_hourly_job() -> None:
    """Refresh OCI AI/GPU telemetry for explicitly configured compartments."""
    session = _get_sync_session()
    try:
        from app.models.source_connection import SourceConnection
        from app.services.oci_operational_monitoring import collect, configured_compartments
        conns = session.query(SourceConnection).filter(SourceConnection.connector_type == "oci").all()
        for conn in conns:
            if configured_compartments(conn):
                result = collect(conn, hours=3, persist=True)
                logger.info("OCI operational refresh %s: inventory=%d metrics=%d errors=%d", conn.code, len(result.get("inventory", [])), len(result.get("metrics", [])), len(result.get("errors", [])))
    except Exception as exc:
        logger.warning("OCI operational hourly collection failed: %s", exc, exc_info=True)
    finally:
        session.close()


def _queue_reservation_collections() -> None:
    """Queue a reservation_collection task for every Azure connection with the flag.

    Reservas (RI + Savings Plans) são uma camada bronze SEPARADA da mesma
    conexão: o task dedicado não re-dispara o ETL de blobs de custo.
    """
    from sqlalchemy import insert, select
    from app.database import SyncSession
    from app.models.source_connection import SourceConnection
    from app.models.etl_task import EtlTask

    session = SyncSession()
    try:
        conns = session.execute(
            select(SourceConnection).where(
                SourceConnection.connector_type.in_(["azure_blob"]),
                SourceConnection.config_json["collect_reservations"].astext == "true",
            )
        ).scalars().all()
        queued = 0
        for conn in conns:
            active = session.execute(
                select(EtlTask.id).where(
                    EtlTask.connection_id == conn.id,
                    EtlTask.task_type == "reservation_collection",
                    EtlTask.status.in_(["QUEUED", "RUNNING"]),
                ).limit(1)
            ).scalar_one_or_none()
            if active:
                logger.info("Reservation collection skipped for connection %d — task %s already active", conn.id, active)
                continue
            session.execute(insert(EtlTask).values(
                task_type="reservation_collection",
                status="QUEUED",
                connection_id=conn.id,
                connection_ids=[conn.id],
                billing_periods=[],
                selected_blobs=[],
                total_blobs=0,
                processed_blobs=0,
                rows_raw=0,
                rows_normalized=0,
                rows_destination=0,
                errors=[],
                phase="Coleta diária de reservas Azure",
                result_json={"collect_reservations": True},
            ))
            queued += 1
        session.commit()
        logger.info("Queued Azure reservation collection for %d/%d connections", queued, len(conns))
    except Exception as exc:
        logger.warning("Azure reservation schedule queue failed: %s", exc)
        session.rollback()
    finally:
        session.close()


def _azure_reservations_daily_job() -> None:
    _queue_reservation_collections()


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
    run = ForecastRun(forecast_config_id=config_id, config_version=config.version, trigger_type="POST_INGESTION", source_etl_task_id=source_task_id, cutoff_date=None)
    session.add(run)
    session.flush()
    task = EtlTask(task_type="forecast", status="QUEUED", destination_table_id=config_id, result_json={"forecast_run_id": run.id, "source_etl_task_id": source_task_id}, errors=[])
    session.add(task)
    session.commit()
    logger.info("Queued forecast run %d after ETL task %d", run.id, source_task_id)
    return run.id


def _schedule_connection_ids(schedule_row) -> list[int]:
    """Resolve the connections covered by a schedule row."""
    ids = list(schedule_row.connection_ids or []) if schedule_row.connection_ids else []
    if not ids and schedule_row.connection_id:
        ids = [schedule_row.connection_id]
    return ids


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
            args=[_schedule_connection_ids(schedule_row), schedule_row.id, schedule_row.name],
            id=f"sched_{schedule_row.id}",
            name=schedule_row.name,
            replace_existing=True,
        )

        # Update next_run_at (safe access for APScheduler 3.x). During
        # startup the scheduler has not started yet, so job.next_run_time is
        # None even though the trigger is valid — compute it via croniter.
        try:
            schedule_row.next_run_at = job.next_run_time
        except AttributeError:
            schedule_row.next_run_at = getattr(job, 'next_run_time', None)
        if schedule_row.next_run_at is None:
            try:
                from croniter import croniter
                from datetime import datetime as _dt
                base = _dt.now().astimezone()
                tz = schedule_row.timezone or "America/Sao_Paulo"
                try:
                    from zoneinfo import ZoneInfo
                    base = _dt.now(ZoneInfo(tz))
                except Exception:
                    pass
                itr = croniter(schedule_row.cron_expression, base)
                schedule_row.next_run_at = itr.get_next(_dt)
            except Exception as exc:
                logger.warning("Could not compute next_run_at for schedule %d: %s", schedule_row.id, exc)
        # Persist on the sync session's own object: the row passed in may
        # belong to the async session (POST /etl/schedules), whose changes
        # this commit would silently ignore.
        from app.models.scheduled_task import ScheduledTask as _SchedModel
        persisted = sync_session.get(_SchedModel, schedule_row.id)
        if persisted is not None:
            persisted.next_run_at = schedule_row.next_run_at
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
        # Disabled: Azure PTU hourly lookback 3h (every hour at :10).
        # _scheduler.add_job(_azure_ptu_hourly_job, "cron", minute=10, id="azure_ptu_hourly", replace_existing=True)
        _scheduler.add_job(_azure_ptu_daily_job, "cron", hour=3, minute=20, id="azure_ptu_daily", replace_existing=True)
        _scheduler.add_job(_azure_ptu_dimon_daily_job, "cron", hour=4, minute=30, id="azure_ptu_dimon_daily", replace_existing=True)
        _scheduler.add_job(_ai_usage_dimon_daily_job, "cron", hour=5, minute=10, id="ai_usage_dimon_daily", replace_existing=True)
        _scheduler.add_job(_azure_ptu_eventhub_job, "interval", minutes=2, id="azure_ptu_eventhub", replace_existing=True)
        _scheduler.add_job(_oci_operational_hourly_job, "cron", minute=18, id="oci_operational_hourly", replace_existing=True)
        _scheduler.add_job(_azure_reservations_daily_job, "cron", hour=3, minute=45, id="azure_reservations_daily", replace_existing=True)
        _scheduler.add_job(_iceberg_orphan_cleanup_job, "cron", hour=3, minute=0, id="iceberg_orphan_cleanup", replace_existing=True)
        logger.info("Scheduler started with %d active schedules", count)
    except Exception as e:
        logger.error("Failed to start scheduler: %s", e)
    finally:
        session.close()


def _iceberg_orphan_cleanup_job() -> None:
    try:
        from app.services.iceberg_service import cleanup_orphan_staging, expire_gold_snapshots
        # Staging is retained for a full seven days. It is removed only when
        # the catalog can prove it is not an active table.
        result = cleanup_orphan_staging(max_age_hours=24 * 7)
        logger.info(
            "Limpeza periódica de staging órfão executada: %d pastas removidas, %.2f MB liberados",
            result.get("deleted_dirs", 0),
            result.get("deleted_bytes", 0) / (1024 * 1024),
        )
        expire_gold_snapshots("dest_modelo_focus", retention_days=7)
    except Exception as exc:
        logger.error("Falha na limpeza periódica de staging órfão do Iceberg: %s", exc)


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
        "active_downloads": 0,
        "errors": [],
    }

    return task_id


async def run_schedule_now(connection_ids) -> list[int]:
    """Queue an extraction task for every connection covered by a schedule.

    Returns the list of queued task ids (one per connection).
    """
    if isinstance(connection_ids, int):
        connection_ids = [connection_ids]
    task_ids = []
    for connection_id in connection_ids:
        task_ids.append(await run_etl_and_reprocess(connection_id))
    return task_ids
