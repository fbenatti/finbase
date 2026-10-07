# Fontes com coletor dedicado: quando a task é de uma delas, o coletor vence o
# transporte da conexão. O price sheet, por exemplo, chega pelo mesmo
# `azure_blob` do custo, mas tem Blob, tabela e parser próprios.
#
# A lista vem do catálogo em vez de ser escrita à mão: manter as duas em sincronia
# é justamente o tipo de divergência que já causou erro silencioso.
_DEDICATED_COLLECTORS: frozenset[str] = frozenset({
    "azure_resource_graph",
    "azure_automation_jobs",
})

#!/usr/bin/env python3
"""FinOps Worker — polls etl_task queue and executes reprocess/extraction jobs.

Usage:
    cd apps/api && source ../../.venv/bin/activate && python worker.py
    # or
    bash apps/api/run_worker.sh

The worker runs independently from the API server. It polls the etl_task
database table every 2 seconds for tasks with status='QUEUED', picks them
up, executes the pipeline, and writes the final status back to the DB.
"""
import time
import signal
import sys
import os
import logging
import multiprocessing
import threading
import psutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Ensure the worker can import app.* modules when run from apps/api/
sys.path.insert(0, os.path.dirname(__file__))

# ── Pre-import ALL models so SQLAlchemy Base.metadata is fully populated ──
from app.database import SyncSession
from app.models.etl_task import EtlTask
from app.models.destination import DestinationTable, DestinationColumn, SourceDestinationMapping
from app.models.source_connection import SourceConnection
from app.models.ingested_blob import IngestedBlob
from app.models.rules import BusinessRule, ClassificationRule, Department
from app.models.skipped_blob import SkippedBlob
from app.models.fact_cost import FactCost
from app.models.period_status import PeriodStatus
from app.models.forecast import ForecastConfig, ForecastAdjustment, ForecastBusinessRule, ForecastRun
from app.models.azure_ptu import AzurePtuProjectMapping, AzurePtuSubproject
from sqlalchemy import select, text
from app.services.etl_task_claim import (
    claim_next_etl_task,
    find_stalled_worker_task,
    recover_worker_orphaned_tasks,
    recover_stalled_task,
    reconcile_stale_running_tasks,
    release_task_resource_locks,
    release_task_to_queue,
    terminate_stale_advisory_lock_holders,
    try_acquire_task_resource_locks,
)
from app.services.worker_runtime import (
    consumer_record_path,
    heartbeat_path,
    pid_path,
    register_consumer,
    runtime_dir,
    supervisor_pid_path,
    touch_heartbeat,
    write_pid,
)

POLL_INTERVAL = 2  # seconds between polls when idle
logger = logging.getLogger("openfinops.worker")


async def _resolve_forecast_source_metadata(table_name: str):
    """Load forecast source metadata from the namespace containing the table."""
    from app.services.forecast_service import fetch_metadata
    errors = []
    for namespace in ("gold", "silver"):
        try:
            metadata = await fetch_metadata(table_name, namespace)
            return namespace, metadata
        except Exception as exc:
            errors.append(f"{namespace}: {exc}")
    raise RuntimeError("Tabela de forecast indisponível: " + " | ".join(errors))


def _resolve_collection_reprocess_periods(
    session,
    *,
    connection_id: int,
    extraction_task_id: int,
    cfg: dict,
) -> list[str]:
    """Billing periods for Gold after a scheduled collection (not full Bronze history by default)."""
    from app.models.ingested_blob import IngestedBlob
    from sqlalchemy import select

    period_mode = cfg.get("period_mode", "ingestion")
    # A daily collection is incremental by contract.  Older schedules were
    # persisted with ``period_mode=all`` before the delta pipeline existed;
    # do not let that legacy value fan out a full historical reprocess.  A
    # full rebuild must be an explicit operator choice.
    if period_mode == "all" and cfg.get("full_reprocess") is True:
        return sorted({
            str(x) for x in session.execute(
                select(IngestedBlob.billing_period).where(
                    IngestedBlob.connection_id == connection_id,
                    IngestedBlob.billing_period.is_not(None),
                ).distinct()
            ).scalars().all() if x
        })
    if period_mode == "selected":
        return sorted({str(x) for x in (cfg.get("billing_periods") or []) if x})

    extraction = session.get(EtlTask, extraction_task_id)
    touched: list[str] = []
    if extraction:
        touched = [str(p) for p in (extraction.billing_periods or []) if p]
        if not touched:
            rj = extraction.result_json if isinstance(extraction.result_json, dict) else {}
            touched = [str(p) for p in (rj.get("billing_periods") or []) if p]
        blob_paths = [str(p) for p in (extraction.selected_blobs or []) if p]
        if not touched and blob_paths:
            touched = list(session.execute(
                select(IngestedBlob.billing_period).where(
                    IngestedBlob.connection_id == connection_id,
                    IngestedBlob.blob_path.in_(blob_paths),
                    IngestedBlob.billing_period.is_not(None),
                ).distinct()
            ).scalars().all())
        if not touched and extraction.started_at:
            touched = list(session.execute(
                select(IngestedBlob.billing_period).where(
                    IngestedBlob.connection_id == connection_id,
                    IngestedBlob.billing_period.is_not(None),
                    IngestedBlob.ingested_at >= extraction.started_at,
                ).distinct()
            ).scalars().all())
    periods = sorted({str(p) for p in touched if p and len(str(p)) >= 6})
    if periods:
        return periods

    # A daily collection can legitimately download zero files when the source
    # is already current.  That must not suppress the scheduled downstream
    # pipeline: reuse the latest Bronze month already available for this
    # connection instead of downloading it again.  Never apply this fallback
    # to a failed/cancelled extraction, because that would publish stale data
    # after an unsuccessful collection.
    if extraction and extraction.status in ("COMPLETED", "COMPLETED_WITH_ERRORS"):
        from app.models.forecast import ForecastConfig
        forecast_ids = [int(x) for x in (cfg.get("forecast_config_ids") or []) if x]
        cutoff_days = session.execute(
            select(ForecastConfig.cutoff_days).where(
                ForecastConfig.id.in_(forecast_ids),
                ForecastConfig.active.is_(True),
            )
        ).scalars().all() if forecast_ids else []
        if not cutoff_days:
            return []
        # Multiple forecast configs may be attached to one schedule. Use the
        # most conservative configured cutoff so no forecast receives a month
        # outside its own D-N window.
        operational_cutoff = datetime.now(timezone.utc).date() - timedelta(
            days=max(0, max(int(days) for days in cutoff_days))
        )
        cutoff_period = operational_cutoff.strftime("%Y%m")
        latest = session.execute(
            select(IngestedBlob.billing_period).where(
                IngestedBlob.connection_id == connection_id,
                IngestedBlob.billing_period.is_not(None),
                IngestedBlob.billing_period <= cutoff_period,
            ).order_by(IngestedBlob.billing_period.desc()).limit(1)
        ).scalar_one_or_none()
        if latest:
            return [str(latest)]
    return []


def _queue_schedule_destination_tasks(extraction_task_id: int, connection_id: int, metadata: dict | None) -> list[int]:
    """Queue Gold reprocessing requested by a collection schedule."""
    if not metadata or not metadata.get("schedule_id"):
        return []
    if metadata.get("source_key") and metadata["source_key"] != "cost_exports":
        # Operational telemetry has its own Silver model; it must not trigger
        # the financial destination configured on a cloud collection schedule.
        return []
    session = SyncSession()
    try:
        from app.models.scheduled_task import ScheduledTask
        from app.models.destination import SourceDestinationMapping
        from sqlalchemy import select
        schedule = session.get(ScheduledTask, metadata["schedule_id"])
        if not schedule or schedule.schedule_type != "collection" or not schedule.destination_reprocess:
            return []
        cfg = schedule.config_json or {}
        selected = [int(x) for x in (cfg.get("destination_table_ids") or [])]
        if not selected:
            selected = list(session.execute(select(SourceDestinationMapping.destination_table_id).where(
                SourceDestinationMapping.source_connection_id == connection_id,
                SourceDestinationMapping.active == True,  # noqa: E712
            ).distinct()).scalars().all())
        periods = _resolve_collection_reprocess_periods(
            session,
            connection_id=connection_id,
            extraction_task_id=extraction_task_id,
            cfg=cfg,
        )
        ids = []
        # Gold publication is intentionally scoped to one source and one
        # billing month per task.  Keeping all collected periods in a single
        # child task makes the worker reject it after claiming it.
        for dest_id in selected:
            for period in periods:
                child = EtlTask(
                    task_type="reprocess", status="QUEUED", destination_table_id=dest_id,
                    connection_ids=[connection_id], billing_periods=[period], rows_raw=0,
                    rows_normalized=0, rows_destination=0, errors=[],
                    phase=f"Agendado: reprocessar Gold {period} após coleta (task {extraction_task_id})",
                    result_json={"schedule_id": schedule.id, "scheduled_pipeline": True,
                                 # This task is deliberately one source + one
                                 # month.  Publish only that Iceberg partition;
                                 # preserving the rest by staging a complete
                                 # Gold rewrite makes a tiny scheduled import
                                 # grow with the whole destination table.
                                 "publish_by_partition": True,
                                 "forecast_config_ids": cfg.get("forecast_config_ids", []),
                                 "accuracy": cfg.get("accuracy", False), "shared_cost": cfg.get("shared_cost", False),
                                 "gold_rule_ids": cfg.get("gold_rule_ids", [])},
                )
                session.add(child)
                session.flush()
                ids.append(child.id)
        session.commit()
        return ids
    finally:
        session.close()


