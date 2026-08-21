"""Manual AI cost connector — materializes license unit-cost entries into Bronze."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.models.ingested_blob import IngestedBlob
from app.models.source_connection import SourceConnection
from app.services import iceberg_service as iceberg

logger = logging.getLogger(__name__)

MANUAL_AI_COST_CODE = "manual_ai_cost"
MANUAL_AI_COST_CONNECTOR = "manual_ai_cost"
METADATA_KEY = "manual_ai_cost_metadata"

BRONZE_COLUMNS = (
    "provider",
    "product_name",
    "license_sku",
    "billing_period",
    "currency",
    "source_unit_price",
    "consumed_quantity",
    "billed_cost",
    "contract_ref",
    "charge_category",
    "service_name",
    "resource_id",
    "charge_description",
    "provider_name",
    "sku_id",
    "sku_name",
)


def _validate_period(period: str) -> str:
    if not re.fullmatch(r"\d{6}", period or ""):
        raise ValueError(f"Período inválido: {period} (esperado YYYYMM)")
    return period


def ensure_manual_ai_cost_connection(session: Session) -> SourceConnection:
    """Return (or create) the singleton manual AI cost source connection."""
    conn = session.query(SourceConnection).filter(
        SourceConnection.connector_type == MANUAL_AI_COST_CONNECTOR,
    ).first()
    if conn:
        return conn
    conn = SourceConnection(
        code=MANUAL_AI_COST_CODE,
        name="Custo manual de IA",
        connector_type=MANUAL_AI_COST_CONNECTOR,
        cloud_name=None,
        cloud_scope=None,
        config_json={},
        blob_prefixes=["manual_ai_cost"],
    )
    session.add(conn)
    session.flush()
    return conn


def build_entry_row(
    *,
    provider: str,
    product_name: str,
    license_sku: str | None,
    billing_period: str,
    currency: str,
    unit_price: float,
    active_licenses: int,
    contract_ref: str | None = None,
    charge_description: str | None = None,
) -> dict[str, Any]:
    period = _validate_period(billing_period)
    quantity = max(int(active_licenses or 0), 0)
    total = float(unit_price or 0) * quantity
    label = charge_description or f"Licença {product_name} — {provider}"
    return {
        "provider": provider.strip(),
        "product_name": product_name.strip(),
        "license_sku": (license_sku or "").strip() or None,
        "billing_period": period,
        "currency": (currency or "USD").strip().upper(),
        "source_unit_price": float(unit_price),
        "consumed_quantity": quantity,
        "billed_cost": total,
        "contract_ref": (contract_ref or "").strip() or None,
        "charge_category": "Purchase",
        "service_name": product_name.strip(),
        "resource_id": f"{provider.strip()}:{product_name.strip()}",
        "charge_description": label,
        "provider_name": provider.strip(),
        "sku_id": (license_sku or "").strip() or None,
        "sku_name": (license_sku or product_name).strip(),
    }


def write_manual_entry_to_bronze(
    session: Session,
    connection: SourceConnection,
    row: dict[str, Any],
    *,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Persist one manual license-cost fact row to Bronze and register metadata."""
    period = _validate_period(str(row.get("billing_period") or ""))
    blob_path = f"manual_ai_cost/{period}/{row.get('provider', 'ai')}_{row.get('product_name', 'product')}.csv".replace(" ", "_")

    df = pd.DataFrame([{col: row.get(col) for col in BRONZE_COLUMNS}])
    iceberg.delete_bronze_slice(connection.code, blob_path=blob_path, billing_period=period)
    written = iceberg.write_bronze(
        connection_code=connection.code,
        df=df,
        billing_period=period,
        blob_path=blob_path,
    )
    if written <= 0:
        raise ValueError("Nenhuma linha foi escrita no Bronze")

    existing = session.query(IngestedBlob).filter_by(
        connection_id=connection.id,
        blob_path=blob_path,
    ).first()
    if existing:
        session.delete(existing)
        session.flush()

    ingested = IngestedBlob(
        connection_id=connection.id,
        blob_path=blob_path,
        blob_prefix="manual_ai_cost",
        billing_period=period,
        last_modified=datetime.now(timezone.utc),
        etag="",
        content_length=0,
        row_count=written,
        status="INGESTED",
    )
    session.add(ingested)

    config = dict(connection.config_json or {})
    meta = dict(config.get(METADATA_KEY) or {})
    meta[period] = {
        "blob_path": blob_path,
        "row_count": written,
        "provider": row.get("provider"),
        "product_name": row.get("product_name"),
        "license_sku": row.get("license_sku"),
        "unit_price": row.get("source_unit_price"),
        "active_licenses": row.get("consumed_quantity"),
        "billed_cost": row.get("billed_cost"),
        "contract_ref": row.get("contract_ref"),
        "created_by": created_by,
    }
    config[METADATA_KEY] = meta
    connection.config_json = config
    session.flush()

    return {
        "connection_id": connection.id,
        "blob_path": blob_path,
        "billing_period": period,
        "row_count": written,
        "billed_cost": row.get("billed_cost"),
        "consumed_quantity": row.get("consumed_quantity"),
    }


def drop_manual_period_meta(config: dict | None, period: str) -> dict:
    next_config = dict(config or {})
    meta = dict(next_config.get(METADATA_KEY) or {})
    if period in meta:
        meta.pop(period, None)
        next_config[METADATA_KEY] = meta
    return next_config
