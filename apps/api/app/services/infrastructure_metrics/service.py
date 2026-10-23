"""Explicit OCI infrastructure-metric collection entry point."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any
import uuid

from .catalog import get_metric
from .collector import collect, estimated_api_calls, plan_queries
from .config import config_from_connection
from .contracts import CollectionCancelled, QueryResult
from .credentials import build_oci_adapter
from .normalization import query_results_to_silver_rows


def collect_oci_metrics(
    connection: Any, *, start_utc: datetime, end_utc: datetime,
    resolution_seconds: int, statistics: tuple[str, ...] =
        ("mean", "min", "max", "count", "sum", "p90", "p95", "p99"),
    collection_run_id: str | None = None, persist: bool = False,
    should_abort=None,
) -> dict:
    """Run the configured, allowlisted OCI pilot and return normalized rows.

    Persistence is opt-in. When enabled, rows are merged and atomically
    published by UTC day; scheduled collection remains disabled.
    """
    config = config_from_connection(connection)
    scope = config.scope(int(connection.id))
    if start_utc.tzinfo is None or end_utc.tzinfo is None:
        raise ValueError("collection window must use timezone-aware datetimes")
    if end_utc <= start_utc or end_utc - start_utc > timedelta(hours=24):
        raise ValueError("pilot collection window must be positive and at most 24 hours")
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
    if persist:
        from .publication import ensure_metric_series_table

        ensure_metric_series_table()
    adapter = build_oci_adapter(connection, config)
    if should_abort:
        adapter._should_abort = should_abort
        if should_abort():
            raise CollectionCancelled("infrastructure metric collection cancelled")
    results: tuple[QueryResult, ...] = collect(adapter, plans)
    if should_abort and should_abort():
        raise CollectionCancelled("infrastructure metric collection cancelled")
    run_id = collection_run_id or str(uuid.uuid4())
    raw_persisted_count = 0
    if persist:
        if should_abort and should_abort():
            raise CollectionCancelled("infrastructure metric collection cancelled")
        # Preserve the provider responses before normalization/derivations can
        # fail. This keeps an acquired run recoverable from Bronze.
        from .bronze import persist_raw_query_results

        raw_persisted_count = persist_raw_query_results(
            connection_code=str(connection.code), connection_id=int(connection.id),
            collection_run_id=run_id, results=results,
        )
        if raw_persisted_count != estimated_calls:
            raise RuntimeError("raw metric responses were not fully persisted to Bronze")
    rows = query_results_to_silver_rows(
        results, source_owner="oci_infrastructure_metrics",
        collection_run_id=run_id, bronze_persisted=persist,
    )
    from .counters import derive_counter_rates

    rows.extend(derive_counter_rates(rows))
    persisted_by_day: dict[str, int] = {}
    if persist:
        from .publication import publish_metric_rows
        rows_by_day: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            row["_billing_period"] = row["metric_day"]
            rows_by_day[str(row["metric_day"])].append(row)
        for metric_day, day_rows in sorted(rows_by_day.items()):
            if should_abort and should_abort():
                raise CollectionCancelled("infrastructure metric collection cancelled")
            persisted_by_day[metric_day] = publish_metric_rows(
                day_rows, connection_id=int(connection.id), metric_day=metric_day,
            )
    call_statuses = [
        int(raw.get("status_code") or result.status_code)
        for result in results for raw in getattr(result, "raw_payloads", ())
    ]
    if not call_statuses:
        # Keep adapters used by callers/tests that aggregate the call status.
        call_statuses = [result.status_code for result in results
                         for _ in range(max(1, int(getattr(result, "attempt_count", 1))))]
    return {
        "provider": "oci",
        "connection_id": int(connection.id),
        "region": config.region,
        "resource_count": len(scope.resource_ids),
        "metric_count": len(descriptors),
        "resolution_seconds": resolution_seconds,
        "statistics": list(statistics),
        "estimated_api_calls": estimated_calls,
        # These counts are HTTP attempts, not metric descriptors.
        "completed_query_count": sum(status < 400 for status in call_statuses),
        "failed_query_count": sum(status >= 400 for status in call_statuses),
        "rows_prepared": rows,
        "persistence_enabled": persist,
        "raw_rows_persisted": raw_persisted_count,
        "persisted_by_day": persisted_by_day,
    }
