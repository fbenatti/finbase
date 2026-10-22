"""
Seed MongoDB Atlas (bronze raw_{connection_code}) → BILLING_CLOUD mappings.

O extrator `app/services/mongodb_extractor.py` grava line items achatados das
invoices da Atlas Admin API com as colunas: sku, clusterName, quantity,
unitPrice, totalPriceCents, startDate, endDate, groupId, invoiceId e, quando
`config_json.marketplace` é true, `marketplace = True`.

Observação: não existe transform "divide por 100" dedicado; usamos EXPR com
`totalPriceCents / 100` para converter centavos em unidade monetária.

Usage: cd apps/api && python seed_mongodb_focus_mappings.py
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
from seed_marketplace_focus import (
    _seed_mappings_for_connections,
    ensure_marketplace_focus,
)

DEST_TABLE_ID = 1
CONNECTOR_TYPE = "mongodb_atlas"
CLOUD_LABEL = "MongoDB Atlas"

_CENTS = {"expression": "totalPriceCents / 100"}

MONGODB_FOCUS_MAPPINGS: list[dict] = [
    # Timeframe (suporta colunas CSV 'Date', 'Usage Date' e fallback JSON 'startDate', 'endDate')
    {"source_column": "Date", "destination_column": "ChargePeriodStart", "transform_type": "RENAME"},
    {"source_column": "Date", "destination_column": "ChargePeriodEnd", "transform_type": "RENAME"},
    {"source_column": "Date", "destination_column": "BillingPeriodStart", "transform_type": "RENAME"},
    {"source_column": "Date", "destination_column": "BillingPeriodEnd", "transform_type": "RENAME"},
    {"source_column": "startDate", "destination_column": "ChargePeriodStart", "transform_type": "RENAME"},
    {"source_column": "endDate", "destination_column": "ChargePeriodEnd", "transform_type": "RENAME"},

    # Costs (CSV 'Amount' já vem em USD)
    {"source_column": "Amount", "destination_column": "BilledCost", "transform_type": "RENAME"},
    {"source_column": "Amount", "destination_column": "EffectiveCost", "transform_type": "RENAME"},
    {"source_column": "Amount", "destination_column": "ListCost", "transform_type": "RENAME"},
    {"source_column": "Amount", "destination_column": "ContractedCost", "transform_type": "RENAME"},
    {"source_column": "Amount", "destination_column": "PricingCurrencyEffectiveCost", "transform_type": "RENAME"},

    # Quantities & Pricing
    {"source_column": "Quantity", "destination_column": "ConsumedQuantity", "transform_type": "RENAME"},
    {"source_column": "Quantity", "destination_column": "PricingQuantity", "transform_type": "RENAME"},
    {"source_column": "quantity", "destination_column": "ConsumedQuantity", "transform_type": "RENAME"},
    {"source_column": "Unit", "destination_column": "ConsumedUnit", "transform_type": "RENAME"},
    {"source_column": "Unit", "destination_column": "PricingUnit", "transform_type": "RENAME"},
    {"source_column": "Unit Price", "destination_column": "ContractedUnitPrice", "transform_type": "RENAME"},
    {"source_column": "Unit Price", "destination_column": "ListUnitPrice", "transform_type": "RENAME"},
    {"source_column": "Unit Price", "destination_column": "PricingCurrencyContractedUnitPrice", "transform_type": "RENAME"},
    {"source_column": "Unit Price", "destination_column": "PricingCurrencyListUnitPrice", "transform_type": "RENAME"},
    {"source_column": "unitPrice", "destination_column": "ContractedUnitPrice", "transform_type": "RENAME"},

    # Description & SKU
    {"source_column": "SKU", "destination_column": "SkuId", "transform_type": "RENAME"},
    {"source_column": "SKU", "destination_column": "SkuMeter", "transform_type": "RENAME"},
    {"source_column": "SKU", "destination_column": "SkuPriceId", "transform_type": "RENAME"},
    {"source_column": "Description", "destination_column": "ChargeDescription", "transform_type": "RENAME"},
    {"source_column": "Description", "destination_column": "ServiceSubcategory", "transform_type": "RENAME"},
    {"source_column": "sku", "destination_column": "SkuId", "transform_type": "RENAME"},

    # Resource, Project & Region
    {"source_column": "Project", "destination_column": "source_resource_group_name", "transform_type": "RENAME"},
    {"source_column": "Project", "destination_column": "ResourceName", "transform_type": "RENAME"},
    {"source_column": "Cluster", "destination_column": "ResourceId", "transform_type": "RENAME"},
    {"source_column": "Region", "destination_column": "RegionName", "transform_type": "RENAME"},
    {"source_column": "Region", "destination_column": "RegionId", "transform_type": "RENAME"},

    # Organization / Account
    {"source_column": "Organization ID", "destination_column": "SubAccountId", "transform_type": "RENAME"},
    {"source_column": "Organization ID", "destination_column": "BillingAccountId", "transform_type": "RENAME"},
    {"source_column": "Organization Name", "destination_column": "BillingAccountName", "transform_type": "RENAME"},
    {"source_column": "Organization Name", "destination_column": "SubAccountName", "transform_type": "RENAME"},
    {"source_column": "groupId", "destination_column": "SubAccountId", "transform_type": "RENAME"},

    # Invoice
    {"source_column": "invoiceId", "destination_column": "InvoiceId", "transform_type": "RENAME"},

    # Cloud Constants
    {"source_column": "Organization ID", "destination_column": "HostProviderName", "transform_type": "CONST", "transform_config": {"value": CLOUD_LABEL}},
    {"source_column": "Organization ID", "destination_column": "ServiceProviderName", "transform_type": "CONST", "transform_config": {"value": CLOUD_LABEL}},
    {"source_column": "Organization ID", "destination_column": "InvoiceIssuerName", "transform_type": "CONST", "transform_config": {"value": "MongoDB"}},
    {"source_column": "Organization ID", "destination_column": "ServiceName", "transform_type": "CONST", "transform_config": {"value": CLOUD_LABEL}},
    {"source_column": "Organization ID", "destination_column": "ServiceCategory", "transform_type": "CONST", "transform_config": {"value": "Databases"}},
    {"source_column": "Organization ID", "destination_column": "ChargeCategory", "transform_type": "CONST", "transform_config": {"value": "Usage"}},
    {"source_column": "Organization ID", "destination_column": "ChargeFrequency", "transform_type": "CONST", "transform_config": {"value": "Usage-Based"}},
    {"source_column": "Organization ID", "destination_column": "BillingCurrency", "transform_type": "CONST", "transform_config": {"value": "USD"}},
    {"source_column": "Organization ID", "destination_column": "PricingCurrency", "transform_type": "CONST", "transform_config": {"value": "USD"}},
]

# Colunas Marketplace* — aplicadas apenas quando a conexão marca
# `config_json.marketplace = true` (o bronze traz `marketplace = True`).
MONGODB_MARKETPLACE_MAPPINGS: list[dict] = [
    {
        "source_column": "sku",
        "destination_column": "MarketplaceProductName",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "marketplace",
            "when_op": "not_empty",
            "then_type": "RENAME",
        },
    },
    {
        "source_column": "sku",
        "destination_column": "MarketplaceOfferUnit",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "marketplace",
            "when_op": "not_empty",
            "then_type": "CONST",
            "then_config": {"value": "UNIT"},
        },
    },
    {
        "source_column": "marketplace",
        "destination_column": "MarketplaceBillingModel",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "marketplace",
            "when_op": "not_empty",
            "then_type": "CONST",
            "then_config": {"value": "RECURRING"},
        },
    },
]


def seed_mongodb_focus_mappings(session) -> int:
    """Insere (idempotente) os mappings FOCUS para conexões mongodb_atlas."""
    connections = session.execute(
        select(SourceConnection).where(
            SourceConnection.connector_type == CONNECTOR_TYPE
        )
    ).scalars().all()
    inserted = 0
    inserted += _seed_mappings_for_connections(
        session,
        connections=connections,
        mappings=MONGODB_FOCUS_MAPPINGS,
        base_priority=1,
    )
    marketplace_conns = [
        c for c in connections if (c.config_json or {}).get("marketplace")
    ]
    inserted += _seed_mappings_for_connections(
        session,
        connections=marketplace_conns,
        mappings=MONGODB_MARKETPLACE_MAPPINGS,
        base_priority=950,
    )
    return inserted


def ensure_mongodb_focus(session=None) -> dict[str, int]:
    own_session = session is None
    session = session or SyncSession()
    try:
        dest = session.execute(
            select(DestinationTable).where(DestinationTable.id == DEST_TABLE_ID)
        ).scalar_one_or_none()
        if not dest:
            return {"mappings": 0}
        # Garante as colunas Marketplace* na tabela de destino (idempotente).
        ensure_marketplace_focus(session)
        mappings = seed_mongodb_focus_mappings(session)
        if own_session:
            session.commit()
        return {"mappings": mappings}
    except Exception:
        if own_session:
            session.rollback()
        raise
    finally:
        if own_session:
            session.close()


def main() -> None:
    result = ensure_mongodb_focus()
    print(f"MongoDB Atlas FOCUS mappings seeded: {result}")


if __name__ == "__main__":
    main()
