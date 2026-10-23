from datetime import datetime, timedelta, timezone

from app.services.infrastructure_metrics.catalog import OCI_METRICS
from app.services.infrastructure_metrics.contracts import (
    MetricPoint, MetricScope, QueryPlan, QueryResult,
)
from app.services.infrastructure_metrics.coverage import build_coverage_rows


def _result(points=()):
    start = datetime(2026, 10, 23, 10, tzinfo=timezone.utc)
    scope = MetricScope("oci", 4, "tenancy", "sa-saopaulo-1", ("vm-a",), enabled=True)
    plan = QueryPlan(OCI_METRICS[0], scope, start, start + timedelta(minutes=3),
                     60, ("mean", "p95"), request_id="request-1")
    return QueryResult(plan, 200, points=tuple(points))


def test_coverage_emits_missing_window_without_filling_zero():
    row, = build_coverage_rows((_result(),), source_owner="oci_metrics", collection_run_id="r1")
    assert row["coverage_status"] == "NO_DATA"
    assert row["coverage_reason"] == "NO_SAMPLES"
    assert row["expected_buckets"] == 3
    assert row["observed_buckets"] == 0
    assert row["missing_buckets"] == 3
    assert row["coverage_ratio"] == 0
    assert row["first_point_utc"] is None


def test_coverage_counts_interior_gaps_and_duplicate_timestamps():
    start = datetime(2026, 10, 23, 10, tzinfo=timezone.utc)
    points = (
        MetricPoint(start, {"resourceId": "vm-a"}, {"mean": 0.0}),
        MetricPoint(start + timedelta(minutes=2), {"resourceId": "vm-a"}, {"mean": 4.0}),
        MetricPoint(start + timedelta(minutes=2), {"resourceId": "vm-a"}, {"mean": 5.0}),
    )
    row, = build_coverage_rows((_result(points),), source_owner="oci_metrics", collection_run_id="r2")
    assert row["observed_buckets"] == 2
    assert row["duplicate_buckets"] == 1
    assert row["missing_buckets"] == 1
    assert row["largest_gap_seconds"] == 60
    assert row["coverage_status"] == "PARTIAL"
