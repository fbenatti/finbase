"""Testes do motor de anomalias v2."""
from datetime import date

from app.services.anomaly_v2.baseline import expected_for_day
from app.services.anomaly_v2.classifier import NATURE_COMMITMENT, NATURE_NEW, classify_scopes
from app.services.anomaly_v2.commitment_ledger import build_ledger_from_rows
from app.services.anomaly_v2.detectors import continuous_episode, detect_commitment_day
from app.services.anomaly_v2.gates import apply_gates
from app.services.anomaly_v2.scope import scope_id_for


def test_scope_id_stable():
    values = {"cloud_origem": "azure-ti", "subaccountname": "Production", "servicename": "Microsoft.Capacity"}
    assert scope_id_for(values) == scope_id_for(values)


def test_classify_commitment():
    rows = [
        {"scope_id": "a", "day": "2026-10-10", "cost_cloud": 100.0, "chargecategory": "Purchase", "commitmentdiscounttype": "Reservation"},
    ]
    profiles = classify_scopes(rows, as_of=date(2026, 10, 10))
    assert profiles["a"]["cost_nature"] == NATURE_COMMITMENT


def test_ondemand_usage_is_not_commitment():
    rows = [
        {"scope_id": "d", "day": f"2026-09-{day:02d}", "cost_cloud": 100.0, "chargecategory": "Usage", "commitmentdiscounttype": "OnDemand"}
        for day in range(1, 21)
    ]
    profiles = classify_scopes(rows, as_of=date(2026, 9, 20))
    assert profiles["d"]["cost_nature"] != NATURE_COMMITMENT
    rows = [{"scope_id": "b", "day": "2026-10-10", "cost_cloud": 50.0, "chargecategory": "Usage"}]
    profiles = classify_scopes(rows, as_of=date(2026, 10, 10))
    assert profiles["b"]["cost_nature"] == NATURE_NEW


def test_commitment_on_calendar():
    ledger = build_ledger_from_rows(
        [
            {
                "day": "2026-09-10",
                "resource_id": "/providers/Microsoft.Capacity/reservationOrders/x",
                "cost_cloud": 29594.0,
                "cloud_origem": "azure-ti",
                "subaccountname": "Production",
                "servicename": "Microsoft.Capacity",
            },
            {
                "day": "2026-10-10",
                "resource_id": "/providers/Microsoft.Capacity/reservationOrders/x",
                "cost_cloud": 29594.0,
                "cloud_origem": "azure-ti",
                "subaccountname": "Production",
                "servicename": "Microsoft.Capacity",
            },
        ]
    )
    sid = ledger[0]["scope_id"]
    hit = detect_commitment_day(
        scope_id=sid,
        day=date(2026, 10, 10),
        observed=29594.0,
        ledger_entries=ledger,
    )
    assert hit is not None
    assert hit["subtype"] == "ON_CALENDAR"


def test_gate_materiality():
    gated = apply_gates(
        {"delta": 50, "pct_delta": 2.0, "observed": 150, "detector": "SPIKE"},
        materiality_daily=500,
        materiality_monthly=3000,
        min_pct=0.2,
        cloud_ratio=1.0,
    )
    assert gated["tier"] == "WATCH"


def test_baseline_expected():
    daily = [{"day": f"2026-10-{d:02d}", "cost_cloud": 100.0, "scope_id": "s"} for d in range(1, 15)]
    expected, lower, upper, _mad = expected_for_day(daily, target=date(2026, 10, 15))
    assert expected > 0
    assert upper >= expected >= lower


def test_spike_that_returns_is_not_an_open_case():
    rows = []
    for day in range(1, 21):
        rows.append({"scope_id": "s", "day": f"2026-09-{day:02d}", "cost_cloud": 100.0})
    rows.append({"scope_id": "s", "day": "2026-09-21", "cost_cloud": 5000.0})
    for day in range(22, 29):
        rows.append({"scope_id": "s", "day": f"2026-09-{day:02d}", "cost_cloud": 100.0})
    episode = continuous_episode(rows, as_of=date(2026, 9, 28), materiality=50, min_pct=0.2)
    assert episode is not None
    assert episode["deviation_status"] == "AUTOCORRIGIDO"
    assert episode["day"] == "2026-09-21"


def test_case_series_reads_the_cloud_partition_without_casting_it():
    from app.services.anomaly_v2.case_series import (
        build_case_daily_sql,
        build_case_detail_sql,
        normalize_charge_day,
        periods_overlapping,
    )

    periods = periods_overlapping(date(2026, 8, 29), date(2026, 10, 14))
    assert periods == ["202608", "202609", "202610"]
    assert normalize_charge_day("10/13/2026", "202610") == "2026-10-13"
    assert normalize_charge_day("09/07/2026", "202609") == "2026-09-07"
    daily = build_case_daily_sql(
        namespace="silver",
        table="dest_modelo_focus",
        date_column="chargeperiodstart",
        type_filter="TRUE",
        period_column="_billing_period",
        period_type="varchar",
        connection_column="source_connection_id",
        connection_ids=[2],
        measure="realizado_cloud",
        scope_filter="TRUE",
        periods=periods,
    )
    detail = build_case_detail_sql(
        namespace="silver",
        table="dest_modelo_focus",
        date_column="chargeperiodstart",
        type_filter="TRUE",
        period_column="_billing_period",
        period_type="varchar",
        connection_column="source_connection_id",
        connection_ids=[2],
        measure="realizado_cloud",
        scope_filter="TRUE",
        event_day=date(2026, 10, 13),
        specs=[("hierarquia_1", "hierarquia_1", "Organograma")],
    )
    assert daily.count("FROM iceberg.") == 1
    assert "CAST(\"_billing_period\" AS VARCHAR) IN" not in daily
    assert "\"source_connection_id\" = 2" in daily
    assert "202607" not in daily
    assert "regexp_like" not in daily
    assert detail.count("FROM iceberg.") == 1
    assert "202608" not in detail
    assert "'10/13/2026'" in detail
    assert "hierarquia_1" in detail


def test_breakdown_keeps_organogram_and_subscription_path():
    from app.services.anomaly_v2.case_series import breakdown_from_rows

    specs = [
        ("hierarquia_1", "hierarquia_1", "Hierarquia 1"),
        ("subaccountname", "subaccountname", "Assinatura / subconta"),
        ("resourcename", "resource_name", "Recurso"),
    ]
    rows = [
        {"hierarquia_1": "TI", "subaccountname": "Aura", "resource_name": "disk-a", "observed_cloud": 300},
        {"hierarquia_1": "TI", "subaccountname": "Aura", "resource_name": "disk-b", "observed_cloud": 120},
    ]
    dimensions, items, common = breakdown_from_rows(rows, specs)
    assert [item["key"] for item in dimensions] == ["hierarquia_1", "subaccountname", "resource_name"]
    assert common == {"hierarquia_1": "TI", "subaccountname": "Aura"}
    assert len(items) == 2
    assert {item["dimensions"]["resource_name"] for item in items} == {"disk-a", "disk-b"}
