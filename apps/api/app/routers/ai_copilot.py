"""
Endpoints dedicados para o Dashboard Específico do GitHub Copilot.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from app.database import get_db
from app.routers.ai_dashboards import _ClientCancelScope, _dept_filter, _period_filter, _resolve_scope_path
from app.services.trino_service import TrinoCancelled, execute as trino_execute, wait_until_ready

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-copilot", tags=["ai-copilot"])

CONS_TABLE = "dest_consumo_ia"
LIC_TABLE = "dest_licencas_ia"


@router.get("/preflight")
async def copilot_api_preflight(
    connection_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Check the GitHub billing/metrics capabilities without persisting data.

    The response intentionally reports required permissions rather than token
    material, making missing access actionable for an enterprise admin.
    """
    from app.models.source_connection import SourceConnection
    from sqlalchemy import select

    stmt = select(SourceConnection).where(SourceConnection.connector_type == "github_token")
    if connection_id:
        stmt = stmt.where(SourceConnection.id == connection_id)
    conn = (await db.execute(stmt.order_by(SourceConnection.id))).scalars().first()
    if not conn:
        raise HTTPException(status_code=404, detail="Conexão github_token não encontrada")
    config = conn.config_json or {}
    enterprise = str(config.get("enterprise_slug") or "").strip()
    token = str(config.get("pat_token") or config.get("client_secret") or "").strip()
    if not enterprise or not token:
        return {
            "connection_id": conn.id,
            "status": "not_configured",
            "checks": [],
            "message": "Configure enterprise_slug e PAT antes do preflight.",
        }

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    probe_day = (datetime.now(timezone.utc).date() - timedelta(days=3)).isoformat()
    checks = [
        {
            "name": "copilot_metrics",
            "url": f"/enterprises/{enterprise}/copilot/metrics/reports/users-1-day?day={probe_day}",
            "required_permission": "Enterprise Copilot metrics (read) or manage_billing:copilot/read:enterprise",
            "token_capability": "Enterprise Copilot metrics",
        },
        {
            "name": "ai_credit_billing",
            "url": f"/enterprises/{enterprise}/settings/billing/ai_credit/usage",
            "required_permission": "Enterprise billing / billing manager",
            "token_capability": "Enterprise billing usage",
        },
        {
            "name": "billing_summary",
            "url": f"/enterprises/{enterprise}/settings/billing/usage/summary",
            "required_permission": "Enterprise billing / billing manager",
            "token_capability": "Enterprise billing usage",
        },
    ]
    async with httpx.AsyncClient(base_url="https://api.github.com", timeout=30) as client:
        for check in checks:
            try:
                params = {"day": probe_day} if check["name"] == "copilot_metrics" else {"year": datetime.now(timezone.utc).year, "month": datetime.now(timezone.utc).month}
                response = await client.get(check["url"].split("?")[0], headers=headers, params=params)
                body = response.text[:500]
                payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                check.update({
                    "http_status": response.status_code,
                    "available": response.status_code < 400,
                    "response": body if response.status_code >= 400 else "ok",
                })
                if check["name"] == "ai_credit_billing":
                    serialized = str(payload).lower()
                    check["token_fields"] = [field for field in ("input", "output", "cache_read", "cache_write") if field in serialized]
                    check["token_data_available"] = len(check["token_fields"]) >= 2
                if response.status_code in (401, 403):
                    check["action"] = "Emita uma chave com a permissão indicada e repita o preflight."
                elif response.status_code == 404:
                    check["action"] = "Confirme enterprise_slug, plano Enhanced Billing e disponibilidade do endpoint."
            except Exception as exc:
                check.update({"http_status": None, "available": False, "response": str(exc)[:500]})
    return {
        "connection_id": conn.id,
        "enterprise": enterprise,
        "status": "ok" if all(c.get("available") for c in checks) else "action_required",
        "checks": checks,
        "token_data_available": next((bool(c.get("token_data_available")) for c in checks if c["name"] == "ai_credit_billing"), False),
        "token_data_note": "O KPI de tokens só deve ser habilitado quando o payload incluir input/output/cache; caso contrário a fonte permanece financeira sem telemetria de tokens.",
    }

class CopilotDashboardRequest(BaseModel):
    period: str | None = None
    year: int | None = None
    department_id: int | None = None
    department_path: str = ""

async def _ensure_trino() -> None:
    try:
        await wait_until_ready(timeout_s=20)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Trino está reiniciando: {exc}")

async def _run_query(sql: str, interactive: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        return await trino_execute(sql, **interactive)
    except TrinoCancelled:
        raise
    except Exception as exc:
        logger.exception("Copilot dashboard query failed")
        raise HTTPException(status_code=503, detail=f"Falha na consulta Trino: {exc}")


async def _raw_token_columns(interactive: dict[str, Any]) -> set[str]:
    """Return available columns so deployments can roll forward safely."""
    rows = await _run_query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'bronze' AND table_name = 'raw_github_token'",
        interactive,
    )
    return {str(row.get("column_name") or "").lower() for row in rows}


class CopilotFinancialInsightsRequest(BaseModel):
    period: str | None = None
    year: int | None = None


