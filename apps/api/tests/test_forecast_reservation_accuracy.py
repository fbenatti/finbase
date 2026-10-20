from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from app.services import forecast_service as service


def purchase_rule():
    return SimpleNamespace(
        name="Recurring RI purchase", active=True, priority=10,
        behavior="RECURRING_MONTHLY", identification_column="chargecategory",
        identification_values=["Purchase"], identification_clauses=[], scope={},
        start_period="202001", end_period=None, charge_type="Purchase",
        growth_rate=0.0, behavior_options={"current_month_policy": "ZERO_WHEN_REALIZED"},
    )


async def simulate(monkeypatch, records, *, rules=(), as_of=date(2026, 9, 2), clouds=None):
    config = SimpleNamespace(
        id=9, destination_physical_name="focus", source_namespace="silver",
        dimensions=["cloud_origem", "project", "chargecategory", "commitmentdiscounttype"],
        date_column="chargeperiodstart", period_column="_billing_period",
        measure_column="realizado", type_column="tipo", has_type_column=True,
        cutoff_days=2, horizon_months=2, enabled_windows=[7, 14, 28],
        minimum_daily_points=7, minimum_monthly_points=3, recent_weight=0.7,
        model_mode="AUTO_BACKTEST", growth_rate=0.0083,
        connection_allocation_rules=[], period_allocation="none",
        gold_metadata={"columns": [{"name": "cloud_origem"}]},
    )
    published = {}

    async def fetch(**kwargs):
        # Emulate the SQL cutoff; future actuals must never enter this backtest.
        cutoff = kwargs["effective_cutoff"]
        return [r for r in records if pd.Timestamp(r["forecast_day"]).date() <= cutoff]

    async def cloud_cutoffs(**kwargs):
        return clouds

    async def no_interval_overrides(*args, **kwargs):
        pass

    def write(table, frame):
        published["frame"] = frame.copy()

    def planning(*args, **kwargs):
        pytest.fail("Simulated accuracy must not replace current planning")

    monkeypatch.setattr(service, "_fetch_period_records_by_cloud", fetch)
    monkeypatch.setattr(service, "_fetch_cloud_cutoffs", cloud_cutoffs)
    monkeypatch.setattr(service, "_apply_interval_cloud_cutoff_overrides", no_interval_overrides)
    monkeypatch.setattr(service.iceberg, "write_forecast_result", write)
    monkeypatch.setattr(service.iceberg, "write_forecast_planning", planning)
    if as_of is None:
        async def enrich(output, *args):
            return output
        monkeypatch.setattr(service, "_enrich_planning_snapshot", enrich)
        monkeypatch.setattr(service.iceberg, "write_forecast_planning", lambda *a, **k: "planning")
    await service.run_forecast(config, [], business_rules=list(rules), simulated_as_of=as_of)
    return published["frame"]


def record(day, category, value, *, cloud="azure-ti"):
    return dict(forecast_day=pd.Timestamp(day), value=value, cloud_origem=cloud,
                project="FABRIC", chargecategory=category, commitmentdiscounttype="Reservation")


@pytest.mark.asyncio
async def test_ri_return_ending_midmonth_does_not_create_orphan_credit(monkeypatch):
    records = []
    for day in pd.date_range("2026-07-01", "2026-08-31"):
        value = 1000.0 if day.month == 7 or day.day <= 12 else 0.0
        records.append(record(day, "Usage", value))
        if value:
            records.append(record(day, "Return", -value))
    records += [record("2026-07-01", "Purchase", 400), record("2026-08-01", "Purchase", 500)]
    # September's outcome is unavailable at the start-of-month cutoff.
    records.append(record("2026-09-30", "Usage", 999999))
    output = await simulate(monkeypatch, records, rules=[purchase_rule()])
    september = output[output.forecast_month.eq("202609")]
    assert set(output.forecast_month) == {"202609", "202610"}
    assert september.loc[september.chargecategory.eq("Return"), "forecast_value"].sum() == 0
    assert september.loc[september.chargecategory.eq("Usage"), "forecast_value"].sum() == 0
    assert september.loc[september.chargecategory.eq("Purchase"), "forecast_value"].sum() == 500
    assert september.forecast_value.sum() == 500


@pytest.mark.asyncio
async def test_continuous_reservation_usage_and_returns_remain_balanced(monkeypatch):
    records = [record(day, category, sign * (100 + day.day))
               for day in pd.date_range("2026-07-01", "2026-08-31")
               for category, sign in [("Usage", 1), ("Return", -1)]]
    output = await simulate(monkeypatch, records)
    assert output.groupby("forecast_month").forecast_value.sum().abs().max() < 1e-8
    assert output.loc[output.chargecategory.eq("Return"), "forecast_value"].min() < 0


@pytest.mark.asyncio
async def test_sparse_monthly_reservation_usage_and_returns_use_symmetric_models(monkeypatch):
    records = [record(day, category, sign * value)
               for day, value in [("2026-07-01", 400), ("2026-08-01", 500)]
               for category, sign in [("Usage", 1), ("Return", -1)]]
    output = await simulate(monkeypatch, records)
    assert set(output.selected_method) == {"MONTHLY_LUMP"}
    assert output.groupby("forecast_month").forecast_value.sum().abs().max() < 1e-8


@pytest.mark.asyncio
async def test_origin_cutoff_is_kept_when_other_cloud_is_more_current(monkeypatch):
    records = [record(day, "Usage", 10, cloud="lagged")
               for day in pd.date_range("2026-08-01", "2026-08-12")]
    records += [record(day, "Usage", 20, cloud="current")
                for day in pd.date_range("2026-08-01", "2026-08-31")]
    output = await simulate(monkeypatch, records, as_of=None,
                            clouds={"lagged": date(2026, 8, 12), "current": date(2026, 8, 31)})
    lagged = output[output.cloud_origem.eq("lagged") & output.forecast_month.eq("202608")]
    assert lagged.forecast_value.sum() == 10 * 19


def test_month_end_ri_anchor_uses_latest_closed_month_but_open_month_uses_prior():
    daily = pd.Series([400.0, 500.0], index=pd.to_datetime(["2026-07-01", "2026-08-01"]))
    assert service._build_series_context(daily, date(2026, 8, 31))["last_closed_month_total"] == 500
    assert service._build_series_context(daily, date(2026, 8, 15))["last_closed_month_total"] == 400
