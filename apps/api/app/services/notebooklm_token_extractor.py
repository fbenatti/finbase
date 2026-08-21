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
from app.services.google_extractor_utils import google_client_from_config, query_billing_export
from app.services.google_workspace_client import usage_dates_for_period

logger = logging.getLogger(__name__)


class NotebookLMTokenExtractor:
    """Google Reports API usage + BigQuery billing export for NotebookLM/Gemini."""

    def __init__(self, connection: SourceConnection):
        self.connection = connection
        self.config_json = connection.config_json or {}
        self.project_id = self.config_json.get("project_id", "")
        self.project_number = self.config_json.get("project_number", "")
        self.billing_dataset = self.config_json.get("billing_export_dataset", "")
        self.billing_table = self.config_json.get("billing_export_table", "gcp_billing_export_v1")

    def test_connection(self) -> dict:
        try:
            google_client_from_config(self.config_json).test()
            return {"status": "OK", "message": f"Google ({self.project_id}) — conexão validada"}
        except Exception:
            try:
                query_billing_export("SELECT 1 AS ok FROM {BILLING_TABLE} LIMIT 1")
                return {"status": "OK", "message": "Workspace indisponível; billing GCP das conexões existentes será usado"}
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
                    blob_prefix="notebooklm_token",
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
                    blob_prefix="notebooklm_token",
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
        records: list[dict] = []
        try:
            client = google_client_from_config(self.config_json)
            for date in usage_dates_for_period(period):
                for report in client.iter_user_usage_report(date, "gemini_in_workspace_apps"):
                    email = report.get("entity", {}).get("userEmail") or report.get("userEmail") or ""
                    for param in report.get("parameters") or []:
                        records.append(
                            {
                                "record_type": "usage_report",
                                "aggregation_level": "user",
                                "usage_start_time": f"{date}T00:00:00Z",
                                "usage_end_time": f"{date}T23:59:59Z",
                                "invoice_month": period,
                                "user_email": email,
                                "model_id": param.get("name") or "",
                                "prompt_token_count": param.get("intValue") or param.get("msgValue") or 0,
                                "candidates_token_count": 0,
                                "cached_content_token_count": 0,
                                "request_count": 1,
                                "project_id": self.project_id,
                                "project_number": self.project_number,
                            }
                        )
        except Exception as exc:
            logger.warning("[NotebookLMToken] Reports API indisponível: %s", exc)

        sql = f"""
            SELECT
              CAST(usage_start_time AS STRING) AS usage_start_time,
              CAST(usage_end_time AS STRING) AS usage_end_time,
              CAST(cost AS STRING) AS cost,
              currency,
              CAST(usage.amount AS STRING) AS usage_amount,
              usage.unit AS usage_unit,
              billing_account_id,
              service.id AS service_id,
              service.description AS service_description,
              sku.id AS sku_id,
              sku.description AS sku_description,
              project.id AS project_id,
              project.name AS project_name,
              CAST(project.number AS STRING) AS project_number,
              location.location AS location,
              location.country AS country,
              location.region AS region,
              location.zone AS zone,
              cost_type,
              CAST((SELECT SUM(c.amount) FROM UNNEST(credits) c) AS STRING) AS credits_amount,
              (SELECT value FROM UNNEST(labels) WHERE key IN ('user','user_email','email') LIMIT 1) AS user_email
            FROM {{BILLING_TABLE}}
            WHERE invoice.month = '{period}'
              AND (
                LOWER(IFNULL(service.description, '')) LIKE '%gemini%'
                OR LOWER(IFNULL(service.description, '')) LIKE '%notebook%'
                OR LOWER(IFNULL(sku.description, '')) LIKE '%gemini%'
                OR LOWER(IFNULL(sku.description, '')) LIKE '%notebook%'
                OR LOWER(IFNULL(service.description, '')) LIKE '%vertex%'
                OR project.id = '{self.project_id}'
              )
        """
        try:
            for row in query_billing_export(sql):
                records.append(
                    {
                        "record_type": "billing_export",
                        "aggregation_level": "user" if row.get("user_email") else "organization",
                        "usage_start_time": row.get("usage_start_time") or "",
                        "usage_end_time": row.get("usage_end_time") or "",
                        "invoice_month": period,
                        "user_email": row.get("user_email") or "",
                        "model_id": row.get("sku_description") or "",
                        "prompt_token_count": 0,
                        "candidates_token_count": 0,
                        "cached_content_token_count": 0,
                        "request_count": 1,
                        "cost": row.get("cost") or 0,
                        "currency": row.get("currency") or "",
                        "usage_amount": row.get("usage_amount") or 0,
                        "usage_unit": row.get("usage_unit") or "",
                        "billing_account_id": row.get("billing_account_id") or "",
                        "service_id": row.get("service_id") or "",
                        "service_description": row.get("service_description") or "",
                        "sku_id": row.get("sku_id") or "",
                        "sku_description": row.get("sku_description") or "",
                        "project_id": row.get("project_id") or self.project_id,
                        "project_name": row.get("project_name") or "",
                        "project_number": row.get("project_number") or self.project_number,
                        "location": row.get("location") or "",
                        "country": row.get("country") or "",
                        "region": row.get("region") or "",
                        "zone": row.get("zone") or "",
                        "cost_type": row.get("cost_type") or "",
                        "credits_amount": row.get("credits_amount") or 0,
                    }
                )
        except Exception as exc:
            logger.warning("[NotebookLMToken] BigQuery billing export skipped: %s", exc)

        if not records:
            # Keep Bronze contract: one empty-safe period row is worse than failing.
            raise RuntimeError(
                f"Nenhum consumo NotebookLM/Gemini encontrado no billing GCP para {period}"
            )
        return pd.DataFrame(records).fillna("")
