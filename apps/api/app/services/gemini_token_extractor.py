import logging
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.ingested_blob import IngestedBlob
from app.models.source_connection import SourceConnection
from app.schemas.schemas import BlobScanItem, BlobScanResult
from app.services.ai_extractor_base import iter_monthly_periods
from app.services.google_extractor_utils import google_client_from_config
from app.services.google_workspace_client import usage_dates_for_period

logger = logging.getLogger(__name__)


class GeminiTokenExtractor:
    """Google Reports API gemini usage for Gemini product."""

    def __init__(self, connection: SourceConnection):
        self.connection = connection
        self.config_json = connection.config_json or {}
        self.project_id = self.config_json.get("project_id", "")

    def test_connection(self) -> dict:
        try:
            google_client_from_config(self.config_json).test()
            return {"status": "OK", "message": f"Google Gemini ({self.project_id}) — conexão validada"}
        except Exception as exc:
            return {"status": "ERROR", "message": str(exc)}

    def _scan_sync(self, db_session: Session) -> list[BlobScanItem]:
        items: list[BlobScanItem] = []
        now = datetime.now(timezone.utc)
        for i, period in enumerate(iter_monthly_periods(12)):
            existing = db_session.execute(
                select(IngestedBlob).where(
                    IngestedBlob.connection_id == self.connection.id,
                    IngestedBlob.blob_path == period,
                )
            ).scalar_one_or_none()
            status = "CHANGED" if (existing and i == 0) else ("UNCHANGED" if existing else "NEW")
            items.append(
                BlobScanItem(
                    blob_path=period,
                    blob_prefix="gemini_token",
                    last_modified=existing.last_modified if existing else now,
                    content_length=None,
                    status=status,
                    billing_period=period,
                )
            )
        return items

    async def scan(self, db: AsyncSession) -> BlobScanResult:
        items = []
        now = datetime.now(timezone.utc)
        for i, period in enumerate(iter_monthly_periods(12)):
            existing = await db.execute(
                select(IngestedBlob).where(
                    IngestedBlob.connection_id == self.connection.id,
                    IngestedBlob.blob_path == period,
                )
            )
            blob_row = existing.scalar_one_or_none()
            status = "CHANGED" if (blob_row and i == 0) else ("UNCHANGED" if blob_row else "NEW")
            items.append(
                BlobScanItem(
                    blob_path=period,
                    blob_prefix="gemini_token",
                    last_modified=blob_row.last_modified if blob_row else now,
                    content_length=None,
                    status=status,
                    billing_period=period,
                )
            )
        return BlobScanResult(
            connection_id=self.connection.id,
            total=len(items),
            new=sum(1 for it in items if it.status == "NEW"),
            changed=sum(1 for it in items if it.status == "CHANGED"),
            unchanged=sum(1 for it in items if it.status == "UNCHANGED"),
            items=items,
        )

    def _fetch_tokens_for_period(self, period: str) -> pd.DataFrame:
        client = google_client_from_config(self.config_json)
        records: list[dict] = []
        for date in usage_dates_for_period(period):
            for report in client.iter_user_usage_report(date, "gemini_in_workspace_apps"):
                email = report.get("entity", {}).get("userEmail") or report.get("userEmail") or ""
                username = email.split("@")[0] if email else ""
                for param in report.get("parameters") or []:
                    prompt = int(param.get("intValue") or 0)
                    records.append(
                        {
                            "date": date,
                            "username": username,
                            "email": email,
                            "ai_product": "Gemini",
                            "model_version": param.get("name") or "",
                            "project_id": self.project_id,
                            "prompt_tokens": prompt,
                            "completion_tokens": 0,
                            "cached_tokens": 0,
                            "audio_overview_seconds": 0,
                            "total_tokens": prompt,
                            "request_count": 1,
                            "estimated_cost_usd": 0,
                            "aggregation_level": "user",
                        }
                    )
        return pd.DataFrame(records).fillna("")
