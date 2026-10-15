"""Série diária de um caso, derivada da referência compacta (F2).

A grade de 15 dias **não** é persistida (plano §4.2). Aqui ela é derivada, sob
demanda, de duas fontes:

* a **referência compacta** — uma linha por série × mês, com valor previsto,
  pesos por dia da semana e offsets de faixa;
* a **fonte observada** — `realizado_cloud` por dia, pelo mesmo parser do
  forecast (`gold_date_expr`).

```text
esperado_dia = previsto_mês × peso[dia da semana]
limite_sup   = esperado_dia + offset diário da faixa
custo_anômalo = max(0, observado_dia − limite_sup)
```

Nada é copiado para o PostgreSQL: a leitura é feita no Iceberg/Trino por
`series_id` do caso.
"""
from __future__ import annotations

import asyncio
import json
import logging
import statistics
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.services.anomaly_observed import observed_daily_sql
from app.services.forecast_service import gold_date_expr
from app.services.trino_service import execute as trino_execute

logger = logging.getLogger(__name__)
_DETAIL_SCHEMA_CACHE: dict[tuple[str, str], tuple[float, list[str]]] = {}
_DETAIL_SCHEMA_TTL_SECONDS = 300

# Drilldown adicional, apenas quando a coluna existe na fonte configurada.
# O forecast continua sendo comparável somente no grão de suas séries.
DETAIL_DIMENSIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("subscription", "Assinatura / subconta", ("subscriptionname", "subaccountname", "subscription_name")),
    ("subscription_id", "ID da assinatura", ("subscriptionid", "subaccountid", "subscription_id", "sub_account_id")),
    ("resource_group", "Resource group", ("source_resource_group_name", "resourcegroupname", "resource_group_name", "resource_group_id", "resourcegroup")),
    ("resource_id", "ID do recurso", ("resourceid", "resource_id", "source_resource_id")),
    ("resource_name", "Recurso", ("resourcename", "resource_name", "source_resource_name")),
    ("gcp_project", "Projeto GCP", ("projectid", "projectname", "project_id", "project_name", "projectnumber")),
    ("service", "Serviço", ("servicename", "service_name")),
    ("service_category", "Categoria FOCUS", ("servicesubcategory", "servicecategory", "service_category", "service_subcategory")),
    ("sku", "SKU / medidor", ("skumeter", "metername", "resource_sku", "sku_name", "skuid", "meterid")),
    ("region", "Região", ("regionname", "region_id", "location", "region")),
)


def resolve_detail_dimensions(columns: Sequence[str], existing: Sequence[str]) -> list[dict[str, str]]:
    """Resolve nomes existentes sem exigir que um provedor tenha todos os campos."""
    available = {str(column).lower(): str(column) for column in columns}
    used = {str(column).lower() for column in existing}
    resolved: list[dict[str, str]] = []
    for key, label, candidates in DETAIL_DIMENSIONS:
        physical = next((available[name] for name in candidates if name in available and name not in used), None)
        if physical:
            resolved.append({"key": key, "label": label, "column": physical})
            used.add(physical.lower())
    resource_id = next((item for item in resolved if item["key"] == "resource_id"), None)
    if resource_id and not any(item["key"] == "resource_group" for item in resolved):
        resolved.insert(
            next(index for index, item in enumerate(resolved) if item["key"] == "resource_id"),
            {"key": "resource_group", "label": "Resource group (do ResourceId Azure)",
             "expression": f"regexp_extract(CAST({_quote(resource_id['column'])} AS VARCHAR), '(?i)/resourceGroups/([^/]+)', 1)"},
        )
    return resolved


