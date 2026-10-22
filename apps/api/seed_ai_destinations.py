"""
Seed LICENCAS IA and CONSUMO IA destination tables with canonical AI columns.

Usage: cd apps/api && python seed_ai_destinations.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app.database import SyncSession
from app.models.destination import DestinationColumn, DestinationTable

PLATFORM_COLUMNS = [
    ("cloud_origem", "TEXT"),
    ("source_connection_id", "INTEGER"),
    ("_billing_period", "TEXT"),
    ("tipo", "TEXT"),
    ("organograma", "TEXT"),
    ("centro_custo", "TEXT"),
    ("hierarquia_1", "TEXT"),
    ("hierarquia_2", "TEXT"),
    ("hierarquia_3", "TEXT"),
    ("hierarquia_4", "TEXT"),
    ("hierarquia_5", "TEXT"),
    ("hierarquia_6", "TEXT"),
    ("hierarquia_7", "TEXT"),
]

LICENSE_COLUMNS = [
    # Identity
    ("UserEmail", "TEXT"),
    ("UserLogin", "TEXT"),
    ("UserId", "TEXT"),
    ("UserName", "TEXT"),
    ("UserType", "TEXT"),
    ("UserRole", "TEXT"),
    ("IsAdmin", "BOOLEAN"),
    ("IsDelegatedAdmin", "BOOLEAN"),
    ("HasSso", "BOOLEAN"),
    ("Is2SVEnrolled", "BOOLEAN"),
    ("EmailResolutionSource", "TEXT"),
    ("AvatarUrl", "TEXT"),
    ("ProfileUrl", "TEXT"),
    # License / product
    ("Provider", "TEXT"),
    ("ProductName", "TEXT"),
    ("LicenseSkuId", "TEXT"),
    ("LicenseSkuName", "TEXT"),
    ("LicenseQuantity", "INTEGER"),
    ("LicenseStatus", "TEXT"),
    ("SuspensionReason", "TEXT"),
    # Organization
    ("TeamId", "TEXT"),
    ("TeamName", "TEXT"),
    ("TeamSlug", "TEXT"),
    ("TeamPrivacy", "TEXT"),
    ("OrgUnitPath", "TEXT"),
    ("WorkspaceId", "TEXT"),
    ("OrganizationId", "TEXT"),
    ("ProjectId", "TEXT"),
    ("ProjectNumber", "TEXT"),
    # Lifecycle / activity
    ("LicenseAssignedAt", "TIMESTAMPTZ"),
    ("LicenseUpdatedAt", "TIMESTAMPTZ"),
    ("LicenseCancellationDate", "TIMESTAMPTZ"),
    ("LastActivityAt", "TIMESTAMPTZ"),
    ("LastActivityEditor", "TEXT"),
    ("SnapshotDate", "TIMESTAMPTZ"),
    ("DaysSinceLastActivity", "INTEGER"),
    ("ActivityStatus", "TEXT"),
    # Cost attribution (portal)
    ("AllocatedLicenseCost", "NUMERIC"),
    ("CostCurrency", "TEXT"),
    ("CostAllocationMethod", "TEXT"),
    ("CostSourceRef", "TEXT"),
    ("IsCostEstimated", "BOOLEAN"),
    # Honesty grain
    ("AggregationLevel", "TEXT"),
    ("IsAllocated", "BOOLEAN"),
]

CONSUMPTION_COLUMNS = [
    # Keys / time
    ("UsageDate", "TEXT"),
    ("UsageStartTime", "TIMESTAMPTZ"),
    ("UsageEndTime", "TIMESTAMPTZ"),
    ("InvoiceMonth", "TEXT"),
    ("UserEmail", "TEXT"),
    ("UserLogin", "TEXT"),
    ("Provider", "TEXT"),
    ("ProductName", "TEXT"),
    ("ModelName", "TEXT"),
    ("AggregationLevel", "TEXT"),
    ("IsAllocated", "BOOLEAN"),
    # Tokens
    ("InputTokens", "NUMERIC"),
    ("OutputTokens", "NUMERIC"),
    ("CacheReadTokens", "NUMERIC"),
    ("CacheWriteTokens", "NUMERIC"),
    ("TotalTokens", "NUMERIC"),
    ("RequestCount", "NUMERIC"),
    ("SourceKind", "TEXT"),
    # GitHub coding activity
    ("SuggestionsCount", "NUMERIC"),
    ("AcceptancesCount", "NUMERIC"),
    ("LinesSuggested", "NUMERIC"),
    ("LinesAccepted", "NUMERIC"),
    ("ChatTurns", "NUMERIC"),
    ("ChatAcceptances", "NUMERIC"),
    ("ActiveUsersCount", "NUMERIC"),
    ("Editor", "TEXT"),
    ("Language", "TEXT"),
    # Google activity
    ("AudioDurationSeconds", "NUMERIC"),
    ("NotebookCount", "NUMERIC"),
    # Anthropic event trail
    ("RecordType", "TEXT"),
    ("EventId", "TEXT"),
    ("EventType", "TEXT"),
    ("RequestId", "TEXT"),
    ("ActorType", "TEXT"),
    ("ActorIpAddress", "TEXT"),
    ("ActorUserAgent", "TEXT"),
    ("ActorApiKeyId", "TEXT"),
    ("RequestMethod", "TEXT"),
    ("StatusCode", "NUMERIC"),
    ("EndpointUrl", "TEXT"),
    ("RequestPayload", "JSONB"),
    ("OrganizationId", "TEXT"),
    ("OrganizationUuid", "TEXT"),
    # Cost / billing
    ("BilledCost", "NUMERIC"),
    ("CostCurrency", "TEXT"),
    ("CurrencyConversionRate", "NUMERIC"),
    ("CreditsAmount", "NUMERIC"),
    ("GrossAmount", "NUMERIC"),
    ("DiscountAmount", "NUMERIC"),
    ("NetAmount", "NUMERIC"),
    ("BillingCurrency", "TEXT"),
    ("CostType", "TEXT"),
    ("ConsumedQuantity", "NUMERIC"),
    ("ConsumedUnit", "TEXT"),
    ("PricingQuantity", "NUMERIC"),
    ("PricingUnit", "TEXT"),
    ("BillingAccountId", "TEXT"),
    ("ServiceId", "TEXT"),
    ("ServiceName", "TEXT"),
    ("SkuId", "TEXT"),
    ("SkuName", "TEXT"),
    ("ProjectId", "TEXT"),
    ("ProjectName", "TEXT"),
    ("ProjectNumber", "TEXT"),
    ("Location", "TEXT"),
    ("Country", "TEXT"),
    ("Region", "TEXT"),
    ("Zone", "TEXT"),
    # Cost attribution (portal)
    ("AllocatedCost", "NUMERIC"),
    ("CostAllocationMethod", "TEXT"),
    ("CostSourceRef", "TEXT"),
    ("IsCostEstimated", "BOOLEAN"),
]

DESTINATIONS = [
    {
        "name": "LICENCAS IA",
        "description": "Snapshot mensal normalizado de licenças de IA por usuário/provedor",
        "physical_name": "dest_licencas_ia",
        "columns": LICENSE_COLUMNS + PLATFORM_COLUMNS,
        "required": {"UserEmail", "Provider", "ProductName", "AggregationLevel"},
    },
    {
        "name": "CONSUMO IA",
        "description": "Consumo normalizado de IA — tokens, atividade e custo por usuário ou organização",
        "physical_name": "dest_consumo_ia",
        "columns": CONSUMPTION_COLUMNS + PLATFORM_COLUMNS,
        "required": {"Provider", "ProductName", "AggregationLevel"},
    },
]


def _seed_destination_table(db, spec: dict) -> tuple[int, int]:
    dt = db.query(DestinationTable).filter(DestinationTable.physical_name == spec["physical_name"]).first()
    if not dt:
        dt = DestinationTable(
            name=spec["name"],
            description=spec["description"],
            physical_name=spec["physical_name"],
            active=True,
        )
        db.add(dt)
        db.flush()
        print(f"Created destination table: {spec['name']} (id={dt.id})")
    else:
        dt.name = spec["name"]
        dt.description = spec["description"]
        dt.active = True
        db.flush()
        print(f"Updated destination table: {spec['name']} (id={dt.id})")

    db.query(DestinationColumn).filter(
        DestinationColumn.destination_table_id == dt.id
    ).delete()
    db.flush()

    required = spec.get("required") or set()
    for pos, (col_name, data_type) in enumerate(spec["columns"], start=1):
        db.add(
            DestinationColumn(
                destination_table_id=dt.id,
                column_name=col_name,
                data_type=data_type,
                is_required=col_name in required,
                position=pos,
            )
        )

    return dt.id, len(spec["columns"])


def seed() -> dict[str, int]:
    db = SyncSession()
    counts: dict[str, int] = {}
    try:
        for spec in DESTINATIONS:
            _table_id, col_count = _seed_destination_table(db, spec)
            counts[spec["physical_name"]] = col_count
        db.commit()
        print(
            f"Seeded AI destinations: "
            f"dest_licencas_ia={counts['dest_licencas_ia']} cols, "
            f"dest_consumo_ia={counts['dest_consumo_ia']} cols"
        )
        return counts
    except Exception as exc:
        db.rollback()
        print(f"ERROR: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
