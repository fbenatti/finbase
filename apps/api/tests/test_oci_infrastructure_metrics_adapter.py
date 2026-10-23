from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.infrastructure_metrics.catalog import get_metric
from app.services.infrastructure_metrics.contracts import MetricScope, QueryPlan
from app.services.infrastructure_metrics.providers import oci as oci_metrics
from app.services.infrastructure_metrics.normalization import query_results_to_silver_rows


class FakeMonitoringClient:
    def __init__(self):
        self.requests = []

    def summarize_metrics_data(self, *, compartment_id, summarize_metrics_data_details):
        self.requests.append((compartment_id, summarize_metrics_data_details))
        if "percentile" in summarize_metrics_data_details:
            error = RuntimeError("native P95 not supported")
            error.status = 400
            error.code = "InvalidParameter"
            raise error
        timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        point = SimpleNamespace(timestamp=timestamp, value=42.5)
        stream = SimpleNamespace(
            dimensions={"resourceId": "private-ocid", "nicId": "nic-a"},
            aggregated_datapoints=[point],
        )
        return SimpleNamespace(data=[stream])


def _plan(*, enabled=True):
    descriptor = get_metric("oci", "oci_computeagent", "CpuUtilization")
    scope = MetricScope(
        "oci", 3, "tenancy", "sa-saopaulo-1", ("private-ocid",),
        enabled=enabled,
    )
    return QueryPlan(
        descriptor, scope,
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        60, ("mean", "p95"),
    )


def test_discovery_is_limited_to_the_allowlist_and_fetch_preserves_partial_stats(monkeypatch):
    monkeypatch.setattr(oci_metrics, "_summarize_details", lambda query, text, minutes: text)
    client = FakeMonitoringClient()
    adapter = oci_metrics.OciMetricsAdapter(client, "compartment-ocid")

    assert adapter.discover(_plan().scope) == ("private-ocid",)
    result = adapter.fetch(_plan())

    assert result.status_code == 400
    assert result.error_code == "InvalidParameter"
    assert len(result.points) == 1
    assert result.points[0].values == {"mean": 42.5}
    assert result.points[0].dimensions["nicId"] == "nic-a"
    assert result.sanitized_request["resource_count"] == 1
    assert "private-ocid" not in str(result.sanitized_request)
    assert result.response_bytes > 0
    assert len(result.raw_payloads) == 2
    assert result.raw_payloads[0]["resource_id"] == "private-ocid"
    assert result.raw_payloads[0]["payload"]
    assert result.raw_payloads[1]["error_code"] == "InvalidParameter"
    assert len(client.requests) == 2
    assert all(request[0] == "compartment-ocid" for request in client.requests)


def test_fetch_fails_closed_when_scope_is_disabled():
    adapter = oci_metrics.OciMetricsAdapter(FakeMonitoringClient(), "compartment-ocid")
    with pytest.raises(PermissionError, match="disabled"):
        adapter.fetch(_plan(enabled=False))


def test_metric_results_merge_to_wide_silver_row_and_keep_native_percentiles():
    plan = _plan()
    timestamp = plan.start_utc
    dims = {"resourceId": "private-ocid", "volumeId": "volume-a"}
    results = [
        SimpleNamespace(
            plan=plan, points=[SimpleNamespace(timestamp_utc=timestamp, dimensions=dims,
                values={"mean": 12.5}, source_sample_period_seconds=10)],
            status_code=200, error_code=None,
        ),
        SimpleNamespace(
            plan=plan, points=[SimpleNamespace(timestamp_utc=timestamp, dimensions=dims,
                values={"p90": 20.0, "p95": 25.0, "p99": 30.0},
                source_sample_period_seconds=10)],
            status_code=200, error_code=None,
        ),
    ]

    row, = query_results_to_silver_rows(
        results, source_owner="oci_metrics", collection_run_id="run-1",
        observed_at=timestamp,
    )

    assert row["value"] == 12.5
    assert row["native_p90"] == 20.0
    assert row["native_p95"] == 25.0
    assert row["native_p99"] == 30.0
    assert row["resolution_seconds"] == 60
    assert row["source_sample_period_seconds"] == 10
    assert row["dimensions_json"] == '{"resourceId":"private-ocid","volumeId":"volume-a"}'
    assert row["metric_day"] == "20260101"
