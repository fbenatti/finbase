"""Run the commitment engine for one billing connection and persist the results.

Each cloud registers a ``CommitmentProvider``: how to read its hourly eligible
usage (on-demand equivalent, covered or not) and its active commitments. The
offers come from the ``commitment_offer`` catalog (market + negotiated). The
engine only recommends; nothing here buys.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select

from .contracts import INSTRUMENTS, Adjustment, ExistingCommitment, OfferRecord, UsageLine, instruments_for
from .engine import (
    BALANCED, COMMIT, DEFAULT_POLICY, ENGINE_VERSION, HOURS_PER_MONTH, analyze_key, group_usage,
    hour_grid, window_bounds,
)
from .offers import resolve_offers


@dataclass(frozen=True)
class UsageSnapshot:
    scope: str
    currency: str | None
    # Hours whose billing data is loaded for the scope; absent usage there is a real zero.
    observed_hours: set[str]
    lines: list[UsageLine]


@dataclass(frozen=True)
class CommitmentProvider:
    provider: str
    read_usage: Callable[[Any, datetime, datetime], UsageSnapshot]
    read_inventory: Callable[[Any], list[ExistingCommitment]] = field(default=lambda connection: [])
    # Billing connector types this adapter reads (a metrics connection of the same cloud is not one).
    connector_types: tuple[str, ...] = ()


_PROVIDERS: dict[str, CommitmentProvider] = {}


def register_commitment_provider(registration: CommitmentProvider) -> None:
    _PROVIDERS[registration.provider] = registration


def provider_of(connection: Any) -> str:
    scope = getattr(connection, "cloud_scope", None) or str(getattr(connection, "connector_type", "") or "").split("_")[0]
    return str(scope).lower()


def commitment_provider_for(connection: Any) -> CommitmentProvider:
    from . import providers  # noqa: F401  (registers the cloud adapters)

    provider = provider_of(connection)
    registration = _PROVIDERS.get(provider)
    connector_type = getattr(connection, "connector_type", None)
    if registration is None or (registration.connector_types and connector_type not in registration.connector_types):
        raise LookupError(f"sem leitor de uso horário de compromissos para {provider!r} ({connector_type})")
    return registration


def supports_connection(connection: Any) -> bool:
    try:
        commitment_provider_for(connection)
    except LookupError:
        return False
    return True


def offer_records(session: Any, provider: str) -> list[OfferRecord]:
    from app.models.commitment import CommitmentOffer

    rows = session.execute(
        select(CommitmentOffer).where(CommitmentOffer.provider == provider, CommitmentOffer.active.is_(True))
    ).scalars()
    return [OfferRecord(
        offer_id=row.id, provider=row.provider, instrument=row.instrument, term_months=row.term_months,
        payment_option=row.payment_option, source=row.source, pricing_mode=row.pricing_mode,
        discount_pct=row.discount_pct, region=row.region, family=row.family, platform=row.platform,
        scope=row.scope, valid_from=row.valid_from, valid_until=row.valid_until, priority=row.priority,
        burn_down=bool(row.burn_down), min_commit_annual=row.min_commit_annual,
    ) for row in rows]


def rightsizing_adjustments(session: Any, provider: str) -> list[Adjustment]:
    """Pending downsizes of the latest rightsizing runs, as on-demand cost per hour."""
    from app.models.infrastructure_opportunity import InfraOpportunity, InfraOpportunityRun

    latest = {}
    for run in session.execute(
        select(InfraOpportunityRun).where(InfraOpportunityRun.status == "COMPLETED").order_by(InfraOpportunityRun.id)
    ).scalars():
        latest[run.connection_id] = run.id
    if not latest:
        return []
    rows = session.execute(select(InfraOpportunity).where(
        InfraOpportunity.run_id.in_(latest.values()), InfraOpportunity.provider == provider,
        InfraOpportunity.action_type == "RIGHTSIZE_DOWN",
        InfraOpportunity.readiness_status == "READY_FOR_CAPACITY_ANALYSIS",
        InfraOpportunity.state.in_(("NEW", "ACCEPTED")),
    )).scalars()
    return [Adjustment(kind="RIGHTSIZING", ondemand_hour=row.monthly_saving / HOURS_PER_MONTH,
                       reference=f"infra_opportunity:{row.id}")
            for row in rows if (row.monthly_saving or 0) > 0]


def run_commitment_engine(
    session: Any, connection: Any, *, days: int = 30, user_id: int | None = None, as_of: datetime | None = None,
    registration: CommitmentProvider | None = None, offers: list[OfferRecord] | None = None,
    adjustments: list[Adjustment] | None = None,
):
    """Analyze every commitment key of the connection and persist one run."""
    from app.models.commitment import CommitmentRecoRun, CommitmentRecommendation

    registration = registration or commitment_provider_for(connection)
    provider = registration.provider
    as_of = as_of or datetime.now(timezone.utc)
    start, end = window_bounds(as_of, days)
    run = CommitmentRecoRun(
        connection_id=int(connection.id), provider=provider, status="RUNNING", engine_version=ENGINE_VERSION,
        policy_version=DEFAULT_POLICY["version"], window_start_utc=start, window_end_utc=end,
        created_by=user_id, readiness_counts={},
    )
    session.add(run)
    session.commit()
    try:
        usage = registration.read_usage(connection, start, end)
        inventory = registration.read_inventory(connection)
        records = offers if offers is not None else offer_records(session, provider)
        cuts = adjustments if adjustments is not None else rightsizing_adjustments(session, provider)
        instruments = instruments_for(provider)
        series_by_key, excluded = group_usage(usage.lines, provider=provider, scope=usage.scope,
                                              instruments=instruments, observed_hours=usage.observed_hours)
        window_hours = hour_grid(start, end)
        previous = {
            (item.key_hash, item.profile, item.term_months, item.payment_option): item
            for item in session.execute(
                select(CommitmentRecommendation).where(CommitmentRecommendation.connection_id == int(connection.id))
                .order_by(CommitmentRecommendation.id)
            ).scalars()
        }
        results = []
        for key, series in sorted(series_by_key.items(), key=lambda item: item[0].label()):
            instrument = INSTRUMENTS[key.instrument]
            # Rightsizing cuts are not mapped to region/family yet: only spend-based keys absorb them.
            key_cuts = cuts if not instrument.key_fields else []
            for result in analyze_key(
                key=key, instrument=instrument, series=series, window_hours=window_hours,
                observed_hours=usage.observed_hours, existing=inventory, adjustments=key_cuts,
                offers=resolve_offers(records, key, as_of.date()), as_of=as_of, currency=usage.currency,
                excluded_resources=excluded.get(key),
            ):
                prior = previous.get((result["key_hash"], result["profile"], result.get("term_months"),
                                      result.get("payment_option")))
                session.add(CommitmentRecommendation(
                    run_id=run.id, connection_id=int(connection.id), source="ENGINE",
                    state=prior.state if prior else "NEW", state_note=prior.state_note if prior else None,
                    **{name: value for name, value in result.items() if name != "policy_version"},
                ))
                results.append(result)
        actionable = [item for item in results if item["action_type"] == COMMIT and item["profile"] == BALANCED]
        counts: dict[str, int] = {}
        for item in results:
            counts[item["readiness_status"]] = counts.get(item["readiness_status"], 0) + 1
        run.keys_analyzed = len(series_by_key)
        run.recommendations_count = len(actionable)
        run.total_monthly_saving = round(sum(item["monthly_saving"] for item in actionable), 2)
        run.currency = usage.currency
        run.readiness_counts = counts
        run.status = "COMPLETED"
    except Exception as exc:
        session.rollback()
        run = session.get(CommitmentRecoRun, run.id)
        run.status = "FAILED"
        # LookupError carries a message meant for the user (e.g. data still missing).
        run.error_message = (str(exc) if isinstance(exc, LookupError) else f"{type(exc).__name__}: {exc}")[:2000]
    run.finished_at = datetime.now(timezone.utc)
    session.commit()
    return run
