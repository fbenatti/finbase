"""Reads over the generic usage Silver (``compute_usage_metrics``).

Collection is no longer done here: metric sources (e.g. ``oci-monitoring``)
land raw points in Bronze through the regular extraction, and the Silver is
materialized by reprocessing with the data catalog mappings. These routes
only read that Silver, for any cloud.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.rbac import get_effective_permissions, has_permission
from app.database import get_db
from app.models.source_connection import SourceConnection

router = APIRouter(prefix="/custos/metricas-infra", tags=["infrastructure-metrics"])
SERIES_MAX_POINTS = 20_000


async def _authorize_connection(user, db: AsyncSession, connection_id: int, action: str) -> None:
    permissions = await get_effective_permissions(db, user.id)
    if not has_permission(
        permissions, "infrastructure_metrics", action, f"connection:{int(connection_id)}",
    ):
        raise HTTPException(403, "Sem permissão para esta fonte de métricas")


def _window(start_utc: datetime, end_utc: datetime) -> tuple[datetime, datetime]:
    if start_utc.tzinfo is None or end_utc.tzinfo is None:
        raise HTTPException(422, "A janela deve incluir fuso horário")
    start, end = start_utc.astimezone(timezone.utc), end_utc.astimezone(timezone.utc)
    if start >= end or end - start > timedelta(days=60):
        raise HTTPException(422, "A janela deve ser positiva e ter no máximo 60 dias")
    return start, end


def _read_resources(connection_id: int) -> list[dict]:
    from app.services import lakehouse_layers as layers
    from app.services.infrastructure_metrics.opportunity_runner import SILVER_TABLE
    from app.services.trino_service import execute_sync

    return execute_sync(
        f"SELECT resource_id, max(resource_name) AS resource_name, max(cloud_provider) AS cloud_provider, "
        f"max(resource_type) AS resource_type, array_join(array_agg(DISTINCT metric), ',') AS metrics, "
        f"array_join(array_agg(DISTINCT CAST(CAST(resolution_seconds AS INTEGER) AS VARCHAR)), ',') AS resolutions, "
        f"min(bucket_start_utc) AS first_bucket, max(bucket_start_utc) AS last_bucket, count(*) AS points "
        f"FROM {layers.detail_layer()}.{SILVER_TABLE} WHERE source_connection_id = {int(connection_id)} "
        f"GROUP BY resource_id ORDER BY 2",
        timeout_s=120,
    )


@router.get("/recursos")
async def list_resources(
    connection_id: int = Query(..., gt=0),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Resources, metrics and resolutions already present in the usage Silver."""
    await _authorize_connection(user, db, connection_id, "read")
    if not await db.get(SourceConnection, connection_id):
        raise HTTPException(404, "Fonte de métricas não encontrada")
    try:
        rows = await asyncio.to_thread(_read_resources, connection_id)
    except Exception as exc:
        raise HTTPException(503, f"Silver indisponível para consulta ({type(exc).__name__})") from exc
    return {"items": [{
        **row,
        "metrics": sorted(filter(None, str(row.get("metrics") or "").split(","))),
        "resolutions": sorted(int(value) for value in str(row.get("resolutions") or "").split(",") if value),
    } for row in rows]}


@router.get("/series")
async def read_series(
    connection_id: int = Query(..., gt=0),
    resource_id: str = Query(..., min_length=1),
    metric: str = Query(..., min_length=1),
    start_utc: datetime = Query(...),
    end_utc: datetime = Query(...),
    resolution_seconds: int = Query(300),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """One canonical series with all its statistics per bucket, plus the window profile."""
    from app.services.infrastructure_metrics.opportunities import summarize_series
    from app.services.infrastructure_metrics.opportunity_runner import read_series as read_silver_series

    if resolution_seconds not in (60, 300, 900, 3600):
        raise HTTPException(422, "resolution_seconds deve ser 60, 300, 900 ou 3600")
    start, end = _window(start_utc, end_utc)
    await _authorize_connection(user, db, connection_id, "read")
    if int((end - start).total_seconds() // resolution_seconds) > SERIES_MAX_POINTS:
        raise HTTPException(422, f"A janela excede {SERIES_MAX_POINTS} buckets nesta resolução")
    try:
        rows = await asyncio.to_thread(
            read_silver_series, connection_id, resource_id, metric, start, end, resolution_seconds,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, f"Silver indisponível para consulta ({type(exc).__name__})") from exc
    profile = summarize_series(rows, window_start=start, window_end=end, resolution_seconds=resolution_seconds)
    return {
        "connection_id": connection_id, "resource_id": resource_id, "metric": metric,
        "resolution_seconds": resolution_seconds,
        "start_utc": start.isoformat(), "end_utc": end.isoformat(),
        "points": [{
            "t": row["bucket_start_utc"], "mean": row.get("value_average"), "max": row.get("value_maximum"),
            "p95": row.get("native_p95"), "p99": row.get("native_p99"),
        } for row in rows],
        "profile": {key: value for key, value in profile.items() if key != "daily"},
        "daily": profile["daily"],
    }


# Rightsizing opportunities share this prefix and the same RBAC module.
from app.routers.infrastructure_opportunities import router as _opportunities_router  # noqa: E402

from app.routers.infrastructure_rightsizing_policy import router as _policy_router  # noqa: E402

router.include_router(_opportunities_router)
router.include_router(_policy_router)
