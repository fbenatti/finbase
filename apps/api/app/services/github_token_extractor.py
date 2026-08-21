import calendar
import io
import logging
import time
from datetime import datetime, timezone

import httpx
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.source_connection import SourceConnection
from app.models.ingested_blob import IngestedBlob
from app.schemas.schemas import BlobScanItem, BlobScanResult

logger = logging.getLogger(__name__)


class GithubTokenExtractor:
    """
    Extrator de consumo de tokens e métricas de uso (Copilot / Actions) do GitHub Enterprise.
    Gera e coleta relatórios mensais de consumo através da API de billing reports.
    """

    def __init__(self, connection: SourceConnection):
        self.connection = connection
        self.config_json = connection.config_json or {}
        self.enterprise_slug = self.config_json.get("enterprise_slug", "")
        self.pat_token = self.config_json.get("pat_token", "")
        self.headers = {
            "Authorization": f"Bearer {self.pat_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self.base_url = f"https://api.github.com/enterprises/{self.enterprise_slug}/settings/billing/reports"

    def test_connection(self) -> dict:
        if not self.enterprise_slug or not self.pat_token:
            return {
                "status": "PENDING",
                "message": "GitHub Token — credenciais não configuradas.",
            }
        try:
            with httpx.Client(timeout=15) as client:
                resp_user = client.get("https://api.github.com/user", headers=self.headers)
                if resp_user.status_code == 401:
                    return {"status": "FAILED", "message": "GitHub retornou HTTP 401: Token inválido ou expirado (Bad credentials)."}

                resp = client.get(self.base_url, headers=self.headers)
                if resp.status_code in (200, 201, 202, 400, 422):
                    return {
                        "status": "OK",
                        "message": f"GitHub Enterprise ({self.enterprise_slug}) — Conexão validada com sucesso!",
                    }
                if resp.status_code == 404:
                    return {
                        "status": "FAILED",
                        "message": f"HTTP 404: A Enterprise '{self.enterprise_slug}' não foi encontrada ou o token não possui 'manage_billing:enterprise'.",
                    }
                return {
                    "status": "FAILED",
                    "message": f"GitHub retornou HTTP {resp.status_code}: {resp.text[:500]}",
                }
        except Exception as e:
            logger.error("[GithubToken] Erro ao testar conexao: %s", e)
            return {"status": "FAILED", "message": str(e)[:500]}

    def _scan_sync(self, db_session: Session) -> list[BlobScanItem]:
        """
        Lista os últimos 12 meses para permitir extração histórica e atual de consumo de tokens.
        """
        items: list[BlobScanItem] = []
        now = datetime.now(timezone.utc)
        
        for i in range(12):
            m = now.month - i
            y = now.year
            while m <= 0:
                m += 12
                y -= 1
            period = f"{y}{m:02d}"
            
            existing = db_session.execute(
                select(IngestedBlob).where(
                    IngestedBlob.connection_id == self.connection.id,
                    IngestedBlob.blob_path == period
                )
            ).scalar_one_or_none()
            
            if i == 0:
                status = "CHANGED" if existing else "NEW"
            else:
                status = "UNCHANGED" if existing else "NEW"
            
            items.append(BlobScanItem(
                blob_path=period,
                blob_prefix="github_token",
                last_modified=existing.last_modified if existing else now,
                content_length=None,
                status=status,
                billing_period=period,
            ))

        return items

    async def scan(self, db: AsyncSession) -> BlobScanResult:
        items = []
        now = datetime.now(timezone.utc)
        
        for i in range(12):
            m = now.month - i
            y = now.year
            while m <= 0:
                m += 12
                y -= 1
            period = f"{y}{m:02d}"
            
            existing = await db.execute(
                select(IngestedBlob).where(
                    IngestedBlob.connection_id == self.connection.id,
                    IngestedBlob.blob_path == period
                )
            )
            blob_row = existing.scalar_one_or_none()
            
            if i == 0:
                status = "CHANGED" if blob_row else "NEW"
            else:
                status = "UNCHANGED" if blob_row else "NEW"
            
            items.append(BlobScanItem(
                blob_path=period,
                blob_prefix="github_token",
                last_modified=blob_row.last_modified if blob_row else now,
                content_length=None,
                status=status,
                billing_period=period,
            ))
            
        new_count = sum(1 for it in items if it.status == "NEW")
        changed_count = sum(1 for it in items if it.status == "CHANGED")
        unchanged_count = sum(1 for it in items if it.status == "UNCHANGED")
        return BlobScanResult(
            connection_id=self.connection.id,
            total=len(items),
            new=new_count,
            changed=changed_count,
            unchanged=unchanged_count,
            items=items,
        )

    def _fetch_report_for_period(self, period: str) -> tuple[pd.DataFrame, str]:
        """
        Solicita ou reutiliza o relatório detalhado de consumo de tokens para o período.
        Retorna (DataFrame, report_id).
        """
        year = int(period[:4])
        month = int(period[4:6])
        last_day = calendar.monthrange(year, month)[1]

        now = datetime.now(timezone.utc).date()
        start_date = f"{year:04d}-{month:02d}-01"
        end_date_obj = min(now, datetime(year, month, last_day, tzinfo=timezone.utc).date())
        end_date = end_date_obj.strftime("%Y-%m-%d")

        with httpx.Client(timeout=60) as client:
            report_id = None
            report_status = None
            logger.info("[GithubToken] Verificando relatórios existentes para %s...", period)
            list_resp = client.get(self.base_url, headers=self.headers)
            if list_resp.status_code == 200:
                exports = list_resp.json().get("usage_report_exports", [])
                for exp in exports:
                    if (
                        exp.get("report_type") == "detailed"
                        and exp.get("start_date") == start_date
                        and exp.get("end_date") == end_date
                    ):
                        report_id = exp.get("id")
                        report_status = (exp.get("status") or "").lower()
                        logger.info("[GithubToken] Relatório existente: id=%s status=%s", report_id, report_status)
                        break

            if not report_id or report_status in ("failed", "error", "cancelled"):
                payload = {
                    "report_type": "detailed",
                    "start_date": start_date,
                    "end_date": end_date,
                }
                logger.info("[GithubToken] Criando relatório para %s: %s", period, payload)
                resp = client.post(self.base_url, headers=self.headers, json=payload)
                if resp.status_code == 409:
                    logger.info("[GithubToken] Relatório já em andamento: id=%s", report_id)
                elif resp.status_code in (200, 201, 202):
                    data = resp.json()
                    report_id = data.get("id") or period
                    report_status = (data.get("status") or "").lower()
                else:
                    raise ValueError(f"Falha ao solicitar relatório GitHub: HTTP {resp.status_code} - {resp.text}")

            if not report_id:
                raise ValueError(f"[GithubToken] Não foi possível obter report_id para {period}")

            # Polling usando o endpoint do report específico (onde o download_urls é retornado)
            download_url = None
            for attempt in range(40):
                poll_resp = client.get(f"{self.base_url}/{report_id}", headers=self.headers)
                if poll_resp.status_code == 200:
                    item_data = poll_resp.json()
                    current_status = (item_data.get("status") or "").lower()
                    if current_status == "completed":
                        urls = item_data.get("download_urls") or []
                        if urls:
                            download_url = urls[0]
                        elif item_data.get("download_url"):
                            download_url = item_data.get("download_url")
                        if download_url:
                            break
                    elif current_status in ("failed", "error", "cancelled"):
                        raise ValueError(f"[GithubToken] Relatório {report_id} falhou com status '{current_status}'")
                time.sleep(2)


            if not download_url:
                raise ValueError(f"[GithubToken] Timeout aguardando geração do relatório para {period} (id={report_id})")

            # Download CSV
            logger.info("[GithubToken] Baixando CSV do relatório %s...", report_id)
            csv_resp = client.get(download_url, timeout=120)
            if csv_resp.status_code != 200:
                raise ValueError(f"Falha ao baixar CSV do GitHub: HTTP {csv_resp.status_code}")

            content = csv_resp.content
            if content.startswith(b"\x1f\x8b"):
                import gzip
                content = gzip.decompress(content)

            df = pd.read_csv(io.BytesIO(content), dtype=str)
            df = df.fillna("")
            df.columns = [c.strip().lower().replace(" ", "_").replace("-", "_") for c in df.columns]
            return df, report_id
