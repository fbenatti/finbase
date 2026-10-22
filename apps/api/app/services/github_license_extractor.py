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
    GitHubEnterpriseClient,
    current_period,
    snapshot_timestamp,
)

logger = logging.getLogger(__name__)


class GithubLicenseExtractor:
    """Raw GitHub Copilot Enterprise billing seats."""

    def __init__(self, connection: SourceConnection):
        self.connection = connection
        self.config_json = connection.config_json or {}
        self.enterprise_slug = self.config_json.get("enterprise_slug", "")
        self.pat_token = self.config_json.get("pat_token", "") or self.config_json.get("client_secret", "")
        self.email_domain = self.config_json.get("email_domain", "")

    def _client(self) -> GitHubEnterpriseClient:
        return GitHubEnterpriseClient(self.pat_token, self.enterprise_slug, self.email_domain)

    def test_connection(self) -> dict:
        try:
            self._client().test()
            return {"status": "OK", "message": f"GitHub Enterprise ({self.enterprise_slug}) — conexão validada"}
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
                blob_prefix="github_license",
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
                blob_prefix="github_license",
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
        client = self._client()
        logger.info("[GithubLicense] Resolving SCIM email map for %s...", self.enterprise_slug)
        scim_map = client.resolve_email_map()
        logger.info("[GithubLicense] SCIM map loaded (%d keys); fetching Copilot seats...", len(scim_map))
        records = []
        for idx, seat in enumerate(client.iter_seats(), start=1):
            assignee = seat.get("assignee") or {}
            team = seat.get("assigning_team") or {}
            login = assignee.get("login", "")
            email, email_source = (
                client.resolve_email(login, scim_map, use_api_fallback=False)
                if login
                else ("", "")
            )
            records.append(
                {
                    "assignee_login": login,
                    "assignee_id": assignee.get("id", ""),
                    "assignee_email": email,
                    "email_resolution_source": email_source,
                    "assignee_avatar_url": assignee.get("avatar_url", ""),
                    "assignee_html_url": assignee.get("html_url", ""),
                    "assignee_type": assignee.get("type", "User"),
                    "assignee_site_admin": assignee.get("site_admin", False),
                    "assigning_team_id": team.get("id", ""),
                    "assigning_team_name": team.get("name", ""),
                    "assigning_team_slug": team.get("slug", ""),
                    "assigning_team_privacy": team.get("privacy", "closed"),
                    "created_at": seat.get("created_at", ""),
                    "updated_at": seat.get("updated_at", ""),
                    "pending_cancellation_date": seat.get("pending_cancellation_date", ""),
                    "last_activity_at": seat.get("last_activity_at", ""),
                    "last_activity_editor": seat.get("last_activity_editor", ""),
                    "plan_type": seat.get("plan_type", "enterprise"),
                    "snapshot_date": snapshot_timestamp(),
                }
            )
            if idx % 500 == 0:
                logger.info("[GithubLicense] Processed %d seats...", idx)
        if not records:
            raise RuntimeError("GitHub Copilot seats API returned no records")
        logger.info("[GithubLicense] Finished fetching %d seats", len(records))
        return pd.DataFrame(records).fillna("")
