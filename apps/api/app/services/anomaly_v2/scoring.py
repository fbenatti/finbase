"""Nota e severidade orientadas a impacto projetado."""
from __future__ import annotations

import math
from typing import Any, Mapping

from app.services.anomaly_v2.settings import detector_settings

SEVERITY_LOW = "LOW"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_HIGH = "HIGH"
SEVERITY_CRITICAL = "CRITICAL"

def projected_month_impact(delta_daily: float, active_days: int = 1, *, days_per_month: float = 30.4) -> float:
    run_rate = delta_daily
    return run_rate * days_per_month


def score_signal(
    signal: Mapping[str, Any],
    *,
    materiality: float,
    severity_tiers: Mapping[str, float] | None = None,
    confidence: str = "MEDIUM",
    settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    config = detector_settings(settings)
    delta = max(0.0, float(signal.get("delta") or 0))
    persistence_days = int(signal.get("persistence_days") or 1)
    detector = str(signal.get("detector") or "")
    if detector == "MONTHLY_ACCUMULATED":
        projected = delta
    elif detector in {"COMMITMENT", "NEW_SPEND"} or (
        detector == "SPIKE" and persistence_days <= int(config["one_off_spike_max_days"])
    ):
        # A one-off purchase/spike does not repeat every day of the month.
        projected = 0.0
    else:
        projected = projected_month_impact(delta, persistence_days, days_per_month=float(config["projection_days_month"]))
    prioritization_impact = max(delta, projected)
    saturation = float(config["score_impact_saturation"])
    surprise = min(saturation, float(signal.get("z_robust") or 0) * float(config["score_surprise_points_per_z"]))
    impact_norm = min(saturation, float(config["score_impact_log_scale"]) * math.log2(1.0 + prioritization_impact / max(materiality, 1.0)))
    persistence = min(saturation, float(signal.get("persistence_days") or 1) / int(config["score_persistence_saturation_days"]) * saturation)
    conf_factor = {
        "HIGH": float(config["score_confidence_high_factor"]),
        "MEDIUM": float(config["score_confidence_medium_factor"]),
        "LOW": float(config["score_confidence_low_factor"]),
    }.get(confidence, float(config["score_confidence_unknown_factor"]))
    weights = [float(config["score_impact_weight_pct"]), float(config["score_surprise_weight_pct"]), float(config["score_persistence_weight_pct"])]
    score = round((weights[0] * impact_norm + weights[1] * surprise + weights[2] * persistence) / sum(weights) * conf_factor)
    tiers = {
        "MEDIUM": float(config["severity_medium_min"]),
        "HIGH": float(config["severity_high_min"]),
        "CRITICAL": float(config["severity_critical_min"]),
    }
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
