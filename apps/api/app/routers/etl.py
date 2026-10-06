import logging
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
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
from app.services.cost_sources import mapping_rejection_reason
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

# Downloads must never materialize an unbounded Bronze snapshot in the API
# process.  CSV is streamed in batches; XLSX has a lower cap because the
# workbook writer necessarily buffers the document in memory.
DOWNLOAD_CSV_MAX_ROWS = int(os.getenv("OPENFINOPS_DOWNLOAD_CSV_MAX_ROWS", "1000000"))
DOWNLOAD_XLSX_MAX_ROWS = int(os.getenv("OPENFINOPS_DOWNLOAD_XLSX_MAX_ROWS", "200000"))


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
    from app.services.worker_runtime import aggregate_worker_health

    return aggregate_worker_health()


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
    from app.services.iceberg_service import read_bronze, read_bronze_batches
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

    max_rows = DOWNLOAD_XLSX_MAX_ROWS if format == "xlsx" else DOWNLOAD_CSV_MAX_ROWS
    known_rows = int(blob.row_count or 0)
    if known_rows > max_rows:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Arquivo possui {known_rows:,} registros; o limite para download "
                f"{format.upper()} é {max_rows:,}. Use a consulta paginada ou reduza a fonte."
            ),
        )

    safe_code = re.sub(r'[^a-zA-Z0-9_\-]', '_', conn_code or 'origem')
    period = blob.billing_period or 'period'
    filename = f"origem_{safe_code}_{period}.{format}"

    if format == "xlsx":
        try:
            loop = asyncio.get_running_loop()
            df = await loop.run_in_executor(
                None,
                lambda: read_bronze(
                    conn_code,
                    blob_path=blob.blob_path,
                    billing_period=blob.billing_period,
                    max_rows=max_rows + 1,
                )
            )
        except Exception as e:
            logger.exception("Failed to read bronze data for blob %d: %s", blob_id, e)
            raise HTTPException(status_code=500, detail=f"Erro ao ler camada Bronze: {e}")
        if df.empty:
            raise HTTPException(status_code=404, detail="Nenhum dado encontrado na camada Bronze para este blob")
        if len(df) > max_rows:
            raise HTTPException(status_code=413, detail=f"Arquivo excede o limite de {max_rows:,} registros para XLSX")
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Bronze_Origem")
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )

    def csv_stream():
        header = True
        found = False
        for frame in read_bronze_batches(
            conn_code,
            blob_path=blob.blob_path,
            billing_period=blob.billing_period,
            max_rows=max_rows,
            batch_size=25_000,
        ):
            found = True
            yield frame.to_csv(index=False, header=header, encoding="utf-8-sig").encode("utf-8-sig")
            header = False
        if not found:
            logger.info("Bronze blob %d has no rows to download", blob_id)

    return StreamingResponse(
        csv_stream(),
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
            cfg = dict(config or {})
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
    """Choose Bronze blobs to reprocess for one connection."""
    from app.services.reprocess_blob_selection import select_reprocess_blobs

    return select_reprocess_blobs(
        candidates,
        connector_type=connector_type,
        preferred_table=preferred_table,
    )

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

    connection_id = int(target["source_connection_id"].iloc[0])
    snapshot_date = None
    for col in ("snapshot_date", "SnapshotDate"):
        if col in target.columns:
            values = target[col].dropna().astype(str).unique()
            if len(values) == 1:
                snapshot_date = str(values[0])
                break
    rows = iceberg.publish_silver_dataframe_slice(
        phys_name,
        target,
        connection_id=connection_id,
        billing_period=billing_period,
        snapshot_date=snapshot_date,
    )
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
    blob_path: str | None = None,
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
        _append_reprocess_log(
            task_id,
            f"Iniciando leitura do primeiro batch do blob {blob_id}",
            details={
                "blob_id": blob_id,
                "connection_id": connection_id,
                "billing_period": str(billing_period),
                "row_count": getattr(meta, "row_count", None),
                "batch_rows": int(os.getenv("REPROCESS_BATCH_ROWS", "100000")),
            },
        )
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
        else:
            # Physical Bronze slices may have no IngestedBlob manifest row.
            # Read them by provenance path instead of leaving source=None.
            source = iceberg.read_bronze_batches(
                connection_code, blob_path=blob_path,
                billing_period=billing_period,
                batch_size=int(os.getenv("REPROCESS_BATCH_ROWS", "100000")),
            )

        chunks: list[pd.DataFrame] = []
        rows_in_blob = 0
        chunk_rows = int(os.getenv("REPROCESS_CLASSIFICATION_CHUNK_ROWS", "50000"))
        cpu_workers = max(1, int(os.getenv("REPROCESS_CPU_WORKERS", "2")))
        use_process_pool = clone_engine is None and cpu_workers > 1
        source_batches = (
            source.iloc[start:start + chunk_rows].copy()
            for start in range(0, len(source), chunk_rows)
        ) if isinstance(source, pd.DataFrame) else source

        def _handle_transformed_target(target: pd.DataFrame, input_rows: int, clone_count: int = 0) -> None:
            nonlocal rows_in_blob
            if target.empty:
                return
            rows_in_blob += len(target)
            if rows_in_blob == len(target):
                _append_reprocess_log(
                    task_id,
                    f"Primeiro batch transformado do blob {blob_id}",
                    details={
                        "blob_id": blob_id,
                        "input_rows": input_rows,
                        "output_rows": len(target),
                        "clone_rows": clone_count,
                    },
                )
            if batch_callback is not None:
                batch_callback(target, input_rows)
            else:
                chunks.append(target)
            progress_text = (
                f"Bronze · {billing_period}: {rows_in_blob:,} linhas processadas ({clone_count:,} Returns no batch)"
            )
            status = _inmemory_tasks.get(task_id)
            if status is not None:
                status["current_blob"] = progress_text
                status["phase"] = progress_text
            session.execute(text("""
                UPDATE etl_task
                   SET current_blob = :phase, phase = :phase, last_progress_at = now()
                 WHERE id = :task_id AND status = 'RUNNING'
            """), {"phase": progress_text, "task_id": task_id})
            session.commit()

        if use_process_pool:
            from concurrent.futures import ProcessPoolExecutor, as_completed
            from app.services.reprocess_transform import build_chunk_payload, transform_reprocess_chunk

            with ProcessPoolExecutor(max_workers=cpu_workers) as executor:
                futures = {}
                for chunk in source_batches:
                    if _check_cancelled(task_id):
                        return None
                    payload = build_chunk_payload(
                        records=chunk.to_dict(orient="records"),
                        connection_id=connection_id,
                        connection_code=connection_code,
                        billing_period=str(billing_period),
                        brules=brules,
                        crules=crules,
                        cmapps=cmapps,
                        dept_cache=dept_cache,
                        hierarchy_cache=hierarchy_cache,
                        dest_col_names=dest_col_names,
                    )
                    futures[executor.submit(transform_reprocess_chunk, payload)] = len(chunk)
                for future in as_completed(futures):
                    if _check_cancelled(task_id):
                        return None
                    records = future.result()
                    if not records:
                        continue
                    _handle_transformed_target(pd.DataFrame(records), futures[future])
        else:
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

                # ``consumo_realizado`` is the provider/rule result.  Keep a
                # direct realized value available for clouds without a
                # contractual settlement; the contract materializer may
                # replace it later for BUDGET_COMMITMENT rows.
                if "realizado_cloud" in target.columns:
                    target["consumo_realizado"] = pd.to_numeric(target["realizado_cloud"], errors="coerce")
                if "consumo_realizado" in target.columns:
                    target["realizado_cloud"] = pd.to_numeric(target["consumo_realizado"], errors="coerce")
                    target["realizado"] = pd.to_numeric(target["consumo_realizado"], errors="coerce")
                    target["ajuste_contratual"] = 0.0

                # Phase 3: Classifications operate on the mapped destination.
                if crules:
                    _apply_classifications_sync(
                        target, crules, None,
                        dept_map=dept_cache,
                        hierarchy_cache=hierarchy_cache,
                    )
                    from app.services.task_runner import fill_inherited_org_fields
                    fill_inherited_org_fields(target, dept_cache)

                _handle_transformed_target(target, len(chunk), clone_count)

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
    publish_by_partition: bool = True,
    snapshot_dates: list[str] | None = None,
) -> dict:
    """Read normalized data blob-by-blob via raw JSON → pandas → Iceberg. Granular progress."""
    import pandas as pd, json
    from decimal import Decimal
    from app.database import SyncSession
    from app.models.source_connection import SourceConnection
    from app.models.ingested_blob import IngestedBlob
    from app.models.destination import DestinationTable, DestinationColumn, SourceDestinationMapping
    from app.models.rules import BusinessRule, ClassificationRule, Department
    from app.models.clone_rule import CloneRule, ClonePricePendencies
    from app.models.etl_task import EtlTask
    from sqlalchemy import select, text, func
    from app.services import iceberg_service as iceberg
    from app.services import gold_builder
    from app.services.contract_settlement import (
        organizational_budget_commitments,
        organizational_budget_owners,
        normalize_cloud_scope,
        period_within_contract,
    )
    from app.services.contract_settlement_frame import (
        assign_contract_budget_ids_frame,
        settle_rows_frame,
    )
    from app.models.financial_contract import FinancialContract, FinancialContractBudgetScope
    from app.models.planning import PlanningEntry, PlanningVersion, FinancialType
    from app.services.destination_slice import (
        resolve_destination_slice_config,
        snapshot_date_from_blob_path,
    )
    from app.services.task_runner import _inmemory_tasks, _check_cancelled
    from app.services.cloud_scope import (
        allowed_rule_cloud_scopes,
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
                row.last_progress_at = func.now()
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
                    "gold_table": status.get("gold_table"),
                    "gold_rows": status.get("gold_rows"),
                    "gold_error": status.get("gold_error"),
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
        partition_spec_json = getattr(dest_info, "partition_spec_json", None)
        snapshot_dates = [str(s) for s in (snapshot_dates or [])]
        if not publish_by_partition:
            logger.warning(
                "publish_by_partition=False ignorado para %s; publicação por fatia é obrigatória.",
                phys_name,
            )
        publish_by_partition = True
        is_focus_destination = (
            (dest_info.name or "").strip().upper() == "MODELO FOCUS"
            or (phys_name or "").strip().lower() == "dest_modelo_focus"
        )
        # O cubo Gold agregado é gerado ao final do reprocess SOMENTE para o
        # destino MODELO FOCUS, reutilizando os DataFrames em memória (não
        # varre a Gold de milhões de linhas).
        # Estrutura Gold única = regras (gold_rule + gold_materialization),
        # geradas sob demanda via /gold/reprocess. A geração automática
        # paralela (gold_builder -> dest_dm_cost_focus) foi desativada.
        enable_gold_cubes = False
        gold_chunks: list = []

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
        from app.services.destination_slice import primary_partition_column

        slice_cfg = resolve_destination_slice_config(
            phys_name, partition_spec_json, column_names=dest_col_names,
        )
        slice_period_col = primary_partition_column(slice_cfg)
        resolved_snapshot = snapshot_dates[0] if snapshot_dates else None

        # Gather Bronze blobs per connection. Azure deduplicates stale export
        # executions; GCP keeps the configured current BigQuery table.
        all_blobs: list[tuple[int, int, str, str]] = []  # (blob_id, connection_id, billing_period, blob_path)
        physical_expected_rows: dict[int, int] = {}
        physical_blob_seq = -1
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
            elif phys_name.lower() == "dest_modelo_focus" and conn:
                from app.services.cost_sources import bronze_blob_eligible_for_focus

                candidates = [
                    c for c in candidates
                    if bronze_blob_eligible_for_focus(conn, c[2])
                ]
            # The SQL manifest is only an index. After a crash it may contain
            # fewer rows than the immutable Bronze table. Reconcile it with
            # physical provenance before selecting Azure's latest run.
            physical = []
            if billing_periods:
                for period in billing_periods:
                    physical.extend(iceberg.list_bronze_slices(conn.code if conn else str(cid), str(period)))
            if phys_name.lower() == "dest_modelo_focus" and conn:
                from app.services.cost_sources import bronze_blob_eligible_for_focus

                physical = [
                    item for item in physical
                    if bronze_blob_eligible_for_focus(conn, str(item.get("blob_path") or ""))
                ]
            if physical:
                manifest_paths = {str(row[2]) for row in candidates}
                physical_paths = {str(item["blob_path"]) for item in physical}
                if physical_paths - manifest_paths:
                    by_path = {str(row[2]): row for row in candidates}
                    for item in physical:
                        path = str(item["blob_path"])
                        if path not in by_path:
                            synthetic_id = physical_blob_seq
                            physical_blob_seq -= 1
                            by_path[path] = (synthetic_id, item["billing_period"], path, None)
                            physical_expected_rows[synthetic_id] = int(item["row_count"])
                    candidates = list(by_path.values())
            for blob_id, period, path in _select_reprocess_blobs(
                candidates,
                connector_type=connector_type,
                preferred_table=preferred_table,
            ):
                all_blobs.append((blob_id, cid, period, path))

        if snapshot_dates:
            allowed = set(snapshot_dates)
            all_blobs = [
                row for row in all_blobs
                if (snapshot_date_from_blob_path(row[3]) or "") in allowed
            ]

        total_blobs = len(all_blobs)
        blob_meta = {
            blob_id: session.get(IngestedBlob, blob_id)
            for blob_id, _, _, _ in all_blobs
        }
        expected_rows_by_blob = {
            blob_id: meta.row_count if meta is not None else None
            for blob_id, meta in blob_meta.items()
        }
        expected_rows_by_blob.update(physical_expected_rows)
        expected_input_rows = sum(
            int(value) for value in expected_rows_by_blob.values() if value is not None
        )
        status["total_blobs"] = total_blobs
        status["processed_blobs"] = 0
        status["rows_destination"] = 0
        _sync_db()
        _append_reprocess_log(task_id, f"Inventário concluído: {total_blobs} blobs e {expected_input_rows:,} linhas de entrada", details={"blobs": total_blobs, "input_rows": expected_input_rows})

        # Validate the complete immutable Bronze input before staging one Gold
        # batch.  A missing blob used to be discovered after tens of millions
        # of rows from earlier blobs had already been processed.
        bronze_audit: list[dict] = []
        status["current_blob"] = f"Validando Bronze: {total_blobs} arquivo(s)"
        _sync_db()
        for blob_id, cid, bperiod, blob_path in all_blobs:
            conn = session.get(SourceConnection, cid)
            audit = iceberg.audit_bronze_blob(
                conn.code if conn else str(cid),
                blob_path=blob_path,
                billing_period=bperiod,
                expected_rows=expected_rows_by_blob.get(blob_id),
                batch_size=int(os.getenv("REPROCESS_BATCH_ROWS", "100000")),
                allow_consolidated_row_count=bool(conn and conn.connector_type == "oci"),
                allow_legitimate_empty=bool(conn and conn.connector_type in ("azure_blob", "mongodb_atlas", "claude_token")),
            )
            audit["blob_id"] = blob_id
            bronze_audit.append(audit)
        failed_audit = [item for item in bronze_audit if item["status"] != "PASSED"]
        if failed_audit:
            message = f"Preflight Bronze falhou em {len(failed_audit)}/{total_blobs} arquivo(s)"
            status.update(status="FAILED", error=message, current_blob=message)
            _sync_db()
            _append_reprocess_log(
                task_id, message, level="ERROR", details={"bronze_audit": failed_audit},
            )
            return {"success": False, "error": message, "rows_written": 0, "bronze_audit": bronze_audit}
        _append_reprocess_log(
            task_id, "Preflight Bronze concluído", details={"bronze_audit": bronze_audit},
        )

        # ── Pre-load connections, rules, and hierarchy for ALL connections BEFORE the thread pool ──
        # ORM objects are loaded in the main session and become detached after session.close().
        # Detached objects retain their loaded attributes (expire_on_commit=False), so
        # worker threads can safely read them without any DB interaction.
        conn_map: dict[int, SourceConnection] = {}
        conn_rules: dict[int, tuple] = {}  # cid → (brules, crules, dept_cache, hierarchy_cache)
        clone_engines: dict[int, object] = {}
        clone_reference_prices: dict[tuple[str, str], float] = {}
        if unique_cids:
            try:
                from app.services.trino_service import execute_sync as _trino_execute_sync
                _reference_rows = _trino_execute_sync(
                    "SELECT meter_id, reference_price_usd "
                    "FROM iceberg.bronze.raw_azure_price_reference "
                    "WHERE coalesce(needs_review, false) = false "
                    "AND try_cast(reference_price_usd AS DOUBLE) > 0",
                    timeout_s=30,
                )
                clone_reference_prices = {
                    ("azure", str(row["meter_id"])):
                    float(row["reference_price_usd"])
                    for row in _reference_rows if row.get("meter_id") and row.get("reference_price_usd")
                }
            except Exception as _price_catalog_error:
                logger.warning("Clone reference catalog unavailable; using observed prices only: %s", _price_catalog_error)
        unique_cids = set(cid for _, cid, _, _ in all_blobs)
        output_cols = {'organograma', 'centro_custo', 'id_orcamento',
                       'ambiente', 'custom_fields', 'subscription_rg',
                       'organograma_nivel_1', 'organograma_nivel_2',
                       'organograma_nivel_3', 'organograma_nivel_4',
                       'organograma_nivel_5', 'organograma_nivel_6',
                       'organograma_nivel_7',
                       'CostBRL', 'ContractedCost', 'ListCost', 'TaxBRL',
                       'EffectiveCost', 'Tax_Base', 'Tax_Marketplace',
                       'realizado_cloud', 'consumo_realizado', 'ajuste_contratual',
                       'financial_effect', 'financial_contract_id',
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
            "financial_effect": "TEXT",
            "financial_contract_id": "BIGINT",
            "consumo_realizado": "DOUBLE",
            "realizado_cloud": "DOUBLE",
            "ajuste_contratual": "DOUBLE",
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

            classification_destination_filter = ClassificationRule.destination_table_id == destination_table_id
            if is_focus_destination:
                classification_destination_filter = or_(
                    classification_destination_filter,
                    ClassificationRule.destination_table_id.is_(None),
                )
            _crules = [
                rule for rule in session.execute(
                    select(ClassificationRule).where(
                        ClassificationRule.active == True,
                        classification_destination_filter,
                        or_(
                            ClassificationRule.cloud_scope.is_(None),
                            ClassificationRule.cloud_scope.in_(
                                sorted(
                                    scope
                                    for scope in allowed_rule_cloud_scopes(conn.cloud_scope, conn.cloud_name)
                                    if scope is not None
                                )
                            ),
                        ),
                    ).order_by(ClassificationRule.priority, ClassificationRule.id)
                ).scalars().all()
                # Classifications are shared by provider, not by source
                # connection.  A rule created from Azure-4P must also classify
                # Azure-TI when its cloud_scope is Azure; connection_id is
                # legacy metadata for these rules and must not narrow Gold.
                if rule_matches_destination(
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
                clone_engines[cid] = CloneEngine(
                    clone_rules, destination_table_id, reference_prices=clone_reference_prices,
                )

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
        _BIGINT_COLS = {
            "clone_rule_id",
            "clone_rule_version",
            "source_connection_id",
            # Contract identifiers are nullable for direct-realized sources,
            # but must remain BIGINT whenever a financial rule assigns one.
            # Without the nullable integer dtype, pandas promotes batches
            # containing NULLs to float64 and Iceberg rejects long -> double.
            "financial_contract_id",
        }

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
            if slice_cfg.grain == "snapshot_date" and resolved_snapshot and slice_period_col in staged.columns:
                staged[slice_period_col] = resolved_snapshot
            return staged

        temp_name: str | None = None
        staged_rows = 0
        contractual_frames: list[pd.DataFrame] = []
        contract_validity_cache: dict[int, tuple[str, str]] = {}

        def _stage_frame(frame: pd.DataFrame) -> None:
            """Append one completed blob to the temporary Iceberg table."""
            nonlocal temp_name, staged_rows
            if enable_gold_cubes:
                # Reaproveita o frame em memória: acumula a agregação do cubo Gold
                # (principais colunas, mensal, SUM quantity/realizado/billedcost)
                # sem reler a Gold publicada.
                try:
                    gold_agg = gold_builder.aggregate_frame(frame)
                    if gold_agg is not None and not gold_agg.empty:
                        gold_chunks.append(gold_agg)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Agregação do cubo Gold falhou (Gold segue normal): %s", exc)
            prepared = _prepare_for_stage(frame)
            for start in range(0, len(prepared), 100_000):
                chunk = prepared.iloc[start:start + 100_000]
                if temp_name is None:
                    _append_reprocess_log(
                        task_id,
                        "Primeira escrita no staging iniciada",
                        details={"rows": len(chunk), "table": phys_name},
                    )
                    temp_name = iceberg.write_destination_staged(
                        phys_name,
                        chunk,
                        partition_col=slice_period_col,
                        column_types=dest_col_types,
                        namespace="silver",
                        partition_spec_json=partition_spec_json,
                        slice_config=slice_cfg,
                    )
                else:
                    iceberg.append_to_staged(
                        temp_name,
                        chunk,
                        partition_col=slice_period_col,
                        column_types=dest_col_types,
                        namespace="silver",
                    )
                staged_rows += len(chunk)
            del prepared

        # ── Phase 1: process one blob and one batch at a time ──
        total_rows_processed = 0
        errors: list[str] = []
        processed = 0
        from app.services.reprocess_memory import memory_pressure_reason

        def _assert_memory_available(location: str) -> None:
            reason = memory_pressure_reason()
            if reason:
                raise RuntimeError(f"{reason} antes de {location}; Gold anterior preservada")

        from app.services.csv_upload_service import is_current_billing_period, should_skip_gold_realizado

        skipped_gold_blobs = 0
        skipped_input_rows = 0
        for blob_id, cid, bperiod, blob_path in all_blobs:
            if _check_cancelled(task_id):
                status["status"] = "CANCELLED"
                return {"success": False, "error": "Cancelado", "rows_written": 0}
            _assert_memory_available(f"blob {blob_id}")
            try:
                status["current_blob"] = f"Lendo primeiro batch: blob {blob_id} ({bperiod})"
                _sync_db()
                _append_reprocess_log(
                    task_id,
                    f"Iniciando processamento do blob {blob_id}",
                    details={"blob_id": blob_id, "connection_id": cid, "billing_period": str(bperiod)},
                )
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
                            max_rows=min(int(expected_rows_by_blob.get(blob_id) or 5000), 5000),
                        )
                    except Exception:
                        # A physical Bronze slice may not have a manifest row;
                        # classification must still receive an empty frame,
                        # never None (which makes the skip predicate fail).
                        skip_sample = pd.DataFrame()
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
                    _assert_memory_available(f"batch do blob {blob_id}")
                    frame = frame.copy()
                    contractual = frame.get("financial_effect", pd.Series(index=frame.index)).astype(str).str.upper().eq("BUDGET_COMMITMENT")
                    if contractual.any():
                        contract_ids = {
                            int(value)
                            for value in pd.to_numeric(
                                frame.loc[contractual, "financial_contract_id"], errors="coerce"
                            ).dropna().unique()
                        } if "financial_contract_id" in frame.columns else set()
                        missing_contract_ids = contract_ids - set(contract_validity_cache)
                        if missing_contract_ids:
                            from app.models.financial_contract import FinancialContract
                            contract_validity_cache.update({
                                int(contract_id): (str(valid_from), str(valid_until))
                                for contract_id, valid_from, valid_until in session.execute(
                                    select(
                                        FinancialContract.id,
                                        FinancialContract.valid_from,
                                        FinancialContract.valid_until,
                                    ).where(FinancialContract.id.in_(missing_contract_ids))
                                ).all()
                            })
                        outside_validity = pd.Series(False, index=frame.index)
                        if contract_ids and "financial_contract_id" in frame.columns:
                            row_contract_ids = pd.to_numeric(
                                frame["financial_contract_id"], errors="coerce"
                            )
                            for contract_id in contract_ids:
                                validity = contract_validity_cache.get(contract_id)
                                if not validity:
                                    continue
                                outside_validity |= row_contract_ids.eq(contract_id) & (
                                    not period_within_contract(str(bperiod), *validity)
                                )
                        if outside_validity.any():
                            frame.loc[outside_validity, "financial_effect"] = "DIRECT_REALIZED"
                            frame.loc[outside_validity, "financial_contract_id"] = None
                            contractual = frame.get("financial_effect", pd.Series(index=frame.index)).astype(str).str.upper().eq("BUDGET_COMMITMENT")
                    if contractual.any():
                        if (~contractual).any():
                            _stage_frame(frame.loc[~contractual].copy())
                        contractual_frames.append(frame.loc[contractual].copy())
                    else:
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
                    blob_path=blob_path,
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
                # Transformations preserve row cardinality. The only allowed
                # expansion is a synthetic clone, and each active clone rule
                # can add at most one row for each input row. A prior OCI
                # reprocess published 70.6M rows from a 788K-row Bronze blob;
                # without an upper bound that silently inflated later forecast
                # runs. Abort before publication and keep the existing slice.
                clone_engine = clone_engines.get(cid)
                clone_rule_count = len(clone_engine.rules) if clone_engine else 0
                max_blob_output_rows = blob_input_rows * (1 + clone_rule_count)
                if blob_output_rows > max_blob_output_rows:
                    raise RuntimeError(
                        f"Expansão anormal no blob {blob_id} ({bperiod}): "
                        f"entrada {blob_input_rows:,}, saída {blob_output_rows:,}, "
                        f"limite {max_blob_output_rows:,} para {clone_rule_count} regra(s) de clone; "
                        "publicação abortada para preservar o destino anterior"
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
                    iceberg.cleanup_staged_table(temp_name, namespace="silver")
                return {"success": False, "error": error_msg, "rows_written": total_rows_processed}

        if errors:
            status.setdefault("errors", []).extend(errors)
        if total_rows_processed == 0:
            if temp_name:
                iceberg.cleanup_staged_table(temp_name, namespace="silver")
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
                iceberg.cleanup_staged_table(temp_name, namespace="silver")
            return {"success": False, "error": msg, "rows_written": 0}

        # Contractual rows must be settled after all batches of the month are
        # available; settling each batch independently would duplicate the
        # commitment and consume credits incorrectly.
        if contractual_frames:
            contractual = pd.concat(contractual_frames, ignore_index=True)
            # Settlement scopes use the canonical connection cloud (ibm,
            # oci, ...), not the human-readable connection code stored in
            # ``cloud_origem``.
            if connection_ids and connection_ids[0] in conn_map:
                contractual["cloud_origem"] = str(conn_map[connection_ids[0]].cloud_scope or contractual["cloud_origem"].iloc[0]).lower()
            contract_ids = {
                int(value)
                for value in pd.to_numeric(contractual["financial_contract_id"], errors="coerce").dropna().unique()
            }
            configured_scopes: dict[tuple[str, str], set[str]] = {}
            if contract_ids:
                for contract_id, cloud, budget_id in session.execute(
                    select(
                        FinancialContractBudgetScope.contract_id,
                        FinancialContractBudgetScope.cloud_scope,
                        FinancialContractBudgetScope.budget_id,
                    ).where(FinancialContractBudgetScope.contract_id.in_(contract_ids))
                ).all():
                    configured_scopes.setdefault(
                        (str(contract_id), normalize_cloud_scope(cloud)), set()
                    ).add(str(budget_id).strip())
            assign_contract_budget_ids_frame(contractual, configured_scopes)
            owners = organizational_budget_owners(
                dept_cache,
                {
                    (str(row.cloud_origem).strip().lower(), str(row.id_orcamento).strip())
                    for row in contractual[["cloud_origem", "id_orcamento"]].drop_duplicates().itertuples(index=False)
                },
            )
            missing_hierarchy = contractual.get(
                "organograma_nivel_1", pd.Series(index=contractual.index, dtype="object")
            ).fillna("").astype(str).str.strip().eq("")
            for index in contractual.index[missing_hierarchy]:
                scope = (
                    str(contractual.at[index, "cloud_origem"] or "").strip().lower(),
                    str(contractual.at[index, "id_orcamento"] or "").strip(),
                )
                owner_id = owners.get(scope)
                if owner_id is None:
                    raise ValueError(
                        f"cannot resolve unique organizational owner for contractual pool {scope[0]}/{scope[1]}"
                    )
                chain = []
                current = dept_cache.get(owner_id)
                seen = set()
                while current is not None and current.id not in seen:
                    seen.add(current.id)
                    chain.append(current)
                    current = dept_cache.get(current.parent_id) if current.parent_id else None
                chain.reverse()
                contractual.at[index, "organograma"] = chain[-1].name
                for level in range(1, 8):
                    contractual.at[index, f"organograma_nivel_{level}"] = (
                        chain[level - 1].name if len(chain) >= level else None
                    )
                for field in ("centro_custo", "ambiente"):
                    inherited = next(
                        (getattr(node, field) for node in reversed(chain) if getattr(node, field, None)),
                        None,
                    )
                    contractual.at[index, field] = inherited
            commitment_by_pool = {}
            budget_type = session.scalar(select(FinancialType).where(FinancialType.code == "orcamento"))
            if budget_type:
                pool_keys = {
                    (str(int(contract_id)), str(cloud).lower(), str(budget_id))
                    for contract_id, cloud, budget_id in contractual[
                        ["financial_contract_id", "cloud_origem", "id_orcamento"]
                    ].drop_duplicates().itertuples(index=False, name=None)
                    if pd.notna(contract_id) and str(budget_id or "").strip()
                }
                period = str(billing_periods[0]) if billing_periods else str(
                    contractual.get("_billing_period", pd.Series([""])).iloc[0]
                )
                year, month = int(period[:4]), int(period[4:])
                budget_entries = session.execute(
                    select(PlanningEntry.department_id, PlanningEntry.value)
                    .join(PlanningVersion, PlanningEntry.planning_version_id == PlanningVersion.id)
                    .where(
                        PlanningVersion.financial_type_id == budget_type.id,
                        PlanningVersion.year == year,
                        PlanningVersion.is_current.is_(True),
                        PlanningEntry.month == month,
                    )
                ).all()
                commitment_by_pool = organizational_budget_commitments(
                    pool_keys,
                    dept_cache,
                    budget_entries,
                )
            settled = settle_rows_frame(contractual, commitment_by_pool=commitment_by_pool)
            _stage_frame(settled)

        # Price gaps do not invalidate a reprocess; they remain auditable.
        for engine in clone_engines.values():
            for pending in engine.pendencies:
                session.add(ClonePricePendencies(**pending))
        missing_meter_ids = {
            str(pending.get("meter_id") or "")
            for engine in clone_engines.values() for pending in engine.pendencies
            if pending.get("meter_id")
        }
        if missing_meter_ids:
            from sqlalchemy import select as _price_select
            for meter_id in sorted(missing_meter_ids):
                active = session.execute(
                    _price_select(EtlTask.id).where(
                        EtlTask.task_type == "price_reference_collection",
                        EtlTask.status.in_(["QUEUED", "RUNNING"]),
                        EtlTask.result_json["meter_id"].as_string() == meter_id,
                    ).limit(1)
                ).scalar_one_or_none()
                if active is None:
                    session.add(EtlTask(
                        task_type="price_reference_collection", status="QUEUED",
                        connection_id=next((
                            p.get("connection_id") for engine in clone_engines.values()
                            for p in engine.pendencies if str(p.get("meter_id")) == meter_id
                        ), None),
                        connection_ids=[], billing_periods=[], errors=[],
                        result_json={"meter_id": meter_id, "provider": "azure", "requested_by": "clone_price_pendency"},
                        phase=f"Na fila: buscar preço de referência para {meter_id}",
                    ))
        session.commit()
        try:
            status["current_blob"] = "Publicando partição na camada Silver..."
            _sync_db()
            _append_reprocess_log(task_id, "Validação concluída; iniciando publicação na camada Silver")
            # The Iceberg commit does not emit batch progress. Without a
            # heartbeat the 15-minute stall watchdog kills a healthy publish.
            import threading
            stop_publish_heartbeat = threading.Event()

            def _publish_heartbeat() -> None:
                while not stop_publish_heartbeat.wait(30):
                    try:
                        beat = SyncSession()
                        beat.execute(text("""
                            UPDATE etl_task
                               SET last_progress_at = now()
                             WHERE id = :task_id AND status = 'RUNNING'
                        """), {"task_id": task_id})
                        beat.commit()
                        beat.close()
                    except Exception:
                        pass

            heartbeat = threading.Thread(target=_publish_heartbeat, daemon=True)
            heartbeat.start()

            # A queue task represents exactly one destination/source/month.
            # Publishing a whole staged destination forced a full Arrow/Pandas
            # materialization and allowed a late Bronze error after millions
            # of rows. Reject broad calls rather than silently merging slices.
            if len(connection_ids) != 1:
                raise RuntimeError(
                    "Reprocessamento deve publicar uma única origem por tarefa"
                )
            if slice_cfg.grain == "snapshot_date":
                if len(snapshot_dates) != 1:
                    raise RuntimeError(
                        "Reprocessamento diário deve publicar uma única origem+snapshot_date por tarefa"
                    )
                total_written = iceberg.publish_staged_partition(
                    phys_name,
                    temp_name,
                    snapshot_date=str(snapshot_dates[0]),
                    billing_period=str(billing_periods[0]) if billing_periods else None,
                    connection_id=int(connection_ids[0]),
                    namespace="silver",
                    partition_spec_json=partition_spec_json,
                    expected_row_count=staged_rows,
                )
            else:
                if len(billing_periods) != 1:
                    raise RuntimeError(
                        "Reprocessamento deve publicar uma única origem+mês por tarefa"
                    )
                total_written = iceberg.publish_staged_partition(
                    phys_name,
                    temp_name,
                    billing_period=str(billing_periods[0]),
                    connection_id=int(connection_ids[0]),
                    namespace="silver",
                    partition_spec_json=partition_spec_json,
                    expected_row_count=staged_rows,
                )

            # Marketplace MongoDB is published from USD source rows, but its
            # financial effect must be allocated from the matching Azure pool
            # in BRL after the normal Silver publication succeeds.
            if (
                int(connection_ids[0]) in {5, 11}
                and phys_name == "dest_modelo_focus"
                and len(billing_periods) == 1
            ):
                from app.services.marketplace_allocation_reprocess import (
                    apply_mongodb_marketplace_allocation,
                )
                allocation = apply_mongodb_marketplace_allocation(
                    str(billing_periods[0]), connection_id=int(connection_ids[0])
                )
                _append_reprocess_log(
                    task_id,
                    "Rateio Marketplace MongoDB aplicado: "
                    f"{allocation['allocated_brl']:.2f} BRL em {allocation['rows']:,} linhas",
                )
        except Exception as stage_err:
            status["current_blob"] = f"Falha na escrita Iceberg ({stage_err}), revertendo..."
            _sync_db()
            if temp_name:
                iceberg.cleanup_staged_table(temp_name)
            raise stage_err
        finally:
            stop_publish_heartbeat.set()

        # ── Gold agregado (só MODELO FOCUS): publica a pré-agregação mensal
        # gerada dos frames em memória. Se falhar, reconstrói da Gold (fallback)
        # para manter consistência; a Gold já publicada permanece a fonte da
        # verdade e o erro fica auditável no resultado da tarefa.
        gold_rows = 0
        gold_error = None
        if enable_gold_cubes:
            try:
                gold_rows = gold_builder.publish_gold_cubes(gold_chunks)
                status["gold_rows"] = gold_rows
                status["gold_table"] = gold_builder.GOLD_TABLE
                status["current_blob"] = (
                    f"Concluido: {total_written} linhas na Gold, "
                    f"{gold_rows:,} linhas no cubo Gold ({gold_builder.GOLD_TABLE})"
                )
                _append_reprocess_log(
                    task_id,
                    f"Gold publicada: {gold_rows:,} linhas agregadas",
                    details={"gold_table": gold_builder.GOLD_TABLE, "gold_rows": gold_rows},
                )
            except Exception as exc:  # noqa: BLE001
                gold_error = str(exc)
                try:
                    gold_rows = gold_builder.rebuild_gold_cubes()
                    gold_error = None
                    status["gold_rows"] = gold_rows
                    status["gold_table"] = gold_builder.GOLD_TABLE
                    _append_reprocess_log(
                        task_id,
                        f"Gold reconstruído da Gold (fallback): {gold_rows:,} linhas",
                        details={"gold_table": gold_builder.GOLD_TABLE, "gold_rows": gold_rows},
                    )
                except Exception as exc2:  # noqa: BLE001
                    gold_error = f"{gold_error} | rebuild: {exc2}"
                    status["gold_error"] = gold_error
                    status["gold_rows"] = 0
                    _append_reprocess_log(task_id, f"Gold falhou: {gold_error}", level="ERROR")
            _sync_db()

        status["status"] = "COMPLETED"
        status["rows_destination"] = total_written
        if not status.get("current_blob"):
            status["current_blob"] = f"Concluido: {total_written} linhas, {len(all_blobs)} blobs processados"
        _sync_db()
        _append_reprocess_log(task_id, "Reprocessamento concluído e Gold publicada", details={"rows_written": total_written, "blobs_processed": len(all_blobs), "table": dest_info.name})
        return {"success": True, "rows_written": total_written, "table": dest_info.name,
                "gold_table": gold_builder.GOLD_TABLE if enable_gold_cubes else None,
                "gold_rows": gold_rows if enable_gold_cubes else None,
                "gold_error": gold_error}

    except Exception as e:
        import traceback
        status["status"] = "FAILED"; status["error"] = str(e); status["current_blob"] = f"Erro: {e}"
        error_traceback = traceback.format_exc()
        traceback.print_exc()
        _sync_db()
        _append_reprocess_log(
            task_id,
            f"Falha no reprocessamento: {e}",
            level="ERROR",
            details={"exception_type": type(e).__name__},
        )
        return {
            "success": False,
            "error": str(e) or type(e).__name__,
            "rows_written": 0,
            "failure_context": {
                "exception_type": type(e).__name__,
                "traceback": error_traceback[-12000:],
                "phase": status.get("phase"),
                "current_blob": status.get("current_blob"),
            },
        }
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


def _audit_reprocess_group_sync(
    task_id: int,
    destination_table_id: int,
    connection_ids: list[int],
    billing_periods: list[str],
) -> dict:
    """Fully read Bronze before releasing any child publication in a group."""
    from app.database import SyncSession
    from app.models.etl_task import EtlTask
    from app.services import iceberg_service as iceberg

    session = SyncSession()
    try:
        from app.services.cost_sources import (
            bronze_blob_eligible_for_focus,
            destination_requires_cost_source,
        )

        stmt = (
            select(IngestedBlob, SourceConnection)
            .join(SourceConnection, SourceConnection.id == IngestedBlob.connection_id)
            .where(IngestedBlob.connection_id.in_(connection_ids))
            .order_by(IngestedBlob.billing_period, IngestedBlob.connection_id, IngestedBlob.blob_path)
        )
        if billing_periods:
            stmt = stmt.where(IngestedBlob.billing_period.in_(billing_periods))
        rows = session.execute(stmt).all()
        if destination_requires_cost_source(destination_table_id):
            rows = [
                (blob, conn)
                for blob, conn in rows
                if bronze_blob_eligible_for_focus(conn, blob.blob_path)
            ]
        audits: list[dict[str, Any]] = []
        for index, (blob, conn) in enumerate(rows, start=1):
            code = conn.code
            connector_type = conn.connector_type
            audit = iceberg.audit_bronze_blob(
                code,
                blob_path=blob.blob_path,
                billing_period=str(blob.billing_period),
                expected_rows=blob.row_count,
                batch_size=int(os.getenv("REPROCESS_BATCH_ROWS", "100000")),
                allow_consolidated_row_count=connector_type == "oci",
                allow_legitimate_empty=connector_type in ("azure_blob", "mongodb_atlas", "claude_token"),
            )
            audit.update({"blob_id": blob.id, "connection_id": blob.connection_id})
            audits.append(audit)
            task = session.get(EtlTask, task_id)
            if task:
                task.total_blobs = len(rows)
                task.processed_blobs = index
                task.phase = f"Auditando Bronze {index}/{len(rows)}: {blob.blob_path}"[:200]
                session.commit()
        failed = [item for item in audits if item.get("status") != "PASSED"]
        return {
            "success": not failed,
            "rows_written": 0,
            "audited_blobs": len(audits),
            "bronze_audit": audits,
            "error": f"Auditoria Bronze bloqueada: {len(failed)}/{len(audits)} arquivo(s) inconsistente(s)" if failed else "",
        }
    finally:
        session.close()


def _reclassify_destination_sync(task_id: int, destination_table_id: int, connection_ids: list[int], billing_periods: list[str]) -> dict:
    """Rewrite Gold classification dimensions only; financial facts stay untouched."""
    import json
    from app.database import SyncSession
    from app.models.destination import DestinationTable
    from app.models.etl_task import EtlTask
    from app.models.rules import ClassificationRule, Department
    from app.models.source_connection import SourceConnection
    from app.services import iceberg_service as iceberg
    from app.services.cloud_scope import rule_matches_cloud, rule_matches_destination
    from app.services.task_runner import _apply_classifications_sync, _check_cancelled

    # Reclassification reads existing nullable BIGINT fields back through
    # pandas.  Without explicit hints, batches containing NULL identifiers are
    # inferred as float64 and Iceberg rejects the staged long -> double change.
    reclassification_column_types = {
        "clone_rule_id": "BIGINT",
        "clone_rule_version": "BIGINT",
        "source_connection_id": "BIGINT",
        "financial_contract_id": "BIGINT",
    }

    def _progress(phase: str, processed: int, expected: int) -> None:
        """Persist incremental progress; this task has no blob lifecycle."""
        progress = min(100, int((processed * 100) / expected)) if expected else 0
        db = SyncSession()
        try:
            task = db.get(EtlTask, task_id)
            if task and task.status != "CANCELLED":
                task.status = "RUNNING"
                task.rows_destination = processed
                task.total_blobs = expected
                task.processed_blobs = processed
                task.phase = f"{phase} · {processed:,}/{expected:,} linhas ({progress}%)"[:200]
                task.current_blob = task.phase[:500]
                result = dict(task.result_json or {})
                result.update({
                    "incremental": True,
                    "rows_processed": processed,
                    "estimated_total_rows": expected,
                    "progress_percent": progress,
                })
                task.result_json = result
                db.commit()
        finally:
            db.close()

    session = SyncSession()
    staged = None
    try:
        dest = session.get(DestinationTable, destination_table_id)
        if not dest:
            return {"success": False, "error": "Tabela destino nao encontrada", "rows_written": 0}
        depts = {d.id: d for d in session.execute(select(Department)).scalars().all()}
        is_focus = (dest.physical_name or "").lower() == "dest_modelo_focus"
        expected_total = sum(
            iceberg.estimate_destination_slice_rows(
                dest.physical_name,
                billing_period=str(period),
                connection_id=cid,
                partition_spec_json=getattr(dest, "partition_spec_json", None),
            )
            for cid in connection_ids
            for period in billing_periods
        )
        total = 0
        _progress("Preparando recorte Gold", total, expected_total)
        for cid in connection_ids:
            conn = session.get(SourceConnection, cid)
            if not conn:
                continue
            rules = [r for r in session.execute(select(ClassificationRule).where(ClassificationRule.active == True).order_by(ClassificationRule.priority, ClassificationRule.id)).scalars().all()
                     if rule_matches_destination(r.destination_table_id, destination_table_id, is_focus_destination=is_focus)
                     and rule_matches_cloud(r.cloud_scope, conn.cloud_scope, conn.cloud_name)]
            hierarchy_cache = {}
            for period in billing_periods:
                staged = None; rows = 0
                for frame in iceberg.read_destination_slice_batches(
                    dest.physical_name,
                    billing_period=str(period),
                    connection_id=cid,
                    batch_size=int(os.getenv("RECLASSIFY_BATCH_ROWS", "50000")),
                    partition_spec_json=getattr(dest, "partition_spec_json", None),
                ):
                    if _check_cancelled(task_id):
                        return {"success": False, "error": "Cancelado", "rows_written": 0}
                    was_classified = pd.Series(False, index=frame.index)
                    if "business_rule" in frame.columns:
                        def clear_classification(value):
                            try: payload = json.loads(value) if isinstance(value, str) and value else {}
                            except (TypeError, json.JSONDecodeError): return (value, False)
                            had_classification = "classification" in payload
                            payload.pop("classification", None)
                            return (json.dumps(payload, ensure_ascii=False) if payload else None, had_classification)
                        cleared = frame["business_rule"].map(clear_classification)
                        frame["business_rule"] = cleared.map(lambda item: item[0])
                        was_classified = cleared.map(lambda item: item[1])
                    # Never erase fields on rows owned only by business rules.
                    # Rows with prior classification (or empty dimensions) are
                    # safe to recompute from the current classification rules.
                    reset_mask = was_classified | frame.get("organograma", pd.Series(None, index=frame.index)).isna()
                    for col in ("organograma", "centro_custo", "id_orcamento", "ambiente", "custom_fields", "subscription_rg", "organograma_nivel_1", "organograma_nivel_2", "organograma_nivel_3", "organograma_nivel_4", "organograma_nivel_5", "organograma_nivel_6", "organograma_nivel_7"):
                        if col in frame.columns:
                            frame.loc[reset_mask, col] = None
                    _apply_classifications_sync(frame, rules, None, dept_map=depts, hierarchy_cache=hierarchy_cache)
                    if staged is None:
                        staged = iceberg.write_destination_staged(
                            dest.physical_name,
                            frame,
                            column_types=reclassification_column_types,
                            namespace="silver",
                            partition_spec_json=getattr(dest, "partition_spec_json", None),
                        )
                    else:
                        iceberg.append_to_staged(
                            staged,
                            frame,
                            column_types=reclassification_column_types,
                            namespace="silver",
                        )
                    rows += len(frame)
                    _progress(f"Reclassificando {conn.code}/{period}", total + rows, expected_total)
                if staged is None:
                    bronze_rows = int(session.scalar(
                        select(func.coalesce(func.sum(IngestedBlob.row_count), 0)).where(
                            IngestedBlob.connection_id == cid,
                            IngestedBlob.billing_period == str(period),
                        )
                    ) or 0)
                    if bronze_rows > 0:
                        return {
                            "success": False,
                            "error": (
                                f"Bronze possui {bronze_rows:,} linhas para {conn.code}/{period}, "
                                "mas não existe partição Silver publicada"
                            ),
                            "rows_written": total,
                            "failure_kind": "silver_partition_missing",
                        }
                    # No Bronze rows is a valid no-op; it should never be
                    # presented as a Silver failure.
                    message = f"Sem linhas Bronze para {conn.code}/{period}; nada a enviar ao Silver"
                    _progress(message, total, expected_total)
                    return {
                        "success": True,
                        "rows_written": total,
                        "table": dest.name,
                        "skipped": True,
                        "message": message,
                    }
                iceberg.publish_staged_partition(
                    dest.physical_name,
                    staged,
                    billing_period=str(period),
                    connection_id=cid,
                    namespace="silver",
                    partition_spec_json=getattr(dest, "partition_spec_json", None),
                    expected_row_count=rows,
                )
                total += rows
                staged = None
        _progress("Publicação Gold concluída", total, expected_total)
        return {"success": True, "rows_written": total, "table": dest.name}
    except Exception:
        if staged:
            iceberg.cleanup_staged_table(staged)
        raise
    finally:
        session.close()


@router.post("/reclassify")
async def reclassify_destination(body: ReprocessRequest, db: AsyncSession = Depends(get_db)):
    """Queue an incremental Gold-only classification refresh by source/month."""
    from app.models.etl_task import EtlTask
    from sqlalchemy import insert
    task_ids = []
    for cid in body.connection_ids:
        for period in body.billing_periods:
            result = await db.execute(insert(EtlTask).values(
                task_type="reclassification", status="QUEUED", destination_table_id=body.destination_table_id,
                connection_ids=[cid], billing_periods=[str(period)], rows_raw=0, rows_normalized=0,
                rows_destination=0, total_blobs=0, processed_blobs=0, errors=[],
                phase=f"Na fila: reclassificar fonte {cid}, mês {period}",
                result_json={"incremental": True, "connection_ids": [cid], "billing_periods": [str(period)]},
            ).returning(EtlTask.id))
            task_ids.append(result.scalar_one())
    await db.commit()
    return {"task_id": task_ids[0], "task_ids": task_ids, "message": "Reclassificação incremental enfileirada"}


@router.post("/reprocess")
async def reprocess_destination(body: ReprocessRequest, db: AsyncSession = Depends(get_db)):
    """Queue one atomic Gold publication per source and billing month."""
    from app.models.etl_task import EtlTask
    from sqlalchemy import insert

    from app.services.worker_runtime import any_worker_healthy

    if not any_worker_healthy():
        raise HTTPException(status_code=503, detail="Worker ETL indisponível; a tarefa não foi criada")

    active = await db.execute(
        select(EtlTask.id).where(
            EtlTask.task_type.in_(["bronze_audit", "reprocess"]),
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
                        "chain_gold_cubes": body.chain_gold_cubes.model_dump() if body.chain_gold_cubes else {},
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

    from app.models.destination import DestinationTable
    from app.services.destination_slice import resolve_destination_slice_config, snapshot_date_from_blob_path

    dest_row = (
        await db.execute(select(DestinationTable).where(DestinationTable.id == body.destination_table_id))
    ).scalar_one_or_none()
    if not dest_row:
        raise HTTPException(status_code=404, detail="Tabela destino não encontrada")
    slice_cfg = resolve_destination_slice_config(dest_row.physical_name, dest_row.partition_spec_json)
    reprocess_group_id = str(uuid4())
    phys_lower = (dest_row.physical_name or "").lower()

    def _blob_allowed(path: str) -> bool:
        if phys_lower == "dest_modelo_automation":
            return path.startswith("automation/jobs/")
        if phys_lower == "dest_modelo_automation_schedule":
            return path.startswith("automation/schedules/")
        if phys_lower == "dest_cloud_resource_inventory":
            return path.startswith("arg/inventory/")
        if phys_lower == "dest_cloud_orphan_resources":
            return path.startswith("arg/orphans/")
        return True

    ordered_jobs: list[tuple[int, str, str | None]] = []
    # Monthly destinations build this set below, while snapshot-date
    # destinations build their queue directly from the Bronze manifest.
    # Keep a common scope for the audit task so the latter never references
    # an uninitialized local variable.
    available: set[tuple[int, str]] = set()
    if slice_cfg.grain == "snapshot_date":
        blob_stmt = select(
            IngestedBlob.connection_id,
            IngestedBlob.billing_period,
            IngestedBlob.blob_path,
        ).where(IngestedBlob.connection_id.in_(body.connection_ids))
        # Empty Bronze downloads remain catalogued for auditability but must
        # never create a Silver reprocess job.
        blob_stmt = blob_stmt.where(IngestedBlob.row_count > 0)
        if body.billing_periods:
            blob_stmt = blob_stmt.where(IngestedBlob.billing_period.in_(body.billing_periods))
        seen_daily: set[tuple[int, str]] = set()
        for connection_id, period, path in (await db.execute(blob_stmt)).all():
            if connection_id is None or not period or not path:
                continue
            path_str = str(path)
            if not _blob_allowed(path_str):
                continue
            if mapping_rejection_reason(conn_by_id.get(int(connection_id)), body.destination_table_id):
                continue
            snap = snapshot_date_from_blob_path(path_str)
            if not snap:
                continue
            key = (int(connection_id), snap)
            if key in seen_daily:
                continue
            seen_daily.add(key)
            ordered_jobs.append((int(connection_id), str(period), snap))
        ordered_jobs.sort(key=lambda item: (item[2] or "", item[0]))
        available = {(connection_id, period) for connection_id, period, _ in ordered_jobs}
        empty_detail = (
            "Nenhum blob Bronze diário encontrado para as fontes selecionadas. "
            "Importe a origem primeiro (Extração)."
        )
        enqueue_label = "fonte+dia"
    else:
        available_stmt = select(IngestedBlob.connection_id, IngestedBlob.billing_period).where(
            IngestedBlob.connection_id.in_(body.connection_ids),
            IngestedBlob.row_count > 0,
        )
        if body.billing_periods:
            available_stmt = available_stmt.where(IngestedBlob.billing_period.in_(body.billing_periods))
        available = {
            (int(connection_id), str(period))
            for connection_id, period in (await db.execute(available_stmt)).all()
            if connection_id is not None and period
        }
        # Iceberg Bronze is the source of truth. ``ingested_blob`` is only a
        # catalog/index and may lag after a crash or historical migration, so
        # explicitly requested periods absent from that index are discovered
        # from the physical Bronze table before deciding the request is empty.
        if body.billing_periods:
            from app.services import trino_service as _trino
            for connection_id in body.connection_ids:
                source = conn_by_id.get(int(connection_id))
                if not source or not source.code:
                    continue
                bronze_name = "raw_" + re.sub(r"[^a-zA-Z0-9_]", "_", source.code)
                for period in body.billing_periods:
                    period = str(period)
                    pair = (int(connection_id), period)
                    if pair in available or not re.fullmatch(r"\d{6}", period):
                        continue
                    try:
                        physical_rows = await _trino.execute(
                            f"SELECT count(*) AS row_count FROM iceberg.bronze.{bronze_name} "
                            f"WHERE _billing_period = '{period}'",
                            timeout_s=30,
                        )
                        if physical_rows and int(physical_rows[0].get("row_count") or 0) > 0:
                            available.add(pair)
                    except Exception as exc:
                        logger.warning(
                            "Não foi possível verificar Bronze físico para conexão %s/mês %s: %s",
                            connection_id, period, exc,
                        )
        # A financial reprocess invalidates downstream contractual balances.
        # Rebuild every later Bronze month in the same ordered queue group so
        # credits, overage, Silver and the eventual Gold all share one input
        # horizon. This is deliberately limited to contracts attached to the
        # selected source(s); ordinary reprocesses keep their requested range.
        if body.billing_periods and body.destination_table_id:
            from app.models.financial_contract import (
                FinancialContract,
                FinancialContractConnection,
            )
            requested_start = min(str(period) for period in body.billing_periods)
            affected_contracts = list((await db.execute(
                select(FinancialContract).join(FinancialContractConnection).where(
                    FinancialContract.active.is_(True),
                    FinancialContractConnection.connection_id.in_(body.connection_ids),
                    FinancialContract.valid_until >= requested_start,
                ).distinct()
            )).scalars().all())
            for contract in affected_contracts:
                # Reprocessing months before the contract starts cannot
                # change its opening balance. Fan out only from the first
                # explicitly selected month inside the contract term.
                contract_requested_periods = [
                    str(period) for period in body.billing_periods
                    if contract.valid_from <= str(period) <= contract.valid_until
                ]
                if not contract_requested_periods:
                    continue
                contract_requested_start = min(contract_requested_periods)
                contract_connection_ids = list((await db.execute(
                    select(FinancialContractConnection.connection_id).where(
                        FinancialContractConnection.contract_id == contract.id
                    )
                )).scalars().all())
                if not contract_connection_ids:
                    continue
                contract_period_rows = (await db.execute(
                    select(IngestedBlob.connection_id, IngestedBlob.billing_period).where(
                        IngestedBlob.connection_id.in_(contract_connection_ids),
                        IngestedBlob.row_count > 0,
                        IngestedBlob.billing_period >= contract_requested_start,
                        IngestedBlob.billing_period <= contract.valid_until,
                    ).distinct()
                )).all()
                available.update(
                    (int(connection_id), str(period))
                    for connection_id, period in contract_period_rows
                    if connection_id is not None and period
                )
                missing_conn_ids = set(contract_connection_ids) - set(conn_by_id)
                if missing_conn_ids:
                    extra_connections = (await db.execute(
                        select(SourceConnection).where(SourceConnection.id.in_(missing_conn_ids))
                    )).scalars().all()
                    conn_by_id.update({connection.id: connection for connection in extra_connections})
        rejected_pairs = {
            (connection_id, period)
            for connection_id, period in available
            if mapping_rejection_reason(conn_by_id.get(connection_id), body.destination_table_id)
        }
        if rejected_pairs:
            logger.warning(
                "Reprocess do destino %s ignorou fontes operacionais não-custo: %s",
                body.destination_table_id,
                sorted({cid for cid, _ in rejected_pairs}),
            )
            available -= rejected_pairs
        ordered_jobs = [
            (connection_id, period, None)
            for period in sorted({period for _, period in available})
            for connection_id in sorted({cid for cid, _ in available})
            if (connection_id, period) in available
        ]
        empty_detail = (
            "Nenhum blob Bronze encontrado para as fontes e meses selecionados. "
            "Importe a origem primeiro (Extração). Se a extração falhou, o Bronze "
            "fica vazio e o reprocesso não tem meses para publicar."
        )
        enqueue_label = "fonte+mês"

    if not ordered_jobs:
        raise HTTPException(status_code=422, detail=empty_detail)

    audit_result = await db.execute(
        insert(EtlTask).values(
            task_type="bronze_audit", status="QUEUED", destination_table_id=body.destination_table_id,
            connection_ids=sorted({connection_id for connection_id, _ in available}),
            billing_periods=sorted({period for _, period, _ in ordered_jobs}),
            result_json={"reprocess_group_id": reprocess_group_id, "audit_scope": "reprocess_group"},
            total_blobs=0, processed_blobs=0, rows_raw=0, rows_normalized=0,
            rows_destination=0, errors=[], phase="Na fila: auditoria física Bronze",
        ).returning(EtlTask.id)
    )
    audit_task_id = audit_result.scalar_one()
    task_ids: list[int] = []
    for position, (connection_id, period, snapshot_date) in enumerate(ordered_jobs, start=1):
        details = {
            "connection_ids": [connection_id],
            "billing_periods": [period],
            "publish_by_partition": True,
            "reprocess_group_id": reprocess_group_id,
            "bronze_audit_task_id": audit_task_id,
            "queue_position": position,
            "queue_total": len(ordered_jobs),
        }
        if snapshot_date:
            details["snapshot_dates"] = [snapshot_date]
            log_message = (
                f"Fila {position}/{len(ordered_jobs)}: fonte {connection_id}, dia {snapshot_date}"
            )
        else:
            log_message = f"Fila {position}/{len(ordered_jobs)}: fonte {connection_id}, mês {period}"
        result = await db.execute(
            insert(EtlTask).values(
                task_type="reprocess", status="QUEUED", destination_table_id=body.destination_table_id,
                connection_ids=[connection_id], billing_periods=[period],
                result_json={"replace_existing": False, **details,
                             "chain_gold_cubes": body.chain_gold_cubes.model_dump() if body.chain_gold_cubes else {},
                             "event_log": [{
                    "at": datetime.now(timezone.utc).isoformat(), "level": "INFO",
                    "message": log_message,
                    "details": details,
                }]},
                total_blobs=0, processed_blobs=0, rows_raw=0, rows_normalized=0,
                rows_destination=0, errors=[],
            ).returning(EtlTask.id)
        )
        task_ids.append(result.scalar_one())
    await db.commit()

    return {
        "task_id": audit_task_id,
        "task_ids": [audit_task_id, *task_ids],
        "reprocess_group_id": reprocess_group_id,
        "message": f"Auditoria Bronze + {len(task_ids)} publicação(ões) {enqueue_label} enfileiradas",
    }


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
        conditions = [EtlTask.task_type.in_(["reprocess", "reclassification"])]
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
                    dt_val = datetime.fromisoformat(clean_val) if "T" in clean_val else datetime.fromisoformat(clean_val + "T00:00:00")
                except ValueError:
                    continue
                if op == "after":
                    expr = column >= dt_val
                elif op == "before":
                    expr = column <= (dt_val + timedelta(days=1) if "T" not in value else dt_val)
                elif op == "notEquals":
                    if ":" in value:
                        expr = func.date_trunc("minute", column) != func.date_trunc("minute", dt_val)
                    else:
                        expr = func.date(column) != dt_val.date()
                else:
                    expr = func.date_trunc("minute", column) == func.date_trunc("minute", dt_val) if ":" in value else func.date(column) == dt_val.date()
            elif field == "rows_written":
                try:
                    num_val = int(value)
                    expr = column == num_val
                except ValueError:
                    continue
            else:
                # Mesmo padrão do grid de Connections: comparar como texto,
                # com NULL incluído no notEquals/notContains.
                if op == "notEquals":
                    conditions.append(or_(column.is_(None), cast(column, String) != value))
                elif op == "contains":
                    conditions.append(cast(column, String).ilike(f"%{value}%"))
                elif op == "notContains":
                    conditions.append(or_(column.is_(None), ~cast(column, String).ilike(f"%{value}%")))
                elif op == "after":
                    conditions.append(cast(column, String) >= value)
                elif op == "before":
                    conditions.append(cast(column, String) <= value)
                else:
                    conditions.append(cast(column, String) == value)
                continue
            conditions.append(or_(column.is_(None), expr == False) if op == "notEquals" else expr)
        sort = fields.get(sort_field, EtlTask.started_at)
        # PostgreSQL places NULLs first on DESC by default. Active/queued
        # tasks can have no timestamps yet, so keep them after dated runs.
        ordering = (
            sort.desc().nullslast()
            if sort_direction == "desc"
            else sort.asc().nullslast()
        )
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


@router.get("/schedules/backup-size-estimates")
async def backup_size_estimates():
    """Current uncompressed data volume per selectable Iceberg layer."""
    from app.services.full_backup import backup_layer_size_estimates
    return await asyncio.to_thread(backup_layer_size_estimates)


@router.post("/schedules", response_model=ScheduledTaskOut, status_code=201)
async def create_schedule(body: ScheduledTaskCreate, db: AsyncSession = Depends(get_db)):
    """Create a new scheduled ETL task."""
    # Validate cron expression
    try:
        croniter(body.cron_expression)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid cron expression: {body.cron_expression}")
    if body.schedule_type not in {"backup", "collection", "reprocess"}:
        raise HTTPException(status_code=422, detail="Tipo de schedule inválido")
    if body.schedule_type != "backup" and not (body.connection_ids or body.connection_id):
        raise HTTPException(status_code=422, detail="Selecione ao menos uma conexão")

    sched = ScheduledTask(
        name=body.name,
        connection_id=body.connection_id,
        connection_ids=(body.connection_ids or ([body.connection_id] if body.connection_id else [])),
        cron_expression=body.cron_expression,
        timezone=body.timezone,
        destination_reprocess=body.destination_reprocess,
        active=body.active,
        config_json=body.config_json,
        schedule_type=body.schedule_type,
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
    if "schedule_type" in update_data and update_data["schedule_type"] not in {"backup", "collection", "reprocess"}:
        raise HTTPException(status_code=422, detail="Tipo de schedule inválido")
    if update_data.get("schedule_type") == "backup":
        update_data["connection_id"] = None
        update_data["connection_ids"] = []
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
async def run_schedule_now(
    schedule_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger a scheduled ETL task right now (all connections in scope)."""
    result = await db.execute(
        select(ScheduledTask).where(ScheduledTask.id == schedule_id)
    )
    sched = result.scalar_one_or_none()
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found")

    connection_ids = list(sched.connection_ids or []) or ([sched.connection_id] if sched.connection_id else [])
    # Manual execution must use the same scheduler path as cron execution.
    # The legacy collection helper inserted tasks directly and lost the
    # schedule metadata required by the incremental follow-up pipeline.
    # Do not hold the HTTP request open while the synchronous scheduler path
    # obtains a worker DB connection and queues one extraction per source.
    # A stale/idle transaction must never make the Play button look frozen.
    import asyncio
    background_tasks.add_task(
        asyncio.to_thread,
        scheduler_service._run_etl_job,
        connection_ids,
        sched.id,
        sched.name,
    )
    return {
        "task_ids": [],
        "status": "QUEUED",
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
