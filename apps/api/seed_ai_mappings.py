"""
Seed source → destination mappings for the 8 AI connectors.

License connectors map to dest_licencas_ia; token connectors map to dest_consumo_ia.
Each bronze column is mapped to a canonical destination column via RENAME, CONST,
JSON_PATH, EXPR or CONDITIONAL as needed.

Usage: cd apps/api && python seed_ai_mappings.py
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

AI_CONNECTOR_TYPES = (
    "claude_license",
    "claude_token",
    "github_license",
    "github_token",
    "notebooklm_license",
    "notebooklm_token",
    "gemini_license",
    "gemini_token",
)

LICENSE_CONNECTORS = {
    "claude_license",
    "github_license",
    "notebooklm_license",
    "gemini_license",
}

TOKEN_CONNECTORS = {
    "claude_token",
    "github_token",
    "notebooklm_token",
    "gemini_token",
}

# ── Provider metadata (CONST) ────────────────────────────────────────────────

PROVIDER_META = {
    "claude_license": {"provider": "Anthropic", "product": "Claude", "agg": "user"},
    "claude_token": {"provider": "Anthropic", "product": "Claude"},
    "github_license": {"provider": "GitHub", "product": "Copilot", "agg": "user"},
    "github_token": {"provider": "GitHub", "product": "Copilot"},
    "notebooklm_license": {"provider": "Google", "product": "NotebookLM", "agg": "user"},
    "notebooklm_token": {"provider": "Google", "product": "NotebookLM"},
    "gemini_license": {"provider": "Google", "product": "Gemini", "agg": "user"},
    "gemini_token": {"provider": "Google", "product": "Gemini"},
}


def _const_mappings(meta: dict) -> list[dict]:
    anchor = meta["provider"]
    mappings = [
        {"source_column": anchor, "destination_column": "Provider", "transform_type": "CONST", "transform_config": {"value": meta["provider"]}},
        {"source_column": anchor, "destination_column": "ProductName", "transform_type": "CONST", "transform_config": {"value": meta["product"]}},
        {"source_column": anchor, "destination_column": "IsAllocated", "transform_type": "CONST", "transform_config": {"value": False}},
    ]
    if "agg" in meta:
        mappings.append({
            "source_column": anchor,
            "destination_column": "AggregationLevel",
            "transform_type": "CONST",
            "transform_config": {"value": meta["agg"]},
        })
    return mappings


# ── License mappings (bronze → dest_licencas_ia) ─────────────────────────────

CLAUDE_LICENSE_MAPPINGS: list[dict] = [
    {"source_column": "email", "destination_column": "UserEmail", "transform_type": "RENAME"},
    {"source_column": "id", "destination_column": "UserId", "transform_type": "RENAME"},
    {"source_column": "name", "destination_column": "UserName", "transform_type": "RENAME"},
    {"source_column": "role", "destination_column": "UserRole", "transform_type": "RENAME"},
    {"source_column": "status", "destination_column": "LicenseStatus", "transform_type": "RENAME"},
    {"source_column": "created_at", "destination_column": "LicenseAssignedAt", "transform_type": "RENAME"},
    {"source_column": "last_active_at", "destination_column": "LastActivityAt", "transform_type": "RENAME"},
    {"source_column": "has_sso", "destination_column": "HasSso", "transform_type": "RENAME"},
    {"source_column": "workspace_id", "destination_column": "WorkspaceId", "transform_type": "RENAME"},
    {"source_column": "organization_id", "destination_column": "OrganizationId", "transform_type": "RENAME"},
    {"source_column": "billing_plan", "destination_column": "LicenseSkuName", "transform_type": "RENAME"},
    {"source_column": "snapshot_date", "destination_column": "SnapshotDate", "transform_type": "RENAME"},
    *_const_mappings(PROVIDER_META["claude_license"]),
]

GITHUB_LICENSE_MAPPINGS: list[dict] = [
    {"source_column": "assignee_login", "destination_column": "UserLogin", "transform_type": "RENAME"},
    {"source_column": "assignee_id", "destination_column": "UserId", "transform_type": "RENAME"},
    {"source_column": "assignee_email", "destination_column": "UserEmail", "transform_type": "RENAME"},
    {"source_column": "email_resolution_source", "destination_column": "EmailResolutionSource", "transform_type": "RENAME"},
    {"source_column": "assignee_avatar_url", "destination_column": "AvatarUrl", "transform_type": "RENAME"},
    {"source_column": "assignee_html_url", "destination_column": "ProfileUrl", "transform_type": "RENAME"},
    {"source_column": "assignee_type", "destination_column": "UserType", "transform_type": "RENAME"},
    {"source_column": "assignee_site_admin", "destination_column": "IsAdmin", "transform_type": "RENAME"},
    {"source_column": "assigning_team_id", "destination_column": "TeamId", "transform_type": "RENAME"},
    {"source_column": "assigning_team_name", "destination_column": "TeamName", "transform_type": "RENAME"},
    {"source_column": "assigning_team_slug", "destination_column": "TeamSlug", "transform_type": "RENAME"},
    {"source_column": "assigning_team_privacy", "destination_column": "TeamPrivacy", "transform_type": "RENAME"},
    {"source_column": "created_at", "destination_column": "LicenseAssignedAt", "transform_type": "RENAME"},
    {"source_column": "updated_at", "destination_column": "LicenseUpdatedAt", "transform_type": "RENAME"},
    {"source_column": "pending_cancellation_date", "destination_column": "LicenseCancellationDate", "transform_type": "RENAME"},
    {"source_column": "last_activity_at", "destination_column": "LastActivityAt", "transform_type": "RENAME"},
    {"source_column": "last_activity_editor", "destination_column": "LastActivityEditor", "transform_type": "RENAME"},
    {"source_column": "plan_type", "destination_column": "LicenseSkuName", "transform_type": "RENAME"},
    {"source_column": "snapshot_date", "destination_column": "SnapshotDate", "transform_type": "RENAME"},
    *_const_mappings(PROVIDER_META["github_license"]),
]

NOTEBOOKLM_LICENSE_MAPPINGS: list[dict] = [
    {"source_column": "user_id", "destination_column": "UserId", "transform_type": "RENAME"},
    {"source_column": "primary_email", "destination_column": "UserEmail", "transform_type": "RENAME"},
    {"source_column": "full_name", "destination_column": "UserName", "transform_type": "RENAME"},
    {"source_column": "role_name", "destination_column": "UserRole", "transform_type": "RENAME"},
    {"source_column": "is_admin", "destination_column": "IsAdmin", "transform_type": "RENAME"},
    {"source_column": "is_delegated_admin", "destination_column": "IsDelegatedAdmin", "transform_type": "RENAME"},
    {"source_column": "org_unit_path", "destination_column": "OrgUnitPath", "transform_type": "RENAME"},
    {"source_column": "license_sku_id", "destination_column": "LicenseSkuId", "transform_type": "RENAME"},
    {"source_column": "license_sku_name", "destination_column": "LicenseSkuName", "transform_type": "RENAME"},
    {"source_column": "license_quantity", "destination_column": "LicenseQuantity", "transform_type": "RENAME"},
    {"source_column": "assigned_time", "destination_column": "LicenseAssignedAt", "transform_type": "RENAME"},
    {"source_column": "updated_time", "destination_column": "LicenseUpdatedAt", "transform_type": "RENAME"},
    {"source_column": "last_login_time", "destination_column": "LastActivityAt", "transform_type": "RENAME"},
    {"source_column": "suspension_reason", "destination_column": "SuspensionReason", "transform_type": "RENAME"},
    {"source_column": "is_enrolled_in_2sv", "destination_column": "Is2SVEnrolled", "transform_type": "RENAME"},
    {"source_column": "project_id", "destination_column": "ProjectId", "transform_type": "RENAME"},
    {"source_column": "project_number", "destination_column": "ProjectNumber", "transform_type": "RENAME"},
    {"source_column": "snapshot_date", "destination_column": "SnapshotDate", "transform_type": "RENAME"},
    {
        "source_column": "license_assignment_state",
        "destination_column": "LicenseStatus",
        "transform_type": "VALUE_MAP",
        "transform_config": {
            "map": {"ASSIGNED": "active", "assigned": "active", "": "active", "nan": "active"},
            "default": "inactive",
        },
    },
    *_const_mappings(PROVIDER_META["notebooklm_license"]),
]

GEMINI_LICENSE_MAPPINGS: list[dict] = [
    {"source_column": "username", "destination_column": "UserLogin", "transform_type": "RENAME"},
    {"source_column": "email", "destination_column": "UserEmail", "transform_type": "RENAME"},
    {"source_column": "user_id", "destination_column": "UserId", "transform_type": "RENAME"},
    {"source_column": "role", "destination_column": "UserRole", "transform_type": "RENAME"},
    {"source_column": "status", "destination_column": "LicenseStatus", "transform_type": "RENAME"},
    {"source_column": "license_type", "destination_column": "LicenseSkuName", "transform_type": "RENAME"},
    {"source_column": "project_id", "destination_column": "ProjectId", "transform_type": "RENAME"},
    {"source_column": "assigned_at", "destination_column": "LicenseAssignedAt", "transform_type": "RENAME"},
    {"source_column": "last_active_at", "destination_column": "LastActivityAt", "transform_type": "RENAME"},
    {"source_column": "snapshot_date", "destination_column": "SnapshotDate", "transform_type": "RENAME"},
    *_const_mappings(PROVIDER_META["gemini_license"]),
]

LICENSE_MAPPINGS_BY_CONNECTOR = {
    "claude_license": CLAUDE_LICENSE_MAPPINGS,
    "github_license": GITHUB_LICENSE_MAPPINGS,
    "notebooklm_license": NOTEBOOKLM_LICENSE_MAPPINGS,
    "gemini_license": GEMINI_LICENSE_MAPPINGS,
}

# ── Consumption mappings (bronze → dest_consumo_ia) ──────────────────────────

CLAUDE_TOKEN_MAPPINGS: list[dict] = [
    {"source_column": "record_type", "destination_column": "RecordType", "transform_type": "RENAME"},
    {"source_column": "record_type", "destination_column": "EventType", "transform_type": "RENAME"},
    {"source_column": "aggregation_level", "destination_column": "AggregationLevel", "transform_type": "RENAME"},
    {"source_column": "usage_date", "destination_column": "UsageDate", "transform_type": "RENAME"},
    {"source_column": "usage_date", "destination_column": "UsageStartTime", "transform_type": "RENAME"},
    {"source_column": "usage_date", "destination_column": "UsageEndTime", "transform_type": "RENAME"},
    {"source_column": "actor_email_address", "destination_column": "UserEmail", "transform_type": "RENAME"},
    {"source_column": "model", "destination_column": "ModelName", "transform_type": "RENAME"},
    {"source_column": "prompt_tokens", "destination_column": "InputTokens", "transform_type": "RENAME"},
    {"source_column": "completion_tokens", "destination_column": "OutputTokens", "transform_type": "RENAME"},
    {"source_column": "cache_read_input_tokens", "destination_column": "CacheReadTokens", "transform_type": "RENAME"},
    {"source_column": "cache_creation_input_tokens", "destination_column": "CacheWriteTokens", "transform_type": "RENAME"},
    {"source_column": "request_count", "destination_column": "RequestCount", "transform_type": "RENAME"},
    {"source_column": "organization_id", "destination_column": "OrganizationId", "transform_type": "RENAME"},
    {"source_column": "organization_uuid", "destination_column": "OrganizationUuid", "transform_type": "RENAME"},
    {"source_column": "api_key_id", "destination_column": "ActorApiKeyId", "transform_type": "RENAME"},
    {"source_column": "workspace_id", "destination_column": "ProjectId", "transform_type": "RENAME"},
    {"source_column": "cost_usd", "destination_column": "BilledCost", "transform_type": "RENAME"},
    {"source_column": "currency", "destination_column": "CostCurrency", "transform_type": "RENAME"},
    {"source_column": "prompt_tokens", "destination_column": "TotalTokens", "transform_type": "RENAME"},
    {"source_column": "prompt_tokens", "destination_column": "ConsumedQuantity", "transform_type": "RENAME"},
    {
        "source_column": "record_type",
        "destination_column": "ConsumedUnit",
        "transform_type": "CONST",
        "transform_config": {"value": "tokens"},
    },
    *_const_mappings(PROVIDER_META["claude_token"]),
]

GITHUB_TOKEN_MAPPINGS: list[dict] = [
    {"source_column": "day", "destination_column": "UsageDate", "transform_type": "RENAME"},
    {"source_column": "day", "destination_column": "UsageStartTime", "transform_type": "RENAME"},
    {"source_column": "day", "destination_column": "UsageEndTime", "transform_type": "RENAME"},
    {"source_column": "assignee_login", "destination_column": "UserLogin", "transform_type": "RENAME"},
    {"source_column": "aggregation_level", "destination_column": "AggregationLevel", "transform_type": "RENAME"},
    {"source_column": "model", "destination_column": "ModelName", "transform_type": "RENAME"},
    {"source_column": "total_suggestions_count", "destination_column": "SuggestionsCount", "transform_type": "RENAME"},
    {"source_column": "total_acceptances_count", "destination_column": "AcceptancesCount", "transform_type": "RENAME"},
    {"source_column": "total_lines_suggested", "destination_column": "LinesSuggested", "transform_type": "RENAME"},
    {"source_column": "total_lines_accepted", "destination_column": "LinesAccepted", "transform_type": "RENAME"},
    {"source_column": "total_chat_turns", "destination_column": "ChatTurns", "transform_type": "RENAME"},
    {"source_column": "total_chat_acceptances", "destination_column": "ChatAcceptances", "transform_type": "RENAME"},
    {"source_column": "total_active_users", "destination_column": "ActiveUsersCount", "transform_type": "RENAME"},
    {"source_column": "editor", "destination_column": "Editor", "transform_type": "RENAME"},
    {"source_column": "language", "destination_column": "Language", "transform_type": "RENAME"},
    *_const_mappings(PROVIDER_META["github_token"]),
]

NOTEBOOKLM_TOKEN_MAPPINGS: list[dict] = [
    {"source_column": "record_type", "destination_column": "RecordType", "transform_type": "RENAME"},
    {"source_column": "aggregation_level", "destination_column": "AggregationLevel", "transform_type": "RENAME"},
    {"source_column": "usage_start_time", "destination_column": "UsageStartTime", "transform_type": "RENAME"},
    {"source_column": "usage_end_time", "destination_column": "UsageEndTime", "transform_type": "RENAME"},
    {"source_column": "usage_start_time", "destination_column": "UsageDate", "transform_type": "RENAME"},
    {"source_column": "invoice_month", "destination_column": "InvoiceMonth", "transform_type": "RENAME"},
    {"source_column": "user_email", "destination_column": "UserEmail", "transform_type": "RENAME"},
    {"source_column": "model_id", "destination_column": "ModelName", "transform_type": "RENAME"},
    {"source_column": "prompt_token_count", "destination_column": "InputTokens", "transform_type": "RENAME"},
    {"source_column": "candidates_token_count", "destination_column": "OutputTokens", "transform_type": "RENAME"},
    {"source_column": "cached_content_token_count", "destination_column": "CacheReadTokens", "transform_type": "RENAME"},
    {"source_column": "request_count", "destination_column": "RequestCount", "transform_type": "RENAME"},
    {"source_column": "project_id", "destination_column": "ProjectId", "transform_type": "RENAME"},
    {"source_column": "project_number", "destination_column": "ProjectNumber", "transform_type": "RENAME"},
    {"source_column": "cost", "destination_column": "BilledCost", "transform_type": "RENAME"},
    {"source_column": "currency", "destination_column": "CostCurrency", "transform_type": "RENAME"},
    {"source_column": "usage_amount", "destination_column": "ConsumedQuantity", "transform_type": "RENAME"},
    {"source_column": "usage_unit", "destination_column": "ConsumedUnit", "transform_type": "RENAME"},
    {"source_column": "usage_amount", "destination_column": "PricingQuantity", "transform_type": "RENAME"},
    {"source_column": "usage_unit", "destination_column": "PricingUnit", "transform_type": "RENAME"},
    {"source_column": "billing_account_id", "destination_column": "BillingAccountId", "transform_type": "RENAME"},
    {"source_column": "service_id", "destination_column": "ServiceId", "transform_type": "RENAME"},
    {"source_column": "service_description", "destination_column": "ServiceName", "transform_type": "RENAME"},
    {"source_column": "sku_id", "destination_column": "SkuId", "transform_type": "RENAME"},
    {"source_column": "sku_description", "destination_column": "SkuName", "transform_type": "RENAME"},
    {"source_column": "project_name", "destination_column": "ProjectName", "transform_type": "RENAME"},
    {"source_column": "location", "destination_column": "Location", "transform_type": "RENAME"},
    {"source_column": "country", "destination_column": "Country", "transform_type": "RENAME"},
    {"source_column": "region", "destination_column": "Region", "transform_type": "RENAME"},
    {"source_column": "zone", "destination_column": "Zone", "transform_type": "RENAME"},
    {"source_column": "cost_type", "destination_column": "CostType", "transform_type": "RENAME"},
    {"source_column": "credits_amount", "destination_column": "CreditsAmount", "transform_type": "RENAME"},
    {"source_column": "prompt_token_count", "destination_column": "TotalTokens", "transform_type": "RENAME"},
    *_const_mappings(PROVIDER_META["notebooklm_token"]),
]

GEMINI_TOKEN_MAPPINGS: list[dict] = [
    {"source_column": "date", "destination_column": "UsageDate", "transform_type": "RENAME"},
    {"source_column": "date", "destination_column": "UsageStartTime", "transform_type": "RENAME"},
    {"source_column": "date", "destination_column": "UsageEndTime", "transform_type": "RENAME"},
    {"source_column": "username", "destination_column": "UserLogin", "transform_type": "RENAME"},
    {"source_column": "email", "destination_column": "UserEmail", "transform_type": "RENAME"},
    {"source_column": "model_version", "destination_column": "ModelName", "transform_type": "RENAME"},
    {"source_column": "project_id", "destination_column": "ProjectId", "transform_type": "RENAME"},
    {"source_column": "prompt_tokens", "destination_column": "InputTokens", "transform_type": "RENAME"},
    {"source_column": "completion_tokens", "destination_column": "OutputTokens", "transform_type": "RENAME"},
    {"source_column": "cached_tokens", "destination_column": "CacheReadTokens", "transform_type": "RENAME"},
    {"source_column": "audio_overview_seconds", "destination_column": "AudioDurationSeconds", "transform_type": "RENAME"},
    {"source_column": "total_tokens", "destination_column": "TotalTokens", "transform_type": "RENAME"},
    {"source_column": "request_count", "destination_column": "RequestCount", "transform_type": "RENAME"},
    {"source_column": "estimated_cost_usd", "destination_column": "BilledCost", "transform_type": "RENAME"},
    {"source_column": "aggregation_level", "destination_column": "AggregationLevel", "transform_type": "RENAME"},
    {"source_column": "total_tokens", "destination_column": "ConsumedQuantity", "transform_type": "RENAME"},
    {"source_column": "ai_product", "destination_column": "RecordType", "transform_type": "RENAME"},
    {
        "source_column": "ai_product",
        "destination_column": "ConsumedUnit",
        "transform_type": "CONST",
        "transform_config": {"value": "tokens"},
    },
    *_const_mappings(PROVIDER_META["gemini_token"]),
]

CONSUMPTION_MAPPINGS_BY_CONNECTOR = {
    "claude_token": CLAUDE_TOKEN_MAPPINGS,
    "github_token": GITHUB_TOKEN_MAPPINGS,
    "notebooklm_token": NOTEBOOKLM_TOKEN_MAPPINGS,
    "gemini_token": GEMINI_TOKEN_MAPPINGS,
}

# Bronze column coverage assertions (every extractor field must appear as source_column)
LICENSE_BRONZE_FIELDS = {
    "claude_license": {
        "id", "email", "name", "role", "status", "created_at", "last_active_at",
        "has_sso", "workspace_id", "organization_id", "billing_plan", "snapshot_date",
    },
    "github_license": {
        "assignee_login", "assignee_id", "assignee_email", "email_resolution_source",
        "assignee_avatar_url", "assignee_html_url", "assignee_type", "assignee_site_admin",
        "assigning_team_id", "assigning_team_name", "assigning_team_slug", "assigning_team_privacy",
        "created_at", "updated_at", "pending_cancellation_date", "last_activity_at",
        "last_activity_editor", "plan_type", "snapshot_date",
    },
    "notebooklm_license": {
        "user_id", "primary_email", "full_name", "role_name", "is_admin", "is_delegated_admin",
        "org_unit_path", "license_sku_id", "license_sku_name", "license_quantity", "assigned_time",
        "created_time", "updated_time", "last_login_time", "license_assignment_state", "license_config",
        "suspension_reason", "is_enrolled_in_2sv", "identity_source", "project_id", "project_number", "snapshot_date",
    },
    "gemini_license": {
        "username", "email", "user_id", "role", "status", "license_type", "project_id",
        "assigned_at", "last_active_at", "snapshot_date",
    },
}

CONSUMPTION_BRONZE_FIELDS = {
    "claude_token": {
        "record_type", "aggregation_level", "usage_date", "actor_email_address", "model",
        "prompt_tokens", "completion_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
        "request_count", "organization_id", "organization_uuid", "api_key_id", "workspace_id",
        "cost_usd", "currency",
    },
    "github_token": {
        "day", "assignee_login", "aggregation_level", "model", "total_suggestions_count",
        "total_acceptances_count", "total_lines_suggested", "total_lines_accepted",
        "total_chat_turns", "total_chat_acceptances", "total_active_users", "editor", "language",
    },
    "notebooklm_token": {
        "record_type", "aggregation_level", "usage_start_time", "usage_end_time", "invoice_month",
        "user_email", "model_id", "prompt_token_count", "candidates_token_count",
        "cached_content_token_count", "request_count", "project_id", "project_number", "cost",
        "currency", "usage_amount", "usage_unit", "billing_account_id", "service_id",
        "service_description", "sku_id", "sku_description", "project_name", "location", "country",
        "region", "zone", "cost_type", "credits_amount",
    },
    "gemini_token": {
        "date", "username", "email", "ai_product", "model_version", "project_id", "prompt_tokens",
        "completion_tokens", "cached_tokens", "audio_overview_seconds", "total_tokens",
        "request_count", "estimated_cost_usd", "aggregation_level",
    },
}


def _assert_bronze_coverage() -> None:
    for connector, fields in LICENSE_BRONZE_FIELDS.items():
        mapped = {m["source_column"] for m in LICENSE_MAPPINGS_BY_CONNECTOR[connector]}
        missing = fields - mapped
        if missing:
            raise RuntimeError(f"{connector} license bronze fields without mapping: {sorted(missing)}")
    for connector, fields in CONSUMPTION_BRONZE_FIELDS.items():
        mapped = {m["source_column"] for m in CONSUMPTION_MAPPINGS_BY_CONNECTOR[connector]}
        missing = fields - mapped
        if missing:
            raise RuntimeError(f"{connector} consumption bronze fields without mapping: {sorted(missing)}")


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
        "name": f"{cloud_label}: {src} -> {dest}",
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
        "name": f"{cloud_label}: {src} -> {dest}",
        "src": src,
        "dest": dest,
        "ttype": mapping["transform_type"],
        "cfg": json.dumps(cfg),
        "prio": priority,
    })


def _destination_id(session, physical_name: str) -> int:
    dest = session.execute(
        select(DestinationTable).where(DestinationTable.physical_name == physical_name)
    ).scalar_one_or_none()
    if not dest:
        raise RuntimeError(
            f"Destination table {physical_name!r} not found — run seed_ai_destinations.py first"
        )
    return dest.id


def seed(session) -> dict[str, int]:
    lic_dest_id = _destination_id(session, "dest_licencas_ia")
    cons_dest_id = _destination_id(session, "dest_consumo_ia")

    connections = session.execute(
        select(SourceConnection).where(SourceConnection.connector_type.in_(AI_CONNECTOR_TYPES))
    ).scalars().all()

    by_type: dict[str, list[SourceConnection]] = {}
    for conn in connections:
        by_type.setdefault(conn.connector_type, []).append(conn)

    counts = {"license_mappings": 0, "consumption_mappings": 0, "connections": 0}

    for connector_type in LICENSE_CONNECTORS:
        mappings = LICENSE_MAPPINGS_BY_CONNECTOR[connector_type]
        for conn in by_type.get(connector_type, []):
            counts["connections"] += 1
            label = conn.code or conn.name
            for priority, mapping in enumerate(mappings, start=1):
                _upsert_mapping(
                    session,
                    conn_id=conn.id,
                    dest_table_id=lic_dest_id,
                    cloud_label=label,
                    mapping=mapping,
                    priority=priority,
                )
                counts["license_mappings"] += 1

    for connector_type in TOKEN_CONNECTORS:
        mappings = CONSUMPTION_MAPPINGS_BY_CONNECTOR[connector_type]
        for conn in by_type.get(connector_type, []):
            counts["connections"] += 1
            label = conn.code or conn.name
            for priority, mapping in enumerate(mappings, start=1):
                _upsert_mapping(
                    session,
                    conn_id=conn.id,
                    dest_table_id=cons_dest_id,
                    cloud_label=label,
                    mapping=mapping,
                    priority=priority,
                )
                counts["consumption_mappings"] += 1

    return counts


def main() -> None:
    _assert_bronze_coverage()
    session = SyncSession()
    try:
        result = seed(session)
        session.commit()
        print(
            "AI destination mappings seeded: "
            f"{result['license_mappings']} license, "
            f"{result['consumption_mappings']} consumption "
            f"({result['connections']} connection passes)"
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
