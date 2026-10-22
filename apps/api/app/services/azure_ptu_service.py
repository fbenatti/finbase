"""Read-only Azure OpenAI PTU discovery and Azure Monitor aggregation."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from azure.identity import ClientSecretCredential

from app.services.azure_ptu_meters import p95 as _p95

logger = logging.getLogger(__name__)
FAILURE_ALERT_THRESHOLD = 2
MAX_MONITOR_WINDOW_DAYS = 30
AZURE_MONITOR_RETENTION_DAYS = 93
# Parallel Azure Monitor HTTP calls per collection run (rate-limit friendly).
MONITOR_CONCURRENCY = 12
DEPLOYMENT_CONCURRENCY = 4
MONITOR_HTTP_TIMEOUT = 60.0
MONITOR_QUERY_TIMEOUT = 120.0
PERSIST_TIMEOUT_S = 300
# Windows longer than 24h, or not ending in the last 24h, use PT1H (faster backfill).
PT1M_MAX_WINDOW_HOURS = 24
ARM = "https://management.azure.com"
ARM_API = "2022-12-01"
GRAPH_API = "2022-10-01"
DEPLOYMENTS_API = "2024-10-01"
METRICS_API = "2018-01-01"

PTU_SKU_MARKERS = ("provisioned",)
METRIC_SPECS = {
    "ProcessedPromptTokens": "Total",
    "GeneratedTokens": "Total",
    "TokenTransaction": "Total",
    "ActiveTokens": "Total,Average,Maximum",
    "AzureOpenAIProvisionedManagedUtilizationV2": "Average,Maximum",
    "AzureOpenAIRequests": "Total",
    "AzureOpenAITimeToResponse": "Average,Maximum",
    "AzureOpenAITTLTInMS": "Average,Maximum",
    "AzureOpenAINormalizedTTFTInMS": "Average,Maximum",
    "AzureOpenAINormalizedTBTInMS": "Average,Maximum",
    "AzureOpenAITokenPerSecond": "Average,Maximum",
    "AzureOpenAIContextTokensCacheMatchRate": "Average,Maximum",
    "AzureOpenAIAvailabilityRate": "Average,Minimum",
}
UTILIZATION_VALID_MAX = 100.0


def _metric_spec_groups() -> list[tuple[str, list[str]]]:
    """Group metric names that share the same aggregation string for batched Monitor calls."""
    groups: dict[str, list[str]] = {}
    for name, aggregation in METRIC_SPECS.items():
        groups.setdefault(aggregation, []).append(name)
    return [(agg, names) for agg, names in groups.items()]


def _monitor_interval(start: datetime, end: datetime, *, now: datetime | None = None) -> str:
    """PT1M only for short windows ending in the last 24h; otherwise PT1H."""
    hours = max(1, int((end - start).total_seconds() // 3600))
    ref = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    recent_cutoff = ref - timedelta(hours=PT1M_MAX_WINDOW_HOURS)
    if hours <= PT1M_MAX_WINDOW_HOURS and end >= recent_cutoff:
        return "PT1M"
    return "PT1H"


def _hour_key(timestamp: str) -> str:
    return timestamp[:13] + ":00:00Z"


def resolve_config(config: dict[str, Any] | None) -> dict[str, Any]:
    return dict(config or {})


def _credential(config: dict[str, Any]) -> ClientSecretCredential:
    cfg = resolve_config(config)
    tenant = cfg.get("tenant_id") or cfg.get("tenant")
    client = cfg.get("client_id") or cfg.get("application_id")
    secret = cfg.get("client_secret")
    if not tenant or not client or not secret:
        raise ValueError("azure_openai_ptu requer tenant_id, client_id e client_secret no banco de dados")
    return ClientSecretCredential(tenant_id=tenant, client_id=client, client_secret=secret)


def _is_ptu(deployment: dict[str, Any]) -> bool:
    name = str((deployment.get("sku") or {}).get("name") or "").lower()
    return any(marker in name for marker in PTU_SKU_MARKERS)


def _project_hint(account: dict[str, Any], deployment: dict[str, Any]) -> str | None:
    """Projeto vem do mapeamento cadastrado (project-mappings), não do nome."""
    return None


async def _get(client: httpx.AsyncClient, path: str, params: dict[str, Any]) -> httpx.Response:
    return await client.get(f"{ARM}{path}", params=params)


async def discover(config: dict[str, Any]) -> dict[str, Any]:
    token = await asyncio.to_thread(_credential(config).get_token, "https://management.azure.com/.default")
    headers = {"Authorization": f"Bearer {token.token}"}
    async with httpx.AsyncClient(headers=headers, timeout=45) as client:
        response = await _get(client, "/subscriptions", {"api-version": ARM_API})
        response.raise_for_status()
        subscriptions = response.json().get("value", [])
        selected = set(config.get("subscription_ids") or [])
        subscription_ids = [s["subscriptionId"] for s in subscriptions if not selected or s.get("subscriptionId") in selected]
        body = {
            "subscriptions": subscription_ids,
            "query": "resources | where type =~ 'microsoft.cognitiveservices/accounts' | where kind =~ 'OpenAI' or kind =~ 'AIServices' | project id,name,kind,location,resourceGroup,subscriptionId,tags",
        }
        graph = await client.post(f"{ARM}/providers/Microsoft.ResourceGraph/resources", params={"api-version": GRAPH_API}, json=body)
        graph.raise_for_status()
        accounts = graph.json().get("data", [])
        allow_accounts = set(config.get("account_resource_ids") or [])
        if allow_accounts:
            accounts = [a for a in accounts if a.get("id") in allow_accounts]
        discovered: list[dict[str, Any]] = []
        for account in accounts:
            dep = await _get(client, f"{account['id']}/deployments", {"api-version": DEPLOYMENTS_API})
            if dep.status_code != 200:
                logger.warning("Azure PTU deployment discovery failed: %s", dep.status_code)
                continue
            for item in dep.json().get("value", []):
                if not _is_ptu(item):
                    continue
                sku = item.get("sku") or {}
                props = item.get("properties") or {}
                model = props.get("model") or {}
                discovered.append({
                    "id": item.get("id"),
                    "account_resource_id": account.get("id"),
                    "account_name": account.get("name"),
                    "resource_group": account.get("resourceGroup"),
                    "subscription_id": account.get("subscriptionId"),
                    "region": account.get("location"),
                    "deployment_name": item.get("name"),
                    "sku_name": sku.get("name"),
                    "ptus": float(sku.get("capacity") or 0),
                    "model_name": model.get("name"),
                    "model_version": model.get("version"),
                    "provisioning_state": props.get("provisioningState"),
                    "tags": {**(account.get("tags") or {}), **(item.get("tags") or {})},
                    "project_hint": _project_hint(account, item),
                })
        return {
            "subscription_count": len(subscription_ids),
            "account_count": len(accounts),
            "deployments": discovered,
            "ptu_deployment_count": len(discovered),
            "total_ptus": sum(x["ptus"] for x in discovered),
        }


def _metric_values(payload: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for metric in payload.get("value", []):
        name = (metric.get("name") or {}).get("value") or (metric.get("name") or {}).get("localizedValue")
        for series in metric.get("timeseries", []):
            dimensions = {str(x.get("name", {}).get("value")): x.get("value") for x in series.get("metadatavalues", [])}
            for point in series.get("data", []):
                values.append({"metric": name, "dimensions": dimensions, **point})
    return values


def _dim_value(dimensions: dict[str, Any], *keys: str) -> str | None:
    lowered = {str(k).lower(): v for k, v in (dimensions or {}).items()}
    for key in keys:
        val = lowered.get(key.lower())
        if val is not None and str(val).strip() != "":
            return str(val)
    return None


def _point_timestamp(point: dict[str, Any]) -> str | None:
    return point.get("timeStamp") or point.get("timestamp")


def _point_numeric(point: dict[str, Any], field: str) -> float | None:
    raw = point.get(field)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _metric_rollup_kind(metric_name: str, field: str) -> str:
    """How to merge dimensioned series at the same timestamp."""
    if field in {"total"} or metric_name in {
        "ProcessedPromptTokens", "GeneratedTokens", "TokenTransaction",
        "ActiveTokens", "AzureOpenAIRequests",
    }:
        return "sum"
    if field in {"maximum", "max"}:
        return "max"
    return "avg"


def _dedupe_metric_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate dimensioned Monitor series once per metric timestamp."""
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for point in points:
        metric = str(point.get("metric") or "")
        timestamp = _point_timestamp(point)
        if not metric or not timestamp:
            continue
        dimensions = point.get("dimensions") or {}
        dim_key = tuple(sorted((str(k), str(v)) for k, v in dimensions.items()))
        key = (metric, timestamp)
        slot = buckets.setdefault(key, {
            "metric": metric,
            "timeStamp": timestamp,
            "dimensions": dimensions,
            "seen_dimensions": set(),
            "_rollup": {},
        })
        if dim_key in slot["seen_dimensions"]:
            continue
        slot["seen_dimensions"].add(dim_key)
        for field in ("total", "average", "maximum", "minimum"):
            val = _point_numeric(point, field)
            if val is None:
                continue
            rollup = _metric_rollup_kind(metric, field)
            current = slot["_rollup"].setdefault(field, {"kind": rollup, "values": []})
            current["values"].append(val)
    output: list[dict[str, Any]] = []
    for (_metric, _ts), slot in buckets.items():
        merged: dict[str, Any] = {
            "metric": slot["metric"],
            "timeStamp": slot["timeStamp"],
            "dimensions": slot["dimensions"],
        }
        for field, payload in slot["_rollup"].items():
            values = payload["values"]
            if not values:
                continue
            kind = payload["kind"]
            if kind == "sum":
                merged[field] = sum(values)
            elif kind == "max":
                merged[field] = max(values)
            else:
                merged[field] = sum(values) / len(values)
        output.append(merged)
    return output


