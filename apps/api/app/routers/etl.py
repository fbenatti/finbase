import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Literal, Any

logger = logging.getLogger(__name__)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text, delete as sa_delete, or_, String, cast, func
import json
from app.database import get_db
from app.models.source_connection import SourceConnection
from app.models.ingested_blob import IngestedBlob
from app.models.skipped_blob import SkippedBlob
from app.schemas.schemas import (
    BlobScanResult, IngestedBlobOut, EtlRunRequest,
    SkippedBlobOut, SkipBlobRequest, ReprocessRequest,
    ScheduledTaskCreate, ScheduledTaskUpdate, ScheduledTaskOut, SourceConnectionCreate,
)
from app.services.azure_extractor import AzureExtractor
from app.services.task_runner import get_task_status as _get_task_status, cancel_task, get_running_tasks
import httpx
from httpx import DigestAuth
import asyncio
import os
import re
import pandas as pd
from uuid import uuid4
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from croniter import croniter

router = APIRouter(prefix="/etl", tags=["etl"])


async def _reconcile_dead_worker_tasks(db: AsyncSession, reason: str) -> int:
    """Close persisted tasks when the worker is definitely no longer alive.

    The API cannot resume a Python worker stack safely. Leaving these rows in
    QUEUED/RUNNING makes the UI show a permanent lock and blocks a new run.
    This only changes control metadata; it never touches Bronze/Silver/Gold.
    """
    from app.models.etl_task import EtlTask
    from sqlalchemy import func as sa_func
    from app.services.task_runner import _inmemory_tasks

    result = await db.execute(
        select(EtlTask).where(EtlTask.status.in_(["QUEUED", "RUNNING"]))
    )
    rows = result.scalars().all()
    if not rows:
        return 0
    for row in rows:
        row.status = "FAILED"
        row.phase = reason[:200]
        row.current_blob = reason[:500]
        row.finished_at = sa_func.now()
        existing = row.result_json if isinstance(row.result_json, dict) else {}
        row.result_json = {**existing, "error": reason}
        _inmemory_tasks.pop(row.id, None)
    await db.commit()
    return len(rows)


@router.get("/worker-health")
async def worker_health(db: AsyncSession = Depends(get_db)):
    """Report worker liveness without mutating task state.

    A health-page refresh must never fail queued/running work. Orphan cleanup
    belongs to worker startup, where process ownership is known.
    """
    heartbeat = os.getenv("OPENFINOPS_WORKER_HEARTBEAT", "/tmp/openfinops-worker.heartbeat")
    pid_file = os.getenv("OPENFINOPS_WORKER_PID", "/tmp/openfinops-worker.pid")
    try:
        pid = int(open(pid_file).read().strip())
        os.kill(pid, 0)
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return {"status": "offline", "heartbeat_age_seconds": None, "worker_pid": None, "orphaned_tasks_cleared": 0}
    try:
        age = max(0, __import__("time").time() - os.path.getmtime(heartbeat))
        status = "ok" if age <= 15 else "stale"
        return {"status": status, "heartbeat_age_seconds": round(age, 1), "worker_pid": pid, "orphaned_tasks_cleared": 0}
    except FileNotFoundError:
        return {"status": "stale", "heartbeat_age_seconds": None, "worker_pid": pid, "orphaned_tasks_cleared": 0}


@router.post("/connections/{connection_id}/scan")
async def scan_blobs(connection_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SourceConnection).where(SourceConnection.id == connection_id)
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    if conn.connector_type == "azure_automation_jobs":
        # Azure Automation: o "scan" descobre as Automation Accounts nas
        # subscriptions configuradas. A coleta de jobs/schedules acontece no
        # worker quando a extração é executada (/etl/run).
        try:
            from app.services.azure_automation_service import discover_accounts
            accounts = await discover_accounts(dict(conn.config_json or {}))
            return {
                "connection_id": connection_id,
                "total": len(accounts),
                "new": 0,
                "changed": 0,
                "unchanged": 0,
                "items": [
                    {
                        "blob_path": f"automation/{a['account_name'] or '?'}",
                        "billing_period": None,
                        "last_modified": None,
                        "etag": "",
                        "content_length": 0,
                        "status": "NEW",
                    }
                    for a in accounts
                ],
                "message": (
                    f"Azure Automation: {len(accounts)} conta(s) encontrada(s) nas subscriptions. "
                    "Execute a extração para coletar jobs e schedules."
                ),
            }
        except Exception as exc:
            return {
                "connection_id": connection_id,
                "total": 0,
                "new": 0,
                "changed": 0,
                "unchanged": 0,
                "items": [],
                "message": f"Falha ao descobrir Automation Accounts: {str(exc)[:400]}",
            }
    if conn.connector_type == "azure_resource_graph":
        # Azure Resource Graph: o "scan" valida a credencial e lista as
        # subscriptions acessíveis com a contagem de recursos. A coleta de
        # inventário/órfãos acontece no worker quando a extração é executada.
        try:
            from app.services.azure_resource_graph_service import discover_scope
            subs = await discover_scope(dict(conn.config_json or {}))
            return {
                "connection_id": connection_id,
                "total": len(subs),
                "new": 0,
                "changed": 0,
                "unchanged": 0,
                "items": [
                    {
                        "blob_path": f"arg/subscription/{s['subscription_id']}",
                        "billing_period": None,
                        "last_modified": None,
                        "etag": "",
                        "content_length": 0,
                        "status": "NEW",
                        "subscription_id": s["subscription_id"],
                        "subscription_name": s["subscription_name"],
                        "resource_count": s["resource_count"],
                    }
                    for s in subs
                ],
                "message": (
                    f"Azure Resource Graph: {len(subs)} subscription(s) acessíveis "
                    f"({sum(s['resource_count'] for s in subs)} recursos). "
                    "Execute a extração para coletar inventário e recursos órfãos."
                ),
            }
        except Exception as exc:
            return {
                "connection_id": connection_id,
                "total": 0,
                "new": 0,
                "changed": 0,
                "unchanged": 0,
                "items": [],
                "message": f"Falha ao consultar Azure Resource Graph: {str(exc)[:400]}",
            }
    if conn.connector_type == "azure_blob":
        extractor = AzureExtractor(conn)
        return await extractor.scan(db)
    if conn.connector_type == "gcp_bigquery":
        from app.services.gcp_bigquery_extractor import GcpBigQueryExtractor
        extractor = GcpBigQueryExtractor(conn)
        return await extractor.scan(db)
    if conn.connector_type == "oci":
        from app.services.oci_extractor import OciExtractor
        extractor = OciExtractor(conn)
        return await extractor.scan(db)
    if conn.connector_type == "mongodb_atlas":
        from app.services.mongodb_extractor import MongoAtlasExtractor
        extractor = MongoAtlasExtractor(conn)
        return await extractor.scan(db)
    if conn.connector_type == "github_billing":
        from app.services.github_extractor import GithubBillingExtractor
        extractor = GithubBillingExtractor(conn)
        return await extractor.scan(db)
    if conn.connector_type == "github_license":
        from app.services.github_license_extractor import GithubLicenseExtractor
        extractor = GithubLicenseExtractor(conn)
        return await extractor.scan(db)
    if conn.connector_type == "github_token":
        from app.services.github_token_extractor import GithubTokenExtractor
        extractor = GithubTokenExtractor(conn)
        return await extractor.scan(db)
    if conn.connector_type == "notebooklm_license":
        from app.services.notebooklm_license_extractor import NotebookLMLicenseExtractor
        extractor = NotebookLMLicenseExtractor(conn)
        return await extractor.scan(db)
    if conn.connector_type == "notebooklm_token":
        from app.services.notebooklm_token_extractor import NotebookLMTokenExtractor
        extractor = NotebookLMTokenExtractor(conn)
        return await extractor.scan(db)
    if conn.connector_type == "claude_license":
        from app.services.claude_license_extractor import ClaudeLicenseExtractor
        extractor = ClaudeLicenseExtractor(conn)
        return await extractor.scan(db)
    if conn.connector_type == "claude_token":
        from app.services.claude_token_extractor import ClaudeTokenExtractor
        extractor = ClaudeTokenExtractor(conn)
        return await extractor.scan(db)
    if conn.connector_type == "gemini_license":
        from app.services.gemini_license_extractor import GeminiLicenseExtractor
        extractor = GeminiLicenseExtractor(conn)
        return await extractor.scan(db)
    if conn.connector_type == "gemini_token":
        from app.services.gemini_token_extractor import GeminiTokenExtractor
        extractor = GeminiTokenExtractor(conn)
        return await extractor.scan(db)
    if conn.connector_type == "gemini_notebooklm":
        from app.services.gemini_notebooklm_extractor import GeminiNotebookLMExtractor
        extractor = GeminiNotebookLMExtractor(conn)
        return await extractor.scan(db)
    if conn.connector_type == "databricks":
        from app.services.databricks_extractor import DatabricksExtractor
        extractor = DatabricksExtractor(conn)
        return await extractor.scan(db)
    if conn.connector_type == "azure_openai_ptu":
        from app.services.azure_ptu_service import discover, resolve_config
        inventory = await discover(resolve_config(conn.config_json))
        items = []
        for dep in inventory.get("deployments") or []:
            items.append({
                "path": dep.get("id") or dep.get("deployment_name"),
                "size": int(dep.get("ptus") or 0),
                "last_modified": None,
                "status": "ptu_deployment",
                "account_name": dep.get("account_name"),
                "deployment_name": dep.get("deployment_name"),
                "region": dep.get("region"),
                "ptus": dep.get("ptus"),
                "model_name": dep.get("model_name"),
            })
        return {
            "connection_id": connection_id,
            "total": len(items),
            "new": len(items),
            "changed": 0,
            "unchanged": 0,
            "items": items,
            "accounts": inventory.get("account_count"),
            "ptu_deployments": inventory.get("ptu_deployment_count"),
            "total_ptus": inventory.get("total_ptus"),
            "persisted_metrics": False,
        }
    raise HTTPException(status_code=400, detail=f"Scan not supported for {conn.connector_type}")


@router.get("/connections/{connection_id}/preview")
async def preview_source(
    connection_id: int,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Return the raw header (columns) plus a sample of rows from a connector.

    Currently implemented for Databricks: lets the user see the source schema
    before deciding on normalization/mapping (Silver/Gold).
    """
    result = await db.execute(
        select(SourceConnection).where(SourceConnection.id == connection_id)
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    if conn.connector_type != "databricks":
        raise HTTPException(status_code=400, detail=f"Preview not supported for {conn.connector_type}")
    from app.services.databricks_extractor import DatabricksExtractor
    try:
        return DatabricksExtractor(conn).preview(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Databricks preview failed for connection %s: %s", connection_id, exc)
        raise HTTPException(status_code=502, detail=f"Falha ao consultar o Databricks: {str(exc)[:300]}") from exc


@router.get("/blobs/", response_model=list[IngestedBlobOut])
async def list_ingested_blobs(
    connection_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(IngestedBlob).order_by(IngestedBlob.ingested_at.desc())
    if connection_id:
        stmt = stmt.where(IngestedBlob.connection_id == connection_id)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.delete("/blobs/skipped", status_code=204)
async def clear_skipped_blobs(
    connection_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Clear all ACK/ignored markers for one connection."""
    result = await db.execute(
        select(SkippedBlob).where(SkippedBlob.connection_id == connection_id)
    )
    for skipped in result.scalars().all():
        await db.delete(skipped)
    await db.commit()


async def _destination_names_for_connection(db: AsyncSession, connection_id: int) -> list[str]:
    from app.models.destination import DestinationTable, SourceDestinationMapping

    result = await db.execute(
        select(DestinationTable.physical_name)
        .join(
            SourceDestinationMapping,
            SourceDestinationMapping.destination_table_id == DestinationTable.id,
        )
        .where(SourceDestinationMapping.source_connection_id == connection_id)
        .distinct()
    )
    return [name for name in result.scalars().all() if name]


def _drop_manual_period_meta(config: dict | None, period: str) -> dict:
    next_config = dict(config or {})
    meta = dict(next_config.get("manual_csv_metadata") or {})
    if period in meta:
        meta.pop(period, None)
        next_config["manual_csv_metadata"] = meta
    return next_config


async def _purge_gold_if_period_empty(
    db: AsyncSession,
    conn: SourceConnection,
    billing_period: str | None,
) -> None:
    """When a month has no remaining ingested blobs, drop that Gold slice too."""
    if not billing_period:
        return
    remaining = await db.execute(
        select(IngestedBlob.id).where(
            IngestedBlob.connection_id == conn.id,
            IngestedBlob.billing_period == billing_period,
        ).limit(1)
    )
    if remaining.first():
        return

    from app.services.iceberg_service import delete_destination_slice

    names = await _destination_names_for_connection(db, conn.id)
    loop = asyncio.get_running_loop()
    for name in names:
        await loop.run_in_executor(
            None,
            lambda physical=name: delete_destination_slice(
                physical,
                billing_period=billing_period,
                connection_id=conn.id,
            ),
        )
    if conn.connector_type == "manual_csv":
        conn.config_json = _drop_manual_period_meta(conn.config_json, billing_period)


@router.delete("/blobs/{blob_id}", status_code=204)
async def delete_ingested_blob(blob_id: int, db: AsyncSession = Depends(get_db)):
    """Remove ingestion metadata and physical Bronze/Gold rows from Iceberg."""
    from app.services.iceberg_service import delete_bronze_slice

    result = await db.execute(
        select(IngestedBlob, SourceConnection)
        .join(SourceConnection, SourceConnection.id == IngestedBlob.connection_id)
        .where(IngestedBlob.id == blob_id)
    )
    row = result.first()
    if not row:
        return

    blob, conn = row
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: delete_bronze_slice(
            conn.code,
            blob_path=blob.blob_path,
            billing_period=blob.billing_period,
        )
    )

    await db.execute(text("DELETE FROM ingested_blob WHERE id = :blob_id"), {"blob_id": blob_id})
    await db.flush()
    await _purge_gold_if_period_empty(db, conn, blob.billing_period)
    await db.commit()


