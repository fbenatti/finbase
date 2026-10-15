"""Baseline robusto: nível 14d + MAD 56d + fator semanal."""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

LEVEL_DAYS = 14
MAD_DAYS = 56
MAD_SCALE = 1.4826
DEFAULT_Z = 3.0


def _mad(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    med = statistics.median(values)
    return statistics.median([abs(v - med) for v in values])


def weekday_factors(daily: Sequence[Mapping[str, Any]], *, as_of: date) -> dict[int, float]:
    start = as_of - timedelta(days=MAD_DAYS - 1)
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
    medians = {wd: statistics.median(vals) for wd, vals in by_wd.items() if vals}
    if not medians:
        return {i: 1.0 for i in range(7)}
    overall = statistics.median(list(medians.values()))
    if overall <= 0:
        return {i: 1.0 for i in range(7)}
    return {wd: max(0.1, medians.get(wd, overall) / overall) for wd in range(7)}


def expected_for_day(
    daily: Sequence[Mapping[str, Any]],
    *,
    target: date,
    z: float = DEFAULT_Z,
) -> tuple[float, float, float, float]:
    """Retorna expected, lower, upper, mad."""
    level_start = target - timedelta(days=LEVEL_DAYS - 1)
    mad_start = target - timedelta(days=MAD_DAYS - 1)
    level_vals: list[float] = []
    mad_residuals: list[float] = []
    factors = weekday_factors(daily, as_of=target)
    wd_factor = factors.get(target.weekday(), 1.0)

    by_day: dict[str, float] = {}
    for row in daily:
        day_s = str(row.get("day") or "")[:10]
        by_day[day_s] = by_day.get(day_s, 0.0) + float(row.get("cost_cloud") or 0)

    for day_s, cost in by_day.items():
        try:
            day = date.fromisoformat(day_s)
        except ValueError:
            continue
        if level_start <= day < target:
            level_vals.append(cost)
        if mad_start <= day < target:
            f = factors.get(day.weekday(), 1.0)
            expected = statistics.median(level_vals) * f if level_vals else cost
            if expected > 0:
                mad_residuals.append(cost - expected)

    level = statistics.median(level_vals) if level_vals else 0.0
    expected = level * wd_factor
    mad = _mad(mad_residuals) if mad_residuals else max(abs(expected) * 0.1, 1.0)
    band = MAD_SCALE * mad * z
    lower = max(0.0, expected - band)
    upper = expected + band
    return expected, lower, upper, mad