def _utilization_values(points: list[dict[str, Any]], field: str = "average") -> tuple[list[float], list[float], bool]:
    """Return (valid, raw, has_anomaly) utilization samples for a field."""
    raw = [_point_numeric(p, field) for p in points]
    raw_vals = [v for v in raw if v is not None]
    valid = [v for v in raw_vals if 0 <= v <= UTILIZATION_VALID_MAX]
    anomaly = any(v > UTILIZATION_VALID_MAX for v in raw_vals)
    return valid, raw_vals, anomaly


def _request_bucket(dimensions: dict[str, Any]) -> str | None:
    spillover = _dim_value(dimensions, "IsSpillover", "Spillover", "isSpillover")
    if spillover and str(spillover).lower() in {"true", "1", "yes"}:
        return "spillover"
    status = _dim_value(dimensions, "StatusCode", "HttpStatusCode", "statusCode", "ResponseCode")
    if not status:
        stream = _dim_value(dimensions, "StreamType", "streamType")
        if stream:
            return f"stream:{stream.lower()}"
        return None
    try:
        code = int(str(status).split()[0])
    except ValueError:
        return None
    if code == 429:
        return "rate_limited"
    if 200 <= code < 300:
        return "success"
    if 400 <= code < 500:
        return "client_error"
    if code >= 500:
        return "server_error"
    return None


def record_checkpoint_result(
    session: Any,
    connection_id: int,
    partition_id: str,
    *,
    success: bool,
    offset: str | None = None,
    sequence_number: int | None = None,
    message: str | None = None,
) -> None:
    from sqlalchemy import select
    from app.models.azure_ptu import AzurePtuCheckpoint
    from app.models.source_connection import SourceConnection

    row = session.execute(
        select(AzurePtuCheckpoint).where(
            AzurePtuCheckpoint.connection_id == connection_id,
            AzurePtuCheckpoint.partition_id == partition_id,
        )
    ).scalar_one_or_none()
    if not row:
        row = AzurePtuCheckpoint(connection_id=connection_id, partition_id=partition_id)
        session.add(row)
    now = datetime.now(timezone.utc)
    if success:
        row.consecutive_failures = 0
        row.last_status = "OK"
        row.last_success_at = now
        if offset is not None:
            row.offset = offset
        if sequence_number is not None:
            row.sequence_number = sequence_number
    else:
        row.consecutive_failures = int(row.consecutive_failures or 0) + 1
        row.last_status = "FAILED"
    if message:
        row.last_message = message[:500]
    row.updated_at = now
    if not success and row.consecutive_failures >= FAILURE_ALERT_THRESHOLD:
        conn = session.get(SourceConnection, connection_id)
        if conn:
            conn.last_test_status = "ALERT"
            conn.last_test_message = f"Azure PTU {partition_id}: {message or 'collector failure'}"[:500]
            conn.last_tested_at = now


def needs_backfill(session: Any, connection_id: int) -> bool:
    from sqlalchemy import select
    from app.models.azure_ptu import AzurePtuCheckpoint

    row = session.execute(
        select(AzurePtuCheckpoint).where(
            AzurePtuCheckpoint.connection_id == connection_id,
            AzurePtuCheckpoint.partition_id == "collector",
        )
    ).scalar_one_or_none()
    return row is None or row.last_success_at is None


def resolve_ptu_collection_params(
    *,
    task_json: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    billing_periods: list[str] | None = None,
    session: Any | None = None,
    connection_id: int | None = None,
) -> dict[str, Any]:
    """Choose month/backfill/incremental collection mode for a queued task."""
    cfg = dict(config or {})
    payload = dict(task_json or {})
    periods = [
        str(period)
        for period in (billing_periods or payload.get("periods") or [])
        if str(period).isdigit() and len(str(period)) == 6
    ] or None

    if periods:
        return {"periods": periods, "backfill_days": None, "lookback_hours": 3}

    if payload.get("backfill") or payload.get("backfill_days"):
        days = int(payload.get("backfill_days") or cfg.get("backfill_days") or 93)
        return {"periods": None, "backfill_days": days, "lookback_hours": 3}

    if payload.get("lookback_hours") is not None and not payload.get("user_triggered"):
        return {
            "periods": None,
            "backfill_days": None,
            "lookback_hours": int(payload["lookback_hours"]),
        }

    if payload.get("user_triggered"):
        if payload.get("backfill") or payload.get("backfill_days"):
            days = int(payload.get("backfill_days") or cfg.get("backfill_days") or AZURE_MONITOR_RETENTION_DAYS)
            return {"periods": None, "backfill_days": days, "lookback_hours": 3}
        if payload.get("lookback_hours") is not None:
            return {
                "periods": None,
                "backfill_days": None,
                "lookback_hours": int(payload["lookback_hours"]),
            }
        days = int(cfg.get("backfill_days") or AZURE_MONITOR_RETENTION_DAYS)
        return {"periods": None, "backfill_days": days, "lookback_hours": 3}

    if session is not None and connection_id is not None and needs_backfill(session, connection_id):
        days = int(cfg.get("backfill_days") or 93)
        return {"periods": None, "backfill_days": days, "lookback_hours": 3}

    hours = int(cfg.get("lookback_hours") or 24)
    return {"periods": None, "backfill_days": None, "lookback_hours": hours}


def split_monitor_windows(
    windows: list[tuple[str, datetime, datetime]],
    *,
    max_window_days: int = MAX_MONITOR_WINDOW_DAYS,
) -> list[tuple[str, datetime, datetime]]:
    """Split any Azure Monitor query window longer than 30 days."""
    split: list[tuple[str, datetime, datetime]] = []
    for period, start, end in windows:
        cursor = start
        while cursor < end:
            chunk_end = min(cursor + timedelta(days=max(1, max_window_days)), end)
            split.append((period, cursor, chunk_end))
            cursor = chunk_end
    return split


def backfill_monitor_windows(
    days: int = 93,
    *,
    max_window_days: int = MAX_MONITOR_WINDOW_DAYS,
    now: datetime | None = None,
) -> list[tuple[str, datetime, datetime]]:
    """Split historical collection into <=30-day Azure Monitor windows."""
    end_limit = (now or datetime.now(timezone.utc)).astimezone(timezone.utc) - timedelta(minutes=10)
    start_limit = end_limit - timedelta(days=max(1, min(int(days), 93)))
    windows: list[tuple[str, datetime, datetime]] = []
    cursor = start_limit
    while cursor < end_limit:
        window_end = min(cursor + timedelta(days=max(1, max_window_days)), end_limit)
        windows.append((cursor.strftime("%Y%m"), cursor, window_end))
        cursor = window_end
    return windows


