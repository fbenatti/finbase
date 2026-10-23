from datetime import datetime, timedelta, timezone

import pytest

from app.services.infrastructure_metrics.profiles import build_window_profiles


def _row(start, value, *, resolution=60, series="series-a"):
    return {
        "bucket_start_utc": start,
        "value_average": value,
        "resolution_seconds": resolution,
        "series_id": series,
        "cloud_provider": "oci",
        "resource_id": "vm-a",
        "_source_connection_id": 1,
        "_silver_owner": "oci_metrics",
    }


def test_profiles_compute_linear_p90_p95_p99_and_record_basis():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [_row(start + timedelta(minutes=i), float(i * 10)) for i in range(10)]
    profile, = build_window_profiles(
        rows, window_start=start, window_end=start + timedelta(minutes=10),
        resolution_seconds=60,
    )
    assert profile["p90"] == pytest.approx(81)
    assert profile["p95"] == pytest.approx(85.5)
    assert profile["p99"] == pytest.approx(89.1)
    assert profile["percentile_basis"] == "WINDOW_OF_BUCKET_VALUES"
    assert profile["quantile_method"] == "linear"
    assert profile["coverage"] == 1


def test_profiles_ignore_other_resolutions_and_fail_on_conflicting_duplicates():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [_row(start, 1.0), _row(start, 2.0, resolution=300)]
    profile, = build_window_profiles(
        rows, window_start=start, window_end=start + timedelta(minutes=1),
        resolution_seconds=60,
    )
    assert profile["samples"] == 1
    with pytest.raises(ValueError, match="conflicting values"):
        build_window_profiles(
            [rows[0], _row(start, 99)], window_start=start,
            window_end=start + timedelta(minutes=1), resolution_seconds=60,
        )
