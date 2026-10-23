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
    "version": "rightsizing_policy_v2",
    "cpu_target_p95": 0.70,
    "cpu_target_p99": 0.90,
    "memory_target_p95": 0.80,
    "memory_target_p99": 0.90,
    # Peaks size the resource too: the P99.5 of the per-bucket native maxima
    # must fit the new capacity (at most 0.5% of the time with a peak above
    # it). Memory peaks must always fit with headroom: running out of memory
    # kills processes instead of queueing them.
    "cpu_peak_quantile": 99.5,
    "cpu_peak_limit": 1.00,
    "memory_peak_limit": 0.95,
    "min_days": 7,
    "min_coverage": 0.90,
    "high_confidence_coverage": 0.98,
    # Reductions smaller than this keep the current size (avoid churn by noise).
    "min_change_ratio": 0.15,
    # Recurring peaks (see peak_patterns): a bucket peaks when its max reaches
    # the threshold (lowered to multiplier × P90 of the maxima, never below the
    # floor); an hour recurs when it peaks on min_day_ratio of the days (or of
    # the same weekdays, min_weeks times at least) starting within jitter_minutes of the
    # usual time; peaks above max_time_share of the time are continuous load.
    "peak_pattern_threshold": 80.0,
    "peak_pattern_floor": 40.0,
    "peak_pattern_baseline_multiplier": 2.0,
    "peak_pattern_min_day_ratio": 0.70,
    "peak_pattern_min_weeks": 4,
    "peak_pattern_jitter_minutes": 15,
    # Chance, over all slots tested, of flagging peaks that have no schedule.
    "peak_pattern_max_false_alarm": 0.001,
    "peak_pattern_max_time_share": 0.10,
    "peak_pattern_timezone": "America/Sao_Paulo",
}


PEAK_STAT = {99.0: "max_p99", 99.5: "max_p995", 99.9: "max_p999", 100.0: "max"}


def cpu_peak(cpu: Mapping, policy: Mapping = DEFAULT_POLICY) -> float | None:
    """Quantile of the per-bucket CPU peaks the policy sizes for (``cpu_peak_quantile``)."""
    return cpu.get(PEAK_STAT.get(float(policy.get("cpu_peak_quantile", 99.5)), "max_p995"))


def _pt(value: float) -> str:
    return f"{value:g}".replace(".", ",")


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
        # Distribution of the per-bucket native maxima (provider ``max``).
        "max_p99": _linear_quantile(maxima, 99),
        "max_p995": _linear_quantile(maxima, 99.5),
        "max_p999": _linear_quantile(maxima, 99.9),
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


def required_capacity(current: CapacitySnapshot, cpu: Mapping, memory: Mapping | None,
                      policy: Mapping = DEFAULT_POLICY) -> dict:
    """vCPU and GB the demand needs, and which criterion set each of them.

    CPU: the largest of P95/target, P99/target and the P99.5 of the bucket
    peaks; memory: P95/target, P99/target and the observed peak with headroom.
    Reductions below ``min_change_ratio`` keep the current size.
    """
    candidates_cpu = {
        "p95": current.vcpus * cpu["p95"] / 100 / policy["cpu_target_p95"],
        "p99": current.vcpus * cpu["p99"] / 100 / policy["cpu_target_p99"],
    }
    peak_cpu = cpu_peak(cpu, policy)
    if peak_cpu is not None:
        candidates_cpu["peak"] = current.vcpus * peak_cpu / 100 / policy["cpu_peak_limit"]
    cpu_basis = max(candidates_cpu, key=candidates_cpu.get)
    need_vcpus = candidates_cpu[cpu_basis]

    memory_from_telemetry = bool(memory and memory.get("p95") is not None and memory.get("p99") is not None)
    if memory_from_telemetry:
        candidates_memory = {
            "p95": current.memory_gb * memory["p95"] / 100 / policy["memory_target_p95"],
            "p99": current.memory_gb * memory["p99"] / 100 / policy["memory_target_p99"],
        }
        if memory.get("max") is not None:
            candidates_memory["peak"] = current.memory_gb * memory["max"] / 100 / policy["memory_peak_limit"]
        memory_basis = max(candidates_memory, key=candidates_memory.get)
        need_gb = candidates_memory[memory_basis]
    else:
        # Without memory telemetry the memory size is preserved, never guessed.
        memory_basis, need_gb = "unchanged", current.memory_gb

    # The criterion that set the need stays as the basis; the hysteresis only
    # rounds a small reduction back to the current size.
    min_change = policy.get("min_change_ratio", 0)
    cpu_kept = need_vcpus < current.vcpus and (current.vcpus - need_vcpus) / current.vcpus < min_change
    memory_kept = need_gb < current.memory_gb and (current.memory_gb - need_gb) / current.memory_gb < min_change
    return {
        "vcpus": current.vcpus if cpu_kept else need_vcpus,
        "memory_gb": current.memory_gb if memory_kept else need_gb,
        "memory_from_telemetry": memory_from_telemetry,
        "cpu_basis": cpu_basis, "memory_basis": memory_basis,
        "cpu_kept_by_min_change": cpu_kept, "memory_kept_by_min_change": memory_kept,
    }


