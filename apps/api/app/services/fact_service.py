import io
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from app.models.fact_cost import FactCost
from app.models.cost_lines import NormalizedCostLine
from app.models.ingested_blob import IngestedBlob

logger = logging.getLogger(__name__)

# Mapeamento de campos do JSONB normalizado para as colunas da fato
JSON_TO_FACT_MAP = {
    "Date": "date",
    "SubscriptionId": "subscription_id",
    "SubscriptionName": "subscription_name",
    "ResourceGroup": "resource_group",
    "ResourceGroupName": "resource_group",
    "ServiceName": "service_name",
    "ServiceCategory": "service_category",
    "MeterCategory": "meter_category",
    "MeterSubCategory": "meter_subcategory",
    "MeterName": "meter_name",
    "ResourceType": "resource_type",
    "ResourceName": "resource_name",
    "ResourceId": "resource_id",
    "ResourceLocation": "region",
    "Location": "region",
    "BillingCurrency": "currency",
    "BillingCurrencyCode": "currency",
    "UnitOfMeasure": "unit_of_measure",
    "ConsumedQuantity": "quantity",
    "Quantity": "quantity",
    "EffectivePrice": "effective_price",
    "CostInBillingCurrency": "cost_usd",
    "PayGPrice": "payg_cost_usd",
    "CostBRL": "cost_brl",
    "Tax_Base": "tax_base",
    "Tax_Marketplace": "tax_marketplace",
    "VL_CUSTO_FINAL_BRL": "final_cost_brl",
    "Diretoria": "diretoria",
    "ModeloAI": "modelo_ai",
    "department_name": "department_name",
    "department_path": "department_path",
    "centro_custo": "centro_custo",
    "ambiente": "ambiente",
    "organograma_nivel_1": "organograma_nivel_1",
    "organograma_nivel_2": "organograma_nivel_2",
    "organograma_nivel_3": "organograma_nivel_3",
    "organograma_nivel_4": "organograma_nivel_4",
    "organograma_nivel_5": "organograma_nivel_5",
    "organograma_nivel_6": "organograma_nivel_6",
    "organograma_nivel_7": "organograma_nivel_7",
    "DiretorResp": "diretor_resp",
}

SKIP_KEYS = {"id", "blob_id", "billing_period", "cost_type", "ingested_at", "Tags", "tags"}


def _json_record_to_fact_row(record: dict) -> dict:
    """Converts a normalized JSONB record into a fact table row dict."""
    row = {}
    extra = {}
    tags = None

    for key, value in record.items():
        if key in SKIP_KEYS:
            continue
        if key.lower() == "tags" and isinstance(value, dict):
            tags = value
            continue

        fact_col = JSON_TO_FACT_MAP.get(key)
        if fact_col:
            row[fact_col] = value
        else:
            extra[key] = value

    row["tags"] = tags
    row["extra"] = extra if extra else None

    return row


async def populate_fact_from_normalized(
    db: AsyncSession, connection_id: int, billing_period: str | None = None
) -> int:
    """Populate fact table from existing normalized_cost_line records."""
    stmt = (
        select(NormalizedCostLine, IngestedBlob.connection_id, IngestedBlob.blob_path)
        .join(IngestedBlob, NormalizedCostLine.blob_id == IngestedBlob.id)
        .where(IngestedBlob.connection_id == connection_id)
    )
    if billing_period:
        stmt = stmt.where(NormalizedCostLine.billing_period == billing_period)

    result = await db.execute(stmt)
    norm_rows = result.all()

    # Remove existing fact rows for this connection/period to avoid duplicates
    delete_stmt = text(
        "DELETE FROM fact_cost WHERE connection_id = :cid"
        + (" AND billing_period = :bp" if billing_period else "")
    )
    params = {"cid": connection_id}
    if billing_period:
        params["bp"] = billing_period
    await db.execute(delete_stmt, params)

    inserted = 0
    batch_size = 5000
    batch = []

    for norm, conn_id, blob_path in norm_rows:
        row_data = _json_record_to_fact_row(norm.data)
        row_data["connection_id"] = conn_id
        row_data["billing_period"] = norm.billing_period or ""
        row_data["cost_type"] = norm.cost_type or "actual"
        row_data["blob_path"] = blob_path

        batch.append(FactCost(**row_data))
        inserted += 1

        if len(batch) >= batch_size:
            for item in batch:
                db.add(item)
            await db.flush()
            batch.clear()

    for item in batch:
        db.add(item)
    await db.flush()
    await db.commit()

    logger.info("Populated fact_cost: %d rows for connection=%d period=%s", inserted, connection_id, billing_period or "all")
    return inserted


async def export_fact_to_parquet(
    db: AsyncSession,
    connection_id: int | None = None,
    billing_period: str | None = None,
    output_dir: str | None = None,
) -> str:
    """Export fact table data to Parquet. Returns file path."""
    stmt = select(FactCost)
    if connection_id:
        stmt = stmt.where(FactCost.connection_id == connection_id)
    if billing_period:
        stmt = stmt.where(FactCost.billing_period == billing_period)
    stmt = stmt.order_by(FactCost.billing_period, FactCost.date)

    result = await db.execute(stmt)
    rows = result.scalars().all()

    if not rows:
        logger.warning("No fact rows found for export")
        return ""

    records = []
    for r in rows:
        rec = {c.name: getattr(r, c.name, None) for c in FactCost.__table__.columns}
        # Convert datetime to isoformat
        for k, v in rec.items():
            if isinstance(v, datetime):
                rec[k] = v.isoformat()
            elif isinstance(v, dict):
                rec[k] = str(v)
        records.append(rec)

    df = pd.DataFrame(records)

    # Determine output path
    if not output_dir:
        output_dir = "/tmp/finops_exports"
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    file_name = f"fact_cost_{connection_id or 'all'}_{billing_period or 'all'}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.parquet"
    file_path = str(Path(output_dir) / file_name)

    df.to_parquet(file_path, index=False, engine="pyarrow")

    logger.info("Exported %d rows to %s", len(records), file_path)
    return file_path
