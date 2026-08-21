"""Combined Gemini + NotebookLM extractor (license snapshot)."""

import logging
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.ingested_blob import IngestedBlob
from app.models.source_connection import SourceConnection
from app.schemas.schemas import BlobScanItem, BlobScanResult
from app.services.ai_extractor_base import current_period
from app.services.notebooklm_license_extractor import NotebookLMLicenseExtractor

logger = logging.getLogger(__name__)


class GeminiNotebookLMExtractor:
    """Delegates to NotebookLM license extractor for combined product view."""

    def __init__(self, connection: SourceConnection):
        self.connection = connection
        self.config_json = connection.config_json or {}
        self.project_id = self.config_json.get("project_id", "")
        self._license_extractor = NotebookLMLicenseExtractor(connection)

    def test_connection(self) -> dict:
        return self._license_extractor.test_connection()

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
                blob_prefix="gemini_notebooklm",
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
                blob_prefix="gemini_notebooklm",
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

    def _fetch_users_and_tokens(self) -> pd.DataFrame:
        df = self._license_extractor._fetch_licenses()
        if df.empty:
            raise RuntimeError("No Gemini/NotebookLM users returned")
        df = df.copy()
        df["ai_product"] = "Gemini NotebookLM"
        df["username"] = df["primary_email"].astype(str).str.split("@").str[0]
        df["email"] = df["primary_email"]
        df["total_tokens_consumed"] = 0
        df["prompt_tokens"] = 0
        df["completion_tokens"] = 0
        df["last_activity_at"] = df.get("last_login_time", "")
        df["created_at"] = df.get("assigned_time", "")
        df["license_type"] = df.get("license_sku_name", "")
        return df.fillna("")