@router.get("/blobs/{blob_id}/download")
async def download_ingested_blob(
    blob_id: int,
    format: Literal["csv", "xlsx"] = Query("csv"),
    db: AsyncSession = Depends(get_db),
):
    """Download raw Bronze layer data for an ingested blob as CSV or XLSX."""
    from fastapi.responses import StreamingResponse
    from app.services.iceberg_service import read_bronze
    import io

    result = await db.execute(
        select(IngestedBlob, SourceConnection.code, SourceConnection.name)
        .join(SourceConnection, SourceConnection.id == IngestedBlob.connection_id)
        .where(IngestedBlob.id == blob_id)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Ingested blob not found")

    blob, conn_code, conn_name = row

    try:
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(
            None,
            lambda: read_bronze(
                conn_code,
                blob_path=blob.blob_path,
                billing_period=blob.billing_period,
            )
        )
    except Exception as e:
        logger.exception("Failed to read bronze data for blob %d: %s", blob_id, e)
        raise HTTPException(status_code=500, detail=f"Erro ao ler camada Bronze: {e}")

    if df.empty:
        raise HTTPException(status_code=404, detail="Nenhum dado encontrado na camada Bronze para este blob")

    safe_code = re.sub(r'[^a-zA-Z0-9_\-]', '_', conn_code or 'origem')
    period = blob.billing_period or 'period'
    filename = f"origem_{safe_code}_{period}.{format}"

    if format == "xlsx":
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Bronze_Origem")
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    else:
        csv_bytes = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        return StreamingResponse(
            io.BytesIO(csv_bytes),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )


@router.delete("/blobs/", status_code=204)
async def delete_all_ingested_blobs(
    connection_id: int = Query(..., description="Connection ID to clear all data for"),
    billing_period: str | None = Query(None, description="Optional: only clear a specific period"),
    db: AsyncSession = Depends(get_db),
):
    """Remove ingestion metadata and Bronze rows (optionally filtered by period)."""
    from app.services.iceberg_service import delete_bronze_slice, reset_bronze

    conn_res = await db.execute(select(SourceConnection).where(SourceConnection.id == connection_id))
    conn = conn_res.scalar_one_or_none()

    if conn:
        loop = asyncio.get_running_loop()
        if billing_period:
            await loop.run_in_executor(
                None,
                lambda: delete_bronze_slice(conn.code, billing_period=billing_period)
            )
        else:
            await loop.run_in_executor(
                None,
                lambda: reset_bronze(conn.code)
            )

    params: dict = {"connection_id": connection_id}
    sql = "DELETE FROM ingested_blob WHERE connection_id = :connection_id"
    if billing_period:
        sql += " AND billing_period = :billing_period"
        params["billing_period"] = billing_period
    await db.execute(text(sql), params)

    if not billing_period:
        await db.execute(text("DELETE FROM skipped_blob WHERE connection_id = :connection_id"), params)
        await db.execute(text("DELETE FROM etl_task WHERE connection_id = :connection_id"), params)

    await db.flush()
    if conn and billing_period:
        await _purge_gold_if_period_empty(db, conn, billing_period)
    await db.commit()


@router.post("/run")
async def run_etl(request: EtlRunRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SourceConnection).where(SourceConnection.id == request.connection_id)
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    if conn.connector_type == "azure_openai_ptu":
        # PTU is an operational source, not an Azure Blob export.  It is
        # executed by the worker through the PTU collector below.
        pass
    elif conn.connector_type not in ("azure_blob", "gcp_bigquery", "oci", "mongodb_atlas", "github_billing", "github_license", "github_token", "notebooklm_license", "notebooklm_token", "claude_license", "claude_token", "gemini_license", "gemini_token", "gemini_notebooklm", "databricks", "azure_automation_jobs", "azure_resource_graph"):
        return {
            "task_id": 0,
            "status": "NOT_IMPLEMENTED",
            "message": f"ETL para {conn.connector_type} ainda nao disponivel."
        }


    from app.models.etl_task import EtlTask
    from sqlalchemy import insert

    cfg = dict(conn.config_json or {})
    billing_periods = request.force_periods or []
    result_json: dict | None = None
    phase: str | None = None
    if conn.connector_type == "azure_openai_ptu":
        if billing_periods:
            result_json = {"periods": billing_periods, "user_triggered": True}
            phase = f"Na fila: buscar métricas Azure PTU · meses {', '.join(billing_periods)}"
        elif request.backfill:
            days = int(request.backfill_days or cfg.get("backfill_days") or 93)
            result_json = {"backfill": True, "backfill_days": days, "user_triggered": True}
            phase = f"Na fila: backfill Azure PTU ({days} dias, janelas de 30 dias)"
        elif request.lookback_hours is not None:
            lookback = int(request.lookback_hours)
            result_json = {"lookback_hours": lookback, "user_triggered": True}
            phase = f"Na fila: coleta incremental Azure PTU ({lookback}h)"
        else:
            days = int(request.backfill_days or cfg.get("backfill_days") or 93)
            result_json = {"backfill": True, "backfill_days": days, "user_triggered": True}
            phase = f"Na fila: importar histórico Azure PTU ({days} dias, retenção Monitor)"

    new_id = await db.execute(
        insert(EtlTask).values(
            task_type="extraction",
            status="QUEUED",
            connection_id=request.connection_id,
            destination_table_id=None,
            connection_ids=[request.connection_id],
            billing_periods=billing_periods,
            selected_blobs=request.selected_blobs or [],
            total_blobs=0,
            processed_blobs=0,
            rows_raw=0,
            rows_normalized=0,
            rows_destination=0,
            errors=[],
            phase=phase,
            result_json=result_json,
        ).returning(EtlTask.id)
    )
    task_id = new_id.scalar_one()
    await db.commit()

    # Register in in-memory dict so the frontend can see it immediately
    from app.services.task_runner import _inmemory_tasks
    _inmemory_tasks[task_id] = {
        "id": task_id,
        "connection_id": request.connection_id,
        "status": "QUEUED",
        "task_type": "extraction",
        "total_blobs": 0,
        "processed_blobs": 0,
        "rows_raw": 0,
        "rows_normalized": 0,
        "rows_destination": 0,
        "current_blob": None,
        "current_blob_size": 0,
        "download_bytes": 0,
        "downloaded_bytes_total": 0,
        "total_download_bytes": 0,
        "active_downloads": 0,
        "errors": [],
    }

    return {"task_id": task_id, "status": "QUEUED", "message": "Extraction queued — worker will pick it up"}


@router.post("/cancel/{task_id}")
async def cancel_etl(task_id: int):
    if cancel_task(task_id):
        return {"status": "CANCELLING"}
    raise HTTPException(status_code=404, detail="Task not found or already finished")


@router.post("/connections/{connection_id}/reset-source")
async def reset_source_for_reimport(connection_id: int, db: AsyncSession = Depends(get_db)):
    """Clear source metadata and Bronze so current CSVs can be reimported cleanly."""
    from app.services import iceberg_service as iceberg
    from app.models.etl_task import EtlTask
    conn = await db.get(SourceConnection, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    active = await db.execute(select(EtlTask.id).where(EtlTask.connection_id == connection_id, EtlTask.status.in_(["QUEUED", "RUNNING"])))
    if active.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Existe uma tarefa ativa para esta fonte")
    await db.execute(sa_delete(IngestedBlob).where(IngestedBlob.connection_id == connection_id))
    await db.commit()
    bronze_reset = iceberg.reset_bronze(conn.code)
    return {"status": "READY", "connection_id": connection_id, "bronze_reset": bronze_reset, "message": "Metadados e Bronze removidos. Execute a importação dos CSVs atuais."}


@router.get("/status/{task_id}")
async def get_etl_status(task_id: int):
    status = _get_task_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="Task not found")
    return status


@router.get("/tasks/")
async def list_running_tasks():
    """List all currently running (non-terminal) ETL tasks."""
    return get_running_tasks()


# --- Skipped Blobs ---

@router.get("/blobs/skipped", response_model=list[SkippedBlobOut])
async def list_skipped_blobs(
    connection_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """List all blobs marked as skipped for a connection."""
    result = await db.execute(
        select(SkippedBlob)
        .where(SkippedBlob.connection_id == connection_id)
        .order_by(SkippedBlob.skipped_at.desc())
    )
    return result.scalars().all()


@router.post("/blobs/skip", response_model=SkippedBlobOut, status_code=201)
async def skip_blob(
    body: SkipBlobRequest,
    db: AsyncSession = Depends(get_db),
):
    """Mark a blob as skipped so it is never imported."""
    # Check if already skipped
    result = await db.execute(
        select(SkippedBlob).where(
            SkippedBlob.connection_id == body.connection_id,
            SkippedBlob.blob_path == body.blob_path,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    skipped = SkippedBlob(
        connection_id=body.connection_id,
        blob_path=body.blob_path,
        reason=body.reason,
    )
    db.add(skipped)
    await db.commit()
    await db.refresh(skipped)
    return skipped


@router.delete("/blobs/skip/{skip_id}", status_code=204)
async def unskip_blob(skip_id: int, db: AsyncSession = Depends(get_db)):
    """Remove a blob from the skip list so it can be imported again."""
    result = await db.execute(
        select(SkippedBlob).where(SkippedBlob.id == skip_id)
    )
    skipped = result.scalar_one_or_none()
    if not skipped:
        raise HTTPException(status_code=404, detail="Skipped blob not found")
    await db.delete(skipped)
    await db.commit()


async def _execute_connection_test(conn: SourceConnection) -> dict[str, str]:
    config = conn.config_json

    if conn.connector_type == "azure_openai_ptu":
        # PTU has its own ARM/Monitor test, but Connections uses this common
        # endpoint for the row action and the create wizard.
        try:
            from app.services.azure_ptu_service import test_connection_checks
            from app.config import settings
            cfg = dict(config or {})
            if cfg.get("credential_profile") == "azure-4p":
                cfg.setdefault("tenant_id", settings.azure_4p_tenant_id)
                cfg.setdefault("client_id", settings.azure_4p_client_id)
                cfg.setdefault("client_secret", settings.azure_4p_client_secret)
            result = await test_connection_checks(cfg)
            summary = result.get("summary") or {}
            result["message"] = (
                f"Azure PTU validado: {summary.get('ptu_deployments', 0)} deployments / {summary.get('total_ptus', 0)} PTUs"
            )
            return result
        except Exception as e:
            return {"status": "FAILED", "message": f"Azure PTU: {str(e)[:450]}"}

    if conn.connector_type == "azure_automation_jobs":
        try:
            from app.services.azure_automation_service import discover_accounts
            accounts = await discover_accounts(config)
            return {
                "status": "OK" if accounts else "PENDING",
                "message": (
                    f"Azure Automation validado: {len(accounts)} conta(s) nas subscriptions"
                    if accounts
                    else "Azure Automation conectado, mas nenhuma Automation Account encontrada nas subscriptions."
                ),
            }
        except Exception as e:
            return {"status": "FAILED", "message": f"Azure Automation: {str(e)[:450]}"}

    if conn.connector_type == "azure_resource_graph":
        try:
            from app.services.azure_resource_graph_service import discover_scope
            subs = await discover_scope(config)
            total_resources = sum(s.get("resource_count") or 0 for s in subs)
            return {
                "status": "OK" if subs else "PENDING",
                "message": (
                    f"Azure Resource Graph validado: {len(subs)} subscription(s) / {total_resources} recursos"
                    if subs
                    else "Azure Resource Graph conectado, mas nenhuma subscription acessível com a credencial."
                ),
            }
        except Exception as e:
            return {"status": "FAILED", "message": f"Azure Resource Graph: {str(e)[:450]}"}

    if conn.connector_type == "azure_blob":
        try:
            extractor = AzureExtractor(conn)
            client = extractor._get_blob_service_client()
            container = config.get("container_name", "")
            cc = client.get_container_client(container)
            next(iter(cc.list_blobs()))
            return {
                "status": "OK",
                "message": f"Connected to {config.get('storage_account','?')}/{container}",
            }
        except Exception as e:
            return {"status": "FAILED", "message": str(e)[:500]}

    if conn.connector_type == "gcp_bigquery":
        try:
            from app.services.gcp_bigquery_extractor import GcpBigQueryExtractor
            return GcpBigQueryExtractor(conn).test_connection()
        except Exception as e:
            return {"status": "FAILED", "message": str(e)[:500]}

    if conn.connector_type == "mongodb_atlas":
        public_key = config.get("public_key", "")
        private_key = config.get("private_key", "")
        base_url = config.get("base_url", "https://cloud.mongodb.com/api/atlas/v1.0")

        if not public_key or not private_key:
            return {
                "status": "PENDING",
                "message": f"MongoDB Atlas ({config.get('contract_type','?')}) — chaves de API nao configuradas. Adicione public_key e private_key na conexao para testar.",
            }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    base_url.rstrip("/"),
                    auth=DigestAuth(username=public_key, password=private_key),
                )
                if response.status_code == 200:
                    return {
                        "status": "OK",
                        "message": f"MongoDB Atlas ({config.get('contract_type','?')}) — API respondendo OK",
                    }
                detail = ""
                try:
                    detail = response.json().get("detail", "")
                except Exception:
                    pass
                return {
                    "status": "FAILED",
                    "message": f"MongoDB Atlas retornou HTTP {response.status_code}: {detail}"[:500],
                }
        except Exception as e:
            return {"status": "FAILED", "message": str(e)[:500]}

    if conn.connector_type == "oci":
        try:
            from app.services.oci_extractor import OciExtractor
            return OciExtractor(conn).test_connection()
        except Exception as e:
            return {"status": "FAILED", "message": str(e)[:500]}

    if conn.connector_type == "github_billing":
        try:
            from app.services.github_extractor import GithubBillingExtractor
            return GithubBillingExtractor(conn).test_connection()
        except Exception as e:
            return {"status": "FAILED", "message": str(e)[:500]}

    if conn.connector_type == "github_license":
        try:
            from app.services.github_license_extractor import GithubLicenseExtractor
            return GithubLicenseExtractor(conn).test_connection()
        except Exception as e:
            return {"status": "FAILED", "message": str(e)[:500]}

    if conn.connector_type == "github_token":
        try:
            from app.services.github_token_extractor import GithubTokenExtractor
            return GithubTokenExtractor(conn).test_connection()
        except Exception as e:
            return {"status": "FAILED", "message": str(e)[:500]}

    if conn.connector_type == "notebooklm_license":
        try:
            from app.services.notebooklm_license_extractor import NotebookLMLicenseExtractor
            return NotebookLMLicenseExtractor(conn).test_connection()
        except Exception as e:
            return {"status": "FAILED", "message": str(e)[:500]}

    if conn.connector_type == "notebooklm_token":
        try:
            from app.services.notebooklm_token_extractor import NotebookLMTokenExtractor
            return NotebookLMTokenExtractor(conn).test_connection()
        except Exception as e:
            return {"status": "FAILED", "message": str(e)[:500]}

    if conn.connector_type == "claude_license":
        try:
            from app.services.claude_license_extractor import ClaudeLicenseExtractor
            return ClaudeLicenseExtractor(conn).test_connection()
        except Exception as e:
            return {"status": "FAILED", "message": str(e)[:500]}

    if conn.connector_type == "claude_token":
        try:
            from app.services.claude_token_extractor import ClaudeTokenExtractor
            return ClaudeTokenExtractor(conn).test_connection()
        except Exception as e:
            return {"status": "FAILED", "message": str(e)[:500]}

    if conn.connector_type == "gemini_license":
        try:
            from app.services.gemini_license_extractor import GeminiLicenseExtractor
            return GeminiLicenseExtractor(conn).test_connection()
        except Exception as e:
            return {"status": "FAILED", "message": str(e)[:500]}

    if conn.connector_type == "gemini_token":
        try:
            from app.services.gemini_token_extractor import GeminiTokenExtractor
            return GeminiTokenExtractor(conn).test_connection()
        except Exception as e:
            return {"status": "FAILED", "message": str(e)[:500]}

    if conn.connector_type == "gemini_notebooklm":
        try:
            from app.services.gemini_notebooklm_extractor import GeminiNotebookLMExtractor
            return GeminiNotebookLMExtractor(conn).test_connection()
        except Exception as e:
            return {"status": "FAILED", "message": str(e)[:500]}

    if conn.connector_type == "databricks":
        try:
            from app.services.databricks_extractor import DatabricksExtractor
            return DatabricksExtractor(conn).test_connection()
        except Exception as e:
            return {"status": "FAILED", "message": str(e)[:500]}

    return {
        "status": "NOT_IMPLEMENTED",
        "message": f"Test not implemented for {conn.connector_type}",
    }


@router.post("/connections/test-config")
async def test_connection_config(data: SourceConnectionCreate):
    """Test a connection from form data without persisting it."""
    conn = SourceConnection(**data.model_dump())
    return await _execute_connection_test(conn)


@router.post("/connections/{connection_id}/test")
async def test_connection(connection_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SourceConnection).where(SourceConnection.id == connection_id)
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    test_result = await _execute_connection_test(conn)
    conn.last_test_status = str(test_result.get("status", "FAILED"))
    conn.last_test_message = str(test_result.get("message", ""))[:500]
    conn.last_tested_at = datetime.now(timezone.utc)
    await db.commit()
    return {
        "status": conn.last_test_status,
        "message": conn.last_test_message,
    }

# ═══════════════════════════════════════════════════════════════════════
# Destination table reprocessing (standalone, async with progress)
# ═══════════════════════════════════════════════════════════════════════

_dest_reprocess_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dest-reprocess")
_dest_task_counter = 0
# NOTE: reprocess tasks are registered into task_runner._inmemory_tasks for global visibility


def _azure_blob_run_id(blob_path: str) -> str:
    """Group Azure Cost Management part files under the same export execution."""
    parts = str(blob_path or "").split("/")
    return parts[3] if len(parts) > 3 else str(blob_path)


def _select_reprocess_blobs(
    candidates: list[tuple[int, str, str, object]],
    *,
    connector_type: str,
    preferred_table: str | None = None,
) -> list[tuple[int, str, str]]:
    """Choose Bronze blobs to reprocess for one connection.

    Azure keeps only the latest export execution per billing month because
    stale executions duplicate the same financial rows.  GCP BigQuery keeps
    distinct export tables (current vs legacy) as separate blob paths; the
    monthly dedup must not collapse them into the most recently ingested
    table, or Gold loses the detailed resource export.
    """
    if connector_type == "gcp_bigquery":
        by_period: dict[str, list[tuple[int, str, str, object]]] = {}
        for blob_id, period, path, modified in candidates:
            by_period.setdefault(str(period), []).append((blob_id, period, path, modified))
        selected: list[tuple[int, str, str]] = []
        for period, blobs in by_period.items():
            # GCP keeps distinct export tables (current resource_v1 vs legacy v1)
            # as separate blob paths per billing month.  The legacy v1 table
            # holds THIRD_PARTY_RESELLER subscriptions (private marketplace,
            # e.g. Denodo) that the resource export does not contain, so all
            # distinct tables for the period must be reprocessed — never only
            # the preferred table or Gold loses the marketplace rows.
            if preferred_table:
                preferred = [b for b in blobs if preferred_table in str(b[2])]
                rest = [b for b in blobs if preferred_table not in str(b[2])]
                picked: list[tuple[int, str, str, object]] = []
                if preferred:
                    picked.append(max(preferred, key=lambda b: b[3]))
                for other in rest:
                    picked.append(other)
                selected.extend((blob_id, period, path) for blob_id, period, path, _ in picked)
                continue
            blob_id, period, path, _ = max(blobs, key=lambda b: b[3])
            selected.append((blob_id, period, path))
        return selected

    if connector_type == "azure_blob":
        latest_run: dict[str, tuple[str, object]] = {}
        for _, period, path, modified in candidates:
            run_id = _azure_blob_run_id(path)
            current = latest_run.get(str(period))
            if current is None or modified > current[1]:
                latest_run[str(period)] = (run_id, modified)
        selected = []
        for blob_id, period, path, _ in candidates:
            run_id = _azure_blob_run_id(path)
            if latest_run.get(str(period), (None, None))[0] == run_id:
                selected.append((blob_id, period, path))
        return selected

    return [(blob_id, period, path) for blob_id, period, path, _ in candidates]

def _append_reprocess_log(task_id: int, message: str, *, level: str = "INFO", details: dict | None = None) -> None:
    """Persist compact lifecycle events for the Reprocess history screen.

    Batch progress remains in ``phase``; persisting every batch would bloat
    PostgreSQL for large source files.  This log is intentionally limited to
    meaningful state transitions and capped to the newest 80 entries.
    """
    from datetime import datetime, timezone
    from app.database import SyncSession
    from app.models.etl_task import EtlTask
    try:
        session = SyncSession()
        task = session.get(EtlTask, task_id)
        if task:
            result = dict(task.result_json or {})
            events = list(result.get("event_log") or [])
            events.append({"at": datetime.now(timezone.utc).isoformat(), "level": level, "message": message, "details": details or {}})
            result["event_log"] = events[-80:]
            task.result_json = result
            session.commit()
        session.close()
    except Exception:
        pass



def _write_dest_chunk(
    records: list[dict],
    conn_mappings: list,
    conn,
    dest_col_names: set[str],
    phys_name: str,
    total_written: int,
) -> int:
    """Build a DataFrame from records, map columns, and write to Iceberg gold.{phys_name}."""
    import pandas as pd
    from sqlalchemy import text
    from app.services import iceberg_service as iceberg

    if not records:
        return total_written

    df = pd.DataFrame(records)

    target_data: dict[str, pd.Series] = {}
    for m in conn_mappings:
        if m.source_column in df.columns:
            target_data[m.destination_column] = df[m.source_column].values

    for col in df.columns:
        if col in dest_col_names and col not in target_data and not col.startswith("_"):
            target_data[col] = df[col].values

    if not target_data:
        return total_written

    target = pd.DataFrame(target_data)
    target["cloud_origem"] = conn.code
    target["source_connection_id"] = conn.id if hasattr(conn, 'id') else conn.connection_id

    internal_cols = {"cloud_origem", "source_connection_id"}
    valid_cols = [c for c in target.columns if c in dest_col_names or c in internal_cols]
    target = target[valid_cols]

    if target.empty:
        return total_written

    target = target.where(pd.notna(target), None)

    # Extract billing period from records for partitioning
    bp_set = set()
    for r in records:
        bp = r.get("_billing_period", r.get("billing_period", ""))
        if bp:
            bp_set.add(str(bp))
    billing_period = sorted(bp_set)[0] if bp_set else "unknown"

    target["_billing_period"] = billing_period

    rows = iceberg.write_destination(phys_name, target, partition_col="_billing_period")
    return total_written + rows


def _process_blob_for_reprocess(
    blob_id: int,
    connection_id: int,
    connection_code: str,
    billing_period: str,
    brules: list,       # detached BusinessRule objects
    crules: list,       # detached ClassificationRule objects
    cmapps: list,       # detached SourceDestinationMapping objects
    dest_col_names: set[str],
    dept_cache: dict,
    hierarchy_cache: dict,
    output_cols: set[str],
    task_id: int,
    clone_engine=None,
    source_frame: "pd.DataFrame | None" = None,
    batch_callback=None,
    connection_cloud_name: str | None = None,
) -> "pd.DataFrame | None":
    """Process a single blob in a thread — reads from DB, applies rules/mappings/classifications.

    Each thread gets its own SQLAlchemy SyncSession (session-per-thread pattern)
    because SQLAlchemy Session is not thread-safe.

    Returns the processed DataFrame (with cloud_origem, source_connection_id,
    _billing_period columns) or None if the blob had no data or was cancelled.
    """
    import pandas as pd
    from app.database import SyncSession
    from app.services import iceberg_service as iceberg
    from app.services.task_runner import (
        _apply_business_rule_sync, _apply_classifications_sync,
        _check_cancelled, _inmemory_tasks,
    )
    from app.services.mapping_transforms import apply_mappings

    session = SyncSession()
    try:
        # Reprocess must start from immutable Bronze, never from the removed
        # legacy SQL normalized_cost_line table.
        source = source_frame if source_frame is not None else None
        # The caller identifies the blob by metadata id; resolve its path once.
        from app.models.ingested_blob import IngestedBlob
        meta = session.get(IngestedBlob, blob_id)
        if meta is not None and source_frame is not None:
            if "_blob_path" in source.columns:
                source = source[source["_blob_path"] == meta.blob_path].copy()
            source = source.iloc[:meta.row_count or 0]
        elif meta is not None:
            # IngestedBlob.row_count is the canonical input contract. Bronze
            # may contain historical append duplicates; never let those rows
            # inflate a reprocess beyond the validated CSV count.
            source = iceberg.read_bronze_batches(
                connection_code, blob_path=meta.blob_path,
                billing_period=billing_period,
                max_rows=meta.row_count or 0,
                batch_size=int(os.getenv("REPROCESS_BATCH_ROWS", "100000")),
            )
        elif source is not None:
            source = source[source["_billing_period"].astype(str) == str(billing_period)].copy()

        chunks: list[pd.DataFrame] = []
        rows_in_blob = 0
        source_batches = (source.iloc[start:start + 50000].copy()
                         for start in range(0, len(source), 50000)) if isinstance(source, pd.DataFrame) else source
        for chunk in source_batches:
            if _check_cancelled(task_id):
                return None

            df_chunk = chunk.drop(columns=[c for c in ["_billing_period", "_blob_path", "_ingested_at"] if c in chunk.columns])

            # Phase 1: Map source columns → destination columns (Silver)
            target = apply_mappings(df_chunk, cmapps)

            # Preserve provenance and only destination-named fields.
            target["cloud_origem"] = connection_code
            target["source_connection_id"] = connection_id
            target["_billing_period"] = str(billing_period)
            # Gold facts are historical realized data. Keeping this explicit
            # on every mapped batch prevents downstream Forecast from having
            # to infer a null type after a future schema evolution.
            target["tipo"] = "realizado"

            # Synthetic Return rows are created on the destination contract,
            # before financial business rules and classifications.
            clone_count = 0
            if clone_engine is not None:
                target, clone_count = clone_engine.apply(target, str(billing_period))

            # Compatibility calculation used by the imported attribution CSV.
            # It is intentionally evaluated after cloning: reservation Usage
            # uses the resolved On-Demand cost and its synthetic Return keeps
            # the equal negative value.  This is a destination calculation,
            # not a provider-header dependency.
            if "TotalCostOnDemandBRL" in dest_col_names:
                # Defensive helper: never let pd.to_numeric(None) collapse to a
                # scalar numpy.float64, which would break vectorized ops below.
                def _num(col: str) -> pd.Series:
                    return pd.to_numeric(
                        target[col] if col in target.columns else pd.Series([None] * len(target), index=target.index),
                        errors="coerce",
                    )

                direct_cost = _num("BilledCost")
                quantity = _num("ConsumedQuantity")
                unit_price = _num("source_unit_price")
                exchange = _num("source_exchange_rate")
                calculated = unit_price * quantity * exchange.fillna(1)
                # The historical rule uses billed cost for contractual events;
                # for usage it uses unit price × quantity × exchange rate.
                # If the provider did not supply a usable unit price (common
                # for a reservation), use the clone-resolved billed value.
                charge = target.get("ChargeCategory", pd.Series("", index=target.index)).astype(str).str.lower()
                contractual = charge.isin({"purchase", "refund", "roundingadjustment"})
                target["TotalCostOnDemandBRL"] = calculated.where(
                    calculated.notna() & calculated.ne(0) & ~contractual,
                    direct_cost,
                )

            # Phase 2: Rules operate on the mapped destination contract.
            for r in brules:
                _apply_business_rule_sync(target, r, str(billing_period))

            # Phase 3: Classifications operate on the mapped destination.
            if crules:
                _apply_classifications_sync(
                    target, crules, None,
                    dept_map=dept_cache,
                    hierarchy_cache=hierarchy_cache,
                )

            if target.empty:
                continue

            rows_in_blob += len(target)
            if batch_callback is not None:
                batch_callback(target, len(chunk))
            else:
                chunks.append(target)
            # Persist heartbeat/progress while a large blob is being processed.
            # Previously the UI stayed on the same blob until the whole future
            # completed, which looked like a deadlock for 300k+ row files.
            progress_text = (
                f"blob {billing_period}: {rows_in_blob:,} linhas processadas ({clone_count:,} Returns no batch)"
            )
            status = _inmemory_tasks.get(task_id)
            if status is not None:
                status["current_blob"] = progress_text
                status["phase"] = progress_text
            session.execute(text("""
                UPDATE etl_task
                   SET current_blob = :phase, phase = :phase
                 WHERE id = :task_id AND status = 'RUNNING'
            """), {"phase": progress_text, "task_id": task_id})
            session.commit()

        if not chunks and batch_callback is None:
            return None
        return pd.concat(chunks, ignore_index=True) if batch_callback is None else None
    finally:
        # Libera o Bronze filtrado e os chunks usados para compor este blob;
        # o DataFrame retornado continua vivo somente até ser escrito no
        # staging pelo coordenador.
        try:
            session.close()
        except Exception:
            pass
        try:
            import gc
            for _name in ("source", "chunks", "chunk", "df_chunk", "target"):
                if _name in locals():
                    locals()[_name] = None
            gc.collect()
        except Exception:
            pass

def _reprocess_destination_sync(
    task_id: int,
    destination_table_id: int,
    connection_ids: list[int],
    billing_periods: list[str],
    replace_existing: bool = False,
    publish_by_partition: bool = False,
) -> dict:
    """Read normalized data blob-by-blob via raw JSON → pandas → Iceberg. Granular progress."""
    import pandas as pd, json
    from app.database import SyncSession
    from app.models.source_connection import SourceConnection
    from app.models.ingested_blob import IngestedBlob
    from app.models.destination import DestinationTable, DestinationColumn, SourceDestinationMapping
    from app.models.rules import BusinessRule, ClassificationRule, Department
    from app.models.clone_rule import CloneRule, ClonePricePendencies
    from app.models.etl_task import EtlTask
    from sqlalchemy import select, text, func
    from app.services import iceberg_service as iceberg
    from app.services import dimon_builder
    from app.services.task_runner import _inmemory_tasks, _check_cancelled
    from app.services.cloud_scope import (
        rule_matches_cloud,
        rule_matches_connection,
        rule_matches_destination,
    )

    status = _inmemory_tasks.get(task_id, {})
    status["status"] = "RUNNING"
    _append_reprocess_log(task_id, "Worker iniciou o reprocessamento")
    
    def _sync_db():
        """Write current status to EtlTask DB row so it survives API reloads."""
        try:
            db2 = SyncSession()
            row = db2.get(EtlTask, task_id)
            if row:
                if row.status == "CANCELLED" or _check_cancelled(task_id):
                    status["status"] = "CANCELLED"
                    row.status = "CANCELLED"
                    row.phase = row.phase or "Cancelado pelo usuario"
                    row.finished_at = func.now()
                    db2.commit()
                    db2.close()
                    return
                row.status = status.get("status", row.status)
                row.total_blobs = status.get("total_blobs", row.total_blobs)
                row.processed_blobs = status.get("processed_blobs", row.processed_blobs)
                row.rows_destination = status.get("rows_destination", row.rows_destination)
                current = status.get("current_blob", row.current_blob)
                if isinstance(current, str):
                    row.current_blob = current[:500]
                    row.phase = current[:200]
                else:
                    row.current_blob = current
                    row.phase = current
                previous = dict(row.result_json or {})
                row.result_json = {
                    **previous,
                    "table": status.get("table", ""),
                    "rows_written": status.get("rows_destination", 0),
                    "error": status.get("error", ""),
                    "dimon_table": status.get("dimon_table"),
                    "dimon_rows": status.get("dimon_rows"),
                    "dimon_error": status.get("dimon_error"),
                }
                if status.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
                    row.finished_at = func.now()
                db2.commit()
            db2.close()
        except Exception:
            pass

    _sync_db()
    session = SyncSession()
    try:
        dest_info = session.get(DestinationTable, destination_table_id)
        if not dest_info:
            status["status"] = "FAILED"; status["error"] = "Tabela destino nao encontrada"
            return {"success": False, "error": "Tabela destino nao encontrada", "rows_written": 0}
        phys_name = dest_info.physical_name; status["table"] = dest_info.name
        is_focus_destination = (
            (dest_info.name or "").strip().upper() == "MODELO FOCUS"
            or (phys_name or "").strip().lower() == "dest_modelo_focus"
        )
        # A Dimon agregada é gerada ao final do reprocess SOMENTE para o
        # destino MODELO FOCUS, reutilizando os DataFrames em memória (não
        # varre a Gold de milhões de linhas).
        # Estrutura Dimon única = regras (dimon_rule + dimon_materialization),
        # geradas sob demanda via /dimon/reprocess. A geração automática
        # paralela (dimon_builder -> dest_dm_cost_focus) foi desativada.
        enable_dimon = False
        dimon_chunks: list = []

        all_mappings = []
        for cid in connection_ids:
            all_mappings.extend(session.execute(select(SourceDestinationMapping).where(
                SourceDestinationMapping.source_connection_id == cid,
                SourceDestinationMapping.destination_table_id == destination_table_id,
                SourceDestinationMapping.active == True,
            )).scalars().all())
        if not all_mappings:
            status["status"] = "FAILED"; status["error"] = "Nenhum mapeamento"
            return {"success": False, "error": "Nenhum mapeamento", "rows_written": 0}

        dest_col_meta = session.execute(
            select(DestinationColumn.column_name, DestinationColumn.data_type)
            .where(DestinationColumn.destination_table_id == destination_table_id)
            .order_by(DestinationColumn.position)
        ).all()
        dest_col_names = {r[0] for r in dest_col_meta}
        dest_col_types: dict[str, str] = {r[0]: (r[1] or "TEXT") for r in dest_col_meta}

        # Gather Bronze blobs per connection. Azure deduplicates stale export
        # executions; GCP keeps the configured current BigQuery table.
        all_blobs: list[tuple[int, int, str, str]] = []  # (blob_id, connection_id, billing_period, blob_path)
        for cid in connection_ids:
            conn = session.get(SourceConnection, cid)
            connector_type = conn.connector_type if conn else ""
            preferred_table = None
            if conn and connector_type == "gcp_bigquery":
                preferred_table = (conn.config_json or {}).get("table_name")

            q = select(
                IngestedBlob.id, IngestedBlob.billing_period,
                IngestedBlob.blob_path, IngestedBlob.last_modified,
            ).where(IngestedBlob.connection_id == cid)
            if billing_periods:
                q = q.where(IngestedBlob.billing_period.in_(billing_periods))
            q = q.order_by(IngestedBlob.billing_period)
            candidates = session.execute(q).all()
            # Azure Automation: a Bronze mistura jobs e schedules; cada destino
            # reprocessa apenas a fatia correspondente (por prefixo do blob).
            if phys_name.lower() == "dest_modelo_automation":
                candidates = [c for c in candidates if c[2].startswith("automation/jobs/")]
            elif phys_name.lower() == "dest_modelo_automation_schedule":
                candidates = [c for c in candidates if c[2].startswith("automation/schedules/")]
            elif phys_name.lower() == "dest_cloud_resource_inventory":
                candidates = [c for c in candidates if c[2].startswith("arg/inventory/")]
            elif phys_name.lower() == "dest_cloud_orphan_resources":
                candidates = [c for c in candidates if c[2].startswith("arg/orphans/")]
            for blob_id, period, path in _select_reprocess_blobs(
                candidates,
                connector_type=connector_type,
                preferred_table=preferred_table,
            ):
                all_blobs.append((blob_id, cid, period, path))

        total_blobs = len(all_blobs)
        expected_rows_by_blob = {
            blob_id: (session.get(IngestedBlob, blob_id).row_count or 0)
            for blob_id, _, _, _ in all_blobs
            if session.get(IngestedBlob, blob_id) is not None
        }
        expected_input_rows = sum(
            (session.get(IngestedBlob, blob_id).row_count or 0)
            for blob_id, _, _, _ in all_blobs
            if session.get(IngestedBlob, blob_id) is not None
        )
        status["total_blobs"] = total_blobs
        status["processed_blobs"] = 0
        status["rows_destination"] = 0
        _sync_db()
        _append_reprocess_log(task_id, f"Inventário concluído: {total_blobs} blobs e {expected_input_rows:,} linhas de entrada", details={"blobs": total_blobs, "input_rows": expected_input_rows})

        # ── Pre-load connections, rules, and hierarchy for ALL connections BEFORE the thread pool ──
        # ORM objects are loaded in the main session and become detached after session.close().
        # Detached objects retain their loaded attributes (expire_on_commit=False), so
        # worker threads can safely read them without any DB interaction.
        conn_map: dict[int, SourceConnection] = {}
        conn_rules: dict[int, tuple] = {}  # cid → (brules, crules, dept_cache, hierarchy_cache)
        clone_engines: dict[int, object] = {}
        unique_cids = set(cid for _, cid, _, _ in all_blobs)
        output_cols = {'departamento', 'centro_custo',
                       'ambiente', 'custom_fields', 'subscription_rg',
                       'departamento_nivel_1', 'departamento_nivel_2',
                       'departamento_nivel_3', 'departamento_nivel_4',
                       'departamento_nivel_5', 'departamento_nivel_6',
                       'departamento_nivel_7',
                       'CostBRL', 'ContractedCost', 'ListCost', 'TaxBRL',
                       'EffectiveCost', 'Tax_Base', 'Tax_Marketplace',
                       'VL_CUSTO_FINAL_BRL'}
        # These columns are part of the Gold technical contract even when a
        # batch generates no synthetic rows.  Without a stable contract the
        # first no-clone batch created a narrower staging schema and the next
        # clone-enabled batch failed with ``Mismatch in fields``.
        internal_col_types = {
            "source_record_id": "TEXT",
            "clone_source_record_id": "TEXT",
            "is_synthetic": "BOOLEAN",
            "clone_rule_id": "BIGINT",
            "clone_rule_version": "BIGINT",
            "ondemand_reference_price": "DOUBLE",
            "ondemand_reference_period": "TEXT",
            "cloud_origem": "TEXT",
            "source_connection_id": "BIGINT",
            "_billing_period": "TEXT",
            "tipo": "TEXT",
            # Auditoria: envelope JSON com a regra de classificacao que deu
            # match na linha (classification) e as regras financeiras aplicadas
            # (financial). Fica na Gold como texto JSON (mesmo padrao de
            # custom_fields), mas registrado como JSONB no catalogo.
            "business_rule": "JSONB",
        }
        internal_cols_all = list(internal_col_types)
        dest_col_types.update(internal_col_types)

        for cid in unique_cids:
            conn = session.get(SourceConnection, cid)
            if not conn:
                continue
            conn_map[cid] = conn

            # Rules are isolated by destination and source connection.  The
            # null-destination compatibility path is deliberately FOCUS-only:
            # it supports legacy FOCUS rules without letting them run in a
            # table created later by the user.
            destination_filter = BusinessRule.destination_table_id == destination_table_id
            if is_focus_destination:
                destination_filter = or_(
                    destination_filter,
                    BusinessRule.destination_table_id.is_(None),
                )
            _brules = [
                rule for rule in session.execute(
                    select(BusinessRule).where(
                        destination_filter,
                        BusinessRule.active == True,
                        or_(
                            BusinessRule.connection_id == cid,
                            BusinessRule.connection_id.is_(None),
                        ),
                    ).order_by(BusinessRule.priority)
                ).scalars().all()
                if rule_matches_connection(rule.connection_id, cid)
                and rule_matches_destination(
                    rule.destination_table_id,
                    destination_table_id,
                    is_focus_destination=is_focus_destination,
                )
                and rule_matches_cloud(rule.cloud_scope, conn.cloud_scope, conn.cloud_name)
            ]
            logger.info(
                "Carregadas %d regras financeiras para tabela destino %s (id=%d) e conexão %s (id=%d)",
                len(_brules), phys_name, destination_table_id, conn.code, cid,
            )
            _append_reprocess_log(
                task_id,
                f"Regras financeiras selecionadas para {conn.code}: {len(_brules)}",
                details={"connection_id": cid, "rule_ids": [rule.id for rule in _brules]},
            )

            _crules = [
                rule for rule in session.execute(
                    select(ClassificationRule).where(
                        ClassificationRule.active == True,
                    ).order_by(ClassificationRule.priority, ClassificationRule.id)
                ).scalars().all()
                if rule_matches_connection(rule.connection_id, cid)
                and rule_matches_destination(
                    rule.destination_table_id,
                    destination_table_id,
                    is_focus_destination=is_focus_destination,
                )
                and rule_matches_cloud(rule.cloud_scope, conn.cloud_scope, conn.cloud_name)
            ]

            # Business rules may create arbitrary destination columns (for
            # example ``realizado``). Keep those columns in the final Gold
            # projection instead of dropping them through the legacy fixed
            # output_cols allow-list below.
            for rule in _brules:
                if rule.target_column:
                    output_cols.add(rule.target_column)
                for condition in (rule.conditions or []):
                    if isinstance(condition, dict) and condition.get("then_field"):
                        output_cols.add(condition["then_field"])
            for rule in _crules:
                if rule.target_field and rule.target_field not in {"diretoria", "projeto_nome"}:
                    output_cols.add(rule.target_field)

            _dept_cache: dict = {}
            _hierarchy_cache: dict = {}
            depts = session.execute(select(Department)).scalars().all()
            _dept_cache = {d.id: d for d in depts}

            if _crules:
                dept_ids_needed = {r.department_id for r in _crules if r.department_id}
                for did in dept_ids_needed:
                    dept = _dept_cache.get(did)
                    if not dept:
                        _hierarchy_cache[did] = (None, None, [])
                        continue
                    visited = [dept]
                    cur = dept.parent_id
                    depth = 0
                    while cur and depth < 7:
                        parent = _dept_cache.get(cur)
                        if parent:
                            visited.append(parent)
                            cur = parent.parent_id
                        else:
                            break
                        depth += 1
                    _hierarchy_cache[did] = (dept, visited[-1], visited)

            conn_rules[cid] = (
                _brules, _crules, _dept_cache, _hierarchy_cache,
                conn.cloud_name,
            )
            clone_rules = session.execute(select(CloneRule).where(
                CloneRule.destination_table_id == destination_table_id,
                CloneRule.active == True,
                or_(CloneRule.connection_id == cid, CloneRule.connection_id.is_(None)),
            ).order_by(CloneRule.priority)).scalars().all()
            if clone_rules:
                from app.services.clone_engine import CloneEngine
                clone_engines[cid] = CloneEngine(clone_rules, destination_table_id)

        # Close main session NOW — all ORM objects are detached but attributes remain accessible
        session.close()

        status["phase"] = "Processando batches"
        status["current_blob"] = "Aguardando primeiro batch"
        _sync_db()
        _append_reprocess_log(task_id, "Processamento em batches iniciado")

        # Processa no máximo três blobs em memória. O código anterior guardava
        # os 28 DataFrames e depois fazia um pd.concat de toda a massa, o que
        # duplicava milhões de linhas em RAM e travava antes da escrita final.
        ordered_dest_cols = [r[0] for r in dest_col_meta]
        stable_stage_cols = list(ordered_dest_cols)
        stable_stage_cols.extend(
            c for c in sorted(output_cols)
            if c not in stable_stage_cols
        )
        stable_stage_cols.extend(
            c for c in internal_cols_all
            if c not in stable_stage_cols
        )

        # Colunas BIGINT que devem sempre ser Int64 (nullable), nunca float64.
        # Pandas converte int+None para float64 por padrão, o que quebra o
        # schema Iceberg (long -> double). Forçamos Int64 explicitamente.
        _BIGINT_COLS = {"clone_rule_id", "clone_rule_version", "source_connection_id"}

        def _prepare_for_stage(frame: pd.DataFrame) -> pd.DataFrame:
            prepared = frame.drop(columns=["diretoria", "projeto_nome"], errors="ignore").copy()
            for column in stable_stage_cols:
                if column not in prepared.columns:
                    if column == "tipo":
                        prepared[column] = "realizado"
                    elif column == "is_synthetic":
                        prepared[column] = False
                    else:
                        prepared[column] = None
            # Marketplace/Azure batches often have no clone rows, so this
            # column arrives all-null. Keep BOOLEAN to match existing Gold.
            if "is_synthetic" in prepared.columns:
                prepared["is_synthetic"] = (
                    prepared["is_synthetic"].map(lambda v: True if v is True or str(v).strip().lower() in {"true", "1"} else False)
                ).astype(bool)
            bool_cols = {
                name for name, dtype in dest_col_types.items()
                if str(dtype or "").upper() in {"BOOLEAN", "BOOL"}
            }
            def _as_bool(value):
                if value is None or (isinstance(value, float) and value != value):
                    return False
                if isinstance(value, bool):
                    return value
                text = str(value).strip().lower()
                return text in {"true", "1", "yes", "sim"}
            for col in bool_cols:
                if col in prepared.columns:
                    prepared[col] = prepared[col].map(_as_bool).astype(bool)
            # Garante colunas BIGINT como Int64 nullable (nunca float64/double).
            # Sem isso, Pandas promove int+None para float64, quebrando o schema
            # Iceberg com o erro "Cannot change column type: long -> double".
            for _bc in _BIGINT_COLS:
                if _bc in prepared.columns:
                    prepared[_bc] = pd.to_numeric(prepared[_bc], errors="coerce").astype("Int64")
            staged = prepared[stable_stage_cols].where(
                pd.notna(prepared[stable_stage_cols]), None
            )
            # Re-aplicar Int64 após .where() que pode regredir para float64
            for _bc in _BIGINT_COLS:
                if _bc in staged.columns:
                    staged[_bc] = pd.to_numeric(staged[_bc], errors="coerce").astype("Int64")
            if "is_synthetic" in staged.columns:
                staged["is_synthetic"] = staged["is_synthetic"].fillna(False).astype(bool)
            return staged

        temp_name: str | None = None
        staged_rows = 0

        def _stage_frame(frame: pd.DataFrame) -> None:
            """Append one completed blob to the temporary Iceberg table."""
            nonlocal temp_name, staged_rows
            if enable_dimon:
                # Reaproveita o frame em memória: acumula a agregação Dimon
                # (principais colunas, mensal, SUM quantity/realizado/billedcost)
                # sem reler a Gold publicada.
                try:
                    dimon_agg = dimon_builder.aggregate_frame(frame)
                    if dimon_agg is not None and not dimon_agg.empty:
                        dimon_chunks.append(dimon_agg)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Agregação Dimon falhou (Gold segue normal): %s", exc)
            prepared = _prepare_for_stage(frame)
            for start in range(0, len(prepared), 100_000):
                chunk = prepared.iloc[start:start + 100_000]
                if temp_name is None:
                    temp_name = iceberg.write_destination_staged(
                        phys_name, chunk, partition_col="_billing_period", column_types=dest_col_types,
                    )
                else:
                    iceberg.append_to_staged(
                        temp_name, chunk, partition_col="_billing_period", column_types=dest_col_types,
                    )
                staged_rows += len(chunk)
            del prepared

        # For partial reprocesses, preserve unaffected rows in the same
        # temporary Gold table. Full rebuilds never read the old Gold table.
        if billing_periods and connection_ids and not replace_existing and not publish_by_partition:
            try:
                for existing_batch in iceberg.read_destination_batches(
                    phys_name, set(billing_periods), set(connection_ids),
                    batch_size=int(os.getenv("REPROCESS_BATCH_ROWS", "100000")),
                ):
                    _stage_frame(existing_batch)
                    del existing_batch
            except Exception as merge_err:
                if "NoSuchTableException" not in type(merge_err).__name__ and "does not exist" not in str(merge_err).lower():
                    raise RuntimeError(f"Falha ao preservar dados existentes: {merge_err}") from merge_err

        # ── Phase 1: process one blob and one batch at a time ──
        total_rows_processed = 0
        errors: list[str] = []
        processed = 0
        import psutil
        memory_limit = int(os.getenv("REPROCESS_MEMORY_LIMIT_MB", "6144")) * 1024 * 1024

        def _memory_exceeded() -> bool:
            return psutil.Process(os.getpid()).memory_info().rss >= memory_limit

        from app.services.csv_upload_service import is_current_billing_period, should_skip_gold_realizado

        skipped_gold_blobs = 0
        skipped_input_rows = 0
        for blob_id, cid, bperiod, blob_path in all_blobs:
            if _check_cancelled(task_id):
                status["status"] = "CANCELLED"
                return {"success": False, "error": "Cancelado", "rows_written": 0}
            if _memory_exceeded():
                raise RuntimeError(
                    f"Limite de memoria do worker excedido antes do blob {blob_id} "
                    f"({memory_limit // (1024 * 1024)} MB); Gold anterior preservada"
                )
            try:
                if cid not in conn_rules:
                    processed += 1
                    status["processed_blobs"] = processed
                    _sync_db()
                    continue
                brules, crules, dept_cache, hierarchy_cache, conn_cloud = conn_rules[cid]
                cmapps = [m for m in all_mappings if m.source_connection_id == cid]
                if not cmapps:
                    processed += 1
                    status["processed_blobs"] = processed
                    _sync_db()
                    continue
                conn = conn_map[cid]

                skip_sample = None
                if is_current_billing_period(bperiod):
                    try:
                        skip_sample = iceberg.read_bronze(
                            conn.code,
                            blob_path=blob_path,
                            billing_period=bperiod,
                            max_rows=min(expected_rows_by_blob.get(blob_id, 5000), 5000),
                        )
                    except Exception:
                        skip_sample = None
                skip_gold, skip_message = should_skip_gold_realizado(
                    bperiod,
                    connection_config=conn.config_json or {},
                    df=skip_sample,
                )
                if skip_gold:
                    skipped_gold_blobs += 1
                    skipped_input_rows += expected_rows_by_blob.get(blob_id, 0)
                    note = skip_message or (
                        f"Blob {blob_id} ({bperiod}): granularidade mensal no mês corrente — "
                        "publicação Gold realizado ignorada"
                    )
                    _append_reprocess_log(task_id, note, details={"blob_id": blob_id, "billing_period": bperiod})
                    status["current_blob"] = note
                    processed += 1
                    status["processed_blobs"] = processed
                    _sync_db()
                    continue

                blob_input_rows = 0
                blob_output_rows = 0

                def _write_batch(frame, source_rows):
                    nonlocal total_rows_processed
                    nonlocal blob_input_rows, blob_output_rows
                    _stage_frame(frame)
                    blob_input_rows += source_rows
                    blob_output_rows += len(frame)
                    total_rows_processed += len(frame)
                    status["rows_destination"] = total_rows_processed
                    status["current_blob"] = (
                        f"batch: blob {processed + 1}/{total_blobs}, {bperiod}, "
                        f"{total_rows_processed:,} linhas processadas"
                    )
                    _sync_db()

                _process_blob_for_reprocess(
                    blob_id, cid, conn.code, bperiod,
                    brules, crules, cmapps, dest_col_names,
                    dept_cache, hierarchy_cache,
                    output_cols, task_id,
                    clone_engine=clone_engines.get(cid), batch_callback=_write_batch,
                    connection_cloud_name=conn_cloud,
                )
                expected_blob_rows = expected_rows_by_blob.get(blob_id, 0)
                if blob_input_rows == 0:
                    if expected_blob_rows == 0:
                        processed += 1
                        status["processed_blobs"] = processed
                        warn_msg = f"Aviso: Blob {blob_id} ({bperiod}) não possui consumo (fatura zerada)"
                        logger.warning(warn_msg)
                        _append_reprocess_log(task_id, warn_msg, details={"blob_id": blob_id, "billing_period": bperiod})
                        status["current_blob"] = f"blob {processed}/{total_blobs}: {bperiod} (Aviso: fatura zerada)"
                        _sync_db()
                        continue
                    raise RuntimeError(
                        f"Nenhuma linha encontrada no Bronze para o blob {blob_id} ({bperiod})"
                    )
                if blob_output_rows < blob_input_rows:
                    raise RuntimeError(
                        f"Contagem divergente no blob {blob_id} ({bperiod}): "
                        f"esperado pelo menos {blob_input_rows}, lido {blob_input_rows}, "
                        f"escrito {blob_output_rows}"
                    )
                processed += 1
                status["processed_blobs"] = processed
                status["current_blob"] = f"blob {processed}/{total_blobs}: {bperiod} ({total_rows_processed:,} linhas acumuladas)"
                _sync_db()
            except Exception as e:
                import traceback
                traceback.print_exc()
                error_msg = f"Blob {blob_id} ({bperiod}): {e}"
                errors.append(error_msg)
                # A partial staging table must never continue after a blob
                # failure. It would hide missing rows behind a later total.
                status["status"] = "FAILED"
                status["error"] = error_msg
                status["current_blob"] = error_msg
                _sync_db()
                if temp_name:
                    iceberg.cleanup_staged_table(temp_name)
                return {"success": False, "error": error_msg, "rows_written": total_rows_processed}

        if errors:
            status.setdefault("errors", []).extend(errors)
        if total_rows_processed == 0:
            if temp_name:
                iceberg.cleanup_staged_table(temp_name)
            if errors:
                status["status"] = "FAILED"
                status["error"] = errors[0]
                status["current_blob"] = f"Falha: {errors[0]}"
                _sync_db()
                return {"success": False, "error": errors[0], "rows_written": 0}
            status["status"] = "COMPLETED"
            status["current_blob"] = "Nenhum dado processado"
            return {"success": True, "rows_written": 0, "table": dest_info.name}

        expected_output_rows = max(0, expected_input_rows - skipped_input_rows)
        if total_rows_processed < expected_output_rows:
            msg = (
                f"Contagem de entrada/saída divergente: entrada {expected_output_rows:,}, "
                f"saída {total_rows_processed:,}. Reprocessamento abortado; destino anterior preservado."
            )
            status["status"] = "FAILED"
            status["error"] = msg
            status["current_blob"] = msg
            status.setdefault("errors", []).append(msg)
            _sync_db()
            _append_reprocess_log(task_id, msg, level="ERROR", details={"input_rows": expected_input_rows, "output_rows": total_rows_processed})
            if temp_name:
                iceberg.cleanup_staged_table(temp_name)
            return {"success": False, "error": msg, "rows_written": 0}

        # Price gaps do not invalidate a reprocess; they remain auditable.
        for engine in clone_engines.values():
            for pending in engine.pendencies:
                session.add(ClonePricePendencies(**pending))
        session.commit()
        try:
            status["current_blob"] = f"publicando lotes na Gold (swap por partição)..."
            _sync_db()
            _append_reprocess_log(task_id, "Validação concluída; iniciando publicação na Gold")

            total_written = iceberg.publish_staged_by_slices(
                phys_name, temp_name,
            )
        except Exception as stage_err:
            status["current_blob"] = f"Falha na escrita Iceberg ({stage_err}), revertendo..."
            _sync_db()
            if temp_name:
                iceberg.cleanup_staged_table(temp_name)
            raise stage_err

        # ── Dimon agregada (só MODELO FOCUS): publica a pré-agregação mensal
        # gerada dos frames em memória. Se falhar, reconstrói da Gold (fallback)
        # para manter consistência; a Gold já publicada permanece a fonte da
        # verdade e o erro fica auditável no resultado da tarefa.
        dimon_rows = 0
        dimon_error = None
        if enable_dimon:
            try:
                dimon_rows = dimon_builder.publish_dimons(dimon_chunks)
                status["dimon_rows"] = dimon_rows
                status["dimon_table"] = dimon_builder.DIMON_TABLE
                status["current_blob"] = (
                    f"Concluido: {total_written} linhas na Gold, "
                    f"{dimon_rows:,} linhas na Dimon ({dimon_builder.DIMON_TABLE})"
                )
                _append_reprocess_log(
                    task_id,
                    f"Dimon publicada: {dimon_rows:,} linhas agregadas",
                    details={"dimon_table": dimon_builder.DIMON_TABLE, "dimon_rows": dimon_rows},
                )
            except Exception as exc:  # noqa: BLE001
                dimon_error = str(exc)
                try:
                    dimon_rows = dimon_builder.rebuild_dimons_from_gold()
                    dimon_error = None
                    status["dimon_rows"] = dimon_rows
                    status["dimon_table"] = dimon_builder.DIMON_TABLE
                    _append_reprocess_log(
                        task_id,
                        f"Dimon reconstruída da Gold (fallback): {dimon_rows:,} linhas",
                        details={"dimon_table": dimon_builder.DIMON_TABLE, "dimon_rows": dimon_rows},
                    )
                except Exception as exc2:  # noqa: BLE001
                    dimon_error = f"{dimon_error} | rebuild: {exc2}"
                    status["dimon_error"] = dimon_error
                    status["dimon_rows"] = 0
                    _append_reprocess_log(task_id, f"Dimon falhou: {dimon_error}", level="ERROR")
            _sync_db()

        status["status"] = "COMPLETED"
        status["rows_destination"] = total_written
        if not status.get("current_blob"):
            status["current_blob"] = f"Concluido: {total_written} linhas, {len(all_blobs)} blobs processados"
        _sync_db()
        _append_reprocess_log(task_id, "Reprocessamento concluído e Gold publicada", details={"rows_written": total_written, "blobs_processed": len(all_blobs), "table": dest_info.name})
        return {"success": True, "rows_written": total_written, "table": dest_info.name,
                "dimon_table": dimon_builder.DIMON_TABLE if enable_dimon else None,
                "dimon_rows": dimon_rows if enable_dimon else None,
                "dimon_error": dimon_error}

    except Exception as e:
        import traceback
        status["status"] = "FAILED"; status["error"] = str(e); status["current_blob"] = f"Erro: {e}"
        traceback.print_exc()
        _sync_db()
        _append_reprocess_log(task_id, f"Falha no reprocessamento: {e}", level="ERROR")
        return {"success": False, "error": str(e), "rows_written": 0}
    finally:
        # Keep the previous Gold table intact whenever staging did not reach
        # the atomic swap, and release worker-held pandas objects promptly.
        try:
            if locals().get("temp_name") and status.get("status") != "COMPLETED":
                iceberg.cleanup_staged_table(temp_name)
        except Exception:
            pass
        try:
            session.close()
        except Exception:
            pass
        try:
            import gc
            gc.collect()
        except Exception:
            pass


@router.post("/reprocess")
async def reprocess_destination(body: ReprocessRequest, db: AsyncSession = Depends(get_db)):
    """Queue one atomic Gold publication per source and billing month."""
    from app.models.etl_task import EtlTask
    from sqlalchemy import insert

    heartbeat = os.getenv("OPENFINOPS_WORKER_HEARTBEAT", "/tmp/openfinops-worker.heartbeat")
    pid_file = os.getenv("OPENFINOPS_WORKER_PID", "/tmp/openfinops-worker.pid")
    try:
        worker_pid = int(open(pid_file).read().strip())
        os.kill(worker_pid, 0)
        worker_age = __import__("time").time() - os.path.getmtime(heartbeat)
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        worker_age = 999
    if worker_age > 15:
        raise HTTPException(status_code=503, detail="Worker ETL indisponível; a tarefa não foi criada")

    active = await db.execute(
        select(EtlTask.id).where(
            EtlTask.task_type == "reprocess",
            EtlTask.destination_table_id == body.destination_table_id,
            EtlTask.status.in_(["QUEUED", "RUNNING"]),
        ).limit(1)
    )
    active_id = active.scalar_one_or_none()
    if active_id is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Já existe um reprocessamento ativo para esta tabela (task {active_id})",
        )

    conn_rows = (await db.execute(
        select(SourceConnection).where(SourceConnection.id.in_(body.connection_ids))
    )).scalars().all()
    conn_by_id = {row.id: row for row in conn_rows}
    ptu_connections = [
        conn_by_id[cid] for cid in body.connection_ids
        if conn_by_id.get(cid) and conn_by_id[cid].connector_type == "azure_openai_ptu"
    ]
    blob_connections = [
        conn_by_id[cid] for cid in body.connection_ids
        if conn_by_id.get(cid) and conn_by_id[cid].connector_type != "azure_openai_ptu"
    ]

    if ptu_connections and not blob_connections:
        cfg_days = int((ptu_connections[0].config_json or {}).get("backfill_days") or 93)
        periods = body.billing_periods or []
        backfill_days = cfg_days if not periods else None
        queue_group = str(uuid4())
        task_ids: list[int] = []
        for position, conn in enumerate(ptu_connections, start=1):
            period_suffix = f" · meses {', '.join(periods)}" if periods else ""
            if periods:
                phase = f"Na fila: buscar métricas Azure PTU{period_suffix}"
                log_message = f"Coleta Azure PTU por período{period_suffix}"
            else:
                phase = f"Na fila: coleta histórica Azure PTU ({cfg_days} dias)"
                log_message = f"Coleta histórica Azure PTU ({cfg_days} dias, janelas de 30 dias)"
            details = {
                "connection_ids": [conn.id],
                "billing_periods": periods,
                "ptu_backfill": not periods,
                "backfill_days": backfill_days,
                "queue_group": queue_group,
                "queue_position": position,
                "queue_total": len(ptu_connections),
            }
            result = await db.execute(
                insert(EtlTask).values(
                    task_type="reprocess",
                    status="QUEUED",
                    destination_table_id=body.destination_table_id,
                    connection_id=conn.id,
                    connection_ids=[conn.id],
                    billing_periods=periods,
                    result_json={
                        "replace_existing": False,
                        "backfill": not periods,
                        **details,
                        "chain_dimons": body.chain_dimons.model_dump() if body.chain_dimons else {},
                        "event_log": [{
                            "at": datetime.now(timezone.utc).isoformat(),
                            "level": "INFO",
                            "message": log_message,
                            "details": details,
                        }],
                    },
                    total_blobs=0,
                    processed_blobs=0,
                    rows_raw=0,
                    rows_normalized=0,
                    rows_destination=0,
                    errors=[],
                    phase=phase,
                ).returning(EtlTask.id)
            )
            task_ids.append(result.scalar_one())
        await db.commit()
        if periods:
            message = (
                f"{len(task_ids)} coleta(s) Azure PTU enfileirada(s) "
                f"para {', '.join(periods)} (Azure Monitor retém ~93 dias)"
            )
        else:
            message = (
                f"{len(task_ids)} coleta(s) histórica(s) Azure PTU enfileirada(s) "
                f"({cfg_days} dias, janelas de até 30 dias)"
            )
        return {
            "task_id": task_ids[0],
            "task_ids": task_ids,
            "queue_group": queue_group,
            "message": message,
        }

    available_stmt = select(IngestedBlob.connection_id, IngestedBlob.billing_period).where(
        IngestedBlob.connection_id.in_(body.connection_ids)
    )
    if body.billing_periods:
        available_stmt = available_stmt.where(IngestedBlob.billing_period.in_(body.billing_periods))
    available = {(int(connection_id), str(period)) for connection_id, period in (await db.execute(available_stmt)).all()
                 if connection_id is not None and period}
    ordered_pairs = [
        (connection_id, period)
        for period in sorted({period for _, period in available})
        for connection_id in body.connection_ids
        if (connection_id, period) in available
    ]
    if not ordered_pairs:
        raise HTTPException(
            status_code=422,
            detail=(
                "Nenhum blob Bronze encontrado para as fontes e meses selecionados. "
                "Importe a origem primeiro (Extração). Se a extração falhou, o Bronze "
                "fica vazio e o reprocesso não tem meses para publicar."
            ),
        )

    queue_group = str(uuid4())
    task_ids: list[int] = []
    for position, (connection_id, period) in enumerate(ordered_pairs, start=1):
        details = {
            "connection_ids": [connection_id], "billing_periods": [period],
            "publish_by_partition": True, "queue_group": queue_group,
            "queue_position": position, "queue_total": len(ordered_pairs),
        }
        result = await db.execute(
            insert(EtlTask).values(
                task_type="reprocess", status="QUEUED", destination_table_id=body.destination_table_id,
                connection_ids=[connection_id], billing_periods=[period],
                # Each child replaces only its own source/month. ``replace_existing``
                # must never erase the other queued months.
                result_json={"replace_existing": False, **details,
                             "chain_dimons": body.chain_dimons.model_dump() if body.chain_dimons else {},
                             "event_log": [{
                    "at": datetime.now(timezone.utc).isoformat(), "level": "INFO",
                    "message": f"Fila {position}/{len(ordered_pairs)}: fonte {connection_id}, mês {period}",
                    "details": details,
                }]},
                total_blobs=0, processed_blobs=0, rows_raw=0, rows_normalized=0,
                rows_destination=0, errors=[],
            ).returning(EtlTask.id)
        )
        task_ids.append(result.scalar_one())
    await db.commit()

    return {"task_id": task_ids[0], "task_ids": task_ids, "queue_group": queue_group,
            "message": f"{len(task_ids)} publicação(ões) fonte+mês enfileiradas"}


@router.post("/reprocess-shared-cost")
async def reprocess_shared_cost(db: AsyncSession = Depends(get_db)):
    """Enqueue a worker job that writes tipo=rateio rows into the planning table."""
    from app.models.etl_task import EtlTask
    from app.models.rules import Department, DepartmentSharedCost
    from app.services.shared_cost import snapshot_enabled_shared_costs

    enabled = (await db.execute(
        select(DepartmentSharedCost).where(DepartmentSharedCost.enabled == True)
    )).scalars().all()
    if not enabled:
        raise HTTPException(
            status_code=422,
            detail="Nenhum custo compartilhado habilitado. Ative Custo compartilhado no departamento correspondente.",
        )

    snapshot = snapshot_enabled_shared_costs(
        list(enabled),
        {department.id: department for department in (await db.execute(select(Department))).scalars().all()},
    )
    if not snapshot["shared_nodes"]:
        raise HTTPException(
            status_code=422,
            detail="Custo compartilhado habilitado, mas sem cloud de origem ou projetos para ratear.",
        )

    now = datetime.now(timezone.utc)
    active = list((await db.execute(
        select(EtlTask).where(
            EtlTask.task_type == "shared_cost",
            EtlTask.status.in_(["QUEUED", "RUNNING"]),
        )
    )).scalars().all())
    if active:
        task = active[0]
        for leftover in active[1:]:
            leftover.status = "CANCELLED"
            leftover.phase = "Substituído por um novo reprocessamento de custo compartilhado"
            leftover.finished_at = now
    else:
        task = EtlTask(task_type="shared_cost", errors=[])
        db.add(task)
        await db.flush()
    task.status = "QUEUED"
    task.phase = "Na fila: gerar linhas de rateio na tabela de planejamento"
    task.started_at = now
    task.finished_at = None
    task.total_blobs = len(snapshot["shared_nodes"])
    task.processed_blobs = 0
    task.rows_destination = 0
    task.result_json = {
        "snapshot": snapshot,
        "event_log": [{
            "at": now.isoformat(),
            "level": "INFO",
            "message": "Reprocessamento de custo compartilhado enfileirado. O worker grava tipo=rateio no planejamento.",
        }],
    }
    await db.commit()
    await db.refresh(task)
    return {
        "task_id": task.id,
        "status": "QUEUED",
        "message": task.phase,
    }


@router.post("/reprocess-ai-cost-relationships")
async def reprocess_ai_cost_relationships(db: AsyncSession = Depends(get_db)):
    """Queue one classified FOCUS pass that publishes one Gold per active rule."""
    from app.models.etl_task import EtlTask
    from app.models.ai_cost import AiCostLink
    from app.services.ai_cost_attribution import load_focus_schema
    from app.services.ai_cost_materialization import _normalized_conditions
    from sqlalchemy.orm import selectinload

    links = list((await db.execute(select(AiCostLink).options(selectinload(AiCostLink.sources)).where(AiCostLink.active == True))).scalars().all())  # noqa: E712
    if not links:
        raise HTTPException(status_code=422, detail="Nenhum relacionamento de custo IA ativo para reprocessar.")
    schema = await load_focus_schema()
    invalid_rules = []
    for link in links:
        for source in link.sources or []:
            _, missing = _normalized_conditions(schema, (source.filter_json or {}).get("conditions") or [])
            if missing:
                invalid_rules.append(f"{link.name} (fonte {source.id}): {', '.join(missing)}")
    now = datetime.now(timezone.utc)
    active = list((await db.execute(select(EtlTask).where(
        EtlTask.task_type == "ai_cost_relationship_reprocess",
        EtlTask.status.in_(["QUEUED", "RUNNING"]),
    ))).scalars().all())
    if active:
        raise HTTPException(status_code=409, detail="Já existe um reprocessamento de relacionamentos IA em andamento.")
    task = EtlTask(task_type="ai_cost_relationship_reprocess", status="QUEUED", errors=[])
    # A rule with FOCUS fields absent from the current Gold is flagged and
    # skipped by the worker (per-rule isolation) instead of blocking the
    # whole batch. Surface the warnings on the queued task for visibility.
    task.phase = (
        "Na fila: classificar FOCUS uma vez e publicar as Gold dos relacionamentos"
        + (f" · {len(invalid_rules)} regra(s) com campo(s) inválido(s)" if invalid_rules else "")
    )
    task.started_at = now
    task.total_blobs = len(links)
    task.processed_blobs = 0
    task.rows_destination = 0
    task.result_json = {
        "event_log": [{"at": now.isoformat(), "level": "INFO", "message": task.phase}],
        "warnings": invalid_rules,
    }
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return {"task_id": task.id, "status": task.status, "message": task.phase, "warnings": invalid_rules}


@router.get("/ai-cost-relationship-history")
async def ai_cost_relationship_history(limit: int = 30, db: AsyncSession = Depends(get_db)):
    from app.models.etl_task import EtlTask
    rows = list((await db.execute(select(EtlTask).where(
        EtlTask.task_type == "ai_cost_relationship_reprocess"
    ).order_by(EtlTask.id.desc()).limit(min(max(limit, 1), 100)))).scalars().all())
    return [{
        "id": row.id, "status": row.status, "phase": row.phase, "started_at": row.started_at,
        "finished_at": row.finished_at, "rows_destination": row.rows_destination,
        "result_json": row.result_json or {}, "error": (row.errors or [None])[0],
    } for row in rows]


@router.get("/shared-cost-history")
async def get_shared_cost_history(limit: int = 30):
    """Return queued, running and finished shared-cost (rateio) jobs."""
    from app.database import SyncSession
    from app.models.etl_task import EtlTask
    session = SyncSession()
    try:
        rows = session.execute(
            select(EtlTask).where(EtlTask.task_type == "shared_cost")
            .order_by(EtlTask.started_at.desc()).limit(max(1, min(limit, 100)))
        ).scalars().all()
        return [{
            "id": row.id,
            "status": row.status,
            "phase": row.phase or row.current_blob or "",
            "rows_written": row.rows_destination,
            "total_blobs": row.total_blobs,
            "processed_blobs": row.processed_blobs,
            "table": (row.result_json or {}).get("planning_table") or (row.result_json or {}).get("table", ""),
            "error": (row.result_json or {}).get("error", ""),
            "errors": row.errors or [],
            "event_log": (row.result_json or {}).get("event_log", []),
            "snapshot": (row.result_json or {}).get("snapshot"),
            "started_at": row.started_at,
            "finished_at": row.finished_at,
        } for row in rows]
    finally:
        session.close()


@router.get("/reprocess-history")
async def get_reprocess_history(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0), search: str | None = None, sort_field: str = "started_at", sort_direction: str = "desc", filters: str | None = None):
    """Return completed and active reprocess runs with their persisted log."""
    from app.database import SyncSession
    from app.models.etl_task import EtlTask
    session = SyncSession()
    try:
        json_str = cast(EtlTask.result_json, String)
        fields = {
            "id": EtlTask.id,
            "status": EtlTask.status,
            "destination_table_id": EtlTask.destination_table_id,
            "rows_written": EtlTask.rows_destination,
            "phase": EtlTask.phase,
            "started_at": EtlTask.started_at,
            "finished_at": EtlTask.finished_at,
            # `table` vive em result_json; comparar pela coluna JSON extraída (texto).
            "table": EtlTask.result_json.op("->>")("table"),
        }
        conditions = [EtlTask.task_type == "reprocess"]
        if search:
            needle = f"%{search.strip()}%"
            conditions.append(or_(
                EtlTask.status.ilike(needle),
                EtlTask.phase.ilike(needle),
                json_str.ilike(needle),
                cast(EtlTask.errors, String).ilike(needle),
            ))
        try:
            specs = json.loads(filters) if filters else []
        except json.JSONDecodeError as exc:
            raise HTTPException(422, "Filtros inválidos") from exc
        for spec in specs if isinstance(specs, list) else []:
            field = str(spec.get("field", ""))
            value = str(spec.get("value", ""))
            op = str(spec.get("operator", "equals"))
            column = fields.get(field)
            if column is None or not value:
                continue
            if field in ("started_at", "finished_at"):
                try:
                    clean_val = value.replace("Z", "+00:00")
                    if "T" in clean_val:
                        dt_val = datetime.fromisoformat(clean_val)
                    else:
                        dt_val = datetime.fromisoformat(clean_val + "T00:00:00")
                    expr = func.date_trunc("minute", column) == func.date_trunc("minute", dt_val) if ":" in value else func.date(column) == dt_val.date()
                except ValueError:
                    continue
            elif field == "rows_written":
                try:
                    num_val = int(value)
                    expr = column == num_val
                except ValueError:
                    continue
            else:
                # Mesmo padrão do grid de Connections: comparar como texto,
                # com NULL incluído no notEquals.
                if op == "notEquals":
                    conditions.append(or_(column.is_(None), cast(column, String) != value))
                else:
                    conditions.append(cast(column, String) == value)
                continue
            conditions.append(or_(column.is_(None), expr == False) if op == "notEquals" else expr)
        sort = fields.get(sort_field, EtlTask.started_at)
        ordering = sort.desc() if sort_direction == "desc" else sort.asc()
        total = session.execute(select(func.count()).select_from(EtlTask).where(*conditions)).scalar_one()
        rows = session.execute(select(EtlTask).where(*conditions).order_by(ordering, EtlTask.id.desc()).offset(offset).limit(limit)).scalars().all()
        items = [{
            "id": row.id, "status": row.status, "destination_table_id": row.destination_table_id,
            "connection_ids": row.connection_ids or [], "billing_periods": row.billing_periods or [],
            "total_blobs": row.total_blobs, "processed_blobs": row.processed_blobs,
            "rows_written": row.rows_destination, "phase": row.phase or row.current_blob or "",
            "table": (row.result_json or {}).get("table", ""), "error": (row.result_json or {}).get("error", ""),
            "errors": row.errors or [], "event_log": (row.result_json or {}).get("event_log", []),
            "started_at": row.started_at, "finished_at": row.finished_at,
        } for row in rows]
        return {"items": items, "total": total}
    finally:
        session.close()


@router.get("/reprocess-status/{task_id}")
async def get_reprocess_status(task_id: int):
    """Get persisted worker status, using memory only for legacy tasks."""
    from app.services.task_runner import _inmemory_tasks
    status = None
    try:
        from app.database import SyncSession
        from app.models.etl_task import EtlTask
        db = SyncSession()
        row = db.get(EtlTask, task_id)
        db.close()
        if row:
            status = {
                "id": row.id,
                "status": row.status,
                "current_blob": row.current_blob or row.phase or "",
                "total_blobs": row.total_blobs,
                "processed_blobs": row.processed_blobs,
                "rows_destination": row.rows_destination,
                "table": (row.result_json or {}).get("table", ""),
                "connection_ids": row.connection_ids or [],
                "error": (row.result_json or {}).get("error", "") or (row.phase if row.status == "FAILED" else ""),
            }
    except Exception:
        pass
    if not status:
        status = _inmemory_tasks.get(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="Task not found")
    tb = status.get("total_blobs", 0)
    cb = status.get("processed_blobs", 0)
    progress = int((cb / tb) * 100) if tb > 0 else 0
    return {
        "id": task_id,
        "status": status.get("status", "UNKNOWN"),
        "phase": status.get("current_blob", status.get("phase", "")),
        "total_batches": tb,
        "completed_batches": cb,
        "progress": progress,
        "rows_written": status.get("rows_destination", 0),
        "table": status.get("table", ""),
        "connection_ids": status.get("connection_ids", []),
        "error": status.get("error", ""),
    }


@router.post("/reprocess/cancel/{task_id}")
async def cancel_reprocess(task_id: int):
    """Cancel a running/queued reprocess task — cross-process via DB + in-memory flag."""
    from app.services.task_runner import _inmemory_tasks, _cancel_flags
    from app.services.task_runner import cancel_task as _cancel_task

    _cancel_flags.add(task_id)
    status = _inmemory_tasks.get(task_id)
    if status:
        status["status"] = "CANCELLED"

    # Also write to DB so the worker process sees the cancellation
    try:
        from app.database import SyncSession
        from app.models.etl_task import EtlTask
        from sqlalchemy import func as sa_func
        db = SyncSession()
        row = db.get(EtlTask, task_id)
        if row and row.status in ("QUEUED", "RUNNING"):
            row.status = "CANCELLED"
            row.phase = "Cancelado pelo usuario"
            row.finished_at = sa_func.now()
            db.commit()
            db.close()
            _append_reprocess_log(task_id, "Cancelado pelo usuário", level="WARNING")
            return {"message": "Cancel signal sent", "task_id": task_id}
        db.close()
    except Exception:
        pass

    # If no in-memory entry and no queued/running DB row, task doesn't exist
    if not status:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"message": "Cancel signal sent", "task_id": task_id}


