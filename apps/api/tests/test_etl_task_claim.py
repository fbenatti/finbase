from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
import fcntl
import multiprocessing
import time

from sqlalchemy import ForeignKeyConstraint, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.services.cloud_scope import allowed_rule_cloud_scopes
from app.services.etl_task_claim import (
    claim_next_etl_task,
    find_stalled_worker_task,
    recover_stalled_task,
    recover_worker_orphaned_tasks,
    task_resource_lock_keys,
    terminate_stale_advisory_lock_holders,
)
from app.models.etl_task import EtlTask
from app.models.source_connection import SourceConnection  # noqa: F401
from app.models.destination import DestinationTable  # noqa: F401


def test_allowed_rule_cloud_scopes_for_azure():
    scopes = allowed_rule_cloud_scopes("azure", "Azure-TI")
    assert None in scopes
    assert "azure" in scopes


def test_allowed_rule_cloud_scopes_for_mongodb_includes_atlas():
    scopes = allowed_rule_cloud_scopes("mongodb", "MongoDB Atlas")
    assert "mongodb" in scopes
    assert "atlas" in scopes


def test_task_resource_lock_keys_queue_group():
    task = EtlTask(
        id=1,
        task_type="reprocess",
        status="QUEUED",
        destination_table_id=10,
        connection_ids=[4],
        billing_periods=["202501"],
        result_json={"queue_group": "group-abc"},
    )
    keys = task_resource_lock_keys(task)
    # queue group + global analytic writer + exact destination slice
    assert len(keys) == 3
    assert len(set(keys)) == 3


def test_task_resource_lock_keys_reprocess_slice():
    task = EtlTask(
        id=2,
        task_type="reprocess",
        status="QUEUED",
        destination_table_id=10,
        connection_ids=[4],
        billing_periods=["202501"],
        result_json={},
    )
    keys = task_resource_lock_keys(task)
    # global analytic writer + exact destination slice
    assert len(keys) == 2


def test_task_resource_lock_keys_snapshot_slice():
    task = EtlTask(
        id=3,
        task_type="reprocess",
        status="QUEUED",
        destination_table_id=11,
        connection_ids=[4],
        billing_periods=["202509"],
        result_json={"snapshot_dates": ["2025-09-15"]},
    )
    keys = task_resource_lock_keys(task)
    assert len(keys) == 2


def test_stale_advisory_lock_recovery_is_noop_for_non_postgres():
    class Bind:
        class dialect:
            name = "sqlite"

    class Session:
        def get_bind(self):
            return Bind()

    assert terminate_stale_advisory_lock_holders(Session()) == 0


def _task_session():
    engine = create_engine("sqlite://")
    constraints = [c for c in EtlTask.__table__.constraints if isinstance(c, ForeignKeyConstraint)]
    for constraint in constraints:
        EtlTask.__table__.constraints.remove(constraint)
    try:
        EtlTask.__table__.create(engine)
    finally:
        for constraint in constraints:
            EtlTask.__table__.append_constraint(constraint)
    return engine, Session(engine)


def test_recent_progress_does_not_mark_worker_task_stalled():
    engine, session = _task_session()
    try:
        now = datetime.now(timezone.utc)
        task = EtlTask(
            task_type="reprocess", status="RUNNING", worker_pid=123,
            started_at=now - timedelta(minutes=30),
            last_progress_at=now - timedelta(minutes=14),
        )
        session.add(task)
        session.commit()

        assert find_stalled_worker_task(session, 123, stale_after_seconds=15 * 60, now=now) is None
    finally:
        session.close()
        engine.dispose()


def test_stalled_safe_task_is_requeued_once_and_worker_ownership_cleared():
    engine, session = _task_session()
    try:
        now = datetime.now(timezone.utc)
        task = EtlTask(
            task_type="extraction", status="RUNNING", worker_pid=456,
            started_at=now - timedelta(minutes=20),
            last_progress_at=now - timedelta(minutes=20),
            recovery_attempts=0,
        )
        session.add(task)
        session.commit()

        stalled = find_stalled_worker_task(session, 456, stale_after_seconds=15 * 60, now=now)
        assert stalled is not None
        assert recover_stalled_task(session, stalled, reason="stalled", now=now) == "REQUEUED"
        session.commit()

        assert task.status == "QUEUED"
        assert task.worker_pid is None
        assert task.recovery_attempts == 1
        assert task.result_json["recovery_history"][-1]["outcome"] == "REQUEUED"
    finally:
        session.close()
        engine.dispose()


