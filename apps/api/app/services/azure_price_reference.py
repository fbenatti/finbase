"""Targeted Azure Price Sheet lookup for missing clone reference prices."""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Any

from azure.storage.blob import BlobServiceClient

logger = logging.getLogger(__name__)
PRICE_SHEET_PREFIX = "reports/report_-pricesheet/"


def _price_sheet_client() -> tuple[BlobServiceClient, str]:
    """Cliente do Price Sheet com as credenciais da conexão ``azure-pricesheet``."""
    from sqlalchemy import select

    from app.database import SyncSession
    from app.models.source_connection import SourceConnection
    from app.services.azure_pricesheet import _azure_client

    with SyncSession() as session:
        connection = session.execute(
            select(SourceConnection).where(SourceConnection.connector_type == "azure_pricesheet")
        ).scalars().first()
        config = dict(connection.config_json or {}) if connection else {}
    if not config:
        raise RuntimeError("Cadastre a conexão Azure Price Sheet para buscar preços de referência")
    return _azure_client(config)


def _latest_month(client: Any, container_name: str) -> str:
    container = client.get_container_client(container_name)
    months: set[str] = set()
    for blob in container.list_blobs(name_starts_with=PRICE_SHEET_PREFIX):
        parts = blob.name.split("/")
        for part in parts:
            if len(part) == 17 and part[:8].isdigit() and part[8] == "-" and part[9:].isdigit():
                months.add(part[:6])
    if not months:
        raise RuntimeError("Nenhum Price Sheet mensal encontrado no prefixo configurado")
    return max(months)


def _find_meter_rows(container: Any, month: str, meter_id: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for blob in container.list_blobs(name_starts_with=f"{PRICE_SHEET_PREFIX}{month[:4]}{month[4:]}01-"):
        name = blob.name.lower()
        if not name.endswith(".csv"):
            continue
        downloader = container.get_blob_client(blob.name)
        query = f"SELECT * FROM BlobStorage WHERE MeterId = '{meter_id}'"
        try:
            result = downloader.query_blob(query, blob_format="CSV", output_format="CSVWithHeaders")
            payload = result.readall().decode("utf-8-sig")
            rows.extend(csv.DictReader(io.StringIO(payload)))
        except Exception:
            logger.exception("Query Acceleration falhou no Price Sheet blob %s", blob.name)
            raise
    return rows


def collect_meter_price(meter_id: str) -> dict[str, Any]:
    """Fetch only matching Price Sheet rows via server-side Query Acceleration."""
    service, container_name = _price_sheet_client()
    try:
        container = service.get_container_client(container_name)
        month = _latest_month(service, container_name)
        rows = _find_meter_rows(container, month, meter_id)
        consumption = [row for row in rows if str(row.get("PriceType", "")).lower() == "consumption"]
        candidates = consumption or rows
        if not candidates:
            raise LookupError(f"MeterId {meter_id} não encontrado no Price Sheet {month}")
        row = candidates[-1]
        market = float(row.get("MarketPrice") or 0)
        unit = float(row.get("UnitPrice") or 0)
        if unit <= 0:
            raise LookupError(f"MeterId {meter_id} não possui UnitPrice positivo no Price Sheet")
        return {
            "meter_id": meter_id,
            "description": row.get("MeterName") or row.get("ProductName") or "",
            "location": row.get("MeterRegion") or row.get("Location") or "",
            "unit_of_measure": row.get("UnitOfMeasure") or "",
            "price_type": row.get("PriceType") or "",
            "market_price_usd": market,
            "price_sheet_unit_price_usd": unit,
            "contract_discount_pct": ((1 - unit / market) * 100) if market else None,
            "reference_price_usd": unit,
            "reference_price_source": "PRICE_SHEET_CONTRACTED_UNITPRICE",
            "source_month": month,
            "needs_review": False,
            "currency": row.get("CurrencyCode") or row.get("Currency") or "USD",
            "source_blob": f"{PRICE_SHEET_PREFIX}{month}",
            "_billing_period": month,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        service.close()


def persist_reference(row: dict[str, Any]) -> None:
    """Upsert the targeted price into the small Iceberg Bronze cache."""
    import pandas as pd
    from app.services import iceberg_service

    from app.services.trino_service import execute_sync
    from app.services.iceberg_service import _get_catalog
    from pyiceberg.exceptions import NoSuchTableError

    escaped_meter = str(row["meter_id"]).replace("'", "''")
    frame = pd.DataFrame([{
        "meter_id": row["meter_id"],
        "description": row.get("description"),
        "location": row.get("location"),
        "unit_of_measure": row.get("unit_of_measure"),
        "price_type": row.get("price_type"),
        "market_price_usd": row.get("market_price_usd"),
        "price_sheet_unit_price_usd": row.get("price_sheet_unit_price_usd"),
        "contract_discount_pct": row.get("contract_discount_pct"),
        "reference_price_usd": row.get("reference_price_usd"),
        "reference_price_source": row.get("reference_price_source"),
        "source_month": row.get("source_month"),
        "table_reference_price_usd": None,
        "table_reference_month": None,
        "reference_delta_pct": None,
        "needs_review": False,
        "currency": row.get("currency") or "USD",
        "billing_currency": row.get("currency") or "USD",
        "source_blob": row.get("source_blob"),
        "_billing_period": str(row["_billing_period"]),
    }])
    try:
        table = _get_catalog().load_table(("bronze", "raw_azure_price_reference"))
    except NoSuchTableError:
        # First targeted lookup bootstraps the small cache; the full Price Sheet
        # remains a separate Bronze source and is never copied into this table.
        for column in (
            "market_price_usd", "price_sheet_unit_price_usd", "contract_discount_pct",
            "reference_price_usd", "table_reference_price_usd", "reference_delta_pct",
        ):
            frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")
        iceberg_service._append_or_create_table(
            "bronze", "raw_azure_price_reference", frame, "_billing_period"
        )
        return

    execute_sync(
        "DELETE FROM iceberg.bronze.raw_azure_price_reference "
        f"WHERE meter_id = '{escaped_meter}' AND _billing_period = '{row['_billing_period']}'",
        timeout_s=30,
    )
    table.refresh()
    arrow = iceberg_service._df_to_arrow(frame, target_schema=table.schema())
    table.append(arrow)


def collect_and_persist(meter_id: str) -> dict[str, Any]:
    record = collect_meter_price(meter_id)
    persist_reference(record)
    return record
