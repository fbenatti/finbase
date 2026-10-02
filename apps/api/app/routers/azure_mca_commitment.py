"""MCA invoice Cost Details extraction into profile-specific Bronze tables."""
from __future__ import annotations

import asyncio
import io
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import httpx
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.sso_crypto import _decrypt, _encrypt
from app.models.source_connection import SourceConnection, SystemIntegration

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mca-commitment", tags=["azure-mca-commitment"])

BILLING_ACCOUNT = "9c326feb-5307-50e2-1fd1-4de7454d10aa:afbd45cf-3f8c-4df7-b57f-8e12efd1659b_2019-05-31"
ARM = "https://management.azure.com"
API_VERSION = "2020-11-01-privatepreview"
_INTEGRATION_CODE = "azure_mca_commitment"


class CredentialUpdate(BaseModel):
    auth_mode: str = Field(pattern="^(bearer|service_principal|azure_cli)$")
    bearer_token: str | None = None
    tenant_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = None


class ExtractionRequest(BaseModel):
    start_period: str = Field(default="202504", pattern=r"^\d{6}$")
    end_period: str = Field(default_factory=lambda: (pd.Timestamp.now(tz="UTC").to_period("M") - 1).strftime("%Y%m"), pattern=r"^\d{6}$")
    billing_profile_ids: list[str] | None = None


def _month_from_iso(value: Any) -> str | None:
    match = re.match(r"^(\d{4})-(\d{2})", str(value or ""))
    return "".join(match.groups()) if match else None


async def _integration(db: AsyncSession) -> SystemIntegration:
    row = (await db.execute(select(SystemIntegration).where(SystemIntegration.code == _INTEGRATION_CODE))).scalar_one_or_none()
    if row is None:
        row = SystemIntegration(
            code=_INTEGRATION_CODE,
            name="Azure MCA Commitment",
            integration_type="cloud_billing",
            description="Credencial da extração mensal de invoices MCA.",
            enabled=True,
            config_json={"auth_mode": "bearer", "billing_account_id": BILLING_ACCOUNT},
            secret_json={},
        )
        db.add(row)
        await db.flush()
    return row


