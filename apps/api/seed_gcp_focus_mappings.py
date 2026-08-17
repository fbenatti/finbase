"""
Seed GCP BigQuery → MODELO FOCUS mappings using the official Google/FOCUS mapping.

Usage: cd apps/api && python seed_gcp_focus_mappings.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import text

from app.database import SyncSession

DEST_TABLE_ID = 1
GCP_CONNECTIONS = {
    3: "GCP-4P",
    4: "GCP-TI",
}

# All 50 leaf fields from the detailed export must appear in at least one mapping source.
LEAF_FIELDS = {
    "billing_account_id", "service", "sku", "usage_start_time", "usage_end_time", "project",
    "labels", "system_labels", "location", "resource", "tags", "price", "subscription",
    "transaction_type", "seller_name", "export_time", "cost", "currency", "currency_conversion_rate",
    "usage", "credits", "invoice", "cost_type", "adjustment_info", "cost_at_list",
    "cost_at_effective_price_default", "cost_at_list_consumption_model", "consumption_model",
}

MAPPINGS: list[dict] = [
    {"source_column": "billing_account_id", "destination_column": "BillingAccountId", "transform_type": "RENAME"},
    {"source_column": "billing_account_id", "destination_column": "BillingAccountName", "transform_type": "CONST", "transform_config": {"value": "__CLOUD_NAME__"}},
    {"source_column": "project", "destination_column": "SubAccountId", "transform_type": "JSON_PATH", "transform_config": {"path": "id"}},
    {"source_column": "project", "destination_column": "SubAccountName", "transform_type": "JSON_PATH", "transform_config": {"path": "name"}},
    {"source_column": "project", "destination_column": "source_resource_group_name", "transform_type": "JSON_PATH", "transform_config": {"path": "name"}},
    {"source_column": "cost", "destination_column": "BilledCost", "transform_type": "EXPR", "transform_config": {"expression": "cost + credits_sum"}},
    {"source_column": "cost", "destination_column": "EffectiveCost", "transform_type": "EXPR", "transform_config": {"expression": "cost + credits_sum"}},
    {"source_column": "cost", "destination_column": "ContractedCost", "transform_type": "RENAME"},
    {"source_column": "cost_at_list", "destination_column": "ListCost", "transform_type": "RENAME"},
    {"source_column": "price", "destination_column": "ContractedUnitPrice", "transform_type": "JSON_PATH", "transform_config": {"path": "effective_price"}},
    {"source_column": "price", "destination_column": "ListUnitPrice", "transform_type": "JSON_PATH", "transform_config": {"path": "list_price"}},
    {"source_column": "price", "destination_column": "PricingCurrencyContractedUnitPrice", "transform_type": "JSON_PATH", "transform_config": {"path": "effective_price"}},
    {"source_column": "price", "destination_column": "PricingCurrencyListUnitPrice", "transform_type": "JSON_PATH", "transform_config": {"path": "list_price"}},
    {"source_column": "price", "destination_column": "source_unit_price", "transform_type": "JSON_PATH", "transform_config": {"path": "effective_price"}},
    {"source_column": "price", "destination_column": "x_PriceTierStartAmount", "transform_type": "JSON_PATH", "transform_config": {"path": "tier_start_amount"}},
    {"source_column": "currency", "destination_column": "BillingCurrency", "transform_type": "RENAME"},
    {"source_column": "currency", "destination_column": "PricingCurrency", "transform_type": "RENAME"},
    {"source_column": "usage_start_time", "destination_column": "ChargePeriodStart", "transform_type": "RENAME"},
    {"source_column": "usage_end_time", "destination_column": "ChargePeriodEnd", "transform_type": "RENAME"},
    {"source_column": "invoice", "destination_column": "BillingPeriodStart", "transform_type": "MONTH_START"},
    {"source_column": "invoice", "destination_column": "BillingPeriodEnd", "transform_type": "MONTH_END"},
    {"source_column": "cost_type", "destination_column": "ChargeCategory", "transform_type": "VALUE_MAP", "transform_config": {"map": {"regular": "Usage", "tax": "Tax", "adjustment": "Adjustment", "rounding_error": "Adjustment"}, "default": "Usage"}},
    {"source_column": "adjustment_info", "destination_column": "ChargeClass", "transform_type": "JSON_PATH", "transform_config": {"path": "id"}},
    {"source_column": "sku", "destination_column": "ChargeDescription", "transform_type": "JSON_PATH", "transform_config": {"path": "description"}},
    {"source_column": "usage", "destination_column": "ConsumedQuantity", "transform_type": "JSON_PATH", "transform_config": {"path": "amount"}},
    {"source_column": "usage", "destination_column": "ConsumedUnit", "transform_type": "JSON_PATH", "transform_config": {"path": "unit"}},
    {"source_column": "usage", "destination_column": "x_UsageAmountInPricingUnits", "transform_type": "JSON_PATH", "transform_config": {"path": "amount_in_pricing_units"}},
    {"source_column": "usage", "destination_column": "x_UsagePricingUnit", "transform_type": "JSON_PATH", "transform_config": {"path": "pricing_unit"}},
    {"source_column": "price", "destination_column": "PricingQuantity", "transform_type": "JSON_PATH", "transform_config": {"path": "pricing_unit_quantity"}},
    {"source_column": "price", "destination_column": "PricingUnit", "transform_type": "JSON_PATH", "transform_config": {"path": "unit"}},
    # Azure maps meterCategory → PricingCategory and consumedService → ServiceName.
    # GCP service.description is that same service-family grain (Compute Engine, Cloud Storage).
    {"source_column": "service", "destination_column": "PricingCategory", "transform_type": "JSON_PATH", "transform_config": {"path": "description"}},
    {"source_column": "service", "destination_column": "ServiceName", "transform_type": "JSON_PATH", "transform_config": {"path": "description"}},
    {"source_column": "sku", "destination_column": "ServiceSubcategory", "transform_type": "JSON_PATH", "transform_config": {"path": "description"}},
    {"source_column": "sku", "destination_column": "SkuMeter", "transform_type": "JSON_PATH", "transform_config": {"path": "description"}},
    {"source_column": "sku", "destination_column": "SkuId", "transform_type": "JSON_PATH", "transform_config": {"path": "id"}},
    {"source_column": "sku", "destination_column": "source_meter_id", "transform_type": "JSON_PATH", "transform_config": {"path": "id"}},
    {"source_column": "sku", "destination_column": "SkuPriceId", "transform_type": "CONCAT", "transform_config": {"parts": ["sku:id", "price:tier_start_amount"], "sep": "-"}},
    {"source_column": "location", "destination_column": "RegionId", "transform_type": "JSON_PATH", "transform_config": {"path": "region"}},
    {"source_column": "location", "destination_column": "RegionName", "transform_type": "JSON_PATH", "transform_config": {"path": "region"}},
    {"source_column": "location", "destination_column": "AvailabilityZone", "transform_type": "JSON_PATH", "transform_config": {"path": "zone"}},
    {"source_column": "resource", "destination_column": "ResourceId", "transform_type": "JSON_PATH", "transform_config": {"path": "global_name"}},
    # Azure ResourceName is ProductName (SKU/product), not the VM instance id.
    {"source_column": "sku", "destination_column": "ResourceName", "transform_type": "JSON_PATH", "transform_config": {"path": "description"}},
    {"source_column": "seller_name", "destination_column": "InvoiceIssuerName", "transform_type": "RENAME"},
    {"source_column": "transaction_type", "destination_column": "HostProviderName", "transform_type": "RENAME"},
    {"source_column": "transaction_type", "destination_column": "x_TransactionType", "transform_type": "RENAME"},
    {"source_column": "seller_name", "destination_column": "ServiceProviderName", "transform_type": "CONST", "transform_config": {"value": "Google Cloud"}},
    {"source_column": "project", "destination_column": "x_Project", "transform_type": "RENAME"},
    {"source_column": "project", "destination_column": "x_ProjectLabels", "transform_type": "JSON_PATH", "transform_config": {"path": "labels"}},
    {"source_column": "credits", "destination_column": "x_Credits", "transform_type": "RENAME"},
    {"source_column": "labels", "destination_column": "x_Labels", "transform_type": "RENAME"},
    {"source_column": "labels", "destination_column": "Tags", "transform_type": "RENAME"},
    {"source_column": "system_labels", "destination_column": "x_SystemLabels", "transform_type": "RENAME"},
    {"source_column": "tags", "destination_column": "x_Tags", "transform_type": "RENAME"},
    {"source_column": "tags", "destination_column": "AllocatedTags", "transform_type": "RENAME"},
    {"source_column": "service", "destination_column": "x_ServiceId", "transform_type": "JSON_PATH", "transform_config": {"path": "id"}},
    {"source_column": "cost_type", "destination_column": "x_CostType", "transform_type": "RENAME"},
    {"source_column": "cost_type", "destination_column": "source_charge_type", "transform_type": "RENAME"},
    {"source_column": "location", "destination_column": "x_Location", "transform_type": "JSON_PATH", "transform_config": {"path": "location"}},
    {"source_column": "location", "destination_column": "x_LocationCountry", "transform_type": "JSON_PATH", "transform_config": {"path": "country"}},
    {"source_column": "currency_conversion_rate", "destination_column": "x_CurrencyConversionRate", "transform_type": "RENAME"},
    {"source_column": "currency_conversion_rate", "destination_column": "source_exchange_rate", "transform_type": "RENAME"},
    {"source_column": "export_time", "destination_column": "x_ExportTime", "transform_type": "RENAME"},
    {"source_column": "subscription", "destination_column": "x_SubscriptionInstanceId", "transform_type": "JSON_PATH", "transform_config": {"path": "instance_id"}},
    {"source_column": "price", "destination_column": "x_PriceEffectivePriceDefault", "transform_type": "JSON_PATH", "transform_config": {"path": "effective_price_default"}},
    {"source_column": "price", "destination_column": "x_PriceListPriceConsumptionModel", "transform_type": "JSON_PATH", "transform_config": {"path": "list_price_consumption_model"}},
    {"source_column": "cost_at_effective_price_default", "destination_column": "x_CostAtEffectivePriceDefault", "transform_type": "RENAME"},
    {"source_column": "cost_at_list_consumption_model", "destination_column": "x_CostAtListConsumptionModel", "transform_type": "RENAME"},
    {"source_column": "consumption_model", "destination_column": "x_ConsumptionModelId", "transform_type": "JSON_PATH", "transform_config": {"path": "id"}},
    {"source_column": "consumption_model", "destination_column": "x_ConsumptionModelDescription", "transform_type": "JSON_PATH", "transform_config": {"path": "description"}},
    {"source_column": "invoice", "destination_column": "x_InvoicePublisherType", "transform_type": "JSON_PATH", "transform_config": {"path": "publisher_type"}},
    {"source_column": "invoice", "destination_column": "x_InvoiceMonth", "transform_type": "JSON_PATH", "transform_config": {"path": "month"}},
    {"source_column": "invoice", "destination_column": "InvoiceId", "transform_type": "JSON_PATH", "transform_config": {"path": "month"}},
    {"source_column": "adjustment_info", "destination_column": "x_AdjustmentInfo", "transform_type": "RENAME"},
    {"source_column": "cost", "destination_column": "PricingCurrencyEffectiveCost", "transform_type": "EXPR", "transform_config": {"expression": "cost + credits_sum"}},
]

X_COLUMNS = [
    ("x_CostType", "TEXT"), ("x_Credits", "JSONB"), ("x_CurrencyConversionRate", "DOUBLE PRECISION"),
    ("x_ExportTime", "TIMESTAMPTZ"), ("x_Labels", "JSONB"), ("x_Location", "TEXT"),
    ("x_Project", "JSONB"), ("x_ProjectLabels", "JSONB"), ("x_ServiceId", "TEXT"),
    ("x_SystemLabels", "JSONB"), ("x_Tags", "JSONB"), ("x_SubscriptionInstanceId", "TEXT"),
    ("x_PriceEffectivePriceDefault", "NUMERIC"), ("x_PriceListPriceConsumptionModel", "NUMERIC"),
    ("x_CostAtEffectivePriceDefault", "NUMERIC"), ("x_CostAtListConsumptionModel", "NUMERIC"),
    ("x_ConsumptionModelId", "TEXT"), ("x_ConsumptionModelDescription", "TEXT"),
    ("x_TransactionType", "TEXT"), ("x_InvoicePublisherType", "TEXT"), ("x_InvoiceMonth", "TEXT"),
    ("x_AdjustmentInfo", "JSONB"), ("x_UsageAmountInPricingUnits", "NUMERIC"),
    ("x_UsagePricingUnit", "TEXT"), ("x_PriceTierStartAmount", "NUMERIC"), ("x_LocationCountry", "TEXT"),
]


def ensure_destination_columns(session) -> None:
    max_pos = session.execute(text(
        "SELECT COALESCE(MAX(position), 0) FROM destination_column WHERE destination_table_id = :did"
    ), {"did": DEST_TABLE_ID}).scalar() or 0
    for idx, (name, dtype) in enumerate(X_COLUMNS, start=max_pos + 1):
        session.execute(text("""
            INSERT INTO destination_column (destination_table_id, column_name, data_type, is_required, position)
            SELECT :did, :name, :dtype, false, :pos
            WHERE NOT EXISTS (
                SELECT 1 FROM destination_column
                WHERE destination_table_id = :did AND column_name = :name
            )
        """), {"did": DEST_TABLE_ID, "name": name, "dtype": dtype, "pos": idx})


def seed_mappings(session) -> int:
    inserted = 0
    for conn_id, cloud_label in GCP_CONNECTIONS.items():
        for priority, mapping in enumerate(MAPPINGS, start=1):
            cfg = dict(mapping.get("transform_config") or {})
            if cfg.get("value") == "__CLOUD_NAME__":
                cfg["value"] = cloud_label
            session.execute(text("""
                INSERT INTO source_destination_mapping (
                    source_connection_id, destination_table_id, name,
                    source_column, destination_column, transform_type, transform_config,
                    priority, active
                )
                SELECT :cid, :did, :name, :src, :dest, :ttype, CAST(:cfg AS jsonb), :prio, true
                WHERE NOT EXISTS (
                    SELECT 1 FROM source_destination_mapping
                    WHERE source_connection_id = :cid
                      AND destination_table_id = :did
                      AND destination_column = :dest
                )
            """), {
                "cid": conn_id,
                "did": DEST_TABLE_ID,
                "name": f"GCP {cloud_label}: {mapping['source_column']} -> {mapping['destination_column']}",
                "src": mapping["source_column"],
                "dest": mapping["destination_column"],
                "ttype": mapping["transform_type"],
                "cfg": __import__("json").dumps(cfg),
                "prio": priority,
            })
            session.execute(text("""
                UPDATE source_destination_mapping
                SET source_column = :src,
                    transform_type = :ttype,
                    transform_config = CAST(:cfg AS jsonb),
                    name = :name,
                    priority = :prio,
                    active = true
                WHERE source_connection_id = :cid
                  AND destination_table_id = :did
                  AND destination_column = :dest
            """), {
                "cid": conn_id,
                "did": DEST_TABLE_ID,
                "name": f"GCP {cloud_label}: {mapping['source_column']} -> {mapping['destination_column']}",
                "src": mapping["source_column"],
                "dest": mapping["destination_column"],
                "ttype": mapping["transform_type"],
                "cfg": __import__("json").dumps(cfg),
                "prio": priority,
            })
            inserted += 1
    return inserted


def assert_leaf_coverage() -> None:
    mapped_sources = {m["source_column"] for m in MAPPINGS}
    missing = LEAF_FIELDS - mapped_sources
    if missing:
        raise RuntimeError(f"GCP leaf fields without mapping: {sorted(missing)}")


def main() -> None:
    assert_leaf_coverage()
    session = SyncSession()
    try:
        ensure_destination_columns(session)
        seed_mappings(session)
        session.commit()
        print("GCP FOCUS mappings seeded successfully.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
