"""Deterministic P90/P95/P99 profiles over one explicitly selected bucket series."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import math
from typing import Iterable, Mapping

from .identity import canonical_json


def _linear_quantile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def build_window_profiles(
    rows: Iterable[Mapping], *, window_start: datetime, window_end: datetime,
    resolution_seconds: int, value_field: str = "value_average",
    algorithm_version: str = "linear_quantile_v1", policy_version: str = "default_v1",
) -> list[dict]:
    """Build profiles without mixing series, resolutions, or statistic bases.

    Input rows are assumed to be provider-normalized buckets. Points outside
    ``[window_start, window_end)`` are ignored; duplicate buckets with different
    values fail rather than silently double-weighting a timestamp.
    """
    if window_start.tzinfo is None or window_end.tzinfo is None:
        raise ValueError("profile windows must use timezone-aware datetimes")
    if window_start >= window_end:
        raise ValueError("profile start must precede profile end")
    if resolution_seconds <= 0:
        raise ValueError("resolution_seconds must be positive")
    start = window_start.astimezone(timezone.utc)
    end = window_end.astimezone(timezone.utc)
    groups: dict[tuple, dict[datetime, float]] = {}
    metadata: dict[tuple, dict] = {}

    for row in rows:
        point_time = row.get("bucket_start_utc")
        if point_time is None:
            continue
        if point_time.tzinfo is None:
            point_time = point_time.replace(tzinfo=timezone.utc)
        point_time = point_time.astimezone(timezone.utc)
        if not start <= point_time < end:
            continue
        if row.get("resolution_seconds") not in (None, resolution_seconds):
            continue
        value = row.get(value_field)
        if value is None:
            continue
        numeric = float(value)
        if not math.isfinite(numeric):
            continue
        key = (
            row.get("_source_connection_id"), row.get("_silver_owner"),
            row.get("series_id"), resolution_seconds, value_field,
        )
        existing = groups.setdefault(key, {}).get(point_time)
        if existing is not None and existing != numeric:
            raise ValueError(f"conflicting values for one bucket at {point_time.isoformat()}")
        groups[key][point_time] = numeric
        metadata.setdefault(key, row)

    expected = max(1, math.ceil((end - start).total_seconds() / resolution_seconds))
    profiles = []
    for key, buckets in groups.items():
        values = list(buckets.values())
        ordered_times = sorted(buckets)
        # Count only missing buckets, including missing buckets at both window
        # edges; regular cadence is not itself a gap.
        gap_buckets = [max(0, int((ordered_times[0] - start).total_seconds() // resolution_seconds))]
        gap_buckets.extend(
            max(0, int((right - left).total_seconds() // resolution_seconds) - 1)
            for left, right in zip(ordered_times, ordered_times[1:])
        )
        gap_buckets.append(max(0, int((end - ordered_times[-1]).total_seconds() // resolution_seconds) - 1))
        coverage = min(1.0, len(values) / expected)
        row = metadata[key]
        identity_payload = {
            "key": key,
            "window_start": start.isoformat(), "window_end": end.isoformat(),
            "values": [[t.isoformat(), buckets[t]] for t in ordered_times],
            "algorithm_version": algorithm_version, "policy_version": policy_version,
            "capacity_snapshot_id": row.get("capacity_snapshot_id"),
        }
        profile_hash = hashlib.sha256(canonical_json(identity_payload).encode("utf-8")).hexdigest()
        profiles.append({
            "cloud_provider": row.get("cloud_provider"),
            "provider_account_id": row.get("provider_account_id"),
            "sub_account_id": row.get("sub_account_id"),
            "resource_id": row.get("resource_id"),
            "resource_type": row.get("resource_type"),
            "region_id": row.get("region_id"),
            "metric": row.get("metric"),
            "series_id": row.get("series_id"),
            "dimensions_hash": row.get("dimensions_hash"),
            "metric_scope": row.get("metric_scope"),
            "series_source_connection_id": key[0],
            "series_owner": key[1],
            "resolution_seconds": resolution_seconds,
            "value_statistic": value_field,
            "percentile_basis": "WINDOW_OF_BUCKET_VALUES",
            "quantile_method": "linear",
            "algorithm_version": algorithm_version,
            "policy_version": policy_version,
            "window_start_utc": start,
            "window_end_utc": end,
            "window_from_day": start.strftime("%Y%m%d"),
            "window_to_day": (end - timedelta(microseconds=1)).strftime("%Y%m%d"),
            "samples": len(values),
            "expected_buckets": expected,
            "observed_duration_seconds": min((end - start).total_seconds(), len(values) * resolution_seconds),
            "coverage": coverage,
            "largest_gap_seconds": max(gap_buckets, default=0) * resolution_seconds,
            "value_min": min(values),
            "p50": _linear_quantile(values, 50),
            "p90": _linear_quantile(values, 90),
            "p95": _linear_quantile(values, 95),
            "p99": _linear_quantile(values, 99),
            "value_max": max(values),
            "value_avg": sum(values) / len(values),
            "capacity_snapshot_id": row.get("capacity_snapshot_id"),
            "profile_hash": profile_hash,
            "profile_built_at": datetime.now(timezone.utc).isoformat(),
        })
    return profiles
