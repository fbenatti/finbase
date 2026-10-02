"""Apply Marketplace financial allocation after a standard Silver reprocess."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd

from app.services import iceberg_service as iceberg
from app.services.marketplace_allocation import allocate_marketplace_pool

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
from scripts.trino_query import run as trino_run  # noqa: E402

MONGO_CONNECTION_ID = 5
CLOUD_CONNECTION_ID = 2
PRODUCT = "(Private Offer) Telefonica Brasil SA - Sep 2024 - Q-201755"
SKU = "DZH318Z0K00S00G3"
RESOURCE_GROUP = "rg-mongo-atlas-brsouth-sharedservices"
RULE_ID = 21
RULE_META = {
    "id": RULE_ID,
    "name": "MongoDB Marketplace → Azure Private Offer Q-201755",
    "financial_effect": "MARKETPLACE_ALLOCATION",
    "config": {
        "allocation_method": "MARKETPLACE_USD_SHARE",
        "marketplace_connection_code": "mongodb-atlas-pro",
        "cloud_connection_code": "azure-ti",
        "cloud_product_name": PRODUCT,
        "cloud_sku": SKU,
        "cloud_resource_group": RESOURCE_GROUP,
    },
}


def _append_rule(value: object) -> str:
    try:
        payload = json.loads(value) if isinstance(value, str) and value else {}
    except json.JSONDecodeError:
        payload = {}
    financial = [item for item in payload.get("financial", []) if item.get("id") != RULE_ID]
    financial.append(RULE_META)
    payload["financial"] = financial
    return json.dumps(payload, ensure_ascii=False)


def _cloud_pool_brl(period: str) -> Decimal:
    rows = trino_run(
        f"""SELECT sum(realizado) pool_brl
              FROM iceberg.silver.dest_modelo_focus
             WHERE _billing_period = '{period}'
               AND source_connection_id = {CLOUD_CONNECTION_ID}
               AND organograma = 'MongoDB'
               AND marketplaceproductname = '{PRODUCT}'
               AND skuid = '{SKU}'
               AND source_resource_group_name = '{RESOURCE_GROUP}'""",
        timeout_s=120,
    )
    value = rows[0].get("pool_brl") if rows else None
    if value is None:
        raise RuntimeError(f"Pool Cloud não encontrado para {period}")
    return Decimal(str(value))


def apply_mongodb_marketplace_allocation(period: str, *, publish: bool = True) -> dict:
    """Rateia o pool Azure fechado nas linhas MongoDB do mesmo período."""
    frames = list(iceberg.read_destination_slice_batches(
        "dest_modelo_focus", billing_period=period,
        connection_id=MONGO_CONNECTION_ID, batch_size=100_000, namespace="silver",
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
    pool = _cloud_pool_brl(period)
    allocations = allocate_marketplace_pool(usd.tolist(), pool)
    mongo["realizado_cloud"] = [float(value) for value in allocations]
    mongo["realizado"] = mongo["realizado_cloud"]
    current_rules = mongo.get("business_rule", pd.Series([None] * len(mongo)))
    mongo["business_rule"] = current_rules.map(_append_rule)

    if publish:
        staged = iceberg.write_destination_staged(
            "dest_modelo_focus", mongo, namespace="silver", partition_spec_json=None,
        )
        iceberg.publish_staged_partition(
            "dest_modelo_focus", staged, billing_period=period,
            connection_id=MONGO_CONNECTION_ID, namespace="silver",
            expected_row_count=len(mongo),
        )

    return {
        "period": period,
        "rows": len(mongo),
        "marketplace_usd": float(usd.sum()),
        "cloud_pool_brl": float(pool),
        "allocated_brl": float(mongo["realizado"].sum()),
    }
