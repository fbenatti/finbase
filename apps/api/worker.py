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
from sqlalchemy import select, text

POLL_INTERVAL = 2  # seconds between polls when idle

running = True


def shutdown(sig, frame):
    global running
    running = False
    print("\nWorker received shutdown signal. Finishing current task (if any) then exiting...")


signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)


def main():
    print("FinOps Worker started. Polling for QUEUED tasks every", POLL_INTERVAL, "seconds...")
    while running:
        session = SyncSession()
        try:
            task = session.execute(
                select(EtlTask).where(
                    EtlTask.status == "QUEUED",
                    EtlTask.task_type.in_(["reprocess", "extraction"]),
                )
                .order_by(EtlTask.id)
                .limit(1)
            ).scalar_one_or_none()

            if task:
                task_id = task.id

                if task.task_type == "reprocess":
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

                        result = _reprocess_destination_sync(
                            task_id,
                            dest_id,
                            conn_ids,
                            periods,
                            replace_existing=bool((task.result_json or {}).get("replace_existing", False)),
                        )
                        print(f"Task {task_id} completed: {result}")

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


if __name__ == "__main__":
    main()
