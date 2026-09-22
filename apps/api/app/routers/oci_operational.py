"""Read-only OCI operational source controls and dashboard query."""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.source_connection import SourceConnection
from app.services.oci_operational_monitoring import GOLD_TABLE, collect, discover_compartments
from app.services.trino_service import execute as trino_execute
from app.services import lakehouse_layers as layers

router = APIRouter(prefix="/oci-operational", tags=["oci-operational"])
COLLECTION_STATUS: dict[int, dict] = {}
class CollectRequest(BaseModel): connection_id: int; hours: int = Field(72, ge=1, le=2160); persist: bool = True

async def _connection(connection_id: int, db: AsyncSession) -> SourceConnection:
    conn = await db.get(SourceConnection, connection_id)
    if not conn or conn.connector_type != "oci": raise HTTPException(404, "Conexão OCI não encontrada")
    return conn

@router.post("/test")
async def test(body: CollectRequest, db: AsyncSession = Depends(get_db)):
    return collect(await _connection(body.connection_id, db), hours=1, persist=False)


@router.post("/discover-compartments")
async def discover(body: CollectRequest, db: AsyncSession = Depends(get_db)):
    return discover_compartments(await _connection(body.connection_id, db))

@router.post("/collect")
async def collect_now(body: CollectRequest, db: AsyncSession = Depends(get_db)):
    conn = await _connection(body.connection_id, db)
    if COLLECTION_STATUS.get(body.connection_id, {}).get("status") == "running":
        return COLLECTION_STATUS[body.connection_id]
    COLLECTION_STATUS[body.connection_id] = {"connection_id": body.connection_id, "status": "running", "started_at": datetime.now(timezone.utc).isoformat(), "hours": body.hours}

    async def run() -> None:
        try:
            result = await asyncio.to_thread(collect, conn, hours=body.hours, persist=body.persist)
            COLLECTION_STATUS[body.connection_id] = {**COLLECTION_STATUS[body.connection_id], "status": "completed", "finished_at": datetime.now(timezone.utc).isoformat(), "result": {"inventory": len(result.get("inventory", [])), "metrics": len(result.get("metrics", [])), "errors": len(result.get("errors", [])), "error_samples": result.get("errors", [])[:25], "persisted": result.get("persisted", {})}}
        except Exception as exc:
            COLLECTION_STATUS[body.connection_id] = {**COLLECTION_STATUS[body.connection_id], "status": "failed", "finished_at": datetime.now(timezone.utc).isoformat(), "error": f"{type(exc).__name__}: {str(exc)[:500]}"}

    asyncio.create_task(run())
    return COLLECTION_STATUS[body.connection_id]


@router.get("/status/{connection_id}")
async def collection_status(connection_id: int):
    return COLLECTION_STATUS.get(connection_id, {"connection_id": connection_id, "status": "not_started"})

@router.get("/overview")
async def overview(hours: int = 24 * 90):
    try:
        rows = await trino_execute(f"SELECT timestamp, resource_type, resource_id, metric_name, metric_value, model_id, unit_shape, unit_count, lifecycle_state FROM iceberg.{layers.detail_layer()}.{GOLD_TABLE} WHERE from_iso8601_timestamp(timestamp) >= current_timestamp - INTERVAL '{max(1, min(hours, 2160))}' HOUR ORDER BY timestamp")
    except Exception as exc:
        raise HTTPException(503, f"Dados operacionais OCI indisponíveis: {exc}") from exc
    return {"table": GOLD_TABLE, "rows": rows}
