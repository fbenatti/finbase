"""Baseline robusto: nível 14d + MAD 56d + fator semanal."""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

from app.services.anomaly_v2.settings import detector_settings

LEVEL_DAYS = 14
MAD_DAYS = 56
MAD_SCALE = 1.4826
DEFAULT_Z = 3.0
MIN_WEEKDAY_SAMPLES = 4


def _mad(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    med = statistics.median(values)
    return statistics.median([abs(v - med) for v in values])


def weekday_factors(daily: Sequence[Mapping[str, Any]], *, as_of: date,
                    settings: Mapping[str, Any] | None = None) -> dict[int, float]:
    config = detector_settings(settings)
    start = as_of - timedelta(days=int(config["baseline_variability_days"]) - 1)
    by_wd: dict[int, list[float]] = defaultdict(list)
    for row in daily:
        day_s = str(row.get("day") or "")[:10]
        try:
            day = date.fromisoformat(day_s)
        except ValueError:
            continue
        if day < start or day > as_of:
            continue
        cost = float(row.get("cost_cloud") or 0)
        by_wd[day.weekday()].append(cost)
    # One new weekend charge is not evidence of a weekly pattern. In sparse
    # histories, default to a neutral factor until that weekday repeats.
    medians = {wd: statistics.median(vals) for wd, vals in by_wd.items()
               if len(vals) >= int(config["minimum_weekday_samples"])}
    if not medians:
        return {i: 1.0 for i in range(7)}
    overall = statistics.median(list(medians.values()))
    if overall <= 0:
        return {i: 1.0 for i in range(7)}
    return {wd: max(0.1, medians[wd] / overall) if wd in medians else 1.0 for wd in range(7)}


def baseline_reference(daily: Sequence[Mapping[str, Any]], *, reference_day: date,
                       settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    config = detector_settings(settings)
    factors = weekday_factors(daily, as_of=reference_day - timedelta(days=1), settings=config)
    expected, _lower, _upper, mad = expected_for_day(daily, target=reference_day, settings=config)
    return {"reference_day": reference_day.isoformat(),
            "level": expected / factors.get(reference_day.weekday(), 1.0),
            "weekday_factors": {str(key): value for key, value in factors.items()},
            "band": float(config["robust_mad_scale"]) * mad * float(config["robust_z_threshold"]), "mad": mad,
            "settings": config,
            "method": "PRE_EPISODE_BASELINE"}


def expected_from_reference(reference: Mapping[str, Any], *, target: date) -> tuple[float, float, float, float]:
    factors = reference.get("weekday_factors") or {}
    factor = float(factors.get(str(target.weekday()), 1.0))
    expected = float(reference.get("level") or 0) * factor
    band = float(reference.get("band") or 0)
    lower_offset = float(reference.get("lower_offset", band))
    return expected, max(0.0, expected - lower_offset), expected + band, float(reference.get("mad") or 0)


def expected_for_day(
    daily: Sequence[Mapping[str, Any]],
    *,
    target: date,
    z: float | None = None,
    reference_day: date | None = None,
    settings: Mapping[str, Any] | None = None,
) -> tuple[float, float, float, float]:
    """Retorna expected, lower, upper, mad."""
    config = detector_settings(settings)
    z_value = float(config["robust_z_threshold"]) if z is None else float(z)
    reference_day = min(reference_day or target, target)
    level_days = int(config["baseline_level_days"])
    mad_days = int(config["baseline_variability_days"])
    level_start = reference_day - timedelta(days=level_days)
    mad_start = reference_day - timedelta(days=mad_days)
    level_vals: list[float] = []
    mad_residuals: list[float] = []
    # A day's own charge (and future data) cannot train its reference.
    factors = weekday_factors(daily, as_of=reference_day - timedelta(days=1), settings=config)
    wd_factor = factors.get(target.weekday(), 1.0)

    by_day: dict[str, float] = {}
    for row in daily:
        day_s = str(row.get("day") or "")[:10]
        by_day[day_s] = by_day.get(day_s, 0.0) + float(row.get("cost_cloud") or 0)

    from collections import deque

    trailing = deque()
    for day_s, cost in sorted(by_day.items()):
        try:
            day = date.fromisoformat(day_s)
        except ValueError:
            continue
        if day >= reference_day:
            continue
        f = factors.get(day.weekday(), 1.0)
        normalized = cost / f
        if level_start <= day:
            level_vals.append(normalized)
        while trailing and trailing[0][0] < day - timedelta(days=level_days):
            trailing.popleft()
        if mad_start <= day and trailing:
            prior_expected = statistics.median([value for _, value in trailing]) * f
            mad_residuals.append(cost - prior_expected)
        trailing.append((day, normalized))

    level = statistics.median(level_vals) if level_vals else 0.0
    expected = level * wd_factor
    mad = _mad(mad_residuals) if mad_residuals else max(abs(expected) * 0.1, 1.0)
    band = float(config["robust_mad_scale"]) * mad * z_value
    lower = max(0.0, expected - band)
    upper = expected + band
    return expected, lower, upper, mad
