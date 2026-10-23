"""Run the generic rightsizing engine over the shared Silver and persist results.

Nothing here is cloud specific. The series come from
``compute_usage_metrics`` (cloud-neutral columns filled by catalog mappings),
and the source's provider registration supplies the scope, capacity, sizing
catalog and price books.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any

from sqlalchemy import select

from .opportunities import (
    CPU_METRIC, ENGINE_VERSION, MEMORY_METRIC, READY, RIGHTSIZE_DOWN,
    analyze_resource, summarize_series, window_bounds,
)
from .policy import PARAMETER_KEYS, load_active_policy
from .registry import provider_for_connection, rightsizing_for
from .sizing import CapacitySnapshot

ANALYZED_METRICS = (CPU_METRIC, MEMORY_METRIC)
SILVER_TABLE = "compute_usage_metrics"
# Statistic names of the Silver → fields the engine summarizes.
STATISTIC_FIELDS = {"mean": "value_average", "max": "value_maximum", "p95": "native_p95", "p99": "native_p99"}
_SAFE = re.compile(r"^[A-Za-z0-9._:\-]+$")


def _literal(value: str) -> str:
    if not _SAFE.match(value):
        raise ValueError(f"valor inválido para filtro da Silver: {value!r}")
    return f"'{value}'"


def _periods(start: datetime, end: datetime) -> list[str]:
    """Billing periods (YYYYMM) touched by [start, end)."""
    periods, month = [], start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while month < end:
        periods.append(month.strftime("%Y%m"))
        month = (month + timedelta(days=32)).replace(day=1)
    return periods


def read_series(
    connection_id: int, resource_id: str, metric: str, start: datetime, end: datetime,
    resolution_seconds: int = 300,
) -> list[dict]:
    """Buckets of one canonical series, one dict per bucket with its statistics."""
    from app.services import lakehouse_layers as layers
    from app.services.trino_service import execute_sync

    rows = execute_sync(
        f"SELECT bucket_start_utc AS bucket, statistic, CAST(value AS DOUBLE) AS value "
        f"FROM {layers.detail_layer()}.{SILVER_TABLE} "
        f"WHERE source_connection_id = {int(connection_id)} AND resource_id = {_literal(resource_id)} "
        f"AND metric = {_literal(metric)} AND CAST(resolution_seconds AS INTEGER) = {int(resolution_seconds)} "
        # TIMESTAMPTZ destination columns land as ISO-8601 UTC text
        # ("2026-10-01T00:05:00+00:00"): comparing the text keeps the order and
        # lets Iceberg prune files; the month filter prunes partitions.
        f"AND _billing_period IN ({', '.join(repr(period) for period in _periods(start, end))}) "
        f"AND bucket_start_utc >= '{start:%Y-%m-%dT%H:%M:%S}' AND bucket_start_utc < '{end:%Y-%m-%dT%H:%M:%S}'",
        timeout_s=180,
    )
    by_bucket: dict[str, dict] = {}
    for row in rows:
        field = STATISTIC_FIELDS.get(str(row["statistic"]))
        if field is None or row["value"] is None:
            continue
        bucket = str(row["bucket"])[:19].replace(" ", "T")
        by_bucket.setdefault(bucket, {"bucket_start_utc": bucket, "origin_kind": "NATIVE"})[field] = float(row["value"])
    return [by_bucket[key] for key in sorted(by_bucket)]


def read_hourly_profiles(
    connection_id: int, resource_ids: list[str], start: datetime, end: datetime,
    resolution_seconds: int = 300,
) -> dict[str, dict[str, dict[str, dict]]]:
    """Hourly P95 of bucket averages and hourly peak, per resource and metric.

    One aggregated query for every resource of a run (the mini charts), instead
    of shipping each host's 5-minute buckets. Same pruning as ``read_series``.
    Returns ``{resource_id: {metric: {"YYYY-MM-DDTHH": {"p95", "peak"}}}}``.
    """
    from app.services import lakehouse_layers as layers
    from app.services.trino_service import execute_sync

    if not resource_ids:
        return {}
    rows = execute_sync(
        f"SELECT resource_id, metric, substr(bucket_start_utc, 1, 13) AS hour, "
        f"approx_percentile(CASE WHEN statistic = 'mean' THEN CAST(value AS DOUBLE) END, 0.95) AS p95, "
        f"max(CASE WHEN statistic IN ('mean', 'max') THEN CAST(value AS DOUBLE) END) AS peak "
        f"FROM {layers.detail_layer()}.{SILVER_TABLE} "
        f"WHERE source_connection_id = {int(connection_id)} "
        f"AND resource_id IN ({', '.join(_literal(item) for item in resource_ids)}) "
        f"AND metric IN ({', '.join(_literal(metric) for metric in ANALYZED_METRICS)}) "
        f"AND CAST(resolution_seconds AS INTEGER) = {int(resolution_seconds)} "
        f"AND _billing_period IN ({', '.join(repr(period) for period in _periods(start, end))}) "
        f"AND bucket_start_utc >= '{start:%Y-%m-%dT%H:%M:%S}' AND bucket_start_utc < '{end:%Y-%m-%dT%H:%M:%S}' "
        f"GROUP BY 1, 2, 3",
        timeout_s=180,
    )
    profiles: dict[str, dict[str, dict[str, dict]]] = {}
    for row in rows:
        if row["p95"] is None and row["peak"] is None:
            continue
        hour = str(row["hour"]).replace(" ", "T")
        profiles.setdefault(str(row["resource_id"]), {}).setdefault(str(row["metric"]), {})[hour] = {
            "p95": row["p95"], "peak": row["peak"],
        }
    return profiles


def run_opportunity_engine(
    session: Any, connection: Any, *, days: int = 7, resolution_seconds: int = 300,
    user_id: int | None = None, as_of: datetime | None = None, rightsizing=None,
    series_reader=read_series, policy: dict | None = None,
):
    """Analyze every allowlisted resource and persist one run with its results.

    The policy is the saved active revision (``DEFAULT_POLICY`` when none was
    saved); the run records its revision and exact values.
    """
    from app.models.infrastructure_opportunity import InfraOpportunity, InfraOpportunityRun

    if resolution_seconds not in (60, 300, 900):
        raise ValueError("resolution_seconds must be 60, 300 or 900")
    registration = provider_for_connection(connection)
    config = registration.config_parser(connection)
    if not config.enabled:
        raise PermissionError("infrastructure metric collection is disabled")
    rightsizing = rightsizing or rightsizing_for(registration.provider)
    start, end = window_bounds(as_of or datetime.now(timezone.utc), days)
    # A source that only keeps N days at this resolution (OCI 5m: 30 days)
    # cannot have data before that edge; analyzing from there keeps the
    # coverage honest instead of counting the provider limit as a gap.
    earliest = getattr(config, "earliest_available_utc", None)
    if callable(earliest) and resolution_seconds == 300:
        oldest = earliest(as_of)
        if oldest is not None and oldest > start:
            start = oldest
    policy = policy or load_active_policy(session)
    run = InfraOpportunityRun(
        connection_id=int(connection.id), status="RUNNING", engine_version=ENGINE_VERSION,
        policy_version=policy["version"], policy_revision=policy.get("revision"),
        policy_aggressiveness=policy.get("aggressiveness"),
        policy_params={key: policy[key] for key in PARAMETER_KEYS},
        window_start_utc=start, window_end_utc=end,
        resolution_seconds=resolution_seconds, created_by=user_id, readiness_counts={},
    )
    session.add(run)
    session.commit()
    try:
        capacity = rightsizing.read_capacity(connection, config)
        price_books = rightsizing.price_books(tuple(config.resource_ids))
        previous = {
            (item.resource_id, item.action_type, str(item.to_config)): item
            for item in session.execute(
                select(InfraOpportunity).where(InfraOpportunity.connection_id == int(connection.id))
                .order_by(InfraOpportunity.id)
            ).scalars()
        }
        results = []
        for resource_id in config.resource_ids:
            stats = {
                metric: summarize_series(
                    series_reader(int(connection.id), resource_id, metric, start, end, resolution_seconds),
                    window_start=start, window_end=end, resolution_seconds=resolution_seconds,
                )
                for metric in ANALYZED_METRICS
            }
            snapshot = capacity.get(resource_id)
            result = analyze_resource(
                resource_id=resource_id,
                capacity=snapshot if isinstance(snapshot, CapacitySnapshot) else None,
                cpu=stats[CPU_METRIC] if stats[CPU_METRIC]["samples"] else None,
                memory=stats[MEMORY_METRIC] if stats[MEMORY_METRIC]["samples"] else None,
                catalog=rightsizing.sizing_catalog, price_book=price_books.get(resource_id), policy=policy,
            )
            prior = previous.get((resource_id, result["action_type"], str(result["to_config"])))
            session.add(InfraOpportunity(
                run_id=run.id, connection_id=int(connection.id), provider=registration.provider,
                resource_id=resource_id, resource_name=result["resource_name"],
                action_type=result["action_type"], readiness_status=result["readiness_status"],
                state=prior.state if prior else "NEW", state_note=prior.state_note if prior else None,
                confidence=result["confidence"], from_config=result["from_config"],
                to_config=result["to_config"], current_monthly_cost=result["current_monthly_cost"],
                target_monthly_cost=result["target_monthly_cost"], monthly_saving=result["monthly_saving"],
                currency=result["currency"], rationale=result["rationale"], risks=result["risks"],
                evidence=result["evidence"], inputs_hash=result["inputs_hash"],
                engine_version=result["engine_version"],
            ))
            results.append(result)
        savings = [
            item["monthly_saving"] for item in results
            if item["readiness_status"] == READY and item["action_type"] == RIGHTSIZE_DOWN
            and (item["monthly_saving"] or 0) > 0
        ]
        counts: dict[str, int] = {}
        for item in results:
            counts[item["readiness_status"]] = counts.get(item["readiness_status"], 0) + 1
        run.resources_analyzed = len(results)
        run.opportunities_count = len(savings)
        run.total_monthly_saving = round(sum(savings), 2) if savings else 0.0
        run.avg_monthly_saving = round(sum(savings) / len(savings), 2) if savings else None
        run.currency = next((item["currency"] for item in results if item["currency"]), None)
        run.readiness_counts = counts
        run.status = "COMPLETED"
    except Exception as exc:
        session.rollback()
        run = session.get(InfraOpportunityRun, run.id)
        run.status = "FAILED"
        run.error_message = f"{type(exc).__name__}: {exc}"[:2000]
    run.finished_at = datetime.now(timezone.utc)
    session.commit()
    return run
