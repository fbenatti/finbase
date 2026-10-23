from datetime import datetime, timedelta, timezone

from app.services.infrastructure_metrics.opportunities import (
    INCOMPLETE, KEEP, MISSING_CAPACITY, MISSING_PRICE, READY, RIGHTSIZE_DOWN, RIGHTSIZE_UP,
    analyze_resource, effective_unit_prices, size_flex_target, summarize_series,
)

START = datetime(2026, 10, 16, tzinfo=timezone.utc)
END = START + timedelta(days=7)
CAPACITY = {"shape": "VM.Optimized3.Flex", "ocpus": 5.0, "memory_gb": 32.0, "vcpus": 10, "display_name": "vm-a"}
PRICES = {"ocpu_hour": 0.28, "memory_gb_hour": 0.0079, "currency": "BRL"}


def _stats(p95, p99, peak, coverage=1.0, days=7):
    return {"p50": p95 / 2, "p95": p95, "p99": p99, "max": peak, "coverage": coverage,
            "days_observed": days, "samples": 10080, "expected_buckets": 10080}


def test_summarize_series_uses_exact_linear_quantiles_and_peak():
    rows = [
        {"bucket_start_utc": (START + timedelta(minutes=i)).isoformat(),
         "value_average": 100.0 if i == 59 else 10.0, "value_maximum": 100.0 if i == 59 else 12.0}
        for i in range(60)
    ]
    stats = summarize_series(rows, window_start=START, window_end=START + timedelta(hours=1))
    assert stats["samples"] == 60 and stats["coverage"] == 1.0
    assert stats["p95"] == 10.0
    assert round(stats["p99"], 2) == 46.9
    assert stats["max"] == 100.0


def test_idle_vm_is_rightsized_down_with_real_price_saving():
    result = analyze_resource(resource_id="r1", capacity=CAPACITY, cpu=_stats(8, 15, 18),
                              memory=_stats(30, 35, 40), prices=PRICES)
    assert result["readiness_status"] == READY
    assert result["action_type"] == RIGHTSIZE_DOWN
    assert result["to_config"]["ocpus"] == 1.0
    # 32 GB × max(30%/80%, 35%/90%) = 12.44 GB → 13 GB
    assert result["to_config"]["memory_gb"] == 13.0
    expected = 730 * ((5 - 1) * 0.28 + (32 - 13) * 0.0079)
    assert abs(result["monthly_saving"] - round(expected, 2)) < 0.02
    assert result["confidence"] == "HIGH"


def test_peak_above_target_capacity_is_flagged_as_risk():
    result = analyze_resource(resource_id="r1", capacity=CAPACITY, cpu=_stats(8, 15, 90),
                              memory=_stats(30, 35, 40), prices=PRICES)
    assert result["risks"] and result["confidence"] == "MEDIUM"


def test_hot_vm_is_sized_up_with_negative_saving():
    result = analyze_resource(resource_id="r1", capacity=CAPACITY, cpu=_stats(85, 97, 100),
                              memory=_stats(30, 35, 40), prices=PRICES)
    assert result["action_type"] == RIGHTSIZE_UP
    assert result["monthly_saving"] < 0


def test_balanced_vm_keeps_size():
    target = size_flex_target(CAPACITY, _stats(65, 85, 90), _stats(75, 85, 90))
    assert target["ocpus"] == 5.0 and target["memory_gb"] == 32.0
    result = analyze_resource(resource_id="r1", capacity=CAPACITY, cpu=_stats(65, 85, 90),
                              memory=_stats(75, 85, 90), prices=PRICES)
    assert result["action_type"] == KEEP


def test_readiness_states_never_invent_saving():
    no_capacity = analyze_resource(resource_id="r1", capacity=None, cpu=_stats(8, 15, 40), memory=None, prices=PRICES)
    assert no_capacity["readiness_status"] == MISSING_CAPACITY and no_capacity["monthly_saving"] is None
    no_price = analyze_resource(resource_id="r1", capacity=CAPACITY, cpu=_stats(8, 15, 40),
                                memory=_stats(30, 35, 40), prices=None)
    assert no_price["readiness_status"] == MISSING_PRICE and no_price["monthly_saving"] is None
    partial = analyze_resource(resource_id="r1", capacity=CAPACITY, cpu=_stats(8, 15, 40, coverage=0.5, days=3),
                               memory=_stats(30, 35, 40), prices=PRICES)
    assert partial["readiness_status"] == INCOMPLETE and partial["confidence"] == "LOW"


def test_same_inputs_produce_same_hash():
    first = analyze_resource(resource_id="r1", capacity=CAPACITY, cpu=_stats(8, 15, 40), memory=_stats(30, 35, 40), prices=PRICES)
    second = analyze_resource(resource_id="r1", capacity=dict(CAPACITY, observed_at_utc="x"), cpu=_stats(8, 15, 40),
                              memory=_stats(30, 35, 40), prices=PRICES)
    assert first["inputs_hash"] == second["inputs_hash"]


def test_effective_unit_prices_use_latest_period_with_both_meters():
    rows = [
        {"resource_id": "r1", "billing_period": "202609", "skumeter": "Optimized - X9", "unit": "OCPU Per Hour", "quantity": 3600, "cost": 1017.82, "currency": "BRL"},
        {"resource_id": "r1", "billing_period": "202609", "skumeter": "Optimized - X9 - Memory", "unit": "Gigabyte Per Hour", "quantity": 23040, "cost": 180.95, "currency": "BRL"},
        {"resource_id": "r1", "billing_period": "202610", "skumeter": "Optimized - X9", "unit": "OCPU Per Hour", "quantity": 100, "cost": 30.0, "currency": "BRL"},
    ]
    prices = effective_unit_prices(rows)
    assert prices["r1"]["billing_period"] == "202609"
    assert round(prices["r1"]["ocpu_hour"], 4) == round(1017.82 / 3600, 4)
