"""Azure benefit (Reserved Instance + Savings Plan) catalog and utilization collector.

Fonte primária: **Microsoft.Billing** (api-version 2024-04-01). Os service
principals usados pelo Cost Export já possuem leitura sobre os billing accounts,
então NÃO é necessário dar permissão extra de Microsoft.Capacity /
Microsoft.BillingBenefits para o catálogo e utilização agregada:

  - EA   → GET /providers/Microsoft.Billing/billingAccounts/{id}/reservations
  - MCA  → GET .../billingAccounts/{mca}/billingProfiles/{prof}/reservations
  - MCA  → GET .../billingAccounts/{mca}/savingsPlans?$filter=billingProfileId eq '...'

Com ``refreshSummary=true`` o payload já traz utilization.aggregates
(1/7/30 dias) embutido — suficiente para o dashboard executivo.

Os dados são gravados no Bronze (tabelas dedicadas, SEM normalização FOCUS por ora):
  - bronze.azure_reservations_catalog
  - bronze.azure_benefit_utilization   (reservada p/ benefitUtilizationSummaries futuro)

Segue o padrão do azure_ptu_service (ClientSecretCredential + httpx + ARM),
com suporte a credential_profile (código de outra conexão Azure) ou config direta.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import pandas as pd
from azure.identity import ClientSecretCredential

from app.services import iceberg_service as iceberg

logger = logging.getLogger(__name__)

ARM = "https://management.azure.com"
BILLING_API = "2024-04-01"           # Microsoft.Billing (catálogo RI + SP)
BILLING_ACCOUNTS_API = "2020-05-01"  # descoberta de billing accounts
COST_API = "2023-11-01"              # Microsoft.CostManagement (utilização futura)
HTTP_TIMEOUT = 60.0
SP_ORDER_CONCURRENCY = 4


# ---------------------------------------------------------------------------
# Config / credentials (mesmo padrão do azure_ptu_service)
# ---------------------------------------------------------------------------
def resolve_config(config: dict[str, Any] | None) -> dict[str, Any]:
    return dict(config or {})


def _credential(config: dict[str, Any]) -> ClientSecretCredential:
    cfg = resolve_config(config)
    tenant = cfg.get("tenant_id") or cfg.get("tenant")
    client = cfg.get("client_id") or cfg.get("application_id")
    secret = cfg.get("client_secret")
    if not tenant or not client or not secret:
        raise ValueError(
            "azure_reservations requer tenant_id, client_id e client_secret "
            "persistidos no banco de dados"
        )
    return ClientSecretCredential(tenant_id=tenant, client_id=client, client_secret=secret)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# ARM discovery via Microsoft.Billing
# ---------------------------------------------------------------------------
async def _get_all(client: httpx.AsyncClient, url: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """GET with pagination (nextLink)."""
    items: list[dict[str, Any]] = []
    query = dict(params)
    while True:
        resp = await client.get(url, params=query)
        resp.raise_for_status()
        payload = resp.json()
        items.extend(payload.get("value") or [])
        next_link = payload.get("nextLink")
        if not next_link:
            break
        query = {"api-version": query.get("api-version")}
        # nextLink já contém a query string; parseamos o $skiptoken quando presente.
        if "$skiptoken" in next_link or "%24skiptoken" in next_link:
            from urllib.parse import parse_qs, urlparse
            parsed = parse_qs(urlparse(next_link).query)
            token = (parsed.get("$skiptoken") or parsed.get("%24skiptoken") or [None])[0]
            if token:
                query["$skiptoken"] = token
    return items


async def _list_billing_accounts(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Descobre billing accounts acessíveis pelo SPN (EA e MCA)."""
    try:
        resp = await client.get(
            f"{ARM}/providers/Microsoft.Billing/billingAccounts",
            params={"api-version": BILLING_ACCOUNTS_API},
        )
        resp.raise_for_status()
        return resp.json().get("value") or []
    except Exception as exc:
        logger.warning("Billing account discovery falhou: %s", exc)
        return []


def _ba_id(ba: dict[str, Any]) -> str:
    return str(ba.get("name") or ba.get("id", "").split("/")[-1])


def _ba_agreement(ba: dict[str, Any]) -> str:
    props = ba.get("properties") or {}
    return str(props.get("agreementType") or props.get("accountStatus") or "")


