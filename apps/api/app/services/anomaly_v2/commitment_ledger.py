"""Livro de compromissos (reservas, savings plans) por resource_id."""
from __future__ import annotations

import hashlib
import logging
import statistics
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence

from app.services.anomaly_v2.scope import scope_id_for, scope_values_from_row
from app.services.forecast_service import gold_date_expr
from app.services.trino_service import execute as trino_execute

logger = logging.getLogger(__name__)

PURCHASE_CHARGE = "purchase"
RESERVATION = "reservation"


def _quote(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _lit(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def commitment_source_sql(
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
    return f"""
        SELECT {date_expr} AS day,
               CAST("cloud_origem" AS VARCHAR) AS cloud_origem,
               CAST("subaccountname" AS VARCHAR) AS subaccountname,
               CAST("servicename" AS VARCHAR) AS servicename,
               CAST("chargecategory" AS VARCHAR) AS chargecategory,
               CAST("commitmentdiscounttype" AS VARCHAR) AS commitmentdiscounttype,
               COALESCE(CAST("resourceid" AS VARCHAR), '') AS resource_id,
               COALESCE(CAST("resourcename" AS VARCHAR), '') AS resource_name,
               COALESCE(TRY_CAST({_quote(measure_column)} AS DOUBLE), 0) AS cost_cloud
          FROM iceberg.{source_namespace}.{_quote(table)}
         WHERE {type_filter}
           AND LOWER(COALESCE(CAST("chargecategory" AS VARCHAR), '')) = 'purchase'
           AND {date_expr} BETWEEN DATE {_lit(start.isoformat())} AND DATE {_lit(end.isoformat())}
           AND COALESCE(TRY_CAST({_quote(measure_column)} AS DOUBLE), 0) > 0
    """


def _commitment_id(resource_id: str) -> str:
    return hashlib.sha1(resource_id.encode("utf-8")).hexdigest()


def build_ledger_from_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Agrupa cobranças por ordem de reserva (resource_id)."""
    by_resource: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        rid = str(row.get("resource_id") or "").strip()
        if not rid or float(row.get("cost_cloud") or 0) <= 0:
            continue
        by_resource[rid].append(row)

    updated_at = datetime.now(timezone.utc).isoformat()
    ledger: list[dict[str, Any]] = []
    for resource_id, events in by_resource.items():
        amounts = [float(e.get("cost_cloud") or 0) for e in events]
        days = sorted({str(e.get("day"))[:10] for e in events if e.get("day")})
        if not days:
            continue
        sample = events[0]
        scope_vals = scope_values_from_row(sample)
        sid = scope_id_for(scope_vals)
        anniversary_days = sorted({int(d[8:10]) for d in days if len(d) >= 10})
        median_amount = statistics.median(amounts)
        deviations = [abs(a - median_amount) for a in amounts]
        mad = statistics.median(deviations) if deviations else 0.0
        last_day = max(days)
        kind = str(sample.get("commitmentdiscounttype") or "Reservation")
        ledger.append(
            {
                "commitment_id": _commitment_id(resource_id),
                "scope_id": sid,
                "kind": kind,
                "description": str(sample.get("resource_name") or resource_id)[:500],
                "cloud": scope_vals["cloud_origem"],
                "subaccount_name": scope_vals["subaccountname"],
                "anniversary_day": anniversary_days[0] if len(anniversary_days) == 1 else 0,
                "anniversary_days": anniversary_days,
                "expected_amount": median_amount,
                "amount_mad": mad,
                "last_charge_date": last_day,
                "next_expected_date": "",
                "charges_seen": len(events),
                "status": "ACTIVE",
                "updated_at": updated_at,
                "resource_id": resource_id,
            }
        )
    return ledger


async def refresh_commitment_ledger(
    config: Mapping[str, Any],
    *,
    as_of: date,
    history_days: int = 400,
) -> dict[str, Any]:
    from datetime import timedelta

    start = as_of - timedelta(days=history_days)
    sql = commitment_source_sql(
        source_namespace=str(config.get("source_namespace") or "silver"),
        table=str(config["destination_physical_name"]),
        date_column=str(config["date_column"]),
        period_column=config.get("period_column"),
        type_column=config.get("type_column"),
        measure_column=str(config.get("measure_column") or "realizado_cloud"),
        start=start,
        end=as_of,
    )
    rows = await trino_execute(sql, timeout_s=900)
    parsed = [
        {
            "day": str(r.get("day") or "")[:10],
            "resource_id": r.get("resource_id"),
            "resource_name": r.get("resource_name"),
            "cost_cloud": r.get("cost_cloud"),
            **scope_values_from_row(r),
            "commitmentdiscounttype": r.get("commitmentdiscounttype"),
        }
        for r in rows
    ]
    ledger = build_ledger_from_rows(parsed)
    logger.info("[anomaly_v2] commitment_ledger entries=%s", len(ledger))
    return {"entries": ledger, "row_count": len(ledger)}
