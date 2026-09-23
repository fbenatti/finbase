"""Recover queue rows left RUNNING after a worker crash."""
from __future__ import annotations

import os
import time
import logging

from app.database import SyncSession
from app.services.etl_task_claim import (
    reconcile_stale_running_tasks,
    terminate_stale_advisory_lock_holders,
)
from app.services.worker_runtime import any_worker_healthy

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("openfinops.supervisor")


def main() -> None:
    interval = float(os.getenv("SUPERVISOR_INTERVAL_SECONDS", "15"))
    while True:
        try:
            if not any_worker_healthy():
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
