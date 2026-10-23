"""Small orchestration layer for bounded provider metric queries."""
from __future__ import annotations

from datetime import datetime
import uuid

from .contracts import MetricDescriptor, MetricScope, QueryPlan, QueryResult
from .registry import MetricsAdapter


DEFAULT_STATISTICS = ("mean", "min", "max", "count", "sum", "p90", "p95", "p99")


def plan_queries(
    scope: MetricScope, descriptors: tuple[MetricDescriptor, ...], *,
    start_utc: datetime, end_utc: datetime, resolution_seconds: int,
    statistics: tuple[str, ...] = DEFAULT_STATISTICS,
) -> tuple[QueryPlan, ...]:
    """Validate limits and build plans; query cost is exposed before execution."""
    scope.validate()
    if start_utc.tzinfo is None or end_utc.tzinfo is None:
        raise ValueError("query window must use timezone-aware datetimes")
    if start_utc >= end_utc:
        raise ValueError("query start must precede query end")
    if not descriptors:
        raise ValueError("at least one metric descriptor is required")
    if not statistics:
        raise ValueError("at least one statistic is required")
    plans = []
    for descriptor in descriptors:
        if descriptor.provider.lower() != scope.provider.lower():
            raise ValueError("metric provider does not match scope provider")
        if resolution_seconds not in descriptor.supported_resolutions_seconds:
            raise ValueError(f"unsupported resolution for {descriptor.name}: {resolution_seconds}")
        unsupported = set(statistics) - set(descriptor.native_statistics) - {
            f"p{p}" for p in descriptor.native_percentiles
        }
        if unsupported:
            raise ValueError(
                f"unsupported statistics for {descriptor.name}: {', '.join(sorted(unsupported))}"
            )
        plan = QueryPlan(
            descriptor=descriptor, scope=scope, start_utc=start_utc, end_utc=end_utc,
            resolution_seconds=resolution_seconds, statistics=statistics,
            request_id=str(uuid.uuid4()),
        )
        plan.validate()
        plans.append(plan)
    return tuple(plans)


def estimated_api_calls(plans: tuple[QueryPlan, ...]) -> int:
    """OCI issues one Monitoring query per resource and statistic."""
    return sum(len(plan.scope.resource_ids) * len(plan.statistics) for plan in plans)


def collect(adapter: MetricsAdapter, plans: tuple[QueryPlan, ...]) -> tuple[QueryResult, ...]:
    """Execute an already-reviewed plan; never discover or expand its resources."""
    if not plans:
        return ()
    for plan in plans:
        if not plan.scope.enabled:
            raise PermissionError("infrastructure metric collection is disabled for this scope")
        if adapter.provider.lower() != plan.scope.provider.lower():
            raise ValueError("adapter provider does not match query scope")
    return tuple(adapter.fetch(plan) for plan in plans)
