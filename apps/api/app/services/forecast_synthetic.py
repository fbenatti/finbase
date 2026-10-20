"""Project traced synthetic offsets from their source Usage forecast.

Classification may move offsets to a central RI department. Forecasting that
aggregate independently is not additive when AUTO_BACKTEST chooses different
windows for the individual consumer departments.
"""
from datetime import date

import pandas as pd


def _key(row, columns):
    return tuple(None if pd.isna(row.get(column)) else row.get(column) for column in columns)


def apply_linked_offsets(output, history, links, columns):
    """Allocate each source forecast by its observed, signed clone shares.

    Only traced synthetic rows are replaced. Real credits remain independent.
    Shares use exactly the source model's history window, never target actuals.
    """
    totals = {}
    for _, row in history.iterrows():
        key = _key(row, columns)
        totals[key] = totals.get(key, 0.0) + float(row["value"])
    forecasts = {}
    source_forecasts = {}
    destinations = {}
    for idx, row in output.iterrows():
        key = (_key(row, columns), str(row["forecast_month"]))
        forecasts[key] = forecasts.get(key, 0.0) + float(row["forecast_value"])
        source_forecasts.setdefault(key[0], {})[key[1]] = forecasts[key]
        destinations.setdefault(key, []).append(idx)
    weights = {}
    for row in links:
        origin = _key({c: row.get(f"source_{i}") for i, c in enumerate(columns)}, columns)
        target = _key({c: row.get(f"target_{i}") for i, c in enumerate(columns)}, columns)
        pair = (origin, target)
        weights[pair] = weights.get(pair, 0.0) + float(row["offset_value"])
    projected = {}
    for (origin, target), amount in weights.items():
        denominator = totals.get(origin, 0.0)
        share = amount / denominator if denominator else 0.0
        for month, forecast in source_forecasts.get(origin, {}).items():
            target_key = (target, month)
            if target_key not in destinations:
                raise ValueError("Synthetic forecast has no destination series for its traced source")
            projected[target_key] = projected.get(target_key, 0.0) + forecast * share
    for key, value in projected.items():
        indexes = destinations[key]
        # Multiple execution components can share a published dimension tuple.
        # Put the linked total on one row and clear the remaining components.
        output.loc[indexes, "forecast_value"] = 0.0
        output.loc[indexes[0], "forecast_value"] = value
        output.loc[indexes, "selected_method"] = "LINKED_SOURCE_USAGE"
    return len(projected)


async def align_synthetic_offsets(
    output, history, *, columns, metadata, source, measure, type_filter,
    date_expr, period_column, history_start: date, cutoff: date,
    cloud_cutoffs: dict[str, date], cloud_column: str | None, execute,
) -> int:
    names = {str(c["name"]).lower(): str(c["name"]) for c in metadata.get("columns", [])}
    required = {"is_synthetic", "clone_source_record_id", "source_record_id", "chargecategory"}
    if not required.issubset(names) or "is_synthetic" not in columns or names["chargecategory"] not in columns:
        return 0
    q = lambda name: '"' + str(name).replace('"', '""') + '"'
    lit = lambda value: "'" + str(value).replace("'", "''") + "'"
    span = cutoff - history_start
    earliest = min([history_start, *(day - span for day in cloud_cutoffs.values())])
    filters = [type_filter, f"{date_expr} BETWEEN DATE {lit(earliest)} AND DATE {lit(cutoff)}"]
    if period_column:
        filters.append(f"CAST({q(period_column)} AS VARCHAR) BETWEEN {lit(earliest.strftime('%Y%m'))} AND {lit(cutoff.strftime('%Y%m'))}")
    # Link identity includes connection and billing period: source row IDs can
    # repeat across invoices or connections. Both sides are partition-pruned.
    joins = [f"r.{q(names['clone_source_record_id'])} = u.{q(names['source_record_id'])}"]
    for name in (names.get("source_connection_id"), period_column):
        if name:
            joins.append(f"r.{q(name)} IS NOT DISTINCT FROM u.{q(name)}")
    selected = []
    for i, column in enumerate(columns):
        selected += [f"u.{q(column)} AS source_{i}", f"r.{q(column)} AS target_{i}"]
    selected += [f"r.{q('__offset_day')} AS offset_day", f"SUM(TRY_CAST(r.{q(measure)} AS DOUBLE)) AS offset_value"]
    grouped = ", ".join(str(i) for i in range(1, 2 * len(columns) + 2))
    sql = f"""
        WITH scoped AS (
            SELECT *, {date_expr} AS {q('__offset_day')} FROM {source}
             WHERE {' AND '.join(filters)}
        )
        SELECT {', '.join(selected)}
          FROM scoped r JOIN scoped u ON {' AND '.join(joins)}
         WHERE COALESCE(TRY_CAST(r.{q(names['is_synthetic'])} AS BOOLEAN), false)
           AND NOT COALESCE(TRY_CAST(u.{q(names['is_synthetic'])} AS BOOLEAN), false)
           AND lower(CAST(r.{q(names['chargecategory'])} AS VARCHAR)) = 'return'
         GROUP BY {grouped}
    """
    rows = await execute(sql, timeout_s=300)
    cloud_index = columns.index(cloud_column) if cloud_column in columns else None
    scoped_links = []
    for row in rows:
        cloud = str(row.get(f"source_{cloud_index}") or "").strip() if cloud_index is not None else ""
        end = cloud_cutoffs.get(cloud, cutoff)
        day = pd.Timestamp(row["offset_day"]).date()
        if end - span <= day <= end:
            scoped_links.append(row)
    return apply_linked_offsets(output, history, scoped_links, columns)
