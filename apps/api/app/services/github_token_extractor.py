import calendar
import io
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.ingested_blob import IngestedBlob
from app.models.source_connection import SourceConnection
from app.schemas.schemas import BlobScanItem, BlobScanResult
from app.services.ai_extractor_base import (
    GITHUB_API,
    GitHubEnterpriseClient,
    iter_monthly_periods,
    period_range,
)

logger = logging.getLogger(__name__)

CANONICAL_COLUMNS = (
    "day",
    "assignee_login",
    "aggregation_level",
    "model",
    "total_suggestions_count",
    "total_acceptances_count",
    "total_lines_suggested",
    "total_lines_accepted",
    "total_chat_turns",
    "total_chat_acceptances",
    "total_active_users",
    "editor",
    "language",
    # Provenance and financial usage (kept separate from IDE activity).
    "source_kind",
    "ai_credits",
    "gross_amount",
    "discount_amount",
    "net_amount",
    "billing_currency",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)


class GithubTokenExtractor:
    """GitHub Copilot usage: Copilot Metrics API with billing-report CSV fallback."""

    def __init__(self, connection: SourceConnection):
        self.connection = connection
        self.config_json = connection.config_json or {}
        self.enterprise_slug = self.config_json.get("enterprise_slug", "")
        self.pat_token = self.config_json.get("pat_token", "") or self.config_json.get("client_secret", "")
        self.email_domain = self.config_json.get("email_domain", "telefonica.com.br")
        self._headers = {
            "Authorization": f"Bearer {self.pat_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._billing_reports_url = (
            f"{GITHUB_API}/enterprises/{self.enterprise_slug}/settings/billing/reports"
        )

    def _client(self) -> GitHubEnterpriseClient:
        return GitHubEnterpriseClient(self.pat_token, self.enterprise_slug, self.email_domain)

    def test_connection(self) -> dict:
        try:
            self._client().test()
            return {"status": "OK", "message": f"GitHub Enterprise ({self.enterprise_slug}) — conexão validada"}
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
                    blob_prefix="github_token",
                    last_modified=existing.last_modified if existing else now,
                    content_length=None,
                    status=status,
                    billing_period=period,
                )
            )
        return items

    async def scan(self, db: AsyncSession) -> BlobScanResult:
        periods = iter_monthly_periods(12)
        result = await db.execute(
            select(IngestedBlob).where(
                IngestedBlob.connection_id == self.connection.id,
                IngestedBlob.blob_path.in_(periods),
            )
        )
        existing_map = {row.blob_path: row for row in result.scalars().all()}
        now = datetime.now(timezone.utc)
        items: list[BlobScanItem] = []
        for i, period in enumerate(periods):
            blob_row = existing_map.get(period)
            status = "CHANGED" if (blob_row and i == 0) else ("UNCHANGED" if blob_row else "NEW")
            items.append(
                BlobScanItem(
                    blob_path=period,
                    blob_prefix="github_token",
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

    @staticmethod
    def _normalize_billing_csv(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df.columns = [c.strip().lower().replace(" ", "_").replace("-", "_") for c in df.columns]
        df = df.fillna("")

        # Legacy detailed usage export (day/assignee_login/...) — keep as-is.
        if "day" in df.columns or "assignee_login" in df.columns:
            if "day" not in df.columns and "date" in df.columns:
                df["day"] = df["date"]
            if "assignee_login" not in df.columns and "username" in df.columns:
                df["assignee_login"] = df["username"]
            if "aggregation_level" not in df.columns:
                df["aggregation_level"] = df["assignee_login"].apply(
                    lambda login: "user" if str(login).strip() else "organization"
                )
            for col in CANONICAL_COLUMNS:
                if col not in df.columns:
                    df[col] = ""
            return df[list(CANONICAL_COLUMNS)].fillna("")

        # Current enterprise billing detailed export (date/username/sku/quantity/...).
        records = []
        for row in df.to_dict(orient="records"):
            username = str(row.get("username") or "").strip()
            unit_type = str(row.get("unit_type") or "").strip().lower()
            quantity = row.get("quantity") or "0"
            is_ai_usage_report = any(
                key in row for key in ("total_monthly_quota", "aic_quantity", "cache_read", "cache_write")
            )
            records.append(
                {
                    "day": str(row.get("date") or "")[:10],
                    "assignee_login": username,
                    "aggregation_level": "user" if username else "organization",
                    # The AI-credit export is per date + model + user. SKU only
                    # identifies the billed product and must not replace model.
                    "model": str(row.get("model") or row.get("sku") or row.get("product") or ""),
                    # Financial data is never IDE adoption data. In particular,
                    # discount/gross amounts must not be mapped as accepted lines.
                    "total_suggestions_count": "",
                    "total_acceptances_count": "",
                    "total_lines_suggested": "",
                    "total_lines_accepted": "",
                    # Billing quantity is not an IDE chat interaction. Keep it
                    # in ai_credits so adoption metrics cannot count billing.
                    "total_chat_turns": "",
                    "total_chat_acceptances": "",
                    "total_active_users": "1" if username else "",
                    "editor": str(row.get("cost_center_name") or ""),
                    "language": unit_type,
                    "source_kind": "ai_usage_report" if is_ai_usage_report else "billing_report",
                    "ai_credits": quantity if unit_type in {"ai-credits", "credits"} else "",
                    "gross_amount": str(row.get("gross_amount") or ""),
                    "discount_amount": str(row.get("discount_amount") or ""),
                    "net_amount": str(row.get("net_amount") or ""),
                    "billing_currency": str(row.get("billing_currency") or row.get("currency") or ""),
                    "input_tokens": str(row.get("input") or row.get("input_tokens") or ""),
                    "output_tokens": str(row.get("output") or row.get("output_tokens") or ""),
                    "cache_read_tokens": str(row.get("cache_read") or row.get("cache_read_tokens") or ""),
                    "cache_write_tokens": str(row.get("cache_write") or row.get("cache_write_tokens") or ""),
                }
            )
        return pd.DataFrame(records).fillna("")

    @staticmethod
    def _metric(value: Any, default: Any = 0) -> Any:
        if value is None or value == "":
            return default
        return value

    @classmethod
    def _canonical_from_usage_row(cls, row: dict) -> list[dict]:
        """Map Copilot usage metrics JSON (2026 API) to bronze canonical columns."""
        day = str(row.get("day") or row.get("day_partition") or "")[:10]
        login = str(row.get("user_login") or "").strip()
        level = "user" if login else "organization"
        active_users = "1" if login else cls._metric(row.get("daily_active_users"), "")

        breakdowns = (
            row.get("totals_by_language_model")
            or row.get("totals_by_language_feature")
            or row.get("totals_by_model_feature")
            or row.get("totals_by_ide")
            or []
        )
        records: list[dict] = []
        if breakdowns:
            for item in breakdowns:
                feature = str(item.get("feature") or "").lower()
                chat_turns = ""
                chat_acceptances = ""
                if feature in {"chat", "copilot_app", "agent", "cli"}:
                    chat_turns = cls._metric(
                        item.get("user_initiated_interaction_count")
                        or item.get("prompt_count")
                        or item.get("request_count")
                    )
                    chat_acceptances = cls._metric(item.get("session_count"))
                records.append(
                    {
                        "day": day,
                        "assignee_login": login,
                        "aggregation_level": level,
                        "model": str(item.get("model") or ""),
                        "total_suggestions_count": cls._metric(item.get("code_generation_activity_count")),
                        "total_acceptances_count": cls._metric(item.get("code_acceptance_activity_count")),
                        "total_lines_suggested": cls._metric(item.get("loc_suggested_to_add_sum")),
                        "total_lines_accepted": cls._metric(item.get("loc_added_sum")),
                        "total_chat_turns": chat_turns,
                        "total_chat_acceptances": chat_acceptances,
                        "total_active_users": active_users,
                        "editor": str(item.get("ide") or item.get("editor") or ""),
                        "language": str(item.get("language") or ""),
                        "source_kind": "copilot_metrics",
                        "ai_credits": "",
                        "gross_amount": "",
                        "discount_amount": "",
                        "net_amount": "",
                        "input_tokens": cls._metric(item.get("input_tokens")),
                        "output_tokens": cls._metric(item.get("output_tokens")),
                        "cache_read_tokens": cls._metric(item.get("cache_read_tokens")),
                        "cache_write_tokens": cls._metric(item.get("cache_write_tokens")),
                    }
                )
            return records

        # Legacy aggregated metrics API shape (editors/models/languages).
        for editor in row.get("editors") or []:
            editor_name = editor.get("name") or editor.get("editor") or ""
            for model in editor.get("models") or []:
                model_name = model.get("name") or model.get("model") or ""
                for lang in model.get("languages") or [{}]:
                    lang_name = lang.get("name") or lang.get("language") or ""
                    records.append(
                        {
                            "day": day,
                            "assignee_login": login,
                            "aggregation_level": level,
                            "model": model_name,
                            "total_suggestions_count": cls._metric(
                                lang.get("total_suggestions_count") or model.get("total_suggestions_count")
                            ),
                            "total_acceptances_count": cls._metric(
                                lang.get("total_acceptances_count") or model.get("total_acceptances_count")
                            ),
                            "total_lines_suggested": cls._metric(
                                lang.get("total_lines_suggested") or model.get("total_lines_suggested")
                            ),
                            "total_lines_accepted": cls._metric(
                                lang.get("total_lines_accepted") or model.get("total_lines_accepted")
                            ),
                            "total_chat_turns": cls._metric(
                                lang.get("total_chat_turns") or model.get("total_chat_turns")
                            ),
                            "total_chat_acceptances": cls._metric(
                                lang.get("total_chat_acceptances") or model.get("total_chat_acceptances")
                            ),
                            "total_active_users": cls._metric(
                                row.get("total_active_users") or editor.get("total_active_users") or active_users
                            ),
                            "editor": editor_name,
                            "language": lang_name,
                        }
                    )
        if records:
            return records

        records.append(
            {
                "day": day,
                "assignee_login": login,
                "aggregation_level": level,
                "model": "",
                "total_suggestions_count": cls._metric(row.get("code_generation_activity_count") or row.get("total_suggestions_count")),
                "total_acceptances_count": cls._metric(row.get("code_acceptance_activity_count") or row.get("total_acceptances_count")),
                "total_lines_suggested": cls._metric(row.get("loc_suggested_to_add_sum") or row.get("total_lines_suggested")),
                "total_lines_accepted": cls._metric(row.get("loc_added_sum") or row.get("total_lines_accepted")),
                "total_chat_turns": cls._metric(row.get("total_chat_turns")),
                "total_chat_acceptances": cls._metric(row.get("total_chat_acceptances")),
                "total_active_users": active_users,
                "editor": "",
                "language": "",
            }
        )
        return records

    def _fetch_copilot_metrics_for_period(self, period: str) -> pd.DataFrame:
        client = self._client()
        start, end = period_range(period)
        since = f"{start}T00:00:00Z"
        until = f"{end}T23:59:59Z"
        records: list[dict] = []
        for row in client.iter_copilot_metrics(since, until):
            records.extend(self._canonical_from_usage_row(row))
        if not records:
            return pd.DataFrame()
        df = pd.DataFrame(records).fillna("")
        for col in CANONICAL_COLUMNS:
            if col not in df.columns:
                df[col] = ""
        return df[list(CANONICAL_COLUMNS)].fillna("")

    def _find_billing_report(
        self, client: httpx.Client, start_date: str, end_date: str, report_type: str
    ) -> tuple[str | None, str | None]:
        list_resp = client.get(self._billing_reports_url, headers=self._headers)
        if list_resp.status_code != 200:
            return None, None
        for exp in list_resp.json().get("usage_report_exports", []):
            if (
                exp.get("report_type") == report_type
                and exp.get("start_date") == start_date
                and exp.get("end_date") == end_date
            ):
                return exp.get("id"), (exp.get("status") or "").lower()
        return None, None

    def _fetch_billing_report_for_period(self, period: str) -> tuple[pd.DataFrame, str]:
        """AI-credit export: per-user, per-model financial usage and tokens."""
        year = int(period[:4])
        month = int(period[4:6])
        last_day = calendar.monthrange(year, month)[1]
        now = datetime.now(timezone.utc).date()
        start_date = f"{year:04d}-{month:02d}-01"
        end_date_obj = min(now, datetime(year, month, last_day, tzinfo=timezone.utc).date())
        end_date = end_date_obj.strftime("%Y-%m-%d")

        with httpx.Client(timeout=60) as client:
            report_id, report_status = self._find_billing_report(
                client, start_date, end_date, "ai_credit"
            )

            if not report_id or report_status in ("failed", "error", "cancelled"):
                payload = {
                    "report_type": "ai_credit",
                    "start_date": start_date,
                    "end_date": end_date,
                }
                logger.info("[GithubToken] Criando relatório para %s: %s", period, payload)
                resp = client.post(self._billing_reports_url, headers=self._headers, json=payload)
                if resp.status_code == 409:
                    report_id, report_status = self._find_billing_report(
                        client, start_date, end_date, "ai_credit"
                    )
                    logger.info(
                        "[GithubToken] 409 — relatório em andamento, id=%s status=%s",
                        report_id,
                        report_status,
                    )
                elif resp.status_code in (200, 201, 202):
                    data = resp.json()
                    report_id = data.get("id") or report_id
                    report_status = (data.get("status") or report_status or "").lower()
                else:
                    raise ValueError(
                        f"Falha ao solicitar relatório GitHub: HTTP {resp.status_code} - {resp.text}"
                    )

            if not report_id:
                raise ValueError(f"[GithubToken] Não foi possível obter report_id para {period}")

            if report_status == "completed":
                poll_resp = client.get(f"{self._billing_reports_url}/{report_id}", headers=self._headers)
                if poll_resp.status_code == 200:
                    item_data = poll_resp.json()
                    urls = item_data.get("download_urls") or []
                    download_url = urls[0] if urls else item_data.get("download_url")
                    if download_url:
                        csv_resp = client.get(download_url, follow_redirects=True, timeout=120)
                        if csv_resp.status_code == 200:
                            content = csv_resp.content
                            if content.startswith(b"\x1f\x8b"):
                                import gzip
                                content = gzip.decompress(content)
                            df = pd.read_csv(io.BytesIO(content), dtype=str)
                            return self._normalize_billing_csv(df), str(report_id)
                        raise ValueError(
                            f"Falha ao baixar CSV do GitHub: HTTP {csv_resp.status_code}"
                        )

            download_url = None
            poll_url = f"{self._billing_reports_url}/{report_id}"
            for attempt in range(60):
                poll_resp = client.get(poll_url, headers=self._headers)
                if poll_resp.status_code != 200:
                    logger.warning(
                        "[GithubToken] Polling HTTP %d para %s (tentativa %d/60)",
                        poll_resp.status_code,
                        report_id,
                        attempt + 1,
                    )
                    time.sleep(5)
                    continue
                item_data = poll_resp.json()
                current_status = (item_data.get("status") or "").lower()
                logger.info(
                    "[GithubToken] Polling %s (%d/60): status=%s",
                    report_id,
                    attempt + 1,
                    current_status,
                )
                if current_status == "completed":
                    urls = item_data.get("download_urls") or []
                    download_url = urls[0] if urls else item_data.get("download_url")
                    if download_url:
                        break
                    raise ValueError(
                        f"[GithubToken] Relatório {report_id} completed mas sem download_urls"
                    )
                if current_status in ("failed", "error", "cancelled"):
                    raise ValueError(
                        f"[GithubToken] Relatório {report_id} falhou com status '{current_status}'"
                    )
                time.sleep(5)

            if not download_url:
                raise ValueError(
                    f"[GithubToken] Timeout aguardando geração do relatório para {period} (id={report_id})"
                )

            logger.info("[GithubToken] Baixando CSV do relatório %s...", report_id)
            csv_resp = client.get(download_url, follow_redirects=True, timeout=120)
            if csv_resp.status_code != 200:
                raise ValueError(f"Falha ao baixar CSV do GitHub: HTTP {csv_resp.status_code}")

            content = csv_resp.content
            if content.startswith(b"\x1f\x8b"):
                import gzip
                content = gzip.decompress(content)

            df = pd.read_csv(io.BytesIO(content), dtype=str)
            return self._normalize_billing_csv(df), str(report_id)

    def _fetch_tokens_for_period(self, period: str) -> pd.DataFrame:
        metrics_df = pd.DataFrame()
        try:
            metrics_df = self._fetch_copilot_metrics_for_period(period)
            if not metrics_df.empty:
                metrics_df["source_kind"] = "copilot_metrics"
        except RuntimeError as exc:
            logger.warning(
                "[GithubToken] Copilot usage metrics indisponível para %s: %s",
                period,
                exc,
            )
        try:
            billing_df, _report_id = self._fetch_billing_report_for_period(period)
        except Exception as exc:
            if metrics_df.empty:
                raise
            logger.warning("[GithubToken] Billing report indisponível para %s: %s", period, exc)
            billing_df = pd.DataFrame()
        if metrics_df.empty:
            logger.warning("[GithubToken] Copilot metrics vazio para %s; usando billing report CSV", period)
            return billing_df
        if billing_df.empty:
            return metrics_df
        return pd.concat([metrics_df, billing_df], ignore_index=True, sort=False).fillna("")

    def _fetch_report_for_period(self, period: str) -> tuple[pd.DataFrame, str]:
        metrics_df = pd.DataFrame()
        try:
            metrics_df = self._fetch_copilot_metrics_for_period(period)
            if not metrics_df.empty:
                metrics_df["source_kind"] = "copilot_metrics"
        except RuntimeError as exc:
            logger.warning(
                "[GithubToken] Copilot usage metrics indisponível para %s: %s",
                period,
                exc,
            )
        billing_df, report_id = self._fetch_billing_report_for_period(period)
        if metrics_df.empty:
            return billing_df, report_id
        if billing_df.empty:
            return metrics_df, f"github_copilot_metrics_{period}"
        return pd.concat([metrics_df, billing_df], ignore_index=True, sort=False).fillna(""), report_id
