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
    _derive_billing_period,
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
    monthly_grain: bool = False
    current_month: bool = False
    warning_message: str | None = None
    skip_gold_realizado: bool = False


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
    monthly_grain: bool = False
    current_month: bool = False
    warning_message: str | None = None
    skip_gold_realizado: bool = False


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
        suggested = _derive_billing_period(file.filename, df)
        preview = build_preview(df, billing_period=suggested)

        return CsvPreviewResponse(
            row_count=preview["row_count"],
            column_count=preview["column_count"],
            columns=preview["columns"],
            sample_rows=preview["sample_rows"],
            suggested_billing_period=suggested,
            monthly_grain=preview.get("monthly_grain", False),
            current_month=preview.get("current_month", False),
            warning_message=preview.get("warning_message"),
            skip_gold_realizado=preview.get("skip_gold_realizado", False),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("CSV preview failed")
        raise HTTPException(status_code=500, detail=f"Falha ao processar CSV: {exc}") from exc


def _parse_column_types(column_types_json: str | None) -> dict[str, str] | None:
    if not column_types_json:
        return None
    import json
    try:
        payload = json.loads(column_types_json)
        if isinstance(payload, list):
            return {item["name"]: item["data_type"] for item in payload}
        if isinstance(payload, dict):
            return payload
        return None
    except (json.JSONDecodeError, KeyError, TypeError):
        raise HTTPException(status_code=400, detail="column_types_json inválido")


def _ingest_one(
    connection_id: int,
    filename: str,
    raw: bytes,
    billing_period: str | None,
    user_types: dict[str, str] | None,
) -> dict[str, Any]:
    """Synchronous ingestion of a single parsed CSV into Bronze."""
    df = parse_csv_bytes(raw)

    from app.database import SyncSession
    session = SyncSession()
    try:
        sync_conn = session.get(SourceConnection, connection_id)
        if sync_conn is None:
            raise HTTPException(status_code=404, detail="Conexão não encontrada")
        result = load_csv_to_bronze(
            session=session,
            connection=sync_conn,
            df=df,
            filename=filename,
            billing_period=billing_period,
            column_types=user_types,
        )
        session.commit()
        return result
    except Exception as exc:
        session.rollback()
        raise exc
    finally:
        session.close()


async def _validate_connection(connection_id: int, db: AsyncSession) -> SourceConnection:
    conn = await db.get(SourceConnection, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Conexão não encontrada")
    allowed_connectors = {"manual_csv", "azure_blob", "gcp_bigquery"}
    if conn.connector_type not in allowed_connectors:
        raise HTTPException(
            status_code=400,
            detail=f"Upload manual não permitido para connector_type={conn.connector_type}",
        )
    return conn


@router.post("/ingest", response_model=CsvIngestResponse)
async def ingest_csv(
    file: UploadFile = File(...),
    connection_id: int = Form(...),
    billing_period: str | None = Form(None),
    column_types_json: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Load a single CSV into the Bronze layer after user confirmation."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Somente arquivos CSV são suportados")

    await _validate_connection(connection_id, db)
    user_types = _parse_column_types(column_types_json)

    try:
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="Arquivo vazio")
        result = _ingest_one(connection_id, file.filename or "upload.csv", raw, billing_period, user_types)
        return CsvIngestResponse(**result)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("CSV ingestion failed")
        raise HTTPException(status_code=500, detail=f"Falha ao ingerir CSV: {exc}") from exc


@router.post("/ingest-batch", response_model=list[CsvIngestResponse])
async def ingest_csv_batch(
    files: list[UploadFile] = File(...),
    connection_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Load multiple CSVs into Bronze, deriving the period from each file.

    The billing period is taken from date/billing columns inside the CSV when
    available (e.g. ``date``, ``ChargePeriodStart``, ``billingPeriodStartDate``),
    falling back to a YYYYMM token in the filename. Types are auto-inferred.
    """
    await _validate_connection(connection_id, db)

    results: list[CsvIngestResponse] = []
    errors: list[dict[str, str]] = []

    for f in files:
        name = f.filename or "upload.csv"
        if not name.lower().endswith(".csv"):
            errors.append({"filename": name, "error": "Somente arquivos CSV são suportados"})
            continue

        raw = await f.read()
        if not raw:
            errors.append({"filename": name, "error": "Arquivo vazio"})
            continue

        try:
            result = _ingest_one(connection_id, name, raw, None, None)
            results.append(CsvIngestResponse(**result))
        except HTTPException as exc:
            errors.append({"filename": name, "error": str(exc.detail)})
        except ValueError as exc:
            errors.append({"filename": name, "error": str(exc)})
        except Exception as exc:
            logger.exception("Batch CSV ingestion failed for %s", name)
            errors.append({"filename": name, "error": f"Falha ao ingerir: {exc}"})

    if not results and errors:
        raise HTTPException(
            status_code=422,
            detail={"errors": errors},
        )

    return results


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
