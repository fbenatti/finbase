"""Rightsizing opportunities over the infrastructure metrics Silver."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.rbac import get_effective_permissions, has_permission
from app.database import get_db
from app.models.infrastructure_opportunity import InfraOpportunity, InfraOpportunityRun
from app.models.source_connection import SourceConnection

router = APIRouter(prefix="/oportunidades")

STATES = ("NEW", "ACCEPTED", "IMPLEMENTED", "DISMISSED")


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: int
    days: int = Field(7, ge=1, le=60)
    resolution_seconds: Literal[60, 300] = 300


class StateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["NEW", "ACCEPTED", "IMPLEMENTED", "DISMISSED"]
    note: str | None = Field(None, max_length=2000)


async def _authorize(user, db: AsyncSession, connection_id: int, action: str) -> None:
    permissions = await get_effective_permissions(db, user.id)
    if not has_permission(permissions, "infrastructure_metrics", action, f"connection:{int(connection_id)}"):
        raise HTTPException(403, "Sem permissão para esta conexão de métricas")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _run_dict(run: InfraOpportunityRun) -> dict:
    return {
        "id": run.id, "connection_id": run.connection_id, "status": run.status,
        "engine_version": run.engine_version, "policy_version": run.policy_version,
        "window_start_utc": _iso(run.window_start_utc), "window_end_utc": _iso(run.window_end_utc),
        "resolution_seconds": run.resolution_seconds,
        "resources_analyzed": run.resources_analyzed, "opportunities_count": run.opportunities_count,
        "total_monthly_saving": run.total_monthly_saving, "avg_monthly_saving": run.avg_monthly_saving,
        "currency": run.currency, "readiness_counts": run.readiness_counts or {},
        "error_message": run.error_message, "started_at": _iso(run.started_at), "finished_at": _iso(run.finished_at),
    }


def _opportunity_dict(item: InfraOpportunity, *, full: bool = False) -> dict:
    evidence = item.evidence or {}
    cpu, memory = evidence.get("cpu") or {}, evidence.get("memory") or {}
    data = {
        "id": item.id, "run_id": item.run_id, "connection_id": item.connection_id, "provider": item.provider,
        "resource_id": item.resource_id, "resource_name": item.resource_name,
        "action_type": item.action_type, "readiness_status": item.readiness_status,
        "state": item.state, "confidence": item.confidence,
        "from_config": item.from_config, "to_config": item.to_config,
        "current_monthly_cost": item.current_monthly_cost, "target_monthly_cost": item.target_monthly_cost,
        "monthly_saving": item.monthly_saving,
        "annual_saving": round(item.monthly_saving * 12, 2) if item.monthly_saving is not None else None,
        "currency": item.currency,
        "cpu_p95": cpu.get("p95"), "cpu_p99": cpu.get("p99"), "cpu_max": cpu.get("max"),
        "memory_p95": memory.get("p95"), "memory_p99": memory.get("p99"),
        "coverage": min([s["coverage"] for s in (cpu, memory) if s.get("coverage") is not None], default=None),
        "risks": item.risks or [], "state_note": item.state_note,
        "state_changed_at": _iso(item.state_changed_at), "created_at": _iso(item.created_at),
        "inputs_hash": item.inputs_hash, "engine_version": item.engine_version,
    }
    if full:
        data.update({"rationale": item.rationale or [], "evidence": evidence})
    return data


async def _latest_run(db: AsyncSession, connection_id: int) -> InfraOpportunityRun | None:
    return (await db.execute(
        select(InfraOpportunityRun).where(
            InfraOpportunityRun.connection_id == connection_id, InfraOpportunityRun.status == "COMPLETED",
        ).order_by(InfraOpportunityRun.id.desc()).limit(1)
    )).scalar_one_or_none()


def _execute(connection_id: int, days: int, resolution_seconds: int, user_id: int):
    from app.database import SyncSession
    from app.services.infrastructure_metrics.opportunity_runner import run_opportunity_engine

    session = SyncSession()
    try:
        connection = session.get(SourceConnection, connection_id)
        return _run_dict(run_opportunity_engine(
            session, connection, days=days, resolution_seconds=resolution_seconds, user_id=user_id,
        ))
    finally:
        session.close()


@router.get("/fontes")
async def list_sources(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Metric sources the engine can analyze (any cloud with rightsizing registered)."""
    from app.services.infrastructure_metrics.registry import metric_source_types

    permissions = await get_effective_permissions(db, user.id)
    rows = (await db.execute(
        select(SourceConnection).where(SourceConnection.connector_type.in_(metric_source_types()))
        .order_by(SourceConnection.name)
    )).scalars().all()
    return {"items": [
        {"id": row.id, "code": row.code, "name": row.name, "connector_type": row.connector_type,
         "provider": row.cloud_scope}
        for row in rows
        if has_permission(permissions, "infrastructure_metrics", "read", f"connection:{row.id}")
    ]}


