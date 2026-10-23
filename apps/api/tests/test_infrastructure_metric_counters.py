from datetime import datetime, timedelta, timezone

from app.services.infrastructure_metrics.counters import derive_counter_rates


def _row(start, value):
    return {
        "cloud_provider": "oci", "provider_account_id": "tenancy",
        "region_id": "sa-saopaulo-1", "resource_type": "instance",
        "resource_id": "vm-a", "metric": "disk.read_bytes_counter",
        "series_id": "counter-series", "metric_kind": "COUNTER",
        "metric_unit": "bytes", "value_average": value,
        "bucket_start_utc": start, "resolution_seconds": 60,
        "_source_connection_id": 8, "_silver_owner": "oci_metrics",
        "dimensions_hash": "dimension-hash", "dimensions_json": "{}",
    }


def test_counter_rate_derivation_marks_baseline_and_counter_reset():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [
        _row(start, 1000),
        _row(start + timedelta(minutes=1), 1600),
        _row(start + timedelta(minutes=2), 200),
    ]
    derived = derive_counter_rates(rows)
    assert [item["value_rate"] for item in derived] == [None, 10.0, None]
    assert [item["quality_reason"] for item in derived] == [
        "NO_PREVIOUS_BUCKET", None, "COUNTER_RESET",
    ]
    assert derived[1]["metric"] == "disk.read_bytes_rate"
    assert derived[1]["metric_unit"] == "bytes/second"
    assert derived[1]["origin_kind"] == "DERIVED_COUNTER_DELTA"
    assert all(item["parent_series_id"] == "counter-series" for item in derived)


def test_counter_rate_does_not_bridge_missing_buckets():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    derived = derive_counter_rates([
        _row(start, 100), _row(start + timedelta(minutes=3), 400),
    ])
    assert derived[1]["value_rate"] is None
    assert derived[1]["quality_reason"] == "NON_ADJACENT_BUCKET"
