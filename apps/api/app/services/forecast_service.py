"""Forecast engine over aggregated Gold data.

The service deliberately receives only daily aggregates from Trino. It never
loads the detailed Gold table into pandas.
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, timedelta
from typing import Any

import pandas as pd

from app.services.trino_service import execute as trino_execute
from app.services import iceberg_service as iceberg

logger = logging.getLogger(__name__)


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _lit(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def gold_date_expr(date_col: str, period_col: str | None = None) -> str:
    """Parse Gold charge dates across Azure slash formats and GCP ISO-8601 timestamps.

    GCP ChargePeriodStart is stored as varchar ``YYYY-MM-DDTHH:MM:SS+00:00``.
    ``TRY_CAST AS DATE`` and ``date_parse(..., '%Y-%m-%d')`` both fail on that
    shape, which previously dropped every GCP series from the forecast.
    """
    if period_col and date_col == period_col:
        return f"TRY_CAST(date_parse(CAST({_q(period_col)} AS VARCHAR) || '01', '%Y%m%d') AS DATE)"
    raw_date = f"CAST({_q(date_col)} AS VARCHAR)"
    # Do not use TRY_CAST for slash-delimited strings: Trino accepts them
    # using a locale-dependent parser before we can validate the billing
    # period. Limit it strictly to ISO dates and ISO-8601 timestamps.
    iso_date = (
        f"CASE WHEN regexp_like({raw_date}, '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}') "
        f"THEN COALESCE("
        f"TRY_CAST({_q(date_col)} AS DATE), "
        f"TRY(date_parse({raw_date}, '%Y-%m-%d')), "
        f"TRY(CAST(from_iso8601_timestamp({raw_date}) AS DATE)), "
        f"TRY(date_parse(substr({raw_date}, 1, 10), '%Y-%m-%d'))"
        f") END"
    )
    mdy_date = f"TRY(date_parse({raw_date}, '%m/%d/%Y'))"
    dmy_date = f"TRY(date_parse({raw_date}, '%d/%m/%Y'))"
    if not period_col:
        return f"COALESCE({iso_date}, {mdy_date}, {dmy_date})"
    # Azure Cost Management uses MM/DD/YYYY. Treat that as the primary
    # non-ISO format, and when a billing period exists use it to resolve
    # ambiguous day/month values deterministically. For example,
    # ``08/01/2026`` in billing period 202608 is 1 Aug, never 8 Jan.
    period_number = f"TRY_CAST(CAST({_q(period_col)} AS VARCHAR) AS INTEGER)"
    return (
        f"COALESCE({iso_date}, "
        f"CASE WHEN year({mdy_date}) * 100 + month({mdy_date}) = {period_number} THEN {mdy_date} END, "
        f"CASE WHEN year({dmy_date}) * 100 + month({dmy_date}) = {period_number} THEN {dmy_date} END, "
        f"{mdy_date}, {dmy_date})"
    )


async def fetch_metadata(table_name: str, namespace: str = "gold") -> dict[str, Any]:
    schema_rows = await trino_execute(f"DESCRIBE iceberg.{namespace}.{table_name}")
    columns = []
    for row in schema_rows:
        name = str(row.get("Column") or row.get("column_name") or row.get("column"))
        typ = str(row.get("Type") or row.get("data_type") or "")
        lower = typ.lower()
        columns.append({"name": name, "type": typ, "numeric": any(x in lower for x in ("double", "decimal", "real", "float", "int", "bigint")), "temporal": any(x in lower for x in ("date", "timestamp"))})
    names = {c["name"].lower(): c["name"] for c in columns}
    numeric = [c["name"] for c in columns if c["numeric"]]
    dates = [c["name"] for c in columns if c["temporal"]]
    date_priority = {"date": 0, "chargedate": 1, "chargeperiodstart": 2, "billingperiodstart": 3, "usage_date": 4}
    date_like = sorted([c["name"] for c in columns if c["name"].lower() in date_priority], key=lambda x: date_priority[x.lower()])
    if date_like:
        dates = date_like + [x for x in dates if x not in date_like]
    default_measure = names.get("realizado")
    if not default_measure:
        default_measure = next((n for n in numeric if n.lower() == "effectivecost"), None)
    period = names.get("_billing_period")
    date_column = dates[0] if dates else period
    dimensions = [c["name"] for c in columns if not c["numeric"] and c["name"] not in {date_column, period}]
    # Do not encode any FOCUS/provider column names here. A destination model
    # may represent another source and must define its own series dimensions.
    # The UI exposes every discovered dimension and the user chooses the set.
    recommended = []
    return {"columns": columns, "measures": numeric, "date_columns": dates, "period_columns": [period] if period else [], "dimensions": dimensions, "recommended_dimensions": recommended, "default_measure": default_measure, "default_date": date_column, "default_period": period}


def validate_config(config: Any, metadata: dict[str, Any]) -> list[str]:
    names = {c["name"] for c in metadata["columns"]}
    errors = []
    if config.measure_column not in names or config.measure_column not in metadata["measures"]:
        errors.append(f"Medida numérica inválida: {config.measure_column}")
    if config.date_column not in names and config.period_column not in names:
        errors.append("Nenhuma coluna de data/período válida foi configurada")
    for dimension in config.dimensions or []:
        if dimension not in names:
            errors.append(f"Dimensão inválida: {dimension}")
    if config.horizon_months < 1 or config.horizon_months > 36:
        errors.append("O horizonte deve estar entre 1 e 36 meses")
    if not config.enabled_windows or any(int(w) not in (7, 14, 28) for w in config.enabled_windows):
        errors.append("As janelas permitidas são 7, 14 e 28 dias")
    if config.model_mode not in {"AUTO_BACKTEST", "WEIGHTED", "MEAN_7", "MEAN_14", "MEAN_28"}:
        errors.append("Estratégia matemática inválida")
    return errors


async def run_forecast(config: Any, adjustments: list[Any], cutoff: date, progress=None, business_rules: list[Any] | None = None, result_table: str | None = None) -> dict[str, Any]:
    table = config.destination_physical_name
    namespace = getattr(config, "source_namespace", "gold")
    dims = list(config.dimensions or [])
    date_col = config.date_column
    measure = config.measure_column
    period = config.period_column
    date_expr = gold_date_expr(date_col, period)
    select_dims = ", ".join(_q(x) for x in dims)
    group = (select_dims + ", ") if select_dims else ""
    # Historical Gold rows produced before ``tipo`` was introduced are still
    # realized facts.  Include NULL for backward compatibility; every new
    # reprocess writes the explicit value below, while forecast/planning rows
    # live in a separate table and are never read here.
    type_filter = (
        f"({_q(config.type_column)} = 'realizado' OR {_q(config.type_column)} IS NULL)"
        if getattr(config, "has_type_column", False) else "TRUE"
    )
    source = f"iceberg.{namespace}.{table}"
    # Determine the as-of date before the expensive dimensional aggregation.
    # The previous query aggregated every Gold period and only discarded late
    # days in Pandas, which made a 18M-row table unstable in Trino.
    latest_period_filter = (
        f" AND CAST({_q(period)} AS VARCHAR) = (SELECT MAX(CAST({_q(period)} AS VARCHAR)) FROM {source} WHERE {type_filter})"
        if period else ""
    )
    latest_sql = f"SELECT MAX({date_expr}) AS collected_through FROM {source} WHERE {type_filter}{latest_period_filter}"
    latest_records = await trino_execute(latest_sql, timeout_s=120)
    latest_value = latest_records[0].get("collected_through") if latest_records else None
    collected_timestamp = pd.to_datetime(latest_value, errors="coerce")
    if pd.isna(collected_timestamp):
        raise ValueError("Não foi possível identificar a última data válida na Gold")
    collected_through = collected_timestamp.date()
    excluded_days = max(0, int(getattr(config, "cutoff_days", 2) or 0))
    effective_cutoff = collected_through - timedelta(days=excluded_days)
    # The longest model uses 28 days and AUTO_BACKTEST reserves seven days for
    # validation. Keep the current month in full when it starts earlier.
    max_window = max([int(w) for w in config.enabled_windows or [7, 14, 28]])
    history_start = min(
        effective_cutoff.replace(day=1),
        effective_cutoff - timedelta(days=max_window + 7),
    )
    date_filter = f"{date_expr} BETWEEN DATE {_lit(history_start.isoformat())} AND DATE {_lit(effective_cutoff.isoformat())}"
    select_sql = f"SELECT {group}{date_expr} AS forecast_day, SUM(TRY_CAST({_q(measure)} AS DOUBLE)) AS value FROM {source}"
    group_sql = f" GROUP BY {select_dims + ', ' if select_dims else ''}{date_expr} ORDER BY forecast_day"
    if period:
        # A wide Gold can contain millions of records per billing month.  Do
        # not make Trino hold two or more months of a high-cardinality GROUP BY
        # at once: aggregate one partition at a time and combine the already
        # small daily aggregates here.
        period_cursor = history_start.replace(day=1)
        period_values: list[str] = []
        while period_cursor <= effective_cutoff:
            period_values.append(period_cursor.strftime("%Y%m"))
            period_cursor = (period_cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        records: list[dict[str, Any]] = []
        for billing_period in period_values:
            sql = f"{select_sql} WHERE {type_filter} AND CAST({_q(period)} AS VARCHAR) = {_lit(billing_period)} AND {date_filter}{group_sql}"
            records.extend(await trino_execute(sql, timeout_s=300))
    else:
        sql = f"{select_sql} WHERE {type_filter} AND {date_filter}{group_sql}"
        records = await trino_execute(sql, timeout_s=300)
    frame = pd.DataFrame(records)
    if frame.empty:
        raise ValueError("A Gold não possui dados realizados para a configuração")
    frame["forecast_day"] = pd.to_datetime(frame["forecast_day"], errors="coerce")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce").fillna(0.0)
    frame = frame.dropna(subset=["forecast_day"])
    if frame.empty:
        raise ValueError("Nenhuma data válida foi encontrada na Gold")
    key_cols = dims
    results: list[dict[str, Any]] = []
    methods: dict[str, str] = {}
    for key, group_frame in frame.groupby(key_cols, dropna=False) if key_cols else [((), frame)]:
        if not isinstance(key, tuple): key = (key,)
        key_values = {col: (None if pd.isna(value) else value) for col, value in zip(key_cols, key)}
        history = group_frame.set_index("forecast_day")["value"].sort_index()
        history = history[history.index.date <= effective_cutoff]
        if history.empty: continue
        # Align every series to the same as-of calendar.  Without this,
        # ``tail(28)`` for a sparse series ended at its last historical
        # purchase instead of the current cutoff, turning a one-off purchase
        # into a large daily baseline.  Missing days are genuine zero
        # consumption for the mathematical Usage forecast.
        daily = history.resample("D").sum()
        daily = daily.reindex(
            pd.date_range(daily.index.min(), pd.Timestamp(effective_cutoff), freq="D"),
            fill_value=0.0,
        )
        candidates = []
        for window in [int(w) for w in config.enabled_windows or [7, 14, 28]]:
            sample = daily.tail(window)
            if len(sample) >= min(window, int(config.minimum_daily_points or 7)):
                candidates.append((f"MEAN_{window}D", float(sample.mean())))
        if not candidates:
            candidates = [("MEAN_AVAILABLE", float(daily.tail(7).mean()))]
        recent_window = min([int(w) for w in config.enabled_windows or [7, 14, 28]])
        extended_window = max([int(w) for w in config.enabled_windows or [7, 14, 28]])
        if len(daily) >= max(recent_window, int(config.minimum_daily_points or 7)):
            recent_mean = float(daily.tail(recent_window).mean())
            extended_mean = float(daily.tail(extended_window).mean())
            weight = max(0.0, min(1.0, float(config.recent_weight or 0.7)))
            candidates.append((f"WEIGHTED_{recent_window}D_{extended_window}D", recent_mean * weight + extended_mean * (1.0 - weight)))
        if config.model_mode == "AUTO_BACKTEST":
            scored = []
            validation = daily.tail(7)
            train = daily.iloc[:-7] if len(daily) > 7 else daily.iloc[0:0]
            for candidate_method, candidate_baseline in candidates:
                if candidate_method.startswith("WEIGHTED_"):
                    parts = candidate_method.split("_")
                    recent_w = int(parts[1][:-1]); extended_w = int(parts[2][:-1])
                    if len(train) >= extended_w:
                        recent_mean = float(train.tail(recent_w).mean())
                        extended_mean = float(train.tail(extended_w).mean())
                        weight = max(0.0, min(1.0, float(config.recent_weight or 0.7)))
                        prediction = recent_mean * weight + extended_mean * (1.0 - weight)
                    else:
                        prediction = float(candidate_baseline)
                    window = extended_w
                else:
                    window = int(candidate_method.split("_")[1][:-1]) if candidate_method.startswith("MEAN_") and candidate_method != "MEAN_AVAILABLE" else min(7, len(train))
                    prediction = float(train.tail(window).mean()) if len(train) >= window else float(candidate_baseline)
                if len(train) >= window and len(validation) > 0:
                    denominator = float(validation.abs().sum())
                    wape = float((validation - prediction).abs().sum()) / denominator if denominator else 0.0
                else:
                    wape = 0.0 if candidate_method == candidates[0][0] else 1.0
                scored.append((wape, candidate_method, candidate_baseline))
            _, method, baseline = min(scored, key=lambda item: item[0])
        else:
            if config.model_mode == "WEIGHTED":
                selected = next((item for item in candidates if item[0].startswith("WEIGHTED_")), None)
                if not selected:
                    raise ValueError("Dados insuficientes para aplicar média ponderada")
                method, baseline = selected
            elif config.model_mode.startswith("MEAN_"):
                selected = next((item for item in candidates if item[0] == config.model_mode), None)
                if not selected:
                    raise ValueError(f"Dados insuficientes para aplicar {config.model_mode}")
                method, baseline = selected
            else:
                method, baseline = candidates[0]
        methods[method] = methods.get(method, 0) + 1
        # Realized part of current month, followed by forecast remainder.
        current = effective_cutoff.replace(day=1)
        last_day = calendar.monthrange(effective_cutoff.year, effective_cutoff.month)[1]
        realized_value = float(history[history.index.to_period("M") == pd.Timestamp(effective_cutoff).to_period("M")].sum())
        remaining_days = last_day - effective_cutoff.day
        if remaining_days > 0:
            results.append({**key_values, "forecast_month": f"{effective_cutoff.year:04d}{effective_cutoff.month:02d}", "tipo": "forecast", "forecast_value": baseline * remaining_days, "selected_method": method, "fallback_level": 0, "history_points": int(len(daily)), "backtest_wape": None})
        results.append({**key_values, "forecast_month": f"{effective_cutoff.year:04d}{effective_cutoff.month:02d}", "tipo": "realizado", "forecast_value": realized_value, "selected_method": "REALIZADO", "fallback_level": 0, "history_points": int(len(daily)), "backtest_wape": None})
        for month_offset in range(1, int(config.horizon_months) + 1):
            month = effective_cutoff.month - 1 + month_offset
            year = effective_cutoff.year + month // 12
            month = month % 12 + 1
            value = baseline * ((1.0 + float(config.growth_rate)) ** month_offset) * calendar.monthrange(year, month)[1]
            results.append({**key_values, "forecast_month": f"{year:04d}{month:02d}", "tipo": "forecast", "forecast_value": value, "selected_method": method, "fallback_level": 0, "history_points": int(len(daily)), "backtest_wape": None})
    output = pd.DataFrame(results)
    if output.empty: raise ValueError("Nenhuma série pôde ser projetada")
    applied_rules = []
    # Rules are metadata-driven. Scope fields are deliberately limited to
    # discovered series dimensions; no provider-specific column is assumed.
    for rule in sorted(business_rules or [], key=lambda item: item.priority):
        mask = output["tipo"].eq("forecast")
        # A rule must identify a series.  Earlier versions ignored
        # identification_column/values when that field was not one of the
        # configured dimensions; a Refund rule with no scope consequently
        # cancelled the entire forecast.  Never promote such a rule to a
        # global action silently.
        identification_column = str(getattr(rule, "identification_column", "") or "")
        identification_values = list(getattr(rule, "identification_values", None) or [])
        output_by_lower = {str(column).lower(): column for column in output.columns}
        identity_field = output_by_lower.get(identification_column.lower())
        has_identity_filter = False
        if identification_values:
            if identity_field is None:
                applied_rules.append(f"{rule.name} (não aplicada: campo fora das dimensões)")
                continue
            mask &= output[identity_field].astype(str).isin([str(value) for value in identification_values])
            has_identity_filter = True
        for column, value in (rule.scope or {}).items():
            scope_field = output_by_lower.get(str(column).lower())
            if scope_field is None:
                raise ValueError(f"Regra '{rule.name}' usa escopo fora das dimensões da série: {column}")
            mask &= output[scope_field].astype(str).eq(str(value))
            has_identity_filter = True
        if not has_identity_filter:
            applied_rules.append(f"{rule.name} (não aplicada: sem identificação/escopo)")
            continue
        if rule.start_period:
            mask &= output["forecast_month"] >= rule.start_period
        if rule.end_period:
            mask &= output["forecast_month"] <= rule.end_period
        if rule.behavior == "CANCEL_ON_REFUND" or (rule.charge_type.lower() == "refund" and rule.cancel_on_refund):
            output.loc[mask, "forecast_value"] = 0.0
        elif rule.behavior in {"REPEAT_NEXT_MONTH", "RECURRING_MONTHLY", "RECURRING_UNTIL_END", "REPLACE_VALUE"}:
            if rule.replacement_value is not None:
                output.loc[mask, "forecast_value"] = float(rule.replacement_value)
            if rule.growth_rate:
                periods = pd.to_numeric(output.loc[mask, "forecast_month"].str[-2:], errors="coerce").fillna(1)
                output.loc[mask, "forecast_value"] *= (1.0 + float(rule.growth_rate)) ** (periods - periods.min())
        applied_rules.append(rule.name)
    for adjustment in sorted(adjustments, key=lambda a: a.priority):
        mask = pd.Series(True, index=output.index)
        for column, value in (adjustment.filters or {}).items():
            if column in output: mask &= output[column].astype(str).eq(str(value))
        if adjustment.start_period: mask &= output["forecast_month"] >= adjustment.start_period
        if adjustment.end_period: mask &= output["forecast_month"] <= adjustment.end_period
        if adjustment.adjustment_type == "PERCENTAGE": output.loc[mask, "forecast_value"] *= 1 + float(adjustment.percentage or 0)
        elif adjustment.adjustment_type == "FIXED_VALUE": output.loc[mask, "forecast_value"] = float(adjustment.fixed_value or 0)
        elif adjustment.adjustment_type == "ADD_VALUE": output.loc[mask, "forecast_value"] += float(adjustment.fixed_value or 0)
        elif adjustment.adjustment_type == "SUBTRACT_VALUE": output.loc[mask, "forecast_value"] -= float(adjustment.fixed_value or 0)
        elif adjustment.adjustment_type == "ZERO": output.loc[mask, "forecast_value"] = 0.0
    # A forecast run is an immutable audit snapshot.  Callers pass the run ID
    # so two executions on the same day can never overwrite each other.
    result_table = result_table or f"result_{config.id}_{int(cutoff.strftime('%Y%m%d'))}"
    iceberg.write_forecast_result(result_table, output)
    planning_table = iceberg.write_forecast_planning(table, output, value_column=measure)
    return {"rows": len(output), "table": result_table, "planning_table": planning_table, "methods": methods, "series": len(output.groupby(key_cols)) if key_cols else 1, "business_rules": applied_rules, "collected_through": collected_through.isoformat(), "effective_cutoff": effective_cutoff.isoformat()}
