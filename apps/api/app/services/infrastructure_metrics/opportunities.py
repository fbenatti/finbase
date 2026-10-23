"""Deterministic rightsizing opportunities for OCI Flex instances.

Inputs are explicit and hashed: the 1m NATIVE Silver series (CPU/memory),
a read-only capacity snapshot and the effective unit prices observed in the
billing Silver for the same resource/SKU. Without capacity or a comparable
price, the engine reports the readiness state instead of a saving.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import hashlib
import math
from typing import Iterable, Mapping

from .identity import canonical_json
from .profiles import _linear_quantile

ENGINE_VERSION = "oci_flex_rightsizing_v1"
HOURS_PER_MONTH = 730

READY = "READY_FOR_CAPACITY_ANALYSIS"
INCOMPLETE = "INCOMPLETE_TELEMETRY"
MISSING_CAPACITY = "MISSING_CAPACITY"
MISSING_PRICE = "MISSING_COMPARABLE_PRICE"

KEEP = "KEEP"
RIGHTSIZE_DOWN = "RIGHTSIZE_DOWN"
RIGHTSIZE_UP = "RIGHTSIZE_UP"

DEFAULT_POLICY = {
    "version": "rightsizing_policy_v1",
    "cpu_target_p95": 0.70,
    "cpu_target_p99": 0.90,
    "memory_target_p95": 0.80,
    "memory_target_p99": 0.90,
    "min_days": 7,
    "min_coverage": 0.90,
    "high_confidence_coverage": 0.98,
    # Reductions smaller than this keep the current size (avoid churn by noise).
    "min_change_ratio": 0.15,
}

# Flex limits per shape (OCI documentation, "oci_flex_rules_v1").
FLEX_SHAPES = {
    "VM.Standard.E4.Flex": {"min_ocpus": 1, "max_ocpus": 64, "min_gb_per_ocpu": 1, "max_gb_per_ocpu": 64, "max_memory_gb": 1024, "vcpus_per_ocpu": 2},
    "VM.Standard.E5.Flex": {"min_ocpus": 1, "max_ocpus": 94, "min_gb_per_ocpu": 1, "max_gb_per_ocpu": 64, "max_memory_gb": 1049, "vcpus_per_ocpu": 2},
    "VM.Standard3.Flex": {"min_ocpus": 1, "max_ocpus": 32, "min_gb_per_ocpu": 1, "max_gb_per_ocpu": 64, "max_memory_gb": 512, "vcpus_per_ocpu": 2},
    "VM.Optimized3.Flex": {"min_ocpus": 1, "max_ocpus": 18, "min_gb_per_ocpu": 1, "max_gb_per_ocpu": 64, "max_memory_gb": 256, "vcpus_per_ocpu": 2},
    "VM.Standard.A1.Flex": {"min_ocpus": 1, "max_ocpus": 80, "min_gb_per_ocpu": 1, "max_gb_per_ocpu": 64, "max_memory_gb": 512, "vcpus_per_ocpu": 1},
}


def summarize_series(
    rows: Iterable[Mapping], *, window_start: datetime, window_end: datetime,
    resolution_seconds: int = 60,
) -> dict:
    """Exact linear quantiles over bucket averages plus the observed 1m peak."""
    by_bucket: dict[str, Mapping] = {}
    for row in rows:
        if row.get("value_average") is None:
            continue
        by_bucket[str(row["bucket_start_utc"])] = row
    values = [float(row["value_average"]) for row in by_bucket.values()]
    maxima = [float(row["value_maximum"]) for row in by_bucket.values() if row.get("value_maximum") is not None]
    expected = int((window_end - window_start).total_seconds() // resolution_seconds)
    origins: dict[str, int] = defaultdict(int)
    for row in by_bucket.values():
        origins[str(row.get("origin_kind") or "NATIVE")] += 1
    daily: dict[str, list[float]] = defaultdict(list)
    for bucket, row in by_bucket.items():
        daily[bucket[:10]].append(float(row["value_average"]))
    return {
        "samples": len(values),
        "expected_buckets": expected,
        "coverage": round(len(values) / expected, 6) if expected else 0.0,
        "days_observed": len(daily),
        "p50": _linear_quantile(values, 50),
        "p95": _linear_quantile(values, 95),
        "p99": _linear_quantile(values, 99),
        "average": sum(values) / len(values) if values else None,
        "max": max(maxima) if maxima else (max(values) if values else None),
        "daily": [
            {"day": day, "p95": _linear_quantile(day_values, 95), "max": max(day_values),
             "average": sum(day_values) / len(day_values)}
            for day, day_values in sorted(daily.items())
        ],
        "quantile_method": "linear",
        "resolution_seconds": resolution_seconds,
        "origins": dict(sorted(origins.items())),
        "percentile_basis": f"WINDOW_OF_{resolution_seconds // 60}M_BUCKET_AVERAGES",
    }


def _required(capacity: float, p95: float | None, p99: float | None, t95: float, t99: float) -> float | None:
    if p95 is None or p99 is None:
        return None
    return max(capacity * p95 / 100 / t95, capacity * p99 / 100 / t99)


def size_flex_target(capacity: Mapping, cpu: Mapping, memory: Mapping | None, policy: Mapping = DEFAULT_POLICY) -> dict | None:
    """Smallest Flex size keeping P95/P99 utilization under the policy targets."""
    rules = FLEX_SHAPES.get(str(capacity.get("shape")))
    ocpus, memory_gb = capacity.get("ocpus"), capacity.get("memory_gb")
    if not rules or not ocpus or not memory_gb:
        return None
    need_ocpus = _required(ocpus, cpu.get("p95"), cpu.get("p99"), policy["cpu_target_p95"], policy["cpu_target_p99"])
    if need_ocpus is None:
        return None
    target_ocpus = min(max(math.ceil(need_ocpus - 1e-9), rules["min_ocpus"]), rules["max_ocpus"])
    if memory:
        need_gb = _required(memory_gb, memory.get("p95"), memory.get("p99"),
                            policy["memory_target_p95"], policy["memory_target_p99"])
    else:
        need_gb = None
    # Without memory telemetry the memory size is preserved, never guessed.
    target_gb = memory_gb if need_gb is None else math.ceil(need_gb - 1e-9)
    if target_gb > target_ocpus * rules["max_gb_per_ocpu"]:
        target_ocpus = min(math.ceil(target_gb / rules["max_gb_per_ocpu"]), rules["max_ocpus"])
    target_gb = min(max(target_gb, target_ocpus * rules["min_gb_per_ocpu"]), rules["max_memory_gb"],
                    target_ocpus * rules["max_gb_per_ocpu"])
    min_change = policy.get("min_change_ratio", 0)
    if target_ocpus < ocpus and (ocpus - target_ocpus) / ocpus < min_change:
        target_ocpus = ocpus
    if target_gb < memory_gb and (memory_gb - target_gb) / memory_gb < min_change:
        target_gb = memory_gb
    return {
        "shape": capacity["shape"],
        "ocpus": float(target_ocpus),
        "memory_gb": float(target_gb),
        "vcpus": int(target_ocpus * rules["vcpus_per_ocpu"]),
        "memory_sized_from_telemetry": need_gb is not None,
    }


def monthly_cost(config: Mapping, prices: Mapping) -> float:
    return HOURS_PER_MONTH * (config["ocpus"] * prices["ocpu_hour"] + config["memory_gb"] * prices["memory_gb_hour"])


def analyze_resource(
    *, resource_id: str, capacity: Mapping | None, cpu: Mapping | None, memory: Mapping | None,
    prices: Mapping | None, policy: Mapping = DEFAULT_POLICY,
) -> dict:
    capacity = capacity or {}
    current = None
    if capacity.get("ocpus") and capacity.get("memory_gb"):
        current = {"shape": capacity.get("shape"), "ocpus": capacity["ocpus"],
                   "memory_gb": capacity["memory_gb"], "vcpus": capacity.get("vcpus")}
    hash_inputs = {
        "engine": ENGINE_VERSION, "policy": dict(policy), "resource_id": resource_id,
        "capacity": {k: v for k, v in capacity.items() if k != "observed_at_utc"},
        "cpu": {k: v for k, v in (cpu or {}).items() if k != "daily"},
        "memory": {k: v for k, v in (memory or {}).items() if k != "daily"},
        "prices": dict(prices or {}),
    }
    result = {
        "resource_id": resource_id,
        "resource_name": capacity.get("display_name") or resource_id,
        "engine_version": ENGINE_VERSION,
        "policy_version": policy["version"],
        "inputs_hash": hashlib.sha256(canonical_json(hash_inputs).encode()).hexdigest(),
        "from_config": current,
        "to_config": None,
        "action_type": KEEP,
        "monthly_saving": None,
        "current_monthly_cost": None,
        "target_monthly_cost": None,
        "currency": (prices or {}).get("currency"),
        "confidence": "LOW",
        "risks": [],
        "rationale": [],
        "evidence": {"cpu": cpu, "memory": memory, "capacity": dict(capacity), "prices": prices, "policy": dict(policy)},
    }
    if current is None or capacity.get("shape") not in FLEX_SHAPES:
        result["readiness_status"] = MISSING_CAPACITY
        result["rationale"].append("Capacidade atual (shape Flex, OCPU e memória) não comprovada.")
        return result
    if not cpu or cpu.get("p95") is None:
        result["readiness_status"] = INCOMPLETE
        result["rationale"].append("Sem série de CPU na janela analisada.")
        return result
    coverages = [stats["coverage"] for stats in (cpu, memory) if stats]
    days = min(stats["days_observed"] for stats in (cpu, memory) if stats)
    coverage = min(coverages)
    target = size_flex_target(capacity, cpu, memory, policy)
    result["to_config"] = target
    if target["ocpus"] == current["ocpus"] and target["memory_gb"] == current["memory_gb"]:
        result["action_type"] = KEEP
    elif target["ocpus"] <= current["ocpus"] and target["memory_gb"] <= current["memory_gb"]:
        result["action_type"] = RIGHTSIZE_DOWN
    else:
        result["action_type"] = RIGHTSIZE_UP
    result["rationale"].append(
        f"CPU P95 {cpu['p95']:.1f}% / P99 {cpu['p99']:.1f}% de {current['ocpus']:g} OCPU; "
        f"alvo mantém P95 ≤ {policy['cpu_target_p95']:.0%} e P99 ≤ {policy['cpu_target_p99']:.0%}."
    )
    if memory and memory.get("p95") is not None:
        result["rationale"].append(
            f"Memória P95 {memory['p95']:.1f}% / P99 {memory['p99']:.1f}% de {current['memory_gb']:g} GB; "
            f"alvo mantém P95 ≤ {policy['memory_target_p95']:.0%} e P99 ≤ {policy['memory_target_p99']:.0%}."
        )
    else:
        result["risks"].append("Memória sem telemetria: tamanho de memória preservado.")
    projected = {
        "cpu_p95": cpu["p95"] * current["ocpus"] / target["ocpus"],
        "cpu_p99": cpu["p99"] * current["ocpus"] / target["ocpus"],
        "cpu_peak_1m": (cpu["max"] or 0) * current["ocpus"] / target["ocpus"],
    }
    if memory and memory.get("p95") is not None:
        projected["memory_p95"] = memory["p95"] * current["memory_gb"] / target["memory_gb"]
        projected["memory_p99"] = memory["p99"] * current["memory_gb"] / target["memory_gb"]
    result["evidence"]["projected_utilization"] = projected
    if projected["cpu_peak_1m"] > 100:
        result["risks"].append(
            f"Pico observado ({cpu['max']:.1f}%) equivaleria a {projected['cpu_peak_1m']:.0f}% no alvo: haverá fila de CPU nesses momentos."
        )
    if days < policy["min_days"] or coverage < policy["min_coverage"]:
        result["readiness_status"] = INCOMPLETE
        result["rationale"].append(f"Telemetria insuficiente: {days} dia(s), cobertura {coverage:.0%}.")
    elif not prices or not prices.get("ocpu_hour") or not prices.get("memory_gb_hour"):
        result["readiness_status"] = MISSING_PRICE
        result["rationale"].append("Sem preço efetivo comparável no billing para OCPU e memória deste recurso.")
    else:
        result["readiness_status"] = READY
    if prices and prices.get("ocpu_hour") and prices.get("memory_gb_hour"):
        result["current_monthly_cost"] = round(monthly_cost(current, prices), 2)
        result["target_monthly_cost"] = round(monthly_cost(target, prices), 2)
        result["monthly_saving"] = round(result["current_monthly_cost"] - result["target_monthly_cost"], 2)
    if result["readiness_status"] == READY:
        if coverage >= policy["high_confidence_coverage"] and projected["cpu_peak_1m"] <= 100:
            result["confidence"] = "HIGH"
        else:
            result["confidence"] = "MEDIUM"
    return result


def effective_unit_prices(billing_rows: Iterable[Mapping]) -> dict[str, dict]:
    """Effective price per OCPU-hour and GB-hour, per resource, from billing.

    Uses the most recent billing period that has both meters for the resource.
    """
    grouped: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    currencies: dict[str, str] = {}
    meters: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    for row in billing_rows:
        quantity = float(row.get("quantity") or 0)
        if quantity <= 0:
            continue
        unit = str(row.get("unit") or "").lower()
        meter = str(row.get("skumeter") or "")
        kind = "memory_gb_hour" if ("gigabyte" in unit or unit.startswith("gb") or "memory" in meter.lower()) else (
            "ocpu_hour" if "ocpu" in unit else None)
        if kind is None:
            continue
        key = (str(row["resource_id"]), str(row["billing_period"]))
        grouped[key][kind][0] += float(row.get("cost") or 0)
        grouped[key][kind][1] += quantity
        meters[key][kind] = meter
        currencies[str(row["resource_id"])] = str(row.get("currency") or "")
    prices: dict[str, dict] = {}
    for (resource_id, period), kinds in sorted(grouped.items(), key=lambda item: item[0][1]):
        if "ocpu_hour" not in kinds or "memory_gb_hour" not in kinds:
            continue
        prices[resource_id] = {
            "ocpu_hour": kinds["ocpu_hour"][0] / kinds["ocpu_hour"][1],
            "memory_gb_hour": kinds["memory_gb_hour"][0] / kinds["memory_gb_hour"][1],
            "currency": currencies.get(resource_id),
            "billing_period": period,
            "meters": meters[(resource_id, period)],
            "basis": "effectivecost / consumedquantity (Silver dest_billing_cloud)",
        }
    return prices


def window_bounds(as_of: datetime, days: int) -> tuple[datetime, datetime]:
    end = as_of.replace(hour=0, minute=0, second=0, microsecond=0)
    return end - timedelta(days=days), end