async def _list_billing_profiles(client: httpx.AsyncClient, billing_account_id: str) -> list[dict[str, Any]]:
    try:
        resp = await client.get(
            f"{ARM}/providers/Microsoft.Billing/billingAccounts/{billing_account_id}/billingProfiles",
            params={"api-version": BILLING_API},
        )
        resp.raise_for_status()
        return resp.json().get("value") or []
    except Exception as exc:
        logger.info("billingProfiles %s falhou: %s", billing_account_id, exc)
        return []


async def _collect_reservations_ea(client: httpx.AsyncClient, ba_id: str) -> list[dict[str, Any]]:
    """RIs de um billing account EA (ou de qualquer conta que exponha /reservations)."""
    return await _get_all(
        client,
        f"{ARM}/providers/Microsoft.Billing/billingAccounts/{ba_id}/reservations",
        {"api-version": BILLING_API, "refreshSummary": "true"},
    )


async def _collect_reservations_mca_profile(
    client: httpx.AsyncClient, mca_id: str, profile_id: str
) -> list[dict[str, Any]]:
    """RIs de um billing profile MCA."""
    return await _get_all(
        client,
        f"{ARM}/providers/Microsoft.Billing/billingAccounts/{mca_id}/billingProfiles/{profile_id}/reservations",
        {"api-version": BILLING_API, "refreshSummary": "true"},
    )


async def _collect_savings_plans_profile(
    client: httpx.AsyncClient, mca_id: str, profile_id: str
) -> list[dict[str, Any]]:
    """Savings plans de um billing profile MCA (filtro por billingProfileId)."""
    filter_expr = (
        f"properties/billingProfileId eq "
        f"'/providers/Microsoft.Billing/billingAccounts/{mca_id}/billingProfiles/{profile_id}'"
    )
    return await _get_all(
        client,
        f"{ARM}/providers/Microsoft.Billing/billingAccounts/{mca_id}/savingsPlans",
        {"api-version": BILLING_API, "$filter": filter_expr, "refreshSummary": "true"},
    )


# ---------------------------------------------------------------------------
# Normalização → DataFrames (Bronze)
# ---------------------------------------------------------------------------
def _util_aggregates(props: dict[str, Any]) -> dict[str, float | None]:
    """Extrai utilization.aggregates (1/7/30 dias) → dict de pcts."""
    util = props.get("utilization") or {}
    out: dict[str, float | None] = {"util_1d": None, "util_7d": None, "util_30d": None}
    for agg in util.get("aggregates") or []:
        if agg.get("valueUnit") != "percentage":
            continue
        grain = int(agg.get("grain") or 0)
        if grain == 1:
            out["util_1d"] = agg.get("value")
        elif grain == 7:
            out["util_7d"] = agg.get("value")
        elif grain == 30:
            out["util_30d"] = agg.get("value")
    return out


_ORDER_RE = re.compile(r"/(?:reservationOrders|savingsPlanOrders)/([^/]+)")


def _order_id_from_resource_id(resource_id: str | None) -> str | None:
    """Extrai o reservationOrderId/savingsPlanOrderId do id ARM completo.

    O FOCUS cost export referencia o benefício pelo ORDER id
    (commitmentdiscountid = .../reservationOrders/{orderId}), enquanto a API
    Microsoft.Billing lista cada reserva com id
    .../reservationOrders/{orderId}/reservations/{reservationId}. Extrair o
    orderId permite o join do catálogo com o custo (saving).
    """
    if not resource_id:
        return None
    m = _ORDER_RE.search(resource_id)
    return m.group(1) if m else None


