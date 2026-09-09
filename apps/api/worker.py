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
import multiprocessing
import threading
from datetime import date, datetime, timezone
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
    reconcile_stale_running_tasks,
    release_task_resource_locks,
    release_task_to_queue,
    try_acquire_task_resource_locks,
)
from app.services.worker_runtime import (
    heartbeat_path,
    pid_path,
    runtime_dir,
    supervisor_pid_path,
    touch_heartbeat,
    write_pid,
)

POLL_INTERVAL = 2  # seconds between polls when idle


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
        if failed or (reprocess_restart_after and reprocess_tasks_since_restart >= reprocess_restart_after):
            print(
                "Recycling worker after a failed reprocess"
                if failed
                else f"Recycling worker after {reprocess_tasks_since_restart} reprocess task(s) to release memory.",
                flush=True,
            )
            sys.exit(1 if failed else 0)

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
            task = claim_next_etl_task(session)
            if task is None:
                session.close()
                time.sleep(POLL_INTERVAL)
                continue

            session.commit()
            session.close()
            task_id = task.id
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
                            wait_phase = (
                                "Aguardando conexão de origem"
                                if queued.task_type == "extraction"
                                else "Aguardando escritor Gold/Dimon"
                            )
                            release_task_to_queue(retry, queued, phase=wait_phase)
                            retry.commit()
                    finally:
                        retry.close()
                    time.sleep(0.2)
                    continue

                if task.task_type == "forecast":
                    config_id = task.destination_table_id
                    run_id = (task.result_json or {}).get("forecast_run_id")
                    task.status = "RUNNING"
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
                        simulated_as_of = None
                        if (run.config_snapshot or {}).get("simulated_as_of"):
                            simulated_as_of = date.fromisoformat(run.config_snapshot["simulated_as_of"])
                        result = asyncio.run(run_forecast(
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
                        rateio: dict = {}
                        try:
                            from app.services.shared_cost import run_shared_cost_job_sync
                            rateio = run_shared_cost_job_sync(s, allow_empty=True)
                            if task_row:
                                task_row.phase = (
                                    f"Forecast publicado · {rateio.get('rows', 0)} linhas de rateio"
                                )
                                s.commit()
                        except Exception as rateio_exc:
                            if task_row:
                                task_row.phase = f"Forecast publicado · rateio pendente: {rateio_exc}"
                                s.commit()
                        # Sequência: forecast → rateio → Dimon (visão completa =
                        # realizado + forecast + rateio). A planning mudou, então a
                        # Dimon pré-agregada precisa ser materializada de novo; a
                        # fila do worker executa em ordem de id (sequência real).
                        try:
                            from app.services.dimon_materialization import enqueue_dimons_task
                            dimon_task = enqueue_dimons_task(s, source="forecast")
                            if dimon_task and task_row:
                                task_row.phase = (
                                    f"Forecast publicado · {rateio.get('rows', 0)} linhas de rateio"
                                    f" · Dimon enfileirada (task #{dimon_task.id})"
                                )
                                s.commit()
                        except Exception as dimon_exc:
                            if task_row:
                                task_row.phase = f"Forecast publicado · Dimon pendente: {dimon_exc}"
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
                    months = list((task.result_json or {}).get("months") or [])
                    task.status = "RUNNING"
                    task.phase = "Validando configuração"
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
                        metadata = asyncio.run(fetch_metadata(dest.physical_name))
                        errors = validate_config(config, metadata)
                        if errors:
                            raise RuntimeError("; ".join(errors))
                        config.destination_physical_name = dest.physical_name
                        config.source_namespace = "gold"
                        config.gold_metadata = metadata
                        config.has_type_column = any(c["name"] == config.type_column for c in metadata["columns"])
                        snapshot = {
                            "config_id": config.id, "config_version": config.version,
                            "destination_table_id": dest.id,
                            "destination_physical_name": dest.physical_name,
                            "source_namespace": "gold",
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
                                result = asyncio.run(run_forecast(
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

                elif task.task_type in ("ai_cost_relationship_reprocess", "dimon_reprocess"):
                    task.status = "RUNNING"
                    task.phase = "Agregando Golds e publicando as tabelas Dimon"
                    session.commit()
                    session.close()
                    try:
                        import asyncio
                        from app.services.dimon_materialization import materialize_dimons
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
                        result = asyncio.run(materialize_dimons(s, run_id=task_id, progress=progress, rule_ids=rule_ids or None))
                        task_row = s.get(EtlTask, task_id)
                        if task_row:
                            failed = list(result.get("failed") or [])
                            rows = int(result.get("rows") or 0)
                            if failed and rows == 0 and not result.get("details"):
                                task_row.status = "FAILED"
                                task_row.phase = "Nenhuma Dimon publicada: " + "; ".join(
                                    f"{f.get('code', f.get('rule_id'))}: {f.get('error', 'erro')}" for f in failed
                                )[:160]
                                task_row.errors = [f.get("error", "") for f in failed]
                            else:
                                task_row.status = "COMPLETED"
                                task_row.phase = f"Dimon publicada: {rows} linhas" + (
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

                            if task.task_type == "reclassification":
                                result = _reclassify_destination_sync(task_id, dest_id, conn_ids, periods)
                            else:
                                result = _reprocess_destination_sync(
                                    task_id, dest_id, conn_ids, periods,
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
                                # Opção "Materializar Dimon na sequência": quando o
                                # reprocessamento pediu chain_dimons, só a ÚLTIMA
                                # tarefa do grupo enfileira a Dimon (visão completa
                                # = realizado + forecast + rateio) na fila do worker.
                                if succeeded:
                                    chain = terminal_task.result_json.get("chain_dimons")
                                    if chain and isinstance(chain, dict) and chain.get("enabled"):
                                        group = terminal_task.result_json.get("queue_group")
                                        position = terminal_task.result_json.get("queue_position")
                                        total = terminal_task.result_json.get("queue_total")
                                        is_last_group_task = (
                                            position is not None and total is not None and position == total
                                        )
                                        if not is_last_group_task and group:
                                            from sqlalchemy import select as _select
                                            group_rows = terminal_session.execute(
                                                _select(EtlTask.id, EtlTask.result_json).where(
                                                    EtlTask.task_type == "reprocess",
                                                    EtlTask.status.in_(["QUEUED", "RUNNING"]),
                                                )
                                            ).all()
                                            remaining = [
                                                rid for rid, rj in group_rows
                                                if (rj or {}).get("queue_group") == group
                                            ]
                                            is_last_group_task = not remaining
                                        if is_last_group_task:
                                            from app.services.dimon_materialization import enqueue_dimons_task
                                            rule_ids = chain.get("rule_ids") or None
                                            dimon_task = enqueue_dimons_task(
                                                terminal_session, rule_ids=rule_ids, source="reprocess"
                                            )
                                            if dimon_task:
                                                terminal_task.phase = (
                                                    (terminal_task.phase or "")
                                                    + f" · Dimon enfileirada (task #{dimon_task.id})"
                                                )
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

                elif task.task_type == "extraction":
                    conn_id = task.connection_id
                    periods = task.billing_periods or []
                    selected = task.selected_blobs or []
                    from app.models.source_connection import SourceConnection as _LogSourceConnection
                    source_row = session.get(_LogSourceConnection, conn_id) if conn_id else None
                    source_code = source_row.code if source_row else f"connection-{conn_id}"

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


def _spawn_consumer(worker_id: int) -> None:
    os.environ["OPENFINOPS_WORKER_ID"] = str(worker_id)
    consumer_main(worker_id)


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
        reconcile_stale_running_tasks(startup)
    finally:
        startup.close()

    ctx = multiprocessing.get_context("spawn")
    processes: dict[int, multiprocessing.Process] = {}
    while running:
        for worker_id in range(concurrency):
            proc = processes.get(worker_id)
            if proc is not None and proc.is_alive():
                continue
            if proc is not None:
                proc.join(timeout=1)
            proc = ctx.Process(
                target=_spawn_consumer,
                args=(worker_id,),
                name=f"openfinops-worker-{worker_id}",
            )
            proc.start()
            processes[worker_id] = proc
        time.sleep(2)

    for proc in processes.values():
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)
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
