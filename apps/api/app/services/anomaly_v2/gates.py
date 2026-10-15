"""Portões anti falso positivo e qualidade de carga."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from app.services.anomaly_v2.detectors import TIER_ALERT, TIER_WATCH


def cloud_load_quality(
    daily_rows: Sequence[Mapping[str, Any]],
    *,
    day: str,
    baseline_days: Sequence[str],
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
        hist = [by_cloud_day.get((cloud, d), 0.0) for d in baseline_days if d != day]
        if not hist:
            ratios[cloud] = 1.0
            continue
        hist_sorted = sorted(hist)
        median = hist_sorted[len(hist_sorted) // 2]
        ratios[cloud] = today / median if median > 0 else (1.0 if today == 0 else 0.0)
    return ratios


def apply_gates(
    signal: Mapping[str, Any],
    *,
    materiality_daily: float,
    materiality_monthly: float,
    min_pct: float,
    cloud_ratio: float,
    persistence_days: int = 1,
    confidence: str = "MEDIUM",
) -> Mapping[str, Any]:
    """Ajusta tier após portões."""
    out = dict(signal)
    reasons: list[str] = list(out.get("reason_codes") or [])
    if cloud_ratio < 0.70:
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
    if persistence_days < 2 and delta < materiality_daily * 5:
        if out.get("tier") == TIER_ALERT and out.get("detector") == "SPIKE":
            out["tier"] = TIER_WATCH
            reasons.append("NEEDS_PERSISTENCE")
    out["reason_codes"] = reasons
    return out
