"""Deterministic 5m/15m rollups from the canonical 1m metric series."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from typing import Iterable, Mapping

from .identity import canonical_json


def build_metric_rollups(
    rows: Iterable[Mapping], *, collection_run_id: str,
    target_resolutions: tuple[int, ...] = (300, 900),
) -> list[dict]:
    """Derive wider buckets without combining provider-native percentiles."""
    base_rows = [dict(row) for row in rows
                 if row.get("origin_kind") == "NATIVE" and row.get("resolution_seconds") == 60]
    output: list[dict] = []
    for target in target_resolutions:
        if target not in (300, 900):
            raise ValueError("metric rollups support only 300s and 900s")
        grouped: dict[tuple, list[dict]] = {}
        for row in base_rows:
            timestamp = row["bucket_start_utc"]
            if isinstance(timestamp, datetime):
                stamp = timestamp.replace(tzinfo=timezone.utc) if timestamp.tzinfo is None else timestamp.astimezone(timezone.utc)
            else:
                stamp = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).astimezone(timezone.utc)
            epoch = int(stamp.timestamp())
            bucket_epoch = epoch // target * target
            key = (
                row.get("_source_connection_id"), row.get("_silver_owner"),
                row.get("cloud_provider"), row.get("provider_account_id"),
                row.get("sub_account_id"), row.get("region_id"), row.get("resource_id"),
                row.get("metric_namespace"), row.get("source_metric_name"),
                row.get("metric_scope"), row.get("dimensions_hash"),
                row.get("dimension_schema_version"), bucket_epoch,
            )
            grouped.setdefault(key, []).append(row)

        for key, bucket_rows in grouped.items():
            bucket_rows.sort(key=lambda row: str(row.get("bucket_start_utc")))
            first, last = bucket_rows[0], bucket_rows[-1]
            start = datetime.fromtimestamp(key[-1], tz=timezone.utc)
            counts = [float(row["sample_count"]) for row in bucket_rows if row.get("sample_count") is not None]
            totals = [float(row["value_total"]) for row in bucket_rows if row.get("value_total") is not None]
            means = [float(row["value_average"]) for row in bucket_rows if row.get("value_average") is not None]
            is_counter = first.get("metric_kind") == "COUNTER"
            weighted = not is_counter and len(counts) == len(bucket_rows) and len(totals) == len(bucket_rows) and sum(counts) > 0
            if is_counter:
                value_average = last.get("value_average")
                value = last.get("value")
            elif weighted:
                value_average = sum(totals) / sum(counts)
                value = value_average
            else:
                value_average = sum(means) / len(means) if means else None
                value = value_average
            expected = target // 60
            complete = len({row.get("bucket_start_utc") for row in bucket_rows}) == expected
            status = "OK" if complete and (weighted or is_counter) else "PARTIAL"
            lineage_input = [{
                "bucket_start_utc": str(row.get("bucket_start_utc")),
                "payload_hash": row.get("payload_hash"),
                "collection_run_id": row.get("collection_run_id"),
            } for row in bucket_rows]
            lineage_hash = hashlib.sha256(canonical_json(lineage_input).encode()).hexdigest()
            result = dict(first)
            result.update({
                "bucket_start_utc": start.replace(tzinfo=None),
                "bucket_end_utc": (start + timedelta(seconds=target)).replace(tzinfo=None),
                "aggregation_interval_seconds": target,
                "resolution_seconds": target,
                "source_sample_period_seconds": 60,
                "value_average": value_average,
                "value": value,
                "value_minimum": min((float(row["value_minimum"]) for row in bucket_rows if row.get("value_minimum") is not None), default=None),
                "value_maximum": max((float(row["value_maximum"]) for row in bucket_rows if row.get("value_maximum") is not None), default=None),
                "value_total": sum(totals) if len(totals) == len(bucket_rows) and not is_counter else None,
                "sample_count": sum(counts) if len(counts) == len(bucket_rows) else None,
                "value_rate": None,
                "native_p90": None, "native_p95": None, "native_p99": None,
                "native_quantile_method": None, "native_quantile_basis": None,
                "origin_kind": "DERIVED_ROLLUP",
                "parent_series_id": first.get("series_id"),
                "parent_resolution_seconds": 60,
                "source_resolution_verified": False,
                "collection_run_id": collection_run_id,
                "request_id": None, "payload_hash": lineage_hash,
                "statistic_lineage_json": canonical_json({
                    "rollup": {"method": "weighted_mean" if weighted else ("last_counter" if is_counter else "arithmetic_mean"),
                               "input_resolution_seconds": 60, "input_buckets": len(bucket_rows),
                               "input_hash": lineage_hash},
                }),
                "statistics_status_json": canonical_json({
                    "requested": ["mean", "min", "max", "count", "sum"],
                    "returned": ["mean", "min", "max"], "native_percentiles": "NOT_COMBINED",
                }),
                "quality_status": status,
                "quality_reason": None if status == "OK" else "INCOMPLETE_OR_UNWEIGHTED_ROLLUP",
                "metric_day": start.strftime("%Y%m%d"),
                "_silver_ingested_at": datetime.now(timezone.utc).isoformat(),
                "_billing_period": start.strftime("%Y%m%d"),
            })
            output.append(result)
    return output
