"""Historical snapshots and Azure Policy execution events for the tag dashboard."""
from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.azure_tag_mapping import AzureTagMapping
from app.models.azure_tag_timeline import AzureTagPolicyEvent, AzureTagPolicySnapshot
from app.models.ingested_blob import IngestedBlob
from app.models.source_connection import SourceConnection
from app.services.azure_tag_dashboard_service import AzureTagDashboardService
from app.services.azure_tag_policy_service import AzureTagPolicyService

ACTIVITY_LOG_API = "2015-04-01"
REMEDIATION_API = "2024-10-01"
ACTIVITY_PROVIDERS = ("Microsoft.Authorization", "Microsoft.PolicyInsights")
POLICY_ASSIGNMENT_QUERY = """
policyresources
| where type =~ 'microsoft.authorization/policyassignments'
| where name startswith 'of-asg'
| project subscriptionId
"""
RELEVANT_EVENT = re.compile(r"policy|remediat", re.IGNORECASE)
ASSIGNMENT_ID = re.compile(r"(/providers/microsoft\.authorization/policyassignments/[^/?]+)", re.IGNORECASE)
REMEDIATION_ID = re.compile(r"(/providers/microsoft\.policyinsights/remediations/[^/?]+)", re.IGNORECASE)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return _as_utc(parsed)
    except (TypeError, ValueError):
        return None


def _policy_id(resource_id: str | None, pattern: re.Pattern[str]) -> str | None:
    match = pattern.search(resource_id or "")
    return match.group(1) if match else None


def _localized(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("value") or value.get("localizedValue") or "")
    return str(value or "")


def _event_record(raw: dict[str, Any], subscription_id: str, connection_id: int) -> dict[str, Any] | None:
    operation_value = _localized(raw.get("operationName"))
    resource_id = str(raw.get("resourceId") or "")
    event_name_value = _localized(raw.get("eventName"))
    if not RELEVANT_EVENT.search(f"{operation_value} {event_name_value} {resource_id}"):
        return None
    event_at = _parse_datetime(raw.get("eventTimestamp"))
    if event_at is None:
        return None
    source_event_id = str(raw.get("eventDataId") or raw.get("id") or "").strip()
    if not source_event_id:
        return None
    status = _localized(raw.get("status"))
    sub_status = _localized(raw.get("subStatus"))
    properties = raw.get("properties") or {}
    details = {
        "event_name": event_name_value or None,
        "category": _localized(raw.get("category")) or None,
        "sub_status": sub_status or None,
        "description": str(raw.get("description") or "")[:1000] or None,
        "resource_type": raw.get("resourceType"),
        "resource_provider": raw.get("resourceProviderName"),
        "authorization": raw.get("authorization") if isinstance(raw.get("authorization"), dict) else None,
        "properties": properties if isinstance(properties, dict) else {},
    }
    assignment_id = _policy_id(resource_id, ASSIGNMENT_ID)
    remediation_id = _policy_id(resource_id, REMEDIATION_ID)
    if not assignment_id and isinstance(properties, dict):
        assignment_id = properties.get("policyAssignmentId")
    return {
        "connection_id": connection_id,
        "event_key": hashlib.sha256(source_event_id.encode("utf-8")).hexdigest(),
        "source_event_id": source_event_id,
        "event_at": event_at,
        "subscription_id": subscription_id,
        "operation": operation_value or event_name_value or "Azure Policy event",
        "status": status or None,
        "resource_id": resource_id or None,
        "policy_assignment_id": assignment_id,
        "remediation_id": remediation_id,
        "correlation_id": str(raw.get("correlationId") or "")[:100] or None,
        "details": details,
    }