def collection_windows(
    config: dict[str, Any],
    periods: list[str] | None = None,
    *,
    lookback_hours: int | None = None,
    backfill_days: int | None = None,
    now: datetime | None = None,
) -> list[tuple[str, datetime, datetime]]:
    """Build UTC collection windows for Azure Monitor queries."""
    end_limit = (now or datetime.now(timezone.utc)).astimezone(timezone.utc) - timedelta(minutes=10)
    requested = sorted({str(period) for period in (periods or []) if str(period).isdigit() and len(str(period)) == 6})
    windows: list[tuple[str, datetime, datetime]] = []

    if requested:
        for period in requested:
            year, month = int(period[:4]), int(period[4:])
            if not 1 <= month <= 12:
                continue
            start = datetime(year, month, 1, tzinfo=timezone.utc)
            next_month = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1, tzinfo=timezone.utc)
            end = min(next_month, end_limit)
            if start < end:
                windows.append((period, start, end))
        return split_monitor_windows(windows)

    if lookback_hours is not None:
        start_limit = end_limit - timedelta(hours=max(1, lookback_hours))
        return [(start_limit.strftime("%Y%m"), start_limit, end_limit)]

    days = backfill_days if backfill_days is not None else int(config.get("backfill_days") or 93)
    return backfill_monitor_windows(days, now=end_limit)


def _persist_task_progress(session: Any, task_id: int | None, **values: Any) -> None:
    if not task_id:
        return
    from app.models.etl_task import EtlTask

    row = session.get(EtlTask, task_id)
    if not row:
        return
    for key, value in values.items():
        setattr(row, key, value)
    session.commit()


def _aggregate_deployment_buckets(
    deployment: dict[str, Any],
    buckets: dict[str, dict[str, Any]],
    *,
    interval: str,
    metric_availability: dict[str, bool] | None = None,
) -> list[dict[str, Any]]:
    """Roll minute or hour Monitor points into hourly Gold rows for one deployment."""
    expected_points = 60 if interval == "PT1M" else 1
    availability = dict(metric_availability or {})
    output: list[dict[str, Any]] = []
    for bucket in buckets.values():
        points = bucket.pop("points", {})
        deduped_points: dict[str, list[dict[str, Any]]] = {}
        for metric_name, metric_points in points.items():
            deduped_points[metric_name] = _dedupe_metric_points(metric_points)

        def vals(name: str, field: str = "total") -> list[float]:
            return [float(p[field]) for p in deduped_points.get(name, []) if p.get(field) is not None]

        inp, out, total = vals("ProcessedPromptTokens"), vals("GeneratedTokens"), vals("TokenTransaction")
        util_valid, util_raw, util_anomaly = _utilization_values(deduped_points.get("AzureOpenAIProvisionedManagedUtilizationV2", []), "average")
        util_max_valid, util_max_raw, _ = _utilization_values(deduped_points.get("AzureOpenAIProvisionedManagedUtilizationV2", []), "maximum")
        active = vals("ActiveTokens")
        req_points = deduped_points.get("AzureOpenAIRequests", [])
        req = [float(p["total"]) for p in req_points if p.get("total") is not None]
        ttr = vals("AzureOpenAITimeToResponse", "average")
        ttlb = vals("AzureOpenAITTLTInMS", "average")
        ttft = vals("AzureOpenAINormalizedTTFTInMS", "average")
        tbt = vals("AzureOpenAINormalizedTBTInMS", "average")
        tps = vals("AzureOpenAITokenPerSecond", "average")
        cache_rate = vals("AzureOpenAIContextTokensCacheMatchRate", "average")
        availability_rate = vals("AzureOpenAIAvailabilityRate", "average")
        util_max = util_max_valid or util_valid
        req_buckets = {"success": 0.0, "client_error": 0.0, "server_error": 0.0, "rate_limited": 0.0, "spillover": 0.0}
        stream_buckets: dict[str, float] = {}
        for point in req_points:
            total_val = point.get("total")
            if total_val is None:
                continue
            bucket_name = _request_bucket(point.get("dimensions") or {})
            if bucket_name and bucket_name.startswith("stream:"):
                stream_buckets[bucket_name.split(":", 1)[1]] = stream_buckets.get(bucket_name.split(":", 1)[1], 0.0) + float(total_val)
            elif bucket_name:
                req_buckets[bucket_name] += float(total_val)
        source_count = len(util_valid) or len(req) or len(inp)
        request_count = sum(req) or sum(req_buckets.values()) or None
        bucket.update({
            "input_tokens": sum(inp) if inp else None,
            "output_tokens": sum(out) if out else None,
            "total_tokens": (sum(total) if total else None) or ((sum(inp) + sum(out)) if (inp or out) else None),
            "input_tokens_sum": sum(inp) if inp else None,
            "output_tokens_sum": sum(out) if out else None,
            "total_tokens_sum": (sum(total) if total else None) or ((sum(inp) + sum(out)) if (inp or out) else None),
            "utilization_avg": sum(util_valid) / len(util_valid) if util_valid else None,
            "utilization_max": max(util_max) if util_max else None,
            "utilization_p95": _p95(util_valid),
            "utilization_avg_raw": sum(util_raw) / len(util_raw) if util_raw else None,
            "utilization_max_raw": max(util_max_raw) if util_max_raw else None,
            "utilization_quality_anomaly": util_anomaly,
            "active_tokens": sum(active) if active else None,
            "active_tokens_sum": sum(active) if active else None,
            "active_tokens_max": max(active) if active else None,
            "active_tokens_p95": _p95(active),
            "request_count": request_count,
            "request_peak_per_minute": max(req) if req and interval == "PT1M" else (max(req) / 60 if req else None),
            "success_count": req_buckets["success"] or None,
            "client_error_count": req_buckets["client_error"] or None,
            "server_error_count": req_buckets["server_error"] or None,
            "rate_limited_count": req_buckets["rate_limited"] or None,
            "spillover_request_count": req_buckets["spillover"] or None,
            "streaming_request_count": stream_buckets.get("true") or stream_buckets.get("streaming") or None,
            "non_streaming_request_count": stream_buckets.get("false") or stream_buckets.get("nonstreaming") or None,
            "time_to_response_avg_ms": sum(ttr) / len(ttr) if ttr else None,
            "time_to_response_max_ms": max(ttr) if ttr else None,
            "time_to_response_p95_ms": _p95(ttr),
            "time_to_last_byte_avg_ms": sum(ttlb) / len(ttlb) if ttlb else None,
            "normalized_ttft_avg_ms": sum(ttft) / len(ttft) if ttft else None,
            "normalized_ttft_max_ms": max(ttft) if ttft else None,
            "normalized_ttft_p95_ms": _p95(ttft),
            "normalized_tbt_avg_ms": sum(tbt) / len(tbt) if tbt else None,
            "normalized_tbt_max_ms": max(tbt) if tbt else None,
            "normalized_tbt_p95_ms": _p95(tbt),
            "tokens_per_second_avg": sum(tps) / len(tps) if tps else None,
            "tokens_per_second_max": max(tps) if tps else None,
            "context_cache_match_rate_avg": sum(cache_rate) / len(cache_rate) if cache_rate else None,
            "availability_rate_avg": sum(availability_rate) / len(availability_rate) if availability_rate else None,
            "source_points": source_count,
            "expected_points": expected_points,
            "completeness_pct": (source_count / expected_points) * 100 if expected_points and source_count else None,
            "metric_availability": availability,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "provisioned_ptus": deployment.get("ptus"),
        })
        output.append(bucket)
    return output