@router.post("/financial-insights")
async def copilot_financial_insights(
    body: CopilotFinancialInsightsRequest,
    request: Request,
):
    """Return FinOps facts split by GitHub source and Azure SKU."""
    async with _ClientCancelScope(request) as scope:
        await _ensure_trino()
        period_filter = _period_filter(body.period, body.year)
        columns = await _raw_token_columns(scope.trino_kwargs)
        credit_expr = "TRY_CAST(total_chat_turns AS DOUBLE)"
        if "ai_credits" in columns:
            credit_expr = "COALESCE(TRY_CAST(ai_credits AS DOUBLE), TRY_CAST(total_chat_turns AS DOUBLE), 0)"
        source_expr = "'legacy'"
        credit_model_condition = "model = 'copilot_ai_credit'"
        if "source_kind" in columns:
            source_expr = "COALESCE(NULLIF(source_kind, ''), 'legacy')"
            credit_model_condition = "source_kind = 'ai_usage_report' OR model = 'copilot_ai_credit'"
        financial_exprs = {
            "gross": "0.0",
            "discount": "0.0",
            "net": "0.0",
            "input": "0.0",
            "output": "0.0",
            "cache_read": "0.0",
            "cache_write": "0.0",
        }
        for key, col in (("gross", "gross_amount"), ("discount", "discount_amount"), ("net", "net_amount"),
                         ("input", "input_tokens"), ("output", "output_tokens"),
                         ("cache_read", "cache_read_tokens"), ("cache_write", "cache_write_tokens")):
            if col in columns:
                financial_exprs[key] = f"COALESCE(TRY_CAST({col} AS DOUBLE), 0)"
        currency_expr = "''"
        if "billing_currency" in columns:
            currency_expr = "COALESCE(NULLIF(billing_currency, ''), '')"

        github_sql = f"""
        SELECT
          model,
          lower(assignee_login) AS user_login,
          {source_expr} AS source_kind,
          {currency_expr} AS billing_currency,
          SUM(CASE WHEN {credit_model_condition} THEN {credit_expr} ELSE 0 END) AS credits,
          SUM({financial_exprs['gross']}) AS gross_amount,
          SUM({financial_exprs['discount']}) AS discount_amount,
          SUM({financial_exprs['net']}) AS net_amount,
          SUM({financial_exprs['input']}) AS input_tokens,
          SUM({financial_exprs['output']}) AS output_tokens,
          SUM({financial_exprs['cache_read']}) AS cache_read_tokens,
          SUM({financial_exprs['cache_write']}) AS cache_write_tokens,
          SUM(CASE WHEN {credit_model_condition} THEN 0 ELSE COALESCE(TRY_CAST(total_lines_accepted AS DOUBLE), 0) END) AS lines_accepted
        FROM iceberg.bronze.raw_github_token
        WHERE {period_filter} AND assignee_login IS NOT NULL AND assignee_login <> ''
        GROUP BY 1,2,3,4
        ORDER BY credits DESC, net_amount DESC
        """
        azure_sql = f"""
        SELECT
          _billing_period AS period,
          COALESCE(metersubcategory, '') AS meter_subcategory,
          COALESCE(metername, '') AS meter_name,
          COALESCE(productname, '') AS product_name,
          CASE
            WHEN lower(metersubcategory) = 'github copilot'
             AND lower(metername) IN ('business user', 'business for non-ghe customers user', 'enterprise user') THEN 'fixed_license'
            WHEN lower(metersubcategory) = 'github copilot'
             AND lower(metername) IN ('ai credit gac', 'premium request') THEN 'variable_consumption'
            WHEN lower(metersubcategory) IN ('github enterprise (ghe)', 'github advanced security ghe') THEN 'outside_copilot'
            ELSE 'other_github'
          END AS cost_category,
          MIN(date) AS usage_start_date,
          MAX(date) AS usage_end_date,
          SUM(COALESCE(TRY_CAST(quantity AS DOUBLE), 0)) AS quantity,
          SUM(COALESCE(TRY_CAST(costinbillingcurrency AS DOUBLE), 0)) AS cost_brl,
          SUM(COALESCE(TRY_CAST(costinusd AS DOUBLE), 0)) AS cost_usd,
          CASE WHEN SUM(COALESCE(TRY_CAST(quantity AS DOUBLE), 0)) > 0
            THEN SUM(COALESCE(TRY_CAST(costinbillingcurrency AS DOUBLE), 0)) / SUM(COALESCE(TRY_CAST(quantity AS DOUBLE), 0))
            ELSE NULL END AS unit_cost_brl,
          COUNT(*) AS rows
        FROM iceberg.bronze.raw_azure_ti
        WHERE lower(consumedservice) = 'github' AND {period_filter}
        GROUP BY 1,2,3,4,5
        ORDER BY cost_brl DESC
        """
        github_rows, azure_rows = await asyncio.gather(
            _run_query(github_sql, scope.trino_kwargs),
            _run_query(azure_sql, scope.trino_kwargs),
        )
        for row in github_rows:
            token_total = sum(float(row.get(key) or 0) for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"))
            net = float(row.get("net_amount") or 0)
            row["total_tokens"] = token_total
            row["cost_per_million_tokens"] = (net / token_total * 1_000_000) if token_total > 0 else None
            row["adoption_signal"] = "financial_without_token_telemetry" if float(row.get("credits") or 0) > 0 and token_total == 0 else "ok"
        github_alerts = [
            row for row in github_rows
            if row.get("adoption_signal") == "financial_without_token_telemetry"
        ][:25]
        github_total_tokens = sum(float(row.get("total_tokens") or 0) for row in github_rows)
        github_net = sum(float(row.get("net_amount") or 0) for row in github_rows)
        azure_brl = sum(float(row.get("cost_brl") or 0) for row in azure_rows)
        currencies = {str(row.get("billing_currency") or "").upper() for row in github_rows if row.get("net_amount") not in (None, "", 0, "0.0")}
        delta_brl = azure_brl - github_net if currencies == {"BRL"} else None
        return {
            "period": body.period,
            # O dashboard só precisa dos alertas; limitar o payload evita
            # transportar milhares de linhas e degradar a máquina do usuário.
            "github": github_rows[:200],
            "github_alerts": github_alerts,
            "azure": azure_rows,
            "totals": {
                "github_credits": sum(float(row.get("credits") or 0) for row in github_rows),
                "github_net_amount": github_net,
                "github_total_tokens": github_total_tokens,
                "github_cost_per_million_tokens": (github_net / github_total_tokens * 1_000_000) if github_total_tokens > 0 else None,
                "azure_cost_brl": azure_brl,
                "reconciliation_delta_brl": delta_brl,
                "github_net_currency": next(iter(currencies), None),
            },
            "definitions": {
                "total_tokens": "input + output + cache_read + cache_write",
                "cost_per_million_tokens": "net_amount / total_tokens * 1,000,000; null when tokens unavailable",
                "azure_license_meters": ["Business User", "Business for non-GHE customers User", "Enterprise User"],
                "azure_variable_meters": ["Premium Request", "AI Credit GAC"],
                "azure_quantity": "Quantidade faturada no Azure; em meses parciais pode representar user-months proporcionais.",
            },
        }

@router.post("/overview")
async def copilot_overview(body: CopilotDashboardRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Retorna métricas de alto nível sobre adoção e custo para o Copilot."""
    async with _ClientCancelScope(request) as scope:
        await _ensure_trino()
        scope_path = await _resolve_scope_path(db, body.department_id, body.department_path)
        period_filter = _period_filter(body.period, body.year)
        dept_sql = _dept_filter(scope_path)
        overview_token_columns = await _raw_token_columns(scope.trino_kwargs)
        overview_credit_expr = "TRY_CAST(total_chat_turns AS DOUBLE)"
        overview_credit_source_filter = ""
        overview_credit_measure_filter = "AND model = 'copilot_ai_credit'"
        if "ai_credits" in overview_token_columns:
            overview_credit_expr = "COALESCE(TRY_CAST(ai_credits AS DOUBLE), TRY_CAST(total_chat_turns AS DOUBLE), 0)"
        if "source_kind" in overview_token_columns:
            overview_credit_source_filter = "AND source_kind IN ('ai_usage_report', 'billing_report')"
            overview_credit_measure_filter = "AND (source_kind = 'ai_usage_report' OR model = 'copilot_ai_credit')"
        # Consulta de adoção e créditos agregados
        stats_sql = f"""
        SELECT
          SUM(TRY_CAST("LinesAccepted" AS DOUBLE)) AS lines_accepted,
          SUM(TRY_CAST("acceptancescount" AS DOUBLE)) AS chat_turns,
          SUM(TRY_CAST("TotalTokens" AS DOUBLE)) AS total_tokens
        FROM iceberg.gold.{CONS_TABLE}
        WHERE lower(CAST("Provider" AS VARCHAR)) = 'github'
          AND {period_filter} AND {dept_sql}
        """

        # Créditos reais consumidos de IA no bronze
        bronze_credits_sql = f"""
        SELECT SUM({overview_credit_expr}) AS total_credits
        FROM iceberg.bronze.raw_github_token
        WHERE {period_filter} {overview_credit_measure_filter}
          {overview_credit_source_filter}
        """

        # Users and Licenses query (dest_licencas_ia)
        lic_sql = f"""
        SELECT
          COUNT(DISTINCT "UserEmail") AS total_licenses,
          COUNT(DISTINCT "UserEmail") FILTER (WHERE lower(COALESCE(CAST("LicenseStatus" AS VARCHAR), 'active')) NOT IN ('revoked', 'cancelled', 'suspended')) AS active_licenses,
          SUM(COALESCE(TRY_CAST("AllocatedLicenseCost" AS DOUBLE), TRY_CAST("listcost" AS DOUBLE), 100)) AS total_lic_cost,
          COUNT(DISTINCT "UserEmail") FILTER (WHERE TRY_CAST("DaysSinceLastActivity" AS INTEGER) >= 30 AND TRY_CAST("DaysSinceLastActivity" AS INTEGER) < 60) AS inactive_30,
          COUNT(DISTINCT "UserEmail") FILTER (WHERE TRY_CAST("DaysSinceLastActivity" AS INTEGER) >= 60 AND TRY_CAST("DaysSinceLastActivity" AS INTEGER) < 90) AS inactive_60,
          COUNT(DISTINCT "UserEmail") FILTER (WHERE TRY_CAST("DaysSinceLastActivity" AS INTEGER) >= 90) AS inactive_90,
          SUM(39.0 * 5.14) FILTER (WHERE TRY_CAST("DaysSinceLastActivity" AS INTEGER) >= 30) AS potential_savings
        FROM iceberg.gold.{LIC_TABLE}
        WHERE lower(CAST("Provider" AS VARCHAR)) = 'github'
          AND {period_filter} AND {dept_sql}
        """

        # Cloud actual cost from Azure TI (MeterSubCategory = 'GitHub Copilot')
        cloud_sql = f"""
        SELECT
          "_billing_period" AS period,
          SUM(COALESCE(TRY_CAST(CostInBillingCurrency AS DOUBLE), 0)) AS cloud_cost_brl,
          SUM(COALESCE(TRY_CAST(CostInUSD AS DOUBLE), 0)) AS cloud_cost_usd
        FROM iceberg.bronze.raw_azure_ti
        WHERE MeterSubCategory = 'GitHub Copilot'
          AND "_billing_period" <= '{body.period or "202608"}'
        GROUP BY 1
        ORDER BY 1 DESC
        LIMIT 2
        """

        # Utilization and engagement query (agrupando por usuário único)
        util_sql = f"""
        WITH user_usage AS (
            SELECT 
                "UserLogin",
                SUM(COALESCE(TRY_CAST("LinesAccepted" AS DOUBLE), 0) + COALESCE(TRY_CAST("acceptancescount" AS DOUBLE), 0)) AS total_interactions
            FROM iceberg.gold.{CONS_TABLE}
            WHERE lower(CAST("Provider" AS VARCHAR)) = 'github'
              AND {period_filter} AND {dept_sql}
            GROUP BY 1
        )
        SELECT 
            COUNT(*) FILTER (WHERE total_interactions >= 20) AS high_usage,
            COUNT(*) FILTER (WHERE total_interactions > 0 AND total_interactions < 20) AS low_usage,
            COUNT(*) AS active_consumers
        FROM user_usage
        """

        logger.info(f"Executando overview otimizado de GitHub Copilot para periodo: {body.period}")
        adoption_rows, credits_rows, lic_rows, util_rows, cloud_rows = await asyncio.gather(
            _run_query(stats_sql, scope.trino_kwargs),
            _run_query(bronze_credits_sql, scope.trino_kwargs),
            _run_query(lic_sql, scope.trino_kwargs),
            _run_query(util_sql, scope.trino_kwargs),
            _run_query(cloud_sql, scope.trino_kwargs),
        )

        a_row = adoption_rows[0] if adoption_rows else {}
        cr_row = credits_rows[0] if credits_rows else {}
        l_row = lic_rows[0] if lic_rows else {}
        u_row = util_rows[0] if util_rows else {}

        # Mês corrente vs Mês anterior realizado
        curr_cloud = cloud_rows[0] if cloud_rows else {}
        prev_cloud = cloud_rows[1] if len(cloud_rows) > 1 else curr_cloud

        lines_accepted = float(a_row.get("lines_accepted") or 0)
        chat_turns = float(a_row.get("chat_turns") or 0)
        total_tokens = float(a_row.get("total_tokens") or 0)
        total_credits = float(cr_row.get("total_credits") or 0)

        active_licenses = int(l_row.get("active_licenses") or 0)
        total_licenses = int(l_row.get("total_licenses") or 0)
        lic_cost = float(l_row.get("total_lic_cost") or 0)

        # Se o mês atual for parcial (ex: 202608), trazemos o realizado consolidado do mês anterior (202607)
        cloud_cost_brl = float(prev_cloud.get("cloud_cost_brl") or curr_cloud.get("cloud_cost_brl") or 0)
        cloud_cost_usd = float(prev_cloud.get("cloud_cost_usd") or curr_cloud.get("cloud_cost_usd") or 0)
        cost_period_label = str(prev_cloud.get("period") or curr_cloud.get("period") or "")
        cost = cloud_cost_brl if cloud_cost_brl > 0 else lic_cost

        # Enterprise plan includes 3900 credits per user/month
        credits_pool = active_licenses * 3900
        credits_utilization_pct = (total_credits / credits_pool * 100) if credits_pool > 0 else 0.0

        inactive_30 = int(l_row.get("inactive_30") or 0)
        inactive_60 = int(l_row.get("inactive_60") or 0)
        inactive_90 = int(l_row.get("inactive_90") or 0)
        potential_savings = float(l_row.get("potential_savings") or (inactive_30 * 39.0 * 5.14))

        high_usage = int(u_row.get("high_usage") or 0)
        low_usage = int(u_row.get("low_usage") or 0)
        active_consumers = int(u_row.get("active_consumers") or 0)
        zero_usage = max(0, active_licenses - (high_usage + low_usage))

        logger.info(f"Overview Copilot: {active_licenses} licencas ({high_usage} high, {low_usage} low, {zero_usage} zero)")

        logger.info(f"Overview Copilot: {active_licenses} licencas, {total_credits} creditos consumidos de {credits_pool} do pool")

        return {
            "metrics": {
                "lines_accepted": lines_accepted,
                "chat_turns": chat_turns,
                "total_tokens": total_tokens,
                "total_credits": total_credits,
                "credits_pool": credits_pool,
                "credits_utilization_pct": round(credits_utilization_pct, 2),
                "active_licenses": active_licenses,
                "total_licenses": total_licenses,
                "cloud_cost": cost,
                "cloud_cost_usd": cloud_cost_usd,
                "cost_period_label": cost_period_label,
                "license_cost": lic_cost,
                "total_cost": cost,
                "inactive_30": inactive_30,
                "inactive_60": inactive_60,
                "inactive_90": inactive_90,
                "potential_savings": potential_savings,
                "utilization": {
                    "high": high_usage,
                    "low": low_usage,
                    "zero": zero_usage
                }
            }
        }

@router.post("/users")
async def copilot_users(body: CopilotDashboardRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Retorna o detalhamento do Top 100 usuários de Copilot."""
    async with _ClientCancelScope(request) as scope:
        await _ensure_trino()
        scope_path = await _resolve_scope_path(db, body.department_id, body.department_path)
        period_filter = _period_filter(body.period, body.year)
        dept_sql = _dept_filter(scope_path)

        # Get top users by suggestions + acceptances
        sql = f"""
        WITH cons AS (
          SELECT 
            lower(COALESCE(CAST("UserEmail" AS VARCHAR), CAST("UserLogin" AS VARCHAR))) AS email,
            SUM(TRY_CAST("LinesAccepted" AS DOUBLE)) AS lines_accepted,
            SUM(TRY_CAST("acceptancescount" AS DOUBLE)) AS chat_turns,
            SUM(TRY_CAST("TotalTokens" AS DOUBLE)) AS total_tokens,
            SUM(TRY_CAST("CreditsAmount" AS DOUBLE)) AS total_credits,
            MAX(COALESCE(TRY_CAST("AllocatedCost" AS DOUBLE), TRY_CAST("realizado" AS DOUBLE), TRY_CAST("BilledCost" AS DOUBLE), 0)) AS cost,
            MAX("Departamento_Nivel_2") as dept
          FROM iceberg.gold.{CONS_TABLE}
          WHERE lower(CAST("Provider" AS VARCHAR)) = 'github'
            AND {period_filter} AND {dept_sql}
          GROUP BY 1
        )
        SELECT 
          email, 
          COALESCE(lines_accepted, 0) AS lines_accepted, 
          COALESCE(chat_turns, 0) AS chat_turns, 
          COALESCE(total_tokens, 0) AS total_tokens, 
          COALESCE(total_credits, 0) AS total_credits, 
          COALESCE(cost, 0) AS cost,
          COALESCE(dept, '') AS dept
        FROM cons
        ORDER BY (COALESCE(lines_accepted, 0) + COALESCE(chat_turns, 0)) DESC
        LIMIT 100
        """

        rows = await _run_query(sql, scope.trino_kwargs)
        
        # ensure floats
        for r in rows:
            lines_accepted = float(r.get('lines_accepted') or 0)
            chat_turns = float(r.get('chat_turns') or 0)
            tokens = float(r.get('total_tokens') or 0)
            credits = float(r.get('total_credits') or 0)
            cost = float(r.get('cost') or 0)
            
            r['lines_accepted'] = lines_accepted
            r['chat_turns'] = chat_turns
            r['total_tokens'] = tokens
            r['total_credits'] = credits
            r['cost'] = cost
            r['cost_per_1m_tokens'] = (cost / (tokens / 1e6)) if tokens > 0 else 0.0
            r['unit_token_cost'] = (cost / tokens) if tokens > 0 else 0.0

        return {"users": rows}

@router.post("/models")
async def copilot_models(body: CopilotDashboardRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Retorna o detalhamento de adoção e créditos consumidos por modelo (LLM) do Copilot."""
    async with _ClientCancelScope(request) as scope:
        await _ensure_trino()
        
        # Filtro de período direto no bronze
        period_str = str(body.period or "202608")
        year_str = str(body.year) if body.year else ""
        if period_str and len(period_str) == 6:
            p_filter = f"\"_billing_period\" = '{period_str}'"
        elif year_str:
            p_filter = f"\"_billing_period\" LIKE '{year_str}%'"
        else:
            p_filter = "1=1"

        sql = f"""
        WITH raw_models AS (
            SELECT 
                COALESCE(NULLIF(TRIM(model), ''), 'Base (Autocompletion)') AS model_name,
                COUNT(DISTINCT assignee_login) AS active_users,
                SUM(COALESCE(TRY_CAST(total_lines_accepted AS DOUBLE), 0)) AS lines_accepted,
                SUM(COALESCE(TRY_CAST(total_suggestions_count AS DOUBLE), 0)) AS suggestions_count,
                SUM(COALESCE(TRY_CAST(total_acceptances_count AS DOUBLE), 0)) AS acceptances_count
            FROM iceberg.bronze.raw_github_token
            WHERE {p_filter}
              AND model NOT IN ('copilot_ai_credit', 'copilot_for_business', 'ghec_licenses')
            GROUP BY 1
        ),
        total_pool AS (
            SELECT 
                COALESCE(
                    (SELECT SUM(TRY_CAST(total_chat_turns AS DOUBLE)) FROM iceberg.bronze.raw_github_token WHERE model = 'copilot_ai_credit' AND {p_filter}),
                    (SELECT SUM(lines_accepted + acceptances_count) FROM raw_models),
                    0
                ) AS total_ai_credits,
                (SELECT SUM(lines_accepted + acceptances_count) FROM raw_models) AS total_model_interactions
        )
        SELECT 
            m.model_name,
            m.active_users,
            m.lines_accepted,
            m.acceptances_count,
            m.suggestions_count,
            ROUND(
                CASE 
                    WHEN t.total_model_interactions > 0 THEN ((m.lines_accepted + m.acceptances_count) / t.total_model_interactions) * t.total_ai_credits 
                    ELSE (m.lines_accepted + m.acceptances_count) 
                END, 2
            ) AS total_credits,
            ROUND(
                CASE 
                    WHEN t.total_model_interactions > 0 THEN ((m.lines_accepted + m.acceptances_count) / t.total_model_interactions * 100.0) 
                    ELSE 0.0 
                END, 2
            ) AS credit_share_pct
        FROM raw_models m
        CROSS JOIN total_pool t
        WHERE (m.lines_accepted + m.acceptances_count) > 0 OR m.suggestions_count > 0
        ORDER BY total_credits DESC
        """

        logger.info(f"Buscando detalhamento por modelo de IA para o período {period_str}")
        rows = await _run_query(sql, scope.trino_kwargs)
        
        for r in rows:
            r['active_users'] = int(r.get('active_users') or 0)
            r['lines_accepted'] = float(r.get('lines_accepted') or 0)
            r['acceptances_count'] = float(r.get('acceptances_count') or 0)
            r['suggestions_count'] = float(r.get('suggestions_count') or 0)
            r['total_credits'] = float(r.get('total_credits') or 0)
            r['credit_share_pct'] = float(r.get('credit_share_pct') or 0)

        return {"models": rows}

@router.post("/history")
async def copilot_history(body: CopilotDashboardRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Retorna a evolução histórica (mensal) de licenças, custo faturado em nuvem (USD/BRL) e consumo de créditos/tokens."""
    async with _ClientCancelScope(request) as scope:
        await _ensure_trino()
        scope_path = await _resolve_scope_path(db, body.department_id, body.department_path)
        
        # Filtra pelo ano, se informado, senao traz tudo ou ultimos 12 meses.
        year_filter = f"CAST(\"_billing_period\" AS VARCHAR) LIKE '{body.year}%'" if body.year else "1=1"
        dept_sql = _dept_filter(scope_path)

        # 1. Licenças ativas da tabela Gold
        lic_sql = f"""
        SELECT
          CAST("_billing_period" AS VARCHAR) AS period,
          COUNT(DISTINCT "UserLogin") AS active_licenses,
          SUM(COALESCE(TRY_CAST("AllocatedLicenseCost" AS DOUBLE), TRY_CAST("listcost" AS DOUBLE), 100)) AS total_lic_cost
        FROM iceberg.gold.{LIC_TABLE}
        WHERE lower(CAST("Provider" AS VARCHAR)) = 'github'
          AND {year_filter} AND {dept_sql}
        GROUP BY 1
        ORDER BY 1
        """

        # 2. Licenças cumulativas criadas a partir do bronze (raw_github_license)
        bronze_lic_sql = f"""
        SELECT 
          (substr(created_at, 1, 4) || substr(created_at, 6, 2)) AS period,
          COUNT(DISTINCT assignee_login) AS new_licenses
        FROM iceberg.bronze.raw_github_license
        WHERE created_at IS NOT NULL AND length(created_at) >= 7
        GROUP BY 1
        ORDER BY 1
        """

        # 3. Consumo de tokens e interações por período (dest_consumo_ia)
        cons_sql = f"""
        SELECT
          CAST("_billing_period" AS VARCHAR) AS period,
          SUM(TRY_CAST("LinesAccepted" AS DOUBLE)) AS lines_accepted,
          SUM(TRY_CAST("acceptancescount" AS DOUBLE)) AS chat_turns,
          SUM(TRY_CAST("TotalTokens" AS DOUBLE)) AS total_tokens,
          COUNT(DISTINCT "UserLogin") AS active_users
        FROM iceberg.gold.{CONS_TABLE}
        WHERE lower(CAST("Provider" AS VARCHAR)) = 'github'
          AND {year_filter} AND {dept_sql}
        GROUP BY 1
        ORDER BY 1
        """

        # 3.1 Créditos reais consumidos de IA no bronze
        bronze_credits_hist_sql = f"""
        SELECT
          CAST("_billing_period" AS VARCHAR) AS period,
          SUM(TRY_CAST(total_chat_turns AS DOUBLE)) AS total_credits
        FROM iceberg.bronze.raw_github_token
        WHERE model = 'copilot_ai_credit'
          AND {year_filter.replace('"_billing_period"', '"_billing_period"')}
        GROUP BY 1
        ORDER BY 1
        """

        # 4. Faturamento real em nuvem Azure TI (USD e BRL)
        cloud_hist_sql = f"""
        SELECT
          CAST("_billing_period" AS VARCHAR) AS period,
          SUM(COALESCE(TRY_CAST(CostInBillingCurrency AS DOUBLE), 0)) AS cost_brl,
          SUM(COALESCE(TRY_CAST(CostInUSD AS DOUBLE), 0)) AS cost_usd
        FROM iceberg.bronze.raw_azure_ti
        WHERE MeterSubCategory = 'GitHub Copilot'
          AND {year_filter.replace('"_billing_period"', '"_billing_period"')}
        GROUP BY 1
        ORDER BY 1
        """

        logger.info("Executando consultas de histórico consolidado para GitHub Copilot")
        lic_rows = await _run_query(lic_sql, scope.trino_kwargs)
        bronze_lic_rows = await _run_query(bronze_lic_sql, scope.trino_kwargs)
        cons_rows = await _run_query(cons_sql, scope.trino_kwargs)
        credits_hist_rows = await _run_query(bronze_credits_hist_sql, scope.trino_kwargs)
        cloud_rows = await _run_query(cloud_hist_sql, scope.trino_kwargs)

        # Mapa de criação cumulativa de licenças
        created_by_period = {str(r.get("period") or ""): int(r.get("new_licenses") or 0) for r in bronze_lic_rows if r.get("period")}
        all_created_periods = sorted(created_by_period.keys())

        def get_cumulative_licenses(period_str: str) -> int:
            return sum(created_by_period[p] for p in all_created_periods if p <= period_str)

        history_map: dict[str, dict[str, Any]] = {}

        # Preenche com consumo
        for r in cons_rows:
            p = str(r.get("period") or "")
            if p:
                history_map[p] = {
                    "period": p,
                    "active_licenses": int(r.get("active_users") or 0),
                    "total_lic_cost": 0.0,
                    "cost_usd": 0.0,
                    "cost_brl": 0.0,
                    "total_cost": 0.0,
                    "total_tokens": float(r.get("total_tokens") or 0),
                    "total_credits": 0.0,
                    "lines_accepted": float(r.get("lines_accepted") or 0),
                    "chat_turns": float(r.get("chat_turns") or 0),
                    "included_credits_pool": int(r.get("active_users") or 0) * 3900,
                }

        # Preenche com créditos reais em Milhões
        for r in credits_hist_rows:
            p = str(r.get("period") or "")
            if p:
                if p not in history_map:
                    history_map[p] = {
                        "period": p,
                        "active_licenses": 0,
                        "total_lic_cost": 0.0,
                        "cost_usd": 0.0,
                        "cost_brl": 0.0,
                        "total_cost": 0.0,
                        "total_tokens": 0.0,
                        "total_credits": 0.0,
                        "lines_accepted": 0.0,
                        "chat_turns": 0.0,
                        "included_credits_pool": 0,
                    }
                history_map[p]["total_credits"] = float(r.get("total_credits") or 0)

        # Preenche com faturamento da nuvem
        for r in cloud_rows:
            p = str(r.get("period") or "")
            if p:
                if p not in history_map:
                    history_map[p] = {
                        "period": p,
                        "active_licenses": 0,
                        "total_lic_cost": 0.0,
                        "cost_usd": 0.0,
                        "cost_brl": 0.0,
                        "total_cost": 0.0,
                        "total_tokens": 0.0,
                        "total_credits": 0.0,
                        "lines_accepted": 0.0,
                        "chat_turns": 0.0,
                        "included_credits_pool": 0,
                    }
                history_map[p]["cost_usd"] = float(r.get("cost_usd") or 0)
                history_map[p]["cost_brl"] = float(r.get("cost_brl") or 0)
                history_map[p]["total_cost"] = float(r.get("cost_brl") or 0)

        # Preenche com licenças gold se existirem, senão calcula licenças cumulativas
        for r in lic_rows:
            p = str(r.get("period") or "")
            if p:
                if p not in history_map:
                    history_map[p] = {
                        "period": p,
                        "active_licenses": 0,
                        "total_lic_cost": 0.0,
                        "cost_usd": 0.0,
                        "cost_brl": 0.0,
                        "total_cost": 0.0,
                        "total_tokens": 0.0,
                        "total_credits": 0.0,
                        "lines_accepted": 0.0,
                        "chat_turns": 0.0,
                        "included_credits_pool": 0,
                    }
                gold_lic = int(r.get("active_licenses") or 0)
                if gold_lic > 0:
                    history_map[p]["active_licenses"] = gold_lic
                history_map[p]["total_lic_cost"] = float(r.get("total_lic_cost") or 0)

        # Ajusta licenças cumulativas e pool de créditos se active_licenses for 0 ou extrapolado
        for p, data in history_map.items():
            cumul = get_cumulative_licenses(p)
            if data["active_licenses"] == 0 and cumul > 0:
                data["active_licenses"] = cumul
            # Franquia de 3.900 créditos por licença Enterprise
            data["included_credits_pool"] = max(data["active_licenses"], cumul) * 3900
            # Se não teve faturamento cloud para o mês, calcula pelo valor de referência ($39 * 5.14)
            if data["total_cost"] == 0.0 and data["active_licenses"] > 0:
                data["cost_usd"] = data["active_licenses"] * 39.0
                data["cost_brl"] = data["active_licenses"] * 39.0 * 5.14
                data["total_cost"] = data["cost_brl"]

        # Sort and return
        history = [history_map[p] for p in sorted(history_map.keys())]
        logger.info(f"Retornando {len(history)} períodos de histórico para GitHub Copilot")
        return {"history": history}


class CopilotEngagementUsersRequest(BaseModel):
    period: str | None = None
    year: int | None = None
    department_id: int | None = None
    department_path: str = ""
    engagement_filter: str = "all"  # "all" | "high" | "low" | "zero" | "overage" | "inactive_30" | "inactive_60" | "inactive_90"
    search: str = ""
    limit: int = 5000
    offset: int = 0


@router.post("/engagement-users")
async def copilot_engagement_users(
    body: CopilotEngagementUsersRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Retorna a lista completa de usuários com seus respectivos indicadores de engajamento, créditos consumidos e overage deduplicados."""
    async with _ClientCancelScope(request) as scope:
        await _ensure_trino()
        scope_path = await _resolve_scope_path(db, body.department_id, body.department_path)
        period_filter = _period_filter(body.period, body.year)
        dept_sql = _dept_filter(scope_path)
        token_columns = await _raw_token_columns(scope.trino_kwargs)
        credit_expr = "TRY_CAST(total_chat_turns AS DOUBLE)"
        credit_raw_expr = "total_chat_turns"
        credit_source_filter = ""
        credit_measure_filter = "AND model = 'copilot_ai_credit'"
        if "ai_credits" in token_columns:
            credit_expr = "COALESCE(TRY_CAST(ai_credits AS DOUBLE), TRY_CAST(total_chat_turns AS DOUBLE), 0)"
            credit_raw_expr = "COALESCE(ai_credits, total_chat_turns)"
        if "source_kind" in token_columns:
            # Créditos só podem vir do relatório financeiro; métricas de adoção
            # não são uma medida de faturamento.
            credit_source_filter = "AND source_kind IN ('ai_usage_report', 'billing_report')"
            credit_measure_filter = "AND (source_kind = 'ai_usage_report' OR model = 'copilot_ai_credit')"

        sql = f"""
        WITH credit_daily AS (
            SELECT 
                day,
                lower(assignee_login) AS assignee_login,
                model,
                language,
                editor,
                MAX_BY({credit_raw_expr}, _ingested_at) AS credit_value,
                MAX_BY(COALESCE(TRY_CAST(net_amount AS DOUBLE), 0), _ingested_at) AS net_amount,
                MAX_BY(COALESCE(TRY_CAST(discount_amount AS DOUBLE), 0), _ingested_at) AS included_amount
            FROM iceberg.bronze.raw_github_token
            WHERE {period_filter}
              {credit_measure_filter}
              {credit_source_filter}
              AND assignee_login IS NOT NULL AND assignee_login <> ''
            GROUP BY 1,2,3,4,5
        ),
        user_credits AS (
            SELECT 
                assignee_login,
                SUM(COALESCE(TRY_CAST(credit_value AS DOUBLE), 0)) AS ai_credits,
                SUM(COALESCE(net_amount, 0)) AS net_amount,
                SUM(COALESCE(included_amount, 0)) AS included_amount
            FROM credit_daily
            GROUP BY 1
        ),
        credit_bounds AS (
            SELECT MIN(day) AS credit_period_start, MAX(day) AS credit_period_end,
                   COUNT(DISTINCT day) AS credit_period_days
            FROM credit_daily
        ),
        lic AS (
            SELECT 
                lower(COALESCE(CAST("UserEmail" AS VARCHAR), '')) AS email,
                lower(COALESCE(CAST("UserLogin" AS VARCHAR), '')) AS user_login,
                COALESCE(CAST("UserName" AS VARCHAR), '') AS user_name,
                lower(COALESCE(CAST("LicenseSkuName" AS VARCHAR), '')) AS license_sku,
                COALESCE(CAST("Departamento_Nivel_2" AS VARCHAR), CAST("departamento" AS VARCHAR), 'Não Informado') AS dept,
                COALESCE(TRY_CAST("DaysSinceLastActivity" AS INTEGER), -1) AS days_inactive,
                CAST("LastActivityAt" AS VARCHAR) AS last_activity
            FROM iceberg.gold.{LIC_TABLE}
            WHERE lower(CAST("Provider" AS VARCHAR)) = 'github'
              AND "_billing_period" = (
                  SELECT COALESCE(
                      MAX("_billing_period") FILTER (WHERE "_billing_period" = '{body.period or ''}'),
                      MAX("_billing_period")
                  )
                  FROM iceberg.gold.{LIC_TABLE}
                  WHERE lower(CAST("Provider" AS VARCHAR)) = 'github'
              )
              AND {dept_sql}
              AND lower(COALESCE(CAST("LicenseStatus" AS VARCHAR), 'active')) NOT IN ('revoked', 'cancelled', 'suspended')
        ),
        cons AS (
            SELECT 
                lower(COALESCE(CAST("UserLogin" AS VARCHAR), CAST("UserEmail" AS VARCHAR), '')) AS user_login,
                SUM(COALESCE(TRY_CAST("LinesAccepted" AS DOUBLE), 0)) AS lines_accepted,
                SUM(COALESCE(TRY_CAST("acceptancescount" AS DOUBLE), 0)) AS chat_turns,
                SUM(COALESCE(TRY_CAST("TotalTokens" AS DOUBLE), 0)) AS total_tokens
            FROM iceberg.gold.{CONS_TABLE}
            WHERE lower(CAST("Provider" AS VARCHAR)) = 'github'
              AND {period_filter} AND {dept_sql}
            GROUP BY 1
        ),
        all_people AS (
            SELECT user_login, email, user_name, license_sku, dept, days_inactive, last_activity
            FROM lic
            UNION ALL
            -- Usuários que aparecem no billing, mas ainda não estão no
            -- snapshot de licenças, também precisam aparecer na relação.
            SELECT uc.assignee_login, uc.assignee_login, '', '', 'Não Informado', -1, ''
            FROM user_credits uc
            WHERE NOT EXISTS (
                SELECT 1 FROM lic l
                WHERE l.user_login = uc.assignee_login OR l.email = uc.assignee_login
            )
        ),
        merged AS (
            SELECT 
                people.user_login,
                people.email,
                people.user_name,
                people.license_sku,
                people.dept,
                people.days_inactive,
                people.last_activity,
                COALESCE(c.lines_accepted, 0) AS lines_accepted,
                COALESCE(c.chat_turns, 0) AS chat_turns,
                COALESCE(c.total_tokens, 0) AS total_tokens,
                (COALESCE(c.lines_accepted, 0) + COALESCE(c.chat_turns, 0)) AS total_interactions,
                -- Créditos financeiros são independentes da telemetria de adoção.
                -- Não zerar usuários sem linhas/chats: isso escondia consumo real
                -- e fazia a soma da tabela não reconciliar com o total mensal.
                COALESCE(uc.ai_credits, 0) AS total_credits,
                COALESCE(uc.net_amount, 0) AS net_amount,
                COALESCE(uc.included_amount, 0) AS included_amount,
                CASE
                    WHEN people.license_sku LIKE '%business%' THEN 1900.0
                    WHEN people.license_sku LIKE '%enterprise%' THEN 3900.0
                    ELSE NULL
                END AS individual_credit_allowance,
                CASE 
                    WHEN (COALESCE(c.lines_accepted, 0) + COALESCE(c.chat_turns, 0)) >= 20 THEN 'high'
                    WHEN (COALESCE(c.lines_accepted, 0) + COALESCE(c.chat_turns, 0)) > 0 THEN 'low'
                    WHEN COALESCE(uc.ai_credits, 0) > 0 THEN 'financial_only'
                    ELSE 'zero'
                END AS engagement_level
            FROM all_people people
            LEFT JOIN cons c ON (
                (people.user_login <> '' AND people.user_login = c.user_login) OR 
                (people.email <> '' AND people.email = c.user_login)
            )
            LEFT JOIN user_credits uc ON (
                (people.user_login <> '' AND people.user_login = uc.assignee_login) OR
                (people.email <> '' AND people.email = uc.assignee_login)
            )
        )
        SELECT m.*, cb.credit_period_start, cb.credit_period_end, cb.credit_period_days
        FROM merged m
        CROSS JOIN credit_bounds cb
        ORDER BY total_interactions DESC, total_credits DESC, days_inactive ASC
        """

        logger.info(
            "Consultando engagement-users Copilot (period=%s, filter=%s, search=%s)",
            body.period, body.engagement_filter, body.search,
        )
        rows = await _run_query(sql, scope.trino_kwargs)

        # Processar métricas e aplicar filtros em memória
        processed: list[dict[str, Any]] = []
        credit_period_start = rows[0].get("credit_period_start") if rows else None
        credit_period_end = rows[0].get("credit_period_end") if rows else None
        credit_period_days = int(rows[0].get("credit_period_days") or 0) if rows else 0
        total_pool_credits = sum(float(row.get("total_credits") or 0) for row in rows)
        pool_accounts = len(rows)
        average_credits_per_account = total_pool_credits / pool_accounts if pool_accounts else 0.0
        contractual_standard_pool = sum(
            float(row.get("individual_credit_allowance") or 0) for row in rows
        )
        covered_consumption_above_standard = max(0.0, total_pool_credits - contractual_standard_pool)
        counts = {
            "all": len(rows),
            "high": 0,
            "low": 0,
            "zero": 0,
            "financial_only": 0,
            "overage": 0,
            "inactive_30": 0,
            "inactive_60": 0,
            "inactive_90": 0,
        }

        search_term = body.search.strip().lower() if body.search else ""

        for r in rows:
            eng = str(r.get("engagement_level") or "zero")
            days = int(r.get("days_inactive") or -1)
            credits = float(r.get("total_credits") or 0)
            individual_allowance = r.get("individual_credit_allowance")
            individual_allowance = float(individual_allowance) if individual_allowance is not None else None

            if eng == "high":
                counts["high"] += 1
            elif eng == "low":
                counts["low"] += 1
            elif eng == "financial_only":
                counts["financial_only"] += 1
            else:
                counts["zero"] += 1

            if 30 <= days < 60:
                counts["inactive_30"] += 1
            elif 60 <= days < 90:
                counts["inactive_60"] += 1
            elif days >= 90:
                counts["inactive_90"] += 1

            # Filtro por tipo de engajamento / inatividade / overage
            if body.engagement_filter == "high" and eng != "high":
                continue
            if body.engagement_filter == "low" and eng != "low":
                continue
            if body.engagement_filter == "zero" and eng != "zero":
                continue
            if body.engagement_filter == "financial_only" and eng != "financial_only":
                continue
            if body.engagement_filter == "inactive_30" and not (30 <= days < 60):
                continue
            if body.engagement_filter == "inactive_60" and not (60 <= days < 90):
                continue
            if body.engagement_filter == "inactive_90" and not (days >= 90):
                continue

            # Filtro de busca textual
            if search_term:
                email = str(r.get("email") or "").lower()
                login = str(r.get("user_login") or "").lower()
                name = str(r.get("user_name") or "").lower()
                dept = str(r.get("dept") or "").lower()
                if not (search_term in email or search_term in login or search_term in name or search_term in dept):
                    continue

            processed.append({
                "user_login": r.get("user_login") or "",
                "email": r.get("email") or r.get("user_login") or "",
                "user_name": r.get("user_name") or "",
                "dept": r.get("dept") or "Não Informado",
                "days_inactive": days,
                "last_activity": r.get("last_activity") or "",
                "lines_accepted": float(r.get("lines_accepted") or 0),
                "chat_turns": float(r.get("chat_turns") or 0),
                "total_tokens": float(r.get("total_tokens") or 0),
                "total_credits": credits,
                "net_amount": float(r.get("net_amount") or 0),
                "included_amount": float(r.get("included_amount") or 0),
                "individual_credit_allowance": individual_allowance,
                "individual_credit_balance": (
                    individual_allowance - credits if individual_allowance is not None else None
                ),
                "individual_credit_source": "franquia contratual padrão por plano; crédito promocional não confirmado pela fonte",
                "average_credits_per_account": average_credits_per_account,
                "credits_vs_pool_average": credits - average_credits_per_account,
                "total_interactions": float(r.get("total_interactions") or 0),
                "engagement_level": eng,
                "consumption_signal": (
                    "alto_consumo_baixa_telemetria"
                    if credits > 3900 and float(r.get("total_interactions") or 0) < 20
                    else "normal"
                ),
            })

        logger.info(
            "Retornando %d usuários após filtros (total antes do filtro: %d)",
            len(processed), len(rows),
        )

        return {
            "users": processed[body.offset : body.offset + body.limit] if body.limit else processed,
            "total": len(processed),
            "counts": counts,
            "credit_period": {
                "start": str(credit_period_start) if credit_period_start else None,
                "end": str(credit_period_end) if credit_period_end else None,
                "days": credit_period_days,
                "basis": "month_to_date",
                "quota_period": "monthly",
                "description": "Soma de ai_credits do relatório financeiro somente no mês/período selecionado; meses anteriores não entram.",
            },
            "pool_benchmark": {
                "accounts": pool_accounts,
                "consumed_credits": total_pool_credits,
                "average_credits_per_account": average_credits_per_account,
                "contractual_standard_pool": contractual_standard_pool,
                "covered_consumption_above_standard": covered_consumption_above_standard,
                "description": "Benchmark estatístico: créditos consumidos no pool no período ÷ contas exibidas. Não representa franquia individual.",
            },
            "metrics_definition": {
                "high": "Engajamento Alto: Total de Interações (Linhas Aceitas + Chats) >= 20 no mês",
                "low": "Engajamento Baixo: Total de Interações entre 1 e 19 no mês",
                "zero": "Sem Engajamento de IA: 0 interações no mês com licença ativa",
                "financial_only": "Consumo financeiro confirmado, sem telemetria de adoção IDE na fonte de métricas.",
                "financial_usage": "Créditos e custo vêm do relatório financeiro; a franquia é compartilhada no pool da entidade de billing, não por usuário.",
                "inactive_30": "Inativo há mais de 30 dias na IDE",
                "inactive_60": "Inativo há mais de 60 dias na IDE",
                "inactive_90": "Inativo há mais de 90 dias na IDE",
            },
        }


class UserModelDetailsRequest(BaseModel):
    user_login: str
    email: str = ""
    period: str | None = None
    year: int | None = None


@router.post("/user-model-details")
async def copilot_user_model_details(
    body: UserModelDetailsRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Retorna o detalhamento completo dos modelos de IA (LLMs), IDE e créditos de um desenvolvedor específico com deduplicação."""
    async with _ClientCancelScope(request) as scope:
        await _ensure_trino()
        period_filter = _period_filter(body.period, body.year)

        login = body.user_login.strip().lower()
        email = body.email.strip().lower()

        user_match_sql = f"(lower(assignee_login) = '{login}'"
        if email and email != login:
            user_match_sql += f" OR lower(assignee_login) = '{email}'"
        user_match_sql += ")"

        sql = f"""
        WITH dedup_token AS (
            SELECT 
                day,
                lower(assignee_login) AS assignee_login,
                model,
                language,
                editor,
                TRY_CAST(COALESCE(ai_credits, total_chat_turns) AS DOUBLE) AS total_chat_turns,
                TRY_CAST(total_lines_accepted AS DOUBLE) AS total_lines_accepted,
                TRY_CAST(total_acceptances_count AS DOUBLE) AS total_acceptances_count,
                ROW_NUMBER() OVER (
                    PARTITION BY day, assignee_login, model, language, editor
                    ORDER BY _ingested_at DESC
                ) AS rn
            FROM iceberg.bronze.raw_github_token
            WHERE {period_filter}
              AND {user_match_sql}
        ),
        user_models AS (
            SELECT 
                COALESCE(NULLIF(TRIM(model), ''), 'Base (Autocompletion)') AS model_name,
                SUM(COALESCE(total_lines_accepted, 0)) AS lines_accepted,
                SUM(COALESCE(total_acceptances_count, 0)) AS acceptances_count,
                MAX(editor) AS primary_editor,
                MAX(language) AS primary_language
            FROM dedup_token
            WHERE rn = 1
              AND model NOT IN ('copilot_ai_credit', 'copilot_for_business', 'ghec_licenses')
            GROUP BY 1
        ),
        totals AS (
            SELECT 
                COALESCE(SUM(lines_accepted + acceptances_count), 0) AS grand_total
            FROM user_models
        ),
        credits AS (
            SELECT 
                COALESCE(SUM(total_chat_turns), 0) AS total_credits,
                MAX(editor) AS billing_editor,
                MAX(language) AS billing_language
            FROM dedup_token
            WHERE rn = 1
              AND model = 'copilot_ai_credit'
        )
        SELECT 
            m.model_name,
            m.lines_accepted,
            m.acceptances_count,
            (m.lines_accepted + m.acceptances_count) AS total_interactions,
            CASE WHEN t.grand_total > 0 THEN ROUND(((m.lines_accepted + m.acceptances_count) / t.grand_total) * 100, 2) ELSE 0.0 END AS share_pct,
            COALESCE(m.primary_editor, c.billing_editor, 'VS Code') AS primary_editor,
            COALESCE(m.primary_language, c.billing_language, 'Geral') AS primary_language,
            c.total_credits,
            c.billing_editor,
            c.billing_language
        FROM user_models m
        CROSS JOIN totals t
        CROSS JOIN credits c
        ORDER BY total_interactions DESC
        """

        logger.info(
            "Consultando detalhamento de modelos deduplicados para usuário %s / %s (period=%s)",
            login, email, body.period,
        )
        rows = await _run_query(sql, scope.trino_kwargs)

        # Se não retornou linhas de modelos, buscar apenas os créditos
        if not rows:
            credit_sql = f"""
            WITH dedup_token AS (
                SELECT 
                    day,
                    lower(assignee_login) AS assignee_login,
                    model,
                    language,
                    editor,
                    TRY_CAST(COALESCE(ai_credits, total_chat_turns) AS DOUBLE) AS total_chat_turns,
                    ROW_NUMBER() OVER (
                        PARTITION BY day, assignee_login, model, language, editor
                        ORDER BY _ingested_at DESC
                    ) AS rn
                FROM iceberg.bronze.raw_github_token
                WHERE {period_filter}
                  AND {user_match_sql}
            )
            SELECT 
                COALESCE(SUM(total_chat_turns), 0) AS total_credits,
                MAX(editor) AS billing_editor,
                MAX(language) AS billing_language
            FROM dedup_token
            WHERE rn = 1 AND model = 'copilot_ai_credit'
            """
            crows = await _run_query(credit_sql, scope.trino_kwargs)
            total_credits = float(crows[0].get("total_credits") or 0) if crows else 0.0
            billing_editor = crows[0].get("billing_editor") if crows else None
            billing_language = crows[0].get("billing_language") if crows else None
        else:
            total_credits = float(rows[0].get("total_credits") or 0)
            billing_editor = rows[0].get("billing_editor")
            billing_language = rows[0].get("billing_language")

        models: list[dict[str, Any]] = []
        tot_lines = 0.0
        tot_chats = 0.0

        for r in rows:
            lines = float(r.get("lines_accepted") or 0)
            chats = float(r.get("acceptances_count") or 0)
            tot_lines += lines
            tot_chats += chats
            models.append({
                "model_name": r.get("model_name") or "Base (Autocompletion)",
                "lines_accepted": lines,
                "acceptances_count": chats,
                "total_interactions": float(r.get("total_interactions") or 0),
                "share_pct": float(r.get("share_pct") or 0),
                "primary_editor": r.get("primary_editor") or "VS Code",
                "primary_language": r.get("primary_language") or "Geral",
            })

        is_billing_account = (tot_lines + tot_chats == 0) and total_credits > 0
        raw_invoice_credits = total_credits

        # A franquia é compartilhada pela entidade de billing. Consumo por
        # usuário é observável, mas não permite inferir um excedente individual.
        operational_credits = total_credits

        return {
            "user_login": body.user_login,
            "email": body.email or body.user_login,
            "period": body.period,
            "is_billing_account": is_billing_account,
            "billing_origin": billing_editor or billing_language or "Fatura Corporativa",
            "contractual_invoice_credits": raw_invoice_credits,
            "summary": {
                "total_credits": operational_credits,
                "quota": None,
                "overage_credits": None,
                "is_overage": False,
                "overage_pct": None,
                "total_lines_accepted": tot_lines,
                "total_chat_acceptances": tot_chats,
                "total_interactions": tot_lines + tot_chats,
                "models_count": len(models),
            },
            "models": models,
        }
