"""
Seed AI token cost mappings → BILLING_CLOUD (ChargeCategory Usage).

Only rows with real billed cost are mapped:
  - claude_token: record_type = cost_report (Anthropic Admin API)
  - notebooklm_token: record_type = billing_export (BigQuery billing export)

GitHub is excluded — no token cost in billing today.

Usage: cd apps/api && python seed_ai_focus_mappings.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import select, text

from app.database import SyncSession
from app.models.destination import DestinationTable
from app.models.source_connection import SourceConnection

FOCUS_PHYSICAL_NAME = "dest_billing_cloud"
COST_RECORD_TYPES = {
    "claude_token": "cost_report",
    "notebooklm_token": "billing_export",
}

CLAUDE_COST_FOCUS: list[dict] = [
    {
        "source_column": "cost_usd",
        "destination_column": "BilledCost",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "RENAME",
            "then_source": "cost_usd",
        },
    },
    {
        "source_column": "cost_usd",
        "destination_column": "EffectiveCost",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "RENAME",
            "then_source": "cost_usd",
        },
    },
    {
        "source_column": "currency",
        "destination_column": "BillingCurrency",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "RENAME",
            "then_source": "currency",
        },
    },
    {
        "source_column": "currency",
        "destination_column": "PricingCurrency",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "RENAME",
            "then_source": "currency",
        },
    },
    {
        "source_column": "usage_date",
        "destination_column": "ChargePeriodStart",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "RENAME",
            "then_source": "usage_date",
        },
    },
    {
        "source_column": "usage_date",
        "destination_column": "ChargePeriodEnd",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "RENAME",
            "then_source": "usage_date",
        },
    },
    {
        "source_column": "usage_date",
        "destination_column": "BillingPeriodStart",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "RENAME",
            "then_source": "usage_date",
        },
    },
    {
        "source_column": "usage_date",
        "destination_column": "BillingPeriodEnd",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "RENAME",
            "then_source": "usage_date",
        },
    },
    {
        "source_column": "prompt_tokens",
        "destination_column": "ConsumedQuantity",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "RENAME",
            "then_source": "prompt_tokens",
        },
    },
    {
        "source_column": "record_type",
        "destination_column": "ConsumedUnit",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "CONST",
            "then_config": {"value": "tokens"},
        },
    },
    {
        "source_column": "prompt_tokens",
        "destination_column": "PricingQuantity",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "RENAME",
            "then_source": "prompt_tokens",
        },
    },
    {
        "source_column": "record_type",
        "destination_column": "PricingUnit",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "CONST",
            "then_config": {"value": "tokens"},
        },
    },
    {
        "source_column": "model",
        "destination_column": "ChargeDescription",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "RENAME",
            "then_source": "model",
        },
    },
    {
        "source_column": "model",
        "destination_column": "ServiceSubcategory",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "RENAME",
            "then_source": "model",
        },
    },
    {
        "source_column": "model",
        "destination_column": "SkuMeter",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "RENAME",
            "then_source": "model",
        },
    },
    {
        "source_column": "organization_id",
        "destination_column": "SubAccountId",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "RENAME",
            "then_source": "organization_id",
        },
    },
    {
        "source_column": "organization_id",
        "destination_column": "BillingAccountId",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "RENAME",
            "then_source": "organization_id",
        },
    },
    {
        "source_column": "workspace_id",
        "destination_column": "ResourceId",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "RENAME",
            "then_source": "workspace_id",
        },
    },
    {
        "source_column": "record_type",
        "destination_column": "ChargeCategory",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "CONST",
            "then_config": {"value": "Usage"},
        },
    },
    {
        "source_column": "record_type",
        "destination_column": "ChargeFrequency",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "CONST",
            "then_config": {"value": "Usage-Based"},
        },
    },
    {
        "source_column": "record_type",
        "destination_column": "ServiceProviderName",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "CONST",
            "then_config": {"value": "Anthropic"},
        },
    },
    {
        "source_column": "record_type",
        "destination_column": "InvoiceIssuerName",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "CONST",
            "then_config": {"value": "Anthropic"},
        },
    },
    {
        "source_column": "record_type",
        "destination_column": "ServiceName",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "CONST",
            "then_config": {"value": "Claude"},
        },
    },
    {
        "source_column": "record_type",
        "destination_column": "ServiceCategory",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "CONST",
            "then_config": {"value": "AI"},
        },
    },
    {
        "source_column": "record_type",
        "destination_column": "PricingCategory",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["cost_report"],
            "then_type": "CONST",
            "then_config": {"value": "AI"},
        },
    },
]

NOTEBOOKLM_BILLING_FOCUS: list[dict] = [
    {
        "source_column": "cost",
        "destination_column": "BilledCost",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "RENAME",
            "then_source": "cost",
        },
    },
    {
        "source_column": "cost",
        "destination_column": "EffectiveCost",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "RENAME",
            "then_source": "cost",
        },
    },
    {
        "source_column": "currency",
        "destination_column": "BillingCurrency",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "RENAME",
            "then_source": "currency",
        },
    },
    {
        "source_column": "currency",
        "destination_column": "PricingCurrency",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "RENAME",
            "then_source": "currency",
        },
    },
    {
        "source_column": "usage_start_time",
        "destination_column": "ChargePeriodStart",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "RENAME",
            "then_source": "usage_start_time",
        },
    },
    {
        "source_column": "usage_end_time",
        "destination_column": "ChargePeriodEnd",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "RENAME",
            "then_source": "usage_end_time",
        },
    },
    {
        "source_column": "invoice_month",
        "destination_column": "BillingPeriodStart",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "MONTH_START",
            "then_source": "invoice_month",
        },
    },
    {
        "source_column": "invoice_month",
        "destination_column": "BillingPeriodEnd",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "MONTH_END",
            "then_source": "invoice_month",
        },
    },
    {
        "source_column": "usage_amount",
        "destination_column": "ConsumedQuantity",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "RENAME",
            "then_source": "usage_amount",
        },
    },
    {
        "source_column": "usage_unit",
        "destination_column": "ConsumedUnit",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "RENAME",
            "then_source": "usage_unit",
        },
    },
    {
        "source_column": "usage_amount",
        "destination_column": "PricingQuantity",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "RENAME",
            "then_source": "usage_amount",
        },
    },
    {
        "source_column": "usage_unit",
        "destination_column": "PricingUnit",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "RENAME",
            "then_source": "usage_unit",
        },
    },
    {
        "source_column": "sku_description",
        "destination_column": "ChargeDescription",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "RENAME",
            "then_source": "sku_description",
        },
    },
    {
        "source_column": "service_description",
        "destination_column": "ServiceName",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "RENAME",
            "then_source": "service_description",
        },
    },
    {
        "source_column": "service_description",
        "destination_column": "PricingCategory",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "RENAME",
            "then_source": "service_description",
        },
    },
    {
        "source_column": "sku_description",
        "destination_column": "ServiceSubcategory",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "RENAME",
            "then_source": "sku_description",
        },
    },
    {
        "source_column": "sku_id",
        "destination_column": "SkuId",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "RENAME",
            "then_source": "sku_id",
        },
    },
    {
        "source_column": "sku_description",
        "destination_column": "SkuMeter",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "RENAME",
            "then_source": "sku_description",
        },
    },
    {
        "source_column": "user_email",
        "destination_column": "ResourceId",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "RENAME",
            "then_source": "user_email",
        },
    },
    {
        "source_column": "project_id",
        "destination_column": "SubAccountId",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "RENAME",
            "then_source": "project_id",
        },
    },
    {
        "source_column": "billing_account_id",
        "destination_column": "BillingAccountId",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "RENAME",
            "then_source": "billing_account_id",
        },
    },
    {
        "source_column": "region",
        "destination_column": "RegionName",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "RENAME",
            "then_source": "region",
        },
    },
    {
        "source_column": "region",
        "destination_column": "RegionId",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "RENAME",
            "then_source": "region",
        },
    },
    {
        "source_column": "record_type",
        "destination_column": "ChargeCategory",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "CONST",
            "then_config": {"value": "Usage"},
        },
    },
    {
        "source_column": "record_type",
        "destination_column": "ChargeFrequency",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "CONST",
            "then_config": {"value": "Usage-Based"},
        },
    },
    {
        "source_column": "record_type",
        "destination_column": "ServiceProviderName",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "CONST",
            "then_config": {"value": "Google Cloud"},
        },
    },
    {
        "source_column": "record_type",
        "destination_column": "InvoiceIssuerName",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "CONST",
            "then_config": {"value": "Google Cloud"},
        },
    },
    {
        "source_column": "record_type",
        "destination_column": "ServiceCategory",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "record_type",
            "when_values": ["billing_export"],
            "then_type": "CONST",
            "then_config": {"value": "AI"},
        },
    },
]

FOCUS_MAPPINGS_BY_CONNECTOR = {
    "claude_token": CLAUDE_COST_FOCUS,
    "notebooklm_token": NOTEBOOKLM_BILLING_FOCUS,
}


def _upsert_mapping(
    session,
    *,
    conn_id: int,
    dest_table_id: int,
    cloud_label: str,
    mapping: dict,
    priority: int,
) -> None:
    cfg = dict(mapping.get("transform_config") or {})
    dest = mapping["destination_column"]
    src = mapping["source_column"]
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
        "did": dest_table_id,
        "name": f"{cloud_label} FOCUS: {src} -> {dest}",
        "src": src,
        "dest": dest,
        "ttype": mapping["transform_type"],
        "cfg": json.dumps(cfg),
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
        "did": dest_table_id,
        "name": f"{cloud_label} FOCUS: {src} -> {dest}",
        "src": src,
        "dest": dest,
        "ttype": mapping["transform_type"],
        "cfg": json.dumps(cfg),
        "prio": priority,
    })


def seed(session) -> dict[str, int]:
    focus_dest = session.execute(
        select(DestinationTable).where(DestinationTable.physical_name == FOCUS_PHYSICAL_NAME)
    ).scalar_one_or_none()
    if not focus_dest:
        raise RuntimeError(
            f"Destination {FOCUS_PHYSICAL_NAME!r} not found — run seed_focus_v14.py first"
        )

    connections = session.execute(
        select(SourceConnection).where(
            SourceConnection.connector_type.in_(FOCUS_MAPPINGS_BY_CONNECTOR.keys())
        )
    ).scalars().all()

    counts = {"mappings": 0, "connections": 0}
    for conn in connections:
        mappings = FOCUS_MAPPINGS_BY_CONNECTOR.get(conn.connector_type, [])
        if not mappings:
            continue
        counts["connections"] += 1
        label = conn.code or conn.name
        for priority, mapping in enumerate(mappings, start=900):
            _upsert_mapping(
                session,
                conn_id=conn.id,
                dest_table_id=focus_dest.id,
                cloud_label=label,
                mapping=mapping,
                priority=priority,
            )
            counts["mappings"] += 1
    return counts


def main() -> None:
    session = SyncSession()
    try:
        result = seed(session)
        session.commit()
        print(
            "AI FOCUS cost mappings seeded: "
            f"{result['mappings']} mappings across {result['connections']} connections"
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