async def collect_metrics(
    config: dict[str, Any],
    deployments: list[dict[str, Any]],
    *,
    hours: int = 24,
    start: datetime | None = None,
    end: datetime | None = None,
    interval: str | None = None,
    on_progress: Any | None = None,
    concurrency: int = MONITOR_CONCURRENCY,
) -> list[dict[str, Any]]:
    token = await asyncio.to_thread(_credential(config).get_token, "https://management.azure.com/.default")
    headers = {"Authorization": f"Bearer {token.token}"}
    end = end or (datetime.now(timezone.utc) - timedelta(minutes=10))
    start = start or (end - timedelta(hours=max(1, hours)))
    grain = interval or _monitor_interval(start, end)
    timespan = f"{start.isoformat().replace('+00:00', 'Z')}/{end.isoformat().replace('+00:00', 'Z')}"
    spec_groups = _metric_spec_groups()
    active_deployments = [d for d in deployments if d.get("id")]
    dep_total = len(active_deployments)
    query_total = dep_total * len(spec_groups)
    completed_queries = 0
    semaphore = asyncio.Semaphore(max(1, concurrency))
    output: list[dict[str, Any]] = []

    async def _report(dep_name: str | None = None) -> None:
        if on_progress:
            on_progress(completed_queries, query_total, dep_name, grain)

    timeout = httpx.Timeout(MONITOR_HTTP_TIMEOUT, read=MONITOR_QUERY_TIMEOUT)
    dep_semaphore = asyncio.Semaphore(max(1, DEPLOYMENT_CONCURRENCY))
    async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
        async def fetch_group(deployment: dict[str, Any], aggregation: str, metric_names: list[str]) -> list[tuple[str, list[dict[str, Any]]]]:
            nonlocal completed_queries
            dep_name = deployment.get("deployment_name")
            async with semaphore:
                try:
                    response = await asyncio.wait_for(
                        _get(client, f"{deployment['account_resource_id']}/providers/microsoft.insights/metrics", {
                            "api-version": METRICS_API,
                            "metricnames": ",".join(metric_names),
                            "timespan": timespan,
                            "interval": grain,
                            "aggregation": aggregation,
                            "$filter": f"ModelDeploymentName eq '{deployment['deployment_name']}'",
                            "AutoAdjustTimegrain": "false",
                        }),
                        timeout=MONITOR_QUERY_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "Azure Monitor timeout (%ss) for %s metrics on %s",
                        MONITOR_QUERY_TIMEOUT, ",".join(metric_names), dep_name,
                    )
                    completed_queries += 1
                    await _report(dep_name)
                    return [(name, []) for name in metric_names]
                except httpx.TimeoutException:
                    logger.warning(
                        "Azure Monitor HTTP timeout for %s metrics on %s",
                        ",".join(metric_names), dep_name,
                    )
                    completed_queries += 1
                    await _report(dep_name)
                    return [(name, []) for name in metric_names]
                completed_queries += 1
                await _report(dep_name)
                if response.status_code != 200:
                    logger.info(
                        "Metrics %s unavailable for %s: %s",
                        ",".join(metric_names), dep_name, response.status_code,
                    )
                    return [(name, []) for name in metric_names]
                payload = response.json()
                by_name: dict[str, list[dict[str, Any]]] = {name: [] for name in metric_names}
                for metric in payload.get("value", []):
                    name = (metric.get("name") or {}).get("value") or (metric.get("name") or {}).get("localizedValue")
                    if name in by_name:
                        for series in metric.get("timeseries", []):
                            dimensions = {str(x.get("name", {}).get("value")): x.get("value") for x in series.get("metadatavalues", [])}
                            for point in series.get("data", []):
                                by_name[name].append({"metric": name, "dimensions": dimensions, **point})
                return [(name, by_name[name]) for name in metric_names]

        async def collect_deployment(deployment: dict[str, Any]) -> list[dict[str, Any]]:
            buckets: dict[str, dict[str, Any]] = {}
            metric_availability: dict[str, bool] = {name: False for name in METRIC_SPECS}
            tasks = [fetch_group(deployment, aggregation, names) for aggregation, names in spec_groups]
            results = await asyncio.gather(*tasks)
            for group_result in results:
                for metric_name, metric_points in group_result:
                    if metric_points:
                        metric_availability[metric_name] = True
                    for point in metric_points:
                        timestamp = _point_timestamp(point)
                        if not timestamp:
                            continue
                        hour = _hour_key(timestamp)
                        bucket = buckets.setdefault(hour, {"metric_hour": hour, **deployment})
                        bucket.setdefault("points", {}).setdefault(metric_name, []).append(point)
            return _aggregate_deployment_buckets(
                deployment, buckets, interval=grain, metric_availability=metric_availability,
            )

        async def collect_deployment_limited(deployment: dict[str, Any]) -> list[dict[str, Any]]:
            async with dep_semaphore:
                return await collect_deployment(deployment)

        if on_progress:
            await _report()
        dep_results = await asyncio.gather(*(collect_deployment_limited(dep) for dep in active_deployments))
        for dep_rows in dep_results:
            output.extend(dep_rows)
    return output


