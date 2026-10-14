"""Consultas de leitura sobre as tabelas publicadas de anomalia (F4).

Nada é copiado para o PostgreSQL: as consultas vão direto ao Iceberg/Trino, com
paginação e limite explícito.

Consultas disponíveis:

* ``expected_events`` — recorrências reconhecidas como esperadas
  (`assessment_status = EXPECTED_EVENT`), que permanecem na tabela analítica sem
  abrir workflow;
* ``quality`` — avaliações com limitação declarada (dado parcial, referência
  reconstruída, tolerância explícita).
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.services.trino_service import execute as trino_execute

#: Status publicados na tabela de avaliação.
EXPECTED_EVENT_STATUS = "EXPECTED_EVENT"
OPEN_STATUS = "OPEN"
CANDIDATE_STATUS = "CANDIDATE"

#: Códigos de razão que caracterizam limitação de qualidade/referência.
QUALITY_REASON_CODES = (
    "PARTIAL_DATA",
    "REFERENCE_UNAVAILABLE",
    "EXPLICIT_TOLERANCE",
    "UNIFORM_FALLBACK",
    "FORECAST_RULE_MISMATCH",
    "NO_APPLICABLE_FORECAST_RULE",
    "CALENDAR_SHIFT_EVIDENCE",
    "FEW_MONTHLY_EVENTS",
)

SELECT_COLUMNS = (
    "assessment_id", "case_id", "series_id", "event_date", "evaluation_grain",
    "period", "source_billing_period", "observed_cloud", "expected_cloud",
    "lower_cloud", "upper_cloud", "delta_cloud", "anomalous_cost_cloud", "score",
    "confidence", "assessment_status", "reason_codes", "reference_kind",
    "reference_id", "forecast_run_id",
)

DIMENSION_COLUMNS = (
    "cloud_origem", "servicename", "subaccountname", "centro_custo",
    "organograma_nivel_1", "chargecategory", "pricingcategory",
)


def _quote(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _lit(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _projection() -> str:
    columns = list(SELECT_COLUMNS) + list(DIMENSION_COLUMNS)
    return ", ".join(_quote(column) for column in columns)


def assessments_sql(
    *,
    namespace: str,
    table: str,
    statuses: Sequence[str] | None = None,
    period: str | None = None,
    reason_code: str | None = None,
    series_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> str:
    """Consulta paginada sobre a tabela de avaliações publicada."""
    where: list[str] = []
    if statuses:
        where.append(
            "CAST(assessment_status AS VARCHAR) IN ("
            + ", ".join(_lit(status) for status in statuses)
            + ")"
        )
    if period:
        where.append(f"CAST(period AS VARCHAR) = {_lit(period)}")
    if reason_code:
        where.append(f"CAST(reason_codes AS VARCHAR) LIKE {_lit('%' + reason_code + '%')}")
    if series_id:
        where.append(f"CAST(series_id AS VARCHAR) = {_lit(series_id)}")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    size = max(1, min(int(limit), 200))
    skip = max(0, int(offset))
    # A gramática do Trino é `OFFSET n LIMIT m` (nesta ordem). `LIMIT n OFFSET m`
    # falha com "mismatched input 'OFFSET'".
    pagination = f"OFFSET {skip} LIMIT {size}" if skip else f"LIMIT {size}"
    return (
        f"SELECT {_projection()} FROM iceberg.{namespace}.{_quote(table)}{clause} "
        f"ORDER BY CAST(event_date AS VARCHAR) DESC, assessment_id "
        f"{pagination}"
    )


def count_sql(
    *,
    namespace: str,
    table: str,
    statuses: Sequence[str] | None = None,
    period: str | None = None,
    reason_code: str | None = None,
) -> str:
    where: list[str] = []
    if statuses:
        where.append(
            "CAST(assessment_status AS VARCHAR) IN ("
            + ", ".join(_lit(status) for status in statuses)
            + ")"
        )
    if period:
        where.append(f"CAST(period AS VARCHAR) = {_lit(period)}")
    if reason_code:
        where.append(f"CAST(reason_codes AS VARCHAR) LIKE {_lit('%' + reason_code + '%')}")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    return f"SELECT COUNT(*) AS total FROM iceberg.{namespace}.{_quote(table)}{clause}"


async def _fetch(sql: str, timeout_s: int = 180) -> list[dict[str, Any]]:
    return await trino_execute(sql, timeout_s=timeout_s)


def _recurrence_key(row: Mapping[str, Any]) -> str:
    parts = [
        str(row.get("cloud_origem") or ""),
        str(row.get("servicename") or ""),
        str(row.get("chargecategory") or ""),
    ]
    return "|".join(part for part in parts if part) or str(row.get("series_id") or "")


async def read_expected_events(
    *,
    namespace: str,
    table: str,
    period: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Recorrências reconhecidas como esperadas, agrupadas por família."""
    rows = await _fetch(
        assessments_sql(
            namespace=namespace,
            table=table,
            statuses=[EXPECTED_EVENT_STATUS],
            period=period,
            limit=limit,
            offset=offset,
        )
    )
    total_rows = await _fetch(
        count_sql(
            namespace=namespace,
            table=table,
            statuses=[EXPECTED_EVENT_STATUS],
            period=period,
        )
    )
    total = int(total_rows[0].get("total") or 0) if total_rows else 0
    items = group_expected_events(rows)
    return {
        "items": items,
        "total": total,
        "returned": len(rows),
        "limit": limit,
        "offset": offset,
        "assessment_table": table,
        "note": (
            "eventos esperados permanecem na tabela analítica e não abrem workflow; "
            "a contagem total considera as avaliações publicadas na publicação corrente"
        ),
    }


