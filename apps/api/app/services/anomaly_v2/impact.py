"""Cálculo de impacto financeiro do desvio."""
from __future__ import annotations

import statistics
from datetime import date
from typing import Any, Mapping, Sequence

from app.services.anomaly_v2.settings import detector_settings

from app.services.anomaly_v2.return_engine import (
    DEVIATION_ACCEPTED,
    DEVIATION_ACTIVE,
    DEVIATION_AUTO_FIXED,
    DEVIATION_DISMISSED,
    DEVIATION_FIXED_ACTION,
    DEVIATION_LEGITIMATE,
    DEVIATION_RELAPSE,
)

IMPACT_COST = "COST"
IMPACT_SAVING = "SAVING"
IMPACT_NEUTRAL = "NEUTRAL"

def daily_excess(observed: float, expected: float) -> float:
    return max(0.0, observed - expected)


def impact_class_for_status(status: str) -> str:
    if status in (DEVIATION_LEGITIMATE, DEVIATION_DISMISSED):
        return IMPACT_NEUTRAL
    if status in (DEVIATION_AUTO_FIXED, DEVIATION_FIXED_ACTION):
        return IMPACT_SAVING
    if status in (DEVIATION_ACTIVE, DEVIATION_ACCEPTED, DEVIATION_RELAPSE):
        return IMPACT_COST
    return IMPACT_NEUTRAL


def compute_impact(
    *,
    daily_points: Sequence[Mapping[str, Any]],
    episode_start: date,
    episode_end: date | None,
    detector: str,
    deviation_status: str,
    treated: bool,
    settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    config = detector_settings(settings)
    excesses = [float(p.get("excess") or 0) for p in daily_points]
    incurred = round(sum(excesses), 2)
    if detector == "SPIKE" and len(excesses) <= int(config["one_off_spike_max_days"]):
        run_rate = 0.0
    else:
        tail = excesses[-min(int(config["impact_run_rate_lookback_days"]), len(excesses)) :] if excesses else [0.0]
        run_rate = statistics.median(tail) if tail else 0.0

    projected_monthly = round(run_rate * float(config["projection_days_month"]), 2)
    projected_annual = round(run_rate * float(config["projection_days_year"]), 2)
    iclass = impact_class_for_status(deviation_status)
    if iclass == IMPACT_NEUTRAL:
        projected_monthly = projected_annual = incurred = 0.0

    open_exposure_monthly = projected_monthly if deviation_status in (
        DEVIATION_ACTIVE,
        DEVIATION_ACCEPTED,
        DEVIATION_RELAPSE,
    ) else 0.0
    open_exposure_annual = projected_annual if open_exposure_monthly else 0.0

    return {
        "incurred_excess_cost": incurred,
        "run_rate_excess_daily": round(run_rate, 2),
        "projected_saving_monthly": projected_monthly
        if deviation_status in (DEVIATION_AUTO_FIXED, DEVIATION_FIXED_ACTION)
        else 0.0,
        "projected_saving_annual": projected_annual
        if deviation_status in (DEVIATION_AUTO_FIXED, DEVIATION_FIXED_ACTION)
        else 0.0,
        "open_exposure_monthly": open_exposure_monthly,
        "open_exposure_annual": open_exposure_annual,
        "impact_class": iclass,
        "treated": treated,
        "deviation_days": len(excesses),
    }
