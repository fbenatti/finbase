"""Runtime paths and health probes for multi-process ETL workers."""

from __future__ import annotations

import os
import time
from pathlib import Path


def runtime_dir() -> Path:
    explicit = os.getenv("OPENFINOPS_WORKER_RUNTIME_DIR")
    if explicit:
        return Path(explicit)
    heartbeat = os.getenv("OPENFINOPS_WORKER_HEARTBEAT", "/tmp/openfinops-worker.heartbeat")
    return Path(heartbeat).parent


def heartbeat_path(worker_id: int) -> Path:
    return runtime_dir() / f"worker-{worker_id}.heartbeat"


def pid_path(worker_id: int) -> Path:
    return runtime_dir() / f"worker-{worker_id}.pid"


def supervisor_pid_path() -> Path:
    return runtime_dir() / "supervisor.pid"


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
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return {
            "worker_id": worker_id,
            "status": "offline",
            "heartbeat_age_seconds": None,
            "worker_pid": None,
        }
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
    return {
        "status": status,
        "heartbeat_age_seconds": best.get("heartbeat_age_seconds"),
        "worker_pid": best.get("worker_pid"),
        "workers": workers,
        "concurrency": len(workers),
        "orphaned_tasks_cleared": 0,
    }