def _queue_scheduled_forecasts(source_task_id: int, task_json: dict, session) -> list[int]:
    """Queue configured Forecasts after a scheduled Gold publication."""
    config_ids = [int(x) for x in (task_json.get("forecast_config_ids") or []) if x]
    if not config_ids:
        return []
    from app.models.forecast import ForecastConfig, ForecastRun
    from sqlalchemy import select
    queued: list[int] = []
    schedule_id = task_json.get("schedule_id")
    if schedule_id:
        # A collection schedule creates one reprocess task per changed
        # source/month.  Only the final one may release the single full
        # forecast; otherwise each completed slice can enqueue another run.
        sibling_tasks = session.query(EtlTask).filter(
            EtlTask.task_type == "reprocess",
            EtlTask.status.in_(["QUEUED", "RUNNING"]),
        ).all()
        if any(
            isinstance(row.result_json, dict)
            and row.result_json.get("schedule_id") == schedule_id
            for row in sibling_tasks
        ):
            return []
        existing_forecasts = session.query(EtlTask).filter(
            EtlTask.task_type == "forecast",
            EtlTask.status.in_(["QUEUED", "RUNNING"]),
            EtlTask.result_json["scheduled_pipeline"].as_boolean() == True,  # noqa: E712
        ).all()
        if any(
            isinstance(row.result_json, dict)
            and row.result_json.get("schedule_id") == schedule_id
            for row in existing_forecasts
        ):
            return []
    for config_id in config_ids:
        prior = session.execute(select(EtlTask.result_json).where(EtlTask.task_type == "forecast")).scalars().all()
        if any(isinstance(item, dict) and item.get("scheduled_reprocess_task_id") == source_task_id and item.get("forecast_config_id") == config_id for item in prior):
            continue
        config = session.get(ForecastConfig, config_id)
        if not config or not config.active:
            continue
        active = session.execute(select(ForecastRun.id).where(
            ForecastRun.forecast_config_id == config_id,
            ForecastRun.status.in_(["QUEUED", "RUNNING"]),
        ).limit(1)).scalar_one_or_none()
        if active:
            continue
        run = ForecastRun(forecast_config_id=config_id, config_version=config.version,
                          trigger_type="SCHEDULED_REPROCESS", source_etl_task_id=source_task_id)
        session.add(run)
        session.flush()
        source_reprocess = session.get(EtlTask, source_task_id)
        pipeline_periods = list(source_reprocess.billing_periods or []) if source_reprocess else []
        gold_rule_ids = [int(x) for x in (task_json.get("gold_rule_ids") or []) if x]
        forecast_task = EtlTask(task_type="forecast", status="QUEUED", destination_table_id=config_id,
                                errors=[], result_json={"forecast_run_id": run.id,
                                "forecast_config_id": config_id, "scheduled_reprocess_task_id": source_task_id,
                                "schedule_id": schedule_id,
                                "scheduled_pipeline": True, "shared_cost": task_json.get("shared_cost", False),
                                "accuracy": task_json.get("accuracy", False),
                                "accuracy_months": pipeline_periods,
                                "gold_rule_ids": gold_rule_ids})
        session.add(forecast_task)
        session.flush()
        queued.append(forecast_task.id)
    return queued


def _queue_scheduled_accuracy(forecast_task_id: int, task_json: dict, session) -> None:
    if not task_json.get("scheduled_pipeline") or not task_json.get("accuracy"):
        return
    config_id = task_json.get("forecast_config_id")
    months = sorted({str(m) for m in (task_json.get("accuracy_months") or []) if len(str(m)) == 6})
    if not config_id or not months:
        return
    session.add(EtlTask(task_type="forecast_accuracy", status="QUEUED", destination_table_id=int(config_id),
                       total_blobs=len(months), errors=[], phase=f"Acurácia agendada: {len(months)} período(s)",
                       result_json={"config_id": int(config_id), "months": months,
                                    "scheduled_pipeline": True, "source_forecast_task_id": forecast_task_id,
                                    "gold_rule_ids": list(task_json.get("gold_rule_ids") or [])}))


def _format_task_error(exc: Exception) -> str:
    """Keep persisted worker errors readable when httpx raises empty messages."""
    message = str(exc).strip()
    if message:
        return message
    name = type(exc).__name__
    if name in {"ReadError", "ConnectError", "RemoteProtocolError"}:
        return f"{name}: conexão com Trino foi fechada (coordinator reiniciando ou sob carga)"
    return name


def _failure_context(result: dict) -> dict:
    """Return bounded diagnostic context from a failed task result."""
    context = result.get("failure_context")
    if not isinstance(context, dict):
        return {}
    return {
        "exception_type": str(context.get("exception_type") or "UnknownError"),
        "traceback": str(context.get("traceback") or "")[-12000:],
        "phase": str(context.get("phase") or "")[:200],
        "current_blob": str(context.get("current_blob") or "")[:500],
    }


def _is_etl_task_cancelled(task_id: int) -> bool:
    session = SyncSession()
    try:
        row = session.get(EtlTask, task_id)
        return row is not None and row.status == "CANCELLED"
    finally:
        session.close()


running = True


def shutdown(sig, frame):
    global running
    running = False
    print("\nWorker received shutdown signal. Finishing current task (if any) then exiting...")


signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)


def _start_heartbeat_thread(worker_id: int, interval: float = 5.0) -> threading.Thread:
    def _loop() -> None:
        while running:
            touch_heartbeat(worker_id)
            time.sleep(interval)

    thread = threading.Thread(
        target=_loop,
        name=f"openfinops-worker-{worker_id}-heartbeat",
        daemon=True,
    )
    thread.start()
    return thread