@router.post("/connections/{connection_id}/process-destination")
async def process_destination_tables(connection_id: int):
    """Legacy: reprocess all dest tables for a single connection (all periods)."""
    from app.database import SyncSession as SS
    from app.models.destination import SourceDestinationMapping
    from sqlalchemy import select

    # Discover which destination tables this connection maps to
    session = SS()
    try:
        mapped_tables = session.execute(
            select(SourceDestinationMapping.destination_table_id)
            .where(
                SourceDestinationMapping.source_connection_id == connection_id,
                SourceDestinationMapping.active == True,
            )
            .distinct()
        ).scalars().all()

        total = 0
        last_error = None
        loop = asyncio.get_running_loop()
        for dt_id in mapped_tables:
            # Use a negative task_id for internal calls (no progress tracking)
            internal_task_id = -dt_id
            from app.services.task_runner import _inmemory_tasks as _gt
            _gt[internal_task_id] = {"status": "RUNNING", "total_blobs": 0, "processed_blobs": 0, "rows_destination": 0}
            res = await loop.run_in_executor(
                _dest_reprocess_executor,
                _reprocess_destination_sync,
                internal_task_id,
                dt_id,
                [connection_id],
                [],
            )
            if res["success"]:
                total += res["rows_written"]
            else:
                last_error = res.get("error", "Unknown")
        session.close()
        return {"success": True, "rows_written": total, "error": last_error}
    except Exception as e:
        session.close()
        return {"success": False, "error": str(e), "rows_written": 0}


