"""
Dashboards executivo/técnico por provedor de IA (Anthropic Claude, Google NotebookLM)
sobre as tabelas gold normalizadas dest_licencas_ia / dest_consumo_ia.

Estratégia: um único endpoint parametrizado por provider ("1 dash, troca o parâmetro").
A semântica de métrica se adapta ao provider:
  - anthropic  → métrica primária = tokens (consumo tem UserEmail/ModelName/tokens)
  - notebooklm → métrica primária = custo (consumo é billing GCP sem e-mail/tokens)
A validação de "usuário logou na ferramenta" vem de dest_licencas_ia
(LastActivityAt / ActivityStatus / DaysSinceLastActivity).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.services.trino_service import TrinoCancelled, execute as trino_execute, wait_until_ready

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/visoes-consumo-ia/provider", tags=["visoes-consumo-ia"])

LIC_TABLE = "dest_licencas_ia"
CONS_TABLE = "dest_consumo_ia"

_PROVIDERS: dict[str, dict[str, str]] = {
    "anthropic": {
        "label": "Anthropic Claude",
        "provider_sql": "lower(TRIM(CAST(\"Provider\" AS VARCHAR))) = 'anthropic'",
        "product_sql": "lower(TRIM(CAST(\"ProductName\" AS VARCHAR))) = 'claude'",
        "primary_metric": "tokens",
        "color": "#D97757",
    },
    "notebooklm": {
        "label": "Google NotebookLM",
        "provider_sql": "lower(TRIM(CAST(\"Provider\" AS VARCHAR))) = 'google'",
        "product_sql": "lower(TRIM(CAST(\"ProductName\" AS VARCHAR))) = 'notebooklm'",
        "primary_metric": "cost",
        "color": "#34A853",
    },
}

# Cache curto: o dashboard é read-only e as tabelas Gold são batch-fed.
_OVERVIEW_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_OVERVIEW_CACHE_TTL_S = 30.0


class _ClientCancelScope:
    def __init__(self, request: Request, *, timeout_s: float = 90):
        self.request = request
        self.timeout_s = timeout_s
        self.cancel_event = asyncio.Event()
        self._watcher: asyncio.Task | None = None

    async def __aenter__(self) -> _ClientCancelScope:
        async def _watch_disconnect() -> None:
            while not self.cancel_event.is_set():
                if await self.request.is_disconnected():
                    self.cancel_event.set()
                    return
                await asyncio.sleep(0.25)

        self._watcher = asyncio.create_task(_watch_disconnect())
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.cancel_event.set()
        if self._watcher:
            self._watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watcher

    @property
    def trino_kwargs(self) -> dict[str, Any]:
        return {"timeout_s": self.timeout_s, "max_retries": 2, "cancel_event": self.cancel_event}


class ProviderIaRequest(BaseModel):
    provider: Literal["anthropic", "notebooklm"]
    date_from: str | None = Field(default=None, description="YYYY-MM-DD inclusivo")
    date_to: str | None = Field(default=None, description="YYYY-MM-DD inclusivo")
    days: int | None = Field(default=90, ge=1, le=730, description="Janela dos últimos N dias até a última amostra")
    billing_period: str | None = Field(default=None, description="YYYYMM — sobrepõe o recorte por data")


def _quote_literal(value: str | int | float | bool) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _provider_scope(provider: str) -> str:
    meta = _PROVIDERS[provider]
    return f"{meta['provider_sql']} AND {meta['product_sql']}"


def _activity_status_expr(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return f"UPPER(TRIM(COALESCE(CAST({prefix}\"ActivityStatus\" AS VARCHAR), '')))"


async def _run_query(sql: str, interactive: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        return await trino_execute(sql, **interactive)
    except TrinoCancelled:
        raise
    except Exception as exc:
        logger.exception("Provider IA dashboard query failed")
        raise HTTPException(status_code=503, detail=f"Falha na consulta Trino: {exc}")


async def _ensure_trino() -> None:
    try:
        await wait_until_ready(timeout_s=20)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Trino está reiniciando: {exc}")


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _str(value: Any) -> str:
    return "" if value is None else str(value)


def _resolve_window(body: ProviderIaRequest, rows: list[dict[str, Any]]) -> tuple[str, str]:
    """Resolve date_from/date_to a partir do corpo + range real dos dados."""
    scope = _provider_scope(body.provider)
    dates = [r for r in rows if r.get("day")]
    if not dates:
        dates = [{"day": datetime.now().strftime("%Y-%m-%d")}]
    max_day = max(_str(r.get("day")) for r in dates)
    min_day = min(_str(r.get("day")) for r in dates)
    if body.billing_period:
        return min_day, max_day
    date_to = body.date_to or max_day
    if body.date_from:
        date_from = body.date_from
    else:
        days = body.days or 90
        from datetime import date, timedelta

        parsed = date.fromisoformat(date_to[:10])
        date_from = (parsed - timedelta(days=days - 1)).isoformat()
        if date_from < min_day:
            date_from = min_day
    return date_from, date_to


@router.post("/overview")
async def provider_overview(body: ProviderIaRequest, request: Request):
    """Payload completo do dashboard executivo/técnico de um provedor de IA."""
    meta = _PROVIDERS[body.provider]
    scope = _provider_scope(body.provider)
    cache_key = f"{body.provider}|{body.date_from}|{body.date_to}|{body.days}|{body.billing_period}"
    hit = _OVERVIEW_CACHE.get(cache_key)
    if hit and time.monotonic() - hit[0] < _OVERVIEW_CACHE_TTL_S:
        return hit[1]

    async with _ClientCancelScope(request, timeout_s=120) as scope_ctx:
        try:
            await _ensure_trino()
            interactive = scope_ctx.trino_kwargs

            # 1) Range real + janela efetiva
            range_rows = await _run_query(
                f"""
                SELECT
                  min(substr("UsageDate", 1, 10)) AS min_day,
                  max(substr("UsageDate", 1, 10)) AS max_day,
                  COUNT(DISTINCT "_billing_period") AS period_count
                FROM iceberg.gold.{CONS_TABLE}
                WHERE {scope}
                """,
                interactive,
            )
            range_row = range_rows[0] if range_rows else {}
            min_day = _str(range_row.get("min_day"))
            max_day = _str(range_row.get("max_day"))
            date_from, date_to = _resolve_window(
                body, [{"day": min_day}, {"day": max_day}]
            )
            if body.billing_period:
                date_filter = f"\"_billing_period\" = {_quote_literal(body.billing_period)}"
            else:
                date_filter = (
                    f"substr(\"UsageDate\", 1, 10) BETWEEN {_quote_literal(date_from)} AND {_quote_literal(date_to)}"
                )

            # 2) KPIs de consumo
            kpi_sql = f"""
            SELECT
              COUNT(*) AS consumption_rows,
              COUNT(DISTINCT NULLIF(TRIM(CAST("UserEmail" AS VARCHAR)), '')) AS consumption_users,
              SUM(CASE WHEN COALESCE(TRY_CAST("TotalTokens" AS DOUBLE), 0) > 0 THEN 1 ELSE 0 END) AS rows_with_tokens,
              SUM(CASE WHEN COALESCE(TRY_CAST("InputTokens" AS DOUBLE), 0) + COALESCE(TRY_CAST("OutputTokens" AS DOUBLE), 0) > 0 THEN 1 ELSE 0 END) AS rows_with_io_tokens,
              SUM(CASE WHEN COALESCE(TRY_CAST("realizado" AS DOUBLE), TRY_CAST("BilledCost" AS DOUBLE), 0) > 0 THEN 1 ELSE 0 END) AS rows_with_cost,
              SUM(COALESCE(TRY_CAST("InputTokens" AS DOUBLE), 0)) AS input_tokens,
              SUM(COALESCE(TRY_CAST("OutputTokens" AS DOUBLE), 0)) AS output_tokens,
              SUM(COALESCE(TRY_CAST("TotalTokens" AS DOUBLE), 0)) AS total_tokens,
              SUM(COALESCE(TRY_CAST("RequestCount" AS DOUBLE), 0)) AS total_requests,
              SUM(COALESCE(TRY_CAST("realizado" AS DOUBLE), TRY_CAST("BilledCost" AS DOUBLE), 0)) AS total_cost
            FROM iceberg.gold.{CONS_TABLE}
            WHERE {scope} AND {date_filter}
            """
            kpi_rows = await _run_query(kpi_sql, interactive)
            kpi = kpi_rows[0] if kpi_rows else {}
            consumption_rows = _int(kpi.get("consumption_rows"))
            consumption_users = _int(kpi.get("consumption_users"))
            input_tokens = _num(kpi.get("input_tokens"))
            output_tokens = _num(kpi.get("output_tokens"))
            total_tokens = _num(kpi.get("total_tokens"))
            total_requests = _num(kpi.get("total_requests"))
            total_cost = _num(kpi.get("total_cost"))
            # TotalTokens espelha InputTokens na fonte normalizada do Anthropic;
            # o total executivo é input+output (evita subcontagem do output).
            tokens_io = input_tokens + output_tokens
            effective_tokens = tokens_io if tokens_io > 0 else total_tokens

            # 3) Série diária
            series_sql = f"""
            SELECT
              substr("UsageDate", 1, 10) AS day,
              SUM(COALESCE(TRY_CAST("InputTokens" AS DOUBLE), 0)) AS input_tokens,
              SUM(COALESCE(TRY_CAST("OutputTokens" AS DOUBLE), 0)) AS output_tokens,
              SUM(COALESCE(TRY_CAST("TotalTokens" AS DOUBLE), 0)) AS total_tokens,
              SUM(COALESCE(TRY_CAST("RequestCount" AS DOUBLE), 0)) AS requests,
              SUM(COALESCE(TRY_CAST("realizado" AS DOUBLE), TRY_CAST("BilledCost" AS DOUBLE), 0)) AS cost,
              COUNT(DISTINCT NULLIF(TRIM(CAST("UserEmail" AS VARCHAR)), '')) AS users
            FROM iceberg.gold.{CONS_TABLE}
            WHERE {scope} AND {date_filter}
            GROUP BY 1
            ORDER BY 1
            """
            series_rows = await _run_query(series_sql, interactive)
            daily_series = [
                {
                    "date": _str(r.get("day")),
                    "input_tokens": _num(r.get("input_tokens")),
                    "output_tokens": _num(r.get("output_tokens")),
                    "total_tokens": _num(r.get("total_tokens")),
                    "requests": _num(r.get("requests")),
                    "cost": _num(r.get("cost")),
                    "users": _int(r.get("users")),
                }
                for r in series_rows
            ]

            # 4) Quebra por modelo / serviço
            model_sql = f"""
            SELECT
              COALESCE(NULLIF(TRIM(CAST("ModelName" AS VARCHAR)), ''), 'não informado') AS model_name,
              COALESCE(NULLIF(TRIM(CAST("RecordType" AS VARCHAR)), ''), '') AS record_type,
              COUNT(*) AS rows,
              SUM(COALESCE(TRY_CAST("InputTokens" AS DOUBLE), 0)) AS input_tokens,
              SUM(COALESCE(TRY_CAST("OutputTokens" AS DOUBLE), 0)) AS output_tokens,
              SUM(COALESCE(TRY_CAST("TotalTokens" AS DOUBLE), 0)) AS total_tokens,
              SUM(COALESCE(TRY_CAST("RequestCount" AS DOUBLE), 0)) AS requests,
              SUM(COALESCE(TRY_CAST("realizado" AS DOUBLE), TRY_CAST("BilledCost" AS DOUBLE), 0)) AS cost
            FROM iceberg.gold.{CONS_TABLE}
            WHERE {scope} AND {date_filter}
            GROUP BY 1, 2
            ORDER BY cost DESC, total_tokens DESC
            LIMIT 40
            """
            model_rows = await _run_query(model_sql, interactive)
            model_breakdown = [
                {
                    "model_name": _str(r.get("model_name")),
                    "record_type": _str(r.get("record_type")),
                    "rows": _int(r.get("rows")),
                    "input_tokens": _num(r.get("input_tokens")),
                    "output_tokens": _num(r.get("output_tokens")),
                    "total_tokens": _num(r.get("total_tokens")),
                    "requests": _num(r.get("requests")),
                    "cost": _num(r.get("cost")),
                    "share_pct": (
                        round(_num(r.get("cost")) * 100.0 / total_cost, 2)
                        if meta["primary_metric"] == "cost" and total_cost > 0
                        else round(
                            (_num(r.get("input_tokens")) + _num(r.get("output_tokens"))) * 100.0 / effective_tokens, 2
                        )
                        if effective_tokens > 0
                        else 0.0
                    ),
                }
                for r in model_rows
            ]

            # 5) Licenças — snapshot mais recente (estado atual de logon/uso)
            lic_scope_sql = f"""
            SELECT "UserEmail", "UserName", "UserRole", "LicenseStatus", "ActivityStatus",
                   "LastActivityAt", "DaysSinceLastActivity", "LicenseSkuName", "OrganizationId",
                   "_billing_period"
            FROM iceberg.gold.{LIC_TABLE}
            WHERE {scope}
              AND "_billing_period" = (
                SELECT MAX("_billing_period") FROM iceberg.gold.{LIC_TABLE} WHERE {scope}
              )
            """
            lic_rows = await _run_query(lic_scope_sql, interactive)
            license_table = [
                {
                    "user_email": _str(r.get("UserEmail")),
                    "user_name": _str(r.get("UserName")),
                    "user_role": _str(r.get("UserRole")),
                    "license_status": _str(r.get("LicenseStatus")),
                    "activity_status": _str(r.get("ActivityStatus")).upper(),
                    "last_activity_at": r.get("LastActivityAt"),
                    "days_since_activity": _num(r.get("DaysSinceLastActivity")) if r.get("LastActivityAt") is not None else None,
                    "license_sku": _str(r.get("LicenseSkuName")),
                    "organization_id": _str(r.get("OrganizationId")),
                    "logged_in": r.get("LastActivityAt") is not None,
                }
                for r in lic_rows
            ]
            license_period = _str(lic_rows[0].get("_billing_period")) if lic_rows else None
            assigned = len(license_table)
            logged_in = sum(1 for u in license_table if u["logged_in"])
            used = sum(
                1 for u in license_table
                if u["activity_status"] not in ("", "NUNCA_USOU")
            )
            healthy = sum(
                1 for u in license_table
                if u["activity_status"] in ("ATIVO", "ACTIVE", "ativo")
            )
            never_used = sum(1 for u in license_table if u["activity_status"] == "NUNCA_USOU")
            active_licenses = sum(
                1 for u in license_table
                if _str(u["license_status"]).lower() not in ("revoked", "cancelled", "suspended")
            )
            inactive_30 = sum(1 for u in license_table if u["days_since_activity"] and u["days_since_activity"] >= 30)
            inactive_60 = sum(1 for u in license_table if u["days_since_activity"] and u["days_since_activity"] >= 60)
            inactive_90 = sum(1 for u in license_table if u["days_since_activity"] and u["days_since_activity"] >= 90)

            # 6) Ranking de usuários — consumo (quando há e-mail) + atividade de licença
            ranking_sql = f"""
            SELECT
              COALESCE(NULLIF(TRIM(CAST(c."UserEmail" AS VARCHAR)), ''), l."UserEmail") AS user_email,
              COALESCE(NULLIF(TRIM(CAST(l."UserName" AS VARCHAR)), ''), '') AS user_name,
              SUM(COALESCE(TRY_CAST(c."InputTokens" AS DOUBLE), 0)) AS input_tokens,
              SUM(COALESCE(TRY_CAST(c."OutputTokens" AS DOUBLE), 0)) AS output_tokens,
              SUM(COALESCE(TRY_CAST(c."RequestCount" AS DOUBLE), 0)) AS requests,
              SUM(COALESCE(TRY_CAST(c."realizado" AS DOUBLE), TRY_CAST(c."BilledCost" AS DOUBLE), 0)) AS cost,
              MAX(l."LastActivityAt") AS last_activity_at,
              MAX(l."DaysSinceLastActivity") AS days_since_activity,
              MAX({_activity_status_expr('l')}) AS activity_status
            FROM iceberg.gold.{LIC_TABLE} l
            LEFT JOIN (
              SELECT "UserEmail", "InputTokens", "OutputTokens", "RequestCount", "realizado", "BilledCost"
              FROM iceberg.gold.{CONS_TABLE}
              WHERE {scope} AND {date_filter}
                AND NULLIF(TRIM(CAST("UserEmail" AS VARCHAR)), '') IS NOT NULL
            ) c
              ON lower(TRIM(CAST(l."UserEmail" AS VARCHAR))) = lower(TRIM(CAST(c."UserEmail" AS VARCHAR)))
            WHERE {scope}
              AND l."_billing_period" = (
                SELECT MAX("_billing_period") FROM iceberg.gold.{LIC_TABLE} WHERE {scope}
              )
            GROUP BY 1, 2
            ORDER BY
              SUM(COALESCE(TRY_CAST(c."realizado" AS DOUBLE), TRY_CAST(c."BilledCost" AS DOUBLE), 0)) DESC,
              SUM(COALESCE(TRY_CAST(c."InputTokens" AS DOUBLE), 0) + COALESCE(TRY_CAST(c."OutputTokens" AS DOUBLE), 0)) DESC,
              1
            LIMIT 500
            """
            ranking_rows = await _run_query(ranking_sql, interactive)
            user_ranking = [
                {
                    "user_email": _str(r.get("user_email")),
                    "user_name": _str(r.get("user_name")),
                    "input_tokens": _num(r.get("input_tokens")),
                    "output_tokens": _num(r.get("output_tokens")),
                    "tokens": _num(r.get("input_tokens")) + _num(r.get("output_tokens")),
                    "requests": _num(r.get("requests")),
                    "cost": _num(r.get("cost")),
                    "last_activity_at": r.get("last_activity_at"),
                    "days_since_activity": _num(r.get("days_since_activity")) if r.get("last_activity_at") is not None else None,
                    "activity_status": _str(r.get("activity_status")),
                    "logged_in": r.get("last_activity_at") is not None,
                }
                for r in ranking_rows
            ]

            # 7) Qualidade do dado — narrativas por provider
            notes: list[str] = []
            if meta["primary_metric"] == "tokens":
                notes.append(
                    "Consumo com tokens por usuário/modelo/dia. " 
                    "Na fonte normalizada, TotalTokens espelha InputTokens; o total executivo usa input+output."
                )
                if total_cost <= 0:
                    notes.append("Sem custo (BilledCost/realizado) no recorte — o consumo é medido em tokens.")
            else:
                notes.append(
                    "Consumo é billing GCP (custo por serviço) sem e-mail/tokens por usuário — " 
                    "a visão por usuário usa a atividade de licença (NotebookLM)."
                )
            if consumption_rows == 0:
                notes.append("Nenhuma linha de consumo no recorte selecionado.")
            if _int(kpi.get("rows_with_io_tokens")) == 0 and _int(kpi.get("rows_with_tokens")) == 0 and meta["primary_metric"] == "tokens":
                notes.append("Recorte sem tokens: verifique o período ou a ingestão do conector.")

            data_quality = {
                "consumption_rows": consumption_rows,
                "consumption_with_user": _int(kpi.get("consumption_users")),
                "consumption_with_tokens": _int(kpi.get("rows_with_tokens")),
                "consumption_with_cost": _int(kpi.get("rows_with_cost")) if "rows_with_cost" in kpi else None,
                "license_rows": len(license_table),
                "license_period": license_period,
                "primary_metric": meta["primary_metric"],
                "notes": notes,
            }

            payload = {
                "provider": body.provider,
                "provider_label": meta["label"],
                "provider_color": meta["color"],
                "primary_metric": meta["primary_metric"],
                "period": {
                    "date_from": date_from,
                    "date_to": date_to,
                    "data_min_day": min_day,
                    "data_max_day": max_day,
                    "billing_period": body.billing_period,
                    "license_period": license_period,
                    "period_count": _int(range_row.get("period_count")),
                },
                "kpis": {
                    "total_tokens": effective_tokens,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "source_total_tokens": total_tokens,
                    "total_requests": total_requests,
                    "consumption_users": consumption_users,
                    "total_licenses": assigned,
                    "active_licenses": active_licenses,
                    "logged_in": logged_in,
                    "never_used": never_used,
                    "inactive_30d": inactive_30,
                    "inactive_60d": inactive_60,
                    "inactive_90d": inactive_90,
                    "total_cost": total_cost,
                    "cost_per_million_tokens": (
                        round(total_cost * 1_000_000.0 / effective_tokens, 2) if effective_tokens > 0 else 0.0
                    ),
                    "tokens_per_request": round(effective_tokens / total_requests, 1) if total_requests > 0 else 0.0,
                    "tokens_per_user": round(effective_tokens / consumption_users, 1) if consumption_users > 0 else 0.0,
                },
                "daily_series": daily_series,
                "model_breakdown": model_breakdown,
                "user_ranking": user_ranking,
                "license_table": license_table,
                "adoption_funnel": {
                    "assigned": assigned,
                    "logged_in": logged_in,
                    "used": used,
                    "healthy": healthy,
                },
                "data_quality": data_quality,
            }
            _OVERVIEW_CACHE[cache_key] = (time.monotonic(), payload)
            return payload
        except TrinoCancelled:
            raise HTTPException(status_code=499, detail="Consulta cancelada")
