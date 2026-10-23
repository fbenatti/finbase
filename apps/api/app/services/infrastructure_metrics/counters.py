"""Derive explicit interval rates from cumulative OCI counter bucket means."""
from __future__ import annotations

from datetime import timezone
from collections import defaultdict
from typing import Iterable, Mapping

from .identity import dimensions_hash, series_id


def derive_counter_rates(rows: Iterable[Mapping]) -> list[dict]:
    """Create derived rate rows while retaining every original counter row.

    Rates are differences between adjacent bucket means. The first bucket,
    counter resets, and non-adjacent buckets remain null with a quality reason;
    a gap is never interpreted as zero. This is a bucket-level estimate, not
    reconstruction of the provider's original 10-second observations.
    """
    grouped: dict[tuple[str, int, str], list[Mapping]] = defaultdict(list)
    for row in rows:
        if row.get("metric_kind") != "COUNTER" or row.get("value_average") is None:
            continue
        if not row.get("series_id") or not row.get("resolution_seconds"):
            continue
        grouped[(str(row["series_id"]), int(row["resolution_seconds"]),
                 str(row.get("origin_kind") or "NATIVE"))].append(row)

    output: list[dict] = []
    for (parent_id, resolution_seconds, _origin_kind), series_rows in grouped.items():
        ordered = sorted(series_rows, key=lambda row: row["bucket_start_utc"])
        previous = None
        for row in ordered:
            current_time = row["bucket_start_utc"]
            if current_time.tzinfo is None:
                current_time = current_time.replace(tzinfo=timezone.utc)
            current_time = current_time.astimezone(timezone.utc)
            resolution = int(row["resolution_seconds"])
            current_value = float(row["value_average"])
            derived = dict(row)
            derived_metric = str(row.get("metric", "counter")).removesuffix("_counter") + "_rate"
            derived_series_id = series_id(
                provider=str(row["cloud_provider"]),
                connection_id=int(row["_source_connection_id"]),
                account_id=str(row.get("provider_account_id") or ""),
                region=str(row.get("region_id") or ""),
                resource_type=str(row.get("resource_type") or ""),
                resource_id=str(row["resource_id"]), metric=derived_metric,
                dimensions={"dimensions_hash": row.get("dimensions_hash"),
                            "resolution_seconds": resolution_seconds},
            )
            reason = None
            rate = None
            if previous is None:
                reason = "NO_PREVIOUS_BUCKET"
            else:
                previous_time, previous_value = previous
                elapsed = (current_time - previous_time).total_seconds()
                if elapsed != resolution:
                    reason = "NON_ADJACENT_BUCKET"
                elif current_value < previous_value:
                    reason = "COUNTER_RESET"
                else:
                    rate = (current_value - previous_value) / elapsed

            derived.update({
                "metric": derived_metric,
                "series_id": derived_series_id,
                "metric_unit": f"{row.get('metric_unit')}/second",
                "metric_kind": "RATE",
                "canonical_value_statistic": "adjacent_bucket_mean_delta_per_second",
                "value": rate,
                "value_average": rate,
                "value_minimum": None,
                "value_maximum": None,
                "value_total": None,
                "sample_count": None,
                "value_rate": rate,
                "native_p90": None,
                "native_p95": None,
                "native_p99": None,
                "native_quantile_method": None,
                "native_quantile_basis": None,
                "origin_kind": "DERIVED_COUNTER_DELTA",
                "parent_series_id": parent_id,
                "parent_resolution_seconds": resolution,
                "quality_status": "OK" if rate is not None else "GAP",
                "quality_reason": reason,
                "dimensions_hash": dimensions_hash({
                    "parent_dimensions_hash": row.get("dimensions_hash"),
                    "derived_metric": derived_metric,
                }),
            })
            derived["dimensions_json"] = row.get("dimensions_json")
            output.append(derived)
            previous = (current_time, current_value)
    return output
