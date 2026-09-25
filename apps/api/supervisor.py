"""Independent watchdog for worker children, including orphaned subprocesses."""
from __future__ import annotations

import os
import time
import logging
import psutil

from app.database import SyncSession
from app.services.etl_task_claim import (
    find_stalled_worker_task,
    recover_worker_orphaned_tasks,
    reconcile_stale_running_tasks,
    terminate_stale_advisory_lock_holders,
)
from app.services.worker_runtime import any_worker_healthy, process_matches, registered_consumers

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("openfinops.supervisor")


def recover_registered_consumers() -> int:
    """Only release a task after its registered process has really exited."""
    recovered = 0
    timeout = max(60, int(os.getenv("WORKER_TASK_STALL_TIMEOUT_SECONDS", "900")))
    for path, record in registered_consumers():
        pid = int(record["pid"])
        alive = process_matches(pid, float(record["created_at"]))
        parent_alive = process_matches(int(record["parent_pid"]), float(record["parent_created_at"]))
        stalled = False
        if alive and parent_alive:
            session = SyncSession()
            try:
                stalled = find_stalled_worker_task(session, pid, stale_after_seconds=timeout) is not None
                session.rollback()
            finally:
                session.close()
        if alive and (not parent_alive or stalled):
            logger.warning("Stopping worker child %s (parent_alive=%s, stalled=%s)", pid, parent_alive, stalled)
            try:
                process = psutil.Process(pid)
                if process_matches(pid, float(record["created_at"])):
                    process.terminate()
                    gone, remaining = psutil.wait_procs([process], timeout=5)
                    for child in remaining:
                        if process_matches(pid, float(record["created_at"])):
                            child.kill()
                    psutil.wait_procs(remaining, timeout=5)
            except psutil.NoSuchProcess:
                pass
            alive = process_matches(pid, float(record["created_at"]))
        if alive:
            continue
        session = SyncSession()
        try:
            rows = recover_worker_orphaned_tasks(session, pid)
            session.commit()
            recovered += len(rows)
            if rows:
                logger.warning("Recovered tasks from dead worker child %s: %s", pid, rows)
            path.unlink(missing_ok=True)
        except Exception:
            session.rollback()
            logger.exception("Could not recover tasks from worker child %s", pid)
        finally:
            session.close()
    return recovered


def main() -> None:
    interval = float(os.getenv("SUPERVISOR_INTERVAL_SECONDS", "15"))
    while True:
        try:
            recover_registered_consumers()
            # Heartbeat files may be stale while a registered child is still
            # executing. Never reset its RUNNING row before stopping it.
            live_children = any(
                process_matches(int(record["pid"]), float(record["created_at"]))
                for _, record in registered_consumers()
            )
            if not any_worker_healthy() and not live_children:
                session = SyncSession()
                try:
                    terminated = terminate_stale_advisory_lock_holders(
                        session,
                        max_age_seconds=int(os.getenv("OPENFINOPS_STALE_ADVISORY_LOCK_SECONDS", "60")),
                    )
                    if terminated:
                        logger.warning("Terminated %d stale advisory-lock holder(s)", terminated)
                    reconcile_stale_running_tasks(session)
                finally:
                    session.close()
        except Exception:
            # A temporary database/API outage must not kill the supervisor.
            logger.exception("Supervisor check failed; retrying")
        time.sleep(interval)


if __name__ == "__main__":
    main()
