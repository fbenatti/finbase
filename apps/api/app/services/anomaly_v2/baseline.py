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
MIN_WEEKDAY_SAMPLES = 4


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
    # One new weekend charge is not evidence of a weekly pattern. In sparse
    # histories, default to a neutral factor until that weekday repeats.
    medians = {wd: statistics.median(vals) for wd, vals in by_wd.items()
               if len(vals) >= MIN_WEEKDAY_SAMPLES}
    if not medians:
        return {i: 1.0 for i in range(7)}
    overall = statistics.median(list(medians.values()))
    if overall <= 0:
        return {i: 1.0 for i in range(7)}
    return {wd: max(0.1, medians[wd] / overall) if wd in medians else 1.0 for wd in range(7)}


def baseline_reference(daily: Sequence[Mapping[str, Any]], *, reference_day: date) -> dict[str, Any]:
    factors = weekday_factors(daily, as_of=reference_day - timedelta(days=1))
    expected, _lower, _upper, mad = expected_for_day(daily, target=reference_day)
    return {"reference_day": reference_day.isoformat(),
            "level": expected / factors.get(reference_day.weekday(), 1.0),
            "weekday_factors": {str(key): value for key, value in factors.items()},
            "band": MAD_SCALE * mad * DEFAULT_Z, "mad": mad,
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
    z: float = DEFAULT_Z,
    reference_day: date | None = None,
) -> tuple[float, float, float, float]:
    """Retorna expected, lower, upper, mad."""
    reference_day = min(reference_day or target, target)
    level_start = reference_day - timedelta(days=LEVEL_DAYS)
    mad_start = reference_day - timedelta(days=MAD_DAYS)
    level_vals: list[float] = []
    mad_residuals: list[float] = []
    # A day's own charge (and future data) cannot train its reference.
    factors = weekday_factors(daily, as_of=reference_day - timedelta(days=1))
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
        while trailing and trailing[0][0] < day - timedelta(days=LEVEL_DAYS):
            trailing.popleft()
        if mad_start <= day and trailing:
            prior_expected = statistics.median([value for _, value in trailing]) * f
            mad_residuals.append(cost - prior_expected)
        trailing.append((day, normalized))

    level = statistics.median(level_vals) if level_vals else 0.0
    expected = level * wd_factor
    mad = _mad(mad_residuals) if mad_residuals else max(abs(expected) * 0.1, 1.0)
    band = MAD_SCALE * mad * z
    lower = max(0.0, expected - band)
    upper = expected + band
    return expected, lower, upper, mad
