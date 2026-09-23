from types import SimpleNamespace

from app.services.cloud_scope import allowed_rule_cloud_scopes
from app.services.etl_task_claim import task_resource_lock_keys, terminate_stale_advisory_lock_holders
from app.models.etl_task import EtlTask


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


def test_stale_advisory_lock_recovery_is_noop_for_non_postgres():
    class Bind:
        class dialect:
            name = "sqlite"

    class Session:
        def get_bind(self):
            return Bind()

    assert terminate_stale_advisory_lock_holders(Session()) == 0
