"""Bounded OCI Monitoring adapter for allowlisted compute resources.

Authentication/client construction stays with ``OciExtractor`` and callers;
this adapter accepts an already-authorized MonitoringClient and never performs
tenancy-wide discovery.
"""
from __future__ import annotations

from datetime import datetime
import time
from typing import Any

from ..contracts import MetricPoint, MetricScope, QueryPlan, QueryResult


_STATISTIC_QUERY = {
    "mean": "mean()", "min": "min()", "max": "max()", "count": "count()",
    "sum": "sum()", "p90": "percentile(0.90)",
    "p95": "percentile(0.95)", "p99": "percentile(0.99)",
}


class OciMetricsAdapter:
    provider = "oci"

    def __init__(self, monitoring_client: Any, compartment_id: str):
        if not compartment_id:
            raise ValueError("compartment_id is required")
        self._client = monitoring_client
        self._compartment_id = compartment_id

    def discover(self, scope: MetricScope) -> tuple[str, ...]:
        """Return only the caller's explicit allowlist; do not expand scope."""
        self._validate_scope(scope)
        return scope.resource_ids

    def fetch(self, query: QueryPlan) -> QueryResult:
        self._validate_scope(query.scope)
        query.validate()
        if not query.scope.enabled:
            raise PermissionError("infrastructure metric collection is disabled for this scope")
        unsupported = set(query.statistics) - _STATISTIC_QUERY.keys()
        if unsupported:
            raise ValueError(f"Unsupported OCI statistics: {', '.join(sorted(unsupported))}")

        started = time.perf_counter()
        points_by_key: dict[tuple[datetime, tuple[tuple[str, str], ...]], dict[str, float | None]] = {}
        sample_periods: dict[tuple[datetime, tuple[tuple[str, str], ...]], int | None] = {}
        streams = pages = 0
        errors: list[tuple[int, str]] = []
        safe_request: dict[str, Any] = {
            "provider": "oci", "namespace": query.descriptor.namespace,
            "metric": query.descriptor.name, "resolution_seconds": query.resolution_seconds,
            "start_utc": query.start_utc.isoformat(), "end_utc": query.end_utc.isoformat(),
            "resource_count": len(query.scope.resource_ids),
            "statistics": list(query.statistics),
        }

        for resource_id in query.scope.resource_ids:
            resolution_minutes = query.resolution_seconds // 60
            if query.resolution_seconds % 60:
                raise ValueError("OCI resolution must be a whole number of minutes")
            for statistic in query.statistics:
                expression = _STATISTIC_QUERY[statistic]
                metric_query = (
                    f'{query.descriptor.name}[{resolution_minutes}m]'
                    f'{{resourceId = "{resource_id}"}}.{expression}'
                )
                details = _summarize_details(query, metric_query, resolution_minutes)
                try:
                    response = self._client.summarize_metrics_data(
                        compartment_id=self._compartment_id,
                        summarize_metrics_data_details=details,
                    )
                    pages += 1
                    for stream in response.data or ():
                        streams += 1
                        dimensions = dict(stream.dimensions or {})
                        labels = tuple(sorted((str(k), str(v)) for k, v in dimensions.items()))
                        for point in stream.aggregated_datapoints or ():
                            timestamp = point.timestamp
                            if not query.start_utc <= timestamp < query.end_utc:
                                continue
                            key = (timestamp, labels)
                            points_by_key.setdefault(key, {})[statistic] = (
                                float(point.value) if point.value is not None else None
                            )
                            sample_periods[key] = query.descriptor.source_sample_period_seconds
                except Exception as exc:  # each statistic is independently optional
                    status = int(getattr(exc, "status", 0) or 0)
                    code = str(getattr(exc, "code", "SOURCE_ERROR"))
                    errors.append((status, code))

        points = tuple(
            MetricPoint(
                timestamp_utc=timestamp,
                dimensions=dict(labels),
                values=values,
                source_sample_period_seconds=sample_periods.get((timestamp, labels)),
            )
            for (timestamp, labels), values in sorted(points_by_key.items())
        )
        latency = int((time.perf_counter() - started) * 1000)
        status_code = 200 if not errors else (errors[0][0] or 502)
        return QueryResult(
            plan=query, status_code=status_code, points=points,
            sanitized_request=safe_request, page_count=pages,
            attempt_count=len(query.statistics), latency_ms=latency,
            stream_count=streams,
            error_code=errors[0][1] if errors else None,
        )

    def _validate_scope(self, scope: MetricScope) -> None:
        scope.validate()
        if scope.provider.lower() != self.provider:
            raise ValueError("scope provider does not match OCI adapter")


def _summarize_details(query: QueryPlan, metric_query: str, resolution_minutes: int):
    """Construct OCI SDK request lazily so contract tests need no OCI SDK."""
    try:
        import oci
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("OCI SDK is required for live infrastructure metrics") from exc
    return oci.monitoring.models.SummarizeMetricsDataDetails(
        namespace=query.descriptor.namespace,
        query=metric_query,
        resolution=f"{resolution_minutes}m",
        start_time=query.start_utc,
        end_time=query.end_utc,
    )
