"""
Ensure Marketplace FOCUS columns and provider mappings (OCI/Azure/GCP).

Usage: cd apps/api && python seed_marketplace_focus.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import or_, select, text

from app.database import SyncSession
from app.models.destination import DestinationTable
from app.models.source_connection import SourceConnection

DEST_TABLE_ID = 1

MARKETPLACE_COLUMNS = [
    ("MarketplaceProductName", "TEXT"),
    ("MarketplaceOfferUnit", "TEXT"),
    ("MarketplaceBillingModel", "TEXT"),
]

GCP_CONNECTIONS = {
    3: "GCP-4P",
    4: "GCP-TI",
    8: "GCP-TTECH",
}

# GCP BigQuery marks marketplace rows with transaction_type = THIRD_PARTY_RESELLER
# (the ``invoice.publisher_type`` flag only exists in Azure).  The marketplace
# product lives in ``service.description`` ("Denodo Enterprise Plus") and the
# offer unit in ``usage.pricing_unit``.
GCP_MARKETPLACE_MAPPINGS: list[dict] = [
    {
        "source_column": "sku",
        "destination_column": "MarketplaceProductName",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "transaction_type",
            "when_values": ["THIRD_PARTY_RESELLER"],
            "then_source": "service",
            "then_type": "JSON_PATH",
            "then_config": {"path": "description"},
        },
    },
    {
        "source_column": "usage",
        "destination_column": "MarketplaceOfferUnit",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "transaction_type",
            "when_values": ["THIRD_PARTY_RESELLER"],
            "then_source": "usage",
            "then_type": "JSON_PATH",
            "then_config": {"path": "pricing_unit"},
        },
    },
    {
        "source_column": "invoice",
        "destination_column": "MarketplaceBillingModel",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "transaction_type",
            "when_values": ["THIRD_PARTY_RESELLER"],
            "then_type": "CONST",
            "then_config": {"value": "RECURRING"},
        },
    },
]

AZURE_MARKETPLACE_MAPPINGS: list[dict] = [
    {
        "source_column": "productname",
        "destination_column": "MarketplaceProductName",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "publisherType",
            "when_values": ["Marketplace"],
            "then_type": "RENAME",
        },
    },
    {
        "source_column": "unitofmeasure",
        "destination_column": "MarketplaceOfferUnit",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "publisherType",
            "when_values": ["Marketplace"],
            "then_type": "RENAME",
        },
    },
    {
        "source_column": "frequency",
        "destination_column": "MarketplaceBillingModel",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "publisherType",
            "when_values": ["Marketplace"],
            "then_type": "VALUE_MAP",
            "then_config": {
                "map": {"Recurring": "RECURRING", "UsageBased": "RECURRING"},
                "default": "ONESHOT",
            },
        },
    },
]

OCI_MARKETPLACE_MAPPINGS: list[dict] = [
    {
        "source_column": "cost/productSku",
        "destination_column": "MarketplaceProductName",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "product/service",
            "when_values": ["MARKETPLACE"],
            "then_type": "RENAME",
        },
    },
    {
        "source_column": "cost/billingUnitReadable",
        "destination_column": "MarketplaceOfferUnit",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "product/service",
            "when_values": ["MARKETPLACE"],
            "then_type": "RENAME",
        },
    },
    {
        "source_column": "cost/billingUnitReadable",
        "destination_column": "MarketplaceBillingModel",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "product/service",
            "when_values": ["MARKETPLACE"],
            "then_type": "VALUE_MAP",
            "then_config": {"map": {"ONE PRIVATE OFFER": "ONESHOT"}, "default": "RECURRING"},
        },
    },
    {
        "source_column": "tags/orcl-marketplace.listing-id",
        "destination_column": "MarketplaceProductName",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "tags/orcl-marketplace.listing-id",
            "when_op": "not_empty",
            "then_type": "RENAME",
        },
    },
    {
        "source_column": "cost/billingUnitReadable",
        "destination_column": "MarketplaceOfferUnit",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "tags/orcl-marketplace.listing-id",
            "when_op": "not_empty",
            "then_type": "RENAME",
        },
    },
    {
        "source_column": "cost/billingUnitReadable",
        "destination_column": "MarketplaceBillingModel",
        "transform_type": "CONDITIONAL",
        "transform_config": {
            "when_column": "tags/orcl-marketplace.listing-id",
            "when_op": "not_empty",
            "then_type": "CONST",
            "then_config": {"value": "USAGE"},
        },
    },
]


def ensure_destination_columns(session) -> None:
    max_pos = session.execute(text(
        "SELECT COALESCE(MAX(position), 0) FROM destination_column WHERE destination_table_id = :did"
    ), {"did": DEST_TABLE_ID}).scalar() or 0
    for idx, (name, dtype) in enumerate(MARKETPLACE_COLUMNS, start=max_pos + 1):
        session.execute(text("""
            INSERT INTO destination_column (destination_table_id, column_name, data_type, is_required, position)
            SELECT :did, :name, :dtype, false, :pos
            WHERE NOT EXISTS (
                SELECT 1 FROM destination_column
                WHERE destination_table_id = :did AND column_name = :name
            )
        """), {"did": DEST_TABLE_ID, "name": name, "dtype": dtype, "pos": idx})


def _upsert_mapping(
    session,
    *,
    conn_id: int,
    cloud_label: str,
    mapping: dict,
    priority: int,
) -> None:
    cfg = dict(mapping.get("transform_config") or {})
    dest = mapping["destination_column"]
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
              AND source_column = :src
        )
    """), {
        "cid": conn_id,
        "did": DEST_TABLE_ID,
        "name": f"{cloud_label}: {mapping['source_column']} -> {dest}",
        "src": mapping["source_column"],
        "dest": dest,
        "ttype": mapping["transform_type"],
        "cfg": json.dumps(cfg),
        "prio": priority,
    })
    session.execute(text("""
        UPDATE source_destination_mapping
        SET transform_type = :ttype,
            transform_config = CAST(:cfg AS jsonb),
            name = :name,
            priority = :prio,
            active = true
        WHERE source_connection_id = :cid
          AND destination_table_id = :did
          AND destination_column = :dest
          AND source_column = :src
    """), {
        "cid": conn_id,
        "did": DEST_TABLE_ID,
        "name": f"{cloud_label}: {mapping['source_column']} -> {dest}",
        "src": mapping["source_column"],
        "dest": dest,
        "ttype": mapping["transform_type"],
        "cfg": json.dumps(cfg),
        "prio": priority,
    })


