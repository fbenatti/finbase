import logging
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.ingested_blob import IngestedBlob
from app.models.source_connection import SourceConnection
from app.schemas.schemas import BlobScanItem, BlobScanResult
from app.services.ai_extractor_base import current_period, snapshot_timestamp
from app.services.google_extractor_utils import google_client_from_config

logger = logging.getLogger(__name__)


class GeminiLicenseExtractor:
    """Raw Google Workspace users for Gemini licenses."""

    PRODUCT_NAME = "Gemini"

    def __init__(self, connection: SourceConnection):
        self.connection = connection
        self.config_json = connection.config_json or {}
        self.project_id = self.config_json.get("project_id", "")
        self.project_number = self.config_json.get("project_number", "")
        self.license_product_id = self.config_json.get("license_product_id", "Google-Apps")
        self.license_sku_id = self.config_json.get("gemini_license_sku_id") or self.config_json.get("license_sku_id", "")

    def test_connection(self) -> dict:
        try:
            google_client_from_config(self.config_json).test()
            return {"status": "OK", "message": f"Google Gemini ({self.project_id}) — conexão validada"}
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
                blob_prefix="gemini_license",
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
                blob_prefix="gemini_license",
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

    def _fetch_licenses(self) -> pd.DataFrame:
        client = google_client_from_config(self.config_json)
        license_by_user: dict[str, dict] = {}
        if self.license_sku_id:
            for item in client.iter_license_assignments(self.license_product_id, self.license_sku_id):
                license_by_user[item.get("userId") or ""] = item

        records = []
        for user in client.iter_users():
            email = user.get("primaryEmail") or ""
            username = email.split("@")[0] if email else ""
            user_id = user.get("id") or ""
            lic = license_by_user.get(user_id, {})
            records.append(
                {
                    "username": username,
                    "email": email,
                    "user_id": user_id,
                    "role": "admin" if user.get("isAdmin") else "member",
                    "status": "suspended" if user.get("suspended") else "active",
                    "license_type": lic.get("skuName") or self.PRODUCT_NAME,
                    "project_id": self.project_id,
                    "assigned_at": lic.get("assignedTime") or user.get("creationTime") or "",
                    "last_active_at": user.get("lastLoginTime") or "",
                    "snapshot_date": snapshot_timestamp(),
                }
            )
        if not records:
            raise RuntimeError("Google Directory API returned no users for Gemini licenses")
        return pd.DataFrame(records).fillna("")
