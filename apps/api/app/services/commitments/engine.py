"""Provider-neutral commitment sizing engine.

Works only on hourly on-demand-equivalent usage: a commitment is consumed hour
by hour and an unused hour is lost, so sizing from daily totals would turn
idle hours into false savings. Without enough hourly data the key is reported
as ``INSUFFICIENT_HOURLY_DATA`` and no commitment is recommended.

For a commitment ``c`` (on-demand cost per hour it covers), discount ``d`` and
uncovered hourly usage ``u(t)``::

    saving(c) = Σ_t [ min(u(t), c) − c·(1 − d) ]

The balanced profile maximizes it; conservative keeps utilization above the
policy target and aggressive buys more coverage while keeping most of the
saving. Every offer (term × payment) is priced and the best NPV wins per profile.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Iterable, Mapping

from .contracts import Adjustment, CommitmentKey, ExistingCommitment, Instrument, Offer, UsageLine, key_for

ENGINE_VERSION = "commitment_v1"
HOURS_PER_MONTH = 730

READY = "READY"
INSUFFICIENT_HOURLY_DATA = "INSUFFICIENT_HOURLY_DATA"
MISSING_OFFER = "MISSING_OFFER"
NO_UNCOVERED_USAGE = "NO_UNCOVERED_USAGE"

COMMIT = "COMMIT"
NO_ACTION = "NO_ACTION"

CONSERVATIVE = "CONSERVATIVE"
BALANCED = "BALANCED"
AGGRESSIVE = "AGGRESSIVE"
PROFILES = (CONSERVATIVE, BALANCED, AGGRESSIVE)
NO_PROFILE = "NONE"

DEFAULT_POLICY = {
    "version": "commitment_policy_v1",
    "min_days": 30,
    # Share of the window's hours that must have loaded data.
    "min_hourly_coverage": 0.98,
    "high_confidence_days": 60,
    # Commitments expiring within this horizon no longer count as coverage.
    "expiry_horizon_days": 90,
    "conservative_utilization": 0.95,
    "aggressive_saving_share": 0.90,
    "usage_drop": 0.20,
    "discount_rate_annual": 0.12,
    "ladder_tranches": 4,
    "min_monthly_saving": 1.0,
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str)


def hour_grid(start: datetime, end: datetime) -> list[str]:
    """ISO hours (``YYYY-MM-DDTHH``) in [start, end)."""
    hours, cursor = [], start.replace(minute=0, second=0, microsecond=0)
    while cursor < end:
        hours.append(cursor.strftime("%Y-%m-%dT%H"))
        cursor += timedelta(hours=1)
    return hours


def window_bounds(as_of: datetime, days: int) -> tuple[datetime, datetime]:
    end = as_of.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return end - timedelta(days=days), end


def group_usage(
    lines: Iterable[UsageLine], *, provider: str, scope: str, instruments: Iterable[Instrument],
) -> dict[CommitmentKey, dict[str, float]]:
    """Hourly series per commitment key; each instrument aggregates over its flexible fields."""
    instruments = list(instruments)
    series: dict[CommitmentKey, dict[str, float]] = {}
    for line in lines:
        hour = line.hour_utc[:13]
        for instrument in instruments:
            key = key_for(provider, instrument, scope, line)
            bucket = series.setdefault(key, {})
            bucket[hour] = bucket.get(hour, 0.0) + float(line.ondemand_cost)
    return series


@dataclass(frozen=True)
class _Curve:
    """Sorted uncovered usage with prefix sums: Σ min(u, c) in O(log n)."""
    values: list[float]
    prefix: list[float]

    @classmethod
    def of(cls, values: Iterable[float]) -> "_Curve":
        ordered = sorted(values)
        prefix = [0.0]
        for value in ordered:
            prefix.append(prefix[-1] + value)
        return cls(ordered, prefix)

    @property
    def hours(self) -> int:
        return len(self.values)

    @property
    def total(self) -> float:
        return self.prefix[-1]

    def used(self, c: float) -> float:
        below = bisect_left(self.values, c)
        return self.prefix[below] + c * (self.hours - below)

    def saving(self, c: float, discount: float) -> float:
        return self.used(c) - self.hours * c * (1 - discount)

    def quantile(self, q: float) -> float | None:
        if not self.values:
            return None
        position = (self.hours - 1) * q
        lower = int(position)
        upper = min(lower + 1, self.hours - 1)
        return self.values[lower] + (self.values[upper] - self.values[lower]) * (position - lower)


def size_profiles(curve: _Curve, discount: float, policy: Mapping = DEFAULT_POLICY) -> dict[str, float]:
    """Commitment per profile (0 when no positive saving exists)."""
    candidates = sorted({value for value in curve.values if value > 0})
    if not candidates:
        return {profile: 0.0 for profile in PROFILES}
    savings = [curve.saving(c, discount) for c in candidates]
    best_index = max(range(len(candidates)), key=lambda i: (savings[i], -candidates[i]))
    if savings[best_index] <= 0:
        return {profile: 0.0 for profile in PROFILES}
    balanced = candidates[best_index]
    target = policy["conservative_utilization"]
    conservative = 0.0
    for c in candidates[: best_index + 1]:
        if curve.used(c) / (curve.hours * c) >= target:
            conservative = c
    floor = savings[best_index] * policy["aggressive_saving_share"]
    aggressive = balanced
    for index in range(best_index, len(candidates)):
        if savings[index] >= floor:
            aggressive = candidates[index]
    return {CONSERVATIVE: conservative, BALANCED: balanced, AGGRESSIVE: aggressive}


def price_commitment(curve: _Curve, c: float, offer: Offer, policy: Mapping = DEFAULT_POLICY) -> dict:
    hours = curve.hours
    used = curve.used(c)
    commit_cost_hour = c * (1 - offer.discount)
    monthly_saving = (used / hours - commit_cost_hour) * HOURS_PER_MONTH
    upfront = offer.upfront_fraction * commit_cost_hour * HOURS_PER_MONTH * offer.term_months
    # The upfront share is no longer paid monthly, so the monthly cash gain grows by it.
    monthly_cash = monthly_saving + offer.upfront_fraction * commit_cost_hour * HOURS_PER_MONTH
    rate = policy["discount_rate_annual"]
    npv = -upfront + sum(monthly_cash / (1 + rate) ** (month / 12) for month in range(1, offer.term_months + 1))
    drop = 1 - policy["usage_drop"]
    used_after_drop = sum(min(value * drop, c) for value in curve.values)
    return {
        "commit_ondemand_hour": round(c, 6),
        "commit_cost_hour": round(commit_cost_hour, 6),
        "expected_utilization": round(used / (hours * c), 6) if c else None,
        "expected_coverage": round(used / curve.total, 6) if curve.total else None,
        "monthly_saving": round(monthly_saving, 2),
        "term_saving": round(monthly_saving * offer.term_months, 2),
        "upfront_cost": round(upfront, 2),
        "npv": round(npv, 2),
        "payback_months": round(upfront / monthly_cash, 2) if upfront and monthly_cash > 0 else (0.0 if not upfront else None),
        "saving_if_usage_drops": round((used_after_drop / hours - commit_cost_hour) * HOURS_PER_MONTH, 2),
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _series_hash(series: Mapping[str, float]) -> str:
    return hashlib.sha256(canonical_json(sorted((hour, round(value, 6)) for hour, value in series.items())).encode()).hexdigest()


def analyze_key(
    *, key: CommitmentKey, instrument: Instrument, series: Mapping[str, float], window_hours: list[str],
    observed_hours: set[str], existing: Iterable[ExistingCommitment] = (), adjustments: Iterable[Adjustment] = (),
    offers: Iterable[Offer] = (), as_of: datetime, currency: str | None = None, policy: Mapping = DEFAULT_POLICY,
) -> list[dict]:
    """One result per profile (or a single NONE result when the key is not ready)."""
    offers = list(offers)
    horizon = as_of + timedelta(days=policy["expiry_horizon_days"])
    matching = [item for item in existing if item.matches(key)]
    lasting = [item for item in matching if item.expires_at is None or item.expires_at > horizon]
    renewals = [item for item in matching if item not in lasting]
    applied = [item for item in adjustments if item.matches(key)]
    covered = sum(item.ondemand_hour for item in lasting)
    removed = sum(item.ondemand_hour for item in applied)
    hours = [hour for hour in window_hours if hour in observed_hours]
    uncovered = [max(0.0, series.get(hour, 0.0) - covered - removed) for hour in hours]
    curve = _Curve.of(uncovered)
    coverage = len(hours) / len(window_hours) if window_hours else 0.0
    days = len(hours) / 24

    base = {
        "provider": key.provider, "commitment_key": key.as_dict(), "instrument": key.instrument,
        "purchase_path": instrument.purchase_path, "engine_version": ENGINE_VERSION,
        "policy_version": policy["version"], "currency": currency,
        "key_hash": hashlib.sha256(canonical_json(key.as_dict()).encode()).hexdigest(),
        "inputs_hash": hashlib.sha256(canonical_json({
            "engine": ENGINE_VERSION, "policy": dict(policy), "key": key.as_dict(),
            "series": _series_hash({hour: series.get(hour, 0.0) for hour in hours}),
            "existing": [vars(item) for item in matching], "adjustments": [vars(item) for item in applied],
            "offers": [item.as_dict() for item in offers], "as_of": as_of.date().isoformat(),
        }).encode()).hexdigest(),
    }
    evidence = {
        "hourly": {
            "window_hours": len(window_hours), "observed_hours": len(hours), "coverage": round(coverage, 6),
            "days_observed": round(days, 2),
            "uncovered_p10": curve.quantile(0.10), "uncovered_p50": curve.quantile(0.50),
            "uncovered_p90": curve.quantile(0.90),
            "uncovered_min": curve.values[0] if curve.values else None,
            "uncovered_max": curve.values[-1] if curve.values else None,
            "uncovered_average": curve.total / curve.hours if curve.hours else None,
        },
        "existing_applied": [{"id": item.commitment_id, "ondemand_hour": item.ondemand_hour,
                              "expires_at": _iso(item.expires_at)} for item in lasting],
        "renewals": [{"id": item.commitment_id, "ondemand_hour": item.ondemand_hour,
                      "expires_at": _iso(item.expires_at)} for item in renewals],
        "adjustments": [vars(item) for item in applied],
        "process_note": instrument.process_note,
        "policy": dict(policy),
    }
    risks = [
        f"Compromisso {item.commitment_id} vence em {item.expires_at:%Y-%m-%d}: o uso dele entrou como descoberto."
        for item in renewals
    ]

    def not_ready(status: str, message: str) -> list[dict]:
        return [dict(base, profile=NO_PROFILE, action_type=NO_ACTION, readiness_status=status, confidence="LOW",
                     rationale=[message], risks=risks, evidence=evidence)]

    if coverage < policy["min_hourly_coverage"] or days < policy["min_days"]:
        return not_ready(INSUFFICIENT_HOURLY_DATA, (
            f"Dado horário insuficiente: {days:.0f} dia(s) com {coverage:.0%} das horas carregadas; "
            f"o motor exige {policy['min_days']} dias e {policy['min_hourly_coverage']:.0%} das horas. "
            "Sem série horária completa não há dimensionamento (evita falso positivo)."
        ))
    if curve.total <= 0:
        return not_ready(NO_UNCOVERED_USAGE, "Todo o uso elegível já está coberto por compromissos ativos.")
    if not offers:
        return not_ready(MISSING_OFFER, f"Sem oferta de mercado ou negociada vigente para {key.label()}.")

    options = []
    for offer in offers:
        for profile, c in size_profiles(curve, offer.discount, policy).items():
            if c <= 0:
                continue
            options.append({"profile": profile, "offer": offer.as_dict(), **price_commitment(curve, c, offer, policy)})
    evidence["options"] = options
    evidence["break_even_utilization"] = {f"{o.term_months}m/{o.payment_option}": round(1 - o.discount, 6) for o in offers}

    results = []
    for profile in PROFILES:
        candidates = [option for option in options if option["profile"] == profile]
        best = max(candidates, key=lambda option: option["npv"], default=None)
        if best is None or best["monthly_saving"] < policy["min_monthly_saving"]:
            results.append(dict(
                base, profile=profile, action_type=NO_ACTION, readiness_status=READY, confidence="MEDIUM",
                rationale=["Nenhum compromisso gera economia nesta curva de uso horária."],
                risks=list(risks), evidence=evidence,
            ))
            continue
        offer = best["offer"]
        tranches = policy["ladder_tranches"]
        profile_risks = list(risks)
        if best["saving_if_usage_drops"] < 0:
            profile_risks.append(
                f"Se o uso cair {policy['usage_drop']:.0%}, o compromisso passa a dar prejuízo "
                f"({best['saving_if_usage_drops']:.2f}/mês)."
            )
        if instrument.purchase_path != "self_service":
            profile_risks.append(instrument.process_note)
        confident = days >= policy["high_confidence_days"] and profile != AGGRESSIVE and best["saving_if_usage_drops"] >= 0
        results.append(dict(
            base, **{name: value for name, value in best.items() if name not in ("profile", "offer")},
            profile=profile, action_type=COMMIT, readiness_status=READY,
            confidence="HIGH" if confident else "MEDIUM",
            term_months=offer["term_months"], payment_option=offer["payment_option"],
            offer_source=offer["source"], offer_id=offer["offer_id"], discount_pct=offer["discount"],
            rationale=[
                f"Uso horário descoberto: P10 {curve.quantile(0.10):.4f} / P50 {curve.quantile(0.50):.4f} / "
                f"P90 {curve.quantile(0.90):.4f} por hora em {days:.0f} dias.",
                f"Compromisso de {best['commit_ondemand_hour']:.4f}/h (on-demand equivalente) a "
                f"{offer['discount']:.0%} de desconto: utilização prevista {best['expected_utilization']:.0%}, "
                f"equilíbrio em {1 - offer['discount']:.0%}.",
                f"Comprar em {tranches} parcelas trimestrais de {best['commit_ondemand_hour'] / tranches:.4f}/h "
                "reduz o risco de mudança de uso.",
            ],
            risks=profile_risks, evidence=dict(evidence, chosen_offer=offer),
        ))
    return results
