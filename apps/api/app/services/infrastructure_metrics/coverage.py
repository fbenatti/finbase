"""Build explicit daily coverage facts, including requests with no samples."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Iterable

from .contracts import QueryResult
from .identity import canonical_json, dimensions_hash, series_id


def build_coverage_rows(
    results: Iterable[QueryResult], *, source_owner: str,
    collection_run_id: str,
) -> list[dict]:
    """Emit one coverage row per requested resource, metric and UTC day."""
    output: list[dict] = []
    for result in results:
        plan = result.plan
        descriptor = plan.descriptor
        scope = plan.scope
        start = plan.start_utc.astimezone(timezone.utc)
        end = plan.end_utc.astimezone(timezone.utc)
        current = start.replace(hour=0, minute=0, second=0, microsecond=0)
        while current < end:
            day_start = max(start, current)
            day_end = min(end, current + timedelta(days=1))
            expected = math.ceil((day_end - day_start).total_seconds() / plan.resolution_seconds)
            for resource_id in scope.resource_ids:
                points = []
                returned_stats: set[str] = set()
                for point in result.points:
                    point_resource = next((value for key, value in point.dimensions.items()
                                            if key.lower() == "resourceid"), None)
                    timestamp = point.timestamp_utc.astimezone(timezone.utc)
                    if point_resource == resource_id and day_start <= timestamp < day_end:
                        points.append((timestamp, point.values))
                        returned_stats.update(point.values)
                dimensions = {"resourceId": resource_id}
                identity = series_id(
                    provider=scope.provider, connection_id=scope.connection_id,
                    account_id=scope.account_id, region=scope.region,
                    resource_type="instance", resource_id=resource_id,
                    metric=descriptor.canonical_metric, dimensions=dimensions,
                    namespace=descriptor.namespace, metric_scope=descriptor.scope,
                    source_owner=source_owner,
                )
                timestamps = sorted({timestamp for timestamp, _values in points})
                gap_buckets = [max(0, int((timestamps[0] - day_start).total_seconds() // plan.resolution_seconds))] if timestamps else [expected]
                gap_buckets.extend(
                    max(0, int((right - left).total_seconds() // plan.resolution_seconds) - 1)
                    for left, right in zip(timestamps, timestamps[1:])
                )
                if timestamps:
                    gap_buckets.append(max(0, int((day_end - timestamps[-1]).total_seconds() // plan.resolution_seconds) - 1))
                observed = len(timestamps)
                errors = [{"status": int(raw.get("status_code") or result.status_code),
                           "code": raw.get("error_code")}
                          for raw in getattr(result, "raw_payloads", ()) if raw.get("error_code")]
                coverage_status = "NO_DATA" if observed == 0 else ("PARTIAL" if observed < expected or errors else "COMPLETE")
                output.append({
                    "cloud_provider": scope.provider.lower(),
                    "provider_account_id": scope.account_id,
                    "sub_account_id": None,
                    "region_id": scope.region,
                    "resource_id": resource_id,
                    "series_id": identity,
                    "metric_namespace": descriptor.namespace,
                    "source_metric_name": descriptor.name,
                    "metric_scope": descriptor.scope,
                    "dimensions_hash": dimensions_hash(dimensions),
                    "resolution_seconds": plan.resolution_seconds,
                    "origin_kind": "NATIVE",
                    "metric_day": current.strftime("%Y%m%d"),
                    "window_start_utc": day_start.replace(tzinfo=None),
                    "window_end_utc": day_end.replace(tzinfo=None),
                    "effective_start_utc": timestamps[0].replace(tzinfo=None) if timestamps else None,
                    "effective_end_utc": timestamps[-1].replace(tzinfo=None) if timestamps else None,
                    "expected_buckets": expected,
                    "observed_buckets": observed,
                    "duplicate_buckets": max(0, len(points) - observed),
                    "missing_buckets": max(0, expected - observed),
                    "first_point_utc": timestamps[0].replace(tzinfo=None) if timestamps else None,
                    "last_point_utc": timestamps[-1].replace(tzinfo=None) if timestamps else None,
                    "largest_gap_seconds": max(gap_buckets, default=0) * plan.resolution_seconds,
                    "gap_spans_json": canonical_json({"largest_gap_buckets": max(gap_buckets, default=0)}),
                    "statistics_status_json": canonical_json({
                        "requested": list(plan.statistics), "returned": sorted(returned_stats),
                        "errors": errors,
                    }),
                    "coverage_ratio": observed / expected if expected else 0.0,
                    "coverage_status": coverage_status,
                    "coverage_reason": errors[0]["code"] if errors else ("NO_SAMPLES" if not observed else None),
                    "collection_run_id": collection_run_id,
                    "_source_connection_id": scope.connection_id,
                    "_silver_owner": source_owner,
                    "_processed_at": datetime.now(timezone.utc).isoformat(),
                })
            current += timedelta(days=1)
    return output
