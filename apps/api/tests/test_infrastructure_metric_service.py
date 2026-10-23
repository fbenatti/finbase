from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.infrastructure_metrics import service
from app.services.infrastructure_metrics.contracts import MetricPoint, QueryResult


class EmptyAdapter:
    provider = "oci"

    def fetch(self, plan):
        return SimpleNamespace(
            plan=plan, status_code=200, error_code=None, points=(),
        )


class CounterAdapter:
    provider = "oci"

    def fetch(self, plan):
        timestamp = plan.start_utc
        point = MetricPoint(
            timestamp_utc=timestamp, dimensions={"resourceId": "vm-a"},
            values={"mean": 100.0}, source_sample_period_seconds=10,
        )
        return QueryResult(plan=plan, status_code=200, points=(point,), stream_count=1)


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


def test_service_can_opt_in_to_day_slice_publication(monkeypatch):
    connection = SimpleNamespace(
        id=4, connector_type="oci", config_json={
            "tenancy": "tenancy-a", "region": "sa-saopaulo-1",
            "infrastructure_metrics": {
                "enabled": True, "compartment_id": "compartment-a",
                "resource_ids": ["vm-a"], "metric_names": ["DiskBytesRead"],
                "resolutions_seconds": [60],
            },
        },
    )
    monkeypatch.setattr(service, "build_oci_adapter", lambda conn, config: CounterAdapter())
    published = []

    def fake_publish(rows, *, connection_id, metric_day):
        published.append((rows, connection_id, metric_day))
        return len(rows)

    from app.services.infrastructure_metrics import publication
    monkeypatch.setattr(publication, "publish_metric_rows", fake_publish)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = service.collect_oci_metrics(
        connection, start_utc=start, end_utc=start + timedelta(minutes=1),
        resolution_seconds=60, statistics=("mean",), persist=True,
    )
    assert result["persistence_enabled"] is True
    assert result["persisted_by_day"] == {"20260101": 2}
    assert len(published) == 1
    assert published[0][1:] == (4, "20260101")