def _seed_mappings_for_connections(
    session,
    *,
    connections: list,
    mappings: list[dict],
    base_priority: int,
) -> int:
    inserted = 0
    for conn in connections:
        cloud_label = conn.code or conn.name
        for offset, mapping in enumerate(mappings):
            _upsert_mapping(
                session,
                conn_id=conn.id,
                cloud_label=cloud_label,
                mapping=mapping,
                priority=base_priority + offset,
            )
            inserted += 1
    return inserted


def seed_gcp_marketplace_mappings(session) -> int:
    connections = [
        session.get(SourceConnection, conn_id)
        for conn_id in GCP_CONNECTIONS
    ]
    connections = [c for c in connections if c is not None]
    return _seed_mappings_for_connections(
        session,
        connections=connections,
        mappings=GCP_MARKETPLACE_MAPPINGS,
        base_priority=950,
    )


def seed_azure_marketplace_mappings(session) -> int:
    connections = session.execute(
        select(SourceConnection).where(SourceConnection.code.ilike("azure%"))
    ).scalars().all()
    for conn in connections:
        session.execute(text("""
            DELETE FROM source_destination_mapping
            WHERE source_connection_id = :cid
              AND destination_table_id = :did
              AND destination_column IN ('MarketplaceProductName', 'MarketplaceOfferUnit', 'MarketplaceBillingModel')
              AND source_column IN ('productName', 'unitOfMeasure')
        """), {"cid": conn.id, "did": DEST_TABLE_ID})
    return _seed_mappings_for_connections(
        session,
        connections=connections,
        mappings=AZURE_MARKETPLACE_MAPPINGS,
        base_priority=950,
    )


def seed_oci_marketplace_mappings(session) -> int:
    connections = session.execute(
        select(SourceConnection).where(
            or_(
                SourceConnection.connector_type == "oci",
                SourceConnection.code == "oci-prod",
            )
        )
    ).scalars().all()
    return _seed_mappings_for_connections(
        session,
        connections=connections,
        mappings=OCI_MARKETPLACE_MAPPINGS,
        base_priority=950,
    )


def ensure_marketplace_focus(session=None) -> dict[str, int]:
    own_session = session is None
    session = session or SyncSession()
    try:
        dest = session.execute(
            select(DestinationTable).where(DestinationTable.id == DEST_TABLE_ID)
        ).scalar_one_or_none()
        if not dest:
            return {"columns": 0, "gcp_mappings": 0, "azure_mappings": 0, "oci_mappings": 0}
        ensure_destination_columns(session)
        gcp = seed_gcp_marketplace_mappings(session)
        azure = seed_azure_marketplace_mappings(session)
        oci = seed_oci_marketplace_mappings(session)
        if own_session:
            session.commit()
        return {
            "columns": len(MARKETPLACE_COLUMNS),
            "gcp_mappings": gcp,
            "azure_mappings": azure,
            "oci_mappings": oci,
        }
    except Exception:
        if own_session:
            session.rollback()
        raise
    finally:
        if own_session:
            session.close()


def main() -> None:
    result = ensure_marketplace_focus()
    print(f"Marketplace FOCUS seeded: {result}")


if __name__ == "__main__":
    main()
