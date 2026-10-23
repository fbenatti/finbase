from datetime import datetime, timedelta, timezone

from app.services.infrastructure_metrics.opportunities import (
    INCOMPLETE, KEEP, MISSING_CAPACITY, MISSING_PRICE, READY, RIGHTSIZE_DOWN, RIGHTSIZE_UP,
    analyze_resource, summarize_series,
)
from app.services.infrastructure_metrics.providers.oci_rightsizing import (
    OCI_FLEX_RULES, OciFlexPriceBook, effective_unit_prices,
)
from app.services.infrastructure_metrics.sizing import (
    CapacitySnapshot, FlexSizingCatalog, SizeOption, SkuSizingCatalog,
)

START = datetime(2026, 10, 16, tzinfo=timezone.utc)
OCI_VM = CapacitySnapshot(
    provider="oci", resource_id="r1", resource_name="vm-a", sku="VM.Optimized3.Flex",
    family="VM.Optimized3.Flex", vcpus=10.0, memory_gb=32.0, attributes={"ocpu": 5.0},
)
OCI_CATALOG = FlexSizingCatalog(OCI_FLEX_RULES)
OCI_PRICES = OciFlexPriceBook(ocpu_hour=0.28, memory_gb_hour=0.0079, currency="BRL")


def _stats(p95, p99, peak, coverage=1.0, days=7):
    return {"p50": p95 / 2, "p95": p95, "p99": p99, "max": peak, "coverage": coverage,
            "days_observed": days, "samples": 2016, "expected_buckets": 2016}


def _oci(cpu, memory, prices=OCI_PRICES, capacity=OCI_VM):
    return analyze_resource(resource_id="r1", capacity=capacity, cpu=cpu, memory=memory,
                            catalog=OCI_CATALOG, price_book=prices)


def test_summarize_series_uses_exact_linear_quantiles_and_peak():
    rows = [
        {"bucket_start_utc": (START + timedelta(minutes=5 * i)).isoformat(),
         "value_average": 100.0 if i == 59 else 10.0, "value_maximum": 100.0 if i == 59 else 12.0}
        for i in range(60)
    ]
    stats = summarize_series(rows, window_start=START, window_end=START + timedelta(hours=5), resolution_seconds=300)
    assert stats["samples"] == 60 and stats["coverage"] == 1.0
    assert stats["p95"] == 10.0
    assert round(stats["p99"], 2) == 46.9
    assert stats["max"] == 100.0


def test_idle_flex_vm_is_rightsized_down_with_real_price_saving():
    result = _oci(_stats(8, 15, 18), _stats(30, 35, 40))
    assert result["readiness_status"] == READY
    assert result["action_type"] == RIGHTSIZE_DOWN
    # 10 vCPU × max(8%/70%, 15%/90%) = 1,67 vCPU → 1 OCPU (2 vCPU)
    assert result["to_config"]["vcpus"] == 2.0 and result["to_config"]["ocpu"] == 1.0
    # 32 GB × max(30%/80%, 35%/90%) = 12,44 GB → 13 GB
    assert result["to_config"]["memory_gb"] == 13.0
    expected = 730 * ((5 - 1) * 0.28 + (32 - 13) * 0.0079)
    assert abs(result["monthly_saving"] - round(expected, 2)) < 0.02
    assert result["confidence"] == "HIGH"


def test_peak_above_target_capacity_is_flagged_as_risk():
    result = _oci(_stats(8, 15, 90), _stats(30, 35, 40))
    assert result["risks"] and result["confidence"] == "MEDIUM"


def test_hot_vm_is_sized_up_with_negative_saving():
    result = _oci(_stats(85, 97, 100), _stats(30, 35, 40))
    assert result["action_type"] == RIGHTSIZE_UP
    assert result["monthly_saving"] < 0


def test_balanced_vm_keeps_size():
    assert _oci(_stats(65, 85, 90), _stats(75, 85, 90))["action_type"] == KEEP


def test_readiness_states_never_invent_saving():
    no_capacity = _oci(_stats(8, 15, 40), None, capacity=None)
    assert no_capacity["readiness_status"] == MISSING_CAPACITY and no_capacity["monthly_saving"] is None
    no_price = _oci(_stats(8, 15, 40), _stats(30, 35, 40), prices=None)
    assert no_price["readiness_status"] == MISSING_PRICE and no_price["monthly_saving"] is None
    partial = _oci(_stats(8, 15, 40, coverage=0.5, days=3), _stats(30, 35, 40))
    assert partial["readiness_status"] == INCOMPLETE and partial["confidence"] == "LOW"


def test_same_inputs_produce_same_hash():
    first = _oci(_stats(8, 15, 40), _stats(30, 35, 40))
    later = CapacitySnapshot(**{**OCI_VM.as_dict(), "observed_at_utc": "x"})
    assert first["inputs_hash"] == _oci(_stats(8, 15, 40), _stats(30, 35, 40), capacity=later)["inputs_hash"]


def test_fixed_sku_cloud_uses_the_same_engine():
    """A cloud with instance types (Azure/AWS style) only supplies data, not code."""
    family = [
        SizeOption("D2s", 2, 8), SizeOption("D4s", 4, 16), SizeOption("D8s", 8, 32), SizeOption("D16s", 16, 64),
    ]
    hourly = {"D2s": 0.1, "D4s": 0.2, "D8s": 0.4, "D16s": 0.8}

    class Prices:
        currency = "USD"
        basis = {"source": "price list fixture"}

        def hourly(self, option):
            return hourly.get(option.sku)

    vm = CapacitySnapshot(provider="azure", resource_id="vm-z", resource_name="vm-z", sku="D16s",
                          family="Dsv5", vcpus=16, memory_gb=64)
    result = analyze_resource(
        resource_id="vm-z", capacity=vm, cpu=_stats(20, 30, 45), memory=_stats(30, 40, 45),
        catalog=SkuSizingCatalog({"Dsv5": family}, price_of=Prices().hourly), price_book=Prices(),
    )
    # 16 vCPU × max(20%/70%, 30%/90%) = 5,3 vCPU; 64 GB × max(30%/80%, 40%/90%) = 28,4 GB → D8s
    assert result["to_config"]["sku"] == "D8s"
    assert result["action_type"] == RIGHTSIZE_DOWN
    assert result["monthly_saving"] == round(730 * 0.8, 2) - round(730 * 0.4, 2)


def test_effective_unit_prices_use_latest_period_with_both_meters():
    rows = [
        {"resource_id": "r1", "billing_period": "202609", "skumeter": "Optimized - X9", "unit": "OCPU Per Hour", "quantity": 3600, "cost": 1017.82, "currency": "BRL"},
        {"resource_id": "r1", "billing_period": "202609", "skumeter": "Optimized - X9 - Memory", "unit": "Gigabyte Per Hour", "quantity": 23040, "cost": 180.95, "currency": "BRL"},
        {"resource_id": "r1", "billing_period": "202610", "skumeter": "Optimized - X9", "unit": "OCPU Per Hour", "quantity": 100, "cost": 30.0, "currency": "BRL"},
    ]
    book = effective_unit_prices(rows)["r1"]
    assert book.basis["billing_period"] == "202609"
    assert round(book.ocpu_hour, 4) == round(1017.82 / 3600, 4)
    assert book.hourly(SizeOption("VM.Optimized3.Flex", 2, 4, {"ocpu": 1.0})) == book.ocpu_hour + 4 * book.memory_gb_hour
