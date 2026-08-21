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
    is_enterprise_analytics_key,
    iter_monthly_periods,
    period_analytics_dates,
    period_range,
)

logger = logging.getLogger(__name__)


class ClaudeTokenExtractor:
    """Raw Anthropic usage (claude_code + messages) and cost reports."""

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
            from app.services.ai_extractor_base import current_period

            client = self._client()
            period = current_period()
            if is_enterprise_analytics_key(self.api_token):
                client.test_analytics_usage(period)
                return {
                    "status": "OK",
                    "message": "Claude Enterprise Analytics API — consumo (read:analytics) validado",
                }
            client.test_usage(period)
            return {"status": "OK", "message": "Anthropic Admin API — consumo (usage_report) validado"}
        except Exception as exc:
            msg = str(exc)
            if "read:analytics" in msg:
                return {"status": "ERROR", "message": msg}
            if "api:admin" in msg:
                return {
                    "status": "ERROR",
                    "message": (
                        "Este endpoint é do Claude Console (api:admin). Organizações Enterprise usam "
                        "read:analytics em claude.ai → Configurações → APIs."
                    ),
                }
            return {"status": "ERROR", "message": msg}

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
                    blob_prefix="claude_token",
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
                    blob_prefix="claude_token",
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

    def _fetch_enterprise_analytics(self, period: str) -> list[dict]:
        client = self._client()
        start, end = period_analytics_dates(period)
        records: list[dict] = []
        for row in client.iter_analytics_users(start, end):
            user = row.get("user") or {}
            email = user.get("email_address") or user.get("email") or ""
            usage_date = row.get("last_activity_date") or start
            code = (row.get("claude_code_metrics") or {}).get("core_metrics") or {}
            loc = code.get("lines_of_code") or {}
            sessions = code.get("distinct_session_count") or 0
            if sessions or code.get("commit_count") or loc.get("added_count"):
                records.append(
                    {
                        "record_type": "analytics_claude_code",
                        "aggregation_level": "user",
                        "usage_date": usage_date,
                        "actor_email_address": email,
                        "model": "claude_code",
                        "prompt_tokens": loc.get("added_count") or 0,
                        "completion_tokens": loc.get("removed_count") or 0,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                        "request_count": sessions,
                        "organization_id": self.org_id,
                        "organization_uuid": user.get("id") or "",
                    }
                )
            chat = row.get("chat_metrics") or {}
            messages = chat.get("message_count") or 0
            if messages:
                records.append(
                    {
                        "record_type": "analytics_chat",
                        "aggregation_level": "user",
                        "usage_date": usage_date,
                        "actor_email_address": email,
                        "model": "claude_chat",
                        "prompt_tokens": messages,
                        "completion_tokens": chat.get("thinking_message_count") or 0,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                        "request_count": chat.get("distinct_conversation_count") or messages,
                        "organization_id": self.org_id,
                        "organization_uuid": user.get("id") or "",
                    }
                )
        return records

    def _fetch_tokens_for_period(self, period: str) -> pd.DataFrame:
        if is_enterprise_analytics_key(self.api_token):
            records = self._fetch_enterprise_analytics(period)
            if not records:
                logger.warning("[ClaudeToken] No Enterprise analytics records for period %s", period)
            return pd.DataFrame(records).fillna("")

        client = self._client()
        start, end = period_range(period)
        records: list[dict] = []

        for row in client.iter_usage_claude_code(start, end):
            actor = row.get("actor") or {}
            email = actor.get("email_address") or actor.get("email") or ""
            model = row.get("model") or row.get("model_name") or ""
            records.append(
                {
                    "record_type": "usage_claude_code",
                    "aggregation_level": "user",
                    "usage_date": row.get("date") or row.get("day") or "",
                    "actor_email_address": email,
                    "model": model,
                    "prompt_tokens": row.get("input_tokens") or row.get("prompt_tokens") or 0,
                    "completion_tokens": row.get("output_tokens") or row.get("completion_tokens") or 0,
                    "cache_read_input_tokens": row.get("cache_read_input_tokens") or 0,
                    "cache_creation_input_tokens": row.get("cache_creation_input_tokens") or 0,
                    "request_count": row.get("num_sessions") or row.get("request_count") or 1,
                    "organization_id": self.org_id,
                    "organization_uuid": row.get("organization_uuid") or "",
                }
            )

        for row in client.iter_usage_messages(start, end):
            records.append(
                {
                    "record_type": "usage_messages",
                    "aggregation_level": "organization",
                    "usage_date": row.get("date") or "",
                    "actor_email_address": "",
                    "model": row.get("model") or "",
                    "prompt_tokens": row.get("input_tokens") or row.get("prompt_tokens") or 0,
                    "completion_tokens": row.get("output_tokens") or row.get("completion_tokens") or 0,
                    "cache_read_input_tokens": row.get("cache_read_input_tokens") or 0,
                    "cache_creation_input_tokens": row.get("cache_creation_input_tokens") or 0,
                    "request_count": row.get("request_count") or 0,
                    "organization_id": self.org_id,
                    "organization_uuid": row.get("organization_uuid") or "",
                    "api_key_id": row.get("api_key_id") or "",
                    "workspace_id": row.get("workspace_id") or "",
                }
            )

        for row in client.iter_cost_report(start, end):
            records.append(
                {
                    "record_type": "cost_report",
                    "aggregation_level": "organization",
                    "usage_date": row.get("date") or row.get("day") or "",
                    "actor_email_address": "",
                    "model": row.get("model") or "",
                    "prompt_tokens": row.get("input_tokens") or row.get("prompt_tokens") or 0,
                    "completion_tokens": row.get("output_tokens") or row.get("completion_tokens") or 0,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "request_count": row.get("request_count") or 0,
                    "organization_id": self.org_id,
                    "organization_uuid": row.get("organization_uuid") or "",
                    "cost_usd": row.get("amount") or row.get("cost_usd") or row.get("cost") or 0,
                    "currency": row.get("currency") or "USD",
                    "api_key_id": row.get("api_key_id") or "",
                    "workspace_id": row.get("workspace_id") or "",
                }
            )

        if not records:
            logger.warning("[ClaudeToken] No usage/cost records for period %s", period)
        return pd.DataFrame(records).fillna("")

    def usage_last_active_by_email(self, period: str) -> dict[str, str]:
        df = self._fetch_tokens_for_period(period)
        if df.empty:
            return {}
        user_rows = df[df["aggregation_level"] == "user"] if "aggregation_level" in df.columns else df
        if user_rows.empty or "actor_email_address" not in user_rows.columns:
            return {}
        grouped = (
            user_rows.groupby("actor_email_address")["usage_date"]
            .max()
            .dropna()
            .to_dict()
        )
        return {str(k): str(v) for k, v in grouped.items() if k}
