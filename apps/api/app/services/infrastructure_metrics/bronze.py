"""Immutable raw-response persistence for infrastructure metric acquisitions."""
from __future__ import annotations

from datetime import timezone
import hashlib
import json
from typing import Iterable


def persist_raw_query_results(
    *, connection_code: str, connection_id: int, collection_run_id: str,
    results: Iterable,
) -> int:
    """Write complete provider responses/errors to a dedicated Bronze source."""
    import pandas as pd

    from app.services import iceberg_service

    bronze_code = f"{connection_code}_infra_metrics"
    rows_written = 0
    for result in results:
        plan = result.plan
        for raw in result.raw_payloads:
            payload_text = json.dumps(
                raw.get("payload"), default=str, ensure_ascii=False,
                sort_keys=True, separators=(",", ":"),
            ) if "payload" in raw else None
            payload_hash = hashlib.sha256(payload_text.encode("utf-8")).hexdigest() if payload_text else None
            resource_id = str(raw.get("resource_id") or "")
            resource_alias = hashlib.sha256(resource_id.encode("utf-8")).hexdigest()[:16]
            statistic = str(raw.get("statistic") or "unknown")
            blob_path = "/".join((
                "infrastructure_metrics", str(collection_run_id),
                str(plan.descriptor.name), str(plan.resolution_seconds),
                resource_alias, statistic,
            ))
            request = {
                "provider": "oci",
                "connection_id": int(connection_id),
                "region": plan.scope.region,
                "compartment_id": raw.get("compartment_id"),
                "resource_id": resource_id,
                "namespace": plan.descriptor.namespace,
                "metric": plan.descriptor.name,
                "statistic": statistic,
                "resolution_seconds": plan.resolution_seconds,
                "start_utc": plan.start_utc.astimezone(timezone.utc).isoformat(),
                "end_utc": plan.end_utc.astimezone(timezone.utc).isoformat(),
            }
            row = {
                "collection_run_id": str(collection_run_id),
                "request_id": raw.get("request_id") or plan.request_id,
                "provider": "oci",
                "connection_id": int(connection_id),
                "region": plan.scope.region,
                "resource_id": resource_id,
                "metric_namespace": plan.descriptor.namespace,
                "metric_name": plan.descriptor.name,
                "statistic": statistic,
                "resolution_seconds": int(plan.resolution_seconds),
                "window_start_utc": plan.start_utc.astimezone(timezone.utc).isoformat(),
                "window_end_utc": plan.end_utc.astimezone(timezone.utc).isoformat(),
                "api_status": int(raw.get("status_code") or result.status_code),
                "error_code": raw.get("error_code"),
                "error_type": raw.get("error_type"),
                "safe_headers_json": json.dumps(raw.get("safe_headers") or {}, sort_keys=True),
                "request_json": json.dumps(request, sort_keys=True, separators=(",", ":")),
                "raw_payload_json": payload_text,
                "payload_sha256": payload_hash,
                "response_bytes_serialized": len(payload_text.encode("utf-8")) if payload_text else 0,
                "latency_ms": int(result.latency_ms),
                "stream_count": int(result.stream_count),
                "_source_connection_id": int(connection_id),
            }
            period = plan.start_utc.astimezone(timezone.utc).strftime("%Y%m")
            rows_written += iceberg_service.replace_bronze_slice(
                bronze_code, pd.DataFrame([row]), period, blob_path,
            )
    return rows_written
