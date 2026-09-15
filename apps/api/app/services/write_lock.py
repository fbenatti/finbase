"""Process-wide filesystem lock shared by backup and data-writing workers."""
from contextlib import contextmanager
from pathlib import Path
import fcntl
import os
from typing import Callable

# Only these task types acquire ``exclusive_write_lock``; others must not wait on
# ``is_locked()`` (forecast/Dimon use the analytic advisory lock only).
WAREHOUSE_FLOCK_TASK_TYPES = frozenset({"backup", "extraction", "reprocess", "reclassification"})


def task_waits_on_warehouse_flock(task_type: str) -> bool:
    return task_type in WAREHOUSE_FLOCK_TASK_TYPES


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
