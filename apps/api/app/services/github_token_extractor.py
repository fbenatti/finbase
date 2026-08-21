import logging
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
    Extrator de Copilot Seats (tokens/acessos) do GitHub Enterprise.

    Usa o endpoint GET /enterprises/{slug}/copilot/billing/seats para coletar
    todos os usuários com seat Copilot ativo, incluindo dados de último acesso,
    editor utilizado e data de criação do seat.
    Os dados são gravados como Bronze em raw_github_token_{code}.
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
        self.base_url = f"https://api.github.com/enterprises/{self.enterprise_slug}"

    def test_connection(self) -> dict:
        if not self.enterprise_slug or not self.pat_token:
            return {
                "status": "PENDING",
                "message": "GitHub Token — credenciais não configuradas (enterprise_slug e pat_token obrigatórios).",
            }
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get("https://api.github.com/user", headers=self.headers)
                if resp.status_code == 401:
                    return {"status": "FAILED", "message": "Token inválido ou expirado (HTTP 401)."}

                resp_seats = client.get(
                    f"{self.base_url}/copilot/billing/seats",
                    headers=self.headers,
                    params={"per_page": 1},
                )
                if resp_seats.status_code == 200:
                    data = resp_seats.json()
                    total = data.get("total_seats", "?")
                    return {
                        "status": "OK",
                        "message": f"GitHub Enterprise ({self.enterprise_slug}) — {total} seat(s) Copilot ativo(s).",
                    }
                if resp_seats.status_code == 404:
                    return {
                        "status": "FAILED",
                        "message": f"Enterprise '{self.enterprise_slug}' não encontrada ou Copilot não habilitado (HTTP 404).",
                    }
                return {
                    "status": "FAILED",
                    "message": f"HTTP {resp_seats.status_code}: {resp_seats.text[:300]}",
                }
        except Exception as e:
            logger.error("[GithubToken] Erro ao testar conexão: %s", e)
            return {"status": "FAILED", "message": str(e)[:500]}

    def _scan_sync(self, db_session: Session) -> list[BlobScanItem]:
        """
        Seats Copilot não têm período — usamos um blob fixo 'current' que sempre
        é marcado como CHANGED para re-extração a cada execução.
        """
        now = datetime.now(timezone.utc)
        existing = db_session.execute(
            select(IngestedBlob).where(
                IngestedBlob.connection_id == self.connection.id,
                IngestedBlob.blob_path == "current",
            )
        ).scalar_one_or_none()
        status = "CHANGED" if existing else "NEW"
        return [
            BlobScanItem(
                blob_path="current",
                blob_prefix="github_token",
                last_modified=now,
                content_length=None,
                status=status,
                billing_period=None,
            )
        ]

    async def scan(self, db: AsyncSession) -> BlobScanResult:
        now = datetime.now(timezone.utc)
        existing = await db.execute(
            select(IngestedBlob).where(
                IngestedBlob.connection_id == self.connection.id,
                IngestedBlob.blob_path == "current",
            )
        )
        blob_row = existing.scalar_one_or_none()
        status = "CHANGED" if blob_row else "NEW"
        items = [
            BlobScanItem(
                blob_path="current",
                blob_prefix="github_token",
                last_modified=now,
                content_length=None,
                status=status,
                billing_period=None,
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

    def _fetch_tokens(self) -> pd.DataFrame:
        """
        Coleta todos os Copilot seats via paginação.
        Endpoint: GET /enterprises/{slug}/copilot/billing/seats
        Retorna campos: created_at, updated_at, last_activity_at, last_activity_editor,
                        plan_type, pending_cancellation_date + dados do assignee.
        """
        all_seats: list[dict] = []
        page = 1
        per_page = 100

        with httpx.Client(timeout=30) as client:
            while True:
                logger.info(
                    "[GithubToken] Buscando página %d de Copilot seats...", page
                )
                resp = client.get(
                    f"{self.base_url}/copilot/billing/seats",
                    headers=self.headers,
                    params={"per_page": per_page, "page": page},
                )
                if resp.status_code != 200:
                    raise ValueError(
                        f"[GithubToken] Falha ao buscar seats: HTTP {resp.status_code} - {resp.text[:300]}"
                    )
                data = resp.json()
                seats = data.get("seats", [])
                if not seats:
                    break

                # Achata campos aninhados
                for seat in seats:
                    assignee = seat.pop("assignee", {}) or {}
                    assigning_team = seat.pop("assigning_team", {}) or {}
                    seat["username"] = assignee.get("login", "")
                    seat["user_id"] = assignee.get("id", "")
                    seat["user_url"] = assignee.get("html_url", "")
                    seat["assigning_team"] = assigning_team.get("name", "") if assigning_team else ""

                all_seats.extend(seats)
                logger.info(
                    "[GithubToken] Página %d: %d seats (acumulado: %d)",
                    page, len(seats), len(all_seats),
                )
                if len(seats) < per_page:
                    break
                page += 1

        df = pd.DataFrame(all_seats)
        logger.info(
            "[GithubToken] Total de Copilot seats: %d, colunas: %s",
            len(df), list(df.columns) if not df.empty else "vazio",
        )
        return df
