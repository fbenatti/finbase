"""Testes do motor de anomalias v2."""
from datetime import date

from app.services.anomaly_v2.baseline import expected_for_day
from app.services.anomaly_v2.classifier import NATURE_COMMITMENT, NATURE_NEW, classify_scopes
from app.services.anomaly_v2.commitment_ledger import build_ledger_from_rows
from app.services.anomaly_v2.detectors import detect_commitment_day
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


def test_classify_new_spend():
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