def consumer_main(worker_id: int):
    # Pandas keeps allocator arenas after large reprocesses.  On developer
    # machines this can push the long-lived worker over its RSS safeguard even
    # though each individual task completed.  Re-exec after a small number of
    # terminal reprocesses to start the next queue items with a clean heap.
    reprocess_tasks_since_restart = 0
    reprocess_restart_after = max(0, int(os.getenv("REPROCESS_WORKER_MAX_TASKS", "6")))

    def _maybe_recycle_reprocess_worker(*, failed: bool = False) -> None:
        nonlocal reprocess_tasks_since_restart
        reprocess_tasks_since_restart += 1
        if reprocess_restart_after and reprocess_tasks_since_restart >= reprocess_restart_after:
            print(
                f"Recycling worker after {reprocess_tasks_since_restart} reprocess task(s)"
                + ("; the last task failed." if failed else " to release memory."),
                flush=True,
            )
            sys.exit(0)

    runtime_dir().mkdir(parents=True, exist_ok=True)
    write_pid(worker_id)
    _start_heartbeat_thread(worker_id)
    print(
        f"FinOps Worker #{worker_id} started. Polling for QUEUED tasks every",
        POLL_INTERVAL,
        "seconds...",
        flush=True,
    )
    touch_heartbeat(worker_id)
    while running:
        session = SyncSession()
        try:
            task = claim_next_etl_task(session, worker_pid=os.getpid())
            if task is None:
                session.close()
                time.sleep(POLL_INTERVAL)
                continue

            session.commit()
            session.close()
            task_id = task.id
            # A full backup freezes all PostgreSQL/Iceberg writers. Tasks
            # claimed while it is active are returned to the queue.
            from app.services.write_lock import is_locked, task_waits_on_warehouse_flock
            if task_waits_on_warehouse_flock(task) and is_locked():
                retry = SyncSession()
                try:
                    queued = retry.get(EtlTask, task_id)
                    if queued and queued.status == "RUNNING":
                        release_task_to_queue(
                            retry,
                            queued,
                            phase="Aguardando bloqueio de escrita (backup ou Bronze/Gold)",
                        )
                        retry.commit()
                finally:
                    retry.close()
                continue
            lock_session = SyncSession()
            try:
                # Advisory locks are session-scoped. Acquire them on the same
                # connection kept open for the whole task; acquiring before
                # closing ``session`` released the lock immediately.
                if not try_acquire_task_resource_locks(lock_session, task):
                    retry = SyncSession()
                    try:
                        queued = retry.get(EtlTask, task_id)
                        if queued and queued.status == "RUNNING":
                            from app.services.etl_task_claim import (
                                _reprocess_task_has_slice_lock,
                                parallel_reprocess_slices_enabled,
                            )
                            if queued.task_type == "extraction":
                                wait_phase = "Aguardando conexão de origem"
                            elif (
                                queued.task_type in {"reprocess", "reclassification"}
                                and parallel_reprocess_slices_enabled()
                                and _reprocess_task_has_slice_lock(queued)
                            ):
                                wait_phase = "Aguardando fatia Silver (mesma origem+mês em execução)"
                            else:
                                wait_phase = "Aguardando escritor Silver/Gold"
                            release_task_to_queue(retry, queued, phase=wait_phase)
                            retry.commit()
                    finally:
                        retry.close()
                    continue

                if task.task_type == "backup":
                    if _is_etl_task_cancelled(task_id):
                        continue
                    # ``session`` was closed immediately after claiming the
                    # task.  Use a fresh session for the visible transition;
                    # otherwise the UI kept showing "Na fila" while the
                    # expensive archive was already running.
                    progress = SyncSession()
                    try:
                        row = progress.get(EtlTask, task_id)
                        if row and row.status == "CANCELLED":
                            continue
                        if row:
                            row.status = "RUNNING"
                            row.phase = "Criando backup full do projeto"
                            row.total_blobs = 1
                            row.processed_blobs = 0
                            progress.commit()
                    finally:
                        progress.close()
                    try:
                        from app.services.full_backup import BackupCancelledError, create_full_backup
                        from app.services.write_lock import exclusive_write_lock

                        def _report_backup_progress(bytes_written: int) -> None:
                            progress = SyncSession()
                            try:
                                row = progress.get(EtlTask, task_id)
                                if row and row.status == "RUNNING":
                                    gib = bytes_written / (1024 ** 3)
                                    row.phase = "Compactando backup full do projeto"
                                    row.current_blob = f"Arquivo parcial: {gib:.1f} GiB compactados"
                                    row.downloaded_bytes_total = bytes_written
                                    # The compressed final size is not known in
                                    # advance; do not display a made-up percent.
                                    row.total_download_bytes = 0
                                    progress.commit()
                            except Exception:
                                # Progress reporting must never invalidate the
                                # backup archive itself.
                                logger.debug("Could not persist backup progress", exc_info=True)
                            finally:
                                progress.close()

                        if _is_etl_task_cancelled(task_id):
                            continue
                        with exclusive_write_lock():
                            if _is_etl_task_cancelled(task_id):
                                raise BackupCancelledError("Backup cancelado pelo usuário")
                            result = create_full_backup(
                                retention_days=int((task.result_json or {}).get("retention_days", 7)),
                                backup_layers=(task.result_json or {}).get("backup_layers") or ["bronze"],
                                should_abort=lambda: _is_etl_task_cancelled(task_id),
                                on_progress=_report_backup_progress,
                            )
                        terminal = SyncSession()
                        row = terminal.get(EtlTask, task_id)
                        if row:
                            row.status = "COMPLETED"
                            row.phase = "Backup full concluído e sincronizado no Blob" if result.get("blob_archive") else "Backup full concluído"
                            row.total_blobs = 1
                            row.processed_blobs = 1
                            row.downloaded_bytes_total = Path(result["archive"]).stat().st_size
                            row.total_download_bytes = 0
                            blob_target = result.get("blob_archive")
                            row.current_blob = f"Local + Blob: {blob_target}" if blob_target else "Arquivo full validado e checksum gerado"
                            row.result_json = {**(row.result_json or {}), **result}
                            row.finished_at = datetime.now(timezone.utc)
                            terminal.commit()
                        terminal.close()
                    except BackupCancelledError:
                        cancelled = SyncSession()
                        row = cancelled.get(EtlTask, task_id)
                        if row:
                            row.status = "CANCELLED"
                            row.phase = "Backup cancelado pelo usuário"
                            row.finished_at = datetime.now(timezone.utc)
                            cancelled.commit()
                        cancelled.close()
                    except Exception as exc:
                        failed = SyncSession()
                        row = failed.get(EtlTask, task_id)
                        if row:
                            row.status = "FAILED"
                            row.phase = f"Falha no backup: {_format_task_error(exc)}"[:200]
                            row.errors = [_format_task_error(exc)]
                            row.finished_at = datetime.now(timezone.utc)
                            failed.commit()
                        failed.close()
                    continue
                if task.task_type == "forecast":
                    config_id = task.destination_table_id
                    run_id = (task.result_json or {}).get("forecast_run_id")
                    task_json = dict(task.result_json or {})
                    task.status = "RUNNING"
                    from app.services.processing_logs import sync_etl_task_log
                    sync_etl_task_log(session, task)
                    session.commit()
                    session.close()
                    try:
                        from app.models.forecast import ForecastConfig, ForecastAdjustment, ForecastBusinessRule, ForecastRun
                        from app.models.destination import DestinationTable
                        from app.services.forecast_service import fetch_metadata, validate_config, run_forecast
                        from types import SimpleNamespace
                        import asyncio
                        s = SyncSession()
                        config = s.get(ForecastConfig, config_id)
                        run = s.get(ForecastRun, run_id)
                        task_row = s.get(EtlTask, task_id)
                        dest = s.get(DestinationTable, config.destination_table_id) if config else None
                        adjustments = s.query(ForecastAdjustment).filter(ForecastAdjustment.forecast_config_id == config_id, ForecastAdjustment.active == True).all() if config else []
                        business_rules = s.query(ForecastBusinessRule).filter(ForecastBusinessRule.forecast_config_id == config_id, ForecastBusinessRule.active == True).all() if config else []
                        if not config or not run or not dest:
                            raise RuntimeError("Configuração de forecast inválida")
                        # Cancellation can race with the worker claiming the
                        # queue row. Re-check the run after loading both rows
                        # and never execute a cancelled forecast.
                        if run.status == "CANCELLED" or (task_row and task_row.status == "CANCELLED"):
                            if task_row:
                                task_row.status = "CANCELLED"
                                task_row.phase = "Cancelado pelo usuário"
                                task_row.finished_at = run.finished_at or datetime.now(timezone.utc)
                            s.commit()
                            s.close()
                            continue
                        # Keep the run status aligned with the task as soon as
                        # the worker claims it; the UI must not show QUEUED
                        # while the forecast is already executing.
                        run.status = "RUNNING"
                        run.phase = "Validando metadados"
                        # A retried run must not continue exposing the error
                        # from a previous attempt while the new attempt is
                        # healthy and still executing.
                        run.error = None
                        run.started_at = datetime.now(timezone.utc)
                        s.commit()
                        from app.database import run_async_isolated
                        source_namespace, metadata = run_async_isolated(_resolve_forecast_source_metadata(dest.physical_name))
                        errors = validate_config(config, metadata)
                        if errors:
                            raise RuntimeError("; ".join(errors))
                        run.phase = "Agregando histórico no Trino"
                        if task_row:
                            task_row.phase = "Agregando histórico no Trino"
                        s.commit()
                        config.destination_physical_name = dest.physical_name
                        config.source_namespace = source_namespace
                        config.gold_metadata = metadata
                        config.has_type_column = any(c["name"] == config.type_column for c in metadata["columns"])
                        simulated_as_of = None
                        if (run.config_snapshot or {}).get("simulated_as_of"):
                            simulated_as_of = date.fromisoformat(run.config_snapshot["simulated_as_of"])
                        from app.database import run_async_isolated
                        result = run_async_isolated(run_forecast(
                            config, adjustments,
                            date.fromisoformat(run.cutoff_date) if run.cutoff_date else None,
                            business_rules=business_rules, result_table=f"result_run_{run.id}",
                            simulated_as_of=simulated_as_of,
                        ))
                        run.status = "COMPLETED"
                        run.phase = "Forecast publicado"
                        run.finished_at = datetime.now(timezone.utc)
                        run.rows_written = result["rows"]
                        run.total_series = result["series"]
                        run.processed_series = result["series"]
                        run.result_table = result["table"]
                        # Persist the data-driven cutoff used by the engine,
                        # rather than the day on which the job was queued.
                        run.cutoff_date = result["effective_cutoff"]
                        run.selected_models = result["methods"]
                        # Keep the rules that the engine actually evaluated in
                        # the immutable run record.  Besides making the
                        # forecast auditable from the UI/API, this prevents a
                        # silent mismatch between an editable rule and a
                        # published snapshot from going unnoticed.
                        run.error_metrics = {
                            "business_rules": result.get("business_rules", []),
                        }
                        if task_row:
                            task_row.status = "COMPLETED"
                            task_row.rows_destination = result["rows"]
                            task_row.phase = "Forecast publicado"
                            task_row.finished_at = datetime.now(timezone.utc)
                        s.commit()
                        if task_json.get("scheduled_pipeline"):
                            _queue_scheduled_accuracy(task_id, task_json, s)
                            s.commit()
                        rateio: dict = {}
                        run_scheduled_rateio = (not task_json.get("scheduled_pipeline")) or bool(task_json.get("shared_cost", False))
                        try:
                            if not run_scheduled_rateio:
                                raise LookupError("rateio desabilitado no schedule")
                            from app.services.shared_cost import run_shared_cost_job_sync
                            rateio = run_shared_cost_job_sync(s, allow_empty=True)
                            if task_row:
                                task_row.phase = (
                                    f"Forecast publicado · {rateio.get('rows', 0)} linhas de rateio"
                                )
                                s.commit()
                        except LookupError:
                            pass
                        except Exception as rateio_exc:
                            if task_row:
                                task_row.phase = f"Forecast publicado · rateio pendente: {rateio_exc}"
                                s.commit()
                        # Sequência: forecast → rateio → Gold (visão completa =
                        # realizado + forecast + rateio). A planning mudou, então a
                        # Gold pré-agregada precisa ser materializada de novo; a
                        # fila do worker executa em ordem de id (sequência real).
                        gold_rule_ids = [int(x) for x in (task_json.get("gold_rule_ids") or []) if x]
                        # Scheduled pipelines publish Gold only after forecast
                        # and (when enabled) accuracy complete.  Manual
                        # forecasts retain the historical immediate Gold
                        # behavior.
                        run_scheduled_gold = not task_json.get("scheduled_pipeline")
                        try:
                            if not run_scheduled_gold:
                                raise LookupError("Gold desabilitada no schedule")
                            from app.services.gold_materialization import enqueue_rule_materialization
                            gold_task = enqueue_rule_materialization(
                                s, rule_ids=gold_rule_ids, source="forecast",
                            )
                            if gold_task and task_row:
                                task_row.phase = (
                                    f"Forecast publicado · {rateio.get('rows', 0)} linhas de rateio"
                                    f" · Gold enfileirada (task #{gold_task.id})"
                                )
                                s.commit()
                        except LookupError:
                            pass
                        except Exception as gold_exc:
                            if task_row:
                                task_row.phase = f"Forecast publicado · Gold pendente: {gold_exc}"
                                s.commit()
                        if task_json.get("scheduled_pipeline") and not task_json.get("accuracy"):
                            from app.services.gold_materialization import enqueue_rule_materialization
                            gold_task = enqueue_rule_materialization(
                                s,
                                rule_ids=gold_rule_ids,
                                source="scheduled_pipeline",
                            )
                            if gold_task and task_row:
                                task_row.phase = f"Forecast publicado · Gold na fila (task #{gold_task.id})"[:200]
                                s.commit()
                        s.close()
                    except Exception as exc:
                        import traceback
                        traceback.print_exc()
                        s = SyncSession()
                        run = s.get(ForecastRun, run_id)
                        task_row = s.get(EtlTask, task_id)
                        error_message = _format_task_error(exc)
                        # A user cancellation may arrive while an in-flight
                        # Trino request is unwinding.  Preserve that terminal
                        # state instead of overwriting it with FAILED.
                        if run and run.status != "CANCELLED":
                            run.status = "FAILED"
                            run.phase = f"Falha no forecast: {error_message[:160]}"
                            run.error = error_message[:2000]
                            run.finished_at = datetime.now(timezone.utc)
                        if task_row and task_row.status != "CANCELLED":
                            task_row.status = "FAILED"
                            task_row.phase = f"Erro: {error_message[:160]}"
                            task_row.finished_at = datetime.now(timezone.utc)
                        s.commit(); s.close()

                elif task.task_type == "forecast_accuracy":
                    # Batch "reprocess accuracy": regenerate the simulated
                    # forecast snapshots (simulated_as_of = day 2 of each month)
                    # so the accuracy dashboard reflects the current config.
                    config_id = task.destination_table_id
                    task_json = dict(task.result_json or {})
                    months = list((task.result_json or {}).get("months") or [])
                    task.status = "RUNNING"
                    task.phase = "Validando configuração"
                    from app.services.processing_logs import sync_etl_task_log
                    sync_etl_task_log(session, task)
                    session.commit()
                    session.close()
                    try:
                        from app.models.forecast import ForecastConfig, ForecastAdjustment, ForecastBusinessRule, ForecastRun
                        from app.models.destination import DestinationTable
                        from app.services.forecast_service import fetch_metadata, validate_config, run_forecast
                        import asyncio
                        s = SyncSession()
                        config = s.get(ForecastConfig, config_id)
                        dest = s.get(DestinationTable, config.destination_table_id) if config else None
                        task_row = s.get(EtlTask, task_id)
                        if not config or not dest:
                            raise RuntimeError("Configuração de forecast inválida")
                        if task_row and task_row.status == "CANCELLED":
                            task_row.phase = "Cancelado pelo usuário"
                            task_row.finished_at = datetime.now(timezone.utc)
                            s.commit(); s.close()
                            continue
                        adjustments = s.query(ForecastAdjustment).filter(
                            ForecastAdjustment.forecast_config_id == config_id,
                            ForecastAdjustment.active == True,
                        ).all()
                        business_rules = s.query(ForecastBusinessRule).filter(
                            ForecastBusinessRule.forecast_config_id == config_id,
                            ForecastBusinessRule.active == True,
                        ).all()
                        from app.database import run_async_isolated
                        source_namespace, metadata = run_async_isolated(_resolve_forecast_source_metadata(dest.physical_name))
                        errors = validate_config(config, metadata)
                        if errors:
                            raise RuntimeError("; ".join(errors))
                        config.destination_physical_name = dest.physical_name
                        config.source_namespace = source_namespace
                        config.gold_metadata = metadata
                        config.has_type_column = any(c["name"] == config.type_column for c in metadata["columns"])
                        snapshot = {
                            "config_id": config.id, "config_version": config.version,
                            "destination_table_id": dest.id,
                            "destination_physical_name": dest.physical_name,
                            "source_namespace": source_namespace,
                            "measure_column": config.measure_column, "date_column": config.date_column,
                            "period_column": config.period_column, "type_column": config.type_column,
                            "dimensions": list(config.dimensions or []), "currency_column": config.currency_column,
                            "cutoff_days": config.cutoff_days, "horizon_months": config.horizon_months,
                            "period_allocation": config.period_allocation,
                            "charge_end_column": config.charge_end_column,
                            "connection_allocation_rules": list(config.connection_allocation_rules or []),
                            "null_dimension_label": getattr(config, "null_dimension_label", "Não classificado") or "Não classificado",
                        }
                        processed = 0
                        failures: list[str] = []
                        total = len(months)
                        for month in months:
                            if task_row and task_row.status == "CANCELLED":
                                break
                            try:
                                as_of = date(int(month[:4]), int(month[4:6]), 2)
                                run = ForecastRun(
                                    forecast_config_id=config_id, config_version=config.version,
                                    trigger_type="MANUAL", cutoff_date=as_of.isoformat(),
                                    config_snapshot={**snapshot, "simulated_as_of": as_of.isoformat()},
                                    destination_table_id_snapshot=dest.id,
                                    destination_physical_name=dest.physical_name,
                                    classification_version_id=None, status="RUNNING",
                                    phase=f"Simulado {month}", started_at=datetime.now(timezone.utc),
                                )
                                s.add(run)
                                s.flush()
                                from app.database import run_async_isolated
                                result = run_async_isolated(run_forecast(
                                    config, adjustments, as_of,
                                    business_rules=business_rules,
                                    result_table=f"result_run_{run.id}",
                                    simulated_as_of=as_of,
                                ))
                                run.status = "COMPLETED"
                                run.phase = "Forecast publicado"
                                run.finished_at = datetime.now(timezone.utc)
                                run.rows_written = result["rows"]
                                run.total_series = result["series"]
                                run.processed_series = result["series"]
                                run.result_table = result["table"]
                                run.cutoff_date = result["effective_cutoff"]
                                run.selected_models = result["methods"]
                                run.error_metrics = {}
                                s.commit()
                                processed += 1
                                if task_row:
                                    task_row.phase = f"Simulado {month} concluído (run {run.id})"
                                    task_row.processed_blobs = processed
                                    task_row.total_blobs = total
                                    task_row.current_blob = month
                                    s.commit()
                            except Exception as exc:
                                s.rollback()
                                failures.append(f"{month}: {_format_task_error(exc)}")
                                if task_row:
                                    task_row.phase = f"Falha no simulado {month}"
                                    s.commit()
                        task_row = s.get(EtlTask, task_id)
                        if task_row:
                            if task_row.status == "CANCELLED":
                                task_row.phase = "Cancelado pelo usuário"
                            elif failures and processed == 0:
                                task_row.status = "FAILED"
                                task_row.phase = "Reprocessamento de acurácia falhou"
                                task_row.errors = failures
                            else:
                                task_row.status = "COMPLETED"
                                task_row.phase = (
                                    f"Acurácia reprocessada: {processed}/{total} meses"
                                    + (f"; falhas: {len(failures)}" if failures else "")
                                )
                                task_row.errors = failures
                            task_row.finished_at = datetime.now(timezone.utc)
                            s.commit()
                        # The scheduled pipeline deliberately materializes
                        # Gold after accuracy, so forecast never races or
                        # implicitly triggers the final cube publication.
                        if task_row and task_row.status == "COMPLETED" and task_json.get("scheduled_pipeline"):
                            from app.services.gold_materialization import enqueue_rule_materialization
                            gold_task = enqueue_rule_materialization(
                                s,
                                rule_ids=[int(x) for x in (task_json.get("gold_rule_ids") or []) if x],
                                source="scheduled_pipeline",
                            )
                            if gold_task:
                                task_row.phase = f"{task_row.phase} · Gold na fila (task #{gold_task.id})"[:200]
                                s.commit()
                        s.close()
                    except Exception as exc:
                        import traceback
                        traceback.print_exc()
                        s = SyncSession()
                        task_row = s.get(EtlTask, task_id)
                        error_message = _format_task_error(exc)
                        if task_row:
                            task_row.status = "FAILED"
                            task_row.phase = f"Erro: {error_message[:160]}"
                            task_row.finished_at = datetime.now(timezone.utc)
                        s.commit(); s.close()

                elif task.task_type in ("ai_cost_relationship_reprocess", "gold_reprocess"):
                    task.status = "RUNNING"
                    task.phase = "Agregando cubos Gold e publicando as tabelas Gold"
                    from app.services.processing_logs import sync_etl_task_log
                    sync_etl_task_log(session, task)
                    session.commit()
                    session.close()
                    try:
                        from app.database import run_async_isolated
                        from app.services.gold_materialization import materialize_gold_cubes
                        s = SyncSession()
                        task_row = s.get(EtlTask, task_id)
                        def progress(done, total, name):
                            current = s.get(EtlTask, task_id)
                            if current:
                                current.processed_blobs = done
                                current.total_blobs = total
                                current.phase = f"Materializando {done}/{total}: {name}"
                                s.commit()
                        rule_ids = list((task_row.result_json or {}).get("rule_ids") or []) if task_row else []
                        billing_periods = list((task_row.result_json or {}).get("billing_periods") or []) if task_row else []
                        result = run_async_isolated(materialize_gold_cubes(
                            s,
                            run_id=task_id,
                            progress=progress,
                            rule_ids=rule_ids or None,
                            billing_periods=billing_periods or None,
                        ))
                        task_row = s.get(EtlTask, task_id)
                        if task_row:
                            failed = list(result.get("failed") or [])
                            rows = int(result.get("rows") or 0)
                            if failed and rows == 0 and not result.get("details"):
                                task_row.status = "FAILED"
                                task_row.phase = "Nenhuma Gold publicada: " + "; ".join(
                                    f"{f.get('code', f.get('rule_id'))}: {f.get('error', 'erro')}" for f in failed
                                )[:160]
                                task_row.errors = [f.get("error", "") for f in failed]
                            else:
                                task_row.status = "COMPLETED"
                                task_row.phase = f"Gold publicada: {rows} linhas" + (
                                    f" · {len(failed)} regra(s) com erro" if failed else ""
                                )
                                task_row.rows_destination = rows
                                task_row.processed_blobs = int(result.get("rules") or 0)
                                task_row.errors = [f.get("error", "") for f in failed] or None
                            task_row.finished_at = datetime.now(timezone.utc)
                            task_row.result_json = {**(task_row.result_json or {}), **result}
                        s.commit(); s.close()
                    except Exception as exc:
                        s = SyncSession()
                        task_row = s.get(EtlTask, task_id)
                        if task_row:
                            task_row.status = "FAILED"
                            task_row.phase = f"Erro: {_format_task_error(exc)[:160]}"
                            task_row.errors = [str(exc)]
                            task_row.finished_at = datetime.now(timezone.utc)
                        s.commit(); s.close()

                elif task.task_type == "bronze_audit":
                    task.status = "RUNNING"
                    task.phase = "Auditando arquivos físicos Bronze"
                    session.commit()
                    session.close()
                    try:
                        from app.routers.etl import _audit_reprocess_group_sync
                        result = _audit_reprocess_group_sync(
                            task_id, task.destination_table_id,
                            list(task.connection_ids or []), list(task.billing_periods or []),
                        )
                        s = SyncSession()
                        audit_row = s.get(EtlTask, task_id)
                        succeeded = bool(result.get("success"))
                        if audit_row:
                            audit_row.status = "COMPLETED" if succeeded else "FAILED"
                            audit_row.total_blobs = int(result.get("audited_blobs") or audit_row.total_blobs)
                            audit_row.phase = "Auditoria Bronze aprovada" if succeeded else str(result.get("error"))[:200]
                            audit_row.errors = [] if succeeded else [str(result.get("error"))]
                            audit_row.finished_at = datetime.now(timezone.utc)
                            audit_row.result_json = {**(audit_row.result_json or {}), **result}
                        if not succeeded:
                            # A failed audit is a hard group barrier.  Mark all
                            # children visibly blocked; leaving them QUEUED
                            # would look like a stuck worker and invite an
                            # unsafe manual retry.
                            for child in s.query(EtlTask).filter(EtlTask.status == "QUEUED").all():
                                payload = child.result_json if isinstance(child.result_json, dict) else {}
                                if payload.get("bronze_audit_task_id") == task_id:
                                    child.status = "FAILED"
                                    child.phase = "Bloqueado pela auditoria Bronze"
                                    child.errors = [str(result.get("error"))]
                                    child.finished_at = datetime.now(timezone.utc)
                        s.commit()
                        s.close()
                    except Exception as exc:
                        s = SyncSession()
                        audit_row = s.get(EtlTask, task_id)
                        if audit_row:
                            audit_row.status = "FAILED"
                            audit_row.phase = f"Erro na auditoria Bronze: {_format_task_error(exc)[:160]}"
                            audit_row.errors = [str(exc)]
                            audit_row.finished_at = datetime.now(timezone.utc)
                        s.commit(); s.close()

                elif task.task_type in ("reprocess", "reclassification"):
                    dest_id = task.destination_table_id
                    conn_ids = task.connection_ids or []
                    periods = task.billing_periods or []

                    print(
                        f"Picked up reprocess task {task_id}:"
                        f" dest_table={dest_id},"
                        f" sources={[getattr(c, 'code', cid) for cid, c in zip(conn_ids, conn_rows)] if 'conn_rows' in locals() else conn_ids},"
                        f" connections={conn_ids},"
                        f" periods={periods}"
                    )

                    # Mark as RUNNING so no other worker picks it up
                    task.status = "RUNNING"
                    task_json = dict(task.result_json or {})
                    session.commit()
                    session.close()

                    try:
                        from app.routers.etl import _reprocess_destination_sync, _reclassify_destination_sync
                        from app.routers.etl import _append_reprocess_log
                        from app.services.task_runner import _inmemory_tasks
                        from app.models.source_connection import SourceConnection as _ReprocessConnection

                        ptu_session = SyncSession()
                        try:
                            conn_rows = [
                                ptu_session.get(_ReprocessConnection, cid) for cid in conn_ids
                            ]
                            is_ptu_reprocess = bool(conn_ids) and all(
                                row and row.connector_type == "azure_openai_ptu" for row in conn_rows
                            )
                        finally:
                            ptu_session.close()

                        if is_ptu_reprocess:
                            from app.services.azure_ptu_service import run_ptu_collection

                            _inmemory_tasks[task_id] = {
                                "id": task_id,
                                "type": "reprocess",
                                "status": "RUNNING",
                                "task_type": "reprocess",
                                "destination_table_id": dest_id,
                                "connection_ids": conn_ids,
                                "billing_periods": periods,
                                "total_blobs": 0,
                                "processed_blobs": 0,
                                "rows_destination": 0,
                                "current_blob": "Coleta histórica Azure PTU",
                                "table": "",
                                "errors": [],
                            }
                            _append_reprocess_log(task_id, "Worker assumiu coleta histórica Azure PTU")
                            from app.services.azure_ptu_service import resolve_ptu_collection_params

                            total_written = 0
                            for conn in conn_rows:
                                if not conn:
                                    continue
                                collect_session = SyncSession()
                                try:
                                    cfg = dict(conn.config_json or {})
                                    params = resolve_ptu_collection_params(
                                        task_json=task_json,
                                        config=cfg,
                                        billing_periods=periods,
                                    )
                                    payload = run_ptu_collection(
                                        collect_session,
                                        conn.id,
                                        lookback_hours=params["lookback_hours"],
                                        backfill_days=params["backfill_days"],
                                        periods=params["periods"],
                                        task_id=task_id,
                                    )
                                finally:
                                    collect_session.close()
                                total_written += int((payload.get("persisted") or {}).get("gold_metrics") or 0)
                            result = {
                                "success": True,
                                "rows_written": total_written,
                                "table": "dest_azure_ptu_metrics",
                            }
                        else:
                            # Validate the exact source/month immediately before
                            # transformation.  This avoids a second full Bronze
                            # pass while preserving the unit-level safety barrier.
                            if task_json.get("bronze_audit_task_id"):
                                from app.routers.etl import _audit_reprocess_group_sync
                                inline_audit = _audit_reprocess_group_sync(
                                    task_id, dest_id, conn_ids, periods
                                )
                                if not inline_audit.get("success"):
                                    result = inline_audit
                                    raise RuntimeError(inline_audit.get("error") or "Auditoria Bronze bloqueada")
                            # Seed the in-memory task dict that _reprocess_destination_sync
                            # uses for progress tracking and _sync_db back into the DB row.
                            _inmemory_tasks[task_id] = {
                                "id": task_id,
                                "type": "reprocess",
                                "status": "RUNNING",
                                "task_type": "reprocess",
                                "destination_table_id": dest_id,
                                "connection_ids": conn_ids,
                                "billing_periods": periods,
                                "total_blobs": 0,
                                "processed_blobs": 0,
                                "rows_destination": 0,
                                "current_blob": "inicializando",
                                "table": "",
                                "errors": [],
                            }
                            _append_reprocess_log(task_id, "Worker assumiu a tarefa da fila")

                            from app.services.write_lock import (
                                exclusive_write_lock,
                                task_holds_exclusive_warehouse_flock,
                            )

                            def _run_reprocess_job():
                                if task.task_type == "reclassification":
                                    return _reclassify_destination_sync(
                                        task_id, dest_id, conn_ids, periods,
                                    )
                                payload = task.result_json or {}
                                snapshot_dates = [
                                    str(s) for s in (payload.get("snapshot_dates") or [])
                                ]
                                return _reprocess_destination_sync(
                                    task_id,
                                    dest_id,
                                    conn_ids,
                                    periods,
                                    replace_existing=bool(payload.get("replace_existing", False)),
                                    publish_by_partition=bool(
                                        payload.get("publish_by_partition", True)
                                        or (
                                            payload.get("scheduled_pipeline")
                                            and len(task.connection_ids or []) == 1
                                            and (
                                                len(task.billing_periods or []) == 1
                                                or len(snapshot_dates) == 1
                                            )
                                        )
                                    ),
                                    snapshot_dates=snapshot_dates or None,
                                )

                            if task_holds_exclusive_warehouse_flock(task):
                                with exclusive_write_lock():
                                    result = _run_reprocess_job()
                            else:
                                result = _run_reprocess_job()
                        print(f"Task {task_id} completed: {result}")

                        # Persist the terminal state explicitly.  The
                        # processing function updates progress incrementally,
                        # but the worker owns task completion across process
                        # boundaries and must never leave a returned task as
                        # RUNNING in PostgreSQL.
                        terminal_session = SyncSession()
                        try:
                            terminal_task = terminal_session.get(EtlTask, task_id)
                            if terminal_task:
                                succeeded = bool(result.get("success"))
                                terminal_task.status = "COMPLETED" if succeeded else "FAILED"
                                terminal_task.rows_destination = int(result.get("rows_written") or 0)
                                error_text = str(result.get("error") or "Falha ao reprocessar")
                                terminal_task.phase = (
                                    f"Concluido: {terminal_task.rows_destination:,} linhas"
                                    if succeeded else error_text
                                )[:200]
                                terminal_task.current_blob = (
                                    f"Concluido: {terminal_task.rows_destination:,} linhas"
                                    if succeeded else error_text
                                )[:500]
                                existing_result = terminal_task.result_json if isinstance(terminal_task.result_json, dict) else {}
                                terminal_task.result_json = {
                                    **existing_result,
                                    "table": result.get("table", existing_result.get("table", "")),
                                    "rows_written": terminal_task.rows_destination,
                                    "error": "" if succeeded else str(result.get("error") or "Falha ao reprocessar"),
                                }
                                if not succeeded:
                                    context = _failure_context(result)
                                    terminal_task.errors = [
                                        str(result.get("error") or "Falha ao reprocessar")[:2000]
                                    ]
                                    terminal_task.result_json["failure_context"] = context
                                    terminal_task.result_json["failure_kind"] = "reprocess_error"
                                # Opção "Materializar Gold na sequência": quando o
                                # reprocessamento pediu chain_gold_cubes, só a ÚLTIMA
                                # tarefa do grupo enfileira a Gold (visão completa
                                # = realizado + forecast + rateio) na fila do worker.
                                if succeeded:
                                    if (terminal_task.result_json or {}).get("scheduled_pipeline"):
                                        scheduled_forecasts = _queue_scheduled_forecasts(task_id, terminal_task.result_json or {}, terminal_session)
                                        if scheduled_forecasts:
                                            terminal_task.phase = ((terminal_task.phase or "") + f" · Forecast(s) na fila: {len(scheduled_forecasts)}")[:200]
                                    payload = terminal_task.result_json or {}
                                    group = payload.get("reprocess_group_id") or payload.get("queue_group")
                                    position = payload.get("queue_position")
                                    total = payload.get("queue_total")
                                    is_last_group_task = (
                                        position is not None and total is not None and position == total
                                    )
                                    chain = payload.get("chain_gold_cubes")
                                    if not is_last_group_task and group and chain and chain.get("enabled"):
                                        from sqlalchemy import select as _select
                                        group_rows = terminal_session.execute(
                                            _select(EtlTask.id, EtlTask.result_json).where(
                                                EtlTask.task_type == "reprocess",
                                                EtlTask.status.in_(["QUEUED", "RUNNING"]),
                                            )
                                        ).all()
                                        remaining = [
                                            rid for rid, rj in group_rows
                                            if rid != task_id and (
                                                (rj or {}).get("reprocess_group_id") == group
                                                or (rj or {}).get("queue_group") == group
                                            )
                                        ]
                                        is_last_group_task = not remaining

                                    if succeeded and is_last_group_task:
                                        from sqlalchemy import select as _select
                                        from app.database import run_async_isolated
                                        from app.services.contract_settlement_materializer import (
                                            recalculate_contracts_for_connections,
                                        )
                                        group_tasks = terminal_session.execute(
                                            _select(EtlTask.connection_ids, EtlTask.result_json, EtlTask.status).where(
                                                EtlTask.task_type == "reprocess",
                                            )
                                        ).all()
                                        group_rows = [
                                            (connection_ids, task_json, status)
                                            for connection_ids, task_json, status in group_tasks
                                            if (task_json or {}).get("reprocess_group_id") == group
                                            or (task_json or {}).get("queue_group") == group
                                        ] if group else []
                                        group_succeeded = (
                                            all(status == "COMPLETED" for _, _, status in group_rows)
                                            if group else True
                                        )
                                        connection_ids = sorted({
                                            int(connection_id)
                                            for connection_list, _, _ in group_rows
                                            for connection_id in (connection_list or [])
                                        }) if group else list(terminal_task.connection_ids or [])
                                        from app.models.financial_contract import (
                                            FinancialContract,
                                            FinancialContractConnection,
                                        )
                                        has_financial_contract = bool(connection_ids) and terminal_session.scalar(
                                            _select(FinancialContract.id)
                                            .join(FinancialContractConnection)
                                            .where(
                                                FinancialContractConnection.connection_id.in_(connection_ids),
                                                FinancialContract.active.is_(True),
                                            )
                                            .limit(1)
                                        ) is not None
                                        try:
                                            if has_financial_contract and not group_succeeded:
                                                raise RuntimeError(
                                                    "Grupo de reprocessamento contém competência não concluída com sucesso"
                                                )
                                            settlement_runs = (
                                                run_async_isolated(
                                                    recalculate_contracts_for_connections(
                                                        connection_ids, source="REPROCESS_GROUP"
                                                    )
                                                )
                                                if has_financial_contract and group_succeeded
                                                else []
                                            )
                                            if settlement_runs:
                                                payload["contract_settlement_runs"] = settlement_runs
                                                terminal_task.result_json = payload
                                                terminal_task.phase = (
                                                    (terminal_task.phase or "")
                                                    + " · Liquidação contratual publicada"
                                                )[:200]
                                            elif (
                                                group_succeeded
                                                and chain
                                                and chain.get("enabled")
                                            ):
                                                from app.services.gold_materialization import enqueue_rule_materialization
                                                gold_task = enqueue_rule_materialization(
                                                    terminal_session,
                                                    rule_ids=chain.get("rule_ids") or None,
                                                    source="reprocess",
                                                )
                                                if gold_task:
                                                    terminal_task.phase = (
                                                        (terminal_task.phase or "")
                                                        + f" · Gold enfileirada (task #{gold_task.id})"
                                                    )[:200]
                                        except Exception as settlement_error:
                                            succeeded = False
                                            terminal_task.status = "FAILED"
                                            terminal_task.errors = [
                                                f"Liquidação contratual falhou: {settlement_error}"[:2000]
                                            ]
                                            terminal_task.phase = (
                                                f"Silver publicada; liquidação contratual falhou: {settlement_error}"
                                            )[:200]
                                            terminal_task.result_json = {
                                                **(terminal_task.result_json or {}),
                                                "contract_settlement_error": str(settlement_error)[:2000],
                                                "failure_kind": "contract_settlement_error",
                                            }
                                    elif succeeded and is_last_group_task and chain and chain.get("enabled"):
                                        from app.services.gold_materialization import enqueue_rule_materialization
                                        gold_task = enqueue_rule_materialization(
                                            terminal_session,
                                            rule_ids=chain.get("rule_ids") or None,
                                            source="reprocess",
                                        )
                                        if gold_task:
                                            terminal_task.phase = (
                                                (terminal_task.phase or "")
                                                + f" · Gold enfileirada (task #{gold_task.id})"
                                            )[:200]
                                terminal_task.finished_at = datetime.now(timezone.utc)
                                terminal_session.commit()
                        finally:
                            terminal_session.close()
                        _append_reprocess_log(task_id, "Tarefa encerrada pelo worker" if succeeded else "Worker encerrou a tarefa com falha", level="INFO" if succeeded else "ERROR")

                        # Clean up in-memory entry
                        _inmemory_tasks.pop(task_id, None)
                        # Reprocesses materialize sizeable Pandas/Arrow batches.  Release
                        # completed-task allocations before the next queue item so an
                        # allocator-retained RSS does not trip the per-worker guard.
                        import gc
                        gc.collect()
                        try:
                            import pyarrow as pa
                            pa.default_memory_pool().release_unused()
                        except Exception:
                            pass
                        _maybe_recycle_reprocess_worker(failed=not succeeded)

                    except Exception as e:
                        print(f"Task {task_id} FAILED: {e}")
                        import traceback
                        traceback.print_exc()

                        # Mark task as FAILED in DB
                        s2 = SyncSession()
                        try:
                            t2 = s2.get(EtlTask, task_id)
                            if t2:
                                t2.status = "FAILED"
                                t2.phase = f"Erro: {e}"[:200]
                                t2.current_blob = f"Erro: {e}"[:500]
                                t2.errors = [str(e)[:2000]]
                                t2.result_json = {
                                    **(t2.result_json or {}),
                                    "error": str(e),
                                    "failure_kind": "worker_exception",
                                }
                                s2.commit()
                            try:
                                from app.routers.etl import _append_reprocess_log
                                _append_reprocess_log(task_id, f"Erro não tratado no worker: {e}", level="ERROR")
                            except Exception:
                                pass
                        finally:
                            s2.close()

                        _inmemory_tasks.pop(task_id, None)
                        import gc
                        gc.collect()
                        try:
                            import pyarrow as pa
                            pa.default_memory_pool().release_unused()
                        except Exception:
                            pass
                        _maybe_recycle_reprocess_worker(failed=True)

                elif task.task_type == "shared_cost":
                    task.status = "RUNNING"
                    task.phase = "Agregando Gold e forecast e gravando linhas de rateio"
                    session.commit()
                    session.close()
                    try:
                        from app.services.shared_cost import run_shared_cost_job_sync
                        s = SyncSession()
                        result = run_shared_cost_job_sync(s)
                        snapshot = result["snapshot"]
                        row = s.get(EtlTask, task_id)
                        if row:
                            payload = dict(row.result_json or {})
                            payload["snapshot"] = snapshot
                            payload["planning_table"] = result.get("planning_table")
                            payload["rateio_rows"] = result.get("rows")
                            row.result_json = payload
                            row.status = "COMPLETED"
                            row.phase = (
                                f"{result.get('rows', 0)} linhas de rateio em {result.get('planning_table')} · "
                                f"{len(snapshot['shared_nodes'])} custo(s) compartilhado(s)"
                            )
                            row.processed_blobs = len(snapshot["shared_nodes"])
                            row.rows_destination = int(result.get("rows") or 0)
                            row.finished_at = datetime.now(timezone.utc)
                            s.commit()
                        s.close()
                    except Exception as exc:
                        s2 = SyncSession()
                        try:
                            t2 = s2.get(EtlTask, task_id)
                            if t2:
                                t2.status = "FAILED"
                                t2.phase = f"Erro: {exc}"[:200]
                                t2.current_blob = f"Erro: {exc}"[:500]
                                t2.finished_at = datetime.now(timezone.utc)
                                s2.commit()
                        finally:
                            s2.close()

                elif task.task_type == "reservation_collection":
                    # Coleta EXCLUSIVA de benefícios Azure (RI + Savings Plans) via
                    # ARM → Bronze. NÃO toca nos blobs de custo: reservas vivem em
                    # outra camada bronze da mesma conexão e não devem re-disparar
                    # o ETL de custo já ingerido.
                    conn_id = task.connection_id
                    print(
                        f"Picked up reservation_collection task {task_id}:"
                        f" connection={conn_id}"
                    )
                    task.status = "RUNNING"
                    session.commit()
                    session.close()
                    try:
                        from app.database import SyncSession as _ResSession
                        from app.services.azure_reservation_service import run_reservation_collection
                        _res_session = _ResSession()
                        try:
                            result = run_reservation_collection(
                                _res_session, conn_id, task_id=task_id
                            )
                            counts = result.get("counts") or {}
                            persisted = result.get("persisted") or {}
                            t2 = _res_session.get(EtlTask, task_id)
                            if t2:
                                t2.status = "COMPLETED"
                                t2.phase = (
                                    f"Reservas Azure concluído: {counts.get('benefits_total', 0)} benefícios "
                                    f"({counts.get('reserved_instances', 0)} RI · "
                                    f"{counts.get('savings_plans', 0)} SP)"
                                )[:200]
                                t2.rows_raw = int(result.get("catalog_rows") or 0)
                                t2.rows_destination = int(persisted.get("catalog") or 0) + int(persisted.get("utilization") or 0)
                                t2.errors = []
                                t2.result_json = {
                                    "counts": counts,
                                    "persisted": persisted,
                                    "discovery": result.get("discovery", []),
                                    "source": result.get("source"),
                                    "collection_period": result.get("collection_period"),
                                }
                                t2.finished_at = datetime.now(timezone.utc)
                                _res_session.commit()
                        finally:
                            _res_session.close()
                        print(f"Task {task_id} (reservation_collection) completed: {result}")
                    except Exception as e:
                        print(f"Reservation collection task {task_id} FAILED: {e}")
                        import traceback
                        traceback.print_exc()
                        s2 = SyncSession()
                        try:
                            t2 = s2.get(EtlTask, task_id)
                            if t2:
                                t2.status = "FAILED"
                                t2.phase = f"Erro: {e}"[:200]
                                t2.current_blob = f"Erro: {e}"[:500]
                                t2.finished_at = datetime.now(timezone.utc)
                                s2.commit()
                        finally:
                            s2.close()

                elif task.task_type == "azure_pricesheet_refresh":
                    print(f"Picked up azure_pricesheet_refresh task {task_id}")
                    source_id = task.connection_id
                    session.commit()
                    session.close()
                    try:
                        from app.models.source_connection import SourceConnection as _SourceConnection
                        _lookup = SyncSession()
                        try:
                            _source = _lookup.get(_SourceConnection, source_id)
                            if _source is None or _source.code != "azure-pricesheet":
                                raise RuntimeError("SourceConnection azure-pricesheet não encontrada")
                            _source_code, _source_name = _source.code, _source.name
                        finally:
                            _lookup.close()
                        from app.services.azure_pricesheet import refresh_snapshot
                        _result = refresh_snapshot(source_id, _source_code, _source_name)
                        _done = SyncSession()
                        try:
                            _task = _done.get(EtlTask, task_id)
                            if _task:
                                _task.status = "COMPLETED"
                                _task.phase = f"Price Sheet Bronze ativo · {_result['rows']:,} linhas"[:200]
                                _task.rows_raw = int(_result.get("bronze_rows", _result["rows"]))
                                _task.rows_destination = int(_result.get("silver_rows", _result["rows"]))
                                _task.result_json = {**dict(_task.result_json or {}), **_result}
                                _task.errors = []
                                _task.finished_at = datetime.now(timezone.utc)
                                _done.commit()
                        finally:
                            _done.close()
                    except Exception as exc:
                        logger.exception("Azure Price Sheet refresh task %s failed", task_id)
                        _failed = SyncSession()
                        try:
                            _task = _failed.get(EtlTask, task_id)
                            if _task:
                                _task.status = "FAILED"
                                _task.phase = f"Erro Price Sheet: {exc}"[:200]
                                _task.current_blob = str(exc)[:500]
                                _task.errors = [str(exc)[:1000]]
                                _task.finished_at = datetime.now(timezone.utc)
                                _failed.commit()
                        finally:
                            _failed.close()

                elif task.task_type == "price_reference_collection":
                    meter_id = str((task.result_json or {}).get("meter_id") or "")
                    print(f"Disabling legacy per-meter price lookup task {task_id}: meter={meter_id}")
                    session.commit()
                    session.close()
                    s2 = SyncSession()
                    try:
                        t2 = s2.get(EtlTask, task_id)
                        if t2:
                            t2.status = "CANCELLED"
                            t2.phase = "Cancelada: coleta por meter substituída pelo snapshot azure-pricesheet"
                            t2.errors = []
                            t2.finished_at = datetime.now(timezone.utc)
                            s2.commit()
                    finally:
                        s2.close()

                elif task.task_type == "extraction":
                    conn_id = task.connection_id
                    periods = task.billing_periods or []
                    selected = task.selected_blobs or []
                    from app.models.source_connection import SourceConnection as _LogSourceConnection
                    source_row = session.get(_LogSourceConnection, conn_id) if conn_id else None
                    source_code = source_row.code if source_row else f"connection-{conn_id}"
                    # Com uma conexão hospedando várias fontes, quem define a
                    # Bronze e o coletor é a fonte da task — não o connector da
                    # conexão, que descreve só o transporte principal dela.
                    _task_source_key = str(
                        (getattr(task, "result_json", None) or {}).get("source_key") or ""
                    )
                    _task_result = dict(getattr(task, "result_json", None) or {})
                    _metric_state_meta = {**_task_result, "connection_id": conn_id}
                    _task_window_days = int(_task_result.get("window_days") or 0)
                    _task_collection_until = (
                        datetime.fromisoformat(_task_result["collection_until"])
                        if _task_result.get("collection_until") else None
                    )
                    _source_connector = ""
                    if _task_source_key:
                        try:
                            from app.services.azure_source_registry import (
                                AZURE_SOURCE_CATALOG as _AZ_CATALOG,
                                source_code as _source_code_of,
                            )
                            source_code = _source_code_of(source_code, _task_source_key)
                            _entry = next(
                                (e for e in _AZ_CATALOG if e["source_key"] == _task_source_key),
                                None,
                            )
                            _source_connector = (_entry or {}).get("connector_type") or ""
                        except Exception:
                            pass

                    print(
                        f"Picked up extraction task {task_id}:"
                        f" source={source_code} (connection={conn_id}),"
                        f" periods={periods},"
                        f" selected_blobs={len(selected)} blobs"
                    )

                    # Mark as RUNNING so no other worker picks it up
                    task.status = "RUNNING"
                    session.commit()
                    session.close()

                    try:
                        from app.services.task_runner import _run_etl_sync, _inmemory_tasks

                        # Seed the in-memory task dict that _run_etl_sync
                        # uses for progress tracking.
                        _inmemory_tasks[task_id] = {
                            "id": task_id,
                            "connection_id": conn_id,
                            "status": "RUNNING",
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

                        # Azure PTU is collected from ARM/Monitor and does
                        # not have blobs.  Keep it in the same extraction
                        # queue so the UI can start/monitor it, while leaving
                        # the existing blob ETL untouched.
                        from app.database import SyncSession as _PtuSession
                        _ptu_session = _PtuSession()
                        try:
                            from app.models.source_connection import SourceConnection as _PtuConnection
                            _ptu_conn = _ptu_session.get(_PtuConnection, conn_id)
                            # O connector **da fonte** vence o da conexão: é ele que
                            # decide o coletor. Antes esta vista só era montada dentro
                            # do ramo de Automation, então as demais fontes nunca
                            # chegavam ao seu próprio coletor e caíam no ETL de blob.
                            if _ptu_conn is not None and _source_connector:
                                class _SourceConn:
                                    """Vista da conexão com o connector da fonte."""
                                    def __init__(self, base, connector):
                                        self._base = base
                                        self.connector_type = connector
                                    def __getattr__(self, name):
                                        return getattr(self._base, name)
                                _ptu_conn = _SourceConn(_ptu_conn, _source_connector)

                            if _ptu_conn and _ptu_conn.connector_type == "azure_automation_jobs":
                                # Azure Automation é coletado via ARM (jobs e
                                # schedules) e persistido na Bronze, seguindo o
                                # mesmo padrão de coleta do PTU.
                                from app.services.azure_automation_service import run_automation_collection
                                _cfg = dict(_ptu_conn.config_json or {})
                                _backfill = int(_cfg.get("backfill_days") or 93)
                                try:
                                    _result = run_automation_collection(
                                        _ptu_session,
                                        _ptu_conn.id,
                                        backfill_days=_backfill,
                                        task_id=task_id,
                                        source_code=source_code,
                                    )
                                except Exception as _coll_exc:
                                    import traceback as _tb
                                    _tb.print_exc()
                                    _t_fail = _ptu_session.get(EtlTask, task_id)
                                    if _t_fail:
                                        _t_fail.status = "FAILED"
                                        _t_fail.phase = f"Azure Automation coleta falhou: {str(_coll_exc)[:400]}"
                                        _t_fail.errors = [str(_coll_exc)[:1000]]
                                        _t_fail.finished_at = datetime.now(timezone.utc)
                                        _ptu_session.commit()
                                    _inmemory_tasks[task_id]["status"] = "FAILED"
                                    _inmemory_tasks.pop(task_id, None)
                                    continue
                                _t = _ptu_session.get(EtlTask, task_id)
                                if _t:
                                    _t.status = "COMPLETED"
                                    _t.phase = (
                                        f"Azure Automation: {_result.get('jobs_collected', 0)} jobs / "
                                        f"{_result.get('schedules_collected', 0)} schedules em "
                                        f"{_result.get('accounts', 0)} conta(s)"
                                    )
                                    _t.rows_raw = _result.get("jobs_bronze", 0) + _result.get("schedules_bronze", 0)
                                    _t.billing_periods = _result.get("billing_periods") or []
                                    _t.result_json = _result
                                    _t.finished_at = datetime.now(timezone.utc)
                                    _ptu_session.commit()
                                _inmemory_tasks[task_id]["status"] = "COMPLETED"
                                _inmemory_tasks.pop(task_id, None)
                                continue
                            if _ptu_conn and _ptu_conn.connector_type == "azure_resource_graph":
                                # Azure Resource Graph é coletado via ARG (inventário
                                # e regras de órfãos) e persistido na Bronze, seguindo
                                # o mesmo padrão de coleta do PTU/Automation.
                                from app.services.azure_resource_graph_service import run_resource_graph_collection
                                try:
                                    _arg_result = run_resource_graph_collection(
                                        _ptu_session,
                                        _ptu_conn.id,
                                        task_id=task_id,
                                        source_code=source_code,
                                    )
                                except Exception as _arg_exc:
                                    import traceback as _tb_arg
                                    _tb_arg.print_exc()
                                    _t_arg_fail = _ptu_session.get(EtlTask, task_id)
                                    if _t_arg_fail:
                                        _t_arg_fail.status = "FAILED"
                                        _t_arg_fail.phase = f"Azure Resource Graph coleta falhou: {str(_arg_exc)[:400]}"
                                        _t_arg_fail.errors = [str(_arg_exc)[:1000]]
                                        _t_arg_fail.finished_at = datetime.now(timezone.utc)
                                        _ptu_session.commit()
                                    _inmemory_tasks[task_id]["status"] = "FAILED"
                                    _inmemory_tasks.pop(task_id, None)
                                    continue
                                _t_arg = _ptu_session.get(EtlTask, task_id)
                                if _t_arg:
                                    _t_arg.status = "COMPLETED"
                                    _t_arg.phase = (
                                        f"Azure Resource Graph: {_arg_result.get('inventory_rows', 0)} recursos / "
                                        f"{sum((_arg_result.get('orphan_counts') or {}).values())} órfãos "
                                        f"({len(_arg_result.get('orphan_counts') or {})} regras)"
                                    )
                                    _t_arg.rows_raw = _arg_result.get("bronze_rows", 0)
                                    _t_arg.billing_periods = [_arg_result.get("billing_period") or ""]
                                    _t_arg.result_json = _arg_result
                                    _t_arg.finished_at = datetime.now(timezone.utc)
                                    _ptu_session.commit()
                                _inmemory_tasks[task_id]["status"] = "COMPLETED"
                                _inmemory_tasks.pop(task_id, None)
                                continue
                            if _ptu_conn and _ptu_conn.connector_type == "azure_openai_ptu":
                                from app.services.azure_ptu_service import run_ptu_collection, resolve_ptu_collection_params
                                _ptu_task = _ptu_session.get(EtlTask, task_id)
                                _task_json = (_ptu_task.result_json or {}) if _ptu_task else {}
                                _cfg = dict(_ptu_conn.config_json or {})
                                _params = resolve_ptu_collection_params(
                                    task_json=_task_json,
                                    config=_cfg,
                                    billing_periods=periods or None,
                                    session=_ptu_session,
                                    connection_id=_ptu_conn.id,
                                )
                                _result = run_ptu_collection(
                                    _ptu_session,
                                    _ptu_conn.id,
                                    lookback_hours=_params["lookback_hours"],
                                    backfill_days=_params["backfill_days"],
                                    periods=_params["periods"],
                                    task_id=task_id,
                                )
                                _ptu_metrics = _result.get("metrics") or []
                                _ptu_periods = _result.get("billing_periods") or []
                                _persisted = _result.get("persisted") or {}
                                _backfill_days = _params["backfill_days"]
                                _selected_periods = _params["periods"]
                                t2 = _ptu_session.get(EtlTask, task_id)
                                if t2:
                                    t2.status = "COMPLETED"
                                    t2.phase = (
                                        f"Azure PTU backfill: {len(_ptu_metrics)} registros"
                                        if _backfill_days
                                        else (
                                            f"Azure PTU período: {len(_ptu_metrics)} registros"
                                            if _selected_periods
                                            else f"Azure PTU: {len(_ptu_metrics)} registros em {len(_ptu_periods)} mês(es)"
                                        )
                                    )
                                    t2.billing_periods = _ptu_periods
                                    t2.rows_raw = _persisted.get("bronze", 0)
                                    t2.rows_destination = _persisted.get("gold_metrics", 0)
                                    t2.errors = []
                                    t2.result_json = {
                                        "inventory": _result.get("inventory"),
                                        "metrics_count": len(_ptu_metrics),
                                        "source": _result.get("source"),
                                        "billing_periods": _ptu_periods,
                                        "persisted": _persisted,
                                        "backfill_days": _backfill_days,
                                        "periods": _selected_periods,
                                    }
                                    t2.finished_at = datetime.now(timezone.utc)
                                    _ptu_session.commit()
                                _inmemory_tasks[task_id]["status"] = "COMPLETED"
                                _inmemory_tasks.pop(task_id, None)
                                continue
                        finally:
                            _ptu_session.close()

                        if _is_etl_task_cancelled(task_id):
                            _inmemory_tasks.pop(task_id, None)
                            continue
                        if _is_etl_task_cancelled(task_id):
                            _inmemory_tasks.pop(task_id, None)
                            continue
                        _run_etl_sync(task_id, conn_id, selected or None, periods)
                        followups = _queue_schedule_destination_tasks(task_id, conn_id, task.result_json or {})
                        if followups:
                            followup_session = SyncSession()
                            try:
                                completed = followup_session.get(EtlTask, task_id)
                                if completed:
                                    completed.phase = f"Bronze concluída · {len(followups)} Gold(s) na fila"
                                    completed.result_json = {**(completed.result_json or {}), "followup_task_ids": followups}
                                    followup_session.commit()
                            finally:
                                followup_session.close()

                        # Clean up in-memory entry
                        _inmemory_tasks.pop(task_id, None)

                    except Exception as e:
                        print(f"Extraction task {task_id} FAILED: {e}")
                        import traceback
                        traceback.print_exc()

                        # Mark task as FAILED in DB
                        s2 = SyncSession()
                        try:
                            t2 = s2.get(EtlTask, task_id)
                            if t2:
                                t2.status = "FAILED"
                                t2.phase = f"Erro: {e}"[:200]
                                t2.current_blob = f"Erro: {e}"[:500]
                                t2.finished_at = datetime.now(timezone.utc)
                                s2.commit()
                        finally:
                            s2.close()

                        _inmemory_tasks.pop(task_id, None)

            finally:
                try:
                    release_task_resource_locks(lock_session, task)
                except Exception:
                    pass
                try:
                    lock_session.close()
                except Exception:
                    pass

        except Exception as e:
            print(f"Worker polling error: {e}")
            import traceback
            traceback.print_exc()
            try:
                session.close()
            except Exception:
                pass
            time.sleep(POLL_INTERVAL)

    print(f"Worker #{worker_id} stopped.")


def _spawn_consumer(worker_id: int, manager_pid: int, manager_created_at: float) -> None:
    # Register inside the child as well: a manager can die between spawn and
    # its own registry write, leaving an otherwise invisible lock holder.
    register_consumer(os.getpid(), manager_pid, parent_created_at=manager_created_at)
    os.environ["OPENFINOPS_WORKER_ID"] = str(worker_id)
    consumer_main(worker_id)


def _terminate_consumer(proc: multiprocessing.Process, *, grace_seconds: float = 5.0) -> None:
    """Stop a synchronous consumer so its DB and filesystem locks are released."""
    if not proc.is_alive():
        proc.join(timeout=0)
        return
    proc.terminate()
    proc.join(timeout=grace_seconds)
    if proc.is_alive():
        proc.kill()
        proc.join(timeout=grace_seconds)


_CONSUMER_CPU_SAMPLES: dict[int, tuple[float, float]] = {}


def _watch_stalled_consumers(processes: dict[int, multiprocessing.Process]) -> None:
    """Kill only the child that owns a stale task, then recover that task row."""
    from app.services.worker_runtime import note_cpu_sample, process_cpu_seconds

    timeout_seconds = max(60, int(os.getenv("WORKER_TASK_STALL_TIMEOUT_SECONDS", "900")))
    for proc in list(processes.values()):
        if not proc.is_alive() or proc.pid is None:
            continue

        session = SyncSession()
        try:
            task = find_stalled_worker_task(
                session,
                proc.pid,
                stale_after_seconds=timeout_seconds,
            )
            task_id = task.id if task is not None else None
            if task is not None and note_cpu_sample(
                _CONSUMER_CPU_SAMPLES,
                proc.pid,
                process_cpu_seconds(proc.pid),
                now=time.monotonic(),
                idle_after_seconds=timeout_seconds,
            ):
                task.last_progress_at = datetime.now(timezone.utc)
                session.commit()
                task_id = None
            else:
                session.rollback()
        except Exception:
            logger.exception("Could not check progress for worker pid %s", proc.pid)
            session.close()
            continue
        finally:
            try:
                session.close()
            except Exception:
                pass

        if task_id is None:
            continue

        logger.error(
            "Worker pid %s made no task progress for %ss; terminating task %s",
            proc.pid,
            timeout_seconds,
            task_id,
        )
        _terminate_consumer(proc)
        if proc.is_alive():
            logger.critical("Worker pid %s did not exit after termination", proc.pid)
            continue

        recovery = SyncSession()
        try:
            from app.models.etl_task import EtlTask

            row = recovery.get(EtlTask, task_id)
            if row is None or row.status != "RUNNING" or row.worker_pid != proc.pid:
                recovery.rollback()
                continue
            outcome = recover_stalled_task(
                recovery,
                row,
                reason=(
                    f"Sem progresso persistido por {timeout_seconds}s; "
                    "subprocesso encerrado para liberar a fila"
                ),
            )
            recovery.commit()
            logger.warning(
                "Stalled task %s recovered as %s (attempt %s)",
                task_id,
                outcome,
                row.recovery_attempts,
            )
        except Exception:
            recovery.rollback()
            logger.exception("Could not recover stalled task %s", task_id)
        finally:
            recovery.close()


def supervisor_main() -> None:
    global running
    concurrency = max(1, int(os.getenv("OPENFINOPS_WORKER_CONCURRENCY", "3")))
    runtime_dir().mkdir(parents=True, exist_ok=True)
    supervisor_pid_path().write_text(str(os.getpid()))
    print(
        f"FinOps Worker supervisor started with concurrency={concurrency}",
        flush=True,
    )

    startup = SyncSession()
    try:
        recovered_locks = terminate_stale_advisory_lock_holders(
            startup,
            max_age_seconds=int(os.getenv("OPENFINOPS_STALE_ADVISORY_LOCK_SECONDS", "60")),
        )
        if recovered_locks:
            print(f"Released {recovered_locks} stale PostgreSQL advisory lock holder(s)", flush=True)
        # A previous manager may have died while its children are still alive.
        # The independent supervisor stops those registered children before
        # recovering their rows. Never reset RUNNING rows on manager startup.
    finally:
        startup.close()

    ctx = multiprocessing.get_context("spawn")
    manager_created_at = psutil.Process(os.getpid()).create_time()
    processes: dict[int, multiprocessing.Process] = {}
    while running:
        _watch_stalled_consumers(processes)
        for worker_id in range(concurrency):
            proc = processes.get(worker_id)
            if proc is not None and proc.is_alive():
                continue
            if proc is not None:
                proc.join(timeout=1)
                if proc.pid is not None:
                    recovery = SyncSession()
                    try:
                        orphaned = recover_worker_orphaned_tasks(recovery, proc.pid)
                        if orphaned:
                            recovery.commit()
                            logger.warning(
                                "Recovered tasks after worker pid %s exited: %s",
                                proc.pid,
                                orphaned,
                            )
                        else:
                            recovery.rollback()
                    except Exception:
                        recovery.rollback()
                        logger.exception("Could not recover tasks from exited worker pid %s", proc.pid)
                    finally:
                        recovery.close()
                consumer_record_path(proc.pid).unlink(missing_ok=True)
            proc = ctx.Process(
                target=_spawn_consumer,
                args=(worker_id, os.getpid(), manager_created_at),
                name=f"openfinops-worker-{worker_id}",
            )
            proc.start()
            try:
                register_consumer(proc.pid, os.getpid(), parent_created_at=manager_created_at)
            except psutil.NoSuchProcess:
                logger.warning("Worker child %s exited before registration", proc.pid)
            processes[worker_id] = proc
        time.sleep(2)

    for proc in processes.values():
        _terminate_consumer(proc)
        if not proc.is_alive() and proc.pid is not None:
            recovery = SyncSession()
            try:
                recover_worker_orphaned_tasks(recovery, proc.pid)
                recovery.commit()
                consumer_record_path(proc.pid).unlink(missing_ok=True)
            finally:
                recovery.close()
    print("FinOps Worker supervisor stopped.")


def main() -> None:
    worker_id = os.getenv("OPENFINOPS_WORKER_ID")
    if worker_id is not None:
        consumer_main(int(worker_id))
        return

    # Always supervise consumers, including concurrency=1. Reprocess workers
    # recycle with sys.exit() to drop RSS; that must not kill the PID that
    # start-local.sh watches.
    supervisor_main()


if __name__ == "__main__":
    main()
