"""Explicit OCI infrastructure-metric collection entry point."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any
import uuid

from .catalog import get_metric
from .collector import collect, estimated_api_calls, plan_queries
from .config import config_from_connection
from .contracts import QueryResult
from .credentials import build_oci_adapter
from .normalization import query_results_to_silver_rows


def collect_oci_metrics(
    connection: Any, *, start_utc: datetime, end_utc: datetime,
    resolution_seconds: int, statistics: tuple[str, ...] =
        ("mean", "min", "max", "count", "sum", "p90", "p95", "p99"),
    collection_run_id: str | None = None, persist: bool = False,
) -> dict:
    """Run the configured, allowlisted OCI pilot and return normalized rows.

    Persistence is opt-in. When enabled, rows are merged and atomically
    published by UTC day; scheduled collection remains disabled.
    """
    config = config_from_connection(connection)
    scope = config.scope(int(connection.id))
    if resolution_seconds not in config.resolutions_seconds:
        raise ValueError("requested resolution is not enabled in pilot configuration")
    descriptors = tuple(
        get_metric("oci", "oci_computeagent", metric_name)
        for metric_name in config.metric_names
    )
    plans = plan_queries(
        scope, descriptors, start_utc=start_utc, end_utc=end_utc,
        resolution_seconds=resolution_seconds, statistics=statistics,
    )
    estimated_calls = estimated_api_calls(plans)
    adapter = build_oci_adapter(connection, config)
    results: tuple[QueryResult, ...] = collect(adapter, plans)
    rows = query_results_to_silver_rows(
        results, source_owner="oci_infrastructure_metrics",
        collection_run_id=collection_run_id or str(uuid.uuid4()),
    )
    from .counters import derive_counter_rates

    rows.extend(derive_counter_rates(rows))
    persisted_by_day: dict[str, int] = {}
    if persist and rows:
        from .publication import publish_metric_rows

        rows_by_day: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            row["_billing_period"] = row["metric_day"]
            rows_by_day[str(row["metric_day"])].append(row)
        for metric_day, day_rows in sorted(rows_by_day.items()):
            persisted_by_day[metric_day] = publish_metric_rows(
                day_rows, connection_id=int(connection.id), metric_day=metric_day,
            )
    return {
        "provider": "oci",
        "connection_id": int(connection.id),
        "region": config.region,
        "resource_count": len(scope.resource_ids),
        "metric_count": len(descriptors),
        "resolution_seconds": resolution_seconds,
        "statistics": list(statistics),
        "estimated_api_calls": estimated_calls,
        "completed_query_count": len(results),
        "rows_prepared": rows,
        "persistence_enabled": persist,
        "persisted_by_day": persisted_by_day,
    }
