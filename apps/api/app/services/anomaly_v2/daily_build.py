"""Agregado diário incremental (gold.anomaly_cost_daily)."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping

from app.services.anomaly_v2.scope import scope_id_for, scope_values_from_row
from app.services.forecast_service import gold_date_expr
from app.services.trino_service import execute as trino_execute

logger = logging.getLogger(__name__)

INCREMENTAL_DAYS = 5
MODEL_VERSION = "v2.0"


def _quote(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _lit(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


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
) -> str:
    date_expr = gold_date_expr(date_column, period_column)
    type_filter = (
        f"({_quote(type_column)} = 'realizado' OR {_quote(type_column)} IS NULL)"
        if type_column
        else "TRUE"
    )
    rg_expr = (
        "COALESCE("
        "CAST(\"source_resource_group_name\" AS VARCHAR), "
        "CAST(\"resourcegroupname\" AS VARCHAR), "
        "CAST(\"resource_group_name\" AS VARCHAR), "
        "''"
        ")"
    )
    region_expr = (
        "COALESCE(CAST(\"regionname\" AS VARCHAR), CAST(\"location\" AS VARCHAR), '')"
    )
    return f"""
        WITH parsed AS (
            SELECT CAST("cloud_origem" AS VARCHAR) AS cloud_origem,
                   CAST("subaccountname" AS VARCHAR) AS subaccountname,
                   {rg_expr} AS resource_group,
                   CAST("servicename" AS VARCHAR) AS servicename,
                   CAST("servicesubcategory" AS VARCHAR) AS servicesubcategory,
                   CAST("chargecategory" AS VARCHAR) AS chargecategory,
                   CAST("pricingcategory" AS VARCHAR) AS pricingcategory,
                   CAST("commitmentdiscounttype" AS VARCHAR) AS commitmentdiscounttype,
                   {region_expr} AS region,
                   CAST("billingcurrency" AS VARCHAR) AS currency,
                   {date_expr} AS day,
                   COALESCE(TRY_CAST({_quote(measure_column)} AS DOUBLE), 0) AS cost_cloud,
                   COALESCE(TRY_CAST("pricingquantity" AS DOUBLE), 0) AS pricing_quantity,
                   COALESCE(TRY_CAST("resourceid" AS VARCHAR), '') AS resource_id
              FROM iceberg.{source_namespace}.{_quote(table)}
             WHERE {type_filter}
        ),
        scoped AS (
            SELECT day,
                   cloud_origem, subaccountname, resource_group, servicename,
                   servicesubcategory, chargecategory, pricingcategory,
                   commitmentdiscounttype, region, currency,
                   SUM(cost_cloud) AS cost_cloud,
                   SUM(pricing_quantity) AS pricing_quantity,
                   COUNT(DISTINCT resource_id) AS resource_count,
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
    sql = daily_aggregate_sql(
        source_namespace=str(config.get("source_namespace") or "silver"),
        table=str(config["destination_physical_name"]),
        date_column=str(config["date_column"]),
        period_column=config.get("period_column"),
        type_column=config.get("type_column"),
        measure_column=str(config.get("measure_column") or "realizado_cloud"),
        start=incremental_start,
        end=as_of,
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
    sql = daily_aggregate_sql(
        source_namespace=str(config.get("source_namespace") or "silver"),
        table=str(config["destination_physical_name"]),
        date_column=str(config["date_column"]),
        period_column=config.get("period_column"),
        type_column=config.get("type_column"),
        measure_column=str(config.get("measure_column") or "realizado_cloud"),
        start=start,
        end=as_of,
    )
    rows = await trino_execute(sql, timeout_s=1200)
    result: list[dict[str, Any]] = []
    for row in rows:
        scope_vals = scope_values_from_row(row)
        result.append(
            {
                "day": str(row.get("day") or "")[:10],
                "scope_id": scope_id_for(scope_vals),
                **scope_vals,
                "cost_cloud": float(row.get("cost_cloud") or 0.0),
            }
        )
    return result
