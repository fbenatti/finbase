"""Provider-neutral rightsizing engine.

Works only on canonical Silver metrics (``cpu.utilization`` and
``memory.utilization``) in vCPU and GB. Each cloud plugs in through
``sizing.RightsizingProvider``: how to read the current size, which sizes are
reachable and what they cost. Inputs are hashed; without capacity or a
comparable price the engine reports the readiness state instead of a saving.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import hashlib
import json
import math
from typing import Any, Iterable, Mapping

from .sizing import CapacitySnapshot, PriceBook, SizeOption, SizingCatalog

ENGINE_VERSION = "rightsizing_v2"
HOURS_PER_MONTH = 730

READY = "READY_FOR_CAPACITY_ANALYSIS"
INCOMPLETE = "INCOMPLETE_TELEMETRY"
MISSING_CAPACITY = "MISSING_CAPACITY"
MISSING_PRICE = "MISSING_COMPARABLE_PRICE"

KEEP = "KEEP"
RIGHTSIZE_DOWN = "RIGHTSIZE_DOWN"
RIGHTSIZE_UP = "RIGHTSIZE_UP"

CPU_METRIC = "cpu.utilization"
MEMORY_METRIC = "memory.utilization"

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


def canonical_json(value: Any) -> str:
    """Canonical JSON used for hashes; mapping insertion order is irrelevant."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str)


def _linear_quantile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


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


def _required(capacity: float, stats: Mapping | None, t95: float, t99: float) -> float | None:
    if not stats or stats.get("p95") is None or stats.get("p99") is None:
        return None
    return max(capacity * stats["p95"] / 100 / t95, capacity * stats["p99"] / 100 / t99)


def required_capacity(current: CapacitySnapshot, cpu: Mapping, memory: Mapping | None,
                      policy: Mapping = DEFAULT_POLICY) -> tuple[float, float, bool]:
    """vCPU and GB that keep P95/P99 under the policy targets (with hysteresis)."""
    need_vcpus = _required(current.vcpus, cpu, policy["cpu_target_p95"], policy["cpu_target_p99"])
    need_gb = _required(current.memory_gb, memory, policy["memory_target_p95"], policy["memory_target_p99"])
    memory_from_telemetry = need_gb is not None
    # Without memory telemetry the memory size is preserved, never guessed.
    need_gb = current.memory_gb if need_gb is None else need_gb
    min_change = policy.get("min_change_ratio", 0)
    if need_vcpus < current.vcpus and (current.vcpus - need_vcpus) / current.vcpus < min_change:
        need_vcpus = current.vcpus
    if need_gb < current.memory_gb and (current.memory_gb - need_gb) / current.memory_gb < min_change:
        need_gb = current.memory_gb
    return need_vcpus, need_gb, memory_from_telemetry


def _current_option(current: CapacitySnapshot) -> SizeOption:
    return SizeOption(sku=current.sku, vcpus=current.vcpus, memory_gb=current.memory_gb,
                      attributes=dict(current.attributes))


