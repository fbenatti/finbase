"""Runtime paths and health probes for multi-process ETL workers."""

from __future__ import annotations

import os
import time
import json
from pathlib import Path

import psutil


def runtime_dir() -> Path:
    explicit = os.getenv("OPENFINOPS_WORKER_RUNTIME_DIR")
    if explicit:
        return Path(explicit)
    legacy_heartbeat = os.getenv("OPENFINOPS_WORKER_HEARTBEAT")
    if legacy_heartbeat:
        legacy = Path(legacy_heartbeat)
        if legacy.name.startswith("worker."):
            return legacy.parent
    return Path("/tmp/openfinops-worker")


def heartbeat_path(worker_id: int) -> Path:
    return runtime_dir() / f"worker-{worker_id}.heartbeat"


def pid_path(worker_id: int) -> Path:
    return runtime_dir() / f"worker-{worker_id}.pid"


def supervisor_pid_path() -> Path:
    return runtime_dir() / "supervisor.pid"


def consumer_record_path(pid: int) -> Path:
    return runtime_dir() / f"consumer-{pid}.json"


def note_cpu_sample(
    samples: dict[int, tuple[float, float]],
    pid: int,
    cpu_seconds: float | None,
    *,
    now: float,
    idle_after_seconds: float,
) -> bool:
    """True while the process is alive and has used CPU inside the idle window.

    The first sample counts as working. A later sample with the same CPU total
    stays working until ``idle_after_seconds`` pass without any increase.
    ``None`` means the process is gone.
    """
    if cpu_seconds is None:
        samples.pop(pid, None)
        return False
    previous = samples.get(pid)
    if previous is None or cpu_seconds > previous[0] + 1e-6:
        samples[pid] = (cpu_seconds, now)
        return True
    _, changed_at = previous
    return (now - changed_at) < idle_after_seconds


def process_cpu_seconds(pid: int) -> float | None:
    """Cumulative CPU of the process and its children, or None if it is gone."""
    try:
        process = psutil.Process(pid)
        totals = [process.cpu_times()]
        try:
            children = process.children(recursive=True)
        except (psutil.AccessDenied, PermissionError):
            children = []
        for child in children:
            try:
                totals.append(child.cpu_times())
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return sum(item.user + item.system for item in totals)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None


def process_matches(pid: int, created_at: float) -> bool:
    """Guard against PID reuse before signaling a registered subprocess."""
    try:
        process = psutil.Process(pid)
        return process.is_running() and abs(process.create_time() - created_at) < 1
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def register_consumer(
    pid: int, parent_pid: int, *, parent_created_at: float | None = None,
) -> None:
    record = {
        "pid": pid,
        "created_at": psutil.Process(pid).create_time(),
        "parent_pid": parent_pid,
        "parent_created_at": (
            parent_created_at if parent_created_at is not None
            else psutil.Process(parent_pid).create_time()
        ),
    }
    path = consumer_record_path(pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(record))
    temporary.replace(path)


def registered_consumers() -> list[tuple[Path, dict]]:
    records = []
    for path in runtime_dir().glob("consumer-*.json"):
        try:
            record = json.loads(path.read_text())
            if all(key in record for key in ("pid", "created_at", "parent_pid", "parent_created_at")):
                records.append((path, record))
        except (OSError, ValueError, TypeError):
            continue
    return records


def touch_heartbeat(worker_id: int) -> None:
    path = heartbeat_path(worker_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def write_pid(worker_id: int) -> None:
    path = pid_path(worker_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()))


def _probe_worker_file(heartbeat_file: Path, pid_file: Path, *, max_age: float) -> dict:
    worker_id = heartbeat_file.stem.replace("worker-", "")
    # The API and ETL worker normally run in separate containers.  Their PID
    # namespaces are therefore different, so os.kill(pid, 0) from the API
    # cannot validate a worker PID written by the worker.  The heartbeat file
    # is the cross-container liveness signal; an old heartbeat is sufficient
    # to classify the worker as stale.
    try:
        pid = int(pid_file.read_text().strip())
    except (FileNotFoundError, ValueError):
        pid = None
    try:
        age = max(0.0, time.time() - heartbeat_file.stat().st_mtime)
        status = "ok" if age <= max_age else "stale"
        return {
            "worker_id": worker_id,
            "status": status,
            "heartbeat_age_seconds": round(age, 1),
            "worker_pid": pid,
        }
    except FileNotFoundError:
        return {
            "worker_id": worker_id,
            "status": "stale",
            "heartbeat_age_seconds": None,
            "worker_pid": pid,
        }


def scan_worker_health(*, max_age: float = 15.0) -> list[dict]:
    root = runtime_dir()
    if not root.exists():
        return []
    workers: list[dict] = []
    for heartbeat_file in sorted(root.glob("worker-*.heartbeat")):
        pid_file = heartbeat_file.with_suffix(".pid")
        workers.append(_probe_worker_file(heartbeat_file, pid_file, max_age=max_age))
    return workers


def any_worker_healthy(*, max_age: float = 15.0) -> bool:
    workers = scan_worker_health(max_age=max_age)
    if workers:
        return any(worker.get("status") == "ok" for worker in workers)

    # Legacy single-worker files.
    heartbeat = os.getenv("OPENFINOPS_WORKER_HEARTBEAT", "/tmp/openfinops-worker.heartbeat")
    pid_file = os.getenv("OPENFINOPS_WORKER_PID", "/tmp/openfinops-worker.pid")
    legacy = _probe_worker_file(Path(heartbeat), Path(pid_file), max_age=max_age)
    return legacy.get("status") == "ok"


def aggregate_worker_health(*, max_age: float = 15.0) -> dict:
    workers = scan_worker_health(max_age=max_age)
    if not workers:
        heartbeat = os.getenv("OPENFINOPS_WORKER_HEARTBEAT", "/tmp/openfinops-worker.heartbeat")
        pid_file = os.getenv("OPENFINOPS_WORKER_PID", "/tmp/openfinops-worker.pid")
        legacy = _probe_worker_file(Path(heartbeat), Path(pid_file), max_age=max_age)
        workers = [legacy] if legacy.get("worker_pid") is not None or Path(heartbeat).exists() else []

    if not workers:
        return {
            "status": "offline",
            "heartbeat_age_seconds": None,
            "worker_pid": None,
            "workers": [],
            "concurrency": 0,
            "orphaned_tasks_cleared": 0,
        }

    healthy = [worker for worker in workers if worker.get("status") == "ok"]
    best = min(healthy, key=lambda item: item.get("heartbeat_age_seconds") or 0) if healthy else workers[0]
    status = "ok" if healthy else ("stale" if workers else "offline")
    warehouse_write_locked = False
    try:
        from app.services.write_lock import is_locked
        warehouse_write_locked = is_locked()
    except Exception:
        pass
    return {
        "status": status,
        "heartbeat_age_seconds": best.get("heartbeat_age_seconds"),
        "worker_pid": best.get("worker_pid"),
        "workers": workers,
        "concurrency": len(workers),
        "orphaned_tasks_cleared": 0,
        "warehouse_write_locked": warehouse_write_locked,
    }
