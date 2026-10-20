"""Nota e severidade orientadas a impacto projetado."""
from __future__ import annotations

import math
from typing import Any, Mapping

SEVERITY_LOW = "LOW"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_HIGH = "HIGH"
SEVERITY_CRITICAL = "CRITICAL"

DEFAULT_TIERS = {
    "LOW": 2000,
    "MEDIUM": 10000,
    "HIGH": 50000,
    "CRITICAL": 50000,
}


def projected_month_impact(delta_daily: float, active_days: int = 1) -> float:
    run_rate = delta_daily
    return run_rate * 30.4


def score_signal(
    signal: Mapping[str, Any],
    *,
    materiality: float,
    severity_tiers: Mapping[str, float] | None = None,
    confidence: str = "MEDIUM",
) -> dict[str, Any]:
    delta = max(0.0, float(signal.get("delta") or 0))
    persistence_days = int(signal.get("persistence_days") or 1)
    detector = str(signal.get("detector") or "")
    if detector == "MONTHLY_ACCUMULATED":
        projected = delta
    elif detector in {"COMMITMENT", "NEW_SPEND"} or (detector == "SPIKE" and persistence_days <= 2):
        # A one-off purchase/spike does not repeat every day of the month.
        projected = 0.0
    else:
        projected = projected_month_impact(delta, persistence_days)
    prioritization_impact = max(delta, projected)
    surprise = min(100.0, float(signal.get("z_robust") or 0) * 25)
    impact_norm = min(100.0, 25.0 * math.log2(1.0 + prioritization_impact / max(materiality, 1.0)))
    persistence = min(100.0, float(signal.get("persistence_days") or 1) / 3 * 100)
    conf_factor = {"HIGH": 1.0, "MEDIUM": 0.85, "LOW": 0.6}.get(confidence, 0.7)
    score = round((0.50 * impact_norm + 0.25 * surprise + 0.15 * persistence) * conf_factor)
    tiers = dict(DEFAULT_TIERS)
    if severity_tiers:
        tiers.update({k.upper(): float(v) for k, v in severity_tiers.items()})
    if prioritization_impact >= tiers["CRITICAL"]:
        severity = SEVERITY_CRITICAL
    elif prioritization_impact >= tiers["HIGH"]:
        severity = SEVERITY_HIGH
    elif prioritization_impact >= tiers["MEDIUM"]:
        severity = SEVERITY_MEDIUM
    else:
        severity = SEVERITY_LOW
    return {
        "score": float(score),
        "severity": severity,
        "projected_month_impact": round(projected, 2),
        "impact_to_date": round(delta, 2),
    }
