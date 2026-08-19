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
import threading
import time
import multiprocessing
import fcntl
from datetime import date, datetime, timezone
from pathlib import Path

# Ensure the worker can import app.* modules when run from apps/api/
sys.path.insert(0, os.path.dirname(__file__))

# ── Pre-import ALL models so SQLAlchemy Base.metadata is fully populated ──
# The sync engine is created at module-import time in database.py with an
# empty metadata. Importing every model file here ensures all __tablename__
# registrations happen before worker queries reference foreign keys.
from app.database import SyncSession
from app.models.etl_task import EtlTask
from app.models.destination import DestinationTable, DestinationColumn, SourceDestinationMapping
from app.models.source_connection import SourceConnection
from app.models.ingested_blob import IngestedBlob
from app.models.rules import BusinessRule, ClassificationRule, Department, Project
from app.models.skipped_blob import SkippedBlob
from app.models.fact_cost import FactCost
from app.models.period_status import PeriodStatus
from app.models.forecast import ForecastConfig, ForecastAdjustment, ForecastBusinessRule, ForecastRun
from sqlalchemy import select, text

POLL_INTERVAL = 2  # seconds between polls when idle
HEARTBEAT = Path(os.getenv("OPENFINOPS_WORKER_HEARTBEAT", "/tmp/openfinops-worker.heartbeat"))
WORKER_PID = Path(os.getenv("OPENFINOPS_WORKER_PID", "/tmp/openfinops-worker.pid"))
WORKER_LOCK = Path(os.getenv("OPENFINOPS_WORKER_LOCK", "/tmp/openfinops-worker.lock"))

running = True


def _format_task_error(exc: Exception) -> str:
    """Keep persisted worker errors readable when httpx raises empty messages."""
    message = str(exc).strip()
    if message:
        return message
    name = type(exc).__name__
    if name in {"ReadError", "ConnectError", "RemoteProtocolError"}:
        return f"{name}: conexão com Trino foi fechada (coordinator reiniciando ou sob carga)"
    return name


def _heartbeat_process(parent_pid: int):
    """Update liveness outside the worker GIL and stop with the parent."""
    # ``spawn`` imports this module and would otherwise inherit/register the
    # worker's graceful SIGTERM handler.  A Process.terminate() must terminate
    # this tiny child immediately so the main worker can actually exit and the
    # supervisor can restart it.
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    while True:
        try:
            os.kill(parent_pid, 0)
        except OSError:
            break
        try:
            HEARTBEAT.touch()
        except OSError:
            pass
        time.sleep(2)


def shutdown(sig, frame):
    global running
    running = False
    print("\nWorker received shutdown signal. Finishing current task (if any) then exiting...")


signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)


