import os
import time

from app.services.worker_runtime import aggregate_worker_health, any_worker_healthy


def test_aggregate_worker_health_reports_offline_when_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENFINOPS_WORKER_RUNTIME_DIR", str(tmp_path))
    payload = aggregate_worker_health()
    assert payload["status"] == "offline"
    assert payload["workers"] == []


def test_any_worker_healthy_with_fresh_heartbeat(tmp_path, monkeypatch):
    runtime = tmp_path / "workers"
    runtime.mkdir()
    monkeypatch.setenv("OPENFINOPS_WORKER_RUNTIME_DIR", str(runtime))
    heartbeat = runtime / "worker-0.heartbeat"
    pid_file = runtime / "worker-0.pid"
    heartbeat.touch()
    pid_file.write_text(str(os.getpid()))
    assert any_worker_healthy(max_age=15.0) is True
    payload = aggregate_worker_health(max_age=15.0)
    assert payload["status"] == "ok"
    assert payload["concurrency"] == 1
