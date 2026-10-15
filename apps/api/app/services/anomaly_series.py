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

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.services.anomaly_observed import observed_daily_sql
from app.services.trino_service import execute as trino_execute

logger = logging.getLogger(__name__)

# Drilldown adicional, apenas quando a coluna existe na fonte configurada.
# O forecast continua sendo comparável somente no grão de suas séries.
DETAIL_DIMENSIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("subscription", "Assinatura / subconta", ("subscriptionname", "subaccountname", "subscription_name")),
    ("subscription_id", "ID da assinatura", ("subscriptionid", "subaccountid", "subscription_id", "sub_account_id")),
    ("resource_group", "Resource group", ("source_resource_group_name", "resourcegroupname", "resource_group_name", "resource_group_id", "resourcegroup")),
    ("gcp_project", "Projeto GCP", ("projectid", "projectname", "project_id", "project_name", "projectnumber")),
    ("service", "Serviço", ("servicename", "service_name")),
    ("service_category", "Categoria do serviço", ("servicesubcategory", "servicecategory", "service_category", "service_subcategory")),
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
    return resolved


def detailed_event_sql(*, source_namespace: str, source_table: str, date_column: str,
                       period_column: str | None, type_column: str | None,
                       measure_column: str, dimensions: Sequence[str],
                       detail_dimensions: Sequence[Mapping[str, str]],
                       member_filters: Sequence[str], event_day: date) -> str:
    selected = list(dict.fromkeys([*dimensions, *(str(item["column"]) for item in detail_dimensions)]))
    dim_select = ", ".join(_quote(name) for name in selected)
    dim_aliases = ", ".join(f"{_quote(item['column'])} AS {_quote(item['key'])}" for item in detail_dimensions)
    # Present only hierarchy fields whose source columns exist.
    projection = ", ".join([dim_select, dim_aliases] if dim_select else [dim_aliases])
    group_columns = ", ".join(str(index + 1) for index in range(len(selected) + len(detail_dimensions)))
    date_expr = gold_date_expr(date_column, period_column)
    type_filter = f"({_quote(type_column)} = 'realizado' OR {_quote(type_column)} IS NULL)" if type_column else "TRUE"
    filters = [value for value in member_filters if value]
    scope = "(" + " OR ".join(f"({value})" for value in filters) + ")" if filters else "FALSE"
    return f"""
        WITH parsed AS (
            SELECT {projection}, {date_expr} AS day,
                   COALESCE(TRY_CAST({_quote(measure_column)} AS DOUBLE), 0) AS value
              FROM iceberg.{source_namespace}.{_quote(source_table)}
             WHERE {type_filter}
        )
        SELECT {projection}, SUM(value) AS observed_cloud
          FROM parsed
         WHERE day = DATE {_lit(event_day.isoformat())} AND {scope}
         GROUP BY {group_columns}
         ORDER BY ABS(SUM(value)) DESC
         LIMIT 500
    """

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
    cutoff = _cutoff_of(publication)
    data_end = analysis_data_end or (datetime.now(timezone.utc).date() - timedelta(days=2))
    raw_day = getattr(case, "event_date", None)
    try:
        event_day = date.fromisoformat(str(raw_day)[:10])
    except (ValueError, TypeError):
        event_day = cutoff
    monthly = str(getattr(case, "evaluation_grain", "")) == "MONTHLY_EVENT"
    context_start = event_day.replace(day=1) if monthly else event_day - timedelta(days=30)
    requested_end = data_end + timedelta(days=max(1, int(horizon_days)))
    months = _months_for(context_start, max(1, (requested_end - context_start).days))
    members = list(contributor_series) or [{"series_id": str(case.series_id), "dimensions": dict(getattr(case, "series_dimensions", None) or {})}]
    rows = await trino_execute(
        reference_series_sql(
            namespace=reference_namespace,
            reference_table=publication.reference_table,
            series_id=str(case.series_id),
            months=months,
            series_ids=[str(member["series_id"]) for member in members],
        ), timeout_s=180,
    )
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
        sql = observed_daily_sql(
            source_namespace=source_namespace, table=source_table,
            date_column=date_column, period_column=period_column,
            type_column=type_column, measure_column=measure_column,
            dimensions=list(dimensions), start=context_start, end=data_end,
        )
        filters = [_dimension_filter(member["dimensions"]) for member in members]
        filters = [f"({value})" for value in filters if value]
        if filters:
            sql = f"SELECT * FROM ({sql}) AS series WHERE " + " OR ".join(filters)
        for row in await trino_execute(sql, timeout_s=600):
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
    contributor_breakdown = []
    for index, member in enumerate(members):
        timeline = compose_case_timeline(points_by_series[index], observed_by_member[index],
                                         start=context_start, end=display_end, data_end=data_end, monthly=monthly)
        event_point = next((point for point in timeline if point["event_date"] == event_day.isoformat()), {})
        contributor_breakdown.append({**member, "event_date": event_day.isoformat(),
                                      "items": timeline,
                                      **{key: event_point.get(key) for key in
                                         ("observed_cloud", "expected_cloud", "delta_cloud")}})
    if monthly and frozen and frozen.get("coverage_kind") != "FULL_MONTH":
        warnings.append("Este forecast contém o saldo restante do mês, não a expectativa mensal congelada antes da compra. A comparação desta compra está indisponível; o saldo não foi distribuído como consumo diário nem usado como zero esperado.")
    if event_day > data_end:
        warnings.append(f"O evento é posterior ao cutoff de dados ({data_end.isoformat()}).")
    if not rows and not warnings:
        warnings.append("Não há referência de forecast disponível para esta série.")
    if not contributor_series:
        warnings.append("Esta avaliação antiga não preservou as séries contribuintes. O gráfico está limitado à série representativa; reprocessar a detecção permite reconciliar o escopo completo.")
    comparable = [point for point in items if point["comparison_supported"]]
    reference = None if not frozen else {
        "reference_id": frozen.get("reference_id"), "forecast_run_id": frozen.get("forecast_run_id"),
        "data_cutoff": frozen.get("data_cutoff"), "generated_at": frozen.get("generated_at"),
        "reference_kind": frozen.get("reference_kind"), "coverage_kind": frozen.get("coverage_kind"),
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
        "contributor_series_count": len(members), "scope_verified": bool(contributor_series),
        "contributor_series": members,
        "contributor_breakdown": contributor_breakdown,
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
