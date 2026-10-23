"""Explicit OCI infrastructure-metric collection entry point (no persistence)."""
from __future__ import annotations

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
    collection_run_id: str | None = None,
) -> dict:
    """Run the configured, allowlisted OCI pilot and return normalized rows.

    This function is intentionally not wired to a scheduler or a writer. The
    caller must review the estimate, choose a connection configured for the
    pilot, and separately implement atomic publication before persisting rows.
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
        "persistence_enabled": False,
    }
