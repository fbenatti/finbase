"""Série diária de um caso v2, com o caminho organograma → assinatura → recurso."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Mapping

from app.services.anomaly_v2.baseline import expected_for_day
from app.services.anomaly_v2.daily_build import billing_periods_for, table_columns
from app.services.forecast_service import gold_date_expr
from app.services.trino_service import execute as trino_execute

logger = logging.getLogger(__name__)

# Coluna física → chave que o drilldown do frontend já conhece.
_BREAKDOWN_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("organograma_nivel_1", "organograma_nivel_1", "Organograma · nível 1"),
    ("organograma_nivel_2", "organograma_nivel_2", "Organograma · nível 2"),
    ("organograma_nivel_3", "organograma_nivel_3", "Organograma · nível 3"),
    ("organograma_nivel_4", "organograma_nivel_4", "Organograma · nível 4"),
    ("organograma_nivel_5", "organograma_nivel_5", "Organograma · nível 5"),
    ("organograma_nivel_6", "organograma_nivel_6", "Organograma · nível 6"),
    ("organograma_nivel_7", "organograma_nivel_7", "Organograma · nível 7"),
    ("subaccountname", "subaccountname", "Assinatura / subconta"),
    ("subaccountid", "subscription_id", "ID da assinatura"),
    ("source_resource_group_name", "resource_group", "Resource group"),
    ("resourcename", "resource_name", "Recurso"),
    ("resourceid", "resource_id", "ID do recurso"),
    ("servicesubcategory", "servicesubcategory", "Produto / família"),
    ("skumeter", "skumeter", "SKU / medidor"),
    ("regionname", "region", "Região"),
)

_DIRECT_FILTERS = (
    "cloud_origem",
    "subaccountname",
    "servicename",
    "servicesubcategory",
    "chargecategory",
    "pricingcategory",
    "commitmentdiscounttype",
)


def _quote(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _lit(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _scope_sql_filter(dimensions: Mapping[str, Any]) -> str:
    clauses = ["TRUE"]
    for key in _DIRECT_FILTERS:
        raw = dimensions.get(key)
        if raw in (None, ""):
            continue
        clauses.append(
            f"lower(trim(CAST({_quote(key)} AS VARCHAR))) = {_lit(str(raw).strip().lower())}"
        )
    resource_group = dimensions.get("resource_group")
    if resource_group:
        clauses.append(
            "lower(trim(CAST(\"source_resource_group_name\" AS VARCHAR))) = "
            + _lit(str(resource_group).strip().lower())
        )
    region = dimensions.get("region")
    if region:
        clauses.append(
            "lower(trim(COALESCE(CAST(\"regionname\" AS VARCHAR), ''))) = "
            + _lit(str(region).strip().lower())
        )
    return " AND ".join(clauses)


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def breakdown_from_rows(
    rows: list[Mapping[str, Any]],
    specs: list[tuple[str, str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, Any]], dict[str, str]]:
    """Monta o caminho único e os ramos (organograma, assinatura, recurso)."""
    detail_dimensions = [{"key": key, "label": label} for _column, key, label in specs]
    detail_items: list[dict[str, Any]] = []
    for row in rows:
        dimensions = {
            key: _clean(row.get(key))
            for _column, key, _label in specs
        }
        cost = row.get("observed_cloud")
        detail_items.append({
            "dimensions": dimensions,
            "observed_cloud": None if cost is None else float(cost),
        })
    common: dict[str, str] = {}
    for _column, key, _label in specs:
        values = {item["dimensions"].get(key) for item in detail_items}
        values.discard(None)
        if len(values) == 1:
            common[key] = next(iter(values))
    return detail_dimensions, detail_items, common


async def _load_scope_breakdown(
    *,
    namespace: str,
    table: str,
    date_expr: str,
    type_filter: str,
    period_column: str | None,
    measure: str,
    scope_filter: str,
    start: date,
    end: date,
) -> tuple[list[dict[str, str]], list[dict[str, Any]], dict[str, str]]:
    columns = {name.lower() for name in await table_columns(namespace, table)}
    specs = [item for item in _BREAKDOWN_COLUMNS if item[0] in columns]
    if not specs:
        return [], [], {}
    projection = ", ".join(
        f"CAST({_quote(column)} AS VARCHAR) AS {_quote(key)}"
        for column, key, _label in specs
    )
    groups = ", ".join(str(index + 1) for index in range(len(specs)))
    periods = billing_periods_for(start, end)
    period_filter = (
        f"CAST({_quote(period_column)} AS VARCHAR) IN ({', '.join(_lit(month) for month in periods)})"
        if period_column
        else "TRUE"
    )
    sql = f"""
        SELECT {projection},
               SUM(COALESCE(TRY_CAST({_quote(measure)} AS DOUBLE), 0)) AS observed_cloud
          FROM iceberg.{namespace}.{_quote(table)}
         WHERE {type_filter}
           AND {period_filter}
           AND {scope_filter}
           AND {date_expr} BETWEEN DATE {_lit(start.isoformat())} AND DATE {_lit(end.isoformat())}
         GROUP BY {groups}
         ORDER BY ABS(SUM(COALESCE(TRY_CAST({_quote(measure)} AS DOUBLE), 0))) DESC
         LIMIT 200
    """
    rows = await trino_execute(sql, timeout_s=90)
    return breakdown_from_rows(list(rows), specs)


async def read_v2_case_series(case: Any, config: Any, *, as_of: date) -> dict[str, Any]:
    """Realizado diário do mês do caso. A comparação do caso vem dos valores já gravados."""
    dimensions = dict(case.series_dimensions or {})
    try:
        event_day = date.fromisoformat(str(case.event_date)[:10])
    except ValueError:
        event_day = as_of
    start = event_day - timedelta(days=45)
    end = max(as_of, event_day)
    namespace = str(config.source_namespace or "silver")
    table = str(config.destination_physical_name)
    period_column = config.period_column
    type_column = config.type_column
    date_expr = gold_date_expr(str(config.date_column), period_column)
    type_filter = (
        f"({_quote(type_column)} = 'realizado' OR {_quote(type_column)} IS NULL)"
        if type_column
        else "TRUE"
    )
    measure = str(config.measure_column or "realizado_cloud")
    scope_filter = _scope_sql_filter(dimensions)
    by_day: dict[str, float] = {}
    for month in billing_periods_for(start, end):
        period_filter = (
            f"CAST({_quote(period_column)} AS VARCHAR) = {_lit(month)}"
            if period_column
            else "TRUE"
        )
        sql = f"""
            SELECT CAST({date_expr} AS VARCHAR) AS day,
                   SUM(COALESCE(TRY_CAST({_quote(measure)} AS DOUBLE), 0)) AS cost_cloud
              FROM iceberg.{namespace}.{_quote(table)}
             WHERE {type_filter}
               AND {period_filter}
               AND {scope_filter}
             GROUP BY 1
        """
        for row in await trino_execute(sql, timeout_s=60):
            day = str(row.get("day") or "")[:10]
            if start.isoformat() <= day <= end.isoformat():
                by_day[day] = by_day.get(day, 0.0) + float(row.get("cost_cloud") or 0.0)

    scope_rows = [{"day": day, "scope_id": str(case.series_id or ""), "cost_cloud": cost} for day, cost in by_day.items()]
    monthly = str(getattr(case, "detector", "") or getattr(case, "event_type", "")) == "MONTHLY_ACCUMULATED"
    items: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end:
        obs = by_day.get(cursor.isoformat()) if cursor <= as_of else None
        expected, lower, upper, _mad = expected_for_day(scope_rows, target=cursor)
        has_history = any(day < cursor.isoformat() and cost > 0 for day, cost in by_day.items())
        comparable = has_history and obs is not None
        items.append({
            "event_date": cursor.isoformat(),
            "weekday": cursor.weekday(),
            "observed_cloud": obs,
            "raw_observed_cloud": obs,
            "expected_cloud": round(expected, 4) if comparable else None,
            "lower_cloud": round(lower, 4) if comparable else None,
            "upper_cloud": round(upper, 4) if comparable else None,
            "delta_cloud": round(obs - expected, 4) if comparable else None,
            "anomalous_cost_cloud": round(max(0.0, obs - upper), 4) if comparable else None,
            "above_band": bool(obs > upper) if comparable else False,
            "comparison_supported": comparable,
            "is_future": cursor > as_of,
            "phase": "OWN_BASELINE" if comparable else "HISTORY",
            "reference_kind": "V2_BASELINE",
        })
        cursor += timedelta(days=1)

    breakdown_start = event_day.replace(day=1) if monthly else event_day
    breakdown_end = min(event_day, as_of)
    detail_dimensions: list[dict[str, str]] = []
    detail_items: list[dict[str, Any]] = []
    common: dict[str, str] = {}
    if breakdown_start <= breakdown_end:
        try:
            detail_dimensions, detail_items, common = await _load_scope_breakdown(
                namespace=namespace,
                table=table,
                date_expr=date_expr,
                type_filter=type_filter,
                period_column=period_column,
                measure=measure,
                scope_filter=scope_filter,
                start=breakdown_start,
                end=breakdown_end,
            )
        except Exception as exc:
            logger.warning("decomposição do caso %s indisponível: %s", getattr(case, "id", None), exc)
    member_dimensions = {**{key: value for key, value in dimensions.items() if value not in (None, "")}, **common}
    event_point = next((point for point in items if point["event_date"] == event_day.isoformat()), {})

    return {
        "items": items,
        "analysis_data_end": as_of.isoformat(),
        "context_start": start.isoformat(),
        "context_end": end.isoformat(),
        "context_event_date": event_day.isoformat(),
        "evaluation_grain": "MONTHLY_EVENT" if monthly else "DAY",
        "warnings": [
            "A comparação deste caso é o acumulado do mês contra a média dos meses anteriores, não o valor de um dia."
        ] if monthly else ([] if by_day else ["Não há realizado neste escopo no mês do caso."]),
        "contributor_series_count": 1,
        "contributor_series": [{"series_id": str(case.series_id or ""), "dimensions": member_dimensions}],
        "contributor_breakdown": [{
            "series_id": str(case.series_id or ""),
            "dimensions": member_dimensions,
            "event_date": event_day.isoformat(),
            "observed_cloud": event_point.get("observed_cloud"),
            "expected_cloud": event_point.get("expected_cloud"),
            "delta_cloud": event_point.get("delta_cloud"),
            "items": items,
        }],
        "detail_dimensions": detail_dimensions,
        "detail_items": detail_items,
        "series_dimensions": dimensions,
        "reference": {"reference_kind": "V2_BASELINE"},
        "returned_to_baseline": False,
    }
