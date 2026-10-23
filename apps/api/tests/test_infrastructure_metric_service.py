from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.infrastructure_metrics import service


class EmptyAdapter:
    provider = "oci"

    def fetch(self, plan):
        return SimpleNamespace(
            plan=plan, status_code=200, error_code=None, points=(),
        )


def test_service_plans_bounded_queries_and_returns_rows_without_persistence(monkeypatch):
    connection = SimpleNamespace(
        id=4, connector_type="oci", config_json={
            "tenancy": "tenancy-a", "region": "sa-saopaulo-1",
            "infrastructure_metrics": {
                "enabled": True, "compartment_id": "compartment-a",
                "resource_ids": ["vm-a", "vm-b"],
                "metric_names": ["CpuUtilization"],
                "resolutions_seconds": [60],
            },
        },
    )
    monkeypatch.setattr(service, "build_oci_adapter", lambda conn, config: EmptyAdapter())
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = service.collect_oci_metrics(
        connection, start_utc=start, end_utc=start + timedelta(hours=1),
        resolution_seconds=60, statistics=("mean", "p95"),
        collection_run_id="run-1",
    )
    assert result["estimated_api_calls"] == 4
    assert result["resource_count"] == 2
    assert result["rows_prepared"] == []
    assert result["persistence_enabled"] is False


def test_service_rejects_unconfigured_resolution(monkeypatch):
    connection = SimpleNamespace(
        id=4, connector_type="oci", config_json={
            "tenancy": "tenancy-a", "region": "sa-saopaulo-1",
            "infrastructure_metrics": {
                "enabled": True, "compartment_id": "compartment-a",
                "resource_ids": ["vm-a"], "resolutions_seconds": [60],
            },
        },
    )
    monkeypatch.setattr(service, "build_oci_adapter", lambda conn, config: EmptyAdapter())
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="not enabled"):
        service.collect_oci_metrics(
            connection, start_utc=start, end_utc=start + timedelta(hours=1),
            resolution_seconds=300, statistics=("mean",),
        )
