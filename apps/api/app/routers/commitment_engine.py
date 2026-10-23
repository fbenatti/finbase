"""Commitment (RI / Savings Plan / CUD / spend commitment) recommendations.

The engine only recommends; it never buys. Authorization comes from the
``reservations`` module through the ``/custos/reservas`` prefix.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.database import get_db
from app.models.commitment import CommitmentOffer, CommitmentRecommendation, CommitmentRecoRun
from app.models.source_connection import SourceConnection
from app.services.commitments.contracts import INSTRUMENTS, NEGOTIATED, SPEND
from app.services.commitments.offers import UPFRONT_FRACTION

router = APIRouter(prefix="/custos/reservas/motor", tags=["commitment_engine"])

# PURCHASED/REALIZED are confirmed by the inventory, not set by hand.
USER_STATES = ("NEW", "APPROVED", "REJECTED")


class OfferBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument: str
    term_months: int = Field(..., ge=1, le=120)
    payment_option: Literal["NO_UPFRONT", "PARTIAL_UPFRONT", "ALL_UPFRONT", "CONTRACT"] = "NO_UPFRONT"
    source: Literal["MARKET", "NEGOTIATED"] = "MARKET"
    pricing_mode: Literal["DISCOUNT", "EXTRA_OVER_MARKET"] = "DISCOUNT"
    discount_pct: float = Field(..., gt=0, lt=1)
    region: str | None = Field(None, max_length=80)
    family: str | None = Field(None, max_length=120)
    platform: str | None = Field(None, max_length=80)
    scope: str | None = Field(None, max_length=255)
    valid_from: date | None = None
    valid_until: date | None = None
    priority: int = 0
    burn_down: bool = False
    min_commit_annual: float | None = Field(None, ge=0)
    contract_id: int | None = None
    currency: str | None = Field(None, max_length=10)
    notes: str | None = Field(None, max_length=4000)
    active: bool = True

    @model_validator(mode="after")
    def _check(self):
        instrument = INSTRUMENTS.get(self.instrument)
        if instrument is None:
            raise ValueError(f"instrumento desconhecido: {self.instrument}")
        negotiated_only = instrument.purchase_path == NEGOTIATED
        if negotiated_only and self.source != "NEGOTIATED":
            raise ValueError(f"{instrument.label} só existe como acordo negociado")
        if negotiated_only and self.payment_option != "CONTRACT":
            raise ValueError("acordo de gasto usa payment_option CONTRACT")
        if not negotiated_only and self.payment_option not in instrument.payment_options:
            raise ValueError(f"{instrument.label} aceita {', '.join(instrument.payment_options)}")
        if self.pricing_mode == "EXTRA_OVER_MARKET" and self.source != "NEGOTIATED":
            raise ValueError("EXTRA_OVER_MARKET só vale para oferta negociada")
        if self.min_commit_annual and instrument.basis != SPEND:
            raise ValueError("faixa de compromisso anual só vale para compromisso de gasto")
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("valid_until anterior a valid_from")
        return self


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: int
    days: int = Field(30, ge=30, le=180)


class StateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["NEW", "APPROVED", "REJECTED"]
    note: str | None = Field(None, max_length=2000)


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _offer_dict(row: CommitmentOffer) -> dict:
    instrument = INSTRUMENTS.get(row.instrument)
    return {
        "id": row.id, "provider": row.provider, "instrument": row.instrument,
        "instrument_label": instrument.label if instrument else row.instrument,
        "purchase_path": instrument.purchase_path if instrument else None,
        "term_months": row.term_months, "payment_option": row.payment_option, "source": row.source,
        "pricing_mode": row.pricing_mode, "discount_pct": row.discount_pct, "region": row.region,
        "family": row.family, "platform": row.platform, "scope": row.scope,
        "valid_from": _iso(row.valid_from), "valid_until": _iso(row.valid_until), "priority": row.priority,
        "burn_down": row.burn_down, "min_commit_annual": row.min_commit_annual,
        "contract_id": row.contract_id, "currency": row.currency,
        "notes": row.notes, "active": row.active, "updated_at": _iso(row.updated_at),
    }


def _run_dict(run: CommitmentRecoRun) -> dict:
    return {
        "id": run.id, "connection_id": run.connection_id, "provider": run.provider, "status": run.status,
        "engine_version": run.engine_version, "policy_version": run.policy_version,
        "window_start_utc": _iso(run.window_start_utc), "window_end_utc": _iso(run.window_end_utc),
        "keys_analyzed": run.keys_analyzed, "recommendations_count": run.recommendations_count,
        "total_monthly_saving": run.total_monthly_saving, "currency": run.currency,
        "readiness_counts": run.readiness_counts or {}, "error_message": run.error_message,
        "started_at": _iso(run.started_at), "finished_at": _iso(run.finished_at),
    }


def _reco_dict(item: CommitmentRecommendation, *, full: bool = False) -> dict:
    instrument = INSTRUMENTS.get(item.instrument)
    data = {
        "id": item.id, "run_id": item.run_id, "connection_id": item.connection_id, "provider": item.provider,
        "source": item.source, "commitment_key": item.commitment_key, "instrument": item.instrument,
        "instrument_label": instrument.label if instrument else item.instrument,
        "purchase_path": item.purchase_path,
        "process_note": instrument.process_note if instrument else None,
        "profile": item.profile, "action_type": item.action_type, "readiness_status": item.readiness_status,
        "state": item.state, "confidence": item.confidence, "term_months": item.term_months,
        "payment_option": item.payment_option, "offer_source": item.offer_source, "discount_pct": item.discount_pct,
        "commit_ondemand_hour": item.commit_ondemand_hour, "commit_cost_hour": item.commit_cost_hour,
        "expected_utilization": item.expected_utilization, "expected_coverage": item.expected_coverage,
        "monthly_saving": item.monthly_saving, "term_saving": item.term_saving, "upfront_cost": item.upfront_cost,
        "npv": item.npv, "payback_months": item.payback_months,
        "saving_if_usage_drops": item.saving_if_usage_drops, "currency": item.currency,
        "risks": item.risks or [], "rationale": item.rationale or [],
        # Spend pools: the projected yearly consumption, also when no discount is registered yet.
        "projected_annual_spend": (item.evidence or {}).get("projected_annual_spend"),
        "state_note": item.state_note, "state_changed_at": _iso(item.state_changed_at),
        "inputs_hash": item.inputs_hash, "engine_version": item.engine_version,
    }
    if full:
        data["evidence"] = item.evidence or {}
    return data


@router.get("/instrumentos")
async def list_instruments():
    return {"items": [{
        "code": item.code, "provider": item.provider, "label": item.label, "key_fields": list(item.key_fields),
        "basis": item.basis,
        "purchase_path": item.purchase_path, "terms_months": list(item.terms_months),
        "payment_options": list(item.payment_options) or ["CONTRACT"], "process_note": item.process_note,
    } for item in INSTRUMENTS.values()], "payment_upfront_fraction": UPFRONT_FRACTION}


@router.get("/conexoes")
async def list_connections(db: AsyncSession = Depends(get_db)):
    """Billing connections with a usage adapter, with their latest completed run."""
    from app.services.commitments.runner import provider_of, supports_connection

    rows = (await db.execute(select(SourceConnection).order_by(SourceConnection.name))).scalars().all()
    items = []
    for row in rows:
        if not supports_connection(row):
            continue
        latest = (await db.execute(
            select(CommitmentRecoRun).where(CommitmentRecoRun.connection_id == row.id)
            .order_by(CommitmentRecoRun.id.desc()).limit(1)
        )).scalar_one_or_none()
        items.append({"id": row.id, "code": row.code, "name": row.name, "provider": provider_of(row),
                      "latest_run": _run_dict(latest) if latest else None})
    return {"items": items}


@router.get("/ofertas")
async def list_offers(provider: str | None = None, include_inactive: bool = False,
                      db: AsyncSession = Depends(get_db)):
    query = select(CommitmentOffer).order_by(CommitmentOffer.provider, CommitmentOffer.instrument,
                                             CommitmentOffer.term_months, CommitmentOffer.id)
    if provider:
        query = query.where(CommitmentOffer.provider == provider)
    if not include_inactive:
        query = query.where(CommitmentOffer.active.is_(True))
    return {"items": [_offer_dict(row) for row in (await db.execute(query)).scalars()]}


@router.post("/ofertas", status_code=201)
async def create_offer(body: OfferBody, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    row = CommitmentOffer(provider=INSTRUMENTS[body.instrument].provider, created_by=int(user.id),
                          **body.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _offer_dict(row)


@router.put("/ofertas/{offer_id}")
async def update_offer(offer_id: int, body: OfferBody, db: AsyncSession = Depends(get_db)):
    row = await db.get(CommitmentOffer, offer_id)
    if not row:
        raise HTTPException(404, "Oferta não encontrada")
    for name, value in body.model_dump().items():
        setattr(row, name, value)
    row.provider = INSTRUMENTS[body.instrument].provider
    await db.commit()
    await db.refresh(row)
    return _offer_dict(row)


@router.delete("/ofertas/{offer_id}")
async def deactivate_offer(offer_id: int, db: AsyncSession = Depends(get_db)):
    """Offers are deactivated, not deleted: past runs cite them in their evidence."""
    row = await db.get(CommitmentOffer, offer_id)
    if not row:
        raise HTTPException(404, "Oferta não encontrada")
    row.active = False
    await db.commit()
    return {"id": offer_id, "active": False}


def _execute(connection_id: int, days: int, user_id: int) -> dict:
    from app.database import SyncSession
    from app.services.commitments.runner import run_commitment_engine

    session = SyncSession()
    try:
        connection = session.get(SourceConnection, connection_id)
        return _run_dict(run_commitment_engine(session, connection, days=days, user_id=user_id))
    finally:
        session.close()


@router.post("/execucoes")
async def run_engine(body: RunRequest, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from app.services.commitments.runner import commitment_provider_for

    connection = await db.get(SourceConnection, body.connection_id)
    if not connection:
        raise HTTPException(404, "Conexão não encontrada")
    try:
        commitment_provider_for(connection)
    except LookupError as exc:
        raise HTTPException(422, f"Conexão sem leitor de uso: {exc}") from exc
    running = (await db.execute(select(CommitmentRecoRun.id).where(
        CommitmentRecoRun.connection_id == connection.id, CommitmentRecoRun.status == "RUNNING",
    ))).first()
    if running:
        raise HTTPException(409, f"A análise {running[0]} ainda está em execução")
    return await asyncio.to_thread(_execute, int(connection.id), body.days, int(user.id))


@router.get("/execucoes")
async def list_runs(connection_id: int = Query(..., gt=0), limit: int = Query(20, ge=1, le=200),
                    db: AsyncSession = Depends(get_db)):
    runs = (await db.execute(
        select(CommitmentRecoRun).where(CommitmentRecoRun.connection_id == connection_id)
        .order_by(CommitmentRecoRun.id.desc()).limit(limit)
    )).scalars().all()
    return {"items": [_run_dict(run) for run in runs]}


@router.get("/recomendacoes")
async def list_recommendations(
    connection_id: int = Query(..., gt=0), run_id: int | None = None,
    profile: Literal["CONSERVATIVE", "BALANCED", "AGGRESSIVE", "NONE"] | None = None,
    actionable_only: bool = False, db: AsyncSession = Depends(get_db),
):
    if run_id is None:
        run_id = (await db.execute(
            select(CommitmentRecoRun.id).where(
                CommitmentRecoRun.connection_id == connection_id, CommitmentRecoRun.status == "COMPLETED",
            ).order_by(CommitmentRecoRun.id.desc()).limit(1)
        )).scalar_one_or_none()
        if run_id is None:
            return {"run_id": None, "items": []}
    query = select(CommitmentRecommendation).where(
        CommitmentRecommendation.connection_id == connection_id, CommitmentRecommendation.run_id == run_id,
    )
    if profile:
        query = query.where(CommitmentRecommendation.profile == profile)
    if actionable_only:
        query = query.where(CommitmentRecommendation.action_type == "COMMIT")
    items = (await db.execute(query.order_by(CommitmentRecommendation.npv.desc().nulls_last()))).scalars().all()
    return {"run_id": run_id, "items": [_reco_dict(item) for item in items]}


@router.get("/recomendacoes/{reco_id}")
async def get_recommendation(reco_id: int, db: AsyncSession = Depends(get_db)):
    item = await db.get(CommitmentRecommendation, reco_id)
    if not item:
        raise HTTPException(404, "Recomendação não encontrada")
    return _reco_dict(item, full=True)


@router.patch("/recomendacoes/{reco_id}/estado")
async def set_state(reco_id: int, body: StateRequest, user=Depends(get_current_user),
                    db: AsyncSession = Depends(get_db)):
    item = await db.get(CommitmentRecommendation, reco_id)
    if not item:
        raise HTTPException(404, "Recomendação não encontrada")
    if item.state not in USER_STATES:
        raise HTTPException(409, f"Estado {item.state} é confirmado pelo inventário e não muda à mão")
    item.state = body.state
    item.state_note = body.note
    item.state_changed_by = int(user.id)
    item.state_changed_at = datetime.now(timezone.utc)
    await db.commit()
    return _reco_dict(item)
