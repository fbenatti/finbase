"""
AI Dashboards — executive, FinOps and user drilldown views over dest_licencas_ia / dest_consumo_ia.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.rules import Department
from app.services.trino_service import TrinoCancelled, execute as trino_execute, wait_until_ready
from app.services.gold_materialization import MART_NAMESPACE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-dashboards", tags=["ai-dashboards"])

LIC_TABLE = "dest_licencas_ia"
CONS_TABLE = "dest_consumo_ia"


class _ClientCancelScope:
    def __init__(self, request: Request, *, timeout_s: float = 90, max_retries: int = 2):
        self.request = request
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.cancelled = asyncio.Event()
        self._watcher: asyncio.Task | None = None

    async def __aenter__(self) -> _ClientCancelScope:
        self._watcher = asyncio.create_task(_watch_disconnect(self.request, self.cancelled))
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.cancelled.set()
        if self._watcher:
            self._watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watcher

    @property
    def trino_kwargs(self) -> dict[str, Any]:
        return {"timeout_s": self.timeout_s, "max_retries": self.max_retries, "cancel_event": self.cancelled}


async def _watch_disconnect(request: Request, cancelled: asyncio.Event) -> None:
    while not cancelled.is_set():
        if await request.is_disconnected():
            cancelled.set()
            return
        await asyncio.sleep(0.05)


def _cancelled_http() -> HTTPException:
    return HTTPException(status_code=499, detail="Consulta cancelada")


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str | int | float | bool) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


MetricKind = Literal["tokens", "cost"]


class AIDashboardBaseRequest(BaseModel):
    period: str | None = None
    year: int | None = None
    department_id: int | None = None
    department_path: str | None = None
    provider: str | None = None
    metric: MetricKind = "tokens"
    inactivity_days: list[int] = Field(default_factory=lambda: [30, 60, 90])
    percentile_low: float = 0.10
    percentile_high: float = 0.95


class AILicensesRequest(AIDashboardBaseRequest):
    limit: int = 100
    offset: int = 0
    reclaim_only: bool = False


class AIConsumptionRequest(AIDashboardBaseRequest):
    limit: int = 50
    outliers_only: bool = False
    zombie_only: bool = False


class AIUserRequest(AIDashboardBaseRequest):
    user_email: str


def _current_period() -> str:
    from datetime import datetime

    now = datetime.now()
    return f"{now.year:04d}{now.month:02d}"


def _period_filter(period: str | None, year: int | None, column: str = "_billing_period") -> str:
    col = _quote_ident(column)
    if period:
        return f"{col} = {_quote_literal(period)}"
    if year:
        return f"{col} >= {_quote_literal(f'{year:04d}01')} AND {col} < {_quote_literal(f'{year + 1:04d}01')}"
    return f"{col} = {_quote_literal(_current_period())}"


async def _resolve_scope_path(
    db: AsyncSession,
    department_id: int | None,
    department_path: str | None,
) -> list[str]:
    if department_path:
        parts = [p.strip() for p in department_path.split(">") if p.strip()]
        if parts:
            return parts

    if not department_id:
        return []

    result = await db.execute(select(Department).where(Department.active == True))  # noqa: E712
    departments = result.scalars().all()
    department_map = {d.id: d for d in departments}

    current = department_map.get(department_id)
    if not current:
        return []

    scope_path: list[str] = []
    seen: set[int] = set()
    while current and current.id not in seen:
        seen.add(current.id)
        scope_path.append((current.name or "").strip())
        current = department_map.get(current.parent_id) if current.parent_id else None
    scope_path.reverse()
    return scope_path


def _dept_filter(scope_path: list[str], alias: str = "") -> str:
    if not scope_path:
        return "1=1"
    prefix = f"{alias}." if alias else ""
    checks: list[str] = []
    for level, name in enumerate(scope_path, start=1):
        col = f"{prefix}{_quote_ident(f'departamento_nivel_{level}')}"
        checks.append(f"TRIM(CAST({col} AS VARCHAR)) = {_quote_literal(name.strip())}")
    return " AND ".join(checks)


def _prov_filter(provider: str | None, alias: str = "") -> str:
    if not provider or provider.strip().lower() in ("todos", "all", ""):
        return "1=1"
    prefix = f"{alias}." if alias else ""
    col = f"{prefix}{_quote_ident('Provider')}"
    return f"lower(TRIM(CAST({col} AS VARCHAR))) = {_quote_literal(provider.strip().lower())}"


def _cons_metric_expr(metric: MetricKind) -> str:
    if metric == "cost":
        return "COALESCE(TRY_CAST(\"AllocatedCost\" AS DOUBLE), TRY_CAST(\"realizado\" AS DOUBLE), TRY_CAST(\"BilledCost\" AS DOUBLE), 0)"
    return """COALESCE(
      NULLIF(TRY_CAST("TotalTokens" AS DOUBLE), 0),
      TRY_CAST("AcceptancesCount" AS DOUBLE),
      TRY_CAST("SuggestionsCount" AS DOUBLE),
      TRY_CAST("ChatTurns" AS DOUBLE),
      TRY_CAST("ConsumedQuantity" AS DOUBLE),
      0
    )"""


def _lic_cost_expr() -> str:
    return "COALESCE(TRY_CAST(\"AllocatedLicenseCost\" AS DOUBLE), 0)"


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
        logger.exception("AI dashboard query failed")
        raise HTTPException(status_code=503, detail=f"Falha na consulta Trino: {exc}")


async def _cost_coverage(period_filter: str, dept_sql: str, interactive: dict[str, Any], prov_sql: str = "1=1") -> dict[str, float]:
    sql = f"""
    WITH cons AS (
      SELECT
        COUNT(*) AS total_rows,
        COUNT(*) FILTER (
          WHERE COALESCE(TRY_CAST("AllocatedCost" AS DOUBLE), TRY_CAST("BilledCost" AS DOUBLE)) IS NOT NULL
            AND COALESCE(TRY_CAST("AllocatedCost" AS DOUBLE), TRY_CAST("BilledCost" AS DOUBLE), 0) > 0
        ) AS with_cost
      FROM iceberg.{MART_NAMESPACE}.{CONS_TABLE}
      WHERE {period_filter} AND {dept_sql} AND {prov_sql}
    ),
    lic AS (
      SELECT
        COUNT(*) AS total_rows,
        COUNT(*) FILTER (
          WHERE COALESCE(TRY_CAST("AllocatedLicenseCost" AS DOUBLE), 0) > 0
        ) AS with_cost
      FROM iceberg.{MART_NAMESPACE}.{LIC_TABLE}
      WHERE {period_filter} AND {dept_sql} AND {prov_sql}
        AND lower(COALESCE(CAST("LicenseStatus" AS VARCHAR), 'active')) NOT IN ('revoked', 'cancelled', 'suspended')
    )
    SELECT
      COALESCE(cons.with_cost * 100.0 / NULLIF(cons.total_rows, 0), 0) AS token_pct,
      COALESCE(lic.with_cost * 100.0 / NULLIF(lic.total_rows, 0), 0) AS license_pct
    FROM cons, lic
    """
    rows = await _run_query(sql, interactive)
    row = rows[0] if rows else {}
    token_pct = float(row.get("token_pct") or 0)
    license_pct = float(row.get("license_pct") or 0)
    overall = (token_pct + license_pct) / 2 if (token_pct or license_pct) else 0
    return {"token": round(token_pct, 2), "license": round(license_pct, 2), "overall": round(overall, 2)}


@router.post("/overview")
async def ai_overview(body: AIDashboardBaseRequest, request: Request, db: AsyncSession = Depends(get_db)):
    async with _ClientCancelScope(request, timeout_s=120) as scope:
        try:
            await _ensure_trino()
            scope_path = await _resolve_scope_path(db, body.department_id, body.department_path)
            period_filter = _period_filter(body.period, body.year)
            dept_sql = _dept_filter(scope_path)
            prov_sql = _prov_filter(body.provider)
            metric_expr = _cons_metric_expr(body.metric)
            interactive = scope.trino_kwargs

            cost_coverage_pct = await _cost_coverage(period_filter, dept_sql, interactive, prov_sql)

            kpi_sql = f"""
            WITH cons AS (
              SELECT
                SUM({_cons_metric_expr(body.metric)}) AS total_usage,
                COUNT(DISTINCT NULLIF(TRIM(CAST("UserEmail" AS VARCHAR)), '')) AS active_users
              FROM iceberg.{MART_NAMESPACE}.{CONS_TABLE}
              WHERE {period_filter} AND {dept_sql} AND {prov_sql}
            ),
            lic AS (
              SELECT
                COUNT(DISTINCT NULLIF(TRIM(CAST("UserEmail" AS VARCHAR)), '')) AS active_licenses,
                COUNT(DISTINCT NULLIF(TRIM(CAST("UserEmail" AS VARCHAR)), '')) FILTER (
                  WHERE lower(COALESCE(CAST("ActivityStatus" AS VARCHAR), '')) IN ('ativo', 'active')
                ) AS healthy_licenses
              FROM iceberg.{MART_NAMESPACE}.{LIC_TABLE}
              WHERE {period_filter} AND {dept_sql} AND {prov_sql}
                AND lower(COALESCE(CAST("LicenseStatus" AS VARCHAR), 'active')) NOT IN ('revoked', 'cancelled')
            ),
            prev AS (
              SELECT SUM({_cons_metric_expr(body.metric)}) AS prev_usage
              FROM iceberg.{MART_NAMESPACE}.{CONS_TABLE}
              WHERE "_billing_period" = CAST(
                date_format(date_add('month', -1, date_parse(
                  (SELECT MAX("_billing_period") FROM iceberg.{MART_NAMESPACE}.{CONS_TABLE} WHERE {dept_sql} AND {prov_sql}),
                  '%Y%m'
                )), '%Y%m') AS VARCHAR)
                AND {dept_sql} AND {prov_sql}
            ),
            waste AS (
              SELECT SUM({_lic_cost_expr()}) AS waste_cost
              FROM iceberg.{MART_NAMESPACE}.{LIC_TABLE}
              WHERE {period_filter} AND {dept_sql} AND {prov_sql}
                AND lower(COALESCE(CAST("ActivityStatus" AS VARCHAR), '')) IN (
                  'nunca_usou', 'inativo_30d', 'inativo_60d', 'inativo_90d'
                )
            )
            SELECT
              COALESCE(cons.total_usage, 0) AS total_usage,
              COALESCE(lic.active_licenses, 0) AS active_licenses,
              CASE WHEN lic.active_licenses > 0
                THEN COALESCE(lic.healthy_licenses, 0) * 1.0 / lic.active_licenses ELSE 0 END AS utilization_rate,
              CASE WHEN cons.active_users > 0
                THEN COALESCE(cons.total_usage, 0) / cons.active_users ELSE 0 END AS usage_per_active_user,
              COALESCE(waste.waste_cost, 0) AS estimated_waste,
              CASE WHEN COALESCE(prev.prev_usage, 0) > 0
                THEN (COALESCE(cons.total_usage, 0) - prev.prev_usage) * 100.0 / prev.prev_usage ELSE 0 END AS mom_change_pct
            FROM cons, lic, prev, waste
            """
            kpi_rows = await _run_query(kpi_sql, interactive)
            kpis = kpi_rows[0] if kpi_rows else {}

            if body.year:
                series_period_filter = (
                    f"\"_billing_period\" >= {_quote_literal(f'{body.year:04d}01')} "
                    f"AND \"_billing_period\" < {_quote_literal(f'{body.year + 1:04d}01')}"
                )
            elif body.period:
                series_period_filter = (
                    f"\"_billing_period\" >= CAST("
                    f"date_format(date_add('month', -11, date_parse({_quote_literal(body.period)}, '%Y%m')), '%Y%m') AS VARCHAR) "
                    f"AND \"_billing_period\" <= {_quote_literal(body.period)}"
                )
            else:
                series_period_filter = period_filter
            series_sql = f"""
            SELECT
              CAST("_billing_period" AS VARCHAR) AS period,
              COALESCE(NULLIF(TRIM(CAST("Provider" AS VARCHAR)), ''), 'unknown') AS provider,
              SUM({metric_expr}) AS value
            FROM iceberg.{MART_NAMESPACE}.{CONS_TABLE}
            WHERE {series_period_filter} AND {dept_sql} AND {prov_sql}
            GROUP BY 1, 2
            ORDER BY 1, 2
            """
            monthly_series = await _run_query(series_sql, interactive)

            dept_level = min(len(scope_path) + 1, 7) if scope_path else 1
            dept_col = _quote_ident(f"departamento_nivel_{dept_level}")
            util_sql = f"""
            SELECT
              COALESCE(NULLIF(TRIM(CAST({dept_col} AS VARCHAR)), ''), 'Sem departamento') AS department,
              COUNT(DISTINCT NULLIF(TRIM(CAST("UserEmail" AS VARCHAR)), '')) AS licenses,
              COUNT(DISTINCT NULLIF(TRIM(CAST("UserEmail" AS VARCHAR)), '')) FILTER (
                WHERE lower(COALESCE(CAST("ActivityStatus" AS VARCHAR), '')) IN ('ativo', 'active')
              ) AS active_users
            FROM iceberg.{MART_NAMESPACE}.{LIC_TABLE}
            WHERE {period_filter} AND {dept_sql} AND {prov_sql}
            GROUP BY 1
            ORDER BY licenses DESC
            LIMIT 20
            """
            license_utilization = await _run_query(util_sql, interactive)

            funnel_sql = f"""
            SELECT
              COUNT(DISTINCT NULLIF(TRIM(CAST("UserEmail" AS VARCHAR)), '')) AS assigned,
              COUNT(DISTINCT NULLIF(TRIM(CAST("UserEmail" AS VARCHAR)), '')) FILTER (
                WHERE "LastActivityAt" IS NOT NULL
              ) AS logged_in,
              COUNT(DISTINCT NULLIF(TRIM(CAST("UserEmail" AS VARCHAR)), '')) FILTER (
                WHERE lower(COALESCE(CAST("ActivityStatus" AS VARCHAR), '')) NOT IN ('nunca_usou', '')
              ) AS used,
              COUNT(DISTINCT NULLIF(TRIM(CAST("UserEmail" AS VARCHAR)), '')) FILTER (
                WHERE lower(COALESCE(CAST("ActivityStatus" AS VARCHAR), '')) IN ('ativo', 'active')
              ) AS healthy
            FROM iceberg.{MART_NAMESPACE}.{LIC_TABLE}
            WHERE {period_filter} AND {dept_sql} AND {prov_sql}
            """
            funnel_rows = await _run_query(funnel_sql, interactive)
            adoption_funnel = funnel_rows[0] if funnel_rows else {}

            lic_period = period_filter.replace('"_billing_period"', 'l."_billing_period"')
            bubble_sql = f"""
            SELECT
              COALESCE(NULLIF(TRIM(CAST(l."Provider" AS VARCHAR)), ''), 'unknown') AS provider,
              COUNT(DISTINCT l."UserEmail") AS licenses,
              COALESCE(SUM(c.metric_value), 0) AS usage_value
            FROM iceberg.{MART_NAMESPACE}.{LIC_TABLE} l
            LEFT JOIN (
              SELECT "UserEmail", "Provider", SUM({metric_expr}) AS metric_value
              FROM iceberg.{MART_NAMESPACE}.{CONS_TABLE}
              WHERE {period_filter} AND {prov_sql}
              GROUP BY 1, 2
            ) c ON TRIM(CAST(l."UserEmail" AS VARCHAR)) = TRIM(CAST(c."UserEmail" AS VARCHAR))
              AND TRIM(CAST(l."Provider" AS VARCHAR)) = TRIM(CAST(c."Provider" AS VARCHAR))
            WHERE {lic_period} AND {_dept_filter(scope_path, "l")} AND {_prov_filter(body.provider, "l")}
            GROUP BY 1
            """
            provider_bubble = await _run_query(bubble_sql, interactive)

            return {
                "metric": body.metric,
                "cost_coverage_pct": cost_coverage_pct,
                "department_path": " > ".join(scope_path) if scope_path else None,
                "kpis": {
                    "total_usage": float(kpis.get("total_usage") or 0),
                    "active_licenses": int(kpis.get("active_licenses") or 0),
                    "utilization_rate": float(kpis.get("utilization_rate") or 0),
                    "usage_per_active_user": float(kpis.get("usage_per_active_user") or 0),
                    "estimated_waste": float(kpis.get("estimated_waste") or 0),
                    "mom_change_pct": float(kpis.get("mom_change_pct") or 0),
                },
                "monthly_series": [
                    {"period": str(r.get("period")), "provider": str(r.get("provider")), "value": float(r.get("value") or 0)}
                    for r in monthly_series
                ],
                "license_utilization_by_dept": [
                    {
                        "department": str(r.get("department")),
                        "licenses": int(r.get("licenses") or 0),
                        "active_users": int(r.get("active_users") or 0),
                        "utilization_rate": (int(r.get("active_users") or 0) / int(r.get("licenses") or 1)) if int(r.get("licenses") or 0) else 0,
                    }
                    for r in license_utilization
                ],
                "adoption_funnel": {
                    "assigned": int(adoption_funnel.get("assigned") or 0),
                    "logged_in": int(adoption_funnel.get("logged_in") or 0),
                    "used": int(adoption_funnel.get("used") or 0),
                    "healthy": int(adoption_funnel.get("healthy") or 0),
                },
                "provider_bubble": [
                    {"provider": str(r.get("provider")), "licenses": int(r.get("licenses") or 0), "usage_value": float(r.get("usage_value") or 0)}
                    for r in provider_bubble
                ],
            }
        except TrinoCancelled:
            raise _cancelled_http()


@router.post("/licenses")
async def ai_licenses(body: AILicensesRequest, request: Request, db: AsyncSession = Depends(get_db)):
    async with _ClientCancelScope(request, timeout_s=120) as scope:
        try:
            await _ensure_trino()
            scope_path = await _resolve_scope_path(db, body.department_id, body.department_path)
            period_filter = _period_filter(body.period, body.year)
            dept_sql = _dept_filter(scope_path)
            prov_sql = _prov_filter(body.provider)
            interactive = scope.trino_kwargs
            cost_coverage_pct = await _cost_coverage(period_filter, dept_sql, interactive, prov_sql)

            inactive_cases = ", ".join(
                f"COUNT(*) FILTER (WHERE COALESCE(TRY_CAST(\"DaysSinceLastActivity\" AS BIGINT), 999) >= {days}) AS inactive_{days}d"
                for days in body.inactivity_days
            )
            summary_sql = f"""
            SELECT
              COUNT(*) AS total_licenses,
              COUNT(*) FILTER (WHERE lower(COALESCE(CAST("ActivityStatus" AS VARCHAR), '')) = 'nunca_usou') AS never_used,
              {inactive_cases},
              SUM({_lic_cost_expr()}) AS total_allocated_cost,
              SUM({_lic_cost_expr()}) FILTER (
                WHERE lower(COALESCE(CAST("ActivityStatus" AS VARCHAR), '')) IN (
                  'nunca_usou', 'inativo_30d', 'inativo_60d', 'inativo_90d'
                )
              ) AS reclaim_potential
            FROM iceberg.{MART_NAMESPACE}.{LIC_TABLE}
            WHERE {period_filter} AND {dept_sql} AND {prov_sql}
            """
            summary_rows = await _run_query(summary_sql, interactive)
            summary = summary_rows[0] if summary_rows else {}

            reclaim_filter = ""
            if body.reclaim_only:
                reclaim_filter = """
                  AND lower(COALESCE(CAST("ActivityStatus" AS VARCHAR), '')) IN (
                    'nunca_usou', 'inativo_30d', 'inativo_60d', 'inativo_90d'
                  )
                """

            list_sql = f"""
            SELECT
              NULLIF(TRIM(CAST("UserEmail" AS VARCHAR)), '') AS user_email,
              COALESCE(NULLIF(TRIM(CAST("Provider" AS VARCHAR)), ''), 'unknown') AS provider,
              COALESCE(NULLIF(TRIM(CAST("ProductName" AS VARCHAR)), ''), '') AS product_name,
              COALESCE(NULLIF(TRIM(CAST("LicenseSkuName" AS VARCHAR)), ''), '') AS license_sku,
              COALESCE(CAST("ActivityStatus" AS VARCHAR), '') AS activity_status,
              COALESCE(TRY_CAST("DaysSinceLastActivity" AS BIGINT), 0) AS days_since_activity,
              {_lic_cost_expr()} AS allocated_cost,
              "LastActivityAt" AS last_activity_at
            FROM iceberg.{MART_NAMESPACE}.{LIC_TABLE}
            WHERE {period_filter} AND {dept_sql} AND {prov_sql}
              {reclaim_filter}
            ORDER BY allocated_cost DESC, days_since_activity DESC
            OFFSET {body.offset} LIMIT {body.limit}
            """
            items = await _run_query(list_sql, interactive)

            return {
                "metric": body.metric,
                "cost_coverage_pct": cost_coverage_pct,
                "summary": {
                    "total_licenses": int(summary.get("total_licenses") or 0),
                    "never_used": int(summary.get("never_used") or 0),
                    **{f"inactive_{d}d": int(summary.get(f"inactive_{d}d") or 0) for d in body.inactivity_days},
                    "total_allocated_cost": float(summary.get("total_allocated_cost") or 0),
                    "reclaim_potential": float(summary.get("reclaim_potential") or 0),
                },
                "items": [
                    {
                        "user_email": str(r.get("user_email") or ""),
                        "provider": str(r.get("provider") or ""),
                        "product_name": str(r.get("product_name") or ""),
                        "license_sku": str(r.get("license_sku") or ""),
                        "activity_status": str(r.get("activity_status") or ""),
                        "days_since_activity": int(r.get("days_since_activity") or 0),
                        "allocated_cost": float(r.get("allocated_cost") or 0),
                        "last_activity_at": r.get("last_activity_at"),
                    }
                    for r in items
                ],
            }
        except TrinoCancelled:
            raise _cancelled_http()


@router.post("/consumption")
async def ai_consumption(body: AIConsumptionRequest, request: Request, db: AsyncSession = Depends(get_db)):
    async with _ClientCancelScope(request, timeout_s=120) as scope:
        try:
            await _ensure_trino()
            scope_path = await _resolve_scope_path(db, body.department_id, body.department_path)
            period_filter = _period_filter(body.period, body.year)
            dept_sql = _dept_filter(scope_path)
            prov_sql = _prov_filter(body.provider)
            metric_expr = _cons_metric_expr(body.metric)
            interactive = scope.trino_kwargs
            cost_coverage_pct = await _cost_coverage(period_filter, dept_sql, interactive, prov_sql)

            p_low = body.percentile_low
            p_high = body.percentile_high

            ranked_sql = f"""
            WITH user_metrics AS (
              SELECT
                NULLIF(TRIM(CAST("UserEmail" AS VARCHAR)), '') AS user_email,
                COALESCE(NULLIF(TRIM(CAST("Provider" AS VARCHAR)), ''), 'unknown') AS provider,
                SUM({metric_expr}) AS metric_value,
                SUM(COALESCE(TRY_CAST("TotalTokens" AS DOUBLE), 0)) AS total_tokens
              FROM iceberg.{MART_NAMESPACE}.{CONS_TABLE}
              WHERE {period_filter} AND {dept_sql} AND {prov_sql}
              GROUP BY 1, 2
            ),
            ranked AS (
              SELECT
                *,
                PERCENT_RANK() OVER (PARTITION BY provider ORDER BY metric_value) AS usage_index,
                PERCENT_RANK() OVER (ORDER BY metric_value) AS global_rank
              FROM user_metrics
              WHERE user_email IS NOT NULL
            ),
            thresholds AS (
              SELECT
                approx_percentile(metric_value, {p_low}) AS p10,
                approx_percentile(metric_value, {p_high}) AS p95
              FROM user_metrics
            )
            SELECT r.*, t.p10, t.p95
            FROM ranked r CROSS JOIN thresholds t
            """
            ranked_rows = await _run_query(ranked_sql, interactive)

            pareto_sql = f"""
            SELECT
              COALESCE(NULLIF(TRIM(CAST("Provider" AS VARCHAR)), ''), 'unknown') AS provider,
              SUM({metric_expr}) AS value
            FROM iceberg.{MART_NAMESPACE}.{CONS_TABLE}
            WHERE {period_filter} AND {dept_sql} AND {prov_sql}
            GROUP BY 1
            ORDER BY value DESC
            """
            pareto = await _run_query(pareto_sql, interactive)

            p95_val = float(ranked_rows[0].get("p95") or 0) if ranked_rows else 0
            p10_val = float(ranked_rows[0].get("p10") or 0) if ranked_rows else 0

            outliers = [
                r for r in ranked_rows
                if float(r.get("metric_value") or 0) >= p95_val and p95_val > 0
            ]
            zombies = [
                r for r in ranked_rows
                if float(r.get("metric_value") or 0) <= p10_val and p10_val > 0
            ]

            if body.outliers_only:
                filtered = outliers
            elif body.zombie_only:
                filtered = zombies
            else:
                filtered = ranked_rows

            filtered = sorted(filtered, key=lambda r: float(r.get("metric_value") or 0), reverse=True)[: body.limit]

            heatmap_sql = f"""
            SELECT
              CAST("UsageDate" AS VARCHAR) AS usage_date,
              NULLIF(TRIM(CAST("UserEmail" AS VARCHAR)), '') AS user_email,
              SUM({metric_expr}) AS value
            FROM iceberg.{MART_NAMESPACE}.{CONS_TABLE}
            WHERE {period_filter} AND {dept_sql} AND {prov_sql}
              AND "UsageDate" IS NOT NULL
            GROUP BY 1, 2
            ORDER BY usage_date, user_email
            LIMIT 500
            """
            heatmap = await _run_query(heatmap_sql, interactive)

            cost_per_m_sql = f"""
            SELECT
              COALESCE(NULLIF(TRIM(CAST("Provider" AS VARCHAR)), ''), 'unknown') AS provider,
              COALESCE(NULLIF(TRIM(CAST("ModelName" AS VARCHAR)), ''), 'all') AS model_name,
              SUM(COALESCE(TRY_CAST("AllocatedCost" AS DOUBLE), TRY_CAST("realizado" AS DOUBLE), TRY_CAST("BilledCost" AS DOUBLE), 0)) AS cost,
              SUM(COALESCE(TRY_CAST("TotalTokens" AS DOUBLE), 0)) AS tokens
            FROM iceberg.{MART_NAMESPACE}.{CONS_TABLE}
            WHERE {period_filter} AND {dept_sql} AND {prov_sql}
            GROUP BY 1, 2
            HAVING SUM(COALESCE(TRY_CAST("TotalTokens" AS DOUBLE), 0)) > 0
            """
            cost_per_m = await _run_query(cost_per_m_sql, interactive)

            return {
                "metric": body.metric,
                "cost_coverage_pct": cost_coverage_pct,
                "percentiles": {"p10": p10_val, "p95": p95_val},
                "pareto": [
                    {"provider": str(r.get("provider")), "value": float(r.get("value") or 0)}
                    for r in pareto
                ],
                "cost_per_million_tokens": [
                    {
                        "provider": str(r.get("provider")),
                        "model_name": str(r.get("model_name")),
                        "cost_per_million": (
                            float(r.get("cost") or 0) * 1_000_000 / float(r.get("tokens") or 1)
                        ),
                    }
                    for r in cost_per_m
                ],
                "outliers": [
                    {
                        "user_email": str(r.get("user_email") or ""),
                        "provider": str(r.get("provider") or ""),
                        "metric_value": float(r.get("metric_value") or 0),
                        "usage_index": float(r.get("usage_index") or 0),
                        "percent_rank": float(r.get("usage_index") or 0),
                    }
                    for r in outliers[:body.limit]
                ],
                "zombies": [
                    {
                        "user_email": str(r.get("user_email") or ""),
                        "provider": str(r.get("provider") or ""),
                        "metric_value": float(r.get("metric_value") or 0),
                        "usage_index": float(r.get("usage_index") or 0),
                        "percent_rank": float(r.get("usage_index") or 0),
                    }
                    for r in zombies[:body.limit]
                ],
                "ranked_users": [
                    {
                        "user_email": str(r.get("user_email") or ""),
                        "provider": str(r.get("provider") or ""),
                        "metric_value": float(r.get("metric_value") or 0),
                        "total_tokens": float(r.get("total_tokens") or 0),
                        "usage_index": float(r.get("usage_index") or 0),
                        "percent_rank": float(r.get("usage_index") or 0),
                    }
                    for r in filtered
                ],
                "heatmap": [
                    {"usage_date": str(r.get("usage_date")), "user_email": str(r.get("user_email") or ""), "value": float(r.get("value") or 0)}
                    for r in heatmap
                ],
            }
        except TrinoCancelled:
            raise _cancelled_http()


@router.post("/user")
async def ai_user(body: AIUserRequest, request: Request, db: AsyncSession = Depends(get_db)):
    async with _ClientCancelScope(request, timeout_s=120) as scope:
        try:
            await _ensure_trino()
            scope_path = await _resolve_scope_path(db, body.department_id, body.department_path)
            period_filter = _period_filter(body.period, body.year)
            dept_sql = _dept_filter(scope_path)
            prov_sql = _prov_filter(body.provider)
            metric_expr = _cons_metric_expr(body.metric)
            interactive = scope.trino_kwargs
            email_lit = _quote_literal(body.user_email.strip().lower())
            cost_coverage_pct = await _cost_coverage(period_filter, dept_sql, interactive, prov_sql)

            lic_sql = f"""
            SELECT
              COALESCE(NULLIF(TRIM(CAST("Provider" AS VARCHAR)), ''), 'unknown') AS provider,
              COALESCE(NULLIF(TRIM(CAST("ProductName" AS VARCHAR)), ''), '') AS product_name,
              COALESCE(CAST("LicenseStatus" AS VARCHAR), '') AS license_status,
              COALESCE(CAST("ActivityStatus" AS VARCHAR), '') AS activity_status,
              "LastActivityAt" AS last_activity_at,
              COALESCE(TRY_CAST("DaysSinceLastActivity" AS BIGINT), 0) AS days_since_activity,
              {_lic_cost_expr()} AS allocated_cost
            FROM iceberg.{MART_NAMESPACE}.{LIC_TABLE}
            WHERE {period_filter} AND {dept_sql} AND {prov_sql}
              AND lower(TRIM(CAST("UserEmail" AS VARCHAR))) = {email_lit}
            """
            licenses = await _run_query(lic_sql, interactive)

            cons_sql = f"""
            SELECT
              COALESCE(NULLIF(TRIM(CAST("Provider" AS VARCHAR)), ''), 'unknown') AS provider,
              COALESCE(NULLIF(TRIM(CAST("ModelName" AS VARCHAR)), ''), 'all') AS model_name,
              CAST("UsageDate" AS VARCHAR) AS usage_date,
              SUM(COALESCE(TRY_CAST("TotalTokens" AS DOUBLE), 0)) AS total_tokens,
              SUM(COALESCE(TRY_CAST("BilledCost" AS DOUBLE), TRY_CAST("AllocatedCost" AS DOUBLE), 0)) AS cost,
              SUM({metric_expr}) AS metric_value
            FROM iceberg.{MART_NAMESPACE}.{CONS_TABLE}
            WHERE {period_filter} AND {dept_sql} AND {prov_sql}
              AND lower(TRIM(CAST("UserEmail" AS VARCHAR))) = {email_lit}
            GROUP BY 1, 2, 3
            ORDER BY usage_date DESC
            """
            consumption = await _run_query(cons_sql, interactive)

            index_sql = f"""
            WITH user_metrics AS (
              SELECT
                lower(TRIM(CAST("UserEmail" AS VARCHAR))) AS user_email,
                SUM({metric_expr}) AS metric_value
              FROM iceberg.{MART_NAMESPACE}.{CONS_TABLE}
              WHERE {period_filter} AND {dept_sql}
              GROUP BY 1
            )
            SELECT
              PERCENT_RANK() OVER (ORDER BY metric_value) AS dept_percent_rank,
              metric_value
            FROM user_metrics
            WHERE user_email = {email_lit}
            """
            index_rows = await _run_query(index_sql, interactive)
            dept_percent_rank = float(index_rows[0].get("dept_percent_rank") or 0) if index_rows else 0

            total_usage = sum(float(r.get("metric_value") or 0) for r in consumption)
            total_cost = sum(float(r.get("cost") or 0) for r in consumption)
            total_tokens = sum(float(r.get("total_tokens") or 0) for r in consumption)

            return {
                "metric": body.metric,
                "cost_coverage_pct": cost_coverage_pct,
                "user_email": body.user_email,
                "summary": {
                    "total_usage": total_usage,
                    "total_cost": total_cost,
                    "total_tokens": total_tokens,
                    "dept_percent_rank": dept_percent_rank,
                    "license_count": len(licenses),
                },
                "licenses": [
                    {
                        "provider": str(r.get("provider") or ""),
                        "product_name": str(r.get("product_name") or ""),
                        "license_status": str(r.get("license_status") or ""),
                        "activity_status": str(r.get("activity_status") or ""),
                        "last_activity_at": r.get("last_activity_at"),
                        "days_since_activity": int(r.get("days_since_activity") or 0),
                        "allocated_cost": float(r.get("allocated_cost") or 0),
                    }
                    for r in licenses
                ],
                "consumption_by_model": [
                    {
                        "provider": str(r.get("provider") or ""),
                        "model_name": str(r.get("model_name") or ""),
                        "usage_date": str(r.get("usage_date") or ""),
                        "total_tokens": float(r.get("total_tokens") or 0),
                        "cost": float(r.get("cost") or 0),
                        "metric_value": float(r.get("metric_value") or 0),
                    }
                    for r in consumption
                ],
            }
        except TrinoCancelled:
            raise _cancelled_http()