def _reservation_rows(items: list[dict[str, Any]], *, mca: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for res in items:
        props = res.get("properties") or {}
        sku = res.get("sku") or {}
        util = _util_aggregates(props)
        benefit_id = (res.get("name") or "").split("/")[-1]
        rows.append({
            "benefit_type": "reserved_instance",
            "benefit_id": benefit_id,
            "benefit_order_id": _order_id_from_resource_id(res.get("id")),
            "display_name": props.get("displayName"),
            "sku_name": sku.get("name"),
            "sku_description": props.get("skuDescription"),
            "term": props.get("term"),
            "billing_plan": props.get("billingPlan"),
            "quantity": props.get("quantity"),
            "reserved_resource_type": props.get("reservedResourceType"),
            "applied_scope_type": props.get("appliedScopeType"),
            "benefit_start": props.get("benefitStartTime") or props.get("effectiveDateTime"),
            "benefit_end": props.get("expiryDateTime") or props.get("expiryDate"),
            "purchase_date": props.get("purchaseDateTime") or props.get("purchaseDate"),
            "status": props.get("provisioningState") or props.get("displayProvisioningState"),
            "renew": props.get("renew"),
            "archived": props.get("archived"),
            "billing_scope_id": props.get("billingScopeId"),
            "location": res.get("location"),
            "commitment_amount": None,
            "commitment_currency": None,
            "utilization_trend": (props.get("utilization") or {}).get("trend"),
            **util,
        })
    return rows


def _savings_plan_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for plan in items:
        props = plan.get("properties") or {}
        sku = plan.get("sku") or {}
        util = _util_aggregates(props)
        commitment = props.get("commitment") or {}
        rows.append({
            "benefit_type": "savings_plan",
            "benefit_id": (plan.get("name") or "").split("/")[-1],
            "benefit_order_id": _order_id_from_resource_id(plan.get("id")),
            "display_name": props.get("displayName"),
            "sku_name": sku.get("name"),
            "sku_description": None,
            "term": props.get("term"),
            "billing_plan": props.get("billingPlan"),
            "quantity": None,
            "reserved_resource_type": "Compute",
            "applied_scope_type": props.get("appliedScopeType"),
            "benefit_start": props.get("benefitStartTime") or props.get("effectiveDateTime"),
            "benefit_end": props.get("expiryDateTime") or props.get("expiryDate"),
            "purchase_date": props.get("purchaseDateTime") or props.get("purchaseDate"),
            "status": props.get("provisioningState") or props.get("displayProvisioningState"),
            "renew": props.get("renew"),
            "archived": None,
            "billing_scope_id": props.get("billingScopeId"),
            "location": None,
            "commitment_amount": commitment.get("amount"),
            "commitment_currency": commitment.get("currencyCode"),
            "utilization_trend": (props.get("utilization") or {}).get("trend"),
            **util,
        })
    return rows


# ---------------------------------------------------------------------------
# Advisor + Benefit Recommendations (coletados junto com as reservas)
# ---------------------------------------------------------------------------
ADVISOR_API = "2022-09-01"
BENEFIT_REC_API = "2023-11-01"


async def _list_subscriptions(client: httpx.AsyncClient) -> list[str]:
    """Lista subscriptions acessíveis pelo SPN."""
    resp = await client.get(f"{ARM}/subscriptions", params={"api-version": "2020-01-01"})
    resp.raise_for_status()
    return [str(s.get("subscriptionId")) for s in (resp.json().get("value") or [])]


async def _collect_advisor_recommendations(
    client: httpx.AsyncClient, subscription_id: str
) -> list[dict[str, Any]]:
    """Recomendações do Azure Advisor de uma subscription (todas as categorias).

    O servidor ignora o $filter de categoria; filtramos Cost/HighAvailability
    etc. no cliente (normalização).
    """
    try:
        items = await _get_all(
            client,
            f"{ARM}/subscriptions/{subscription_id}/providers/Microsoft.Advisor/recommendations",
            {"api-version": ADVISOR_API},
        )
        return items
    except Exception as exc:
        logger.warning("Advisor recommendations %s falhou: %s", subscription_id, exc)
        return []


async def _collect_benefit_recommendations(
    client: httpx.AsyncClient, subscription_id: str
) -> list[dict[str, Any]]:
    """Recomendações de benefício (Savings Plan / Reserved Instance) por subscription."""
    try:
        items = await _get_all(
            client,
            f"{ARM}/subscriptions/{subscription_id}/providers/Microsoft.CostManagement/benefitRecommendations",
            {"api-version": BENEFIT_REC_API},
        )
        return items
    except Exception as exc:
        logger.warning("benefitRecommendations %s falhou: %s", subscription_id, exc)
        return []


def _advisor_rows(items: list[dict[str, Any]], subscription_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rec in items:
        props = rec.get("properties") or {}
        category = props.get("category")
        # Interesse: custo e disponibilidade; o Advisor também devolve Security etc.
        rows.append({
            "subscription_id": subscription_id,
            "recommendation_id": rec.get("name"),
            "category": category,
            "impact": props.get("impact"),
            "impacted_field": props.get("impactedField"),
            "impacted_value": props.get("impactedValue"),
            "problem": (props.get("shortDescription") or {}).get("problem"),
            "solution": (props.get("shortDescription") or {}).get("solution"),
            "last_updated": props.get("lastUpdated"),
            "recommendation_type_id": props.get("recommendationTypeId"),
            "extended_properties": props.get("extendedProperties"),
            "resource_id": (props.get("resourceMetadata") or {}).get("resourceId"),
            "resource_region": (props.get("resourceMetadata") or {}).get("resourceRegion"),
        })
    return rows


def _benefit_recommendation_rows(items: list[dict[str, Any]], subscription_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rec in items:
        props = rec.get("properties") or {}
        details = props.get("recommendationDetails") or {}
        rows.append({
            "subscription_id": subscription_id,
            "recommendation_id": rec.get("name"),
            "kind": rec.get("kind"),  # SavingsPlan | Reservation
            "arm_sku_name": props.get("armSkuName"),
            "term": props.get("term"),
            "look_back_period": props.get("lookBackPeriod"),
            "scope": props.get("scope"),
            "commitment_granularity": props.get("commitmentGranularity"),
            "currency_code": props.get("currencyCode"),
            "commitment_amount": details.get("commitmentAmount"),
            "average_utilization_pct": details.get("averageUtilizationPercentage"),
            "coverage_pct": details.get("coveragePercentage"),
            "savings_amount": details.get("savingsAmount"),
            "savings_pct": details.get("savingsPercentage"),
            "cost_without_benefit": props.get("costWithoutBenefit"),
            "total_cost": details.get("totalCost"),
            "benefit_cost": details.get("benefitCost"),
            "overage_cost": details.get("overageCost"),
            "wastage_cost": details.get("wastageCost"),
            "first_consumption_date": props.get("firstConsumptionDate"),
            "last_consumption_date": props.get("lastConsumptionDate"),
        })
    return rows


async def collect_recommendations(config: dict[str, Any]) -> dict[str, Any]:
    """Coleta Advisor (todas categorias) + benefitRecommendations por subscription."""
    token = await asyncio.to_thread(_credential(config).get_token, "https://management.azure.com/.default")
    headers = _headers(token.token)
    async with httpx.AsyncClient(headers=headers, timeout=HTTP_TIMEOUT) as client:
        subscriptions = await _list_subscriptions(client)
        advisor: list[dict[str, Any]] = []
        benefit: list[dict[str, Any]] = []
        sem = asyncio.Semaphore(SP_ORDER_CONCURRENCY)

        async def _fetch(sub: str) -> None:
            async with sem:
                adv = await _collect_advisor_recommendations(client, sub)
                advisor.extend(_advisor_rows(adv, sub))
                br = await _collect_benefit_recommendations(client, sub)
                benefit.extend(_benefit_recommendation_rows(br, sub))

        await asyncio.gather(*[_fetch(sub) for sub in subscriptions])

    advisor_df = pd.DataFrame(advisor) if advisor else pd.DataFrame()
    benefit_df = pd.DataFrame(benefit) if benefit else pd.DataFrame()
    cost_categories = [c for c in ("Cost", "HighAvailability") if c in set(advisor_df["category"])] if not advisor_df.empty else []
    return {
        "advisor": advisor_df,
        "benefit_recommendations": benefit_df,
        "counts": {
            "subscriptions": len(subscriptions),
            "advisor_total": len(advisor),
            "advisor_cost": int((advisor_df["category"] == "Cost").sum()) if not advisor_df.empty else 0,
            "benefit_recommendations": len(benefit),
            "benefit_savings_amount": round(float(benefit_df["savings_amount"].sum()), 2) if not benefit_df.empty else 0.0,
        },
        "categories": cost_categories,
    }



def _tabela_bronze(connection_code: str, base: str) -> str:
    """Nome da tabela Bronze no padrão da conexão.

    `raw_azure_4p_advisor_recommendations`. Guardar várias conexões na mesma
    tabela era desenho legado: sem o código no nome, uma conexão nova
    sobrescreveria o dado da anterior.
    """
    codigo = (connection_code or "azure").replace("-", "_")
    return f"raw_{codigo}_{base}"

def persist_recommendations_bronze(
    connection_id: int,
    connection_code: str,
    advisor_df: pd.DataFrame,
    benefit_df: pd.DataFrame,
    collection_period: str,
    task_id: int | None = None,
) -> dict[str, int]:
    """Grava recomendações no Bronze (tabelas dedicadas, sem FOCUS)."""
    written = {"advisor": 0, "benefit": 0}
    if advisor_df is not None and not advisor_df.empty:
        df = advisor_df.copy()
        df["connection_id"] = connection_id
        df["connection_code"] = connection_code
        df["_collected_at"] = datetime.now(timezone.utc).isoformat()
        written["advisor"] = iceberg.write_bronze_catalog(
            _tabela_bronze(connection_code, "advisor_recommendations"), df, collection_period,
            column_types={
                "subscription_id": "TEXT", "recommendation_id": "TEXT",
                "category": "TEXT", "impact": "TEXT", "impacted_field": "TEXT",
                "impacted_value": "TEXT", "problem": "TEXT", "solution": "TEXT",
                "last_updated": "TEXT", "recommendation_type_id": "TEXT",
                "extended_properties": "TEXT", "resource_id": "TEXT",
                "resource_region": "TEXT", "connection_id": "INTEGER",
                "connection_code": "TEXT", "_collected_at": "TEXT",
            },
        )
    if benefit_df is not None and not benefit_df.empty:
        df = benefit_df.copy()
        df["connection_id"] = connection_id
        df["connection_code"] = connection_code
        df["_collected_at"] = datetime.now(timezone.utc).isoformat()
        written["benefit"] = iceberg.write_bronze_catalog(
            _tabela_bronze(connection_code, "benefit_recommendations"), df, collection_period,
            column_types={
                "subscription_id": "TEXT", "recommendation_id": "TEXT",
                "kind": "TEXT", "arm_sku_name": "TEXT", "term": "TEXT",
                "look_back_period": "TEXT", "scope": "TEXT",
                "commitment_granularity": "TEXT", "currency_code": "TEXT",
                "commitment_amount": "DOUBLE", "average_utilization_pct": "DOUBLE",
                "coverage_pct": "DOUBLE", "savings_amount": "DOUBLE",
                "savings_pct": "DOUBLE", "cost_without_benefit": "DOUBLE",
                "total_cost": "DOUBLE", "benefit_cost": "DOUBLE",
                "overage_cost": "DOUBLE", "wastage_cost": "DOUBLE",
                "first_consumption_date": "TEXT", "last_consumption_date": "TEXT",
                "connection_id": "INTEGER", "connection_code": "TEXT",
                "_collected_at": "TEXT",
            },
        )
    return written


# ---------------------------------------------------------------------------
# Coleção completa (async) → payload com DataFrames
# ---------------------------------------------------------------------------
async def collect_all(config: dict[str, Any]) -> dict[str, Any]:
    token = await asyncio.to_thread(_credential(config).get_token, "https://management.azure.com/.default")
    headers = _headers(token.token)
    async with httpx.AsyncClient(headers=headers, timeout=HTTP_TIMEOUT) as client:
        billing_accounts = await _list_billing_accounts(client)
        catalog: list[dict[str, Any]] = []
        discovery: list[dict[str, Any]] = []

        for ba in billing_accounts:
            ba_id = _ba_id(ba)
            agreement = _ba_agreement(ba)
            # EA: /reservations direto no billing account
            try:
                ea_res = await _collect_reservations_ea(client, ba_id)
                if ea_res:
                    catalog.extend(_reservation_rows(ea_res))
                    discovery.append({"billing_account": ba_id, "agreement": agreement,
                                      "scope": "billing_account", "reservations": len(ea_res),
                                      "savings_plans": 0})
                    continue
            except Exception as exc:
                logger.info("EA reservations %s falhou (pode ser MCA): %s", ba_id, exc)

            # MCA: billing profiles → reservations + savings plans
            profiles = await _list_billing_profiles(client, ba_id)
            for prof in profiles:
                prof_id = (prof.get("name") or "").split("/")[-1]
                try:
                    res = await _collect_reservations_mca_profile(client, ba_id, prof_id)
                    catalog.extend(_reservation_rows(res, mca=True))
                except Exception as exc:
                    logger.warning("MCA reservations %s/%s falhou: %s", ba_id, prof_id, exc)
                    res = []
                try:
                    sps = await _collect_savings_plans_profile(client, ba_id, prof_id)
                    catalog.extend(_savings_plan_rows(sps))
                except Exception as exc:
                    logger.warning("MCA savings plans %s/%s falhou: %s", ba_id, prof_id, exc)
                    sps = []
                discovery.append({
                    "billing_account": ba_id, "agreement": agreement,
                    "scope": f"billing_profile/{prof_id}",
                    "reservations": len(res), "savings_plans": len(sps),
                })

    catalog_df = pd.DataFrame(catalog) if catalog else pd.DataFrame()
    counts = {
        "billing_accounts": len(billing_accounts),
        "benefits_total": len(catalog),
        "reserved_instances": sum(1 for r in catalog if r["benefit_type"] == "reserved_instance"),
        "savings_plans": sum(1 for r in catalog if r["benefit_type"] == "savings_plan"),
    }
    return {
        "catalog": catalog_df,
        "utilization": pd.DataFrame(),
        "counts": counts,
        "discovery": discovery,
    }


# ---------------------------------------------------------------------------
# Persistência no Bronze
# ---------------------------------------------------------------------------
def _persist_task_progress(session: Any, task_id: int | None, **values: Any) -> None:
    if task_id is None:
        return
    from app.models.etl_task import EtlTask
    task = session.get(EtlTask, task_id)
    if task:
        for key, value in values.items():
            setattr(task, key, value)
        session.commit()


def persist_catalog_bronze(
    connection_id: int,
    connection_code: str,
    catalog_df: pd.DataFrame,
    utilization_df: pd.DataFrame,
    collection_period: str,
    task_id: int | None = None,
) -> dict[str, int]:
    """Grava catálogo e utilização no Bronze (sem normalização FOCUS)."""
    written = {"catalog": 0, "utilization": 0}
    if catalog_df is not None and not catalog_df.empty:
        df = catalog_df.copy()
        df["connection_id"] = connection_id
        df["connection_code"] = connection_code
        df["_collected_at"] = datetime.now(timezone.utc).isoformat()
        written["catalog"] = iceberg.write_bronze_catalog(
            _tabela_bronze(connection_code, "reservations_catalog"), df, collection_period,
            column_types={
                "benefit_type": "TEXT", "benefit_id": "TEXT", "benefit_order_id": "TEXT",
                "display_name": "TEXT", "sku_name": "TEXT", "sku_description": "TEXT",
                "term": "TEXT", "billing_plan": "TEXT", "quantity": "INTEGER",
                "reserved_resource_type": "TEXT", "applied_scope_type": "TEXT",
                "benefit_start": "TEXT", "benefit_end": "TEXT", "purchase_date": "TEXT",
                "status": "TEXT", "renew": "TEXT", "archived": "TEXT",
                "billing_scope_id": "TEXT", "location": "TEXT",
                "commitment_amount": "DOUBLE", "commitment_currency": "TEXT",
                "utilization_trend": "TEXT",
                "util_1d": "DOUBLE", "util_7d": "DOUBLE", "util_30d": "DOUBLE",
                "connection_id": "INTEGER", "connection_code": "TEXT",
                "_collected_at": "TEXT",
            },
        )
    if utilization_df is not None and not utilization_df.empty:
        df = utilization_df.copy()
        df["connection_id"] = connection_id
        df["connection_code"] = connection_code
        df["_collected_at"] = datetime.now(timezone.utc).isoformat()
        written["utilization"] = iceberg.write_bronze_catalog(
            _tabela_bronze(connection_code, "benefit_utilization"), df, collection_period,
            column_types={
                "benefit_id": "TEXT", "benefit_name": "TEXT", "benefit_type": "TEXT",
                "usage_date": "TEXT", "avg_utilization_pct": "DOUBLE",
                "min_utilization_pct": "DOUBLE", "max_utilization_pct": "DOUBLE",
                "connection_id": "INTEGER", "connection_code": "TEXT",
                "_collected_at": "TEXT",
            },
        )
    return written


def run_reservation_collection(
    session: Any,
    connection_id: int,
    *,
    task_id: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Coleta catálogo + utilização de benefícios Azure e grava no Bronze."""
    from app.models.source_connection import SourceConnection

    conn = session.get(SourceConnection, connection_id)
    if not conn:
        raise ValueError(f"connection {connection_id} not found")
    cfg = resolve_config(config if config is not None else dict(conn.config_json or {}))
    connection_code = conn.code

    _persist_task_progress(
        session, task_id,
        phase="Reservas Azure: descoberta ARM (Microsoft.Billing: RI + Savings Plans)",
        current_blob="Descoberta ARM",
    )
    session.rollback()

    payload = asyncio.run(collect_all(cfg))
    catalog_df = payload["catalog"]
    utilization_df = payload["utilization"]
    counts = payload["counts"]

    # Recomendações Advisor + benefitRecommendations são coletadas na MESMA
    # coleta das reservas (config flag collect_reservations habilita ambas).
    _persist_task_progress(
        session, task_id,
        phase="Reservas Azure: coletando recomendações Advisor + benefit recommendations...",
        current_blob="Azure Advisor / benefitRecommendations",
    )
    rec_payload = asyncio.run(collect_recommendations(cfg))
    advisor_df = rec_payload["advisor"]
    benefit_rec_df = rec_payload["benefit_recommendations"]
    rec_counts = rec_payload["counts"]

    collection_period = datetime.now(timezone.utc).strftime("%Y%m")
    _persist_task_progress(
        session, task_id,
        phase=(
            f"Reservas Azure: {counts['reserved_instances']} RI · {counts['savings_plans']} SP · "
            f"{rec_counts['advisor_total']} Advisor · {rec_counts['benefit_recommendations']} benefit rec — persistindo Bronze..."
        )[:200],
        current_blob=f"bronze azure_reservations_catalog · {len(catalog_df)} rows",
    )

    written = persist_catalog_bronze(
        connection_id, connection_code, catalog_df, utilization_df,
        collection_period, task_id=task_id,
    )
    written_recs = persist_recommendations_bronze(
        connection_id, connection_code, advisor_df, benefit_rec_df,
        collection_period, task_id=task_id,
    )

    total = written["catalog"] + written["utilization"] + written_recs["advisor"] + written_recs["benefit"]
    _persist_task_progress(
        session, task_id,
        processed_blobs=1,
        rows_raw=len(catalog_df),
        rows_destination=total,
        phase=f"Reservas Azure concluído: {len(catalog_df)} benefícios · {total} linhas Bronze"[:200],
        current_blob=f"Bronze {total} linhas · catálogo {len(catalog_df)}",
    )
    return {
        "catalog_rows": len(catalog_df),
        "utilization_rows": len(utilization_df),
        "counts": counts,
        "recommendations": rec_counts,
        "discovery": payload.get("discovery", []),
        "persisted": {**written, **written_recs},
        "source": "azure_billing",
        "collection_period": collection_period,
    }


async def test_connection(config: dict[str, Any]) -> dict[str, Any]:
    """Valida credenciais + acesso de leitura aos billing accounts e benefícios."""
    cfg = resolve_config(config)
    try:
        token = await asyncio.to_thread(_credential(cfg).get_token, "https://management.azure.com/.default")
    except Exception as exc:
        return {"status": "ERRO", "message": f"Falha de autenticação: {exc}"}
    headers = _headers(token.token)
    async with httpx.AsyncClient(headers=headers, timeout=HTTP_TIMEOUT) as client:
        try:
            bas = await _list_billing_accounts(client)
        except Exception as exc:
            return {"status": "ERRO", "message": f"Falha ao listar billing accounts: {exc}"}
        if not bas:
            return {"status": "ERRO", "message": "Nenhum billing account acessível pelo SPN"}
        total_ri = 0
        total_sp = 0
        scopes = []
        for ba in bas:
            ba_id = _ba_id(ba)
            agreement = _ba_agreement(ba)
            try:
                res = await _collect_reservations_ea(client, ba_id)
                total_ri += len(res)
                scopes.append(f"{ba_id} (EA): {len(res)} RI")
                continue
            except Exception:
                pass
            profiles = await _list_billing_profiles(client, ba_id)
            for prof in profiles:
                prof_id = (prof.get("name") or "").split("/")[-1]
                try:
                    res = await _collect_reservations_mca_profile(client, ba_id, prof_id)
                    total_ri += len(res)
                except Exception:
                    res = []
                try:
                    sps = await _collect_savings_plans_profile(client, ba_id, prof_id)
                    total_sp += len(sps)
                except Exception:
                    sps = []
                scopes.append(f"{ba_id}/{prof_id} (MCA): {len(res)} RI · {len(sps)} SP")
        return {
            "status": "OK",
            "message": f"{total_ri} reservas · {total_sp} savings plans",
            "reserved_instances": total_ri,
            "savings_plans": total_sp,
            "billing_accounts": len(bas),
            "scopes": scopes,
        }