def test_second_stall_fails_task_so_queue_can_advance():
    engine, session = _task_session()
    try:
        now = datetime.now(timezone.utc)
        task = EtlTask(
            task_type="reprocess", status="RUNNING", worker_pid=789,
            started_at=now - timedelta(minutes=20),
            last_progress_at=now - timedelta(minutes=20),
            recovery_attempts=1,
        )
        session.add(task)
        session.commit()

        assert recover_stalled_task(session, task, reason="stalled twice", now=now) == "FAILED"
        session.commit()

        assert task.status == "FAILED"
        assert task.worker_pid is None
        assert task.finished_at.replace(tzinfo=timezone.utc) == now
        assert task.result_json["failure_kind"] == "worker_stalled"
        assert task.result_json["recovery_history"][-1]["outcome"] == "FAILED"
    finally:
        session.close()
        engine.dispose()


def test_non_retryable_stalled_task_fails_immediately():
    engine, session = _task_session()
    try:
        now = datetime.now(timezone.utc)
        task = EtlTask(
            task_type="forecast", status="RUNNING", worker_pid=42,
            started_at=now - timedelta(minutes=20),
            last_progress_at=now - timedelta(minutes=20),
        )
        session.add(task)
        session.commit()

        assert recover_stalled_task(session, task, reason="unsafe to retry", now=now) == "FAILED"
        session.commit()
        assert task.status == "FAILED"
        assert task.recovery_attempts == 0
    finally:
        session.close()
        engine.dispose()


def test_unexpected_worker_exit_recovers_its_running_task():
    engine, session = _task_session()
    try:
        task = EtlTask(
            task_type="extraction", status="RUNNING", worker_pid=321,
            started_at=datetime.now(timezone.utc) - timedelta(seconds=30),
            last_progress_at=datetime.now(timezone.utc),
        )
        session.add(task)
        session.commit()

        assert recover_worker_orphaned_tasks(session, 321) == [(task.id, "REQUEUED")]
        session.commit()
        assert task.status == "QUEUED"
        assert task.worker_pid is None
    finally:
        session.close()
        engine.dispose()


def test_external_supervisor_stops_orphan_before_requeue(monkeypatch, tmp_path):
    import json
    import psutil
    import supervisor

    engine, session = _task_session()
    monkeypatch.setattr(supervisor, "SyncSession", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setenv("OPENFINOPS_WORKER_RUNTIME_DIR", str(tmp_path))
    lock_path = tmp_path / "warehouse-write.lock"
    ready = multiprocessing.Event()
    process = multiprocessing.get_context("spawn").Process(
        target=_hold_test_file_lock, args=(str(lock_path), ready),
    )
    process.start()
    try:
        assert ready.wait(timeout=10)
        task = EtlTask(
            task_type="reprocess", status="RUNNING", worker_pid=process.pid,
            started_at=datetime.now(timezone.utc), last_progress_at=datetime.now(timezone.utc),
        )
        session.add(task)
        session.commit()
        (tmp_path / f"consumer-{process.pid}.json").write_text(json.dumps({
            "pid": process.pid,
            "created_at": psutil.Process(process.pid).create_time(),
            "parent_pid": 99999999,
            "parent_created_at": 0,
        }))

        assert supervisor.recover_registered_consumers() == 1
        process.join(timeout=5)
        assert not psutil.pid_exists(process.pid)
        session.expire_all()
        assert session.get(EtlTask, task.id).status == "QUEUED"
        with open(lock_path, "a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        if psutil.pid_exists(process.pid):
            psutil.Process(process.pid).kill()
        process.join(timeout=5)
        session.close()
        engine.dispose()


def _hold_test_file_lock(path, ready):
    with open(path, "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        ready.set()
        while True:
            time.sleep(1)


def test_watchdog_stops_lock_holder_before_requeue(monkeypatch, tmp_path):
    import worker

    engine, session = _task_session()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(worker, "SyncSession", factory)
    lock_path = tmp_path / "warehouse-write.lock"
    ready = multiprocessing.Event()
    process = multiprocessing.get_context("spawn").Process(
        target=_hold_test_file_lock,
        args=(str(lock_path), ready),
    )
    process.start()
    try:
        assert ready.wait(timeout=10)
        now = datetime.now(timezone.utc)
        task = EtlTask(
            task_type="reprocess", status="RUNNING", worker_pid=process.pid,
            started_at=now - timedelta(minutes=20),
            last_progress_at=now - timedelta(minutes=20),
        )
        session.add(task)
        session.commit()

        worker._watch_stalled_consumers({0: process})
        session.expire_all()
        assert not process.is_alive()
        assert session.get(EtlTask, task.id).status == "QUEUED"

        # The task becomes claimable only after the process holding the shared
        # write lock has exited; the retry cannot overlap the abandoned run.
        with open(lock_path, "a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        recovered = session.get(EtlTask, task.id)
        recovered.available_at = None
        session.commit()
        claimed = claim_next_etl_task(session, worker_pid=process.pid + 1)
        assert claimed is not None
        assert claimed.id == task.id
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        session.close()
        engine.dispose()
