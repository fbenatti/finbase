"""Convert provider-neutral query results into the shared metric Silver shape."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Iterable

from .contracts import QueryResult
from .identity import canonical_json, dimensions_hash, series_id


_STAT_FIELD = {
    "mean": "value_average", "min": "value_minimum", "max": "value_maximum",
    "sum": "value_total", "count": "sample_count", "p90": "native_p90",
    "p95": "native_p95", "p99": "native_p99",
}


def query_results_to_silver_rows(
    results: Iterable[QueryResult], *, source_owner: str,
    collection_run_id: str, observed_at: datetime | None = None,
) -> list[dict]:
    """Return one wide row per provider stream and bucket, without writing data."""
    observed_at = observed_at or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    rows_by_key: dict[tuple, dict] = {}

    for result in results:
        plan = result.plan
        descriptor = plan.descriptor
        scope = plan.scope
        descriptor_payload = asdict(descriptor)
        if descriptor_payload["verified_at"] is not None:
            descriptor_payload["verified_at"] = descriptor_payload["verified_at"].isoformat()
        descriptor_hash = hashlib.sha256(canonical_json(descriptor_payload).encode("utf-8")).hexdigest()
        for point in result.points:
            timestamp = point.timestamp_utc
            if timestamp.tzinfo is None:
                raise ValueError("metric point timestamps must be timezone-aware")
            timestamp = timestamp.astimezone(timezone.utc)
            labels_json = canonical_json(dict(point.dimensions))
            labels_digest = dimensions_hash(point.dimensions)
            resource_id = next(
                (value for key, value in point.dimensions.items() if key.lower() == "resourceid"),
                scope.resource_ids[0] if len(scope.resource_ids) == 1 else None,
            )
            if not resource_id:
                raise ValueError("OCI metric stream has no resource identity")
            identity = series_id(
                provider=scope.provider, connection_id=scope.connection_id,
                account_id=scope.account_id, region=scope.region,
                resource_type="instance", resource_id=resource_id,
                metric=descriptor.canonical_metric, dimensions=point.dimensions,
                namespace=descriptor.namespace, metric_scope=descriptor.scope,
                source_owner=source_owner,
            )
            key = (identity, timestamp, plan.resolution_seconds)
            row = rows_by_key.get(key)
            if row is None:
                interval = plan.resolution_seconds
                row = {
                    "cloud_provider": scope.provider.lower(),
                    "cloud_partition": None,
                    "provider_account_id": scope.account_id,
                    "sub_account_id": None,
                    "region_id": scope.region,
                    "resource_type": "instance",
                    "resource_id": resource_id,
                    "metric": descriptor.canonical_metric,
                    "series_id": identity,
                    "metric_namespace": descriptor.namespace,
                    "source_metric_name": descriptor.name,
                    "metric_scope": descriptor.scope,
                    "metric_unit": descriptor.canonical_unit,
                    "source_unit": descriptor.unit,
                    "unit_scale": 1.0 if descriptor.unit == descriptor.canonical_unit else None,
                    "metric_kind": descriptor.kind.value,
                    "canonical_value_statistic": "mean",
                    "semantic_version": 2,
                    "source_descriptor_hash": descriptor_hash,
                    "dimensions_json": labels_json,
                    "dimensions_hash": labels_digest,
                    "dimension_schema_version": 2,
                    "bucket_start_utc": timestamp.replace(tzinfo=None),
                    "bucket_end_utc": timestamp.replace(tzinfo=None) + timedelta(seconds=interval),
                    "aggregation_interval_seconds": interval,
                    "resolution_seconds": plan.resolution_seconds,
                    "source_sample_period_seconds": point.source_sample_period_seconds,
                    "observed_at_utc": observed_at.astimezone(timezone.utc).replace(tzinfo=None),
                    "value_rate": None,
                    "native_quantile_method": "provider_native" if any(
                        stat in point.values for stat in ("p90", "p95", "p99")
                    ) else None,
                    "native_quantile_basis": "bucket",
                    "statistic_lineage_json": canonical_json({
                        stat: {"request_id": plan.request_id, "payload_hash": None,
                               "bronze_path": None, "source_status": result.status_code}
                        for stat in point.values
                    }),
                    "statistics_status_json": canonical_json({
                        "requested": list(plan.statistics),
                        "returned": sorted(point.values),
                        "query_status": result.status_code,
                        "error_code": result.error_code,
                    }),
                    "source_resolution_verified": plan.resolution_seconds in
                        descriptor.supported_resolutions_seconds,
                    "origin_kind": "NATIVE",
                    "collection_run_id": collection_run_id,
                    "request_id": plan.request_id,
                    "payload_hash": None,
                    "mapping_version": "oci_metrics_v2",
                    "schema_version": 2,
                    "metric_day": timestamp.strftime("%Y%m%d"),
                    "quality_status": "PARTIAL" if result.error_code or result.status_code >= 400 else "OK",
                    "quality_reason": result.error_code if result.error_code else (
                        "PARTIAL_PROVIDER_RESPONSE" if result.status_code >= 400 else None
                    ),
                    "source_api_version": None,
                    "_silver_ingested_at": observed_at.astimezone(timezone.utc).isoformat(),
                    "_silver_owner": source_owner,
                    "_source_connection_id": scope.connection_id,
                    "_source_connection_code": f"{scope.provider}:{scope.connection_id}",
                    "_source_connection_name": descriptor.namespace,
                }
                rows_by_key[key] = row

            for statistic, field in _STAT_FIELD.items():
                value = point.values.get(statistic)
                if value is not None:
                    row[field] = float(value)
            if "mean" in point.values:
                row["value"] = point.values["mean"]
            if "mean" in point.values and descriptor.kind.value == "RATE":
                row["value_rate"] = point.values["mean"]
            # Each provider statistic query contributes to the same wide row.
            lineage = json.loads(row["statistic_lineage_json"])
            for statistic in point.values:
                lineage[statistic] = {
                    "request_id": plan.request_id,
                    "payload_hash": None,
                    "bronze_path": None,
                    "source_status": result.status_code,
                }
            row["statistic_lineage_json"] = canonical_json(lineage)
            status = json.loads(row["statistics_status_json"])
            status["returned"] = sorted(set(status.get("returned", ())) | set(point.values))
            if result.error_code:
                status.setdefault("errors", []).append({
                    "status": result.status_code, "code": result.error_code,
                })
                row["quality_status"] = "PARTIAL"
                row["quality_reason"] = result.error_code
            row["statistics_status_json"] = canonical_json(status)

    return list(rows_by_key.values())