def _remediation_record(raw: dict[str, Any], subscription_id: str, connection_id: int,
                        since: datetime) -> dict[str, Any] | None:
    properties = raw.get("properties") or {}
    if not isinstance(properties, dict):
        return None
    def field(name: str, *alternates: str) -> Any:
        for key in (name, *alternates):
            if key in properties and properties[key] is not None:
                return properties[key]
            if key in raw and raw[key] is not None:
                return raw[key]
        return None

    created_at = _parse_datetime(field("createdOn"))
    updated_at = _parse_datetime(field("lastUpdatedOn"))
    event_at = updated_at or created_at
    if event_at is None or event_at < since:
        return None
    resource_id = str(raw.get("id") or "")
    name = str(raw.get("name") or resource_id.rsplit("/", 1)[-1])
    provisioning_state = field("provisioningState")
    deployment_status = field("deploymentStatus", "deploymentSummary")
    stable_id = "|".join((resource_id, str(properties.get("createdOn") or ""),
                          str(properties.get("lastUpdatedOn") or ""), str(created_at or ""),
                          str(updated_at or ""), str(provisioning_state or "")))
    return {
        "connection_id": connection_id,
        "event_key": hashlib.sha256(stable_id.encode("utf-8")).hexdigest(),
        "source_event_id": stable_id,
        "event_at": event_at,
        "subscription_id": subscription_id,
        "operation": "Microsoft.PolicyInsights/remediations/status",
        "status": str(provisioning_state or "Unknown"),
        "resource_id": resource_id or None,
        "policy_assignment_id": field("policyAssignmentId"),
        "remediation_id": resource_id or name,
        "correlation_id": str(field("correlationId") or "")[:100] or None,
        "details": {
            "name": name,
            "created_at": created_at.isoformat() if created_at else None,
            "updated_at": updated_at.isoformat() if updated_at else None,
            "resource_count": field("resourceCount"),
            "parallel_deployments": field("parallelDeployments"),
            "resource_discovery_mode": field("resourceDiscoveryMode"),
            "deployment_summary": deployment_status,
            "status_message": field("statusMessage"),
            "filters": field("filters"),
        },
    }