def analyze_resource(
    *, resource_id: str, capacity: CapacitySnapshot | None, cpu: Mapping | None, memory: Mapping | None,
    catalog: SizingCatalog | None, price_book: PriceBook | None, policy: Mapping = DEFAULT_POLICY,
) -> dict:
    hash_inputs = {
        "engine": ENGINE_VERSION, "policy": dict(policy), "resource_id": resource_id,
        "capacity": {k: v for k, v in (capacity.as_dict() if capacity else {}).items() if k != "observed_at_utc"},
        "cpu": {k: v for k, v in (cpu or {}).items() if k != "daily"},
        "memory": {k: v for k, v in (memory or {}).items() if k != "daily"},
        "prices": dict(price_book.basis) if price_book else {},
    }
    result = {
        "resource_id": resource_id,
        "provider": capacity.provider if capacity else None,
        "resource_name": capacity.resource_name if capacity else resource_id,
        "engine_version": ENGINE_VERSION,
        "policy_version": policy["version"],
        "inputs_hash": hashlib.sha256(canonical_json(hash_inputs).encode()).hexdigest(),
        "from_config": None,
        "to_config": None,
        "action_type": KEEP,
        "monthly_saving": None,
        "current_monthly_cost": None,
        "target_monthly_cost": None,
        "currency": price_book.currency if price_book else None,
        "confidence": "LOW",
        "risks": [],
        "rationale": [],
        "evidence": {
            "cpu": cpu, "memory": memory, "capacity": capacity.as_dict() if capacity else None,
            "prices": dict(price_book.basis, currency=price_book.currency) if price_book else None,
            "policy": dict(policy),
        },
    }
    if capacity is None or capacity.vcpus <= 0 or capacity.memory_gb <= 0 or catalog is None or not catalog.supports(capacity):
        result["readiness_status"] = MISSING_CAPACITY
        result["rationale"].append("Capacidade atual (vCPU, memória e família de tamanhos) não comprovada.")
        return result
    current = _current_option(capacity)
    result["from_config"] = current.as_dict()
    if not cpu or cpu.get("p95") is None:
        result["readiness_status"] = INCOMPLETE
        result["rationale"].append("Sem série de CPU na janela analisada.")
        return result
    days = min(stats["days_observed"] for stats in (cpu, memory) if stats)
    coverage = min(stats["coverage"] for stats in (cpu, memory) if stats)
    need_vcpus, need_gb, memory_from_telemetry = required_capacity(capacity, cpu, memory, policy)
    target = catalog.fit(capacity, need_vcpus, need_gb)
    result["to_config"] = dict(target.as_dict(), memory_sized_from_telemetry=memory_from_telemetry)
    if target.vcpus == current.vcpus and target.memory_gb == current.memory_gb:
        result["action_type"] = KEEP
    elif target.vcpus <= current.vcpus and target.memory_gb <= current.memory_gb:
        result["action_type"] = RIGHTSIZE_DOWN
    else:
        result["action_type"] = RIGHTSIZE_UP
    result["rationale"].append(
        f"CPU P95 {cpu['p95']:.1f}% / P99 {cpu['p99']:.1f}% de {current.vcpus:g} vCPU; "
        f"alvo mantém P95 ≤ {policy['cpu_target_p95']:.0%} e P99 ≤ {policy['cpu_target_p99']:.0%}."
    )
    if memory_from_telemetry:
        result["rationale"].append(
            f"Memória P95 {memory['p95']:.1f}% / P99 {memory['p99']:.1f}% de {current.memory_gb:g} GB; "
            f"alvo mantém P95 ≤ {policy['memory_target_p95']:.0%} e P99 ≤ {policy['memory_target_p99']:.0%}."
        )
    else:
        result["risks"].append("Memória sem telemetria: tamanho de memória preservado.")
    projected = {
        "cpu_p95": cpu["p95"] * current.vcpus / target.vcpus,
        "cpu_p99": cpu["p99"] * current.vcpus / target.vcpus,
        "cpu_peak": (cpu["max"] or 0) * current.vcpus / target.vcpus,
    }
    if memory_from_telemetry:
        projected["memory_p95"] = memory["p95"] * current.memory_gb / target.memory_gb
        projected["memory_p99"] = memory["p99"] * current.memory_gb / target.memory_gb
    result["evidence"]["projected_utilization"] = projected
    if projected["cpu_peak"] > 100:
        result["risks"].append(
            f"Pico observado ({cpu['max']:.1f}%) equivaleria a {projected['cpu_peak']:.0f}% no alvo: haverá fila de CPU nesses momentos."
        )
    current_hourly = price_book.hourly(current) if price_book else None
    target_hourly = price_book.hourly(target) if price_book else None
    if days < policy["min_days"] or coverage < policy["min_coverage"]:
        result["readiness_status"] = INCOMPLETE
        result["rationale"].append(f"Telemetria insuficiente: {days} dia(s), cobertura {coverage:.0%}.")
    elif current_hourly is None or target_hourly is None:
        result["readiness_status"] = MISSING_PRICE
        result["rationale"].append("Sem preço comparável para o tamanho atual e o recomendado.")
    else:
        result["readiness_status"] = READY
    if current_hourly is not None and target_hourly is not None:
        result["current_monthly_cost"] = round(current_hourly * HOURS_PER_MONTH, 2)
        result["target_monthly_cost"] = round(target_hourly * HOURS_PER_MONTH, 2)
        result["monthly_saving"] = round(result["current_monthly_cost"] - result["target_monthly_cost"], 2)
    if result["readiness_status"] == READY:
        if coverage >= policy["high_confidence_coverage"] and projected["cpu_peak"] <= 100:
            result["confidence"] = "HIGH"
        else:
            result["confidence"] = "MEDIUM"
    return result


def window_bounds(as_of: datetime, days: int) -> tuple[datetime, datetime]:
    end = as_of.replace(hour=0, minute=0, second=0, microsecond=0)
    return end - timedelta(days=days), end
