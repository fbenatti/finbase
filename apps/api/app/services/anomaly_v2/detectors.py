"""Detectores v2 por natureza de custo."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

from app.services.anomaly_v2.baseline import expected_for_day
from app.services.anomaly_v2.return_engine import DEVIATION_ACTIVE, DEVIATION_AUTO_FIXED
from app.services.anomaly_v2.classifier import (
    NATURE_COMMITMENT,
    NATURE_CONTINUOUS,
    NATURE_INTERMITTENT,
    NATURE_NEW,
)

TIER_NORMAL = "NORMAL"
TIER_WATCH = "WATCH"
TIER_ALERT = "ALERT"

DET_SPIKE = "SPIKE"
DET_NEW_SPEND = "NEW_SPEND"
DET_MONTHLY_ACC = "MONTHLY_ACCUMULATED"
DET_COMMITMENT = "COMMITMENT"


def _pct_delta(observed: float, expected: float) -> float:
    if expected <= 0:
        return 1.0 if observed > 0 else 0.0
    return (observed - expected) / expected


def detect_commitment_day(
    *,
    scope_id: str,
    day: date,
    observed: float,
    ledger_entries: Sequence[Mapping[str, Any]],
    date_tolerance: int = 3,
    value_tolerance_pct: float = 0.15,
) -> dict[str, Any] | None:
    """Cobrança de compromisso no calendário vs fora."""
    dom = day.day
    for entry in ledger_entries:
        if str(entry.get("scope_id")) != scope_id:
            continue
        anniversaries = entry.get("anniversary_days") or (
            [int(entry.get("anniversary_day") or 0)] if entry.get("anniversary_day") else []
        )
        expected_amount = float(entry.get("expected_amount") or 0)
        tol = max(expected_amount * value_tolerance_pct, float(entry.get("amount_mad") or 0) * 3, 1.0)
        on_calendar = any(abs(dom - int(a)) <= date_tolerance for a in anniversaries if a)
        if on_calendar:
            if abs(observed - expected_amount) <= tol:
                return {
                    "detector": DET_COMMITMENT,
                    "tier": TIER_NORMAL,
                    "subtype": "ON_CALENDAR",
                    "expected": expected_amount,
                    "observed": observed,
                }
            return {
                "detector": DET_COMMITMENT,
                "tier": TIER_ALERT,
                "subtype": "VALUE_MISMATCH",
                "expected": expected_amount,
                "observed": observed,
                "delta": observed - expected_amount,
            }
    return None


def run_detectors_for_scope(
    *,
    scope_id: str,
    daily: Sequence[Mapping[str, Any]],
    profile: Mapping[str, Any],
    as_of: date,
    materiality: float,
    min_pct: float,
    investigate_score: int,
    ledger_entries: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    nature = str(profile.get("cost_nature") or NATURE_CONTINUOUS)
    by_day = {str(r["day"])[:10]: float(r.get("cost_cloud") or 0) for r in daily if r.get("scope_id") == scope_id}
    signals: list[dict[str, Any]] = []

    if nature == NATURE_COMMITMENT:
        obs = by_day.get(as_of.isoformat(), 0.0)
        if obs > 0:
            hit = detect_commitment_day(
                scope_id=scope_id, day=as_of, observed=obs, ledger_entries=ledger_entries
            )
            if hit and hit.get("tier") == TIER_ALERT:
                expected = float(hit.get("expected") or 0)
                upper = expected * 1.15
                delta = obs - expected
                if delta >= materiality and obs > upper:
                    hit.update({
                        "scope_id": scope_id,
                        "day": as_of.isoformat(),
                        "upper": upper,
                        "lower": expected * 0.85,
                        "delta": delta,
                        "pct_delta": _pct_delta(obs, expected),
                        "deviation_status": DEVIATION_ACTIVE,
                    })
                    signals.append(hit)
                    return signals
        # Sem calendário de reserva, o mesmo dia segue a faixa do próprio histórico.

    if nature == NATURE_NEW:
        obs = by_day.get(as_of.isoformat(), 0.0)
        if obs >= materiality:
            signals.append(
                {
                    "scope_id": scope_id,
                    "day": as_of.isoformat(),
                    "detector": DET_NEW_SPEND,
                    "tier": TIER_WATCH if obs < materiality * 3 else TIER_ALERT,
                    "expected": 0.0,
                    "observed": obs,
                    "delta": obs,
                    "pct_delta": 1.0,
                }
            )
        return signals

    if nature == NATURE_INTERMITTENT:
        month_start = as_of.replace(day=1)
        acc = sum(
            v for d, v in by_day.items() if month_start.isoformat() <= d <= as_of.isoformat()
        )
        prev_months: list[float] = []
        for m in range(1, 4):
            ref = (month_start - timedelta(days=1)).replace(day=1)
            for _ in range(m - 1):
                ref = (ref - timedelta(days=1)).replace(day=1)
            end_m = (ref.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            same_day = min(as_of.day, end_m.day)
            partial_end = end_m.replace(day=same_day)
            partial = sum(
                v
                for d, v in by_day.items()
                if ref.isoformat() <= d <= partial_end.isoformat()
            )
            # An uncollected month is not a zero-cost reference month.
            if any(ref.isoformat() <= d <= partial_end.isoformat() for d in by_day):
                prev_months.append(partial)
        expected_acc = sum(prev_months) / len(prev_months) if prev_months else 0.0
        delta = acc - expected_acc
        if expected_acc > 0 and delta >= materiality and _pct_delta(acc, expected_acc) >= min_pct:
            tier = TIER_ALERT if delta >= materiality * 2 else TIER_WATCH
            signals.append(
                {
                    "scope_id": scope_id,
                    "day": as_of.isoformat(),
                    "detector": DET_MONTHLY_ACC,
                    "tier": tier,
                    "expected": expected_acc,
                    "observed": acc,
                    "delta": delta,
                    "pct_delta": _pct_delta(acc, expected_acc),
                }
            )
        return signals

    scope_daily = [row for row in daily if str(row.get("scope_id")) == scope_id]
    episode = continuous_episode(
        scope_daily,
        as_of=as_of,
        materiality=materiality,
        min_pct=min_pct,
    )
    if episode is None:
        return signals
    signals.append(episode)
    return signals


def continuous_episode(
    scope_daily: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
    materiality: float,
    min_pct: float,
) -> dict[str, Any] | None:
    """Um episódio por escopo: aberto se ainda desvia, fechado se voltou sozinho.

    A faixa usa só o histórico deste escopo. Um pico que já voltou ao normal
    não permanece como caso crítico em aberto.
    """
    by_day: dict[str, float] = {}
    scope_id = ""
    for row in scope_daily:
        scope_id = scope_id or str(row.get("scope_id") or "")
        day_s = str(row.get("day") or "")[:10]
        by_day[day_s] = by_day.get(day_s, 0.0) + float(row.get("cost_cloud") or 0)

    window_start = as_of - timedelta(days=45)
    active: dict[str, Any] | None = None
    quiet = 0
    active_days = 0
    latest_closed: dict[str, Any] | None = None

    cursor = window_start
    while cursor <= as_of:
        if cursor.isoformat() not in by_day:
            quiet = 0
            cursor += timedelta(days=1)
            continue
        obs = by_day[cursor.isoformat()]
        expected, lower, upper, mad = expected_for_day(scope_daily, target=cursor)
        pct = _pct_delta(obs, expected)
        hot = obs > upper and obs >= materiality and pct >= min_pct
        if hot:
            quiet = 0
            active_days = active_days + 1 if active is not None else 1
            delta = obs - expected
            z = (obs - upper) / max(mad * 1.4826, 1e-6) if mad else 0.0
            peak = {
                "scope_id": scope_id,
                "day": cursor.isoformat(),
                "detector": DET_SPIKE,
                "tier": TIER_ALERT,
                "expected": expected,
                "lower": lower,
                "upper": upper,
                "observed": obs,
                "delta": delta,
                "pct_delta": pct,
                "z_robust": z,
                "episode_start": cursor.isoformat(),
                "deviation_status": DEVIATION_ACTIVE,
                "persistence_days": active_days,
            }
            if active is None or obs >= float(active.get("observed") or 0):
                if active is not None:
                    peak["episode_start"] = active["episode_start"]
                active = peak
            active["persistence_days"] = active_days
        elif active is not None:
            quiet += 1
            if quiet >= 3:
                active["deviation_status"] = DEVIATION_AUTO_FIXED
                active["episode_end"] = (cursor - timedelta(days=2)).isoformat()
                latest_closed = active
                active = None
                quiet = 0
        cursor += timedelta(days=1)

    if active is not None:
        return active
    if latest_closed is not None and str(latest_closed.get("episode_end") or "") >= window_start.isoformat():
        return latest_closed
    return None
