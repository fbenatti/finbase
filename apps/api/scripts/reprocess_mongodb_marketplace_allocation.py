"""Apply the MongoDB Marketplace allocation rule to one closed period."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal

import pandas as pd

from app.services import iceberg_service as iceberg
from app.services.marketplace_allocation import allocate_marketplace_pool


PERIOD = "202608"
MONGO_CONNECTION_ID = 5
CLOUD_CONNECTION_ID = 2
PRODUCT = "(Private Offer) Telefonica Brasil SA - Sep 2024 - Q-201755"
SKU = "DZH318Z0K00S00G3"
RESOURCE_GROUP = "rg-mongo-atlas-brsouth-sharedservices"
RULE_META = {
    "id": 21,
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


def _read(connection_id: int) -> pd.DataFrame:
    frames = list(iceberg.read_destination_slice_batches(
        "dest_modelo_focus",
        billing_period=PERIOD,
        connection_id=connection_id,
        batch_size=100_000,
        namespace="silver",
    ))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _append_rule(value: object) -> str:
    try:
        payload = json.loads(value) if isinstance(value, str) and value else {}
    except json.JSONDecodeError:
        payload = {}
    financial = [item for item in payload.get("financial", []) if item.get("id") != 21]
    financial.append(RULE_META)
    payload["financial"] = financial
    return json.dumps(payload, ensure_ascii=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="publish the MongoDB Silver slice")
    args = parser.parse_args()

    mongo = _read(MONGO_CONNECTION_ID)
    cloud = _read(CLOUD_CONNECTION_ID)
    cloud = cloud[
        (cloud.get("organograma") == "MongoDB")
        & (cloud.get("marketplaceproductname") == PRODUCT)
        & (cloud.get("skuid") == SKU)
        & (cloud.get("source_resource_group_name") == RESOURCE_GROUP)
    ].copy()
    if mongo.empty or cloud.empty:
        raise RuntimeError("Silver MongoDB ou pool Cloud vazio para 202608")

    usd = pd.to_numeric(mongo["billedcost"], errors="coerce").fillna(0)
    pool = Decimal(str(pd.to_numeric(cloud["realizado"], errors="coerce").sum()))
    allocations = allocate_marketplace_pool(usd.tolist(), pool)
    mongo["realizado_cloud"] = [float(value) for value in allocations]
    mongo["realizado"] = mongo["realizado_cloud"]
    mongo["business_rule"] = mongo.get("business_rule", pd.Series([None] * len(mongo))).map(_append_rule)

    print(f"period={PERIOD} marketplace_rows={len(mongo)} cloud_rows={len(cloud)}")
    print(f"marketplace_usd={usd.sum():.2f}")
    print(f"cloud_pool_brl={pool:.2f}")
    print(f"allocated_brl={mongo['realizado'].sum():.2f}")
    print(f"delta_brl={mongo['realizado'].sum() - float(pool):.2f}")

    if not args.apply:
        print("dry-run: use --apply to publish")
        return

    staged = iceberg.write_destination_staged(
        "dest_modelo_focus",
        mongo,
        namespace="silver",
        partition_spec_json=None,
    )
    iceberg.publish_staged_partition(
        "dest_modelo_focus",
        staged,
        billing_period=PERIOD,
        connection_id=MONGO_CONNECTION_ID,
        namespace="silver",
        expected_row_count=len(mongo),
    )
    print("published: mongodb-atlas-pro/202608")


if __name__ == "__main__":
    main()