def detailed_event_sql(*, source_namespace: str, source_table: str, date_column: str,
                       period_column: str | None, type_column: str | None,
                       measure_column: str, dimensions: Sequence[str],
                       detail_dimensions: Sequence[Mapping[str, str]],
                       member_filters: Sequence[str], event_day: date) -> str:
    dim_select = [_quote(name) for name in dimensions]
    dim_aliases = [f"{item.get('expression') or _quote(str(item['column']))} AS {_quote(item['key'])}" for item in detail_dimensions]
    # The CTE renames fine-grained columns; the outer query must use those
    # aliases instead of referring to physical source names that are out of scope.
    source_projection = ", ".join([*dim_select, *dim_aliases])
    output_projection = ", ".join([*dim_select, *(_quote(str(item['key'])) for item in detail_dimensions)])
    group_columns = ", ".join(str(index + 1) for index in range(len(dim_select) + len(dim_aliases)))
    date_expr = gold_date_expr(date_column, period_column)
    type_filter = f"({_quote(type_column)} = 'realizado' OR {_quote(type_column)} IS NULL)" if type_column else "TRUE"
    filters = [value for value in member_filters if value]
    scope = "(" + " OR ".join(f"({value})" for value in filters) + ")" if filters else "FALSE"
    return f"""
        WITH parsed AS (
            SELECT {source_projection}, {date_expr} AS day,
                   COALESCE(TRY_CAST({_quote(measure_column)} AS DOUBLE), 0) AS value
             FROM iceberg.{source_namespace}.{_quote(source_table)}
             WHERE {type_filter} AND {scope} AND {date_expr} = DATE {_lit(event_day.isoformat())}
        )
        SELECT {output_projection}, SUM(value) AS observed_cloud
          FROM parsed
         GROUP BY {group_columns}
         ORDER BY ABS(SUM(value)) DESC
         LIMIT 500
    """


async def _read_detail_schema(source_namespace: str, source_table: str) -> list[str]:
    key = (source_namespace, source_table)
    cached = _DETAIL_SCHEMA_CACHE.get(key)
    if cached and time.monotonic() - cached[0] < _DETAIL_SCHEMA_TTL_SECONDS:
        return cached[1]
    rows = await trino_execute(
        f"DESCRIBE iceberg.{source_namespace}.{_quote(source_table)}", timeout_s=30
    )
    columns = [str(row.get("Column") or row.get("column_name") or row.get("column") or "")
               for row in rows]
    _DETAIL_SCHEMA_CACHE[key] = (time.monotonic(), columns)
    return columns

#: Horizonte usado quando o caso não tem publicação com corte conhecido.
DEFAULT_HORIZON_DAYS = 15


