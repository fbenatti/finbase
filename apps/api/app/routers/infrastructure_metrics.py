"""Manual, bounded entry points for the OCI infrastructure metric pilot."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.rbac import get_effective_permissions, has_permission
from app.database import get_db
from app.models.etl_task import EtlTask
from app.models.source_connection import SourceConnection
from app.services.infrastructure_metrics.catalog import get_metric
from app.services.infrastructure_metrics.collector import DEFAULT_STATISTICS, estimated_api_calls, plan_queries
from app.services.infrastructure_metrics.config import config_from_connection

router = APIRouter(prefix="/custos/metricas-infra", tags=["infrastructure-metrics"])
MAX_API_CALLS = 128


class CollectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: int
    start_utc: datetime
    end_utc: datetime
    resolution_seconds: Literal[60, 300, 900]
    statistics: tuple[str, ...] = DEFAULT_STATISTICS


async def _authorize_connection(user, db: AsyncSession, connection_id: int, action: str) -> None:
    permissions = await get_effective_permissions(db, user.id)
    if not has_permission(
        permissions, "infrastructure_metrics", action, f"connection:{int(connection_id)}",
    ):
        raise HTTPException(403, "Sem permissão para esta conexão de métricas")


async def _validated_plan(body: CollectionRequest, db: AsyncSession, user, action: str):
    if body.start_utc.tzinfo is None or body.end_utc.tzinfo is None:
        raise HTTPException(422, "start_utc e end_utc devem incluir fuso horário")
    start = body.start_utc.astimezone(timezone.utc)
    end = body.end_utc.astimezone(timezone.utc)
    if end <= start or end - start > timedelta(hours=24):
        raise HTTPException(422, "A janela deve ser positiva e ter no máximo 24 horas")
    conn = await db.get(SourceConnection, body.connection_id)
    if not conn or conn.connector_type != "oci":
        raise HTTPException(404, "Conexão OCI não encontrada")
    await _authorize_connection(user, db, conn.id, action)
    try:
        config = config_from_connection(conn)
        if not config.enabled:
            raise PermissionError("coleta desabilitada")
        if body.resolution_seconds not in config.resolutions_seconds:
            raise ValueError("resolução não habilitada na configuração do piloto")
        scope = config.scope(int(conn.id))
        descriptors = tuple(get_metric("oci", "oci_computeagent", name) for name in config.metric_names)
        plans = plan_queries(
            scope, descriptors, start_utc=start, end_utc=end,
            resolution_seconds=body.resolution_seconds,
            statistics=tuple(body.statistics),
        )
    except (ValueError, PermissionError, LookupError) as exc:
        raise HTTPException(422, str(exc)) from exc
    calls = estimated_api_calls(plans)
    if calls > MAX_API_CALLS:
        raise HTTPException(422, f"Plano excede o limite de {MAX_API_CALLS} chamadas OCI")
    return conn, config, plans, calls, start, end


def _safe_plan(conn, config, plans, calls: int, start: datetime, end: datetime) -> dict:
    return {
        "connection_id": conn.id,
        "provider": "oci",
        "enabled": config.enabled,
        "resource_count": len(config.resource_ids),
        "metric_names": [plan.descriptor.name for plan in plans],
        "statistics": list(plans[0].statistics) if plans else [],
        "resolution_seconds": plans[0].resolution_seconds if plans else None,
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "estimated_api_calls": calls,
        "retention_days": config.retention_days,
        "schedule_enabled": False,
    }


@router.post("/planejar")
async def plan_collection(
    body: CollectionRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conn, config, plans, calls, start, end = await _validated_plan(body, db, user, "read")
    return _safe_plan(conn, config, plans, calls, start, end)


@router.post("/coletar", status_code=202)
async def queue_collection(
    body: CollectionRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conn, config, plans, calls, start, end = await _validated_plan(body, db, user, "create")
    active = (await db.execute(
        select(EtlTask).where(
            EtlTask.task_type == "infrastructure_metrics_collection",
            EtlTask.connection_id == conn.id,
            EtlTask.status.in_(("QUEUED", "RUNNING")),
        ).order_by(EtlTask.id.desc()).limit(1)
    )).scalar_one_or_none()
    if active:
        return {"task_id": active.id, "status": active.status, "plan": _safe_plan(conn, config, plans, calls, start, end)}
    task = EtlTask(
        task_type="infrastructure_metrics_collection", status="QUEUED",
        connection_id=conn.id, connection_ids=[conn.id], errors=[],
        result_json={
            "start_utc": start.isoformat(), "end_utc": end.isoformat(),
            "resolution_seconds": body.resolution_seconds,
            "statistics": list(body.statistics), "persist": True,
        },
        phase="Na fila: coleta manual de métricas de infraestrutura OCI",
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return {"task_id": task.id, "status": task.status, "plan": _safe_plan(conn, config, plans, calls, start, end)}


@router.get("/tarefas/{task_id}")
async def collection_task_status(
    task_id: int,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    task = await db.get(EtlTask, task_id)
    if not task or task.task_type != "infrastructure_metrics_collection":
        raise HTTPException(404, "Tarefa de métricas não encontrada")
    if task.connection_id is None:
        raise HTTPException(404, "Tarefa de métricas sem conexão associada")
    await _authorize_connection(user, db, int(task.connection_id), "read")
    result = task.result_json if isinstance(task.result_json, dict) else {}
    summary = {
        key: result[key] for key in (
            "resource_count", "metric_count", "resolution_seconds", "estimated_api_calls",
            "completed_query_count", "failed_query_count", "raw_rows_persisted", "persisted_by_day",
        ) if key in result
    }
    return {
        "task_id": task.id,
        "status": task.status,
        "phase": task.phase,
        "rows_raw": task.rows_raw,
        "rows_destination": task.rows_destination,
        "errors": [str(error)[:80] for error in (task.errors or [])[:20]],
        "summary": summary,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
    }