@router.get("/periods")
async def list_available_periods(
    connection_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """List distinct billing periods available for a connection."""
    conn = await db.get(SourceConnection, connection_id)
    if conn and conn.connector_type == "azure_openai_ptu":
        from app.services.azure_ptu_service import collection_windows, resolve_config

        cfg = resolve_config(dict(conn.config_json or {}))
        windows = collection_windows(cfg, backfill_days=int(cfg.get("backfill_days") or 93))
        counts: dict[str, int] = {}
        for period, _, _ in windows:
            counts[period] = counts.get(period, 0) + 1
        return [
            {
                "period": period,
                "blob_count": count,
                "label": f"{period[4:]}/{period[:4]}",
            }
            for period, count in sorted(counts.items(), key=lambda item: item[0], reverse=True)
        ]

    rows = (await db.execute(
        text(
            "SELECT DISTINCT billing_period, COUNT(*) as cnt "
            "FROM ingested_blob WHERE connection_id = :cid AND billing_period IS NOT NULL AND billing_period != '' "
            "GROUP BY billing_period ORDER BY billing_period DESC"
        ),
        {"cid": connection_id},
    )).fetchall()
    return [
        {"period": r[0], "blob_count": r[1]}
        for r in rows
    ]


@router.get("/connections-for-dest/{destination_table_id}")
async def list_connections_for_destination(
    destination_table_id: int,
    db: AsyncSession = Depends(get_db),
):
    """List connections that have SourceDestinationMappings for this destination table."""
    rows = (await db.execute(
        text(
            "SELECT DISTINCT sc.id, sc.code, sc.name, sc.connector_type, COUNT(sdm.id) AS mapping_count "
            "FROM source_destination_mapping sdm "
            "JOIN source_connection sc ON sc.id = sdm.source_connection_id "
            "WHERE sdm.destination_table_id = :did AND sdm.active = true "
            "GROUP BY sc.id, sc.code, sc.name, sc.connector_type "
            "ORDER BY sc.code"
        ),
        {"did": destination_table_id},
    )).fetchall()
    return [
        {
            "id": r[0],
            "code": r[1],
            "name": r[2],
            "connector_type": r[3],
            "mapping_count": r[4],
        }
        for r in rows
    ]


# ═══════════════════════════════════════════════════════════════════════
# Scheduled Tasks (cron-like ETL automation)
# ═══════════════════════════════════════════════════════════════════════

from app.models.scheduled_task import ScheduledTask
from app.services import scheduler_service


@router.get("/schedules", response_model=list[ScheduledTaskOut])
async def list_schedules(db: AsyncSession = Depends(get_db)):
    """List all scheduled ETL tasks."""
    result = await db.execute(
        select(ScheduledTask).order_by(ScheduledTask.created_at.desc())
    )
    return result.scalars().all()


@router.post("/schedules", response_model=ScheduledTaskOut, status_code=201)
async def create_schedule(body: ScheduledTaskCreate, db: AsyncSession = Depends(get_db)):
    """Create a new scheduled ETL task."""
    # Validate cron expression
    try:
        croniter(body.cron_expression)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid cron expression: {body.cron_expression}")

    sched = ScheduledTask(
        name=body.name,
        connection_id=body.connection_id,
        connection_ids=body.connection_ids or [body.connection_id],
        cron_expression=body.cron_expression,
        timezone=body.timezone,
        destination_reprocess=body.destination_reprocess,
        active=body.active,
        config_json=body.config_json,
    )
    db.add(sched)
    await db.commit()
    await db.refresh(sched)

    # Register in the scheduler if active
    if sched.active:
        scheduler_service.add_job(sched)

    return sched


@router.put("/schedules/{schedule_id}", response_model=ScheduledTaskOut)
async def update_schedule(schedule_id: int, body: ScheduledTaskUpdate, db: AsyncSession = Depends(get_db)):
    """Update a scheduled ETL task."""
    result = await db.execute(
        select(ScheduledTask).where(ScheduledTask.id == schedule_id)
    )
    sched = result.scalar_one_or_none()
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(sched, key, value)

    await db.commit()
    await db.refresh(sched)

    # Re-register or remove from scheduler
    scheduler_service.remove_job(schedule_id)
    if sched.active:
        scheduler_service.add_job(sched)

    return sched


@router.delete("/schedules/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a scheduled ETL task."""
    result = await db.execute(
        select(ScheduledTask).where(ScheduledTask.id == schedule_id)
    )
    sched = result.scalar_one_or_none()
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found")

    scheduler_service.remove_job(schedule_id)
    await db.delete(sched)
    await db.commit()


@router.post("/schedules/{schedule_id}/run")
async def run_schedule_now(schedule_id: int, db: AsyncSession = Depends(get_db)):
    """Manually trigger a scheduled ETL task right now (all connections in scope)."""
    result = await db.execute(
        select(ScheduledTask).where(ScheduledTask.id == schedule_id)
    )
    sched = result.scalar_one_or_none()
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found")

    connection_ids = list(sched.connection_ids or []) or [sched.connection_id]
    task_ids = await scheduler_service.run_schedule_now(connection_ids)
    return {
        "task_ids": task_ids,
        "connection_ids": connection_ids,
        "schedule_name": sched.name,
    }


# ═══════════════════════════════════════════════════════════════════════
# Extraction History & Server-Side Bronze DataGrid
# ═══════════════════════════════════════════════════════════════════════

class ExtractionHistoryQueryIn(BaseModel):
    offset: int = 0
    limit: int = 100
    search: str = ""
    filters: dict[str, Any] = {}
    sort_field: str | None = None
    sort_direction: str = "asc"


def _qi_trino(identifier: str) -> str:
    """Escape Trino identifier safely."""
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


@router.get("/history")
async def get_extraction_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    connection_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Lista o histórico de execuções de extração (ETL tasks)."""
    from app.models.etl_task import EtlTask
    query = (
        select(
            EtlTask,
            SourceConnection.code.label("connection_code"),
            SourceConnection.name.label("connection_name"),
            SourceConnection.connector_type.label("connector_type"),
        )
        .outerjoin(SourceConnection, SourceConnection.id == EtlTask.connection_id)
        .where(EtlTask.task_type == "extraction")
    )
    if connection_id:
        query = query.where(EtlTask.connection_id == connection_id)

    query = query.order_by(EtlTask.id.desc()).offset(offset).limit(limit)
    rows = (await db.execute(query)).all()

    result = []
    for task_row, conn_code, conn_name, connector_type in rows:
        result.append({
            "id": task_row.id,
            "status": task_row.status,
            "connection_id": task_row.connection_id,
            "connection_code": conn_code,
            "connection_name": conn_name,
            "connector_type": connector_type,
            "total_blobs": task_row.total_blobs,
            "processed_blobs": task_row.processed_blobs,
            "rows_raw": task_row.rows_raw,
            "rows_normalized": task_row.rows_normalized,
            "rows_destination": task_row.rows_destination,
            "phase": task_row.phase,
            "errors": task_row.errors or [],
            "started_at": task_row.started_at,
            "finished_at": task_row.finished_at,
            "billing_periods": task_row.billing_periods or [],
            "result_json": task_row.result_json,
        })
    return result


@router.get("/history/{task_id}/metadata")
async def get_extraction_history_metadata(task_id: int, db: AsyncSession = Depends(get_db)):
    """Retorna metadados e colunas da tabela Bronze para visualização da tarefa."""
    from app.models.etl_task import EtlTask
    from app.services import trino_service as trino

    task = await db.get(EtlTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")

    conn = await db.get(SourceConnection, task.connection_id) if task.connection_id else None
    if not conn:
        raise HTTPException(status_code=404, detail="Conexão associada não encontrada")

    safe_code = conn.code.replace("-", "_").replace(".", "_")
    table_name = f"raw_{safe_code}"

    # Obter colunas via Trino
    try:
        cols_raw = await trino.execute(f"DESCRIBE iceberg.bronze.{_qi_trino(table_name)}")
        columns = [r["Column"] for r in cols_raw if r.get("Column")]
    except Exception as exc:
        logger.warning("Falha ao descrever tabela Bronze %s no Trino: %s", table_name, exc)
        columns = []

    # Obter contagem total de linhas
    total_count = task.rows_raw or 0
    try:
        task_periods = [str(period) for period in (task.billing_periods or []) if str(period).isdigit() and len(str(period)) == 6]
        period_where = ""
        if task_periods:
            period_values = ", ".join(f"'{period}'" for period in task_periods)
            period_where = f" WHERE CAST(_billing_period AS VARCHAR) IN ({period_values})"
        count_res = await trino.execute(
            f"SELECT COUNT(*) AS total FROM iceberg.bronze.{_qi_trino(table_name)}{period_where}"
        )
        if count_res and "total" in count_res[0]:
            total_count = int(count_res[0]["total"])
    except Exception:
        pass

    return {
        "task_id": task.id,
        "connection_id": conn.id,
        "connection_code": conn.code,
        "connection_name": conn.name,
        "connector_type": conn.connector_type,
        "table_name": f"iceberg.bronze.{table_name}",
        "columns": columns,
        "total_rows": total_count,
        "status": task.status,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "phase": task.phase,
    }


@router.post("/history/{task_id}/query")
async def query_extraction_history_data(
    task_id: int,
    body: ExtractionHistoryQueryIn,
    db: AsyncSession = Depends(get_db),
):
    """Consulta paginada com filtros e busca server-side via Trino para o DataGrid da tarefa."""
    from app.models.etl_task import EtlTask
    from app.services import trino_service as trino

    task = await db.get(EtlTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")

    conn = await db.get(SourceConnection, task.connection_id) if task.connection_id else None
    if not conn:
        raise HTTPException(status_code=404, detail="Conexão associada não encontrada")

    safe_code = conn.code.replace("-", "_").replace(".", "_")
    table_name = f"raw_{safe_code}"

    # Descrever tabela para saber colunas válidas
    try:
        cols_raw = await trino.execute(f"DESCRIBE iceberg.bronze.{_qi_trino(table_name)}")
        valid_cols = {r["Column"] for r in cols_raw if r.get("Column")}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao acessar tabela Bronze no Trino: {exc}")

    where_clauses = []

    # Extraction tasks are monthly slices. Keep the history viewer scoped to
    # the periods recorded by that task instead of returning the whole source.
    task_periods = [str(period) for period in (task.billing_periods or []) if str(period).isdigit() and len(str(period)) == 6]
    if task_periods and "_billing_period" in valid_cols:
        period_values = ", ".join(f"'{period}'" for period in task_periods)
        where_clauses.append(f"CAST({_qi_trino('_billing_period')} AS VARCHAR) IN ({period_values})")

    # 1. Filtro global de texto
    if body.search and body.search.strip():
        search_term = body.search.strip().replace("'", "''")
        # Aplicar busca em até 10 colunas textuais principais
        search_candidates = [c for c in valid_cols if not c.startswith("_")][:10]
        if search_candidates:
            search_parts = [f"LOWER(CAST({_qi_trino(c)} AS VARCHAR)) LIKE '%{search_term.lower()}%'" for c in search_candidates]
            where_clauses.append(f"({' OR '.join(search_parts)})")

    # 2. Filtros de coluna do DataGrid
    if body.filters:
        for field, filter_def in body.filters.items():
            if field not in valid_cols:
                continue
            if isinstance(filter_def, dict):
                op = filter_def.get("operator", "equals")
                val = filter_def.get("value")
            else:
                op = "equals"
                val = filter_def

            if val is None or val == "":
                continue

            escaped_col = _qi_trino(field)
            if op == "equals":
                val_escaped = str(val).replace("'", "''")
                where_clauses.append(f"CAST({escaped_col} AS VARCHAR) = '{val_escaped}'")
            elif op == "notEquals":
                val_escaped = str(val).replace("'", "''")
                where_clauses.append(f"(CAST({escaped_col} AS VARCHAR) != '{val_escaped}' OR {escaped_col} IS NULL)")
            elif op == "contains":
                val_escaped = str(val).replace("'", "''").lower()
                where_clauses.append(f"LOWER(CAST({escaped_col} AS VARCHAR)) LIKE '%{val_escaped}%'")
            elif op == "isAnyOf" and isinstance(val, (list, tuple)):
                items = []
                for v in val:
                    if v:
                        v_str = str(v).replace("'", "''")
                        items.append(f"'{v_str}'")
                if items:
                    items_str = ", ".join(items)
                    where_clauses.append(f"CAST({escaped_col} AS VARCHAR) IN ({items_str})")

    where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    # Ordenação
    order_sql = ""
    if body.sort_field and body.sort_field in valid_cols:
        direction = "DESC" if body.sort_direction.lower() == "desc" else "ASC"
        order_sql = f" ORDER BY {_qi_trino(body.sort_field)} {direction}"
    elif "_ingested_at" in valid_cols:
        order_sql = " ORDER BY _ingested_at DESC"
    elif "_billing_period" in valid_cols:
        order_sql = " ORDER BY _billing_period DESC"

    # Query de registros
    limit = max(1, min(body.limit, 500))
    offset = max(0, body.offset)
    sql_data = f"SELECT * FROM iceberg.bronze.{_qi_trino(table_name)}{where_sql}{order_sql} OFFSET {offset} LIMIT {limit}"

    # Query de contagem total
    sql_count = f"SELECT COUNT(*) AS total FROM iceberg.bronze.{_qi_trino(table_name)}{where_sql}"

    try:
        records = await trino.execute(sql_data, timeout_s=60)
        count_res = await trino.execute(sql_count, timeout_s=60)
        total_rows = int(count_res[0]["total"]) if count_res and "total" in count_res[0] else len(records)
    except Exception as exc:
        logger.error("Erro ao executar query Bronze no Trino: %s", exc)
        raise HTTPException(status_code=500, detail=f"Erro ao consultar Trino: {exc}")

    columns_list = list(valid_cols)
    # Ordenar colunas colocando as principais no início
    priority_order = ["_billing_period", "_blob_path", "product/service", "cost/myCost", "usage/billedQuantity", "lineItem/intervalUsageStart"]
    columns_list.sort(key=lambda c: priority_order.index(c) if c in priority_order else 999)

    return {
        "task_id": task_id,
        "connection_code": conn.code,
        "connection_name": conn.name,
        "table_name": f"iceberg.bronze.{table_name}",
        "columns": columns_list,
        "records": records,
        "total_records": total_rows,
        "offset": offset,
        "limit": limit,
    }
