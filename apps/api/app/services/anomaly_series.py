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
from app.services.anomaly_calibration import weekday_profile
from app.services.trino_service import execute as trino_execute

logger = logging.getLogger(__name__)

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
) -> str:
    quoted_months = ", ".join(_lit(month) for month in months)
    return (
        f"SELECT reference_month, coverage_kind, coverage_start, coverage_end, covered_days, "
        f"predicted_cloud, weekday_weights, normalizer, distribution_method, "
        f"daily_band_lower_offset, daily_band_upper_offset, band_method, band_confidence, "
        f"reference_id, reference_kind, data_cutoff, series_id "
        f"FROM iceberg.{namespace}.{_quote(reference_table)} "
        f"WHERE series_id = {_lit(series_id)} "
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
) -> list[dict[str, Any]]:
    """Uma linha por dia coberto, com esperado, faixa e custo anômalo.

    Função pura: recebe a referência e o observado já lidos, o que a torna
    testável sem Trino.
    """
    points: list[dict[str, Any]] = []
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
            raw_weight = weights.get(cursor.weekday())
            historical_fallback = float((weekday_medians or {}).get(cursor.weekday(), 0.0))
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
            points.append(
                {
                    "event_date": cursor.isoformat(),
                    "weekday": cursor.weekday(),
                    "reference_month": row.get("reference_month"),
                    "reference_id": row.get("reference_id"),
                    "reference_kind": row.get("reference_kind"),
                    "coverage_kind": row.get("coverage_kind"),
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
            )
            cursor += timedelta(days=1)
    points.sort(key=lambda item: item["event_date"])
    return points


def _dimension_filter(dimensions: Mapping[str, Any]) -> str | None:
    clauses: list[str] = []
    for name, value in dimensions.items():
        column = _quote(name)
        if value is None or str(value) == "":
            clauses.append(f"COALESCE(CAST({column} AS VARCHAR), '') = ''")
        else:
            clauses.append(f"CAST({column} AS VARCHAR) = {_lit(str(value))}")
    return " AND ".join(clauses) if clauses else None


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
) -> dict[str, Any]:
    """Série diária do caso: referência + observado, sem persistir a grade."""
    months = sorted(
        {
            str(getattr(case, "period", "") or ""),
            *_months_for(
                _cutoff_of(publication), horizon_days
            ),
        }
        - {""}
    )
    if not months:
        months = [str(getattr(case, "period", "") or "")] if getattr(case, "period", None) else []

    reference_rows = await trino_execute(
        reference_series_sql(
            namespace=reference_namespace,
            reference_table=publication.reference_table,
            series_id=str(case.series_id),
            months=months,
        ),
        timeout_s=180,
    )
    cutoff = _cutoff_of(publication)
    effective_data_end = analysis_data_end or (datetime.now(timezone.utc).date() - timedelta(days=2))
    horizon_end = min(cutoff + timedelta(days=max(1, int(horizon_days))), effective_data_end)
    raw_event_date = getattr(case, "event_date", None)
    if isinstance(raw_event_date, date):
        event_day = raw_event_date
    else:
        try:
            event_day = date.fromisoformat(str(raw_event_date)[:10])
        except (TypeError, ValueError):
            event_day = cutoff
    context_start = event_day - timedelta(days=30)
    context_end = min(event_day + timedelta(days=15), effective_data_end)

    covered_days: list[date] = []
    for row in reference_rows:
        start = row.get("coverage_start")
        end = row.get("coverage_end")
        if not start or not end:
            continue
        cursor = date.fromisoformat(str(start)[:10])
        last = min(date.fromisoformat(str(end)[:10]), horizon_end)
        while cursor <= last:
            covered_days.append(cursor)
            cursor += timedelta(days=1)
    # O detalhe do caso também precisa explicar o desvio com o realizado que o
    # precedeu e o que veio depois. Esta janela é contexto observado, não uma
    # nova referência nem uma reconstrução de forecast.
    series_dimensions = dict(getattr(case, "series_dimensions", None) or {})
    query_start = min([context_start, *covered_days])
    query_end = min(max([context_end, *covered_days]), effective_data_end)
    if query_end < query_start:
        return {
            "items": [], "context_items": [], "months": months,
            "context_start": context_start.isoformat(),
            "context_end": context_end.isoformat(),
            "context_event_date": event_day.isoformat(),
            "analysis_data_end": effective_data_end.isoformat(),
            "note": "evento posterior ao corte de dados D-2",
        }
    sql = observed_daily_sql(
        source_namespace=source_namespace,
        table=source_table,
        date_column=date_column,
        period_column=period_column,
        type_column=type_column,
        measure_column=measure_column,
        dimensions=list(dimensions),
        start=query_start,
        end=query_end,
    )
    series_filter = _dimension_filter(series_dimensions)
    if series_filter:
        sql = f"SELECT * FROM ({sql}) AS series WHERE {series_filter}"

    observed_rows = await trino_execute(sql, timeout_s=600)
    observed_by_day: dict[date, float] = {}
    for row in observed_rows:
        try:
            day = date.fromisoformat(str(row.get("event_date"))[:10])
        except (TypeError, ValueError):
            continue
        observed_by_day[day] = observed_by_day.get(day, 0.0) + float(row.get("observed_cloud") or 0.0)

    historical = {day: value for day, value in observed_by_day.items() if day <= cutoff}
    profile = weekday_profile(historical, as_of=cutoff, weeks=8)
    items = derive_daily_points(
        reference_rows,
        observed_by_day,
        horizon_end=horizon_end,
        weekday_medians=profile.medians if not profile.is_fallback else None,
    ) if covered_days and reference_rows else []
    context_items = []
    context_day = context_start
    while context_day <= context_end:
        context_items.append(
            {
                "event_date": context_day.isoformat(),
                "observed_cloud": observed_by_day.get(context_day),
                "is_event_day": context_day == event_day,
            }
        )
        context_day += timedelta(days=1)
    return {
        "items": items,
        "context_items": context_items,
        "context_start": context_start.isoformat(),
        "context_end": context_end.isoformat(),
        "context_event_date": event_day.isoformat(),
        "analysis_data_end": effective_data_end.isoformat(),
        "months": months,
        "data_cutoff": cutoff.isoformat(),
        "horizon_end": horizon_end.isoformat(),
        "reference_table": publication.reference_table,
        "series_dimensions": series_dimensions,
        "distribution_methods": sorted({str(item["distribution_method"]) for item in items}),
        "band_methods": sorted({str(row.get("band_method") or "UNAVAILABLE") for row in reference_rows}),
        "confidence": sorted({str(row.get("band_confidence") or "UNAVAILABLE") for row in reference_rows}),
        "note": (
            "sem linhas de referência na publicação corrente"
            if not reference_rows
            else "referência sem intervalo coberto" if not covered_days else None
        ),
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
