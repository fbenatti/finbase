"""Manual CSV upload router for Bronze ingestion."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.source_connection import SourceConnection
from app.schemas.schemas import IngestedBlobOut
from app.services.csv_upload_service import (
    build_preview,
    load_csv_to_bronze,
    parse_csv_bytes,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/csv-upload", tags=["csv-upload"])


class ColumnTypeOverride(BaseModel):
    name: str
    data_type: str


class CsvPreviewResponse(BaseModel):
    row_count: int
    column_count: int
    columns: list[dict[str, Any]]
    sample_rows: list[dict[str, Any]]
    suggested_billing_period: str | None


class CsvIngestRequest(BaseModel):
    connection_id: int
    billing_period: str | None = None
    column_types: list[ColumnTypeOverride] | None = None


class CsvIngestResponse(BaseModel):
    row_count: int
    billing_period: str
    blob_path: str
    table_name: str
    column_types: dict[str, str]


@router.post("/preview", response_model=CsvPreviewResponse)
async def preview_csv(
    file: UploadFile = File(...),
):
    """Upload a CSV and return a preview with inferred column types."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Somente arquivos CSV são suportados")

    try:
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="Arquivo vazio")
        df = parse_csv_bytes(raw)
        preview = build_preview(df)

        # Re-derive billing period for the UI default.
        from app.services.csv_upload_service import _derive_billing_period
        suggested = _derive_billing_period(file.filename, df)

        return CsvPreviewResponse(
            row_count=preview["row_count"],
            column_count=preview["column_count"],
            columns=preview["columns"],
            sample_rows=preview["sample_rows"],
            suggested_billing_period=suggested,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("CSV preview failed")
        raise HTTPException(status_code=500, detail=f"Falha ao processar CSV: {exc}") from exc


@router.post("/ingest", response_model=CsvIngestResponse)
async def ingest_csv(
    file: UploadFile = File(...),
    connection_id: int = Form(...),
    billing_period: str | None = Form(None),
    column_types_json: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Load a CSV into the Bronze layer after user confirmation.

    The file is re-parsed on ingestion so the user-adjusted types can be
    validated and stored as metadata.
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Somente arquivos CSV são suportados")

    conn = await db.get(SourceConnection, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Conexão não encontrada")

    allowed_connectors = {"manual_csv", "azure_blob", "gcp_bigquery"}
    if conn.connector_type not in allowed_connectors:
        raise HTTPException(
            status_code=400,
            detail=f"Upload manual não permitido para connector_type={conn.connector_type}",
        )

    try:
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="Arquivo vazio")
        df = parse_csv_bytes(raw)

        user_types: dict[str, str] | None = None
        if column_types_json:
            import json
            try:
                payload = json.loads(column_types_json)
                if isinstance(payload, list):
                    user_types = {item["name"]: item["data_type"] for item in payload}
                elif isinstance(payload, dict):
                    user_types = payload
            except (json.JSONDecodeError, KeyError, TypeError):
                raise HTTPException(status_code=400, detail="column_types_json inválido")

        from app.database import SyncSession
        session = SyncSession()
        try:
            result = load_csv_to_bronze(
                session=session,
                connection=conn,
                df=df,
                filename=file.filename,
                billing_period=billing_period,
                column_types=user_types,
            )
            session.commit()
            # Refresh async ORM object from sync session changes.
            await db.refresh(conn)
        except Exception as exc:
            session.rollback()
            raise exc
        finally:
            session.close()

        return CsvIngestResponse(**result)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("CSV ingestion failed")
        raise HTTPException(status_code=500, detail=f"Falha ao ingerir CSV: {exc}") from exc


@router.get("/connections", response_model=list[dict[str, Any]])
async def list_csv_connections(db: AsyncSession = Depends(get_db)):
    """List connections eligible for manual CSV upload."""
    result = await db.execute(
        select(SourceConnection).where(
            SourceConnection.connector_type.in_(["manual_csv", "azure_blob", "gcp_bigquery"])
        ).order_by(SourceConnection.code)
    )
    return [
        {
            "id": c.id,
            "code": c.code,
            "name": c.name,
            "connector_type": c.connector_type,
            "cloud_name": c.cloud_name,
        }
        for c in result.scalars().all()
    ]


@router.get("/history/{connection_id}", response_model=list[IngestedBlobOut])
async def list_csv_history(
    connection_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Return ingested blobs for a connection (manual uploads included)."""
    result = await db.execute(
        select(IngestedBlob)
        .where(IngestedBlob.connection_id == connection_id)
        .order_by(IngestedBlob.ingested_at.desc())
    )
    return result.scalars().all()
