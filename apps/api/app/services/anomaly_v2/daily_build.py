"""Agregado diário incremental (gold.anomaly_cost_daily)."""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping

from app.services.anomaly_v2.scope import scope_id_for, scope_values_from_row
from app.services.forecast_service import gold_date_expr
from app.services.anomaly_v2.query_control import execute as trino_execute

logger = logging.getLogger(__name__)

_COLUMN_CACHE: dict[tuple[str, str], tuple[float, dict[str, str]]] = {}
_COLUMN_CACHE_TTL_S = 600.0

INCREMENTAL_DAYS = 5
MODEL_VERSION = "v2.0"


def _quote(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _lit(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


async def table_column_types(namespace: str, table: str) -> dict[str, str]:
    """Nome → tipo. Uma consulta de catálogo, reaproveitada por alguns minutos."""
    key = (namespace, table)
    cached = _COLUMN_CACHE.get(key)
    if cached and time.monotonic() - cached[0] < _COLUMN_CACHE_TTL_S:
        return dict(cached[1])
    rows = await trino_execute(
        "SELECT column_name, data_type FROM iceberg.information_schema.columns "
        f"WHERE table_schema = '{namespace}' AND table_name = '{table}'",
        timeout_s=60,
    )
    types = {
        str(row.get("column_name") or "").lower(): str(row.get("data_type") or "").lower()
        for row in rows
        if row.get("column_name")
    }
    _COLUMN_CACHE[key] = (time.monotonic(), types)
    return dict(types)


async def table_columns(namespace: str, table: str) -> set[str]:
    return set((await table_column_types(namespace, table)).keys())


def _varchar_coalesce(columns: set[str], candidates: tuple[str, ...]) -> str:
    present = [name for name in candidates if name.lower() in columns]
    if not present:
        return "CAST('' AS VARCHAR)"
    casts = [f"CAST({_quote(name)} AS VARCHAR)" for name in present]
    if len(casts) == 1:
        return casts[0]
    return "COALESCE(" + ", ".join([*casts, "''"]) + ")"


def billing_periods_for(start: date, end: date) -> list[str]:
    months: list[str] = []
    cursor = (start.replace(day=1) - timedelta(days=1)).replace(day=1)
    while cursor <= end:
        months.append(cursor.strftime("%Y%m"))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


def billing_period_filter(
    period_column: str | None, start: date, end: date, periods: list[str] | None = None
) -> str:
    """Poda de partição: a silver é particionada pelo período de faturamento."""
    if not period_column:
        return "TRUE"
    months = periods or billing_periods_for(start, end)
    values = ", ".join(_lit(month) for month in months)
    return f"CAST({_quote(period_column)} AS VARCHAR) IN ({values})"


def daily_aggregate_sql(
    *,
    source_namespace: str,
    table: str,
    date_column: str,
    period_column: str | None,
    type_column: str | None,
    measure_column: str,
    start: date,
    end: date,
    columns: set[str] | None = None,
    billing_periods: list[str] | None = None,
    row_filter: str = "TRUE",
) -> str:
    date_expr = gold_date_expr(date_column, period_column)
    type_filter = (
        f"({_quote(type_column)} = 'realizado' OR {_quote(type_column)} IS NULL)"
        if type_column
        else "TRUE"
    )
    available = columns or set()
    rg_expr = _varchar_coalesce(
        available,
        ("source_resource_group_name", "resourcegroupname", "resource_group_name", "resourcegroup"),
    )
    region_expr = _varchar_coalesce(available, ("regionname", "region", "location"))
    return f"""
        WITH parsed AS (
            SELECT CAST("cloud_origem" AS VARCHAR) AS cloud_origem,
                   CAST("subaccountname" AS VARCHAR) AS subaccountname,
                   {rg_expr} AS resource_group,
                   CAST("servicename" AS VARCHAR) AS servicename,
                   {_varchar_coalesce(available, ("servicesubcategory",))} AS servicesubcategory,
                   {_varchar_coalesce(available, ("chargecategory",))} AS chargecategory,
                   {_varchar_coalesce(available, ("pricingcategory",))} AS pricingcategory,
                   {_varchar_coalesce(available, ("commitmentdiscounttype",))} AS commitmentdiscounttype,
                   {region_expr} AS region,
                   {_varchar_coalesce(available, ("billingcurrency", "currency"))} AS currency,
                   {date_expr} AS day,
                   COALESCE(TRY_CAST({_quote(measure_column)} AS DOUBLE), 0) AS cost_cloud,
                   {("COALESCE(TRY_CAST(" + _quote("pricingquantity") + " AS DOUBLE), 0)" if "pricingquantity" in available else "CAST(0 AS DOUBLE)")} AS pricing_quantity,
                   {_varchar_coalesce(available, ("resourceid", "resource_id"))} AS resource_id
              FROM iceberg.{source_namespace}.{_quote(table)}
             WHERE {type_filter}
               AND {billing_period_filter(period_column, start, end, billing_periods)}
               AND {row_filter}
        ),
        scoped AS (
            SELECT day,
                   cloud_origem, subaccountname, resource_group, servicename,
                   servicesubcategory, chargecategory, pricingcategory,
                   commitmentdiscounttype, region, currency,
                   SUM(cost_cloud) AS cost_cloud,
                   SUM(pricing_quantity) AS pricing_quantity,
                   approx_distinct(resource_id) AS resource_count,
                   COUNT(*) AS row_count
              FROM parsed
             WHERE day IS NOT NULL
               AND day BETWEEN DATE {_lit(start.isoformat())} AND DATE {_lit(end.isoformat())}
             GROUP BY 1,2,3,4,5,6,7,8,9,10,11
        )
        SELECT CAST(day AS VARCHAR) AS day,
               cloud_origem, subaccountname, resource_group, servicename,
               servicesubcategory, chargecategory, pricingcategory,
               commitmentdiscounttype, region, currency,
               cost_cloud, pricing_quantity, resource_count, row_count
          FROM scoped
    """


async def build_daily_aggregate(
    config: Mapping[str, Any],
    *,
    as_of: date,
    lookback_days: int = 90,
) -> dict[str, Any]:
    """Lê o silver no intervalo e devolve linhas com scope_id (para publicação)."""
    start = as_of - timedelta(days=max(INCREMENTAL_DAYS, lookback_days) - 1)
    incremental_start = as_of - timedelta(days=INCREMENTAL_DAYS - 1)
    columns = await table_columns(
        str(config.get("source_namespace") or "silver"),
        str(config["destination_physical_name"]),
    )
    sql = daily_aggregate_sql(
        source_namespace=str(config.get("source_namespace") or "silver"),
        table=str(config["destination_physical_name"]),
        date_column=str(config["date_column"]),
        period_column=config.get("period_column"),
        type_column=config.get("type_column"),
        measure_column=str(config.get("measure_column") or "realizado_cloud"),
        start=incremental_start,
        end=as_of,
        columns=columns,
    )
    rows = await trino_execute(sql, timeout_s=900)
    loaded_at = datetime.now(timezone.utc).isoformat()
    out: list[dict[str, Any]] = []
    for row in rows:
        scope_vals = scope_values_from_row(row)
        sid = scope_id_for(scope_vals)
        out.append(
            {
                "day": str(row.get("day") or "")[:10],
                "scope_id": sid,
                "cloud": scope_vals["cloud_origem"],
                "subaccount_name": scope_vals["subaccountname"],
                "resource_group": scope_vals["resource_group"],
                "service_name": scope_vals["servicename"],
                "service_category": scope_vals["servicesubcategory"],
                "charge_category": scope_vals["chargecategory"],
                "pricing_model": scope_vals["pricingcategory"],
                "commitment_type": scope_vals["commitmentdiscounttype"],
                "region": scope_vals["region"],
                "currency": str(row.get("currency") or config.get("currency") or "BRL"),
                "cost_cloud": float(row.get("cost_cloud") or 0.0),
                "pricing_quantity": float(row.get("pricing_quantity") or 0.0),
                "resource_count": int(row.get("resource_count") or 0),
                "row_count": int(row.get("row_count") or 0),
                "source_snapshot": str(config.get("source_snapshot") or ""),
                "loaded_at": loaded_at,
            }
        )
    logger.info(
        "[anomaly_v2] daily_build %s..%s -> %s scope-days",
        incremental_start,
        as_of,
        len(out),
    )
    return {
        "rows": out,
        "incremental_start": incremental_start.isoformat(),
        "end": as_of.isoformat(),
        "history_start": start.isoformat(),
        "row_count": len(out),
    }


async def load_daily_history(
    config: Mapping[str, Any],
    *,
    as_of: date,
    history_days: int = 90,
) -> list[dict[str, Any]]:
    """Carrega histórico para detecção (pode usar cache Iceberg quando publicado)."""
    start = as_of - timedelta(days=max(1, history_days) - 1)
    columns = await table_columns(
        str(config.get("source_namespace") or "silver"),
        str(config["destination_physical_name"]),
    )
    period_column = config.get("period_column")
    # Uma consulta por mês de faturamento: a janela inteira de uma vez estoura a
    # memória do worker do Trino.
    batches: list[list[str] | None] = (
        [[month] for month in billing_periods_for(start, as_of)] if period_column else [None]
    )
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for batch in batches:
        sql = daily_aggregate_sql(
            source_namespace=str(config.get("source_namespace") or "silver"),
            table=str(config["destination_physical_name"]),
            date_column=str(config["date_column"]),
            period_column=period_column,
            type_column=config.get("type_column"),
            measure_column=str(config.get("measure_column") or "realizado_cloud"),
            start=start,
            end=as_of,
            columns=columns,
            billing_periods=batch,
        )
        rows = await trino_execute(sql, timeout_s=1200)
        for row in rows:
            scope_vals = scope_values_from_row(row)
            day = str(row.get("day") or "")[:10]
            sid = scope_id_for(scope_vals)
            cost = float(row.get("cost_cloud") or 0.0)
            existing = merged.get((day, sid))
            if existing is None:
                merged[(day, sid)] = {"day": day, "scope_id": sid, **scope_vals, "cost_cloud": cost}
            else:
                existing["cost_cloud"] += cost
        logger.info("[anomaly_v2] histórico período %s: %s linhas", batch, len(rows))
    return list(merged.values())
