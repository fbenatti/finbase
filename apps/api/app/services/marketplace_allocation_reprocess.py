"""Apply Marketplace financial allocation after a standard Silver reprocess."""

from __future__ import annotations

import json
import asyncio
from decimal import Decimal

import pandas as pd

from app.services import iceberg_service as iceberg
from app.services.marketplace_allocation import allocate_marketplace_pool
from app.services.trino_service import execute as trino_execute

CLOUD_CONNECTION_ID = 2
MAPPINGS = {
    5: {"rule_id": 21, "source": "mongodb-atlas-pro", "product": "(Private Offer) Telefonica Brasil SA - Sep 2024 - Q-201755", "sku": "DZH318Z0K00S00G3", "resource_group": "rg-mongo-atlas-brsouth-sharedservices", "name": "MongoDB Marketplace → Azure Private Offer Q-201755"},
    11: {"rule_id": 22, "source": "mongodb-atlas-enterprise", "product": "(Private Offer) Telefonica Brasil SA - Jan 2025 - Q-213513", "sku": "DZH318Z0K00S00NW", "resource_group": "rg-mongo-atlas-brsouth-sharedservices", "name": "MongoDB Enterprise Marketplace → Azure Private Offer Q-213513"},
}


def _append_rule(value: object, mapping: dict) -> str:
    try:
        payload = json.loads(value) if isinstance(value, str) and value else {}
    except json.JSONDecodeError:
        payload = {}
    financial = [item for item in payload.get("financial", []) if item.get("id") != mapping["rule_id"]]
    financial.append({"id": mapping["rule_id"], "name": mapping["name"], "financial_effect": "MARKETPLACE_ALLOCATION", "config": mapping})
    payload["financial"] = financial
    return json.dumps(payload, ensure_ascii=False)


def _cloud_pool_brl(period: str, mapping: dict) -> Decimal:
    rows = asyncio.run(trino_execute(
        f"""SELECT sum(realizado) pool_brl
              FROM iceberg.silver.dest_modelo_focus
             WHERE _billing_period = '{period}'
               AND source_connection_id = {CLOUD_CONNECTION_ID}
               AND organograma = 'MongoDB'
               AND marketplaceproductname = '{mapping["product"]}'
               AND skuid = '{mapping["sku"]}'
               AND source_resource_group_name = '{mapping["resource_group"]}'""",
        timeout_s=120,
    ))
    value = rows[0].get("pool_brl") if rows else None
    if value is None:
        raise RuntimeError(f"Pool Cloud não encontrado para {period}")
    return Decimal(str(value))


def apply_mongodb_marketplace_allocation(period: str, *, connection_id: int = 5, publish: bool = True) -> dict:
    """Rateia o pool Azure fechado nas linhas MongoDB do mesmo período."""
    frames = list(iceberg.read_destination_slice_batches(
        "dest_modelo_focus", billing_period=period,
        connection_id=connection_id, batch_size=100_000, namespace="silver",
    ))
    mongo = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if mongo.empty:
        raise RuntimeError(f"Silver MongoDB vazia para {period}")

    amount_column = next(
        (column for column in mongo.columns if str(column).lower() in {"billedcost", "amount"}),
        None,
    )
    if amount_column is None:
        raise RuntimeError(f"Coluna USD não encontrada para {period}")

    usd = pd.to_numeric(mongo[amount_column], errors="coerce").fillna(0)
    mapping = MAPPINGS[connection_id]
    pool = _cloud_pool_brl(period, mapping)
    allocations = allocate_marketplace_pool(usd.tolist(), pool)
    mongo["realizado_cloud"] = [float(value) for value in allocations]
    mongo["realizado"] = mongo["realizado_cloud"]
    current_rules = mongo.get("business_rule", pd.Series([None] * len(mongo)))
    mongo["business_rule"] = current_rules.map(lambda value: _append_rule(value, mapping))

    if publish:
        staged = iceberg.write_destination_staged(
            "dest_modelo_focus", mongo, namespace="silver", partition_spec_json=None,
        )
        iceberg.publish_staged_partition(
            "dest_modelo_focus", staged, billing_period=period,
            connection_id=connection_id, namespace="silver",
            expected_row_count=len(mongo),
        )

    return {
        "period": period,
        "rows": len(mongo),
        "marketplace_usd": float(usd.sum()),
        "cloud_pool_brl": float(pool),
        "allocated_brl": float(mongo["realizado"].sum()),
    }
