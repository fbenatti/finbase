"""Harness de backtest / simulação."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Callable, Mapping, Sequence


def run_backtest(
    *,
    days: Sequence[date],
    evaluate_day: Callable[[date], Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    alerts_by_day: dict[str, int] = defaultdict(int)
    all_alerts: list[Mapping[str, Any]] = []
    for day in days:
        signals = list(evaluate_day(day))
        alerts = [s for s in signals if s.get("tier") == "ALERT"]
        alerts_by_day[day.isoformat()] = len(alerts)
        all_alerts.extend(alerts)
    return {
        "days": len(days),
        "total_alerts": len(all_alerts),
        "alerts_per_day_avg": len(all_alerts) / max(1, len(days)),
        "alerts_by_day": dict(alerts_by_day),
        "alerts": all_alerts[:500],
    }


def backtest_date_range(end: date, *, history_days: int = 60) -> list[date]:
    start = end - timedelta(days=history_days - 1)
    out: list[date] = []
    cursor = start
    while cursor <= end:
        out.append(cursor)
        cursor += timedelta(days=1)
    return out
