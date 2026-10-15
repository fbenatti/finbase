"""Azure tag coverage dashboard based on the latest Resource Graph Bronze snapshot."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.azure_tag_mapping import AzureTagMapping
from app.models.ingested_blob import IngestedBlob
from app.models.source_connection import SourceConnection

logger = logging.getLogger("openfinops.azure_tag_dashboard")
RESOURCE_GROUP_TYPE = "microsoft.resources/subscriptions/resourcegroups"
SQL_DATABASE_TYPE = "microsoft.sql/servers/databases"
def _managed_tag_status(tags: Any, tag_keys: list[str]) -> tuple[str, list[str]]:
    if not tag_keys:
        return "not_assessed", []
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except (TypeError, ValueError):
            tags = {}
    values = tags if isinstance(tags, dict) else {}
    normalized = {str(key).casefold(): str(value).strip() for key, value in values.items()}
    missing = [key for key in tag_keys if not normalized.get(key.casefold())]
    if not missing:
        return "complete", []
    if len(missing) == len(tag_keys):
        return "none", missing
    return "partial", missing


def _is_sql_master(name: str, resource_type: str) -> bool:
    return resource_type.casefold() == SQL_DATABASE_TYPE and (
        name.casefold() == "master" or name.casefold().endswith("/master")
    )


def _resource_tag_support(resource_type: str, name: str,
                          capabilities: dict[str, bool]) -> tuple[bool | None, str | None]:
    normalized_type = str(resource_type or "").casefold()
    if _is_sql_master(name, normalized_type):
        return False, "Banco SQL master não aceita tags no Azure."
    if normalized_type not in capabilities:
        return None, "O Azure não retornou metadados de suporte a tags para este tipo."
    if not capabilities[normalized_type]:
        return False, "O tipo de recurso não declara SupportsTags nos metadados do Azure."
    return True, None


class AzureTagDashboardService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _connection(self, connection_id: int) -> SourceConnection:
        connection = await self.db.get(SourceConnection, connection_id)
        if not connection or connection.connector_type != "azure_blob":
            raise ValueError("Conexão Azure não encontrada")
        return connection

    async def _latest_inventory(self, connection: SourceConnection,
                                subscription_id: str | None = None) -> tuple[list[dict[str, Any]], IngestedBlob | None]:
        result = await self.db.execute(
            select(IngestedBlob).where(
                IngestedBlob.connection_id == connection.id,
                IngestedBlob.blob_path.like("arg/inventory/%"),
                IngestedBlob.status == "INGESTED",
            ).order_by(IngestedBlob.last_modified.desc()).limit(1)
        )
        blob = result.scalars().first()
        if blob is None:
            return [], None
        from app.services import iceberg_service
        from app.services.azure_source_registry import source_code

        bronze_code = source_code(connection.code, "resource_graph")
        row_filter = None
        if subscription_id:
            from pyiceberg.expressions import EqualTo
            row_filter = EqualTo("subscription_id", subscription_id)
        frame = await asyncio.to_thread(
            iceberg_service.read_bronze, bronze_code, blob_path=blob.blob_path,
            billing_period=blob.billing_period, row_filter=row_filter,
        )
        records = frame.to_dict(orient="records")
        for row in records:
            for key, value in row.items():
                if isinstance(value, float) and math.isnan(value):
                    row[key] = None
                elif hasattr(value, "item"):
                    try:
                        row[key] = value.item()
                    except (TypeError, ValueError):
                        pass
        return records, blob

    async def _silver_subscription_names(self, connection_id: int) -> dict[str, str]:
        try:
            from app.services.azure_tag_policy_service import AzureTagPolicyService
            subscriptions = await AzureTagPolicyService(self.db).list_subscriptions(connection_id)
            return {item["subscription_id"].casefold(): item["name"] for item in subscriptions}
        except Exception as exc:
            logger.warning("Azure tag dashboard subscription names from Silver unavailable: %s", str(exc)[:160])
            return {}

    @staticmethod
    def _inventory_aggregates(rows: list[dict[str, Any]], tag_keys: list[str]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, dict[tuple, dict[str, Any]]] = {}
        for row in rows:
            subscription_id = str(row.get("subscription_id") or "").strip()
            resource_type = str(row.get("resource_type") or "")
            if not subscription_id or not resource_type:
                continue
            is_rg = resource_type.casefold() == RESOURCE_GROUP_TYPE
            name = str(row.get("resource_name") or "")
            is_master = _is_sql_master(name, resource_type)
            taggable = row.get("taggable")
            if isinstance(taggable, str):
                taggable = taggable.casefold() == "true" if taggable.casefold() in {"true", "false"} else None
            if not isinstance(taggable, bool):
                taggable = False if is_master else None
            status, _missing = _managed_tag_status(row.get("tags"), tag_keys)
            key = (resource_type.casefold(), is_rg, is_master, taggable)
            by_type = grouped.setdefault(subscription_id.casefold(), {})
            aggregate = by_type.setdefault(key, {
                "subscription_id": subscription_id, "resource_type": resource_type.casefold(),
                "is_resource_group": is_rg, "is_sql_master": is_master,
                "taggable": taggable, "total": 0, "complete": 0, "partial": 0, "none": 0,
            })
            aggregate["total"] += 1
            if status == "complete":
                aggregate["complete"] += 1
            elif status == "partial":
                aggregate["partial"] += 1
            elif status == "none":
                aggregate["none"] += 1
        return {sid: list(items.values()) for sid, items in grouped.items()}

    async def _tag_keys(self) -> list[str]:
        result = await self.db.execute(select(AzureTagMapping).where(
            AzureTagMapping.active == True,
        ).order_by(AzureTagMapping.id))
        keys: list[str] = []
        seen: set[str] = set()
        for row in result.scalars().all():
            key = str(row.tag_key or "").strip()
            if key and key.casefold() not in seen:
                keys.append(key)
                seen.add(key.casefold())
        return keys

    @staticmethod
    def _subscription_summary(subscription: dict[str, Any], rows: list[dict[str, Any]],
                             capabilities: dict[str, bool], tag_keys: list[str],
                             capability_errors: list[str]) -> dict[str, Any]:
        summary = {
            "subscription_id": subscription["subscription_id"],
            "name": subscription["subscription_name"],
            "total_resources": 0,
            "resource_groups": 0,
            "total_items": 0,
            "taggable_resources": 0,
            "non_taggable_resources": 0,
            "unclassified_resources": 0,
            "taggable_resource_groups": 0,
            "non_taggable_resource_groups": 0,
            "unclassified_resource_groups": 0,
            "taggable_items": 0,
            "fully_tagged": 0,
            "partially_tagged": 0,
            "without_managed_tags": 0,
            "adherence_percent": None,
            "inventory_error": None,
            "capability_warnings": capability_errors[:5],
        }
        for row in rows:
            count = int(row.get("total") or 0)
            resource_type = str(row.get("resource_type") or "")
            is_rg = row.get("is_resource_group") is True or str(row.get("is_resource_group")).casefold() == "true"
            is_master = row.get("is_sql_master") is True or str(row.get("is_sql_master")).casefold() == "true"
            stored_support = row.get("taggable")
            if isinstance(stored_support, str):
                stored_support = stored_support.casefold() == "true" if stored_support.casefold() in {"true", "false"} else None
            supported, _reason = ((False, "SQL master não aceita tags") if is_master else
                                  (stored_support, None) if isinstance(stored_support, bool) else
                                  _resource_tag_support(resource_type, "", capabilities))
            summary["total_items"] += count
            if is_rg:
                summary["resource_groups"] += count
            else:
                summary["total_resources"] += count
            if supported is True:
                summary["taggable_resource_groups" if is_rg else "taggable_resources"] += count
                summary["taggable_items"] += count
                summary["fully_tagged"] += int(row.get("complete") or 0)
                summary["partially_tagged"] += int(row.get("partial") or 0)
                summary["without_managed_tags"] += int(row.get("none") or 0)
            elif supported is False:
                summary["non_taggable_resource_groups" if is_rg else "non_taggable_resources"] += count
            else:
                summary["unclassified_resource_groups" if is_rg else "unclassified_resources"] += count
        if summary["taggable_items"] and tag_keys:
            summary["adherence_percent"] = round(
                100 * summary["fully_tagged"] / summary["taggable_items"], 1,
            )
        return summary

    async def executive_summary(self, connection_id: int) -> dict[str, Any]:
        connection = await self._connection(connection_id)
        tag_keys = await self._tag_keys()
        rows, blob = await self._latest_inventory(connection)
        aggregates = self._inventory_aggregates(rows, tag_keys)
        subscription_ids = set(aggregates)
        if rows:
            names_from_inventory = {
                sid.casefold(): str(row.get("subscription_name") or "")
                for row in rows if (sid := str(row.get("subscription_id") or "").strip())
                and row.get("subscription_name")
            }
            missing_names = subscription_ids - set(names_from_inventory)
            silver_names = await self._silver_subscription_names(connection_id) if missing_names else {}
        else:
            names_from_inventory = {}
            silver_names = await self._silver_subscription_names(connection_id)
            subscription_ids.update(silver_names)
        latest_snapshot = str(blob.blob_path).rsplit("/", 1)[-1].removesuffix(".csv") if blob else None
        generated_at = blob.last_modified.isoformat() if blob and blob.last_modified else None

        return {
            "connection_id": connection.id,
            "connection_name": connection.name,
            "connection_code": connection.code,
            "generated_at": generated_at,
            "snapshot_date": latest_snapshot,
            "tag_keys": tag_keys,
            "adherence_definition": "Percentual de recursos tagueáveis com todas as chaves gerenciadas preenchidas; não compara valores esperados pela regra de negócio.",
            "subscriptions": [
                ({**self._subscription_summary(
                    {"subscription_id": sid, "subscription_name": names_from_inventory.get(sid, "") or silver_names.get(sid, sid)},
                    aggregates.get(sid, []), {}, tag_keys, [],
                ), "inventory_error": None if blob else "Sem snapshot Bronze do Resource Graph. Acione Atualizar inventário.",
                 "snapshot_date": latest_snapshot})
                for sid in sorted(subscription_ids, key=lambda value: (names_from_inventory.get(value, "") or silver_names.get(value, value)).casefold())
            ],
        }

    async def subscription_resources(self, connection_id: int, subscription_id: str,
                                     page_token: str | None = None) -> dict[str, Any]:
        connection = await self._connection(connection_id)
        tag_keys = await self._tag_keys()
        all_rows, blob = await self._latest_inventory(connection, subscription_id=subscription_id)
        rows = sorted(
            all_rows,
            key=lambda row: str(row.get("resource_id") or "").casefold(),
        )
        if not rows:
            raise ValueError("Não há recursos desta subscription no último snapshot Bronze; atualize o inventário Azure.")
        try:
            offset = int(base64.urlsafe_b64decode(page_token.encode()).decode()) if page_token else 0
        except Exception as exc:
            raise ValueError("Token de paginação inválido") from exc
        page_size = 250
        page_rows = rows[offset:offset + page_size]
        next_token = (base64.urlsafe_b64encode(str(offset + page_size).encode()).decode()
                      if offset + page_size < len(rows) else None)
        resources: list[dict[str, Any]] = []
        for row in page_rows:
            resource_type = str(row.get("resource_type") or "")
            name = str(row.get("resource_name") or "")
            supported = row.get("taggable")
            if isinstance(supported, str):
                supported = supported.casefold() == "true" if supported.casefold() in {"true", "false"} else None
            if _is_sql_master(name, resource_type):
                supported, reason = False, "Banco SQL master não aceita tags no Azure."
            else:
                reason = str(row.get("tag_support_reason") or "") or None
            tag_status, missing = _managed_tag_status(row.get("tags"), tag_keys)
            is_rg = resource_type.casefold() == RESOURCE_GROUP_TYPE
            resources.append({
                **row,
                "is_resource_group": is_rg,
                "taggable": supported,
                "tag_support_reason": reason,
                "tag_status": tag_status,
                "missing_tags": missing,
            })
        return {
            "subscription_id": subscription_id,
            "subscription_name": next((str(row.get("subscription_name") or "").strip()
                                        for row in rows if row.get("subscription_name")), subscription_id),
            "tag_keys": tag_keys,
            "resources": resources,
            "next_page_token": next_token,
            "snapshot_date": str(blob.blob_path).rsplit("/", 1)[-1].removesuffix(".csv") if blob else None,
            "capability_warnings": [],
        }
