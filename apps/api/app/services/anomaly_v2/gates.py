"""Portões anti falso positivo e qualidade de carga."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
import statistics
from typing import Any, Mapping, Sequence

from app.services.anomaly_v2.detectors import TIER_ALERT, TIER_WATCH
from app.services.anomaly_v2.settings import detector_settings


def cloud_load_quality(
    daily_rows: Sequence[Mapping[str, Any]],
    *,
    day: str,
    baseline_days: Sequence[str],
    settings: Mapping[str, Any] | None = None,
) -> dict[str, float]:
    """Razão do total do dia vs mediana dos dias baseline por nuvem."""
    by_cloud_day: dict[tuple[str, str], float] = defaultdict(float)
    for row in daily_rows:
        cloud = str(row.get("cloud") or row.get("cloud_origem") or "")
        d = str(row.get("day") or "")[:10]
        by_cloud_day[(cloud, d)] += float(row.get("cost_cloud") or 0)

    ratios: dict[str, float] = {}
    clouds = {c for c, _ in by_cloud_day}
    for cloud in clouds:
        today = by_cloud_day.get((cloud, day), 0.0)
        hist = [by_cloud_day[(cloud, d)] for d in baseline_days if d < day and (cloud, d) in by_cloud_day]
        if not hist:
            ratios[cloud] = 1.0
            continue
        hist_sorted = sorted(hist)
        median = statistics.median(hist_sorted)
        ratios[cloud] = today / median if median > 0 else 1.0
    return ratios


def valid_days_by_cloud(daily_rows: Sequence[Mapping[str, Any]], *,
                        settings: Mapping[str, Any] | None = None) -> dict[str, set[str]]:
    config = detector_settings(settings)
    lookback = int(config["load_quality_lookback_days"])
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in daily_rows:
        cloud = str(row.get("cloud_origem") or row.get("cloud") or "")
        totals[cloud][str(row["day"])[:10]] += float(row.get("cost_cloud") or 0)
    valid: dict[str, set[str]] = defaultdict(set)
    for cloud, days in totals.items():
        for day, total in days.items():
            current = date.fromisoformat(day)
            history = [days[prior] for offset in range(1, lookback + 1)
                       if (prior := (current - timedelta(days=offset)).isoformat()) in days]
            median = statistics.median(history) if history else 0
            if median <= 0 or total >= median * float(config["load_completeness_threshold"]):
                valid[cloud].add(day)
    return dict(valid)


def apply_gates(
    signal: Mapping[str, Any],
    *,
    materiality_daily: float,
    materiality_monthly: float,
    min_pct: float,
    cloud_ratio: float,
    persistence_days: int = 1,
    confidence: str = "MEDIUM",
    settings: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Ajusta tier após portões."""
    config = detector_settings(settings)
    out = dict(signal)
    reasons: list[str] = list(out.get("reason_codes") or [])
    if cloud_ratio < float(config["load_completeness_threshold"]):
        out["tier"] = TIER_WATCH
        reasons.append("PARTIAL_LOAD")
    obs = float(out.get("observed") or 0)
    delta = float(out.get("delta") or max(0.0, obs - float(out.get("expected") or 0)))
    pct = float(out.get("pct_delta") or 0)
    if delta < materiality_daily and obs < materiality_monthly:
        out["tier"] = TIER_WATCH
        reasons.append("BELOW_MATERIALITY")
    if pct < min_pct and out.get("detector") != "NEW_SPEND":
        out["tier"] = TIER_WATCH
        reasons.append("BELOW_MIN_PCT")
    if confidence == "LOW" and out.get("tier") == TIER_ALERT:
        out["tier"] = TIER_WATCH
        reasons.append("LOW_CONFIDENCE")
    if persistence_days < int(config["persistence_gate_min_days"]) and delta < materiality_daily * float(config["persistence_gate_materiality_multiple"]):
        if out.get("tier") == TIER_ALERT and out.get("detector") == "SPIKE":
            out["tier"] = TIER_WATCH
            reasons.append("NEEDS_PERSISTENCE")
    out["reason_codes"] = reasons
    return out
