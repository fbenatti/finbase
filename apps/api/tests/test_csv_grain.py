from datetime import date

import pandas as pd

from app.services.csv_upload_service import (
    analyze_csv_grain,
    detect_monthly_grain,
    is_current_billing_period,
    should_skip_gold_realizado,
)
from app.services.forecast_service import _expand_charge_interval


def test_is_current_billing_period():
    today = date.today()
    current = f"{today.year}{today.month:02d}"
    assert is_current_billing_period(current) is True
    assert is_current_billing_period("199901") is False
    assert is_current_billing_period("invalid") is False


def test_detect_monthly_grain_ibm_focus_pattern():
    df = pd.DataFrame({
        "ChargePeriodStart": ["2026-07-01", "2026-07-01"],
        "ChargePeriodEnd": ["2026-07-31", "2026-07-31"],
        "BilledCost": [100.0, 200.0],
    })
    assert detect_monthly_grain(df, "202607") is True


def test_detect_monthly_grain_daily_detail():
    df = pd.DataFrame({
        "ChargePeriodStart": [f"2026-07-{day:02d}" for day in range(1, 11)],
        "BilledCost": [10.0] * 10,
    })
    assert detect_monthly_grain(df, "202607") is False


def test_analyze_csv_grain_closed_month():
    df = pd.DataFrame({
        "ChargePeriodStart": ["2026-07-01"],
        "ChargePeriodEnd": ["2026-07-31"],
        "BilledCost": [100.0],
    })
    grain = analyze_csv_grain(df, "202607")
    assert grain["monthly_grain"] is True
    assert grain["skip_gold_realizado"] is False
    assert grain["warning_message"] is None


def test_should_skip_gold_from_metadata():
    config = {
        "manual_csv_metadata": {
            "202608": {"skip_gold_realizado": True, "monthly_grain": True},
        }
    }
    skip, message = should_skip_gold_realizado("202608", connection_config=config)
    if is_current_billing_period("202608"):
        assert skip is True
        assert message


def test_monthly_lump_baseline_from_closed_months():
    from app.services.forecast_service import _build_series_context
    index = pd.to_datetime(["2026-06-01", "2026-07-01"])
    daily = pd.Series([310.0, 620.0], index=index)
    ctx = _build_series_context(daily, date(2026, 8, 10))
    assert ctx["last_closed_month_total"] == 620.0
    assert ctx["monthly_realized"]["202607"] == 620.0


def test_expand_charge_interval_spreads_full_month():
    frame = pd.DataFrame({
        "charge_start": ["2026-07-01"],
        "charge_end": ["2026-07-31"],
        "value": [310.0],
    })
    daily = _expand_charge_interval(frame, [])
    assert len(daily) == 31
    assert abs(float(daily["value"].sum()) - 310.0) < 0.01
    assert abs(float(daily["value"].iloc[0]) - (310.0 / 31)) < 0.01


def test_expand_charge_interval_single_day():
    frame = pd.DataFrame({
        "charge_start": ["2026-07-15"],
        "charge_end": ["2026-07-15"],
        "value": [42.0],
    })
    daily = _expand_charge_interval(frame, [])
    assert len(daily) == 1
    assert float(daily["value"].iloc[0]) == 42.0


def test_expand_charge_interval_by_dimension():
    frame = pd.DataFrame({
        "cloud": ["ibm", "ibm"],
        "charge_start": ["2026-07-01", "2026-07-01"],
        "charge_end": ["2026-07-02", "2026-07-02"],
        "value": [20.0, 40.0],
    })
    daily = _expand_charge_interval(frame, ["cloud"])
    assert len(daily) == 2
    assert abs(float(daily.loc[daily["cloud"] == "ibm", "value"].sum()) - 60.0) < 0.01
    assert abs(float(daily["value"].iloc[0]) - 30.0) < 0.01
