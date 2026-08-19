from datetime import date
from types import SimpleNamespace

from app.services.forecast_service import (
    _allocation_rules_from_business_rules,
    _merge_allocation_rules,
    _series_matches_charge_interval,
)


def _rule(**kwargs):
    defaults = {
        "name": "Fatura mensal — ibm-ti",
        "identification_column": "cloud_origem",
        "identification_values": ["ibm-ti"],
        "behavior": "CHARGE_INTERVAL",
        "reference_column": "ChargePeriodEnd",
        "scope": {},
        "priority": 3,
        "active": True,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_charge_interval_business_rule_builds_allocation_rule():
    config = SimpleNamespace(charge_end_column=None)
    rules = _allocation_rules_from_business_rules(
        [_rule()],
        "cloud_origem",
        "source_connection_id",
        config,
    )
    assert len(rules) == 1
    assert rules[0]["connection_code"] == "ibm-ti"
    assert rules[0]["period_allocation"] == "charge_interval"
    assert rules[0]["charge_end_column"] == "ChargePeriodEnd"


def test_stale_interval_series_are_identifiable():
    rules = [{"connection_code": "acct-a", "period_allocation": "charge_interval"}]
    assert _series_matches_charge_interval({"cloud_origem": "acct-a"}, rules, "cloud_origem", "source_connection_id")
    assert date(2026, 7, 31) < date(2026, 8, 16).replace(day=1)


def test_merge_allocation_rules_deduplicates_connection():
    explicit = [{"connection_code": "ibm-ti", "period_allocation": "charge_interval", "charge_end_column": "ChargePeriodEnd"}]
    from_rules = [{"connection_code": "ibm-ti", "period_allocation": "charge_interval", "charge_end_column": "ChargePeriodEnd"}]
    merged = _merge_allocation_rules(explicit, from_rules)
    assert len(merged) == 1