async def _get_token(db: AsyncSession) -> str:
    integration = await _integration(db)
    cfg, secret = dict(integration.config_json or {}), dict(integration.secret_json or {})
    mode = cfg.get("auth_mode", "bearer")
    if mode == "bearer":
        encrypted = secret.get("bearer_token_enc")
        token = _decrypt(str(encrypted)) if encrypted else ""
        if not token:
            raise HTTPException(400, "Configure o bearer token em /mca-commitment/credentials.")
        return token.removeprefix("Bearer ").strip()
    if mode == "azure_cli":
        tenant_id = str(cfg.get("tenant_id") or "")
        if not tenant_id:
            raise HTTPException(400, "Configure o tenant_id para autenticação Azure CLI.")
        try:
            process = await asyncio.create_subprocess_exec(
                "az", "account", "get-access-token", "--tenant", tenant_id,
                "--resource", "https://management.core.windows.net/", "--output", "json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=60)
            if process.returncode != 0:
                raise RuntimeError("Azure CLI não conseguiu renovar o token.")
            token = str(__import__("json").loads(stdout).get("accessToken") or "")
            if not token:
                raise RuntimeError("Azure CLI não retornou access token.")
            return token
        except Exception as exc:  # noqa: BLE001 - never expose CLI output, which may contain credentials
            logger.warning("Azure MCA CLI token acquisition failed (%s)", type(exc).__name__)
            raise HTTPException(401, "Sessão Azure CLI indisponível. Execute az login no usuário do servidor da API.") from exc
    try:
        from azure.identity import ClientSecretCredential
        credential = ClientSecretCredential(
            tenant_id=str(cfg.get("tenant_id") or ""),
            client_id=str(cfg.get("client_id") or ""),
            client_secret=_decrypt(str(secret.get("client_secret_enc") or "")) if secret.get("client_secret_enc") else "",
        )
        return credential.get_token("https://management.azure.com/.default").token
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Credenciais Service Principal incompletas ou inválidas: {type(exc).__name__}") from exc


async def _headers(db: AsyncSession) -> dict[str, str]:
    return {"Authorization": f"Bearer {await _get_token(db)}", "Content-Type": "application/json"}


async def _get_values(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    while url:
        url = urljoin(ARM, url)
        response = await client.get(url, headers=headers, timeout=90)
        if response.status_code >= 400:
            # Azure response body can contain account data; surface a compact status only.
            raise HTTPException(response.status_code, f"Azure ARM retornou HTTP {response.status_code} ao listar dados MCA.")
        data = response.json()
        values.extend(data.get("value") or [])
        url = data.get("nextLink") or ""
    return values


async def _profiles(client: httpx.AsyncClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    url = f"{ARM}/providers/Microsoft.Billing/billingAccounts/{BILLING_ACCOUNT}/billingProfiles?api-version={API_VERSION}&$top=100&$orderBy=properties/displayName%20asc"
    return await _get_values(client, url, headers)


def _period_bounds(start: str, end: str) -> tuple[str, str]:
    if start > end:
        raise HTTPException(422, "start_period deve ser menor ou igual a end_period.")
    # Invoice lookup uses UTC boundaries inclusive at the start and exclusive after month end.
    first = f"{start[:4]}-{start[4:]}-01T00:00:00Z"
    y, m = int(end[:4]), int(end[4:])
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    after = f"{y:04d}-{m:02d}-01T00:00:00Z"
    return first, after


async def _invoice_is_current(code: str, period: str, invoice_id: str, props: dict[str, Any]) -> bool:
    """Skip a previously loaded invoice; void documents are reconsidered."""
    from app.services.trino_service import execute as trino_execute

    if str(props.get("status") or "").casefold() in {"void", "voided", "cancelled", "canceled"}:
        return False
    table = re.sub(r"[^a-z0-9_]", "_", f"raw_{code.lower()}")
    quote = lambda value: str(value or "").replace("'", "''")
    try:
        rows = await trino_execute(
            f"SELECT COUNT(*) AS n FROM iceberg.bronze.{table} "
            f"WHERE invoiceId = '{quote(invoice_id)}' AND _billing_period = '{quote(period)}'",
            timeout_s=25,
        )
    except Exception:  # noqa: BLE001 - absent table means first load
        return False
    row = rows[0] if rows else {}
    if int(row.get("n") or 0) == 0:
        return False
    return int(row.get("n") or 0) > 0


async def _invoice_details(client: httpx.AsyncClient, headers: dict[str, str], profile_id: str, invoice_id: str) -> pd.DataFrame:
    url = f"{ARM}/providers/Microsoft.Billing/billingAccounts/{BILLING_ACCOUNT}/billingProfiles/{profile_id}/providers/Microsoft.CostManagement/generateCostDetailsReport?api-version=2025-03-01"
    response = None
    for attempt in range(6):
        response = await client.post(url, headers=headers, json={"invoiceId": invoice_id, "metric": "ActualCost"}, timeout=90)
        if response.status_code not in (429, 503):
            break
        retry_after = response.headers.get("Retry-After")
        delay = int(retry_after) if retry_after and retry_after.isdigit() else min(30 * (attempt + 1), 180)
        logger.info("Azure Cost Details rate limited; retrying invoice=%s after %ss", invoice_id, delay)
        await asyncio.sleep(delay)
    assert response is not None
    if response.status_code not in (200, 202):
        raise RuntimeError(f"Cost Details HTTP {response.status_code} para invoice {invoice_id}")
    data = response.json() if response.content else {}
    location = response.headers.get("Location") or data.get("manifestUrl") or data.get("location")
    retry = max(1, int(response.headers.get("Retry-After", "5")))
    for _ in range(30):
        status = str(data.get("status") or data.get("properties", {}).get("status") or "").lower()
        if data.get("manifest") or status in {"completed", "succeeded", "success"}:
            break
        if status in {"failed", "error", "canceled", "cancelled"}:
            raise RuntimeError(f"Cost Details status={status} para invoice {invoice_id}")
        if response.status_code == 200 and not location:
            break
        if not location:
            raise RuntimeError(f"Resposta assíncrona sem Location para invoice {invoice_id}")
        await asyncio.sleep(min(retry, 10))
        response = await client.get(location, headers=headers, timeout=90)
        if response.status_code == 200:
            data = response.json()
            status = str(data.get("status") or data.get("properties", {}).get("status") or "").lower()
            if data.get("manifest") or data.get("manifestUrl") or data.get("blobLink") or data.get("downloadUrl") or status in {"completed", "succeeded", "success"}:
                break
        elif response.status_code in (429, 503):
            retry_after = response.headers.get("Retry-After")
            retry = int(retry_after) if retry_after and retry_after.isdigit() else min(retry * 2, 60)
        elif response.status_code not in (202, 204):
            raise RuntimeError(f"Polling Cost Details HTTP {response.status_code} para invoice {invoice_id}")
        retry = max(1, int(response.headers.get("Retry-After", str(retry))))
    else:
        raise TimeoutError(f"Cost Details não concluiu para invoice {invoice_id}")

    manifest = data.get("manifest") or data.get("properties", {}).get("manifest") or {}
    manifest_url = data.get("manifestUrl") or data.get("properties", {}).get("manifestUrl")
    files = data.get("files") or data.get("properties", {}).get("files") or manifest.get("blobs") or manifest.get("files") or []
    if manifest_url:
        # Manifest/blob URLs are SAS-authorized; never forward the ARM bearer to storage.
        manifest_resp = await client.get(manifest_url, timeout=90)
        manifest_resp.raise_for_status()
        manifest = manifest_resp.json()
        files = manifest.get("blobs") or manifest.get("files") or []
    if not files and (data.get("blobLink") or data.get("downloadUrl")):
        files = [{"blobLink": data.get("blobLink") or data.get("downloadUrl")}]
    if not files:
        raise RuntimeError(f"Cost Details sem arquivos para invoice {invoice_id}")
    frames = []
    for entry in files:
        download_url = entry.get("blobLink") or entry.get("downloadUrl") or entry.get("url")
        if not download_url:
            continue
        # The SAS is sufficient for download. Do not send Azure ARM credentials to Blob Storage.
        download = await client.get(download_url, timeout=180)
        download.raise_for_status()
        frames.append(pd.read_csv(io.BytesIO(download.content), dtype=str))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db)):
    integration = await _integration(db)
    source = (await db.execute(select(SourceConnection).where(SourceConnection.code == "contratos-mca"))).scalar_one_or_none()
    if source is None:
        source = SourceConnection(
            code="contratos-mca",
            name="Contratos MCA Brasil",
            connector_type="azure_mca_invoice",
            cloud_name="Azure MCA Brasil",
            cloud_scope="azure",
            config_json={"source_key": "contracts_mca", "billing_account_id": BILLING_ACCOUNT, "collection_enabled": True},
            blob_prefixes=[],
        )
        db.add(source)
    cfg, secret = dict(integration.config_json or {}), dict(integration.secret_json or {})
    await db.commit()
    auth_mode = cfg.get("auth_mode", "bearer")
    credential_configured = auth_mode == "azure_cli" or bool(
        secret.get("bearer_token_enc") if auth_mode == "bearer" else secret.get("client_secret_enc")
    )
    return {"connection_id": source.id, "source_code": "contratos-mca", "auth_mode": auth_mode, "credential_configured": credential_configured, "billing_account_id": cfg.get("billing_account_id", BILLING_ACCOUNT), "bronze_table_pattern": "bronze.raw_contratos_mca_{billingProfileId}"}


@router.put("/credentials")
async def update_credentials(payload: CredentialUpdate, db: AsyncSession = Depends(get_db)):
    integration = await _integration(db)
    config, secret = dict(integration.config_json or {}), dict(integration.secret_json or {})
    config["auth_mode"] = payload.auth_mode
    if payload.auth_mode == "azure_cli":
        config["tenant_id"] = payload.tenant_id or "9b03d892-fe8c-4e05-80f1-32e928a16ba2"
        secret.pop("bearer_token_enc", None)
        secret.pop("client_secret_enc", None)
        secret.pop("bearer_token", None)
        secret.pop("client_secret", None)
    elif payload.auth_mode == "bearer":
        if payload.bearer_token:
            secret["bearer_token_enc"] = _encrypt(payload.bearer_token.strip().removeprefix("Bearer ").strip())
            secret.pop("bearer_token", None)
        if not secret.get("bearer_token_enc"):
            raise HTTPException(422, "Informe bearer_token.")
    else:
        config.update({"tenant_id": payload.tenant_id, "client_id": payload.client_id})
        if payload.client_secret:
            secret["client_secret_enc"] = _encrypt(payload.client_secret)
            secret.pop("client_secret", None)
        if not all(config.get(k) for k in ("tenant_id", "client_id")) or not secret.get("client_secret_enc"):
            raise HTTPException(422, "Informe tenant_id, client_id e client_secret para Service Principal.")
        secret.pop("bearer_token", None)
        secret.pop("bearer_token_enc", None)
    integration.config_json, integration.secret_json = config, secret
    await db.commit()
    return {"auth_mode": payload.auth_mode, "credential_configured": True}


@router.get("/profiles")
async def list_profiles(db: AsyncSession = Depends(get_db)):
    headers = await _headers(db)
    async with httpx.AsyncClient() as client:
        rows = await _profiles(client, headers)
    await db.commit()
    return {"profiles": [{"billingProfileId": row.get("name"), "displayName": row.get("properties", {}).get("displayName"), "billingAccountId": row.get("properties", {}).get("billingAccountId")} for row in rows]}


@router.post("/extract")
async def extract_invoices(payload: ExtractionRequest, db: AsyncSession = Depends(get_db)):
    """Fetch invoice Cost Details into profile-specific Bronze tables."""
    from app.services import iceberg_service

    headers = await _headers(db)
    start, after_end = _period_bounds(payload.start_period, payload.end_period)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        profiles = await _profiles(client, headers)
        if payload.billing_profile_ids:
            wanted = set(payload.billing_profile_ids)
            profiles = [p for p in profiles if p.get("name") in wanted]
            found = {str(p.get("name")) for p in profiles}
            # Billing Profile IDs already present in existing Azure source
            # connections can be valid invoice scopes even when the private
            # preview list endpoint omits them.
            profiles.extend({"name": profile_id, "properties": {}} for profile_id in sorted(wanted - found))
        results: list[dict[str, Any]] = []
        for profile in profiles:
            profile_id = str(profile.get("name") or "")
            safe_profile = re.sub(r"[^A-Za-z0-9_-]", "_", profile_id)
            if not profile_id:
                continue
            invoice_url = (f"{ARM}/providers/Microsoft.Billing/billingAccounts/{BILLING_ACCOUNT}/billingProfiles/{profile_id}/invoices"
                           f"?api-version={API_VERSION}&includeVoid=true&periodStartDate={start}&periodEndDate={after_end}&$orderBy=properties/invoiceDate%20desc")
            invoices = await _get_values(client, invoice_url, headers)
            for invoice in invoices:
                props = invoice.get("properties") or {}
                invoice_id = str(invoice.get("name") or "")
                period = _month_from_iso(props.get("billingPeriodStartDate") or props.get("periodStartDate") or props.get("invoicePeriodStartDate") or props.get("invoiceDate"))
                if not invoice_id or not period or not payload.start_period <= period <= payload.end_period:
                    continue
                try:
                    # Trino folds unquoted identifiers to lowercase; keep
                    # Iceberg's physical name aligned for profile codes.
                    code = f"contratos-mca-{safe_profile}".lower()
                    if await _invoice_is_current(code, period, invoice_id, props):
                        results.append({"billingProfileId": profile_id, "invoiceId": invoice_id, "billingPeriod": period, "rows": 0, "status": "skipped_current"})
                        continue
                    # A full backfill can run longer than one access-token
                    # lifetime. Ask Azure CLI for a fresh token per invoice;
                    # CLI reuses/renews its own refresh-token session.
                    invoice_headers = await _headers(db)
                    df = await _invoice_details(client, invoice_headers, profile_id, invoice_id)
                    if df.empty:
                        results.append({"billingProfileId": profile_id, "invoiceId": invoice_id, "billingPeriod": period, "rows": 0, "status": "empty"})
                        continue
                    df["billingProfileId"] = profile_id
                    df["invoiceId"] = df.get("invoiceId", pd.Series([invoice_id] * len(df))).fillna(invoice_id)
                    for key, value in {
                        "invoiceDate": props.get("invoiceDate"),
                        "billingPeriodStartDate": props.get("billingPeriodStartDate") or props.get("periodStartDate") or props.get("invoicePeriodStartDate"),
                        "billingPeriodEndDate": props.get("billingPeriodEndDate") or props.get("periodEndDate") or props.get("invoicePeriodEndDate"),
                        "invoiceStatus": props.get("status"),
                        "invoiceDocumentType": props.get("documentType"),
                        "invoiceCurrency": props.get("billingCurrency"),
                        "invoiceSubTotal": props.get("subTotal"),
                        "invoiceTax": props.get("taxAmount"),
                        "invoiceTotal": props.get("totalAmount"),
                    }.items():
                        df[key] = value
                    blob = f"mca-invoice/{profile_id}/{period}/{invoice_id}"
                    count = iceberg_service.write_bronze(code, df, period, blob)
                    results.append({"billingProfileId": profile_id, "invoiceId": invoice_id, "billingPeriod": period, "rows": count, "status": "loaded"})
                except Exception as exc:  # noqa: BLE001
                    logger.exception("MCA invoice extraction failed for profile=%s invoice=%s", profile_id, invoice_id)
                    results.append({"billingProfileId": profile_id, "invoiceId": invoice_id, "billingPeriod": period, "status": "error", "error": f"{type(exc).__name__}: {str(exc)[:180]}"})
    await db.commit()
    return {"profiles_seen": len(profiles), "invoices_seen": len(results), "loaded": sum(r["status"] == "loaded" for r in results), "errors": sum(r["status"] == "error" for r in results), "results": results}