class AzureTagTimelineService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _connection(self, connection_id: int) -> SourceConnection:
        connection = await self.db.get(SourceConnection, connection_id)
        if not connection or connection.connector_type != "azure_blob" or connection.cloud_scope != "azure":
            raise ValueError("Conexão Azure não encontrada")
        return connection

    async def _snapshot_inventory(self, connection: SourceConnection, since: date) -> int:
        blobs = (await self.db.execute(
            select(IngestedBlob).where(
                IngestedBlob.connection_id == connection.id,
                IngestedBlob.blob_path.like("arg/inventory/%"),
                IngestedBlob.status == "INGESTED",
                IngestedBlob.last_modified >= datetime.combine(since, time.min, tzinfo=timezone.utc),
            ).order_by(IngestedBlob.last_modified)
        )).scalars().all()
        mappings = (await self.db.execute(
            select(AzureTagMapping).where(AzureTagMapping.active.is_(True)).order_by(AzureTagMapping.id)
        )).scalars().all()
        tag_keys = list(dict.fromkeys(str(row.tag_key).strip() for row in mappings if str(row.tag_key or "").strip()))
        mapping_data = [{"tag_key": row.tag_key, "focus_field": row.focus_field} for row in mappings]
        if not tag_keys:
            return 0

        from app.services import iceberg_service
        from app.services.azure_source_registry import source_code

        bronze_code = source_code(connection.code, "resource_graph")
        added = 0
        for blob in blobs:
            existing = (await self.db.execute(
                select(AzureTagPolicySnapshot.id).where(AzureTagPolicySnapshot.source_blob_id == blob.id).limit(1)
            )).first()
            if existing:
                continue
            frame = await asyncio.to_thread(
                iceberg_service.read_bronze,
                bronze_code,
                blob_path=blob.blob_path,
                billing_period=blob.billing_period,
            )
            rows = frame.to_dict(orient="records")
            aggregates = AzureTagDashboardService._inventory_aggregates(rows, tag_keys, mapping_data)
            names: dict[str, str] = {}
            for row in rows:
                sid = str(row.get("subscription_id") or "").strip()
                if sid and row.get("subscription_name"):
                    names.setdefault(sid.casefold(), str(row["subscription_name"]))

            records: list[dict[str, Any]] = []
            snapshot_at = _as_utc(blob.last_modified)
            snapshot_date = snapshot_at.date()
            for subscription_id, values in aggregates.items():
                subscription_id = str(subscription_id)
                summary = AzureTagDashboardService._subscription_summary(
                    {"subscription_id": subscription_id,
                     "subscription_name": names.get(subscription_id.casefold(), subscription_id)},
                    values,
                    {},
                    tag_keys,
                    [],
                )
                records.append({
                    "connection_id": connection.id,
                    "source_blob_id": blob.id,
                    "snapshot_at": snapshot_at,
                    "snapshot_date": snapshot_date,
                    "subscription_id": subscription_id,
                    "subscription_name": summary["name"],
                    "tag_keys": tag_keys,
                    "taggable_items": summary["taggable_items"],
                    "fully_tagged": summary["fully_tagged"],
                    "partially_tagged": summary["partially_tagged"],
                    "without_managed_tags": summary["without_managed_tags"],
                    "taggable_resources": summary["taggable_resources"],
                    "taggable_resource_groups": summary["taggable_resource_groups"],
                    "adherence_percent": summary["adherence_percent"],
                    "source_blob_path": blob.blob_path,
                })
            if records:
                result = await self.db.execute(
                    insert(AzureTagPolicySnapshot).values(records).on_conflict_do_nothing(
                        constraint="uq_azure_tag_snapshot_blob_subscription"
                    )
                )
                added += max(0, result.rowcount or 0)
            await self.db.commit()
        return added

    async def _policy_subscription_ids(self, connection: SourceConnection) -> tuple[list[str], list[str]]:
        from app.services.azure_identity import arm_headers
        from app.services.azure_resource_graph_service import _post_query

        config = dict(connection.config_json or {})
        headers = await arm_headers(config)
        subscriptions = await AzureTagPolicyService(self.db).list_subscriptions(connection.id)
        subscription_ids = [str(item["subscription_id"]) for item in subscriptions]
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            assignments, _token, _count = await _post_query(
                client, headers, POLICY_ASSIGNMENT_QUERY, subscription_ids,
            )
        targets = sorted({str(row.get("subscriptionId") or "").strip() for row in assignments
                          if str(row.get("subscriptionId") or "").strip()})
        return targets or subscription_ids, [] if targets else [
            "Nenhuma assignment `of-asg*` atual foi encontrada no Resource Graph; consultei o escopo da conexão."
        ]

    async def _activity_events(self, connection: SourceConnection, since: date) -> tuple[int, list[str]]:
        from app.services.azure_identity import arm_headers, get_json

        try:
            subscription_ids, warnings = await self._policy_subscription_ids(connection)
            headers = await arm_headers(dict(connection.config_json or {}))
        except Exception as exc:
            return 0, [f"Não foi possível identificar o escopo Azure: {str(exc)[:240]}"]

        start_at = datetime.combine(since, time.min, tzinfo=timezone.utc)
        end_at = datetime.now(timezone.utc)
        start_literal = start_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_literal = end_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        semaphore = asyncio.Semaphore(4)
        errors: list[str] = []
        events: list[dict[str, Any]] = []

        async def fetch_subscription(subscription_id: str) -> None:
            async with semaphore:
                async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                    for provider in ACTIVITY_PROVIDERS:
                        url = (
                            "https://management.azure.com/subscriptions/"
                            f"{subscription_id}/providers/Microsoft.Insights/eventtypes/management/values"
                        )
                        params = {
                            "api-version": ACTIVITY_LOG_API,
                            "$filter": (
                                f"eventTimestamp ge '{start_literal}' and eventTimestamp le '{end_literal}' "
                                f"and resourceProvider eq '{provider}'"
                            ),
                        }
                        try:
                            raw_events = await get_json(client, url, headers, params)
                        except Exception as exc:
                            errors.append(f"{subscription_id} / {provider}: {str(exc)[:220]}")
                            continue
                        for raw in raw_events:
                            normalized = _event_record(raw, subscription_id, connection.id)
                            if normalized:
                                events.append(normalized)
                    remediation_url = (
                        "https://management.azure.com/subscriptions/"
                        f"{subscription_id}/providers/Microsoft.PolicyInsights/remediations"
                    )
                    try:
                        remediations = await get_json(
                            client,
                            remediation_url,
                            headers,
                            {"api-version": REMEDIATION_API},
                        )
                    except Exception as exc:
                        errors.append(f"{subscription_id} / remediations: {str(exc)[:220]}")
                    else:
                        for remediation in remediations:
                            normalized = _remediation_record(remediation, subscription_id, connection.id, start_at)
                            if normalized:
                                events.append(normalized)

        await asyncio.gather(*(fetch_subscription(subscription_id) for subscription_id in subscription_ids))
        if events:
            result = await self.db.execute(
                insert(AzureTagPolicyEvent).values(events).on_conflict_do_nothing(
                    constraint="uq_azure_tag_event_connection_key"
                )
            )
            inserted = max(0, result.rowcount or 0)
        else:
            inserted = 0
        await self.db.commit()
        return inserted, (warnings + errors[:50])

    async def backfill(self, connection_id: int, since: date) -> dict[str, Any]:
        connection = await self._connection(connection_id)
        snapshot_count = await self._snapshot_inventory(connection, since)
        event_count, errors = await self._activity_events(connection, since)
        return {
            "connection_id": connection_id,
            "since": since.isoformat(),
            "snapshots_added": snapshot_count,
            "events_added": event_count,
            "errors": errors,
            "partial": bool(errors),
        }

    async def timeline(self, connection_id: int, since: date) -> dict[str, Any]:
        connection = await self._connection(connection_id)
        # Bronze is the durable daily source; import any newly ingested inventory
        # before serving history so the timeline follows the existing collector.
        await self._snapshot_inventory(connection, since)
        start_at = datetime.combine(since, time.min, tzinfo=timezone.utc)
        rows = (await self.db.execute(
            select(AzureTagPolicySnapshot).where(
                AzureTagPolicySnapshot.connection_id == connection_id,
                AzureTagPolicySnapshot.snapshot_at >= start_at,
            ).order_by(AzureTagPolicySnapshot.snapshot_at, AzureTagPolicySnapshot.subscription_name)
        )).scalars().all()
        events = (await self.db.execute(
            select(AzureTagPolicyEvent).where(
                AzureTagPolicyEvent.connection_id == connection_id,
                AzureTagPolicyEvent.event_at >= start_at,
            ).order_by(AzureTagPolicyEvent.event_at.desc()).limit(1000)
        )).scalars().all()

        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            stamp = _as_utc(row.snapshot_at).isoformat()
            snapshot = grouped.setdefault(stamp, {
                "snapshot_at": stamp,
                "snapshot_date": row.snapshot_date.isoformat(),
                "tag_keys": row.tag_keys or [],
                "source": "Azure Resource Graph / Bronze",
                "global": {"taggable_items": 0, "fully_tagged": 0, "partially_tagged": 0,
                           "without_managed_tags": 0, "adherence_percent": None},
                "subscriptions": [],
            })
            subscription = {
                "subscription_id": row.subscription_id,
                "name": row.subscription_name,
                "taggable_items": row.taggable_items,
                "fully_tagged": row.fully_tagged,
                "partially_tagged": row.partially_tagged,
                "without_managed_tags": row.without_managed_tags,
                "adherence_percent": row.adherence_percent,
            }
            snapshot["subscriptions"].append(subscription)
            for key in ("taggable_items", "fully_tagged", "partially_tagged", "without_managed_tags"):
                snapshot["global"][key] += int(subscription[key] or 0)

        for snapshot in grouped.values():
            total = snapshot["global"]["taggable_items"]
            complete = snapshot["global"]["fully_tagged"]
            snapshot["global"]["adherence_percent"] = round(100 * complete / total, 1) if total else None

        ordered_snapshots = sorted(grouped.values(), key=lambda item: item["snapshot_at"])
        by_date = {item["snapshot_date"] for item in ordered_snapshots}
        through = datetime.now(timezone.utc).date()
        expected_days = (through - since).days + 1
        missing_days = [
            (since + timedelta(days=offset)).isoformat()
            for offset in range(max(expected_days, 0))
            if (since + timedelta(days=offset)).isoformat() not in by_date
        ]
        event_payload = [{
            "event_at": _as_utc(row.event_at).isoformat(),
            "subscription_id": row.subscription_id,
            "operation": row.operation,
            "status": row.status,
            "resource_id": row.resource_id,
            "policy_assignment_id": row.policy_assignment_id,
            "remediation_id": row.remediation_id,
            "correlation_id": row.correlation_id,
            "details": row.details or {},
        } for row in reversed(events)]
        subscriptions: dict[str, dict[str, str]] = {}
        for snapshot in ordered_snapshots:
            for item in snapshot["subscriptions"]:
                subscriptions[item["subscription_id"]] = {
                    "subscription_id": item["subscription_id"], "name": item["name"],
                }
        for event in event_payload:
            subscriptions.setdefault(event["subscription_id"], {
                "subscription_id": event["subscription_id"], "name": event["subscription_id"],
            })
        return {
            "connection_id": connection_id,
            "since": since.isoformat(),
            "through": through.isoformat(),
            "snapshot_count": len(ordered_snapshots),
            "subscription_count": len(subscriptions),
            "event_count": len(event_payload),
            "events_truncated": len(events) >= 1000,
            "missing_inventory_dates": missing_days,
            "snapshots": ordered_snapshots,
            "subscriptions": sorted(subscriptions.values(), key=lambda item: item["name"].casefold()),
            "events": event_payload,
        }
