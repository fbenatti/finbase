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


class GithubLicenseExtractor:
    """
    Extrator de licenças consumidas do GitHub Enterprise.

    Usa o endpoint GET /enterprises/{slug}/consumed-licenses para coletar
    todos os usuários com licenças ativas (Enterprise, Copilot, VS, etc.).
    Os dados são gravados como Bronze em raw_github_license_{code}.
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
                "message": "GitHub License — credenciais não configuradas (enterprise_slug e pat_token obrigatórios).",
            }
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get("https://api.github.com/user", headers=self.headers)
                if resp.status_code == 401:
                    return {"status": "FAILED", "message": "Token inválido ou expirado (HTTP 401)."}

                resp_lic = client.get(
                    f"{self.base_url}/consumed-licenses",
                    headers=self.headers,
                    params={"per_page": 1},
                )
                if resp_lic.status_code == 200:
                    data = resp_lic.json()
                    total = data.get("total_seats_consumed", "?")
                    return {
                        "status": "OK",
                        "message": f"GitHub Enterprise ({self.enterprise_slug}) — {total} licença(s) consumida(s).",
                    }
                if resp_lic.status_code == 404:
                    return {
                        "status": "FAILED",
                        "message": f"Enterprise '{self.enterprise_slug}' não encontrada ou sem permissão (HTTP 404).",
                    }
                return {
                    "status": "FAILED",
                    "message": f"HTTP {resp_lic.status_code}: {resp_lic.text[:300]}",
                }
        except Exception as e:
            logger.error("[GithubLicense] Erro ao testar conexão: %s", e)
            return {"status": "FAILED", "message": str(e)[:500]}

    def _scan_sync(self, db_session: Session) -> list[BlobScanItem]:
        """
        Licenças não têm período — usamos um blob fixo 'current' que sempre
        é marcado como CHANGED para forçar a re-extração a cada execução.
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
                blob_prefix="github_license",
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
                blob_prefix="github_license",
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

    def _fetch_licenses(self) -> pd.DataFrame:
        """
        Coleta todos os usuários com licenças consumidas via paginação.
        Endpoint: GET /enterprises/{slug}/consumed-licenses
        """
        all_users: list[dict] = []
        page = 1
        per_page = 100

        with httpx.Client(timeout=30) as client:
            while True:
                logger.info(
                    "[GithubLicense] Buscando página %d de usuários licenciados...", page
                )
                resp = client.get(
                    f"{self.base_url}/consumed-licenses",
                    headers=self.headers,
                    params={"per_page": per_page, "page": page},
                )
                if resp.status_code != 200:
                    raise ValueError(
                        f"[GithubLicense] Falha ao buscar licenças: HTTP {resp.status_code} - {resp.text[:300]}"
                    )
                data = resp.json()
                users = data.get("users", [])
                if not users:
                    break
                # Achata campos aninhados (listas → string separada por vírgula)
                for user in users:
                    for k, v in user.items():
                        if isinstance(v, list):
                            user[k] = ", ".join(str(i) for i in v) if v else ""
                all_users.extend(users)
                logger.info(
                    "[GithubLicense] Página %d: %d usuários (acumulado: %d)",
                    page, len(users), len(all_users),
                )
                if len(users) < per_page:
                    break
                page += 1

        df = pd.DataFrame(all_users)
        logger.info(
            "[GithubLicense] Total de usuários licenciados: %d, colunas: %s",
            len(df), list(df.columns) if not df.empty else "vazio",
        )
        return df
