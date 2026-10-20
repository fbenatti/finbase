"""Historical tag adherence snapshots derived from Resource Graph Bronze inventories."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.azure_tag_mapping import AzureTagMapping
from app.models.azure_tag_timeline import AzureTagPolicySnapshot
from app.models.ingested_blob import IngestedBlob
from app.models.source_connection import SourceConnection
from app.services.azure_tag_dashboard_service import AzureTagDashboardService


class AzureTagTimelineService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _connection(self, connection_id: int) -> SourceConnection:
        connection = await self.db.get(SourceConnection, connection_id)
        if not connection or connection.connector_type != "azure_blob":
            raise ValueError("Conexão Azure não encontrada")
        return connection

    async def _snapshot_inventory(self, connection: SourceConnection, since: date) -> None:
        since_at = datetime.combine(since, time.min, tzinfo=timezone.utc)
        blobs = (await self.db.execute(select(IngestedBlob).where(
            IngestedBlob.connection_id == connection.id,
            IngestedBlob.blob_path.like("arg/inventory/%"),
            IngestedBlob.status == "INGESTED",
            IngestedBlob.last_modified >= since_at,
        ).order_by(IngestedBlob.last_modified, IngestedBlob.id))).scalars().all()
        mappings = (await self.db.execute(select(AzureTagMapping).where(
            AzureTagMapping.active.is_(True),
        ).order_by(AzureTagMapping.id))).scalars().all()
        tag_keys: list[str] = []
        for mapping in mappings:
            key = str(mapping.tag_key or "").strip()
            if key and key.casefold() not in {item.casefold() for item in tag_keys}:
                tag_keys.append(key)
        mapping_values = [{"tag_key": row.tag_key, "focus_field": row.focus_field} for row in mappings]
        from app.services import iceberg_service
        from app.services.azure_source_registry import source_code

        for blob in blobs:
            existing = await self.db.execute(select(AzureTagPolicySnapshot.id).where(
                AzureTagPolicySnapshot.source_blob_id == blob.id,
            ).limit(1))
            if existing.scalar_one_or_none() is not None:
                continue
            frame = await asyncio.to_thread(
                iceberg_service.read_bronze,
                source_code(connection.code, "resource_graph"),
                blob_path=blob.blob_path,
                billing_period=blob.billing_period,
            )
            records = frame.to_dict(orient="records")
            grouped = AzureTagDashboardService._inventory_aggregates(records, tag_keys, mapping_values)
            snapshot_at = blob.last_modified
            if snapshot_at.tzinfo is None:
                snapshot_at = snapshot_at.replace(tzinfo=timezone.utc)
            snapshot_date = snapshot_at.date()
            rows = []
            for subscription_id, aggregates in grouped.items():
                name = next((str(row.get("subscription_name") or "").strip()
                             for row in records
                             if str(row.get("subscription_id") or "").casefold() == subscription_id.casefold()
                             and str(row.get("subscription_name") or "").strip()), subscription_id)
                summary = AzureTagDashboardService._subscription_summary(
                    {"subscription_id": subscription_id, "subscription_name": name},
                    aggregates, {}, tag_keys, [],
                )
                rows.append({
                    "connection_id": connection.id,
                    "source_blob_id": blob.id,
                    "snapshot_at": snapshot_at,
                    "snapshot_date": snapshot_date,
                    "subscription_id": summary["subscription_id"],
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
            if rows:
                statement = insert(AzureTagPolicySnapshot).values(rows).on_conflict_do_nothing(
                    constraint="uq_azure_tag_snapshot_blob_subscription",
                )
                await self.db.execute(statement)
        await self.db.commit()

    async def timeline(self, connection_id: int, since: date) -> dict[str, Any]:
        connection = await self._connection(connection_id)
        await self._snapshot_inventory(connection, since)
        rows = (await self.db.execute(select(AzureTagPolicySnapshot).where(
            AzureTagPolicySnapshot.connection_id == connection_id,
            AzureTagPolicySnapshot.snapshot_date >= since,
        ).order_by(AzureTagPolicySnapshot.snapshot_at, AzureTagPolicySnapshot.subscription_name))).scalars().all()

        grouped: dict[datetime, list[AzureTagPolicySnapshot]] = {}
        for row in rows:
            grouped.setdefault(row.snapshot_at, []).append(row)
        snapshots = []
        subscription_names: dict[str, str] = {}
        for snapshot_at, items in grouped.items():
            global_taggable = sum(item.taggable_items for item in items)
            global_complete = sum(item.fully_tagged for item in items)
            subscriptions = []
            for item in items:
                subscription_names[item.subscription_id] = item.subscription_name
                subscriptions.append({
                    "subscription_id": item.subscription_id,
                    "name": item.subscription_name,
                    "taggable_items": item.taggable_items,
                    "fully_tagged": item.fully_tagged,
                    "partially_tagged": item.partially_tagged,
                    "without_managed_tags": item.without_managed_tags,
                    "adherence_percent": item.adherence_percent,
                })
            snapshots.append({
                "snapshot_at": snapshot_at.isoformat(),
                "snapshot_date": items[0].snapshot_date.isoformat(),
                "tag_keys": items[0].tag_keys or [],
                "source": "Resource Graph Bronze",
                "global": {
                    "taggable_items": global_taggable,
                    "fully_tagged": global_complete,
                    "partially_tagged": sum(item.partially_tagged for item in items),
                    "without_managed_tags": sum(item.without_managed_tags for item in items),
                    "adherence_percent": round(global_complete * 100 / global_taggable, 1) if global_taggable else None,
                },
                "subscriptions": subscriptions,
            })

        days_with_data = {date.fromisoformat(item["snapshot_date"]) for item in snapshots}
        through = datetime.now(timezone.utc).date()
        missing_dates = []
        day = since
        while day <= through:
            if day not in days_with_data:
                missing_dates.append(day.isoformat())
            day += timedelta(days=1)
        return {
            "connection_id": connection_id,
            "since": since.isoformat(),
            "through": through.isoformat(),
            "snapshot_count": len(snapshots),
            "subscription_count": len(subscription_names),
            "missing_inventory_dates": missing_dates,
            "snapshots": snapshots,
            "subscriptions": [
                {"subscription_id": key, "name": name} for key, name in sorted(
                    subscription_names.items(), key=lambda item: item[1].casefold(),
                )
            ],
        }
