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
                        "message": f"GitHub Enterprise ({self.enterprise_slug}) — {total} usuário(s) com licenças ativas.",
                    }
                if resp_seats.status_code == 404:
                    return {
                        "status": "FAILED",
                        "message": f"Enterprise '{self.enterprise_slug}' não encontrada (HTTP 404).",
                    }
                return {
                    "status": "FAILED",
                    "message": f"HTTP {resp_seats.status_code}: {resp_seats.text[:300]}",
                }
        except Exception as e:
            logger.error("[GithubLicense] Erro ao testar conexão: %s", e)
            return {"status": "FAILED", "message": str(e)[:500]}

    def _scan_sync(self, db_session: Session) -> list[BlobScanItem]:
        """
        Lista de usuários e licenças associadas ao período atual.
        """
        now = datetime.now(timezone.utc)
        current_period = now.strftime("%Y%m")
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
                billing_period=current_period,
            )
        ]

    async def scan(self, db: AsyncSession) -> BlobScanResult:
        now = datetime.now(timezone.utc)
        current_period = now.strftime("%Y%m")
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
                billing_period=current_period,
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
        Coleta a lista completa de usuários e licenças ativas da Enterprise (Copilot Seats + Consumed Licenses).
        """
        all_users: list[dict] = []
        page = 1
        per_page = 100

        with httpx.Client(timeout=30) as client:
            # 1. Coleta todos os Copilot seats via paginação
            while True:
                logger.info("[GithubLicense] Buscando página %d de usuários/seats Copilot...", page)
                resp = client.get(
                    f"{self.base_url}/copilot/billing/seats",
                    headers=self.headers,
                    params={"per_page": per_page, "page": page},
                )
                if resp.status_code != 200:
                    logger.warning("[GithubLicense] Erro ao buscar copilot seats: HTTP %d", resp.status_code)
                    break
                data = resp.json()
                seats = data.get("seats", [])
                if not seats:
                    break

                for s in seats:
                    assignee = s.get("assignee") or {}
                    team = s.get("assigning_team") or {}
                    all_users.append({
                        "username": assignee.get("login", ""),
                        "user_id": str(assignee.get("id", "")),
                        "user_url": assignee.get("html_url", ""),
                        "name": "",
                        "license_type": "Copilot " + (s.get("plan_type") or "business").title(),
                        "plan_type": s.get("plan_type", ""),
                        "team_name": team.get("name", "") if team else "",
                        "team_slug": team.get("slug", "") if team else "",
                        "created_at": s.get("created_at", ""),
                        "updated_at": s.get("updated_at", ""),
                        "last_activity_at": s.get("last_activity_at", ""),
                        "last_activity_editor": s.get("last_activity_editor", ""),
                        "last_authenticated_at": s.get("last_authenticated_at", ""),
                        "pending_cancellation_date": s.get("pending_cancellation_date", "") or "",
                        "enterprise_server_user": "false",
                        "status": "active",
                    })

                logger.info("[GithubLicense] Página %d: %d usuários (acumulado: %d)", page, len(seats), len(all_users))
                if len(seats) < per_page:
                    break
                page += 1

            # 2. Coleta usuários de consumed-licenses (Enterprise / VS) e consolida
            try:
                r_lic = client.get(f"{self.base_url}/consumed-licenses", headers=self.headers)
                if r_lic.status_code == 200:
                    lic_users = r_lic.json().get("users", [])
                    for u in lic_users:
                        login = u.get("github_com_login", "")
                        found = next((x for x in all_users if x["username"] == login), None)
                        if found:
                            found["name"] = u.get("github_com_name", "") or found["name"]
                            found["license_type"] = found["license_type"] + ", " + (u.get("license_type") or "Enterprise")
                        else:
                            all_users.append({
                                "username": login,
                                "user_id": "",
                                "user_url": u.get("github_com_profile", ""),
                                "name": u.get("github_com_name", ""),
                                "license_type": u.get("license_type", "Enterprise"),
                                "plan_type": "enterprise",
                                "team_name": "",
                                "team_slug": "",
                                "created_at": u.get("ghe_license_start_date", "") or "",
                                "updated_at": "",
                                "last_activity_at": "",
                                "last_activity_editor": "",
                                "last_authenticated_at": "",
                                "pending_cancellation_date": "",
                                "enterprise_server_user": str(u.get("enterprise_server_user", False)).lower(),
                                "status": "active" if u.get("ghe_license_active") else "inactive",
                            })
            except Exception as e:
                logger.warning("[GithubLicense] Erro ao consultar consumed-licenses: %s", e)

        df = pd.DataFrame(all_users)
        df = df.fillna("")
        logger.info("[GithubLicense] Total final de usuários licenciados: %d", len(df))
        return df

