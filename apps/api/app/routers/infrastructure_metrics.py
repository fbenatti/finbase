"""Manual, bounded entry points for the OCI infrastructure metric pilot."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.rbac import get_effective_permissions, has_permission
from app.database import get_db
from app.models.etl_task import EtlTask
from app.models.source_connection import SourceConnection
from app.services.infrastructure_metrics.catalog import get_metric
from app.services.infrastructure_metrics.collector import DEFAULT_STATISTICS, estimated_api_calls, plan_queries
from app.services.infrastructure_metrics.config import config_from_connection, max_collection_window, validate_collection_age

router = APIRouter(prefix="/custos/metricas-infra", tags=["infrastructure-metrics"])
MAX_API_CALLS = 128


class CollectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: int
    start_utc: datetime
    end_utc: datetime
    resolution_seconds: Literal[60, 300, 900]
    statistics: tuple[str, ...] = DEFAULT_STATISTICS


class ProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: int
    resource_id: str
    metric: str
    start_utc: datetime
    end_utc: datetime
    resolution_seconds: Literal[60, 300, 900] = 60
    value_statistic: Literal["value_average", "value_minimum", "value_maximum", "value_total", "sample_count"] = "value_average"


PROFILE_MAX_POINTS = 20_000


def _profile_snapshot_date(row: dict) -> str:
    return str(row["window_to_day"])


def _read_silver_slice(table_name: str, filters: dict, limit: int) -> list[dict]:
    """Read a bounded allowlisted Iceberg slice without accepting SQL text."""
    from pyiceberg.expressions import And, EqualTo, GreaterThanOrEqual, LessThan

    from app.services import iceberg_service

    if not iceberg_service.table_exists(table_name, namespace="silver"):
        return []
    table = iceberg_service.get_catalog().load_table(("silver", table_name))
    predicates = []
    for field, value in filters.items():
        if field.endswith("__gte"):
            predicates.append(GreaterThanOrEqual(field[:-5], value))
        elif field.endswith("__lt"):
            predicates.append(LessThan(field[:-4], value))
        else:
            predicates.append(EqualTo(field, value))
    expression = And(*predicates) if len(predicates) > 1 else predicates[0]
    arrow = table.scan(row_filter=expression).limit(limit).to_arrow()
    return [
        {key: value.isoformat() if isinstance(value, datetime) else value
         for key, value in row.items()}
        for row in arrow.to_pylist()
    ]


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
    limit = max_collection_window(body.resolution_seconds)
    if end <= start or end - start > limit:
        raise HTTPException(
            422, f"A janela deve ser positiva e ter no máximo {int(limit.total_seconds() // 3600)} horas nesta resolução",
        )
    try:
        validate_collection_age(start, body.resolution_seconds)
    except ValueError:
        raise HTTPException(
            422, f"A OCI só entrega buckets de {body.resolution_seconds // 60} min dos últimos 30 dias; colete dias mais antigos em 1 min",
        ) from None
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


def _collection_config_snapshot(config) -> dict:
    """Freeze the allowlist and acquisition semantics, excluding credentials."""
    return {
        "version": 1,
        "enabled": config.enabled,
        "compartment_id": config.compartment_id,
        "provider_account_id": config.provider_account_id,
        "region": config.region,
        "resource_ids": list(config.resource_ids),
        "metric_names": list(config.metric_names),
        "resolutions_seconds": list(config.resolutions_seconds),
        "retention_days": config.retention_days,
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
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"infrastructure-metrics:{int(conn.id)}"},
    )
    active = (await db.execute(
        select(EtlTask).where(
            EtlTask.task_type == "infrastructure_metrics_collection",
            EtlTask.connection_id == conn.id,
            EtlTask.status.in_(("QUEUED", "RUNNING")),
        ).order_by(EtlTask.id.desc()).limit(1)
    )).scalar_one_or_none()
    if active:
        active_result = active.result_json if isinstance(active.result_json, dict) else {}
        active_plan = active_result.get("request_plan")
        if not isinstance(active_plan, dict):
            raise HTTPException(
                409,
                f"A tarefa {active.id} já está ativa, mas não possui snapshot do plano; "
                "aguarde-a terminar antes de solicitar outra coleta.",
            )
        return {"task_id": active.id, "status": active.status, "plan": active_plan}
    task = EtlTask(
        task_type="infrastructure_metrics_collection", status="QUEUED",
        connection_id=conn.id, connection_ids=[conn.id], errors=[],
        result_json={
            "start_utc": start.isoformat(), "end_utc": end.isoformat(),
            "resolution_seconds": body.resolution_seconds,
            "statistics": list(body.statistics), "persist": True,
            "request_plan": _safe_plan(conn, config, plans, calls, start, end),
            "config_snapshot": _collection_config_snapshot(config),
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
            "coverage_rows_prepared", "coverage_persisted_by_day",
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


@router.post("/perfis/gerar")
async def generate_metric_profile(
    body: ProfileRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Materialize deterministic percentiles from one selected Silver series."""
    start, end = body.start_utc, body.end_utc
    if start.tzinfo is None or end.tzinfo is None:
        raise HTTPException(422, "start_utc e end_utc devem incluir fuso horário")
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    if end <= start or end - start > timedelta(days=60):
        raise HTTPException(422, "A janela deve ser positiva e ter no máximo 60 dias")
    connection = await db.get(SourceConnection, body.connection_id)
    if not connection or connection.connector_type != "oci":
        raise HTTPException(404, "Conexão OCI não encontrada")
    await _authorize_connection(user, db, connection.id, "create")
    try:
        config = config_from_connection(connection)
        if not config.enabled:
            raise ValueError("coleta de métricas está desabilitada nesta conexão")
        if body.resource_id not in config.resource_ids:
            raise ValueError("recurso fora da allowlist desta conexão")
        descriptor = get_metric("oci", "oci_computeagent", body.metric)
        if descriptor.name not in config.metric_names:
            raise ValueError("métrica fora da allowlist desta conexão")
    except (ValueError, LookupError) as exc:
        raise HTTPException(422, str(exc)) from exc

    filters = {
        "_source_connection_id": int(connection.id),
        "resource_id": body.resource_id,
        "source_metric_name": body.metric,
        "resolution_seconds": body.resolution_seconds,
        "origin_kind": "NATIVE",
        "bucket_start_utc__gte": start.replace(tzinfo=None),
        "bucket_start_utc__lt": end.replace(tzinfo=None),
    }
    source_rows = _read_silver_slice("resource_metric_series", filters, PROFILE_MAX_POINTS + 1)
    if len(source_rows) > PROFILE_MAX_POINTS:
        raise HTTPException(422, f"A janela excede o limite de {PROFILE_MAX_POINTS} pontos; reduza o período")
    if not source_rows:
        return {"profiles": [], "point_count": 0, "coverage": "NO_DATA"}

    from app.services.infrastructure_metrics.profiles import build_window_profiles
    from app.services.infrastructure_metrics.publication import ensure_metric_profile_table, publish_metric_profile_rows

    normalized = []
    for row in source_rows:
        point_time = row.get("bucket_start_utc")
        if isinstance(point_time, str):
            row["bucket_start_utc"] = datetime.fromisoformat(point_time.replace("Z", "+00:00"))
        row["_source_connection_id"] = int(connection.id)
        row["_silver_owner"] = row.get("_silver_owner") or "oci_infrastructure_metrics"
        normalized.append(row)
    profiles = build_window_profiles(
        normalized, window_start=start, window_end=end,
        resolution_seconds=body.resolution_seconds, value_field=body.value_statistic,
    )
    if not profiles:
        return {"profiles": [], "point_count": len(source_rows), "coverage": "NO_DATA"}

    now = datetime.now(timezone.utc).isoformat()
    persisted_rows = []
    for profile in profiles:
        row = dict(profile)
        row.update({
            "billing_period": None,
            "requested_days": max(1, int((end - start).total_seconds() // 86400)),
            "observed_days": len({item["bucket_start_utc"].strftime("%Y%m%d") for item in normalized}),
            "threshold_durations_json": None,
            "profile_hash_inputs": profile["profile_hash"],
            "confidence": "PARTIAL" if profile["coverage"] < 1 else "OBSERVED",
            "cost_window_amount": None,
            "effective_cost": None,
            "cost_proration_rule": None,
            "billed_cost": None,
            "consumed_quantity": None,
            "pricing_currency": None,
            "_billing_period": None,
            "_processed_at": now,
        })
        persisted_rows.append(row)
    ensure_metric_profile_table()
    snapshot_date = persisted_rows[0]["window_to_day"]
    count = publish_metric_profile_rows(
        persisted_rows, connection_id=int(connection.id), snapshot_date=snapshot_date,
    )
    return {
        "profiles": persisted_rows,
        "point_count": len(source_rows),
        "persisted_rows_in_slice": count,
        "coverage": "COMPLETE" if all(row["coverage"] >= 1 for row in persisted_rows) else "PARTIAL",
        "percentile_basis": "WINDOW_OF_BUCKET_VALUES",
        "value_statistic": body.value_statistic,
        "resolution_seconds": body.resolution_seconds,
    }


@router.get("/perfis")
async def read_metric_profiles(
    connection_id: int = Query(..., gt=0),
    start_utc: datetime = Query(...),
    end_utc: datetime = Query(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if start_utc.tzinfo is None or end_utc.tzinfo is None:
        raise HTTPException(422, "start_utc e end_utc devem incluir fuso horário")
    start_utc, end_utc = start_utc.astimezone(timezone.utc), end_utc.astimezone(timezone.utc)
    if end_utc <= start_utc or end_utc - start_utc > timedelta(days=60):
        raise HTTPException(422, "A janela deve ser positiva e ter no máximo 60 dias")
    connection = await db.get(SourceConnection, connection_id)
    if not connection or connection.connector_type != "oci":
        raise HTTPException(404, "Conexão OCI não encontrada")
    await _authorize_connection(user, db, connection.id, "read")
    filters = {
        "series_source_connection_id": int(connection_id),
        "window_start_utc__gte": start_utc.replace(tzinfo=None),
        "window_start_utc__lt": end_utc.replace(tzinfo=None),
    }
    rows = _read_silver_slice("resource_profile_daily_window", filters, PROFILE_MAX_POINTS)
    return {"profiles": rows, "point_count": len(rows)}


@router.get("/series")
async def read_metric_series(
    connection_id: int,
    start_utc: datetime,
    end_utc: datetime,
    resource_id: str | None = None,
    metric: str | None = None,
    source_metric_name: str | None = None,
    resolution_seconds: Literal[60, 300, 900] | None = None,
    limit: int = Query(5000, ge=1, le=20000),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if start_utc.tzinfo is None or end_utc.tzinfo is None:
        raise HTTPException(422, "A janela deve incluir fuso horário")
    start = start_utc.astimezone(timezone.utc)
    end = end_utc.astimezone(timezone.utc)
    if start >= end or end - start > timedelta(days=60):
        raise HTTPException(422, "A janela deve ser positiva e ter no máximo 60 dias")
    await _authorize_connection(user, db, connection_id, "read")
    connection = await db.get(SourceConnection, connection_id)
    if not connection or connection.connector_type != "oci":
        raise HTTPException(404, "Conexão OCI não encontrada")
    if resource_id or source_metric_name:
        try:
            config = config_from_connection(connection)
            if resource_id and resource_id not in config.resource_ids:
                raise ValueError("recurso fora da allowlist desta conexão")
            if source_metric_name:
                descriptor = get_metric("oci", "oci_computeagent", source_metric_name)
                if descriptor.name not in config.metric_names:
                    raise ValueError("métrica fora da allowlist desta conexão")
        except (ValueError, LookupError) as exc:
            raise HTTPException(422, str(exc)) from exc
    filters = {
        "_source_connection_id": connection_id,
        "bucket_start_utc__gte": start.replace(tzinfo=None),
        "bucket_start_utc__lt": end.replace(tzinfo=None),
    }
    if resource_id:
        filters["resource_id"] = resource_id
    if metric:
        filters["metric"] = metric
    if source_metric_name:
        filters["source_metric_name"] = source_metric_name
    if resolution_seconds:
        filters["resolution_seconds"] = resolution_seconds
    try:
        rows = await asyncio.to_thread(_read_silver_slice, "resource_metric_series", filters, limit)
    except Exception as exc:
        raise HTTPException(503, f"Silver indisponível para consulta ({type(exc).__name__})") from exc
    return {"connection_id": connection_id, "start_utc": start.isoformat(),
            "end_utc": end.isoformat(), "limit": limit, "rows": rows}


@router.get("/coverage")
async def read_metric_coverage(
    connection_id: int,
    start_utc: datetime,
    end_utc: datetime,
    resource_id: str | None = None,
    limit: int = Query(5000, ge=1, le=20000),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if start_utc.tzinfo is None or end_utc.tzinfo is None:
        raise HTTPException(422, "A janela deve incluir fuso horário")
    start = start_utc.astimezone(timezone.utc)
    end = end_utc.astimezone(timezone.utc)
    if start >= end or end - start > timedelta(days=60):
        raise HTTPException(422, "A janela deve ser positiva e ter no máximo 60 dias")
    await _authorize_connection(user, db, connection_id, "read")
    filters = {
        "_source_connection_id": connection_id,
        "window_start_utc__gte": start.replace(tzinfo=None),
        "window_start_utc__lt": end.replace(tzinfo=None),
    }
    if resource_id:
        filters["resource_id"] = resource_id
    try:
        rows = await asyncio.to_thread(_read_silver_slice, "resource_metric_coverage", filters, limit)
    except Exception as exc:
        raise HTTPException(503, f"Silver indisponível para consulta ({type(exc).__name__})") from exc
    return {"connection_id": connection_id, "start_utc": start.isoformat(),
            "end_utc": end.isoformat(), "limit": limit, "rows": rows}


# Rightsizing opportunities share this prefix and the same RBAC module.
from app.routers.infrastructure_opportunities import router as _opportunities_router  # noqa: E402

router.include_router(_opportunities_router)
