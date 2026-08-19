from datetime import date
from types import SimpleNamespace

import pandas as pd

from app.services.forecast_service import (
    _apply_forecast_business_rules,
    _build_series_context,
    _replicated_month_value,
    _series_key,
)


def _rule(**kwargs):
    defaults = {
        "name": "test-rule",
        "identification_column": "",
        "identification_values": [],
        "identification_clauses": [],
        "charge_type": "Purchase",
        "behavior": "RECURRING_MONTHLY",
        "reference_column": None,
        "reference_values": [],
        "value_column": "realizado",
        "start_period": "202001",
        "end_period": None,
        "growth_rate": 0.0,
        "replacement_value": None,
        "cancel_on_refund": False,
        "scope": {},
        "priority": 10,
        "active": True,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _marketplace_context():
    index = pd.to_datetime(["2026-06-15"])
    daily = pd.Series([8_561_121.0], index=index)
    return _build_series_context(daily, date(2026, 8, 18))


def test_last_closed_month_uses_last_nonzero_closed_month():
    ctx = _marketplace_context()
    assert ctx["last_closed_month_total"] == 8_561_121.0


def test_last_closed_month_is_zero_when_no_prior_nonzero_month():
    index = pd.date_range("2026-08-01", periods=10, freq="D")
    daily = pd.Series([100.0] * len(index), index=index)
    ctx = _build_series_context(daily, date(2026, 8, 18))
    assert ctx["last_closed_month_total"] == 0.0


def test_recurring_monthly_uses_last_nonzero_closed_month():
    index = pd.date_range("2026-07-01", periods=31, freq="D")
    daily = pd.Series([100.0] * len(index), index=index)
    ctx = _build_series_context(daily, date(2026, 8, 18))
    assert ctx["last_closed_month_total"] == 3100.0
    key_cols = ["cloud_origem", "servicename"]
    key_values = {"cloud_origem": "oci-prod", "servicename": "COMPUTE"}
    output = pd.DataFrame(
        [
            {**key_values, "forecast_month": "202608", "tipo": "forecast", "forecast_value": 999.0},
            {**key_values, "forecast_month": "202609", "tipo": "forecast", "forecast_value": 999.0},
        ]
    )
    rule = _rule(
        identification_column="servicename",
        identification_values=["COMPUTE"],
        scope={"cloud_origem": "oci-prod"},
        behavior="RECURRING_MONTHLY",
    )
    applied = _apply_forecast_business_rules(
        output,
        [rule],
        {_series_key(key_values, key_cols): ctx},
        key_cols,
        date(2026, 8, 18),
    )
    assert rule.name in applied
    assert output.loc[output["forecast_month"] == "202608", "forecast_value"].iloc[0] == 0.0
    assert output.loc[output["forecast_month"] == "202609", "forecast_value"].iloc[0] == 3100.0


def test_marketplace_oneshot_rule_zeros_forecast():
    ctx = _marketplace_context()
    key_cols = ["marketplacebillingmodel"]
    key_values = {"marketplacebillingmodel": "ONESHOT"}
    inflated_math_forecast = 128_000_000.0
    output = pd.DataFrame(
        [
            {**key_values, "forecast_month": "202608", "tipo": "forecast", "forecast_value": inflated_math_forecast},
            {**key_values, "forecast_month": "202609", "tipo": "forecast", "forecast_value": inflated_math_forecast},
        ]
    )
    rule = _rule(
        name="Marketplace ONESHOT zera forecast",
        identification_clauses=[{"column": "marketplacebillingmodel", "values": ["ONESHOT"]}],
        behavior="REPLACE_VALUE",
        replacement_value=0.0,
    )
    applied = _apply_forecast_business_rules(
        output,
        [rule],
        {_series_key(key_values, key_cols): ctx},
        key_cols,
        date(2026, 8, 18),
    )
    assert "Marketplace ONESHOT zera forecast" in applied
    assert output["forecast_value"].tolist() == [0.0, 0.0]


def test_identification_clauses_use_or_semantics():
    output = pd.DataFrame(
        [
            {"marketplacebillingmodel": "ONESHOT", "forecast_month": "202608", "tipo": "forecast", "forecast_value": 100.0},
            {"marketplacebillingmodel": "RECURRING", "forecast_month": "202608", "tipo": "forecast", "forecast_value": 200.0},
            {"marketplacebillingmodel": "OTHER", "forecast_month": "202608", "tipo": "forecast", "forecast_value": 300.0},
        ]
    )
    rule = _rule(
        name="Marketplace billing OR",
        identification_clauses=[
            {"column": "marketplacebillingmodel", "values": ["ONESHOT"]},
            {"column": "marketplacebillingmodel", "values": ["RECURRING"]},
        ],
        behavior="REPLACE_VALUE",
        replacement_value=0.0,
    )
    applied = _apply_forecast_business_rules(
        output,
        [rule],
        {},
        ["marketplacebillingmodel"],
        date(2026, 8, 18),
    )
    assert "Marketplace billing OR" in applied
    assert output["forecast_value"].tolist() == [0.0, 0.0, 300.0]


def test_usage_series_without_purchase_rule_keeps_math_forecast():
    key_cols = ["cloud_origem", "servicename"]
    key_values = {"cloud_origem": "oci-prod", "servicename": "COMPUTE"}
    index = pd.date_range("2026-07-01", periods=30, freq="D")
    daily = pd.Series([100_000.0] * len(index), index=index)
    series_context = {_series_key(key_values, key_cols): _build_series_context(daily, date(2026, 8, 18))}
    math_value = 2_500_000.0
    output = pd.DataFrame(
        [{**key_values, "forecast_month": "202608", "tipo": "forecast", "forecast_value": math_value}]
    )
    marketplace_rule = _rule(
        name="Marketplace ONESHOT zera forecast",
        identification_clauses=[{"column": "marketplacebillingmodel", "values": ["ONESHOT"]}],
        behavior="REPLACE_VALUE",
        replacement_value=0.0,
    )
    applied = _apply_forecast_business_rules(
        output,
        [marketplace_rule],
        series_context,
        key_cols,
        date(2026, 8, 18),
    )
    assert "Marketplace ONESHOT zera forecast (não aplicada: campo fora das dimensões)" in applied
    assert output["forecast_value"].iloc[0] == math_value


def test_repeat_next_month_only_first_future_month():
    current = "202608"
    assert _replicated_month_value(
        behavior="REPEAT_NEXT_MONTH",
        forecast_month="202609",
        current_month_key=current,
        last_closed_month_total=8_561_121.0,
        current_month_realized=0.0,
        growth_rate=0.0,
    ) == 8_561_121.0
    assert (
        _replicated_month_value(
            behavior="REPEAT_NEXT_MONTH",
            forecast_month="202610",
            current_month_key=current,
            last_closed_month_total=8_561_121.0,
            current_month_realized=0.0,
            growth_rate=0.0,
        )
        is None
    )


def test_rule_without_identification_or_scope_is_not_applied_globally():
    output = pd.DataFrame(
        [
            {"cloud_origem": "oci-prod", "servicename": "COMPUTE", "forecast_month": "202608", "tipo": "forecast", "forecast_value": 100.0},
            {"cloud_origem": "gcp-ttech", "servicename": "COMPUTE", "forecast_month": "202608", "tipo": "forecast", "forecast_value": 200.0},
        ]
    )
    rule = _rule(name="Refund global", behavior="CANCEL_ON_REFUND", cancel_on_refund=True)
    applied = _apply_forecast_business_rules(
        output,
        [rule],
        {},
        ["cloud_origem", "servicename"],
        date(2026, 8, 18),
    )
    assert applied == ["Refund global (não aplicada: sem identificação/escopo)"]
    assert output["forecast_value"].tolist() == [100.0, 200.0]
