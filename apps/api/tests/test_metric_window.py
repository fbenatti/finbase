from datetime import date

import pytest
from fastapi import HTTPException


def test_default_metric_window_is_last_30_closed_utc_days():
    from app.services.metric_window import resolve_metric_day_window

    window = resolve_metric_day_window(today_utc=date(2026, 9, 25))
    assert (window.from_day, window.to_day, window.requested_days) == ("20260826", "20260924", 30)


def test_explicit_metric_window_bounds_are_inclusive_and_limited():
    from app.services.metric_window import resolve_metric_day_window

    window = resolve_metric_day_window("20260918", "20260924", today_utc=date(2026, 9, 25))
    assert window.requested_days == 7
    assert window.as_dict(observed_days=7) == {
        "from_day": "20260918", "to_day": "20260924",
        "requested_days": 7, "observed_days": 7,
    }


@pytest.mark.parametrize(
    "bounds",
    [
        ("20260918", None),
        ("2026-09-18", "20260924"),
        ("20260925", "20260925"),
        ("20260925", "20260918"),
        ("20260601", "20260924"),
    ],
)
def test_invalid_metric_windows_are_rejected(bounds):
    from app.services.metric_window import resolve_metric_day_window

    with pytest.raises(HTTPException):
        resolve_metric_day_window(*bounds, today_utc=date(2026, 9, 25))


def test_series_partition_filter_always_scopes_connection_owner_and_days():
    from app.routers.finops_center import _metric_partition_filter
    from app.services.metric_window import resolve_metric_day_window

    window = resolve_metric_day_window("20260918", "20260924", today_utc=date(2026, 9, 25))
    where = _metric_partition_filter("m", window, owner="monitor_vm_metrics")
    assert "m._source_connection_id = 1" in where
    assert "m._silver_owner = 'monitor_vm_metrics'" in where
    assert "m._billing_period BETWEEN '20260918' AND '20260924'" in where


def test_daily_profile_sql_keeps_provenance_and_prorates_monthly_cost():
    from app.services.resource_profile_service import (
        CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, MONTHLY_COST_PRORATION_RULE,
        PROFILE_SERIES_OWNER, PROFILE_SOURCE_CONNECTION_ID, PROFILE_SQL, SAMPLES_PER_DAY,
    )
    from app.services.metric_window import resolve_metric_day_window

    window = resolve_metric_day_window("20260918", "20260924", today_utc=date(2026, 9, 25))
    sql = PROFILE_SQL.format(
        requested_days=window.requested_days,
        from_date=window.from_date.isoformat(), to_date=window.to_date.isoformat(),
        from_day=window.from_day, to_day=window.to_day,
        source_connection_id=PROFILE_SOURCE_CONNECTION_ID,
        series_owner=PROFILE_SERIES_OWNER,
        cost_proration_rule=MONTHLY_COST_PRORATION_RULE,
        samples_per_day=SAMPLES_PER_DAY,
        conf_high=CONFIDENCE_HIGH, conf_med=CONFIDENCE_MEDIUM,
    )
    assert "m._source_connection_id = 1" in sql
    assert "m._silver_owner = 'monitor_vm_metrics'" in sql
    assert "m._billing_period BETWEEN '20260918' AND '20260924'" in sql
    assert "date_diff('day', GREATEST(month_start, j.window_start)" in sql
    assert "cost_window_amount" in sql
    assert "window_from_day" in sql and "window_to_day" in sql
