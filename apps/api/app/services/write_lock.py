"""Process-wide filesystem lock shared by backup and data-writing workers."""
from contextlib import contextmanager
from pathlib import Path
import fcntl
import os
from typing import Callable

# Only these task types acquire ``exclusive_write_lock``; others must not wait on
# ``is_locked()`` (forecast/Gold use the analytic advisory lock only).
# Extraction downloads run in parallel; only Bronze/Gold/Silver commits that
# hold this flock are serialized (backup, reprocess paths that opt in).
WAREHOUSE_FLOCK_TASK_TYPES = frozenset({"backup", "reprocess", "reclassification"})


def task_waits_on_warehouse_flock(task) -> bool:
    """Whether *task* must wait on / hold the process-wide warehouse flock."""
    task_type = task if isinstance(task, str) else getattr(task, "task_type", "")
    if task_type not in WAREHOUSE_FLOCK_TASK_TYPES:
        return False
    if task_type in {"reprocess", "reclassification"} and not isinstance(task, str):
        from app.services.etl_task_claim import (
            _reprocess_task_has_slice_lock,
            parallel_reprocess_slices_enabled,
        )
        if parallel_reprocess_slices_enabled() and _reprocess_task_has_slice_lock(task):
            return False
    return True


def task_holds_exclusive_warehouse_flock(task) -> bool:
    return task_waits_on_warehouse_flock(task)


def lock_path() -> Path:
    return Path(os.getenv("OPENFINOPS_WORKER_RUNTIME_DIR", "/tmp/openfinops-worker")) / "warehouse-write.lock"


def is_locked() -> bool:
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return True
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()
    return False


@contextmanager
def exclusive_write_lock():
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
