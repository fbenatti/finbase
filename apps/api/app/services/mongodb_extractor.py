import logging
from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd
from httpx import DigestAuth
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.source_connection import SourceConnection
from app.models.ingested_blob import IngestedBlob
from app.models.skipped_blob import SkippedBlob
from app.schemas.schemas import BlobScanResult, BlobScanItem

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://cloud.mongodb.com/api/atlas/v1.0"


def _parse_iso_datetime(value: str | None) -> datetime | None:
    """Converte timestamps ISO-8601 da Atlas Admin API para datetime com tz UTC."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


def derive_billing_period_from_invoice(start_date: str | None) -> str | None:
    """Deriva o período de faturamento (YYYYMM) a partir do startDate da invoice."""
    dt = _parse_iso_datetime(start_date)
    if dt:
        return dt.strftime("%Y%m")
    return None


def _sanitize_df(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if df[col].dtype in ("float64", "float32"):
            mask = df[col].isna()
            if mask.any():
                df.loc[mask, col] = None
                df[col] = df[col].astype(object)
    return df


class MongoAtlasExtractor:
    """Extrator de invoices de billing da MongoDB Atlas Admin API.

    O billing do Atlas é faturado no nível da **organização** (API v2):
    o scan lista as invoices via
    ``GET {billing_base_url}/orgs/{org_id}/invoices`` e a ingestão busca os
    line items de uma invoice via
    ``GET {billing_base_url}/orgs/{org_id}/invoices/{invoice_id}``, achatando o
    JSON para um DataFrame pandas (uma linha por line item) gravado na
    camada Bronze do Iceberg.

    O ``org_id`` vem de ``config_json.org_id``; quando ausente, usa-se o
    ``group_id`` (a conexão legada armazena o org id nesse campo).
    """

    ATLAS_V2_ACCEPT = "application/vnd.atlas.2023-01-01+json"

    def __init__(self, connection: SourceConnection):
        self.connection = connection
        self.config_json = connection.config_json or {}
        self.base_url = self.config_json.get("base_url", DEFAULT_BASE_URL).rstrip("/")
        # Invoices só existem na API v2 (org-level); a v1.0 retorna 404/406.
        self.billing_base_url = self.base_url.replace("/v1.0", "/v2")
        self.public_key = self.config_json.get("public_key", "")
        self.private_key = self.config_json.get("private_key", "")
        self.group_id = self.config_json.get("group_id", "")
        self.org_id = self.config_json.get("org_id") or self.group_id

    def _get_auth(self) -> DigestAuth:
        return DigestAuth(username=self.public_key, password=self.private_key)

    def _get(self, client: httpx.Client, path: str, params: dict | None = None,
             billing: bool = False) -> dict:
        base = self.billing_base_url if billing else self.base_url
        headers = {"Accept": self.ATLAS_V2_ACCEPT} if billing else None
        resp = client.get(f"{base}{path}", params=params, headers=headers)
        if resp.status_code != 200:
            detail = ""
            try:
                detail = resp.json().get("detail", "")
            except Exception:
                pass
            raise ValueError(
                f"MongoDB Atlas retornou HTTP {resp.status_code} em {path}: {detail}"[:500]
            )
        return resp.json()

    def _get_paginated(self, client: httpx.Client, path: str,
                       billing: bool = False) -> list[dict]:
        """Percorre todos os resultados de um endpoint paginado da Atlas API."""
        results: list[dict] = []
        page_num = 1
        while True:
            payload = self._get(
                client, path,
                params={"itemsPerPage": 500, "pageNum": page_num},
                billing=billing,
            )
            results.extend(payload.get("results", []))
            links = payload.get("links", [])
            if not any(link.get("rel") == "next" for link in links):
                break
            page_num += 1
        return results

    def test_connection(self) -> dict:
        """Testa conexão com a Atlas Admin API usando Digest Auth."""
        if not self.public_key or not self.private_key:
            return {
                "status": "PENDING",
                "message": f"MongoDB Atlas ({self.config_json.get('contract_type','?')}) — chaves de API nao configuradas.",
            }
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(self.base_url, auth=self._get_auth())
                if resp.status_code == 200:
                    return {
                        "status": "OK",
                        "message": f"MongoDB Atlas ({self.config_json.get('contract_type','?')}) — API respondendo OK",
                    }
                detail = ""
                try:
                    detail = resp.json().get("detail", "")
                except Exception:
                    pass
                return {
                    "status": "FAILED",
                    "message": f"MongoDB Atlas retornou HTTP {resp.status_code}: {detail}"[:500],
                }
        except Exception as e:
            logger.error("Erro ao testar conexao MongoDB Atlas: %s", e)
            return {"status": "FAILED", "message": str(e)[:500]}

    def _list_invoices(self) -> list[dict]:
        with httpx.Client(timeout=60, auth=self._get_auth()) as client:
            return self._get_paginated(
                client, f"/orgs/{self.org_id}/invoices", billing=True
            )

    def _build_scan_items(
        self,
        invoices: list[dict],
        existing_blobs: dict[str, IngestedBlob],
        skipped_paths: set[str],
    ) -> list[BlobScanItem]:
        items: list[BlobScanItem] = []
        for invoice in invoices:
            invoice_id = str(invoice.get("id", ""))
            if not invoice_id or invoice_id in skipped_paths:
                continue

            start_date = invoice.get("startDate")
            end_date = invoice.get("endDate")
            billing_period = derive_billing_period_from_invoice(start_date)
            last_modified = (
                _parse_iso_datetime(end_date)
                or _parse_iso_datetime(start_date)
                or datetime.now(timezone.utc)
            )
            total_price_cents = invoice.get("totalPriceCents")

            existing = existing_blobs.get(invoice_id)
            if existing is None:
                status = "NEW"
            elif existing.last_modified and last_modified > existing.last_modified.replace(tzinfo=timezone.utc):
                status = "CHANGED"
            elif existing.content_length is not None and total_price_cents is not None \
                    and int(total_price_cents) != existing.content_length:
                # Invoices pendentes podem ter o total atualizado sem mudança de data
                status = "CHANGED"
            else:
                status = "UNCHANGED"

            items.append(BlobScanItem(
                blob_path=invoice_id,
                blob_prefix="invoices",
                last_modified=last_modified,
                content_length=int(total_price_cents) if total_price_cents is not None else None,
                status=status,
                billing_period=billing_period,
            ))

        items.sort(key=lambda x: x.billing_period or "", reverse=True)
        return items

    def _scan_sync(self, db_session: Session) -> list[BlobScanItem]:
        """Lista as invoices da Atlas API comparando com IngestedBlob (versão síncrona)."""
        existing_blobs_result = db_session.execute(
            select(IngestedBlob).where(IngestedBlob.connection_id == self.connection.id)
        )
        existing_blobs = {b.blob_path: b for b in existing_blobs_result.scalars().all()}

        skipped_result = db_session.execute(
            select(SkippedBlob.blob_path).where(SkippedBlob.connection_id == self.connection.id)
        )
        skipped_paths = {row[0] for row in skipped_result}

        invoices = self._list_invoices()
        return self._build_scan_items(invoices, existing_blobs, skipped_paths)

    async def scan(self, db: AsyncSession) -> BlobScanResult:
        """Lista as invoices da Atlas API comparando com IngestedBlob (versão assíncrona)."""
        existing_blobs_result = await db.execute(
            select(IngestedBlob).where(IngestedBlob.connection_id == self.connection.id)
        )
        existing_blobs = {b.blob_path: b for b in existing_blobs_result.scalars().all()}

        skipped_result = await db.execute(
            select(SkippedBlob.blob_path).where(SkippedBlob.connection_id == self.connection.id)
        )
        skipped_paths = {row[0] for row in skipped_result}

        invoices = self._list_invoices()
        items = self._build_scan_items(invoices, existing_blobs, skipped_paths)

        stats = {"new": 0, "changed": 0, "unchanged": 0}
        for it in items:
            if it.status == "NEW":
                stats["new"] += 1
            elif it.status == "CHANGED":
                stats["changed"] += 1
            else:
                stats["unchanged"] += 1

        return BlobScanResult(
            connection_id=self.connection.id,
            total=len(items),
            new=stats["new"],
            changed=stats["changed"],
            unchanged=stats["unchanged"],
            items=items,
        )

    def fetch_invoice(self, invoice_id: str) -> tuple[pd.DataFrame, str | None, dict]:
        """Busca a exportação CSV completa de uma invoice MongoDB Atlas e retorna como DataFrame.

        Baixa o relatório CSV oficial via GET /orgs/{org_id}/invoices/{invoice_id}/csv,
        preservando todas as colunas de origem (Date, Usage Date, Description, SKU, Region,
        Cluster, Application, All Projects, licencas, Tags, etc.) e grava na camada Bronze.
        """
        from io import StringIO

        invoice_meta: dict[str, Any] = {}
        start_date: str | None = None
        end_date: str | None = None

        with httpx.Client(timeout=300, auth=self._get_auth()) as client:
            try:
                invoice_meta = self._get(
                    client, f"/orgs/{self.org_id}/invoices/{invoice_id}", billing=True
                )
                start_date = invoice_meta.get("startDate")
                end_date = invoice_meta.get("endDate")
            except Exception as e:
                logger.warning("Falha ao obter metadados JSON da invoice %s: %s", invoice_id, e)

            # Tenta a exportação CSV oficial que contém todas as colunas e All Projects
            csv_url = f"{self.base_url}/orgs/{self.org_id}/invoices/{invoice_id}/csv"
            df = pd.DataFrame()
            try:
                csv_resp = client.get(csv_url)
                if csv_resp.status_code == 200:
                    raw_text = csv_resp.content.decode("utf-8")
                    lines = raw_text.splitlines()
                    header_idx = 0
                    for i, l in enumerate(lines[:10]):
                        if "Usage Date" in l or "Description" in l or "Project" in l:
                            header_idx = i
                            break
                    data_lines = lines[header_idx:] if len(lines) > header_idx else lines
                    if data_lines:
                        df = pd.read_csv(StringIO("\n".join(data_lines)))
                        logger.info("CSV MongoDB Atlas %s baixado: %d linhas, %d colunas", invoice_id, len(df), len(df.columns))
            except Exception as exc:
                logger.warning("Falha ao baixar CSV da invoice %s (%s); tentando fallback JSON", invoice_id, exc)

            # Fallback para JSON caso o CSV falhe
            if df.empty and invoice_meta:
                line_items = invoice_meta.get("lineItems") or invoice_meta.get("results") or []
                rows: list[dict] = []
                for item in line_items:
                    flat = {k: v for k, v in item.items() if not isinstance(v, (dict, list))}
                    rows.append(flat)
                df = pd.DataFrame(rows)

        invoice_fields = {
            "invoiceId": invoice_id,
            "startDate": start_date,
            "endDate": end_date,
            "groupId": self.org_id,
        }

        billing_period = derive_billing_period_from_invoice(start_date)

        if not df.empty:
            for col, value in invoice_fields.items():
                if col not in df.columns:
                    df[col] = value
            if self.config_json.get("marketplace"):
                df["marketplace"] = True

            # Converte colunas numéricas de origem para float
            for num_col in ["Amount", "Quantity", "Unit Price", "Discount Percent"]:
                if num_col in df.columns:
                    df[num_col] = pd.to_numeric(df[num_col], errors="coerce").fillna(0.0)

            # Filtra apenas itens de consumo com valor positivo (Amount > 0),
            # exatamente como o script legado realizado_mongodb_v2.py faz,
            # eliminando as linhas sintéticas de crédito abatido que zeravam a soma na Gold.
            if "Amount" in df.columns:
                df = df[df["Amount"] > 0].copy()

        df = _sanitize_df(df)

        logger.info(
            "Invoice MongoDB Atlas %s: %d registros de consumo positivo obtidos (período=%s, colunas=%d)",
            invoice_id, len(df), billing_period, len(df.columns),
        )
        return df, billing_period, invoice_fields
