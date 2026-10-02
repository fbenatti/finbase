"""MCA invoice-linked monthly totals into a compact Bronze table."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, timedelta
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

ARM = "https://management.azure.com"
API_VERSION = "2020-11-01-privatepreview"
COST_QUERY_API_VERSION = "2021-10-01"
COMMITMENT_BRONZE_CODE = "azure_commitment_br"
_INTEGRATION_CODE = "azure_mca_commitment"
_ZERO_COST_EPSILON = 1e-9


class CredentialUpdate(BaseModel):
    auth_mode: str = Field(pattern="^(bearer|service_principal|azure_cli)$")
    billing_account_ids: list[str] = Field(min_length=1)
    bearer_token: str | None = None
    tenant_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = None


class ExtractionRequest(BaseModel):
    start_period: str = Field(default="202504", pattern=r"^\d{6}$")
    end_period: str = Field(default_factory=lambda: (pd.Timestamp.now(tz="UTC").to_period("M") - 1).strftime("%Y%m"), pattern=r"^\d{6}$")
    billing_profile_ids: list[str] | None = None
    billing_account_ids: list[str] | None = None


def _normalize_account_id(value: str) -> str:
    account_id = value.strip()
    prefix = "/providers/Microsoft.Billing/billingAccounts/"
    if account_id.casefold().startswith(prefix.casefold()):
        account_id = account_id[len(prefix):]
    account_id = account_id.strip("/")
    if not re.fullmatch(r"[A-Za-z0-9-]+:[A-Za-z0-9-]+_\d{4}-\d{2}-\d{2}", account_id):
        raise HTTPException(422, f"Billing Account ID inválido: {value}")
    return account_id


def _configured_accounts(config: dict[str, Any]) -> list[str]:
    # Migrate the previous single-scope setting without baking account IDs into code.
    values = config.get("billing_account_ids") or ([config["billing_account_id"]] if config.get("billing_account_id") else [])
    return list(dict.fromkeys(_normalize_account_id(str(value)) for value in values))


def _profile_name_map(profiles: list[dict[str, Any]]) -> dict[str, str]:
    names: dict[str, str] = {}
    for profile in profiles:
        profile_id = str(profile.get("name") or "").strip()
        display_name = str((profile.get("properties") or {}).get("displayName") or "").strip()
        if profile_id and display_name:
            names[profile_id.casefold()] = display_name
    return names


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
            description="Credencial da consulta mensal consolidada de invoices MCA.",
            enabled=True,
            config_json={"auth_mode": "bearer", "billing_account_ids": []},
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


async def _profiles(client: httpx.AsyncClient, headers: dict[str, str], billing_account_id: str) -> list[dict[str, Any]]:
    url = f"{ARM}/providers/Microsoft.Billing/billingAccounts/{billing_account_id}/billingProfiles?api-version={API_VERSION}&$top=100&$orderBy=properties/displayName%20asc"
    return await _get_values(client, url, headers)


def _query_windows(start_period: str, end_period: str) -> list[tuple[str, str]]:
    """Split history into <=12-calendar-month Query API calls."""
    if start_period > end_period:
        raise HTTPException(422, "start_period deve ser menor ou igual a end_period.")
    first = date(int(start_period[:4]), int(start_period[4:]), 1)
    end = date(int(end_period[:4]), int(end_period[4:]), 1)
    windows: list[tuple[str, str]] = []
    cursor = first
    while cursor <= end:
        end_index = cursor.year * 12 + cursor.month - 1 + 11
        last = date(end_index // 12, end_index % 12 + 1, 1)
        if last > end:
            last = end
        windows.append((cursor.strftime("%Y%m"), last.strftime("%Y%m")))
        next_index = last.year * 12 + last.month
        cursor = date(next_index // 12, next_index % 12 + 1, 1)
    return windows


def _requested_months(start_period: str, end_period: str) -> list[str]:
    if start_period > end_period:
        raise HTTPException(422, "start_period deve ser menor ou igual a end_period.")
    year, month = int(start_period[:4]), int(start_period[4:])
    end_year, end_month = int(end_period[:4]), int(end_period[4:])
    months: list[str] = []
    while (year, month) <= (end_year, end_month):
        months.append(f"{year:04d}{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _query_payload(start_period: str, end_period: str) -> dict[str, Any]:
    year, month = int(end_period[:4]), int(end_period[4:])
    next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    last_day = next_month - timedelta(days=1)
    return {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {
            "from": f"{start_period[:4]}-{start_period[4:]}-01T00:00:00Z",
            "to": f"{last_day.isoformat()}T23:59:59Z",
        },
        "dataset": {
            "granularity": "Monthly",
            "aggregation": {
                "preTaxCostUSD": {"name": "PreTaxCostUSD", "function": "Sum"},
                "preTaxCost": {"name": "PreTaxCost", "function": "Sum"},
            },
            "grouping": [
                {"type": "Dimension", "name": "BillingProfileId"},
                {"type": "Dimension", "name": "InvoiceId"},
            ],
        },
    }


async def _query_invoice_totals(
    client: httpx.AsyncClient,
    db: AsyncSession,
    start_period: str,
    end_period: str,
    billing_account_id: str,
) -> list[dict[str, Any]]:
    """Fetch monthly invoice totals from account scope, following Query nextLink."""
    url = f"{ARM}/providers/Microsoft.Billing/billingAccounts/{billing_account_id}/providers/Microsoft.CostManagement/query?api-version={COST_QUERY_API_VERSION}"
    payload = _query_payload(start_period, end_period)
    results: list[dict[str, Any]] = []
    for _page in range(100):
        response = None
        for attempt in range(6):
            response = await client.post(url, headers=await _headers(db), json=payload, timeout=120)
            if response.status_code not in (429, 503):
                break
            retry_after = response.headers.get("Retry-After")
            delay = int(retry_after) if retry_after and retry_after.isdigit() else min(15 * (attempt + 1), 60)
            logger.info("Cost Management Query rate limited; retrying after %ss", delay)
            await asyncio.sleep(delay)
        assert response is not None
        if response.status_code >= 400:
            raise HTTPException(response.status_code, f"Azure Cost Management Query retornou HTTP {response.status_code}.")
        properties = response.json().get("properties") or {}
        columns = [str(column.get("name")) for column in properties.get("columns") or []]
        for values in properties.get("rows") or []:
            row = dict(zip(columns, values))
            invoice_id = str(row.get("InvoiceId") or "").strip()
            period = _month_from_iso(row.get("BillingMonth"))
            profile_id = str(row.get("BillingProfileId") or "").strip()
            if not invoice_id or not period or not profile_id:
                continue  # Accrued/unbilled rows are outside this invoice reconciliation table.
            try:
                usd_cost = float(row.get("PreTaxCostUSD") or 0)
                billing_cost = float(row.get("PreTaxCost") or 0)
            except (TypeError, ValueError):
                usd_cost = billing_cost = float("nan")
            if abs(usd_cost) <= _ZERO_COST_EPSILON and abs(billing_cost) <= _ZERO_COST_EPSILON:
                continue  # Suppress numeric rounding noise that would make a no-consumption profile appear active.
            results.append({
                "billingAccountId": billing_account_id,
                "billingProfileId": profile_id,
                "invoiceId": invoice_id,
                "billingMonth": period,
                "preTaxCostUSD": row.get("PreTaxCostUSD"),
                "preTaxCost": row.get("PreTaxCost"),
                "billingCurrency": row.get("Currency"),
            })
        next_link = properties.get("nextLink")
        if not next_link:
            return results
        url = urljoin(ARM, str(next_link))
    raise HTTPException(502, "Cost Management Query excedeu 100 páginas.")


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
            config_json={"source_key": "contracts_mca", "collection_enabled": True},
            blob_prefixes=[],
        )
        db.add(source)
    cfg, secret = dict(integration.config_json or {}), dict(integration.secret_json or {})
    await db.commit()
    auth_mode = cfg.get("auth_mode", "bearer")
    credential_configured = auth_mode == "azure_cli" or bool(
        secret.get("bearer_token_enc") if auth_mode == "bearer" else secret.get("client_secret_enc")
    )
    accounts = _configured_accounts(cfg)
    return {"connection_id": source.id, "source_code": "contratos-mca", "auth_mode": auth_mode, "credential_configured": credential_configured, "billing_account_ids": accounts, "bronze_table": "bronze.raw_azure_commitment_br"}


@router.put("/credentials")
async def update_credentials(payload: CredentialUpdate, db: AsyncSession = Depends(get_db)):
    integration = await _integration(db)
    config, secret = dict(integration.config_json or {}), dict(integration.secret_json or {})
    config["auth_mode"] = payload.auth_mode
    config["billing_account_ids"] = list(dict.fromkeys(_normalize_account_id(value) for value in payload.billing_account_ids))
    config.pop("billing_account_id", None)
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
    return {"auth_mode": payload.auth_mode, "credential_configured": True, "billing_account_ids": config["billing_account_ids"]}


@router.get("/profiles")
async def list_profiles(billing_account_id: str | None = None, db: AsyncSession = Depends(get_db)):
    cfg = dict((await _integration(db)).config_json or {})
    accounts = _configured_accounts(cfg)
    if billing_account_id:
        account_id = _normalize_account_id(billing_account_id)
        if account_id not in accounts:
            raise HTTPException(422, "Billing Account não está configurado na fonte MCA.")
        accounts = [account_id]
    if not accounts:
        raise HTTPException(400, "Configure ao menos um Billing Account na fonte MCA.")
    headers = await _headers(db)
    rows = []
    async with httpx.AsyncClient() as client:
        for account_id in accounts:
            rows.extend(await _profiles(client, headers, account_id))
    await db.commit()
    return {"profiles": [{"billingProfileId": row.get("name"), "displayName": row.get("properties", {}).get("displayName"), "billingAccountId": row.get("properties", {}).get("billingAccountId")} for row in rows]}


@router.post("/extract")
async def extract_invoices(payload: ExtractionRequest, db: AsyncSession = Depends(get_db)):
    """Load compact billed monthly totals; never downloads row-level Cost Details."""
    from app.services import iceberg_service
    cfg = dict((await _integration(db)).config_json or {})
    configured_accounts = _configured_accounts(cfg)
    account_ids = list(dict.fromkeys(_normalize_account_id(value) for value in (payload.billing_account_ids or configured_accounts)))
    if not account_ids or any(value not in configured_accounts for value in account_ids):
        raise HTTPException(422, "Selecione Billing Accounts cadastrados na fonte MCA.")
    rows: list[dict[str, Any]] = []
    profile_names: dict[tuple[str, str], str] = {}
    async with httpx.AsyncClient() as client:
        for account_id in account_ids:
            profiles = await _profiles(client, await _headers(db), account_id)
            for profile_id, display_name in _profile_name_map(profiles).items():
                profile_names[(account_id, profile_id)] = display_name
            for window_start, window_end in _query_windows(payload.start_period, payload.end_period):
                rows.extend(await _query_invoice_totals(client, db, window_start, window_end, account_id))
    if payload.billing_profile_ids:
        wanted = {str(value).casefold() for value in payload.billing_profile_ids}
        rows = [row for row in rows if str(row["billingProfileId"]).casefold() in wanted]
    if rows:
        missing_names = sorted({
            (row["billingAccountId"], row["billingProfileId"])
            for row in rows
            if not profile_names.get((row["billingAccountId"], row["billingProfileId"].casefold()))
        })
        if missing_names:
            raise HTTPException(502, f"A API de billing profiles não retornou displayName para {len(missing_names)} profile(s); Bronze não foi alterada.")
        for row in rows:
            row["billingProfileName"] = profile_names[(row["billingAccountId"], row["billingProfileId"].casefold())]
        frame = pd.DataFrame(rows)
        frame["consumedUsd"] = frame["preTaxCostUSD"]
    else:
        frame = pd.DataFrame(columns=[
            "billingProfileId", "billingProfileName", "invoiceId", "billingMonth", "preTaxCostUSD",
            "preTaxCost", "billingCurrency", "billingAccountId", "consumedUsd",
        ])
    written = 0
    for account_id in account_ids:
        account_tag = re.sub(r"[^A-Za-z0-9-]", "_", account_id)
        for period in _requested_months(payload.start_period, payload.end_period):
            group = frame.loc[(frame["billingMonth"] == period) & (frame["billingAccountId"] == account_id)] if not frame.empty else frame
            blob = f"cost-query-invoices/{account_tag}/{period}"
            if group.empty:
                iceberg_service.delete_bronze_slice(COMMITMENT_BRONZE_CODE, blob_path=blob, billing_period=period)
                continue
            written += iceberg_service.write_bronze(
                COMMITMENT_BRONZE_CODE,
                group.drop(columns=["billingMonth"]).reset_index(drop=True),
                period,
                blob,
            )
    await db.commit()
    return {
        "method": "Cost Management Query (invoice-linked monthly aggregate)",
        "metric": "PreTaxCostUSD",
        "billing_currency_metric": "PreTaxCost",
        "billing_account_ids": account_ids,
        "source_code": "contratos-mca",
        "bronze_table": "bronze.raw_azure_commitment_br",
        "months_requested": len(_requested_months(payload.start_period, payload.end_period)),
        "profiles_loaded": len({row["billingProfileId"] for row in rows}),
        "invoice_profile_month_rows": written,
        "invoice_ids": len({row["invoiceId"] for row in rows}),
        "included_unbilled_rows": False,
    }
