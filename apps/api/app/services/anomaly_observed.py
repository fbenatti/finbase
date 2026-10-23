"""Leitura do consumo observado (`realizado_cloud`) para a detecção.

Contrato obrigatório do plano:

* a medida lida é ``realizado_cloud``; nunca é substituída por outra;
* a janela usa a **data completa do consumo/cobrança**, obtida pelo mesmo
  parser do forecast (``gold_date_expr``);
* ``_billing_period`` é usado **apenas** para desambiguar ``MM/DD`` x ``DD/MM``
  no parsing — nunca para selecionar, deslocar ou limitar o dia analisado;
* a agregação acontece no Trino, na granularidade da série; o Python recebe
  apenas a série agregada.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

from app.services.forecast_service import gold_date_expr
from app.services.trino_service import execute as trino_execute

logger = logging.getLogger(__name__)

#: Tipos de linha considerados realizado (retrocompatibilidade com nulos).
REALIZED_TYPE_VALUES = ("realizado",)


def _quote(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _lit(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def billing_periods_between(start: date, end: date) -> list[str]:
    """Meses ``YYYYMM`` de um mês antes de ``start`` até ``end``, para podar partições."""
    months: list[str] = []
    cursor = (start.replace(day=1) - timedelta(days=1)).replace(day=1)
    while cursor <= end:
        months.append(cursor.strftime("%Y%m"))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


def observed_daily_sql(
    *,
    source_namespace: str,
    table: str,
    date_column: str,
    period_column: str | None,
    type_column: str | None,
    measure_column: str,
    dimensions: Sequence[str],
    start: date,
    end: date,
    series_filters: Sequence[str] = (),
) -> str:
    """SQL de leitura do observado diário por série.

    ``source_billing_period`` é carregado só como procedência.
    """
    date_expr = gold_date_expr(date_column, period_column)
    dim_select = "".join(f"{_quote(dim)}, " for dim in dimensions)
    group_by = ", ".join(str(i + 1) for i in range(len(dimensions) + 1))
    type_filter = (
        f"({_quote(type_column)} = 'realizado' OR {_quote(type_column)} IS NULL)"
        if type_column
        else "TRUE"
    )
    series_filter = " OR ".join(f"({item})" for item in series_filters if item) or "TRUE"
    # Poda de partição: a Silver é particionada por período de faturamento, e
    # sem este predicado o Trino lia a tabela inteira para depois filtrar por
    # dia. Um mês de margem antes do início cobre cobranças faturadas no mês
    # seguinte ao consumo (mesma regra do cubo diário v2).
    period_prune = (
        f" AND CAST({_quote(period_column)} AS VARCHAR) IN ({', '.join(_lit(m) for m in billing_periods_between(start, end))})"
        if period_column
        else ""
    )
    period_select = (
        f"CAST({_quote(period_column)} AS VARCHAR) AS source_billing_period"
        if period_column
        else "CAST(NULL AS VARCHAR) AS source_billing_period"
    )
    return f"""
        WITH parsed AS (
            SELECT {dim_select}
                   {date_expr} AS day,
                   COALESCE(TRY_CAST({_quote(measure_column)} AS DOUBLE), 0) AS value,
                   {period_select}
              FROM iceberg.{source_namespace}.{_quote(table)}
             WHERE {type_filter} AND ({series_filter}){period_prune}
        )
        SELECT {dim_select}CAST(day AS VARCHAR) AS event_date,
               SUM(value) AS observed_cloud,
               MAX(source_billing_period) AS source_billing_period
          FROM parsed
         WHERE day IS NOT NULL AND day BETWEEN DATE {_lit(start.isoformat())} AND DATE {_lit(end.isoformat())}
         GROUP BY {group_by}
    """


async def read_daily_observed(
    *,
    source_namespace: str,
    table: str,
    date_column: str,
    period_column: str | None,
    type_column: str | None,
    measure_column: str,
    dimensions: Sequence[str],
    start: date,
    end: date,
    timeout_s: int = 900,
) -> list[dict[str, Any]]:
    """Séries diárias observadas em ``realizado_cloud``."""
    if not dimensions:
        raise ValueError("a leitura do observado exige ao menos uma dimensão de série")
    sql = observed_daily_sql(
        source_namespace=source_namespace,
        table=table,
        date_column=date_column,
        period_column=period_column,
        type_column=type_column,
        measure_column=measure_column,
        dimensions=list(dimensions),
        start=start,
        end=end,
    )
    rows = await trino_execute(sql, timeout_s=timeout_s)
    parsed: list[dict[str, Any]] = []
    for row in rows:
        day = str(row.get("event_date") or "")[:10]
        try:
            event_date = date.fromisoformat(day)
        except ValueError:
            logger.warning("[anomaly] data não parseada no observado: %r", row.get("event_date"))
            continue
        parsed.append(
            {
                **{dim: row.get(dim) for dim in dimensions},
                "event_date": event_date,
                "observed_cloud": float(row.get("observed_cloud") or 0.0),
                "source_billing_period": row.get("source_billing_period"),
            }
        )
    return parsed


def daily_by_weekday(rows: Sequence[Mapping[str, Any]]) -> dict[date, float]:
    """Soma observada por dia — insumo do perfil semanal (E3)."""
    totals: dict[date, float] = {}
    for row in rows:
        day = row.get("event_date")
        if not isinstance(day, date):
            continue
        totals[day] = totals.get(day, 0.0) + float(row.get("observed_cloud") or 0.0)
    return totals


def group_by_series(
    rows: Sequence[Mapping[str, Any]], dimensions: Sequence[str]
) -> dict[tuple, list[Mapping[str, Any]]]:
    """Agrupa as linhas observadas pela chave de série."""
    grouped: dict[tuple, list[Mapping[str, Any]]] = {}
    for row in rows:
        key = tuple(str(row.get(dim) or "") for dim in dimensions)
        grouped.setdefault(key, []).append(row)
    return grouped
