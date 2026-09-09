from datetime import date
from types import SimpleNamespace

import pytest

from app.services.forecast_service import (
    _billing_period_values,
    _charge_interval_rules,
    _connection_rule_filter,
    _fetch_period_records_by_cloud,
    _normalize_allocation_rules,
    _series_matches_charge_interval,
    validate_connection_allocation_rules,
)


def _gold_metadata(*columns: str):
    return {"columns": [{"name": name, "type": "varchar"} for name in columns]}


def test_normalize_allocation_rules_prefers_explicit_rules():
    config = SimpleNamespace(
        connection_allocation_rules=[{"connection_code": "ibm-ti", "period_allocation": "charge_interval"}],
        period_allocation="none",
        charge_end_column=None,
    )
    rules = _normalize_allocation_rules(config)
    assert len(rules) == 1
    assert rules[0]["connection_code"] == "ibm-ti"


def test_normalize_allocation_rules_legacy_global_fallback():
    config = SimpleNamespace(
        connection_allocation_rules=[],
        period_allocation="charge_interval",
        charge_end_column="ChargePeriodEnd",
    )
    rules = _normalize_allocation_rules(config)
    assert len(rules) == 1
    assert rules[0].get("_global") is True


def test_connection_rule_filter_matches_cloud_origem():
    rules = [{"connection_code": "ibm-ti", "connection_id": 7}]
    clause = _connection_rule_filter(rules, "cloud_origem", "source_connection_id", negate=False)
    assert clause is not None
    assert "ibm-ti" in clause
    assert "7" in clause


def test_series_matches_charge_interval_by_cloud_origem():
    rules = [{"connection_code": "ibm-ti", "period_allocation": "charge_interval"}]
    assert _series_matches_charge_interval({"cloud_origem": "ibm-ti"}, rules, "cloud_origem", "source_connection_id")
    assert not _series_matches_charge_interval({"cloud_origem": "gcp-ttech"}, rules, "cloud_origem", "source_connection_id")


def test_validate_connection_allocation_rules_requires_identifier():
    errors = validate_connection_allocation_rules(
        [{"period_allocation": "charge_interval"}],
        _gold_metadata("cloud_origem", "ChargePeriodEnd", "_billing_period"),
        "_billing_period",
    )
    assert any("connection_code" in err for err in errors)


def test_validate_connection_allocation_rules_accepts_ibm_rule():
    errors = validate_connection_allocation_rules(
        [{
            "connection_code": "ibm-ti",
            "connection_id": 3,
            "period_allocation": "charge_interval",
            "charge_end_column": "ChargePeriodEnd",
        }],
        _gold_metadata("cloud_origem", "source_connection_id", "ChargePeriodEnd", "_billing_period"),
        "_billing_period",
    )
    assert errors == []


def test_billing_period_values_include_closed_invoice_months():
    # GCP-style cutoff mid-August must still load July for IBM monthly invoices.
    periods = _billing_period_values(
        date(2026, 7, 9),
        date(2026, 8, 13),
        minimum_months=3,
    )
    assert "202607" in periods
    assert "202608" in periods


def test_charge_interval_rules_include_connection_allocation_config():
    rules = _charge_interval_rules([
        {"connection_code": "ibm-ti", "connection_id": 3, "period_allocation": "charge_interval", "charge_end_column": "ChargePeriodEnd"},
        {"connection_code": "gcp-ti", "period_allocation": "none"},
    ])
    assert len(rules) == 1
    assert rules[0]["connection_id"] == 3


def test_connection_rule_filter_matches_connection_id_only():
    rules = [{"connection_id": 3, "period_allocation": "charge_interval"}]
    clause = _connection_rule_filter(rules, "cloud_origem", "source_connection_id", negate=False)
    assert clause is not None
    assert "3" in clause
    assert "source_connection_id" in clause


@pytest.mark.asyncio
async def test_cloud_fetch_batches_shards_without_removing_dimensions(monkeypatch):
    shard_names = [f"account-{index}" for index in range(65)] + [None]
    aggregation_filters = []

    async def fake_trino_execute(sql, timeout_s):
        assert "_billing_period" in sql
        return [{"shard": shard} for shard in shard_names]

    async def fake_fetch_period_records(**kwargs):
        aggregation_filters.append(kwargs["connection_filter"])
        assert kwargs["dims"] == ["cloud_origem", "subaccountname", "servicename"]
        return []

    monkeypatch.setattr("app.services.forecast_service.trino_execute", fake_trino_execute)
    monkeypatch.setattr(
        "app.services.forecast_service._fetch_period_records",
        fake_fetch_period_records,
    )

    await _fetch_period_records_by_cloud(
        source="iceberg.gold.focus",
        type_filter="tipo = 'realizado'",
        period="_billing_period",
        history_start=date(2026, 8, 3),
        effective_cutoff=date(2026, 9, 7),
        dims=["cloud_origem", "subaccountname", "servicename"],
        measure="realizado",
        date_expr="CAST(chargeperiodstart AS DATE)",
        mode="daily",
        charge_end_col=None,
        period_col="_billing_period",
        connection_filter=None,
        exclusion_filter=None,
        cloud_col="cloud_origem",
        clouds={"azure-ti": date(2026, 9, 7)},
        shard_col="subaccountname",
        minimum_monthly_points=1,
    )

    # 11 shard values become two bounded batches, plus the fallback for an
    # absent/unforeseen cloud. The configured dimensions remain untouched.
    assert len(aggregation_filters) == 3
    assert "account-0" in aggregation_filters[0]
    assert "account-63" in aggregation_filters[0]
    assert "account-64" in aggregation_filters[1]
    assert "IS NULL" in aggregation_filters[1]