def _quote(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _lit(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def series_ids_from(values: Sequence[Any]) -> list[str]:
    return [str(value) for value in values if value]


def reference_series_sql(
    *,
    namespace: str,
    reference_table: str,
    series_id: str,
    months: Sequence[str],
    series_ids: Sequence[str] = (),
) -> str:
    quoted_months = ", ".join(_lit(month) for month in months)
    series_filter = ("series_id IN (" + ", ".join(_lit(value) for value in series_ids) + ")") if series_ids else f"series_id = {_lit(series_id)}"
    return (
        f"SELECT reference_month, coverage_kind, coverage_start, coverage_end, covered_days, "
        f"predicted_cloud, weekday_weights, normalizer, distribution_method, "
        f"daily_band_lower_offset, daily_band_upper_offset, band_method, band_confidence, "
        f"reference_id, reference_kind, data_cutoff, series_id, forecast_run_id, generated_at, "
        f"target_start, target_end, band_lower_cloud, band_upper_cloud "
        f"FROM iceberg.{namespace}.{_quote(reference_table)} "
        f"WHERE {series_filter} "
        f"AND CAST(reference_month AS VARCHAR) IN ({quoted_months}) "
        f"ORDER BY reference_month"
    )


def _weekday_weights(raw: Any) -> dict[int, float]:
    if raw is None:
        return {}
    payload = raw
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    if not isinstance(payload, Mapping):
        return {}
    weights: dict[int, float] = {}
    for key, value in payload.items():
        try:
            weights[int(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return weights


def derive_daily_points(
    reference_rows: Sequence[Mapping[str, Any]],
    observed_by_day: Mapping[date, float],
    *,
    horizon_end: date,
    weekday_medians: Mapping[int, float] | None = None,
    frozen_reference_id: str | None = None,
) -> list[dict[str, Any]]:
    """Uma linha por dia coberto, com esperado, faixa e custo anômalo.

    Função pura: recebe a referência e o observado já lidos, o que a torna
    testável sem Trino.
    """
    by_day: dict[str, tuple[tuple, dict[str, Any]]] = {}
    frozen = next((row for row in reference_rows if row.get("reference_id") == frozen_reference_id), None)
    frozen_run = frozen.get("forecast_run_id") if frozen else None
    for row in reference_rows:
        start = row.get("coverage_start")
        end = row.get("coverage_end")
        if not start or not end:
            continue
        try:
            cursor = date.fromisoformat(str(start)[:10])
            last = date.fromisoformat(str(end)[:10])
        except ValueError:
            continue
        predicted = float(row.get("predicted_cloud") or 0.0)
        weights = _weekday_weights(row.get("weekday_weights"))
        upper_offset = float(row.get("daily_band_upper_offset") or 0.0)
        lower_offset = float(row.get("daily_band_lower_offset") or 0.0)
        covered = max(1, int(row.get("covered_days") or 1))
        cutoff = str(row.get("data_cutoff") or "")[:10]
        target_end = str(row.get("target_end") or "")[:10]
        frozen_row = bool(frozen_reference_id and (
            row.get("reference_id") == frozen_reference_id
            or (frozen_run is not None and row.get("forecast_run_id") == frozen_run)
        ))
        rank = (frozen_row, cutoff, int(row.get("forecast_run_id") or 0), str(row.get("reference_id") or ""))

        # Normaliza sobre TODO o intervalo coberto pela projeção (plano §2.3), e
        # não apenas sobre a janela exibida: o recorte do horizonte não pode
        # mudar o valor diário. O denominador é recalculado aqui para que a
        # distribuição feche com o valor do forecast mesmo que os pesos venham
        # das medianas brutas.
        denominator = 0.0
        probe = cursor
        while probe <= last:
            denominator += weights.get(probe.weekday(), 0.0)
            probe += timedelta(days=1)
        if denominator <= 0:
            denominator = 0.0

        while cursor <= last:
            if cursor > horizon_end:
                break
            day = cursor.isoformat()
            if (cutoff and day <= cutoff) or (target_end and day > target_end):
                cursor += timedelta(days=1)
                continue
            raw_weight = weights.get(cursor.weekday())
            historical_fallback = float((weekday_medians or {}).get(cursor.weekday(), 0.0))
            # O normalizador preservado permite recuperar a mediana original.
            # Recalibrar usando o histórico atual absorvia o próprio aumento.
            if row.get("distribution_method") == "WEEKDAY_MEDIAN" and raw_weight is not None and row.get("normalizer"):
                historical_fallback = raw_weight * float(row["normalizer"])
            if predicted <= 0 and historical_fallback > 0:
                expected = historical_fallback
                method = "HISTORICAL_BASELINE_ZERO_FORECAST"
            elif raw_weight is None or denominator <= 0:
                expected = predicted / covered
                method = "UNIFORM_FALLBACK"
            else:
                expected = predicted * (raw_weight / denominator)
                method = str(row.get("distribution_method") or "WEEKDAY_MEDIAN")
            observed = observed_by_day.get(cursor)
            upper = expected + upper_offset
            point = {
                    "event_date": day,
                    "weekday": cursor.weekday(),
                    "reference_month": row.get("reference_month"),
                    "reference_id": row.get("reference_id"),
                    "reference_kind": row.get("reference_kind"),
                    "coverage_kind": row.get("coverage_kind"),
                    "forecast_run_id": row.get("forecast_run_id"),
                    "forecast_cutoff": cutoff or None,
                    "monthly_forecast_cloud": predicted,
                    "monthly_lower_cloud": row.get("band_lower_cloud"),
                    "monthly_upper_cloud": row.get("band_upper_cloud"),
                    "distribution_method": method,
                    "expected_cloud": round(expected, 4),
                    "lower_cloud": round(max(0.0, expected - lower_offset), 4),
                    "upper_cloud": round(upper, 4),
                    "observed_cloud": None if observed is None else float(observed),
                    "delta_cloud": None if observed is None else float(observed) - round(expected, 4),
                    "anomalous_cost_cloud": (
                        None if observed is None else max(0.0, float(observed) - upper)
                    ),
                    "above_band": None if observed is None else float(observed) > upper,
                }
            if day not in by_day or rank > by_day[day][0]:
                by_day[day] = (rank, point)
            cursor += timedelta(days=1)
    return [by_day[day][1] for day in sorted(by_day)]


def _dimension_filter(dimensions: Mapping[str, Any]) -> str | None:
    clauses: list[str] = []
    for name, value in dimensions.items():
        column = _quote(name)
        if value is None or str(value) == "":
            clauses.append(f"COALESCE(CAST({column} AS VARCHAR), '') = ''")
        else:
            clauses.append(f"CAST({column} AS VARCHAR) = {_lit(str(value))}")
    return " AND ".join(clauses) if clauses else None


def aggregate_reference_points(points_by_series: Sequence[Sequence[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    """Soma referências do mesmo conjunto de séries que originou o caso.

    Uma previsão incompleta não pode parecer um forecast completo menor.
    """
    by_day: dict[str, list[Mapping[str, Any]]] = {}
    for points in points_by_series:
        for point in points:
            by_day.setdefault(point["event_date"], []).append(point)
    result = []
    for day, points in sorted(by_day.items()):
        if len(points) != len(points_by_series):
            continue
        merged = dict(points[0])
        for column in ("expected_cloud", "lower_cloud", "upper_cloud", "monthly_forecast_cloud", "monthly_lower_cloud", "monthly_upper_cloud"):
            values = [point.get(column) for point in points]
            merged[column] = sum(values) if all(value is not None for value in values) else None
        merged["coverage_kind"] = "FULL_MONTH" if all(point.get("coverage_kind") == "FULL_MONTH" for point in points) else "REMAINING_MONTH"
        merged["contributor_series_count"] = len(points)
        result.append(merged)
    return result


def compose_case_timeline(
    reference_points: Sequence[Mapping[str, Any]],
    observed_by_day: Mapping[date, float],
    *,
    start: date,
    end: date,
    data_end: date,
    monthly: bool = False,
) -> list[dict[str, Any]]:
    """Une histórico, comparação e projeção sem inventar realizado futuro.

    Compras usam acumulado em datas de eventos. Saldo restante mensal não é
    apresentado como expectativa completa anterior à compra.
    """
    references = {point["event_date"]: point for point in reference_points}
    items: list[dict[str, Any]] = []
    monthly_total: dict[str, float] = {}
    cursor = start
    while cursor <= end:
        day = cursor.isoformat()
        month = day[:7]
        raw_observed = observed_by_day.get(cursor) if cursor <= data_end else None
        observed = raw_observed
        if monthly and raw_observed is not None:
            monthly_total[month] = monthly_total.get(month, 0.0) + raw_observed
            observed = monthly_total[month]
        point = dict(references.get(day) or {})
        expected = point.get("expected_cloud")
        lower = point.get("lower_cloud")
        upper = point.get("upper_cloud")
        if monthly:
            supported = point.get("coverage_kind") == "FULL_MONTH"
            expected = point.get("monthly_forecast_cloud") if supported else None
            lower = point.get("monthly_lower_cloud") if supported else None
            upper = point.get("monthly_upper_cloud") if supported else None
        supported = expected is not None and upper is not None
        point.update({
            "event_date": day,
            "weekday": cursor.weekday(),
            "expected_cloud": expected,
            "lower_cloud": lower,
            "upper_cloud": upper,
            "observed_cloud": observed,
            "raw_observed_cloud": raw_observed,
            "delta_cloud": observed - expected if supported and observed is not None else None,
            "anomalous_cost_cloud": max(0.0, observed - upper) if supported and observed is not None else None,
            "above_band": observed > upper if supported and observed is not None else None,
            "comparison_supported": supported,
            "is_future": cursor > data_end,
            "phase": "FORECAST" if cursor > data_end else "COMPARISON" if supported else "HISTORY",
        })
        items.append(point)
        cursor += timedelta(days=1)
    return items


def apply_case_evaluation_snapshot(items: list[dict[str, Any]], case: Any) -> bool:
    """Restore the exact event comparison captured by the detector.

    Monthly remaining-balance forecasts cannot stand in for a full-month
    expectation. When their original comparison is unavailable in the current
    reference publication, the immutable case evaluation is the only honest
    event-level baseline and must be labelled as such.
    """
    event_day = str(getattr(case, "event_date", ""))[:10]
    point = next((item for item in items if item.get("event_date") == event_day), None)
    expected = getattr(case, "expected_cloud", None)
    upper = getattr(case, "upper_cloud", None)
    if point is None or expected is None or upper is None or point.get("comparison_supported"):
        return False
    observed = getattr(case, "observed_cloud", None)
    if observed is None:
        observed = point.get("observed_cloud")
    if observed is None:
        return False
    expected = float(expected)
    observed = float(observed)
    point.update({
        "observed_cloud": observed,
        "expected_cloud": expected,
        "lower_cloud": getattr(case, "lower_cloud", None),
        "upper_cloud": float(upper),
        "delta_cloud": float(getattr(case, "delta_cloud", None) if getattr(case, "delta_cloud", None) is not None else observed - expected),
        "anomalous_cost_cloud": float(getattr(case, "anomalous_cost_cloud", 0.0) or 0.0),
        "above_band": observed > float(upper),
        "comparison_supported": True,
        "phase": "CASE_EVALUATION",
        "reference_kind": "CASE_EVALUATION_SNAPSHOT",
        "is_case_evaluation_snapshot": True,
    })
    return True


def reconcile_forecast_with_history(items: list[dict[str, Any]], event_day: date) -> str | None:
    """Substitui forecast vazio ou incompatível pela mediana do próprio realizado.

    Um esperado perto de zero com histórico recorrente (reserva, data flow, OCPU)
    não é desvio. A faixa passa a ser o histórico do escopo.
    """
    event_key = event_day.isoformat()
    event = next((item for item in items if item.get("event_date") == event_key), None)
    prior = [
        float(item["observed_cloud"])
        for item in items
        if str(item.get("event_date") or "") < event_key and float(item.get("observed_cloud") or 0) > 0
    ]
    if event is None or len(prior) < 3:
        return None
    observed = event.get("observed_cloud")
    if observed is None:
        return None
    observed = float(observed)
    expected = event.get("expected_cloud")
    median = float(statistics.median(prior))
    if median <= 0:
        return None
    forecast_missing = expected in (None, 0) or float(expected) <= 0
    forecast_far_below_history = (
        expected not in (None, 0)
        and observed > float(expected) * 8
        and median > float(expected) * 3
    )
    points_missing_forecast = [
        item for item in items
        if item.get("observed_cloud") is not None and item.get("expected_cloud") in (None, 0)
    ]
    chart_empty = len(points_missing_forecast) >= max(3, len(items) // 2)
    if not forecast_missing and not forecast_far_below_history and not chart_empty:
        return None
    if not forecast_missing and not forecast_far_below_history:
        # Há comparação no dia do evento; só completa a linha onde o forecast não existe.
        for item in points_missing_forecast:
            item["expected_cloud"] = round(median, 4)
            item["comparison_supported"] = True
            item["reference_kind"] = "OWN_BASELINE"
        return "Parte da série não tinha forecast. A linha pontilhada usa a mediana do realizado deste escopo, sem tratar isso como economia."
    mad = statistics.median([abs(value - median) for value in prior]) or max(median * 0.1, 1.0)
    band = 1.4826 * mad * 3
    upper = median + band
    event.update({
        "expected_cloud": round(median, 4),
        "lower_cloud": round(max(0.0, median - band), 4),
        "upper_cloud": round(upper, 4),
        "delta_cloud": round(observed - median, 4),
        "anomalous_cost_cloud": round(max(0.0, observed - upper), 4),
        "above_band": observed > upper,
        "comparison_supported": True,
        "reference_kind": "OWN_BASELINE",
        "phase": "OWN_BASELINE",
    })
    for item in items:
        if item.get("expected_cloud") in (None, 0):
            item["expected_cloud"] = round(median, 4)
            item["upper_cloud"] = round(upper, 4)
            item["comparison_supported"] = True
            item["reference_kind"] = "OWN_BASELINE"
    return (
        "O forecast deste escopo não acompanha o histórico (esperado zerado ou muito abaixo do realizado recorrente). "
        "A faixa passou a ser a mediana do próprio custo, não uma previsão externa."
    )


def returned_to_baseline(items: list[dict[str, Any]], event_day: date) -> bool:
    after = [
        item for item in items
        if str(item.get("event_date") or "") > event_day.isoformat()
        and item.get("observed_cloud") is not None
        and item.get("above_band") is not None
    ]
    if len(after) < 3:
        return False
    return all(item.get("above_band") is False for item in after[-3:])


async def read_case_series(
    *,
    case: Any,
    publication: Any,
    reference_namespace: str,
    source_namespace: str,
    source_table: str,
    date_column: str,
    period_column: str | None,
    type_column: str | None,
    measure_column: str,
    dimensions: Sequence[str],
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    analysis_data_end: date | None = None,
    contributor_series: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Uma única linha do tempo: realizado até o cutoff e forecast além dele."""
    request_started = time.monotonic()
    cutoff = _cutoff_of(publication)
    data_end = analysis_data_end or (datetime.now(timezone.utc).date() - timedelta(days=2))
    raw_day = getattr(case, "event_date", None)
    try:
        event_day = date.fromisoformat(str(raw_day)[:10])
    except (ValueError, TypeError):
        event_day = cutoff
    monthly = str(getattr(case, "evaluation_grain", "")) == "MONTHLY_EVENT"
    # Reservas e eventos mensais precisam dos meses anteriores; 30 dias escondem o padrão.
    context_start = event_day - timedelta(days=120 if monthly else 45)
    requested_end = data_end + timedelta(days=max(1, int(horizon_days)))
    months = _months_for(context_start, max(1, (requested_end - context_start).days))
    members = list(contributor_series) or [{"series_id": str(case.series_id), "dimensions": dict(getattr(case, "series_dimensions", None) or {})}]
    reference_task = asyncio.create_task(trino_execute(
        reference_series_sql(
            namespace=reference_namespace,
            reference_table=publication.reference_table,
            series_id=str(case.series_id),
            months=months,
            series_ids=[str(member["series_id"]) for member in members],
        ), timeout_s=180,
    ))
    member_filters = [_dimension_filter(member["dimensions"]) for member in members]
    member_filters = [value for value in member_filters if value]
    observed_task = asyncio.create_task(asyncio.sleep(0, result=[]))
    if context_start <= data_end:
        observed_sql = observed_daily_sql(
            source_namespace=source_namespace, table=source_table,
            date_column=date_column, period_column=period_column,
            type_column=type_column, measure_column=measure_column,
            dimensions=list(dimensions), start=context_start, end=data_end,
            series_filters=member_filters,
        )
        observed_task = asyncio.create_task(trino_execute(observed_sql, timeout_s=600))
    schema_task = asyncio.create_task(asyncio.sleep(0, result=[]))
    if event_day <= data_end and contributor_series:
        schema_task = asyncio.create_task(_read_detail_schema(source_namespace, source_table))
    query_results = await asyncio.gather(reference_task, observed_task, schema_task, return_exceptions=True)
    parallel_queries_ms = round((time.monotonic() - request_started) * 1000)
    for result in query_results[:2]:
        if isinstance(result, BaseException):
            raise result
    rows, observed_rows, schema_rows = query_results
    if isinstance(schema_rows, BaseException):
        logger.warning("[anomaly] schema das dimensões não disponível para o caso %s: %s", getattr(case, "id", None), schema_rows)
        schema_rows = []
    frozen_id = getattr(case, "current_reference_id", None)
    frozen = next((row for row in rows if row.get("reference_id") == frozen_id), None)
    warnings: list[str] = []
    if frozen_id and frozen is None:
        warnings.append("A referência original do caso não está na publicação atual. O forecast não foi substituído por uma referência posterior.")
        rows = []
    elif frozen is None and rows:
        eligible = [row for row in rows if str(row.get("data_cutoff") or "")[:10] < event_day.isoformat()]
        frozen = max(eligible, key=lambda row: (str(row.get("data_cutoff") or ""), int(row.get("forecast_run_id") or 0))) if eligible else None
        frozen_id = frozen.get("reference_id") if frozen else None
    forecast_rows = [row for row in rows if frozen and row.get("forecast_run_id") == frozen.get("forecast_run_id")]
    forecast_ends = [str(row.get("target_end") or row.get("coverage_end") or "")[:10] for row in forecast_rows]
    forecast_ends = [date.fromisoformat(value) for value in forecast_ends if value]
    forecast_end = min(max(forecast_ends), requested_end) if forecast_ends else data_end
    if monthly and frozen and frozen.get("coverage_kind") != "FULL_MONTH":
        forecast_end = data_end
    display_end = max(data_end, forecast_end)
    series_dimensions = dict(getattr(case, "series_dimensions", None) or {})
    observed_by_day: dict[date, float] = {}
    observed_by_member: list[dict[date, float]] = [{} for _ in members]
    if context_start <= data_end:
        for row in observed_rows:
            try:
                day = date.fromisoformat(str(row.get("event_date"))[:10])
            except (ValueError, TypeError):
                continue
            value = row.get("observed_cloud")
            if day <= data_end and value is not None:
                observed_by_day[day] = observed_by_day.get(day, 0.0) + float(value)
                for index, member in enumerate(members):
                    if all(row.get(key) == dimension for key, dimension in member["dimensions"].items()):
                        member_days = observed_by_member[index]
                        member_days[day] = member_days.get(day, 0.0) + float(value)
                        break
    points_by_series = []
    for member in members:
        member_rows = [row for row in rows if row.get("series_id") == member["series_id"]]
        # Legacy test/adapter rows can omit series_id when only one series is read.
        if len(members) == 1 and not member_rows:
            member_rows = rows
        member_frozen = next((row for row in member_rows if frozen and row.get("forecast_run_id") == frozen.get("forecast_run_id")), None)
        points_by_series.append(derive_daily_points(member_rows, {}, horizon_end=display_end,
                               frozen_reference_id=member_frozen.get("reference_id") if member_frozen else None))
    reference_points = aggregate_reference_points(points_by_series)
    # A previsão futura continua a referência congelada do episódio. Referências
    # mais novas não podem absorver o desvio do caso em andamento.
    if frozen:
        reference_points = [point for point in reference_points if point["event_date"] <= data_end.isoformat()
                            or point.get("forecast_run_id") == frozen.get("forecast_run_id")]
    items = compose_case_timeline(reference_points, observed_by_day, start=context_start,
                                 end=display_end, data_end=data_end, monthly=monthly)
    used_case_snapshot = apply_case_evaluation_snapshot(items, case)
    detail_dimensions: list[dict[str, str]] = []
    detail_items: list[dict[str, Any]] = []
    # O custo real pode ter granularidade mais fina que o forecast. Isso localiza
    # o recurso no dia do evento, mas não inventa forecast/desvio nesse nível.
    if event_day <= data_end and contributor_series and schema_rows:
        detail_started = time.monotonic()
        try:
            source_columns = [
                str(row.get("Column") or row.get("column_name") or row.get("column") or "")
                if isinstance(row, Mapping) else str(row)
                for row in schema_rows
            ]
            detail_dimensions = resolve_detail_dimensions(source_columns, dimensions)
            if detail_dimensions:
                detail_rows = await trino_execute(
                    detailed_event_sql(
                        source_namespace=source_namespace, source_table=source_table,
                        date_column=date_column, period_column=period_column, type_column=type_column,
                        measure_column=measure_column, dimensions=dimensions,
                        detail_dimensions=detail_dimensions, member_filters=member_filters, event_day=event_day,
                    ), timeout_s=180,
                )
                detail_items = [{
                    "dimensions": {
                        **{str(name).lower(): row.get(str(name).lower(), row.get(str(name))) for name in dimensions},
                        **{item["key"]: row.get(item["key"]) for item in detail_dimensions},
                    },
                    "observed_cloud": None if row.get("observed_cloud") is None else float(row["observed_cloud"]),
                } for row in detail_rows]
        except Exception as exc:
            logger.warning("[anomaly] decomposição fina indisponível para o caso %s: %s", getattr(case, "id", None), exc)
        finally:
            logger.info("[anomaly] case_series case=%s parallel_queries_ms=%s detail_query_ms=%s rows=%s dimensions=%s",
                        getattr(case, "id", None), parallel_queries_ms,
                        round((time.monotonic() - detail_started) * 1000), len(detail_items), len(detail_dimensions))
    else:
        logger.info("[anomaly] case_series case=%s parallel_queries_ms=%s detail_query_ms=0 rows=0 dimensions=0",
                    getattr(case, "id", None), parallel_queries_ms)
    contributor_breakdown = []
    for index, member in enumerate(members):
        timeline = compose_case_timeline(points_by_series[index], observed_by_member[index],
                                         start=context_start, end=display_end, data_end=data_end, monthly=monthly)
        if len(members) == 1:
            apply_case_evaluation_snapshot(timeline, case)
        event_point = next((point for point in timeline if point["event_date"] == event_day.isoformat()), {})
        contributor_breakdown.append({**member, "event_date": event_day.isoformat(),
                                      "items": timeline,
                                      **{key: event_point.get(key) for key in
                                         ("observed_cloud", "expected_cloud", "delta_cloud")}})
    if monthly and frozen and frozen.get("coverage_kind") != "FULL_MONTH":
        if used_case_snapshot:
            warnings.append("Comparação do evento restaurada da avaliação original do caso. A referência publicada contém apenas o saldo restante do mês (não a expectativa mensal completa); o gráfico usa o forecast e observado congelados na detecção para esta data.")
        else:
            warnings.append("Este forecast contém o saldo restante do mês, não a expectativa mensal congelada antes da compra. A comparação desta compra está indisponível; o saldo não foi distribuído como consumo diário nem usado como zero esperado.")
    if event_day > data_end:
        warnings.append(f"O evento é posterior ao cutoff de dados ({data_end.isoformat()}).")
    history_note = reconcile_forecast_with_history(items, event_day)
    if history_note:
        warnings.append(history_note)
    healed = returned_to_baseline(items, event_day)
    if healed:
        warnings.append(
            "Depois do dia do evento o custo voltou à faixa por pelo menos 3 dias. "
            "Isso entra como desvio autocorrigido, sem economia causal atribuída."
        )
    if not rows and not warnings:
        warnings.append("Não há referência de forecast disponível para esta série.")
    if not contributor_series:
        warnings.append("Esta avaliação antiga não preservou as séries contribuintes. O gráfico está limitado à série representativa; reprocessar a detecção permite reconciliar o escopo completo.")
    comparable = [point for point in items if point["comparison_supported"]]
    reference = None if not frozen else {
        "reference_id": frozen.get("reference_id"), "forecast_run_id": frozen.get("forecast_run_id"),
        "data_cutoff": frozen.get("data_cutoff"), "generated_at": frozen.get("generated_at"),
        "reference_kind": "CASE_EVALUATION_SNAPSHOT" if used_case_snapshot else frozen.get("reference_kind"), "coverage_kind": frozen.get("coverage_kind"),
        "monthly_forecast_cloud": sum(float(row.get("predicted_cloud") or 0) for row in forecast_rows if str(row.get("reference_month")) == event_day.strftime("%Y%m")),
    }
    return {
        "items": items,
        "context_items": [{"event_date": day.isoformat(), "observed_cloud": value, "is_event_day": day == event_day}
                          for day, value in sorted(observed_by_day.items())],
        "context_start": context_start.isoformat(), "context_end": data_end.isoformat(),
        "context_event_date": event_day.isoformat(), "analysis_data_end": data_end.isoformat(),
        "months": months, "data_cutoff": str(frozen.get("data_cutoff")) if frozen else cutoff.isoformat(),
        "horizon_end": display_end.isoformat(), "forecast_end": forecast_end.isoformat(),
        "forecast_start": comparable[0]["event_date"] if comparable else None,
        "evaluation_grain": "MONTHLY_EVENT" if monthly else "DAILY",
        "reference": reference, "warnings": warnings,
        "returned_to_baseline": healed,
        "contributor_series_count": len(members), "scope_verified": bool(contributor_series),
        "contributor_series": members,
        "contributor_breakdown": contributor_breakdown,
        "detail_dimensions": [{"key": item["key"], "label": item["label"]} for item in detail_dimensions],
        "detail_items": detail_items,
        "reference_table": publication.reference_table, "series_dimensions": series_dimensions,
        "distribution_methods": sorted({str(point.get("distribution_method")) for point in comparable}),
        "band_methods": sorted({str(row.get("band_method") or "UNAVAILABLE") for row in forecast_rows}),
        "confidence": sorted({str(row.get("band_confidence") or "UNAVAILABLE") for row in forecast_rows}),
        "note": " ".join(warnings) if warnings else None,
    }


def _cutoff_of(publication: Any) -> date:
    raw = getattr(publication, "data_cutoff", None)
    if raw:
        try:
            return date.fromisoformat(str(raw)[:10])
        except ValueError:
            pass
    generated = getattr(publication, "generated_at", None)
    if generated is not None:
        return generated.date()
    return date.today()


def _months_for(cutoff: date, horizon_days: int) -> list[str]:
    months: list[str] = []
    cursor = cutoff
    end = cutoff + timedelta(days=max(1, int(horizon_days)))
    while cursor <= end:
        key = f"{cursor.year:04d}{cursor.month:02d}"
        if key not in months:
            months.append(key)
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months
