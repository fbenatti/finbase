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

class GithubBillingExtractor:
    """Extrator de relatórios de billing do GitHub (Enterprise)."""

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
                "message": "GitHub Billing — credenciais não configuradas.",
            }
        try:
            with httpx.Client(timeout=15) as client:
                # Testa se o token é válido
                resp_user = client.get("https://api.github.com/user", headers=self.headers)
                if resp_user.status_code == 401:
                    return {"status": "FAILED", "message": "GitHub retornou HTTP 401: Token inválido ou expirado (Bad credentials)."}

                # Testa o acesso à Enterprise/Org (tentando listar os relatórios ou checar a existência)
                # Como a geração de relatório é POST, vamos fazer um GET básico nas settings de billing
                resp = client.get(
                    f"https://api.github.com/enterprises/{self.enterprise_slug}/settings/billing/reports",
                    headers=self.headers
                )
                if resp.status_code in (200, 201, 202, 400, 422): # Qualquer código que indique que a rota existe e fomos autenticados
                    return {
                        "status": "OK",
                        "message": f"GitHub Enterprise ({self.enterprise_slug}) — Conexão validada com sucesso!",
                    }
                if resp.status_code == 404:
                    return {
                        "status": "FAILED",
                        "message": f"HTTP 404: A Enterprise '{self.enterprise_slug}' não foi encontrada. Verifique se o nome (slug) está correto e se o token possui permissão de 'manage_billing:enterprise'. (Se a sua conta for uma Organization e não Enterprise, avise-me para ajustar a rota!).",
                    }
                
                return {
                    "status": "FAILED",
                    "message": f"GitHub retornou HTTP {resp.status_code}: {resp.text}"[:500],
                }
        except Exception as e:
            logger.error("Erro ao testar conexao GitHub: %s", e)
            return {"status": "FAILED", "message": str(e)[:500]}

    def _scan_sync(self, db_session: Session) -> list[BlobScanItem]:
        """
        Lista os últimos 12 meses para permitir extração histórica e atual.
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
            
            # Mês atual sempre como CHANGED para permitir atualização contínua de custos do mês corrente
            if i == 0:
                status = "CHANGED" if existing else "NEW"
            else:
                status = "UNCHANGED" if existing else "NEW"
            
            items.append(BlobScanItem(
                blob_path=period,
                blob_prefix="github_reports",
                last_modified=existing.last_modified if existing else now,
                content_length=None,
                status=status,
                billing_period=period,
            ))

        return items

    async def scan(self, db: AsyncSession) -> BlobScanResult:
        items = []
        now = datetime.now(timezone.utc)
        
        # Lista os últimos 12 meses para permitir extração histórica e atual
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
            
            # Mês atual sempre como CHANGED para permitir atualização contínua de custos do mês corrente
            if i == 0:
                status = "CHANGED" if blob_row else "NEW"
            else:
                status = "UNCHANGED" if blob_row else "NEW"
            
            items.append(BlobScanItem(
                blob_path=period,
                blob_prefix="github_reports",
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
        Solicita ou reutiliza um relatório detalhado do GitHub para o período e faz o polling.
        Retorna (DataFrame, report_id).
        
        Comportamentos importantes da API do GitHub:
        - A lista de relatórios fica em resp["usage_report_exports"] (lista, não raiz)
        - O status é o campo "status" (não "state")
        - Quando "completed", as URLs estão em "download_urls" (lista)
        - As URLs são pré-assinadas do Azure → NÃO enviar headers do GitHub
        - Retorna 409 se já há um relatório em processamento
        """
        import calendar
        year = int(period[:4])
        month = int(period[4:6])
        last_day = calendar.monthrange(year, month)[1]

        now = datetime.now(timezone.utc).date()
        start_date = f"{year:04d}-{month:02d}-01"
        end_date_obj = min(now, datetime(year, month, last_day, tzinfo=timezone.utc).date())
        end_date = end_date_obj.strftime("%Y-%m-%d")

        with httpx.Client(timeout=60) as client:
            # ──────────────────────────────────────────────────────────────
            # 1. Verificar se já existe um relatório "detailed" para o período
            # ──────────────────────────────────────────────────────────────
            report_id = None
            report_status = None
            logger.info("[GitHub] Listando relatórios existentes para verificar %s...", period)
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
                        logger.info(
                            "[GitHub] Relatório existente: id=%s status=%s",
                            report_id, report_status
                        )
                        break

            # ──────────────────────────────────────────────────────────────
            # 2. Criar relatório se não existir ou se o existente falhou
            # ──────────────────────────────────────────────────────────────
            if not report_id or report_status in ("failed", "error", "cancelled"):
                payload = {
                    "report_type": "detailed",
                    "start_date": start_date,
                    "end_date": end_date,
                }
                logger.info("[GitHub] Criando relatório para %s: payload=%s", period, payload)
                resp = client.post(self.base_url, headers=self.headers, json=payload)

                if resp.status_code == 409:
                    # Já há um em andamento — o report_id da listagem já está definido
                    logger.info(
                        "[GitHub] 409 — Relatório já em andamento. Usando id=%s", report_id
                    )
                elif resp.status_code in (200, 201, 202):
                    data = resp.json()
                    report_id = data.get("id") or period
                    report_status = (data.get("status") or "").lower()
                    logger.info(
                        "[GitHub] Relatório criado: id=%s status=%s", report_id, report_status
                    )
                else:
                    raise ValueError(
                        f"Falha ao solicitar relatório GitHub: HTTP {resp.status_code} - {resp.text}"
                    )

            if not report_id:
                raise ValueError(f"[GitHub] Não foi possível obter report_id para {period}")

            # ──────────────────────────────────────────────────────────────
            # 3. Se já está completed, pula o polling
            # ──────────────────────────────────────────────────────────────
            if report_status == "completed":
                logger.info("[GitHub] Relatório %s já está completed, buscando download_urls...", report_id)
                poll_resp = client.get(f"{self.base_url}/{report_id}", headers=self.headers)
                if poll_resp.status_code == 200:
                    poll_data = poll_resp.json()
                    download_urls = poll_data.get("download_urls") or []
                    if download_urls:
                        logger.info("[GitHub] Baixando CSV de URL pré-assinada (sem auth header)...")
                        csv_resp = client.get(download_urls[0], follow_redirects=True)
                        if csv_resp.status_code == 200:
                            df = pd.read_csv(io.StringIO(csv_resp.text))
                            logger.info(
                                "[GitHub] %s baixado: %d linhas, cols=%s",
                                period, len(df), list(df.columns)
                            )
                            return df, str(report_id)
                        raise ValueError(
                            f"[GitHub] Falha ao baixar CSV ({csv_resp.status_code}): {csv_resp.text[:300]}"
                        )

            # ──────────────────────────────────────────────────────────────
            # 4. Polling: aguarda até status = completed
            # ──────────────────────────────────────────────────────────────
            poll_url = f"{self.base_url}/{report_id}"
            for attempt in range(60):  # até 5 min (60 × 5s)
                poll_resp = client.get(poll_url, headers=self.headers)
                if poll_resp.status_code != 200:
                    logger.warning(
                        "[GitHub] Polling retornou HTTP %d para %s",
                        poll_resp.status_code, poll_url
                    )
                    time.sleep(5)
                    continue

                poll_data = poll_resp.json()
                status = (poll_data.get("status") or "").lower()
                logger.info(
                    "[GitHub] Polling %s (%d/60): status=%s",
                    report_id, attempt + 1, status
                )

                if status == "completed":
                    # download_urls é uma LISTA — NÃO enviar headers do GitHub (Azure CDN pré-assinado)
                    download_urls = poll_data.get("download_urls") or []
                    if not download_urls:
                        raise ValueError(
                            f"[GitHub] Relatório {report_id} completed mas sem download_urls"
                        )
                    logger.info(
                        "[GitHub] Baixando CSV de URL pré-assinada: %s...",
                        download_urls[0][:80]
                    )
                    csv_resp = client.get(download_urls[0], follow_redirects=True)
                    if csv_resp.status_code != 200:
                        raise ValueError(
                            f"[GitHub] Falha ao baixar CSV ({csv_resp.status_code}): {csv_resp.text[:300]}"
                        )
                    df = pd.read_csv(io.StringIO(csv_resp.text))
                    logger.info(
                        "[GitHub] %s baixado com sucesso: %d linhas, cols=%s",
                        period, len(df), list(df.columns)
                    )
                    return df, str(report_id)

                if status in ("failed", "error", "cancelled"):
                    raise ValueError(
                        f"[GitHub] Geração do relatório {report_id} falhou: status={status}"
                    )

                time.sleep(5)

            raise TimeoutError(
                f"[GitHub] Tempo excedido aguardando relatório {report_id} para {period}"
            )

