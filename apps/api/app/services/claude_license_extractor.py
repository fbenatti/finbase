import logging
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.ingested_blob import IngestedBlob
from app.models.source_connection import SourceConnection
from app.schemas.schemas import BlobScanItem, BlobScanResult
from app.services.ai_extractor_base import (
    AnthropicAdminClient,
    current_period,
    is_enterprise_analytics_key,
    snapshot_timestamp,
)

logger = logging.getLogger(__name__)


class ClaudeLicenseExtractor:
    """Raw Anthropic organization users for license snapshot."""

    def __init__(self, connection: SourceConnection):
        self.connection = connection
        self.config_json = connection.config_json or {}
        self.api_token = (
            self.config_json.get("api_token", "")
            or self.config_json.get("api_key", "")
            or self.config_json.get("client_secret", "")
        )
        self.org_id = self.config_json.get("org_id", "") or self.config_json.get("tenant_id", "")

    def _client(self) -> AnthropicAdminClient:
        return AnthropicAdminClient(self.api_token, self.org_id)

    def test_connection(self) -> dict:
        try:
            client = self._client()
            client.test()
            try:
                if is_enterprise_analytics_key(self.api_token):
                    client.test_analytics_usage()
                else:
                    client.test_usage()
            except Exception as usage_exc:
                if is_enterprise_analytics_key(self.api_token):
                    return {
                        "status": "OK",
                        "message": (
                            "Claude Enterprise — licenças OK (read:members). Para consumo, habilite "
                            "API de Analytics e adicione escopo read:analytics na chave."
                        ),
                        "warning": str(usage_exc)[:300],
                    }
                return {
                    "status": "OK",
                    "message": (
                        "Claude Console — licenças OK, mas consumo exige Admin API Key (sk-ant-admin) "
                        "com api:admin."
                    ),
                    "warning": str(usage_exc)[:300],
                }
            return {"status": "OK", "message": "Anthropic Admin API — licenças e consumo validados"}
        except Exception as exc:
            return {"status": "ERROR", "message": str(exc)}

    def _scan_sync(self, db_session: Session) -> list[BlobScanItem]:
        period = current_period()
        now = datetime.now(timezone.utc)
        existing = db_session.execute(
            select(IngestedBlob).where(
                IngestedBlob.connection_id == self.connection.id,
                IngestedBlob.blob_path == period,
            )
        ).scalar_one_or_none()
        status = "CHANGED" if existing else "NEW"
        return [
            BlobScanItem(
                blob_path=period,
                blob_prefix="claude_license",
                last_modified=now,
                content_length=None,
                status=status,
                billing_period=period,
            )
        ]

    async def scan(self, db: AsyncSession) -> BlobScanResult:
        period = current_period()
        now = datetime.now(timezone.utc)
        existing = await db.execute(
            select(IngestedBlob).where(
                IngestedBlob.connection_id == self.connection.id,
                IngestedBlob.blob_path == period,
            )
        )
        blob_row = existing.scalar_one_or_none()
        status = "CHANGED" if blob_row else "NEW"
        items = [
            BlobScanItem(
                blob_path=period,
                blob_prefix="claude_license",
                last_modified=now,
                content_length=None,
                status=status,
                billing_period=period,
            )
        ]
        return BlobScanResult(
            connection_id=self.connection.id,
            total=1,
            new=1 if status == "NEW" else 0,
            changed=1 if status == "CHANGED" else 0,
            unchanged=0,
            items=items,
        )

    def _fetch_licenses(self, usage_last_active: dict[str, str] | None = None) -> pd.DataFrame:
        client = self._client()
        usage_last_active = usage_last_active or {}
        records = []
        for user in client.iter_users():
            email = (user.get("email") or "").strip()
            user_id = user.get("id") or user.get("user_id") or ""
            last_active = usage_last_active.get(email, "") or user.get("last_active_at") or ""
            records.append(
                {
                    "id": user_id,
                    "email": email,
                    "name": user.get("name") or user.get("display_name") or "",
                    "role": user.get("role") or "",
                    "status": user.get("status") or "active",
                    "created_at": user.get("added_at") or user.get("created_at") or "",
                    "last_active_at": last_active,
                    "has_sso": user.get("has_sso", user.get("sso_enabled", False)),
                    "workspace_id": user.get("workspace_id") or "",
                    "organization_id": self.org_id or user.get("organization_id") or "",
                    "billing_plan": user.get("billing_plan") or user.get("plan") or "enterprise",
                    "snapshot_date": snapshot_timestamp(),
                }
            )
        if not records:
            raise RuntimeError("Anthropic users API returned no records")
        return pd.DataFrame(records).fillna("")
