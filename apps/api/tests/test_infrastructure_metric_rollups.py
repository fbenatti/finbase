from datetime import datetime, timedelta, timezone

from app.services.infrastructure_metrics.rollups import build_metric_rollups


def _row(start, value, *, count=1, total=None, kind="GAUGE", p95=99.0):
    return {
        "_source_connection_id": 1, "_silver_owner": "oci_metrics",
        "cloud_provider": "oci", "provider_account_id": "tenancy",
        "region_id": "sa-saopaulo-1", "resource_id": "vm-a",
        "metric_namespace": "oci_computeagent", "source_metric_name": "CpuUtilization",
        "metric_scope": "resource", "dimensions_hash": "dimensions-v1",
        "dimension_schema_version": 1, "series_id": "series-a",
        "bucket_start_utc": start, "resolution_seconds": 60,
        "aggregation_interval_seconds": 60, "origin_kind": "NATIVE",
        "metric_kind": kind, "value": value, "value_average": value,
        "value_minimum": value, "value_maximum": value,
        "sample_count": count, "value_total": total if total is not None else value * count,
        "native_p95": p95, "collection_run_id": "source-run",
    }


def test_rollup_weights_means_and_does_not_combine_native_percentiles():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [_row(start + timedelta(minutes=i), float(i * 10), count=i + 1,
                 total=float(i * 10 * (i + 1)), p95=99) for i in range(5)]
    rollup, = build_metric_rollups(rows, collection_run_id="rollup-run", target_resolutions=(300,))
    assert rollup["value_average"] == 400 / 15
    assert rollup["native_p95"] is None
    assert rollup["origin_kind"] == "DERIVED_ROLLUP"
    assert rollup["parent_resolution_seconds"] == 60
    assert rollup["quality_status"] == "OK"


def test_rollup_marks_missing_input_and_counter_uses_last_cumulative_value():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [_row(start, 10), _row(start + timedelta(minutes=2), 30)]
    partial, = build_metric_rollups(rows, collection_run_id="r", target_resolutions=(300,))
    assert partial["quality_status"] == "PARTIAL"
    assert partial["quality_reason"] == "INCOMPLETE_OR_UNWEIGHTED_ROLLUP"

    counters = [
        _row(start + timedelta(minutes=i), float((i + 1) * 100), kind="COUNTER")
        for i in range(5)
    ]
    counter, = build_metric_rollups(counters, collection_run_id="r", target_resolutions=(300,))
    assert counter["value"] == 500
    assert counter["value_average"] == 500
    assert counter["value_rate"] is None
