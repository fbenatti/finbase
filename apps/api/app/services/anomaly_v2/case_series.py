"""Série diária de um caso v2, com o caminho organograma → assinatura → recurso."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any, Mapping

from sqlalchemy import text

from app.database import async_session
from app.services.anomaly_v2.baseline import expected_for_day, expected_from_reference
from app.services.trino_service import execute as trino_execute

logger = logging.getLogger(__name__)

# Short-lived, bounded cache: reopening an investigation must not rescan Silver.
# In-flight requests share the same task; failures are never cached.
_SERIES_TTL_S = 60.0
_SERIES_CACHE_MAX = 64
_SERIES_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_SERIES_PENDING: dict[str, asyncio.Task] = {}


async def read_v2_case_series(case: Any, config: Any, *, as_of: date) -> dict[str, Any]:
    case_fields = ("series_dimensions", "series_id", "event_date", "detector", "event_type", "case_version")
    config_fields = (
        "id", "version", "source_namespace", "destination_physical_name", "date_column",
        "period_column", "type_column", "measure_column", "connection_id_column",
    )
    key = json.dumps([
        as_of.isoformat(),
        {field: getattr(case, field, None) for field in case_fields},
        {field: getattr(config, field, None) for field in config_fields},
    ], sort_keys=True, default=str)
    now = time.monotonic()
    for expired in [key for key, (expires, _) in _SERIES_CACHE.items() if expires <= now]:
        _SERIES_CACHE.pop(expired, None)
    cached = _SERIES_CACHE.get(key)
    if cached:
        return copy.deepcopy(cached[1])
    task = _SERIES_PENDING.get(key)
    if task is None:
        async def load() -> dict[str, Any]:
            try:
                result = await _read_v2_case_series(case, config, as_of=as_of)
                # A failed optional breakdown must be retried on the next open.
                if result.pop("_detail_failed", False):
                    return result
                while len(_SERIES_CACHE) >= _SERIES_CACHE_MAX:
                    _SERIES_CACHE.pop(next(iter(_SERIES_CACHE)))
                _SERIES_CACHE[key] = (time.monotonic() + _SERIES_TTL_S, result)
                return result
            finally:
                _SERIES_PENDING.pop(key, None)

        task = asyncio.create_task(load())
        # Retrieve exceptions even if every HTTP caller disconnects.
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        _SERIES_PENDING[key] = task
    return copy.deepcopy(await asyncio.shield(task))

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


def _scope_sql_filter(dimensions: Mapping[str, Any], exact_values: Mapping[str, list[str]] | None = None) -> str:
    clauses = ["TRUE"]
    native_clauses: list[str] = []
    columns = [(key, key) for key in _DIRECT_FILTERS] + [
        ("resource_group", "source_resource_group_name"), ("region", "regionname"),
    ]
    for key, column in columns:
        raw = dimensions.get(key)
        if raw in (None, ""):
            continue
        if exact_values is not None and key in exact_values:
            values = exact_values[key]
            native_clauses.append(f"{_quote(column)} IN ({', '.join(_lit(value) for value in values)})" if values else "FALSE")
            continue
        clauses.append(
            f"lower(trim(CAST({_quote(column)} AS VARCHAR))) = {_lit(str(raw).strip().lower())}"
        )
    normalized = " AND ".join(clauses)
    if native_clauses:
        native = " AND ".join(native_clauses)
        # Keep the native domain visible for file/dictionary pruning and defer
        # all expensive normalization until the selective anchor has matched.
        return f"({native}) AND CASE WHEN {native} THEN ({normalized}) ELSE FALSE END"
    return normalized


def _table_ref(namespace: str, table: str, snapshot_id: int | None = None) -> str:
    ref = f"iceberg.{_quote(namespace)}.{_quote(table)}"
    return ref + (f" FOR VERSION AS OF {int(snapshot_id)}" if snapshot_id is not None else "")


async def source_read_metadata(namespace: str, table: str) -> tuple[dict[str, str], int | None]:
    """Read schema/snapshot from catalog metadata, without a Trino query."""
    from app.services.iceberg_service import get_catalog

    def read():
        source = get_catalog().load_table((namespace, table))
        snapshot = source.current_snapshot()
        return ({field.name.lower(): str(field.field_type).lower() for field in source.schema().fields},
                snapshot.snapshot_id if snapshot else None)

    return await asyncio.to_thread(read)


def build_scope_lookup_sql(*, namespace: str, table: str, column: str, value: str,
                           period_filter: str, connection_filter: str, snapshot_id: int | None = None) -> str:
    # Resolve the complete raw spelling domain using only ONE dictionary column.
    # The expensive measure/detail scans can then push an exact IN predicate
    # into Parquet, instead of normalizing every input row before filtering.
    quoted = _quote(column)
    return f"""SELECT DISTINCT {quoted} AS scope_value
        FROM {_table_ref(namespace, table, snapshot_id)}
        WHERE {period_filter} AND {connection_filter}
          AND lower(trim(CAST({quoted} AS VARCHAR))) = {_lit(value.strip().lower())}
        LIMIT 257"""


def raw_date_range_predicate(column: str, start: date, end: date, data_type: str | None = None) -> str:
    """Native column ranges, including slash dates with optional time suffixes.

    These are conservative candidates. The existing date normalizer still
    resolves MM/DD vs DD/MM using the billing period after aggregation.
    """
    quoted = _quote(column)
    next_day = end + timedelta(days=1)
    kind = str(data_type or "").lower()
    if kind == "date":
        return f"{quoted} BETWEEN DATE {_lit(start.isoformat())} AND DATE {_lit(end.isoformat())}"
    if kind.startswith("timestamp"):
        return (f"({quoted} >= TIMESTAMP {_lit(start.isoformat() + ' 00:00:00')} "
                f"AND {quoted} < TIMESTAMP {_lit(next_day.isoformat() + ' 00:00:00')})")
    clauses = [f"({quoted} >= {_lit(start.isoformat())} AND {quoted} < {_lit(next_day.isoformat())})"]
    values: dict[str, list[str]] = {"%m/%d/%Y": [], "%d/%m/%Y": []}
    cursor = start
    while cursor <= end:
        for fmt in values:
            values[fmt].append(cursor.strftime(fmt))
        cursor += timedelta(days=1)
    for candidates in values.values():
        lower, last = min(candidates), max(candidates)
        upper = last[:-1] + chr(ord(last[-1]) + 1)
        clauses.append(f"({quoted} >= {_lit(lower)} AND {quoted} < {_lit(upper)})")
    return "(" + " OR ".join(clauses) + ")"


def periods_overlapping(start: date, end: date) -> list[str]:
    """Meses de faturamento que cruzam o intervalo, sem o mês anterior."""
    months: list[str] = []
    cursor = start.replace(day=1)
    last = end.replace(day=1)
    while cursor <= last:
        months.append(cursor.strftime("%Y%m"))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


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


def period_predicate(column: str | None, periods: list[str], data_type: str | None) -> str:
    """Filtra a partição sem CAST quando a coluna já é texto.

    CAST na coluna de partição impede o Trino de podar os arquivos do mês.
    """
    if not column or not periods:
        return "TRUE"
    values = ", ".join(_lit(month) for month in periods)
    quoted = _quote(column)
    kind = (data_type or "").lower()
    if kind.startswith("varchar") or kind in {"char", "string"}:
        return f"{quoted} IN ({values})"
    return f"CAST({quoted} AS VARCHAR) IN ({values})"


def connection_predicate(column: str | None, ids: list[int]) -> str:
    """A silver é particionada por conexão. O id poda o arquivo da nuvem."""
    if not column or not ids:
        return "TRUE"
    quoted = _quote(column)
    safe = [str(int(item)) for item in ids]
    if len(safe) == 1:
        return f"{quoted} = {safe[0]}"
    return f"{quoted} IN ({', '.join(safe)})"


def raw_day_predicate(column: str, event: date) -> str:
    quoted = _quote(column)
    exact = (
        event.strftime("%m/%d/%Y"),
        event.strftime("%d/%m/%Y"),
        event.isoformat(),
    )
    in_list = ", ".join(_lit(value) for value in exact)
    return f"({quoted} IN ({in_list}) OR starts_with({quoted}, {_lit(event.isoformat())}))"


def normalize_charge_day(raw: str, period: str | None) -> str | None:
    """Converte o texto da cobrança em data, usando o mês de faturamento para desambiguar."""
    text_value = str(raw or "").strip()
    if len(text_value) >= 10 and text_value[4] == "-" and text_value[7] == "-":
        try:
            return date.fromisoformat(text_value[:10]).isoformat()
        except ValueError:
            return None
    parsed: list[date] = []
    for fmt in ("%m/%d/%Y", "%d/%m/%Y"):
        try:
            parsed.append(datetime.strptime(text_value[:10], fmt).date())
        except ValueError:
            continue
    if not parsed:
        return None
    period_key = str(period or "")[:6]
    matched = [day for day in parsed if day.strftime("%Y%m") == period_key]
    return (matched or parsed)[0].isoformat()


def build_case_daily_sql(
    *,
    namespace: str,
    table: str,
    date_column: str,
    type_filter: str,
    period_column: str | None,
    period_type: str | None,
    connection_column: str | None,
    connection_ids: list[int],
    measure: str,
    scope_filter: str,
    periods: list[str],
    start: date | None = None,
    end: date | None = None,
    date_type: str | None = None,
    snapshot_id: int | None = None,
) -> str:
    """Uma agregação diária. Sem regexp de data e sem colunas de recurso."""
    period_filter = period_predicate(period_column, periods, period_type)
    period_select = (
        f"CAST({_quote(period_column)} AS VARCHAR)"
        if period_column
        else "CAST(NULL AS VARCHAR)"
    )
    return f"""
        SELECT CAST({_quote(date_column)} AS VARCHAR) AS day_raw,
               {period_select} AS period,
               SUM(COALESCE(TRY_CAST({_quote(measure)} AS DOUBLE), 0)) AS cost_cloud
          FROM {_table_ref(namespace, table, snapshot_id)}
         WHERE {type_filter}
           AND {period_filter}
           AND {connection_predicate(connection_column, connection_ids)}
           AND {scope_filter}
           AND {raw_date_range_predicate(date_column, start, end, date_type) if start and end else 'TRUE'}
         GROUP BY 1, 2
    """


def build_case_detail_sql(
    *,
    namespace: str,
    table: str,
    date_column: str,
    type_filter: str,
    period_column: str | None,
    period_type: str | None,
    connection_column: str | None,
    connection_ids: list[int],
    measure: str,
    scope_filter: str,
    event_day: date,
    specs: list[tuple[str, str, str]],
    date_type: str | None = None,
    snapshot_id: int | None = None,
) -> str:
    """Caminho do desvio só no dia do evento, na partição daquele mês."""
    period_filter = period_predicate(period_column, [event_day.strftime("%Y%m")], period_type)
    period_select = (
        f"CAST({_quote(period_column)} AS VARCHAR)"
        if period_column
        else "CAST(NULL AS VARCHAR)"
    )
    detail_select = [
        f"CAST({_quote(column)} AS VARCHAR) AS {_quote(key)}"
        for column, key, _label in specs
    ]
    select_list = ", ".join(
        [
            f"CAST({_quote(date_column)} AS VARCHAR) AS day_raw",
            f"{period_select} AS period",
            *detail_select,
            f"SUM(COALESCE(TRY_CAST({_quote(measure)} AS DOUBLE), 0)) AS cost_cloud",
        ]
    )
    group_by = ", ".join(str(index) for index in range(1, 3 + len(specs)))
    return f"""
        SELECT {select_list}
          FROM {_table_ref(namespace, table, snapshot_id)}
         WHERE {type_filter}
           AND {period_filter}
           AND {connection_predicate(connection_column, connection_ids)}
           AND {raw_date_range_predicate(date_column, event_day, event_day, date_type)}
           AND {scope_filter}
         GROUP BY {group_by}
    """


async def connection_ids_for_cloud(cloud: str | None) -> list[int]:
    code = str(cloud or "").strip().lower()
    if not code:
        return []
    async with async_session() as session:
        rows = (
            await session.execute(
                text("SELECT id FROM source_connection WHERE lower(code) = :code"),
                {"code": code},
            )
        ).scalars().all()
    return [int(row) for row in rows]


def _chart_items(
    by_day: dict[str, float],
    scope_rows: list[dict[str, Any]],
    *,
    start: date,
    end: date,
    as_of: date,
    frozen_reference: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    # Trino GROUP BY has no ordering guarantee. Baseline residuals accumulate
    # chronologically, so the same daily values must always enter in date order.
    scope_rows = sorted(scope_rows, key=lambda row: str(row.get("day") or ""))
    first_positive = min((day for day, cost in by_day.items() if cost > 0), default=None)
    items: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end:
        iso = cursor.isoformat()
        obs = by_day.get(iso) if cursor <= as_of else None
        if frozen_reference and iso >= str(frozen_reference["reference_day"]):
            expected, lower, upper, _mad = expected_from_reference(frozen_reference, target=cursor)
        else:
            expected, lower, upper, _mad = expected_for_day(scope_rows, target=cursor)
        has_history = first_positive is not None and first_positive < iso
        comparable = has_history and obs is not None
        items.append({
            "event_date": iso,
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
    return items


async def _read_v2_case_series(case: Any, config: Any, *, as_of: date) -> dict[str, Any]:
    """Realizado diário do caso: uma agregação na partição da nuvem e o detalhe do dia."""
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
    date_column = str(config.date_column)
    type_filter = (
        f"({_quote(type_column)} = 'realizado' OR {_quote(type_column)} IS NULL)"
        if type_column
        else "TRUE"
    )
    measure = str(config.measure_column or "realizado_cloud")
    (column_types, snapshot_id), connection_ids = await asyncio.gather(
        source_read_metadata(namespace, table),
        connection_ids_for_cloud(dimensions.get("cloud_origem")),
    )
    specs = [item for item in _BREAKDOWN_COLUMNS if item[0] in column_types]
    periods = periods_overlapping(start, end)
    connection_column = str(getattr(config, "connection_id_column", None) or "source_connection_id")
    if connection_column.lower() not in column_types:
        connection_column = None
        connection_ids = []
    period_type = column_types.get(str(period_column or "").lower())
    exact_values: dict[str, list[str]] = {}
    for key, column in [("resource_group", "source_resource_group_name"),
                        ("subaccountname", "subaccountname"), ("servicename", "servicename")]:
        if dimensions.get(key) and column in column_types:
            lookup = build_scope_lookup_sql(namespace=namespace, table=table, column=column,
                value=str(dimensions[key]), period_filter=period_predicate(period_column, periods, period_type),
                connection_filter=connection_predicate(connection_column, connection_ids), snapshot_id=snapshot_id)
            raw_values = await trino_execute(lookup, timeout_s=20, max_retries=1)
            if len(raw_values) <= 256:
                exact_values[key] = [str(row["scope_value"]) for row in raw_values if row.get("scope_value") is not None]
            break
    scope_filter = _scope_sql_filter(dimensions, exact_values)
    date_type = column_types.get(date_column.lower())
    daily_sql = build_case_daily_sql(
        namespace=namespace,
        table=table,
        date_column=date_column,
        type_filter=type_filter,
        period_column=period_column,
        period_type=period_type,
        connection_column=connection_column,
        connection_ids=connection_ids,
        measure=measure,
        scope_filter=scope_filter,
        periods=periods,
        start=start,
        end=end,
        date_type=date_type,
        snapshot_id=snapshot_id,
    )
    detail_sql = build_case_detail_sql(
        namespace=namespace,
        table=table,
        date_column=date_column,
        type_filter=type_filter,
        period_column=period_column,
        period_type=period_type,
        connection_column=connection_column,
        connection_ids=connection_ids,
        measure=measure,
        scope_filter=scope_filter,
        event_day=event_day,
        specs=specs,
        date_type=date_type,
        snapshot_id=snapshot_id,
    ) if specs else None

    detail_failed = False

    async def _detail_rows() -> list[dict[str, Any]]:
        nonlocal detail_failed
        if not detail_sql:
            return []
        try:
            return list(await trino_execute(detail_sql, timeout_s=20, max_retries=1))
        except Exception as exc:
            detail_failed = True
            logger.warning("decomposição do caso %s indisponível: %s", getattr(case, "id", None), exc)
            return []

    started = time.perf_counter()
    rows, raw_detail = await asyncio.gather(
        trino_execute(daily_sql, timeout_s=20, max_retries=1),
        _detail_rows(),
    )
    logger.info(
        "série do caso %s: períodos %s, conexão %s, %s dias e %s detalhes em %.2fs",
        getattr(case, "id", None),
        periods,
        connection_ids,
        len(rows),
        len(raw_detail),
        time.perf_counter() - started,
    )

    by_day: dict[str, float] = {}
    for row in rows:
        day = normalize_charge_day(str(row.get("day_raw") or ""), str(row.get("period") or ""))
        if day is None or not (start.isoformat() <= day <= end.isoformat()):
            continue
        by_day[day] = by_day.get(day, 0.0) + float(row.get("cost_cloud") or 0.0)

    event_iso = event_day.isoformat()
    detail_rows: list[dict[str, Any]] = []
    for row in raw_detail:
        day = normalize_charge_day(str(row.get("day_raw") or ""), str(row.get("period") or ""))
        if day != event_iso:
            continue
        detail_rows.append({
            key: row.get(key)
            for _column, key, _label in specs
        } | {"observed_cloud": float(row.get("cost_cloud") or 0.0)})

    scope_rows = [
        {"day": day, "scope_id": str(case.series_id or ""), "cost_cloud": cost}
        for day, cost in by_day.items()
    ]
    monthly = str(getattr(case, "detector", "") or getattr(case, "event_type", "")) == "MONTHLY_ACCUMULATED"
    frozen_reference = dimensions.get("baseline_reference")
    if not isinstance(frozen_reference, dict):
        # Older cases saved scalar reference values but not their weekday model.
        # Preserve those recorded values; do not retrain on the deviation or
        # invent seasonal coefficients that were never persisted.
        expected = float(getattr(case, "expected_cloud", None) or 0)
        frozen_reference = {
            "reference_day": str(getattr(case, "episode_start", None) or dimensions.get("episode_start") or event_iso)[:10],
            "level": expected, "weekday_factors": {},
            "band": max(0.0, float(getattr(case, "upper_cloud", None) or expected) - expected),
            "lower_offset": max(0.0, expected - float(getattr(case, "lower_cloud", None) or 0)),
            "method": "FROZEN_CASE_VALUES",
        }
    items = _chart_items(by_day, scope_rows, start=start, end=end, as_of=as_of,
                         frozen_reference=frozen_reference if not monthly else None)
    detail_dimensions, detail_items, common = breakdown_from_rows(detail_rows, specs) if detail_rows else ([], [], {})
    member_dimensions = {**{key: value for key, value in dimensions.items() if value not in (None, "")}, **common}
    event_point = next((point for point in items if point["event_date"] == event_iso), {})

    return {
        "_detail_failed": detail_failed,
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
        "reference": {"reference_kind": "V2_BASELINE", "method": frozen_reference["method"],
                      "data_cutoff": (date.fromisoformat(frozen_reference["reference_day"]) - timedelta(days=1)).isoformat()},
        "returned_to_baseline": False,
    }