def main():
    lock_handle = WORKER_LOCK.open("a+")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another FinOps Worker already owns the worker lock; exiting.", flush=True)
        return
    WORKER_PID.write_text(str(os.getpid()))
    print("FinOps Worker started. Polling for QUEUED tasks every", POLL_INTERVAL, "seconds...", flush=True)
    HEARTBEAT.touch()
    heartbeat_worker = multiprocessing.Process(
        target=_heartbeat_process, args=(os.getpid(),), name="worker-heartbeat", daemon=True
    )
    heartbeat_worker.start()
    startup = SyncSession()
    try:
        # A process restart cannot resume a Python stack safely. Make stale
        # RUNNING rows visible as failures instead of leaving them hanging.
        stale = startup.execute(select(EtlTask).where(EtlTask.status == "RUNNING")).scalars().all()
        stale_forecast_run_ids: set[int] = set()
        for row in stale:
            row.status = "FAILED"
            row.phase = "Worker reiniciado; tarefa RUNNING anterior foi encerrada"
            row.finished_at = datetime.now(timezone.utc)
            if row.task_type == "forecast":
                run_id = (row.result_json or {}).get("forecast_run_id")
                if isinstance(run_id, int):
                    stale_forecast_run_ids.add(run_id)
        if stale_forecast_run_ids:
            stale_runs = startup.execute(
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
        startup.commit()
    finally:
        startup.close()
    while running:
        HEARTBEAT.touch()
        session = SyncSession()
        try:
            task = session.execute(
                select(EtlTask).where(
                    EtlTask.status == "QUEUED",
                    EtlTask.task_type.in_(["reprocess", "extraction", "forecast"]),
                )
                .order_by(EtlTask.id)
                .limit(1)
            ).scalar_one_or_none()

            if task:
                task_id = task.id

                if task.task_type == "forecast":
                    config_id = task.destination_table_id
                    run_id = (task.result_json or {}).get("forecast_run_id")
                    task.status = "RUNNING"
                    session.commit()
                    session.close()
                    try:
                        from app.models.forecast import ForecastConfig, ForecastAdjustment, ForecastRun
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
                        run.started_at = datetime.now(timezone.utc)
                        s.commit()
                        metadata = asyncio.run(fetch_metadata(dest.physical_name))
                        errors = validate_config(config, metadata)
                        if errors:
                            raise RuntimeError("; ".join(errors))
                        run.phase = "Agregando histórico no Trino"
                        if task_row:
                            task_row.phase = "Agregando histórico no Trino"
                        s.commit()
                        config.destination_physical_name = dest.physical_name
                        config.source_namespace = "gold"
                        config.gold_metadata = metadata
                        config.has_type_column = any(c["name"] == config.type_column for c in metadata["columns"])
                        result = asyncio.run(run_forecast(
                            config, adjustments, date.fromisoformat(run.cutoff_date),
                            business_rules=business_rules, result_table=f"result_run_{run.id}",
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
                        run.error_metrics = {}
                        if task_row:
                            task_row.status = "COMPLETED"
                            task_row.rows_destination = result["rows"]
                            task_row.phase = "Forecast publicado"
                            task_row.finished_at = datetime.now(timezone.utc)
                        s.commit()
                        s.close()
                    except Exception as exc:
                        import traceback
                        traceback.print_exc()
                        s = SyncSession()
                        run = s.get(ForecastRun, run_id)
                        task_row = s.get(EtlTask, task_id)
                        error_message = _format_task_error(exc)
                        if run:
                            run.status = "FAILED"
                            run.phase = f"Falha no forecast: {error_message[:160]}"
                            run.error = error_message[:2000]
                            run.finished_at = datetime.now(timezone.utc)
                        if task_row:
                            task_row.status = "FAILED"
                            task_row.phase = f"Erro: {error_message[:160]}"
                            task_row.finished_at = datetime.now(timezone.utc)
                        s.commit(); s.close()

                elif task.task_type == "reprocess":
                    dest_id = task.destination_table_id
                    conn_ids = task.connection_ids or []
                    periods = task.billing_periods or []

                    print(
                        f"Picked up reprocess task {task_id}:"
                        f" dest_table={dest_id},"
                        f" connections={conn_ids},"
                        f" periods={periods}"
                    )

                    # Mark as RUNNING so no other worker picks it up
                    task.status = "RUNNING"
                    session.commit()
                    session.close()

                    try:
                        from app.routers.etl import _reprocess_destination_sync
                        from app.routers.etl import _append_reprocess_log
                        from app.services.task_runner import _inmemory_tasks

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

                        result = _reprocess_destination_sync(
                            task_id,
                            dest_id,
                            conn_ids,
                            periods,
                            replace_existing=bool((task.result_json or {}).get("replace_existing", False)),
                            publish_by_partition=bool((task.result_json or {}).get("publish_by_partition", False)),
                        )
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
                                terminal_task.phase = (
                                    f"Concluido: {terminal_task.rows_destination:,} linhas"
                                    if succeeded else str(result.get("error") or "Falha ao reprocessar")[:500]
                                )
                                terminal_task.current_blob = terminal_task.phase
                                existing_result = terminal_task.result_json if isinstance(terminal_task.result_json, dict) else {}
                                terminal_task.result_json = {
                                    **existing_result,
                                    "table": result.get("table", existing_result.get("table", "")),
                                    "rows_written": terminal_task.rows_destination,
                                    "error": "" if succeeded else str(result.get("error") or "Falha ao reprocessar"),
                                }
                                terminal_task.finished_at = datetime.now(timezone.utc)
                                terminal_session.commit()
                        finally:
                            terminal_session.close()
                        _append_reprocess_log(task_id, "Tarefa encerrada pelo worker" if succeeded else "Worker encerrou a tarefa com falha", level="INFO" if succeeded else "ERROR")

                        # Clean up in-memory entry
                        _inmemory_tasks.pop(task_id, None)

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
                                t2.phase = f"Erro: {e}"
                                s2.commit()
                            try:
                                from app.routers.etl import _append_reprocess_log
                                _append_reprocess_log(task_id, f"Erro não tratado no worker: {e}", level="ERROR")
                            except Exception:
                                pass
                        finally:
                            s2.close()

                        _inmemory_tasks.pop(task_id, None)

                elif task.task_type == "extraction":
                    conn_id = task.connection_id
                    periods = task.billing_periods or []
                    selected = task.selected_blobs or []

                    print(
                        f"Picked up extraction task {task_id}:"
                        f" connection={conn_id},"
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

                        _run_etl_sync(task_id, conn_id, selected or None, periods)

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
                                t2.phase = f"Erro: {e}"
                                s2.commit()
                        finally:
                            s2.close()

                        _inmemory_tasks.pop(task_id, None)
            else:
                session.close()
                time.sleep(POLL_INTERVAL)

        except Exception as e:
            print(f"Worker polling error: {e}")
            import traceback
            traceback.print_exc()
            try:
                session.close()
            except Exception:
                pass
            time.sleep(POLL_INTERVAL)

    print("Worker stopped.")
    heartbeat_worker.terminate()
    heartbeat_worker.join(timeout=2)
    try:
        HEARTBEAT.unlink(missing_ok=True)
        WORKER_PID.unlink(missing_ok=True)
    except Exception:
        pass
    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    lock_handle.close()


if __name__ == "__main__":
    main()