@router.post("/executar")
async def run_engine(body: RunRequest, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    connection = await db.get(SourceConnection, body.connection_id)
    if not connection:
        raise HTTPException(404, "Fonte de métricas não encontrada")
    try:
        from app.services.infrastructure_metrics.registry import provider_for_connection, rightsizing_for

        rightsizing_for(provider_for_connection(connection).provider)
    except LookupError as exc:
        raise HTTPException(422, f"Fonte sem rightsizing configurado: {exc}") from exc
    await _authorize(user, db, connection.id, "create")
    running = (await db.execute(select(InfraOpportunityRun.id).where(
        InfraOpportunityRun.connection_id == connection.id, InfraOpportunityRun.status == "RUNNING",
    ))).first()
    if running:
        raise HTTPException(409, f"A análise {running[0]} ainda está em execução")
    try:
        return await asyncio.to_thread(_execute, int(connection.id), body.days, body.resolution_seconds, int(user.id))
    except (ValueError, PermissionError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/execucoes")
async def list_runs(connection_id: int = Query(..., gt=0), limit: int = Query(20, ge=1, le=200),
                    user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _authorize(user, db, connection_id, "read")
    runs = (await db.execute(
        select(InfraOpportunityRun).where(InfraOpportunityRun.connection_id == connection_id)
        .order_by(InfraOpportunityRun.id.desc()).limit(limit)
    )).scalars().all()
    return {"items": [_run_dict(run) for run in runs]}


@router.get("/resumo")
async def summary(connection_id: int = Query(..., gt=0), user=Depends(get_current_user),
                  db: AsyncSession = Depends(get_db)):
    await _authorize(user, db, connection_id, "read")
    run = await _latest_run(db, connection_id)
    completed = (await db.execute(select(
        func.count(InfraOpportunityRun.id), func.avg(InfraOpportunityRun.total_monthly_saving),
    ).where(InfraOpportunityRun.connection_id == connection_id, InfraOpportunityRun.status == "COMPLETED"))).one()
    if not run:
        return {"run": None, "completed_runs": 0, "avg_saving_per_run": None}
    items = (await db.execute(select(InfraOpportunity).where(InfraOpportunity.run_id == run.id))).scalars().all()
    by = lambda attr: {key: sum(1 for item in items if getattr(item, attr) == key)
                       for key in sorted({getattr(item, attr) for item in items})}
    actionable = [item for item in items if item.action_type == "RIGHTSIZE_DOWN"
                  and item.readiness_status == "READY_FOR_CAPACITY_ANALYSIS" and (item.monthly_saving or 0) > 0]
    pick = lambda states: round(sum(i.monthly_saving for i in actionable if i.state in states), 2)
    projected = pick(("NEW", "ACCEPTED", "IMPLEMENTED"))
    current_cost = sum(item.current_monthly_cost or 0 for item in items)
    return {
        "run": _run_dict(run),
        "completed_runs": completed[0],
        "avg_saving_per_run": round(float(completed[1]), 2) if completed[1] is not None else None,
        "currency": run.currency,
        "resources_analyzed": len(items),
        "opportunities": len(actionable),
        "projected_saving_monthly": projected,
        "projected_saving_annual": round(projected * 12, 2),
        "implemented_saving_monthly": pick(("IMPLEMENTED",)),
        "accepted_saving_monthly": pick(("ACCEPTED",)),
        "dismissed_saving_monthly": pick(("DISMISSED",)),
        "avg_saving_per_opportunity": round(projected / len(actionable), 2) if actionable else None,
        "analyzed_monthly_cost": round(current_cost, 2),
        "saving_ratio": round(projected / current_cost, 4) if current_cost else None,
        "by_action": by("action_type"),
        "by_state": by("state"),
        "by_readiness": by("readiness_status"),
        "by_confidence": by("confidence"),
    }


@router.get("")
async def list_opportunities(connection_id: int = Query(..., gt=0), run_id: int | None = None,
                             user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _authorize(user, db, connection_id, "read")
    if run_id is None:
        run = await _latest_run(db, connection_id)
        if not run:
            return {"run_id": None, "items": []}
        run_id = run.id
    items = (await db.execute(
        select(InfraOpportunity).where(InfraOpportunity.connection_id == connection_id, InfraOpportunity.run_id == run_id)
        .order_by(InfraOpportunity.monthly_saving.desc().nulls_last())
    )).scalars().all()
    return {"run_id": run_id, "items": [_opportunity_dict(item) for item in items]}


@router.get("/{opportunity_id}")
async def get_opportunity(opportunity_id: int, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    item = await db.get(InfraOpportunity, opportunity_id)
    if not item:
        raise HTTPException(404, "Oportunidade não encontrada")
    await _authorize(user, db, item.connection_id, "read")
    history = (await db.execute(
        select(InfraOpportunity, InfraOpportunityRun).join(InfraOpportunityRun, InfraOpportunity.run_id == InfraOpportunityRun.id)
        .where(InfraOpportunity.connection_id == item.connection_id, InfraOpportunity.resource_id == item.resource_id)
        .order_by(InfraOpportunity.id.desc()).limit(20)
    )).all()
    data = _opportunity_dict(item, full=True)
    data["history"] = [{
        "run_id": opp.run_id, "finished_at": _iso(run.finished_at), "action_type": opp.action_type,
        "to_config": opp.to_config, "monthly_saving": opp.monthly_saving, "readiness_status": opp.readiness_status,
        "window_start_utc": _iso(run.window_start_utc), "window_end_utc": _iso(run.window_end_utc),
    } for opp, run in history]
    return data


def _read_chart_series(item: InfraOpportunity, run: InfraOpportunityRun, resolution_seconds: int) -> dict:
    from app.services.infrastructure_metrics.opportunities import CPU_METRIC
    from app.services.infrastructure_metrics.opportunity_runner import ANALYZED_METRICS, read_series

    points: dict[str, dict] = {}
    for metric in ANALYZED_METRICS:
        key = "cpu" if metric == CPU_METRIC else "memory"
        for row in read_series(
            item.connection_id, item.resource_id, metric, run.window_start_utc, run.window_end_utc, resolution_seconds,
        ):
            point = points.setdefault(row["bucket_start_utc"], {"t": row["bucket_start_utc"]})
            point[f"{key}_avg"] = row.get("value_average")
            point[f"{key}_max"] = row.get("value_maximum")
    return {
        "resolution_seconds": resolution_seconds,
        "window_start_utc": _iso(run.window_start_utc),
        "window_end_utc": _iso(run.window_end_utc),
        "from_config": item.from_config,
        "to_config": item.to_config,
        "points": [points[key] for key in sorted(points)],
    }


@router.get("/{opportunity_id}/serie")
async def opportunity_series(
    opportunity_id: int, resolution_seconds: int = Query(300),
    user=Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Observed CPU/memory series of the run window; the UI projects it onto the target size."""
    if resolution_seconds not in (60, 300, 900):
        raise HTTPException(422, "resolution_seconds deve ser 60, 300 ou 900")
    item = await db.get(InfraOpportunity, opportunity_id)
    if not item:
        raise HTTPException(404, "Oportunidade não encontrada")
    await _authorize(user, db, item.connection_id, "read")
    run = await db.get(InfraOpportunityRun, item.run_id)
    try:
        return await asyncio.to_thread(_read_chart_series, item, run, resolution_seconds)
    except Exception as exc:
        raise HTTPException(503, f"Silver indisponível para consulta ({type(exc).__name__})") from exc


@router.post("/{opportunity_id}/estado")
async def set_state(opportunity_id: int, body: StateRequest, user=Depends(get_current_user),
                    db: AsyncSession = Depends(get_db)):
    item = await db.get(InfraOpportunity, opportunity_id)
    if not item:
        raise HTTPException(404, "Oportunidade não encontrada")
    await _authorize(user, db, item.connection_id, "update")
    item.state = body.state
    item.state_note = body.note
    item.state_changed_by = int(user.id)
    item.state_changed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(item)
    return _opportunity_dict(item, full=True)