def group_expected_events(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Agrupa avaliações de evento esperado por família de recorrência.

    Função pura: recebe as linhas publicadas e devolve uma entrada por família,
    com contagem, valores, meses, confiança e códigos de razão.
    """
    import json as _json

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = _recurrence_key(row)
        entry = grouped.setdefault(
            key,
            {
                "family": key,
                "occurrences": 0,
                "expected_cloud": 0.0,
                "observed_cloud": 0.0,
                "months": set(),
                "last_event_date": None,
                "confidence": set(),
                "reason_codes": set(),
                "series": set(),
            },
        )
        entry["occurrences"] += 1
        entry["expected_cloud"] += float(row.get("expected_cloud") or 0.0)
        entry["observed_cloud"] += float(row.get("observed_cloud") or 0.0)
        entry["months"].add(str(row.get("period") or ""))
        entry["confidence"].add(str(row.get("confidence") or ""))
        entry["series"].add(str(row.get("series_id") or ""))
        if row.get("reason_codes"):
            try:
                codes = _json.loads(str(row["reason_codes"]))
            except ValueError:
                codes = [str(row["reason_codes"])]
            entry["reason_codes"].update(str(code) for code in codes)
        event_date = str(row.get("event_date") or "")
        if event_date and (entry["last_event_date"] is None or event_date > entry["last_event_date"]):
            entry["last_event_date"] = event_date

    items = []
    for entry in grouped.values():
        items.append(
            {
                "family": entry["family"],
                "occurrences": entry["occurrences"],
                "series_count": len(entry["series"]),
                "expected_cloud": round(entry["expected_cloud"], 2),
                "observed_cloud": round(entry["observed_cloud"], 2),
                "delta_cloud": round(entry["observed_cloud"] - entry["expected_cloud"], 2),
                "months": sorted(month for month in entry["months"] if month),
                "last_event_date": entry["last_event_date"],
                "confidence": sorted(value for value in entry["confidence"] if value),
                "reason_codes": sorted(entry["reason_codes"]),
            }
        )
    items.sort(key=lambda item: (-item["occurrences"], item["family"]))
    return items


async def read_quality(
    *,
    namespace: str,
    table: str,
    period: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Avaliações com limitação declarada, uma linha por código de razão."""
    rows = await _fetch(
        assessments_sql(
            namespace=namespace,
            table=table,
            period=period,
            limit=200,
            offset=offset,
        ),
        timeout_s=240,
    )
    items = bucket_quality(rows)
    return {
        "items": items,
        "sampled": len(rows),
        "assessment_table": table,
        "note": "amostra limitada a 200 avaliações por consulta; refine por período",
    }


def bucket_quality(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Conta avaliações por código de razão de qualidade (função pura)."""
    import json as _json

    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        raw = row.get("reason_codes")
        codes: list[str] = []
        if raw:
            try:
                codes = [str(code) for code in _json.loads(str(raw))]
            except ValueError:
                codes = [str(raw)]
        for code in codes:
            if code not in QUALITY_REASON_CODES:
                continue
            bucket = buckets.setdefault(
                code,
                {"reason_code": code, "count": 0, "anomalous_cost_cloud": 0.0,
                 "periods": set(), "statuses": set()},
            )
            bucket["count"] += 1
            bucket["anomalous_cost_cloud"] += float(row.get("anomalous_cost_cloud") or 0.0)
            bucket["periods"].add(str(row.get("period") or ""))
            bucket["statuses"].add(str(row.get("assessment_status") or ""))
    items = [
        {
            "reason_code": bucket["reason_code"],
            "count": bucket["count"],
            "anomalous_cost_cloud": round(bucket["anomalous_cost_cloud"], 2),
            "periods": sorted(period for period in bucket["periods"] if period),
            "statuses": sorted(status for status in bucket["statuses"] if status),
        }
        for bucket in buckets.values()
    ]
    items.sort(key=lambda item: -item["count"])
    return items