async def backfill_metrics(
    config: dict[str, Any],
    deployments: list[dict[str, Any]],
    *,
    days: int = 93,
    max_window_days: int = 30,
) -> list[dict[str, Any]]:
    """Collect historical metrics in <=30-day windows (Azure Monitor limit)."""
    end = datetime.now(timezone.utc) - timedelta(minutes=10)
    start = end - timedelta(days=max(1, min(days, 93)))
    output: list[dict[str, Any]] = []
    window_start = start
    while window_start < end:
        window_end = min(window_start + timedelta(days=max_window_days), end)
        hours = max(1, int((window_end - window_start).total_seconds() // 3600))
        chunk = await collect_metrics(config, deployments, start=window_start, end=window_end)
        output.extend(chunk)
        window_start = window_end
    return output


def run_ptu_collection(
    session: Any,
    connection_id: int,
    *,
    lookback_hours: int = 24,
    backfill_days: int | None = None,
    periods: list[str] | None = None,
    task_id: int | None = None,
    inventory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Discover, collect (optionally backfill) and persist hourly metrics."""
    import asyncio
    from app.models.source_connection import SourceConnection

    conn = session.get(SourceConnection, connection_id)
    if not conn:
        raise ValueError(f"connection {connection_id} not found")
    cfg = resolve_config(dict(conn.config_json or {}))
    connection_code = conn.code
    if inventory is None:
        _persist_task_progress(
            session,
            task_id,
            phase="Azure PTU: descoberta ARM (deployments PTU)",
            current_blob="Descoberta ARM",
        )
        # Release the read transaction before potentially long ARM/Monitor calls.
        session.rollback()
        inventory = asyncio.run(discover(cfg))
    else:
        session.rollback()
    deployments = inventory.get("deployments") or []
    dep_count = len(deployments)
    spec_groups = len(_metric_spec_groups())
    windows = collection_windows(
        cfg,
        periods,
        lookback_hours=None if (periods or backfill_days) else lookback_hours,
        backfill_days=backfill_days,
    )
    _persist_task_progress(
        session,
        task_id,
        total_blobs=len(windows),
        processed_blobs=0,
        rows_destination=0,
        phase=(
            f"Azure PTU backfill: 0/{len(windows)} janelas · {dep_count} deployments · consultando Monitor..."
            if backfill_days
            else (
                f"Azure PTU período: 0/{len(windows)} janelas · {dep_count} deployments · consultando Monitor..."
                if periods
                else f"Azure PTU: 0/{len(windows)} janelas · {dep_count} deployments · consultando Monitor..."
            )
        ),
        current_blob=f"{dep_count} deployments · {spec_groups} grupos de métricas",
    )

    total_gold = 0
    total_bronze = 0
    all_metrics: list[dict[str, Any]] = []
    capacity_persisted = False

    def _on_window_start(index: int, total: int, period: str, start: datetime, end: datetime) -> None:
        grain = _monitor_interval(start, end)
        _persist_task_progress(
            session,
            task_id,
            processed_blobs=max(0, index - 1),
            rows_destination=total_gold,
            phase=(
                f"Azure Monitor: janela {index}/{total} ({period}) "
                f"{start.strftime('%Y-%m-%d')} → {end.strftime('%Y-%m-%d')} · {grain} · consultando..."
            )[:200],
            current_blob=f"{dep_count} deployments · {period}",
        )

    def _on_query_progress(done: int, total: int, dep_name: str | None, grain: str) -> None:
        label = dep_name or "Monitor"
        _persist_task_progress(
            session,
            task_id,
            rows_destination=total_gold,
            phase=(
                f"Azure Monitor: {label} · {done}/{total} consultas · {grain}"
            )[:200],
            current_blob=f"consultando Monitor · {done}/{total}",
        )

    def _on_persist_start(index: int, total: int, period: str, chunk_size: int) -> None:
        _persist_task_progress(
            session,
            task_id,
            processed_blobs=max(0, index - 1),
            rows_destination=total_gold,
            phase=(
                f"Persistindo Gold janela {index}/{total} ({period}) · {chunk_size} registros..."
            )[:200],
            current_blob=f"iceberg dest_azure_ptu_metrics · {chunk_size} rows",
        )

    def _on_window_complete(
        index: int,
        total: int,
        period: str,
        chunk: list[dict[str, Any]],
        window_counts: dict[str, int],
    ) -> None:
        nonlocal total_gold, total_bronze, capacity_persisted
        written = int(window_counts.get("gold_metrics") or 0)
        total_gold += written
        total_bronze += int(window_counts.get("bronze") or 0)
        if window_counts.get("gold_capacity"):
            capacity_persisted = True
        _persist_task_progress(
            session,
            task_id,
            processed_blobs=index,
            rows_destination=total_gold,
            rows_raw=total_bronze,
            phase=(
                f"Azure PTU: janela {index}/{total} ({period}) · "
                f"{written} registros Gold · total {total_gold}"
            )[:200],
            current_blob=f"Gold {total_gold} registros · janela {index}/{total}",
        )

    try:
        all_metrics, collected_periods = asyncio.run(
            collect_monthly(
                cfg,
                deployments,
                periods if periods else None,
                lookback_hours=lookback_hours if not periods and not backfill_days else None,
                backfill_days=backfill_days,
                on_window_start=_on_window_start,
                on_query_progress=_on_query_progress,
                on_persist_start=_on_persist_start,
                on_window_complete=_on_window_complete,
                persist_fn=lambda chunk, cap: persist_hourly(
                    connection_id, inventory, chunk,
                    connection_code=connection_code,
                    write_capacity=cap,
                    strict_gold=True,
                ),
            )
        )
        counts = {
            "bronze": total_bronze,
            "gold_metrics": total_gold,
            "gold_capacity": 1 if capacity_persisted else 0,
        }
        record_checkpoint_result(
            session, connection_id, "collector",
            success=True, message=f"metrics={len(all_metrics)}",
        )
        phase = (
            f"Azure PTU backfill concluído: {len(all_metrics)} registros · Gold {total_gold}"
            if backfill_days
            else (
                f"Azure PTU período concluído: {len(all_metrics)} registros · Gold {total_gold}"
                if periods
                else f"Azure PTU concluído: {len(all_metrics)} registros · Gold {total_gold}"
            )
        )
        _persist_task_progress(
            session,
            task_id,
            processed_blobs=len(windows),
            rows_raw=total_bronze,
            rows_destination=total_gold,
            phase=phase[:200],
            current_blob=phase[:500],
        )
        return {
            "inventory": inventory,
            "metrics": all_metrics,
            "persisted": counts,
            "source": "azure_monitor",
            "billing_periods": collected_periods,
            "backfill_days": backfill_days,
        }
    except Exception as exc:
        record_checkpoint_result(session, connection_id, "collector", success=False, message=str(exc)[:500])
        raise


async def collect_monthly(
    config: dict[str, Any],
    deployments: list[dict[str, Any]],
    periods: list[str] | None = None,
    *,
    lookback_hours: int | None = None,
    backfill_days: int | None = None,
    on_window: Any | None = None,
    on_window_start: Any | None = None,
    on_query_progress: Any | None = None,
    on_persist_start: Any | None = None,
    on_window_complete: Any | None = None,
    persist_fn: Any | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Collect every requested interval separately; optionally persist Gold per window."""
    rows: list[dict[str, Any]] = []
    collected_periods: list[str] = []
    windows = split_monitor_windows(collection_windows(
        config,
        periods,
        lookback_hours=lookback_hours,
        backfill_days=backfill_days,
    ))
    total = len(windows)
    for index, (period, start, end) in enumerate(windows, start=1):
        if on_window_start:
            on_window_start(index, total, period, start, end)
        elif on_window:
            on_window(index, total, period, start, end)
        grain = _monitor_interval(start, end)
        chunk = await collect_metrics(
            config, deployments, start=start, end=end, interval=grain,
            on_progress=on_query_progress,
        )
        rows.extend(chunk)
        if period not in collected_periods:
            collected_periods.append(period)
        window_counts = {"bronze": 0, "gold_metrics": 0, "gold_capacity": 0}
        if persist_fn and chunk:
            if on_persist_start:
                on_persist_start(index, total, period, len(chunk))
            write_cap = index == total
            window_counts = persist_fn(chunk, write_cap)
        if on_window_complete:
            on_window_complete(index, total, period, chunk, window_counts)
    return rows, collected_periods


async def probe_eventhub(config: dict[str, Any]) -> dict[str, Any]:
    hub = (config.get("eventhub") or {}) if isinstance(config.get("eventhub"), dict) else {}
    if not hub.get("namespace") or not hub.get("eventhub_name"):
        return {"status": "SKIPPED", "message": "Event Hub não configurado"}
    token = await asyncio.to_thread(_credential(config).get_token, "https://management.azure.com/.default")
    headers = {"Authorization": f"Bearer {token.token}"}
    ns = hub["namespace"]
    name = hub["eventhub_name"]
    group = hub.get("consumer_group") or "openfinops-azure-ptu"
    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        subs = await client.get(f"{ARM}/subscriptions", params={"api-version": ARM_API})
        subs.raise_for_status()
        for sub in subs.json().get("value", []):
            sid = sub.get("subscriptionId")
            listed = await client.get(
                f"{ARM}/subscriptions/{sid}/providers/Microsoft.EventHub/namespaces",
                params={"api-version": "2021-11-01"},
            )
            if listed.status_code != 200:
                continue
            for item in listed.json().get("value", []) or []:
                if str(item.get("name") or "").lower() != str(ns).lower():
                    continue
                hub_resp = await client.get(f"{item['id']}/eventhubs/{name}", params={"api-version": "2021-11-01"})
                parts = await client.get(f"{item['id']}/eventhubs/{name}/partitions", params={"api-version": "2021-11-01"})
                cg = await client.get(
                    f"{item['id']}/eventhubs/{name}/consumergroups/{group}",
                    params={"api-version": "2021-11-01"},
                )
                return {
                    "status": "OK" if hub_resp.status_code == 200 else "FAILED",
                    "namespace": ns,
                    "eventhub_name": name,
                    "consumer_group": group,
                    "consumer_group_status": cg.status_code,
                    "partition_count": len((parts.json() or {}).get("value") or []) if parts.status_code == 200 else 0,
                    "checkpoint": "postgres:azure_ptu_checkpoint",
                }
    return {"status": "FAILED", "message": f"Namespace {ns} não encontrado nas assinaturas visíveis"}


async def test_connection_checks(config: dict[str, Any]) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "authentication": "PENDING",
        "resource_graph": "PENDING",
        "deployments": "PENDING",
        "metric_definitions": "PENDING",
        "metric_sample": "PENDING",
        "eventhub": "SKIPPED",
    }
    cfg = resolve_config(config)
    await asyncio.to_thread(_credential(cfg).get_token, "https://management.azure.com/.default")
    checks["authentication"] = "OK"
    inventory = await discover(cfg)
    checks["resource_graph"] = "OK"
    checks["deployments"] = "OK" if inventory.get("ptu_deployment_count") else "EMPTY"
    deployments = inventory.get("deployments") or []
    if deployments:
        sample = await collect_metrics(cfg, deployments[:1], hours=1)
        checks["metric_definitions"] = "OK"
        checks["metric_sample"] = "OK" if sample else "EMPTY"
    else:
        checks["metric_definitions"] = "EMPTY"
        checks["metric_sample"] = "EMPTY"
    hub = await probe_eventhub(cfg)
    checks["eventhub"] = hub.get("status") or "SKIPPED"
    status = "OK" if checks["authentication"] == "OK" and checks["resource_graph"] == "OK" else "FAILED"
    summary = {
        "subscriptions": inventory.get("subscription_count"),
        "openai_accounts": inventory.get("account_count"),
        "deployments": inventory.get("ptu_deployment_count"),
        "ptu_deployments": inventory.get("ptu_deployment_count"),
        "total_ptus": inventory.get("total_ptus"),
    }
    return {
        "status": status,
        "checks": checks,
        "eventhub": hub,
        "summary": summary,
        "message": f"Azure PTU validado: {summary.get('ptu_deployments', 0)} deployments / {summary.get('total_ptus', 0)} PTUs",
        "inventory": {k: inventory[k] for k in inventory if k != "deployments"},
    }


DASHBOARD_TRINO_TIMEOUT_S = 25
FOCUS_DASHBOARD_TIMEOUT_S = 90


def _gold_metrics_window(hours: int) -> tuple[str, str]:
    end = datetime.now(timezone.utc) - timedelta(minutes=10)
    start = end - timedelta(hours=max(1, min(hours, 93 * 24)))
    return start.strftime("%Y-%m-%dT%H:00:00Z"), end.strftime("%Y-%m-%dT%H:00:00Z")


def _gold_connection_filter(connection_ids: list[int] | None) -> str:
    if not connection_ids:
        return ""
    ids = ", ".join(str(int(x)) for x in connection_ids)
    return f" AND CAST(connection_id AS BIGINT) IN ({ids})"


def _gold_project_hint_filter(project_hints: list[str] | None) -> str:
    if not project_hints:
        return ""
    hints = ", ".join(f"'{h}'" for h in project_hints)
    return f" AND CAST(project_hint AS VARCHAR) IN ({hints})"


def _gold_metric_hour_varchar(column: str = "metric_hour") -> str:
    """Gold metric_hour is ISO-8601 varchar (e.g. 2026-05-23T20:00:00Z)."""
    return f"CAST({column} AS VARCHAR)"


def _gold_metric_hour_filter(start_iso: str, end_iso: str, column: str = "metric_hour") -> str:
    """Lexicographic range on ISO varchar — Iceberg-friendly (no per-row timestamp parse in WHERE)."""
    col = _gold_metric_hour_varchar(column)
    return f"{col} >= '{start_iso}' AND {col} <= '{end_iso}'"


def _gold_metric_hour_weekday(column: str = "metric_hour") -> str:
    col = _gold_metric_hour_varchar(column)
    return f"(day_of_week(CAST(substr({col}, 1, 10) AS DATE)) + 6) % 7"


def _gold_metric_hour_hour(column: str = "metric_hour") -> str:
    col = _gold_metric_hour_varchar(column)
    return f"CAST(substr({col}, 12, 2) AS INTEGER)"


def _gold_metrics_bucket_expr(grain: str) -> str:
    hour_col = "CAST(metric_hour AS VARCHAR)"
    if grain == "month":
        return f"concat(substr({hour_col}, 1, 7), '-01T00:00:00Z')"
    if grain == "day":
        return f"concat(substr({hour_col}, 1, 10), 'T00:00:00Z')"
    return hour_col


_gold_ptu_columns_cache: set[str] | None = None

GOLD_METRIC_OPTIONAL_COLUMNS = (
    "streaming_request_count",
    "non_streaming_request_count",
    "context_cache_match_rate_avg",
    "availability_rate_avg",
    "utilization_avg_raw",
    "utilization_max_raw",
    "utilization_quality_anomaly",
)


async def _gold_ptu_table_columns(*, force_refresh: bool = False) -> set[str]:
    """Cached Iceberg column list — load_gold_metrics must not reference missing fields."""
    global _gold_ptu_columns_cache
    if _gold_ptu_columns_cache is not None and not force_refresh:
        return _gold_ptu_columns_cache
    from app.services.trino_service import execute as trino_execute

    try:
        rows = await trino_execute("DESCRIBE iceberg.gold.dest_azure_ptu_metrics", timeout_s=10)
        _gold_ptu_columns_cache = {
            str(row.get("Column") or row.get("column") or next(iter(row.values())))
            for row in (rows or [])
        }
    except Exception as exc:
        logger.warning("Gold PTU describe failed: %s", exc)
        _gold_ptu_columns_cache = set()
    return _gold_ptu_columns_cache


def _gold_sum_expr(columns: set[str], name: str, *, alias: str | None = None) -> str:
    out = alias or name
    if name in columns:
        return f"SUM(TRY_CAST({name} AS DOUBLE)) AS {out}"
    return f"CAST(NULL AS DOUBLE) AS {out}"


def _gold_max_expr(columns: set[str], name: str, *, cast: str = "DOUBLE") -> str:
    if name in columns:
        return f"MAX(TRY_CAST({name} AS {cast})) AS {name}"
    return f"CAST(NULL AS {cast}) AS {name}"


def _gold_avg_expr(columns: set[str], name: str) -> str:
    if name in columns:
        return f"AVG(TRY_CAST({name} AS DOUBLE)) AS {name}"
    return f"CAST(NULL AS DOUBLE) AS {name}"


def _gold_request_weighted_avg_expr(columns: set[str], name: str) -> str:
    if name not in columns:
        return f"CAST(NULL AS DOUBLE) AS {name}"
    if "request_count" in columns:
        return f"""CASE WHEN SUM(TRY_CAST(request_count AS DOUBLE)) > 0
          THEN SUM(TRY_CAST({name} AS DOUBLE) * TRY_CAST(request_count AS DOUBLE))
               / SUM(TRY_CAST(request_count AS DOUBLE))
          ELSE AVG(TRY_CAST({name} AS DOUBLE))
        END AS {name}"""
    return f"AVG(TRY_CAST({name} AS DOUBLE)) AS {name}"


def _gold_utilization_avg_expr(columns: set[str]) -> str:
    if "utilization_avg" not in columns:
        return "CAST(NULL AS DOUBLE) AS utilization_avg"
    if "provisioned_ptus" in columns:
        return """CASE WHEN SUM(TRY_CAST(provisioned_ptus AS DOUBLE)) > 0
          THEN SUM(TRY_CAST(utilization_avg AS DOUBLE) * TRY_CAST(provisioned_ptus AS DOUBLE))
               / SUM(TRY_CAST(provisioned_ptus AS DOUBLE))
          ELSE AVG(TRY_CAST(utilization_avg AS DOUBLE))
        END AS utilization_avg"""
    return "AVG(TRY_CAST(utilization_avg AS DOUBLE)) AS utilization_avg"


def _gold_utilization_quality_expr(columns: set[str]) -> str:
    if "utilization_quality_anomaly" in columns:
        return "MAX(CASE WHEN TRY_CAST(utilization_quality_anomaly AS BOOLEAN) THEN 1 ELSE 0 END) AS utilization_quality_anomaly"
    return "CAST(NULL AS BIGINT) AS utilization_quality_anomaly"


def _build_load_gold_metrics_sql(
    *,
    bucket: str,
    hour_filter: str,
    conn_filter: str,
    project_filter: str,
    columns: set[str],
) -> str:
    util_max = "utilization_max" if "utilization_max" in columns else None
    hours_80 = (
        "SUM(CASE WHEN TRY_CAST(utilization_max AS DOUBLE) >= 80 THEN 1 ELSE 0 END) AS hours_at_or_above_80"
        if util_max
        else "CAST(NULL AS BIGINT) AS hours_at_or_above_80"
    )
    hours_95 = (
        "SUM(CASE WHEN TRY_CAST(utilization_max AS DOUBLE) >= 95 THEN 1 ELSE 0 END) AS hours_at_or_above_95"
        if util_max
        else "CAST(NULL AS BIGINT) AS hours_at_or_above_95"
    )
    return f"""
      SELECT
        CAST(connection_id AS BIGINT) AS connection_id,
        {bucket} AS metric_hour,
        CAST(account_resource_id AS VARCHAR) AS account_resource_id,
        CAST(deployment_name AS VARCHAR) AS deployment_name,
        CAST(model_name AS VARCHAR) AS model_name,
        CAST(model_version AS VARCHAR) AS model_version,
        CAST(region AS VARCHAR) AS region,
        CAST(sku_name AS VARCHAR) AS sku_name,
        {_gold_max_expr(columns, "provisioned_ptus")},
        MAX(CAST(project_hint AS VARCHAR)) AS project_hint,
        MAX(CAST(project_hint AS VARCHAR)) AS project_key,
        {_gold_sum_expr(columns, "input_tokens_sum")},
        {_gold_sum_expr(columns, "output_tokens_sum")},
        {_gold_sum_expr(columns, "total_tokens_sum")},
        {_gold_sum_expr(columns, "input_tokens_sum", alias="input_tokens")},
        {_gold_sum_expr(columns, "output_tokens_sum", alias="output_tokens")},
        {_gold_sum_expr(columns, "total_tokens_sum", alias="total_tokens")},
        {_gold_utilization_avg_expr(columns)},
        {_gold_max_expr(columns, "utilization_max")},
        {_gold_max_expr(columns, "utilization_p95")},
        {_gold_max_expr(columns, "request_peak_per_minute")},
        {_gold_sum_expr(columns, "request_count")},
        {_gold_sum_expr(columns, "success_count")},
        {_gold_sum_expr(columns, "client_error_count")},
        {_gold_sum_expr(columns, "server_error_count")},
        {_gold_sum_expr(columns, "rate_limited_count")},
        {_gold_sum_expr(columns, "spillover_request_count")},
        {_gold_sum_expr(columns, "streaming_request_count")},
        {_gold_sum_expr(columns, "non_streaming_request_count")},
        {_gold_request_weighted_avg_expr(columns, "time_to_response_avg_ms")},
        {_gold_max_expr(columns, "time_to_response_max_ms")},
        {_gold_request_weighted_avg_expr(columns, "time_to_last_byte_avg_ms")},
        {_gold_request_weighted_avg_expr(columns, "normalized_ttft_avg_ms")},
        {_gold_max_expr(columns, "normalized_ttft_max_ms")},
        {_gold_request_weighted_avg_expr(columns, "normalized_tbt_avg_ms")},
        {_gold_max_expr(columns, "normalized_tbt_max_ms")},
        {_gold_avg_expr(columns, "tokens_per_second_avg")},
        {_gold_max_expr(columns, "tokens_per_second_max")},
        {_gold_request_weighted_avg_expr(columns, "context_cache_match_rate_avg")},
        {_gold_request_weighted_avg_expr(columns, "availability_rate_avg")},
        {hours_80},
        {hours_95},
        {_gold_utilization_quality_expr(columns)},
        COUNT(*) AS sample_count
      FROM iceberg.gold.dest_azure_ptu_metrics
      WHERE {hour_filter}{conn_filter}{project_filter}
      GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
      ORDER BY metric_hour DESC
    """


async def load_gold_metrics(
    *,
    hours: int = 24,
    connection_ids: list[int] | None = None,
    project_hints: list[str] | None = None,
    grain: str = "hour",
    timeout_s: float = DASHBOARD_TRINO_TIMEOUT_S,
) -> list[dict[str, Any]]:
    """Load PTU metrics from Gold Iceberg, aggregated in SQL at hour/day/month (never PT1M)."""
    from app.services.trino_service import execute as trino_execute

    effective_grain = grain if grain in {"hour", "day", "month"} else "day"
    if effective_grain == "hour" and hours > 7 * 24:
        effective_grain = "day"
    start_iso, end_iso = _gold_metrics_window(hours)
    conn_filter = _gold_connection_filter(connection_ids)
    project_filter = _gold_project_hint_filter(project_hints)
    bucket = _gold_metrics_bucket_expr(effective_grain)
    hour_filter = _gold_metric_hour_filter(start_iso, end_iso)
    columns = await _gold_ptu_table_columns()
    sql = _build_load_gold_metrics_sql(
        bucket=bucket,
        hour_filter=hour_filter,
        conn_filter=conn_filter,
        project_filter=project_filter,
        columns=columns,
    )
    try:
        rows = await trino_execute(sql, timeout_s=timeout_s)
        return [dict(row) for row in (rows or [])]
    except Exception as exc:
        logger.warning("Gold PTU metrics query failed: %s", exc, exc_info=True)
        global _gold_ptu_columns_cache
        _gold_ptu_columns_cache = None
        return []


async def load_gold_heatmap(
    *,
    hours: int = 168,
    connection_ids: list[int] | None = None,
    project_hints: list[str] | None = None,
    timeout_s: float = DASHBOARD_TRINO_TIMEOUT_S,
) -> list[dict[str, Any]]:
    """Aggregate utilization by weekday/hour in SQL (no hourly row dump to Python)."""
    from app.services.trino_service import execute as trino_execute

    window_hours = min(max(1, hours), 93 * 24)
    start_iso, end_iso = _gold_metrics_window(window_hours)
    conn_filter = _gold_connection_filter(connection_ids)
    project_filter = _gold_project_hint_filter(project_hints)
    hour_filter = _gold_metric_hour_filter(start_iso, end_iso)
    sql = f"""
      SELECT
        {_gold_metric_hour_weekday()} AS weekday,
        {_gold_metric_hour_hour()} AS hour,
        CASE WHEN SUM(TRY_CAST(provisioned_ptus AS DOUBLE)) > 0
          THEN SUM(TRY_CAST(utilization_avg AS DOUBLE) * TRY_CAST(provisioned_ptus AS DOUBLE))
               / SUM(TRY_CAST(provisioned_ptus AS DOUBLE))
          ELSE AVG(TRY_CAST(utilization_avg AS DOUBLE))
        END AS utilization_avg,
        COALESCE(SUM(TRY_CAST(input_tokens_sum AS DOUBLE)), 0) AS input_tokens,
        COALESCE(SUM(TRY_CAST(output_tokens_sum AS DOUBLE)), 0) AS output_tokens,
        COALESCE(SUM(TRY_CAST(total_tokens_sum AS DOUBLE)), 0) AS total_tokens,
        COUNT(*) AS samples
      FROM iceberg.gold.dest_azure_ptu_metrics
      WHERE {hour_filter}{conn_filter}{project_filter}
        AND utilization_avg IS NOT NULL
      GROUP BY 1, 2
      ORDER BY 1, 2
    """
    try:
        rows = await trino_execute(sql, timeout_s=timeout_s)
        return [
            {
                "weekday": int(row.get("weekday") or 0),
                "hour": int(row.get("hour") or 0),
                "utilization_avg": float(row.get("utilization_avg") or 0),
                "input_tokens": float(row.get("input_tokens") or 0),
                "output_tokens": float(row.get("output_tokens") or 0),
                "total_tokens": float(row.get("total_tokens") or 0),
                "samples": int(row.get("samples") or 0),
            }
            for row in (rows or [])
        ]
    except Exception as exc:
        logger.warning("Gold PTU heatmap query failed: %s", exc, exc_info=True)
        return []


async def load_gold_inventory(
    connection_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Load PTU deployment inventory from Gold capacity; empty when unavailable."""
    from app.services.trino_service import execute as trino_execute

    conn_filter = ""
    if connection_ids:
        ids = ", ".join(str(int(x)) for x in connection_ids)
        conn_filter = f" AND CAST(connection_id AS BIGINT) IN ({ids})"
    capacity_sql = f"""
      SELECT CAST(deployment_resource_id AS VARCHAR) AS id,
             CAST(account_resource_id AS VARCHAR) AS account_resource_id,
             CAST(deployment_name AS VARCHAR) AS deployment_name,
             CAST(sku_name AS VARCHAR) AS sku_name,
             CAST(model_name AS VARCHAR) AS model_name,
             CAST(model_version AS VARCHAR) AS model_version,
             CAST(region AS VARCHAR) AS region,
             CAST(project_hint AS VARCHAR) AS project_hint,
             TRY_CAST(provisioned_ptus AS DOUBLE) AS ptus
      FROM (
        SELECT CAST(deployment_resource_id AS VARCHAR) AS deployment_resource_id,
               CAST(account_resource_id AS VARCHAR) AS account_resource_id,
               CAST(deployment_name AS VARCHAR) AS deployment_name,
               CAST(sku_name AS VARCHAR) AS sku_name,
               CAST(model_name AS VARCHAR) AS model_name,
               CAST(model_version AS VARCHAR) AS model_version,
               CAST(region AS VARCHAR) AS region,
               CAST(project_hint AS VARCHAR) AS project_hint,
               TRY_CAST(provisioned_ptus AS DOUBLE) AS provisioned_ptus,
               row_number() OVER (
                 PARTITION BY CAST(deployment_resource_id AS VARCHAR)
                 ORDER BY CAST(COALESCE(valid_from, '000000') AS VARCHAR) DESC
               ) AS _rn
        FROM iceberg.gold.dest_azure_ptu_capacity
        WHERE valid_until IS NULL{conn_filter}
      ) cap
      WHERE cap._rn = 1
    """
    try:
        rows = await trino_execute(capacity_sql, timeout_s=DASHBOARD_TRINO_TIMEOUT_S)
        if rows:
            return [
                {
                    **dict(row),
                    "name": row.get("deployment_name"),
                    "provisioned_ptus": row.get("ptus"),
                }
                for row in rows
            ]
    except Exception as exc:
        logger.info("Gold PTU capacity unavailable: %s", exc)

    # Fallback: latest row per deployment from a bounded metrics window (not full table scan).
    start_iso, end_iso = _gold_metrics_window(93 * 24)
    hour_filter = _gold_metric_hour_filter(start_iso, end_iso)
    hour_col = _gold_metric_hour_varchar()
    metrics_sql = f"""
      SELECT deployment_name, account_resource_id, model_name, model_version, region,
             sku_name, project_hint, ptus, metric_hour
      FROM (
        SELECT CAST(deployment_name AS VARCHAR) AS deployment_name,
               CAST(account_resource_id AS VARCHAR) AS account_resource_id,
               CAST(model_name AS VARCHAR) AS model_name,
               CAST(model_version AS VARCHAR) AS model_version,
               CAST(region AS VARCHAR) AS region,
               CAST(sku_name AS VARCHAR) AS sku_name,
               CAST(project_hint AS VARCHAR) AS project_hint,
               TRY_CAST(provisioned_ptus AS DOUBLE) AS ptus,
               {hour_col} AS metric_hour,
               row_number() OVER (
                 PARTITION BY CAST(deployment_name AS VARCHAR)
                 ORDER BY {hour_col} DESC
               ) AS rn
        FROM iceberg.gold.dest_azure_ptu_metrics
        WHERE {hour_filter}{conn_filter}
      ) latest
      WHERE rn = 1
    """
    try:
        rows = await trino_execute(metrics_sql, timeout_s=DASHBOARD_TRINO_TIMEOUT_S)
        if not rows:
            return []
        return [
            {
                "id": str(row.get("deployment_name") or ""),
                "deployment_name": row.get("deployment_name"),
                "name": row.get("deployment_name"),
                "account_resource_id": row.get("account_resource_id"),
                "sku_name": row.get("sku_name"),
                "ptus": float(row.get("ptus") or 0),
                "model_name": row.get("model_name"),
                "model_version": row.get("model_version"),
                "region": row.get("region"),
                "project_hint": row.get("project_hint"),
            }
            for row in rows
            if row.get("deployment_name")
        ]
    except Exception as exc:
        logger.warning("Gold PTU metrics inventory fallback failed: %s", exc, exc_info=True)
        return []


def persist_hourly(
    connection_id: int,
    inventory: dict[str, Any],
    metrics: list[dict[str, Any]],
    *,
    connection_code: str | None = None,
    write_capacity: bool = True,
    strict_gold: bool = False,
) -> dict[str, int]:
    """Write Bronze audit + Gold hourly metrics and SCD2 capacity. Iceberg optional."""
    import concurrent.futures
    import pandas as pd
    from app.services import iceberg_service as iceberg
    from app.services.azure_ptu_meters import scd2_apply_capacity_change

    def _run_timed(label: str, fn: Any) -> Any:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(fn)
            try:
                return future.result(timeout=PERSIST_TIMEOUT_S)
            except concurrent.futures.TimeoutError as exc:
                raise TimeoutError(
                    f"Persistência Iceberg ({label}) excedeu {PERSIST_TIMEOUT_S}s"
                ) from exc

    collected_at = datetime.now(timezone.utc).isoformat()
    bronze_rows = []
    gold_rows = []
    for row in metrics:
        hour = str(row.get("metric_hour") or "")
        period = hour[:7].replace("-", "") if hour else datetime.now(timezone.utc).strftime("%Y%m")
        payload = {
            "connection_id": connection_id,
            "metric_hour": hour,
            "account_resource_id": row.get("account_resource_id"),
            "deployment_name": row.get("deployment_name"),
            "model_name": row.get("model_name"),
            "model_version": row.get("model_version"),
            "region": row.get("region"),
            "sku_name": row.get("sku_name"),
            "provisioned_ptus": row.get("provisioned_ptus") or row.get("ptus"),
            "input_tokens_sum": row.get("input_tokens_sum") or row.get("input_tokens") or 0,
            "output_tokens_sum": row.get("output_tokens_sum") or row.get("output_tokens") or 0,
            "total_tokens_sum": row.get("total_tokens_sum") or row.get("total_tokens") or 0,
            "active_tokens_sum": row.get("active_tokens_sum") or row.get("active_tokens") or 0,
            "active_tokens_max": row.get("active_tokens_max"),
            "active_tokens_p95": row.get("active_tokens_p95"),
            "utilization_avg": row.get("utilization_avg"),
            "utilization_max": row.get("utilization_max"),
            "utilization_p95": row.get("utilization_p95"),
            "request_count": row.get("request_count"),
            "request_peak_per_minute": row.get("request_peak_per_minute"),
            "success_count": row.get("success_count"),
            "client_error_count": row.get("client_error_count"),
            "server_error_count": row.get("server_error_count"),
            "rate_limited_count": row.get("rate_limited_count"),
            "spillover_request_count": row.get("spillover_request_count"),
            "time_to_response_avg_ms": row.get("time_to_response_avg_ms"),
            "time_to_response_max_ms": row.get("time_to_response_max_ms"),
            "time_to_response_p95_ms": row.get("time_to_response_p95_ms"),
            "time_to_last_byte_avg_ms": row.get("time_to_last_byte_avg_ms"),
            "normalized_ttft_avg_ms": row.get("normalized_ttft_avg_ms"),
            "normalized_ttft_max_ms": row.get("normalized_ttft_max_ms"),
            "normalized_ttft_p95_ms": row.get("normalized_ttft_p95_ms"),
            "normalized_tbt_avg_ms": row.get("normalized_tbt_avg_ms"),
            "normalized_tbt_max_ms": row.get("normalized_tbt_max_ms"),
            "normalized_tbt_p95_ms": row.get("normalized_tbt_p95_ms"),
            "tokens_per_second_avg": row.get("tokens_per_second_avg"),
            "tokens_per_second_max": row.get("tokens_per_second_max"),
            "context_cache_match_rate_avg": row.get("context_cache_match_rate_avg"),
            "availability_rate_avg": row.get("availability_rate_avg"),
            "utilization_avg_raw": row.get("utilization_avg_raw"),
            "utilization_max_raw": row.get("utilization_max_raw"),
            "utilization_quality_anomaly": row.get("utilization_quality_anomaly"),
            "streaming_request_count": row.get("streaming_request_count"),
            "non_streaming_request_count": row.get("non_streaming_request_count"),
            "source_points": row.get("source_points"),
            "expected_points": row.get("expected_points"),
            "completeness_pct": row.get("completeness_pct"),
            "collected_at": row.get("collected_at") or collected_at,
            "project_hint": row.get("project_hint"),
            "_billing_period": period,
        }
        gold_rows.append(payload)
        bronze_rows.append({**payload, "window_start": hour, "raw_dimensions": str({k: row.get(k) for k in ("id", "account_name")})})
    counts = {"bronze": 0, "gold_metrics": 0, "gold_capacity": 0}
    try:
        if bronze_rows:
            # Append-only audit table. Never delete bronze history.
            counts["bronze"] = iceberg.write_azure_ptu_bronze_metrics(pd.DataFrame(bronze_rows))
    except Exception as exc:
        logger.warning("Azure PTU Bronze persist failed: %s", exc)
    try:
        if gold_rows:
            gold_df = pd.DataFrame(gold_rows)
            for col in GOLD_METRIC_OPTIONAL_COLUMNS:
                if col not in gold_df.columns:
                    gold_df[col] = None
            counts["gold_metrics"] = _run_timed(
                "dest_azure_ptu_metrics",
                lambda: iceberg.upsert_azure_ptu_hourly(gold_df),
            )
    except Exception as exc:
        logger.warning("Azure PTU hourly Gold persist failed: %s", exc)
        if strict_gold:
            raise
    try:
        cap_rows: list[dict[str, Any]] = []
        if write_capacity:
            observed = collected_at
            for dep in inventory.get("deployments") or []:
                cap_rows = scd2_apply_capacity_change(
                    cap_rows,
                    deployment_id=str(dep.get("id") or dep.get("deployment_name")),
                    ptus=float(dep.get("ptus") or 0),
                    observed_at=observed,
                    extra={
                        "connection_id": connection_id,
                        "account_resource_id": dep.get("account_resource_id"),
                        "deployment_name": dep.get("deployment_name"),
                        "sku_name": dep.get("sku_name"),
                        "model_name": dep.get("model_name"),
                        "model_version": dep.get("model_version"),
                        "region": dep.get("region"),
                        "project_hint": dep.get("project_hint"),
                    },
                )
            if cap_rows:
                for row in cap_rows:
                    row["_billing_period"] = str(row.get("valid_from") or collected_at)[:7].replace("-", "")
                counts["gold_capacity"] = iceberg.write_azure_ptu_capacity(pd.DataFrame(cap_rows))
    except Exception as exc:
        logger.warning("Azure PTU capacity Gold persist failed: %s", exc)
    return counts
