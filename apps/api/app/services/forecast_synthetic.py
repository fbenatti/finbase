"""Project traced synthetic offsets from their source Usage forecast.

Classification may move offsets to a central RI department. Forecasting that
aggregate independently is not additive when AUTO_BACKTEST chooses different
windows for the individual consumer departments.
"""
import logging
import time
from datetime import date

import pandas as pd

logger = logging.getLogger(__name__)


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
    # No distributed JOIN: on the small local cluster (2G heap per worker) any
    # self-join of the wide billing table took workers down (JVM OOM). Instead
    # run two plain aggregations - the same shape as the history query - and
    # link them in Python. Link identity includes connection and billing
    # period because source row IDs can repeat across invoices/connections.
    link_cols = [n for n in (names.get("source_connection_id"), period_column) if n]
    link_sql = "".join(f", COALESCE(CAST({q(n)} AS VARCHAR), '') AS link_{i}" for i, n in enumerate(link_cols))
    synth = names["is_synthetic"]
    cols_sql = ", ".join(f"{q(c)} AS dim_{i}" for i, c in enumerate(columns))
    where = " AND ".join(filters)
    is_synth = f"COALESCE(TRY_CAST({q(synth)} AS BOOLEAN), false)"
    clone_id = q(names["clone_source_record_id"])
    source_id = q(names["source_record_id"])
    n_keys = len(columns) + 1 + len(link_cols)
    returns_sql = f"""
        SELECT {cols_sql}, CAST({clone_id} AS VARCHAR) AS link_id{link_sql},
               {date_expr} AS offset_day,
               SUM(TRY_CAST({q(measure)} AS DOUBLE)) AS offset_value
          FROM {source}
         WHERE {where}
           AND {clone_id} IS NOT NULL
           AND {is_synth}
           AND lower(CAST({q(names['chargecategory'])} AS VARCHAR)) = 'return'
         GROUP BY {", ".join(str(i) for i in range(1, n_keys + 2))}
    """
    started = time.monotonic()
    logger.info("[forecast_synthetic] consultando retornos RI sintéticos (%s a %s)", earliest, cutoff)
    try:
        returns = await execute(returns_sql, timeout_s=300)
        logger.info("[forecast_synthetic] %d grupos de retorno em %.1fs", len(returns), time.monotonic() - started)
        link_key = lambda row: (str(row["link_id"]), *(str(row[f"link_{i}"]) for i in range(len(link_cols))))
        ids = sorted({str(row["link_id"]) for row in returns})
        usages: dict[tuple, list[dict]] = {}
        batch = 1000
        for start in range(0, len(ids), batch):
            chunk = ", ".join(lit(v) for v in ids[start:start + batch])
            usage_sql = f"""
                SELECT DISTINCT {cols_sql}, CAST({source_id} AS VARCHAR) AS link_id{link_sql}
                  FROM {source}
                 WHERE {where}
                   AND NOT {is_synth}
                   AND CAST({source_id} AS VARCHAR) IN ({chunk})
            """
            for row in await execute(usage_sql, timeout_s=300):
                usages.setdefault(link_key(row), []).append(row)
            logger.info("[forecast_synthetic] usos de origem: lote %d/%d", start // batch + 1, -(-len(ids) // batch))
    except Exception:
        logger.exception("[forecast_synthetic] falha na consulta de offsets após %.1fs", time.monotonic() - started)
        raise
    # Equivalent of the former JOIN + GROUP BY (source dims, target dims, day).
    grouped: dict[tuple, float] = {}
    for ret in returns:
        for use in usages.get(link_key(ret), []):
            key = (tuple(use[f"dim_{i}"] for i in range(len(columns))),
                   tuple(ret[f"dim_{i}"] for i in range(len(columns))), ret["offset_day"])
            grouped[key] = grouped.get(key, 0.0) + float(ret["offset_value"] or 0.0)
    rows = []
    for (src, tgt, day), value in grouped.items():
        row = {f"source_{i}": v for i, v in enumerate(src)}
        row.update({f"target_{i}": v for i, v in enumerate(tgt)})
        row.update(offset_day=day, offset_value=value)
        rows.append(row)
    logger.info("[forecast_synthetic] %d vínculos retornados em %.1fs", len(rows), time.monotonic() - started)
    cloud_index = columns.index(cloud_column) if cloud_column in columns else None
    scoped_links = []
    for row in rows:
        cloud = str(row.get(f"source_{cloud_index}") or "").strip() if cloud_index is not None else ""
        end = cloud_cutoffs.get(cloud, cutoff)
        day = pd.Timestamp(row["offset_day"]).date()
        if end - span <= day <= end:
            scoped_links.append(row)
    return apply_linked_offsets(output, history, scoped_links, columns)