BASIS_LABEL = {
    "p95": "pelo P95 das médias", "p99": "pelo P99 das médias", "peak": "pelos picos",
    "unchanged": "pela falta de telemetria (tamanho preservado)",
}


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
    need = required_capacity(capacity, cpu, memory, policy)
    memory_from_telemetry = need["memory_from_telemetry"]
    target = catalog.fit(capacity, need["vcpus"], need["memory_gb"])
    result["to_config"] = dict(target.as_dict(), memory_sized_from_telemetry=memory_from_telemetry)
    result["evidence"]["sizing_basis"] = {
        "cpu": need["cpu_basis"], "memory": need["memory_basis"],
        "required_vcpus": need["vcpus"], "required_memory_gb": need["memory_gb"],
        "cpu_kept_by_min_change": need["cpu_kept_by_min_change"],
        "memory_kept_by_min_change": need["memory_kept_by_min_change"],
    }
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
    peak, quantile = cpu_peak(cpu, policy), float(policy.get("cpu_peak_quantile", 99.5))
    tail = _pt(round(100 - quantile, 1))
    if peak is not None:
        result["rationale"].append(
            f"Picos de CPU: em {_pt(quantile)}% dos intervalos o pico ficou até {peak:.1f}% "
            f"({current.vcpus * peak / 100:.1f} vCPU); o novo tamanho precisa comportá-los "
            f"(no máximo {tail}% do tempo com pico acima de {policy['cpu_peak_limit']:.0%} da capacidade)."
        )
    result["rationale"].append(
        f"O tamanho de CPU foi definido {BASIS_LABEL.get(need['cpu_basis'], need['cpu_basis'])}"
        + (f" e o de memória {BASIS_LABEL.get(need['memory_basis'], need['memory_basis'])}." if memory_from_telemetry else ".")
    )
    for resource, kept in (("CPU", need["cpu_kept_by_min_change"]), ("memória", need["memory_kept_by_min_change"])):
        if kept:
            result["rationale"].append(
                f"A redução de {resource} que os dados permitiriam é menor que "
                f"{policy.get('min_change_ratio', 0):.0%}; o tamanho atual foi mantido."
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
        "cpu_peak_p995": (peak or 0) * current.vcpus / target.vcpus,
    }
    if memory_from_telemetry:
        projected["memory_p95"] = memory["p95"] * current.memory_gb / target.memory_gb
        projected["memory_p99"] = memory["p99"] * current.memory_gb / target.memory_gb
    result["evidence"]["projected_utilization"] = projected
    if projected["cpu_peak_p995"] > 100 * policy["cpu_peak_limit"]:
        result["risks"].append(
            f"Picos frequentes: o P{_pt(quantile)} dos picos ({peak:.1f}%) equivaleria a {projected['cpu_peak_p995']:.0f}% "
            f"no novo tamanho; mais de {tail}% do tempo com fila de CPU."
        )
    elif projected["cpu_peak"] > 100:
        result["risks"].append(
            f"Pico isolado: o maior pico ({cpu['max']:.1f}%) equivaleria a {projected['cpu_peak']:.0f}% no novo tamanho, "
            f"dentro da tolerância de {tail}% do tempo."
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
