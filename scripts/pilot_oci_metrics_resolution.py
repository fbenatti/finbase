#!/usr/bin/env python3
"""Read-only, bounded OCI pilot; never writes application databases/lakehouse.

Run from the repository root with a configured OCI API key::

    python3 scripts/pilot_oci_metrics_resolution.py \
      --config env/oci_config_preview.conf --compartment tivit \
      --output apps/data/metric-pilot/oci-resolution

Raw responses and resource identities stay in the ignored output directory.
Only ``summary.json`` is suitable for copying to a versioned research report.
"""
from __future__ import annotations

import argparse
import configparser
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import oci
import pandas as pd


METRICS = (
    "CpuUtilization", "MemoryUtilization", "DiskIopsRead", "DiskIopsWritten",
    "DiskBytesRead", "DiskBytesWritten", "NetworksBytesIn", "NetworksBytesOut",
)
STATISTICS = {
    "mean": "mean()", "min": "min()", "max": "max()", "count": "count()",
    "sum": "sum()", "p90": "percentile(0.90)",
    "p95": "percentile(0.95)", "p99": "percentile(0.99)",
}


def dump(path: Path, value: object) -> int:
    body = json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":"))
    path.write_text(body + "\n")
    return len(body.encode())


def quantiles(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    a = np.asarray(values, dtype=float)
    return {
        "n": len(a), "mean": float(a.mean()), "min": float(a.min()),
        "max": float(a.max()),
        **{f"p{q}": float(np.percentile(a, q, method="linear")) for q in (90, 95, 99)},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--profile", default="DEFAULT")
    parser.add_argument("--compartment", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--instances", type=int, choices=(1, 2, 3), default=2)
    parser.add_argument("--hours", type=int, choices=(6, 12, 24), default=24)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    raw_dir = output / "raw"
    raw_dir.mkdir()
    conf = configparser.ConfigParser(interpolation=None)
    config_path = Path(args.config).resolve()
    conf.read(config_path)
    cfg = dict(conf[args.profile])
    key = Path(cfg["key_file"]).expanduser()
    if not key.is_file():
        cfg["key_file"] = str(config_path.parent / key.name)
    oci.config.validate_config(cfg)
    client_options = {"retry_strategy": oci.retry.NoneRetryStrategy(), "timeout": (5, 30)}
    identity = oci.identity.IdentityClient(cfg, **client_options)
    compute = oci.core.ComputeClient(cfg, **client_options)
    monitor = oci.monitoring.MonitoringClient(cfg, **client_options)
    discovery_calls = 0
    compartments = oci.pagination.list_call_get_all_results(
        identity.list_compartments, compartment_id=cfg["tenancy"],
        compartment_id_in_subtree=True, access_level="ACCESSIBLE",
    ).data
    discovery_calls += 1  # Logical calls; pagination can add transport requests.
    matches = [c for c in compartments if c.name == args.compartment and c.lifecycle_state == "ACTIVE"]
    if len(matches) != 1:
        raise RuntimeError("Compartment name must identify exactly one active compartment")
    compartment = matches[0]
    instances = oci.pagination.list_call_get_all_results(
        compute.list_instances, compartment_id=compartment.id,
    ).data
    discovery_calls += 1
    active_metrics = oci.pagination.list_call_get_all_results(
        monitor.list_metrics, compartment_id=compartment.id,
        list_metrics_details=oci.monitoring.models.ListMetricsDetails(
            namespace="oci_computeagent", name="CpuUtilization",
        ),
    ).data
    discovery_calls += 1
    emitting_ids = {m.dimensions.get("resourceId") for m in active_metrics}
    selected = sorted(
        (i for i in instances if i.lifecycle_state == "RUNNING" and i.id in emitting_ids),
        key=lambda i: hashlib.sha256(i.id.encode()).hexdigest(),
    )[:args.instances]
    if not selected:
        raise RuntimeError("No running instances with discoverable CPU telemetry")
    dump(output / "inventory.private.json", [oci.util.to_dict(i) for i in selected])
    now = datetime.now(timezone.utc)
    end = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 15 + 15)
    start = end - timedelta(hours=args.hours)
    rows: list[dict] = []
    calls: list[dict] = []
    history: list[dict] = []
    summary = {
        "pilot_started_at": now.isoformat(), "cloud_provider": "oci", "region": cfg["region"],
        "scope": {"instances": len(selected), "compartments": 1, "metrics": list(METRICS)},
        "window_start_utc": start.isoformat(), "window_end_utc_exclusive": end.isoformat(),
        "hours": args.hours, "discovery_logical_calls": discovery_calls,
        "api_retries": 0, "request_min_spacing_seconds": 0.26,
        "measurement_notes": [
            "JSON sizes are locally serialized OCI SDK data, not measured wire bytes.",
            "Gzip and Parquet sizes are measured local files, not Iceberg production files.",
            "Returned points are a usage proxy, not an invoice or total billable OCI retrieval.",
            "Quantiles over bucket means are distinct from provider percentiles within buckets.",
            "The pilot does not establish seasonal or fleet-wide representativeness.",
        ],
    }
    last_call_at = 0.0

    def query(instance, metric, minutes, statistic, window_start, window_end, *, historical=False):
        nonlocal last_call_at
        time.sleep(max(0.0, 0.26 - (time.perf_counter() - last_call_at)))
        alias = "vm-" + hashlib.sha256(instance.id.encode()).hexdigest()[:10]
        query_text = f'{metric}[{minutes}m]{{resourceId = "{instance.id}"}}.{STATISTICS[statistic]}'
        entry = {
            "resource_alias": alias, "metric": metric, "minutes": minutes, "statistic": statistic,
            "start": window_start.isoformat(), "end": window_end.isoformat(), "historical": historical,
        }
        details = oci.monitoring.models.SummarizeMetricsDataDetails(
            namespace="oci_computeagent", query=query_text, resolution=f"{minutes}m",
            start_time=window_start, end_time=window_end,
        )
        last_call_at = time.perf_counter()
        try:
            response = monitor.summarize_metrics_data(
                compartment_id=compartment.id, summarize_metrics_data_details=details,
            )
            entry["elapsed_seconds"] = round(time.perf_counter() - last_call_at, 6)
            payload = oci.util.to_dict(response.data)
            body = json.dumps(payload, default=str, ensure_ascii=False, separators=(",", ":")).encode()
            entry.update(status="OK", streams=len(response.data), json_bytes=len(body),
                         gzip_bytes=len(gzip.compress(body, mtime=0)))
            (raw_dir / f"{len(calls):04d}.json").write_bytes(body)
            kept, returned = 0, 0
            for stream in response.data:
                dimensions = json.dumps(stream.dimensions or {}, sort_keys=True, separators=(",", ":"))
                for point in stream.aggregated_datapoints or []:
                    returned += 1
                    # Providers can return a point at end; use [start, end).
                    if not window_start <= point.timestamp < window_end:
                        continue
                    kept += 1
                    if not historical:
                        rows.append({
                            "resource_alias": alias, "metric": metric, "minutes": minutes,
                            "statistic": statistic, "timestamp": point.timestamp,
                            "value": float(point.value), "dimensions_json": dimensions,
                            "namespace": stream.namespace, "source_raw_file": f"raw/{len(calls):04d}.json",
                        })
            entry.update(returned_points=returned, kept_points=kept)
        except oci.exceptions.ServiceError as exc:
            entry.update(status="ERROR", code=exc.code, http_status=exc.status,
                         elapsed_seconds=round(time.perf_counter() - last_call_at, 6))
        calls.append(entry)
        dump(output / "calls.private.json", calls)
        if len(calls) % 24 == 0:
            print(f"progress calls={len(calls)} rows={len(rows)} errors={sum(c['status'] != 'OK' for c in calls)}", flush=True)
        return entry

    for instance in selected:
        print("selected vm-" + hashlib.sha256(instance.id.encode()).hexdigest()[:10], flush=True)
        for minutes in (1, 5, 15):
            for metric in METRICS:
                for statistic in STATISTICS:
                    query(instance, metric, minutes, statistic, start, end)
    # Short old windows test availability, without requesting months of data.
    for age_days, minutes in ((8, 1), (31, 5), (35, 15), (59, 60)):
        old_end = end - timedelta(days=age_days)
        entry = query(selected[0], "CpuUtilization", minutes, "mean", old_end - timedelta(hours=2), old_end, historical=True)
        history.append(entry)
    frame = pd.DataFrame(rows)
    frame.to_parquet(output / "points.private.parquet", index=False, compression="zstd")
    resolution_reports = []
    comparisons = []
    for minutes in (1, 5, 15):
        sample = frame[frame.minutes == minutes]
        selected_calls = [c for c in calls if c["minutes"] == minutes and not c["historical"]]
        parquet = output / f"points-{minutes}m.private.parquet"
        sample.to_parquet(parquet, index=False, compression="zstd")
        resolution_reports.append({
            "minutes": minutes, "calls": len(selected_calls),
            "errors": sum(c["status"] != "OK" for c in selected_calls),
            "returned_points": sum(c.get("returned_points", 0) for c in selected_calls),
            "kept_points": len(sample), "json_bytes": sum(c.get("json_bytes", 0) for c in selected_calls),
            "sum_response_gzip_bytes": sum(c.get("gzip_bytes", 0) for c in selected_calls),
            "parquet_zstd_bytes": parquet.stat().st_size,
            "api_elapsed_sum_seconds": sum(c["elapsed_seconds"] for c in selected_calls),
            "api_latency_seconds": quantiles([c["elapsed_seconds"] for c in selected_calls]),
            "expected_mean_buckets_per_stream": args.hours * 60 // minutes,
        })
        for (alias, metric), group in sample.groupby(["resource_alias", "metric"]):
            means = group[group.statistic == "mean"].sort_values("timestamp")
            peaks = group[group.statistic == "max"]
            # Per-stream comparisons; do not blend dimensions or resources.
            if means.dimensions_json.nunique() > 1:
                raise RuntimeError("Pilot found multiple dimensions for one resource; compare streams separately")
            baseline = frame[(frame.resource_alias == alias) & (frame.metric == metric) &
                             (frame.minutes == 1) & (frame.statistic == "mean")]
            stats = quantiles(means.value.tolist())
            reference = quantiles(baseline.value.tolist())
            item = {"resource_alias": alias, "metric": metric, "minutes": minutes,
                    "bucket_mean_distribution": stats,
                    "coverage": len(means) / (args.hours * 60 / minutes),
                    "max_of_provider_maxima": float(peaks.value.max()) if len(peaks) else None,
                    "provider_percentile_points": {k: int((group.statistic == k).sum()) for k in ("p90", "p95", "p99")}}
            if stats["n"] and reference["n"]:
                item["relative_change_vs_1m"] = {
                    k: (stats[k] - reference[k]) / abs(reference[k]) if reference[k] else None
                    for k in ("p90", "p95", "p99", "max")
                }
            if metric in ("CpuUtilization", "MemoryUtilization"):
                item["observed_minutes_bucket_mean_over_threshold"] = {
                    str(t): int((means.value > t).sum()) * minutes for t in (50, 70, 80, 90)
                }
            comparisons.append(item)
    summary.update(resolutions=resolution_reports, comparisons=comparisons,
                   historical_probes=history, calls_total=len(calls),
                   errors_total=sum(c["status"] != "OK" for c in calls),
                   completed_at=datetime.now(timezone.utc).isoformat())
    dump(output / "summary.json", summary)
    print(json.dumps({"summary": str(output / "summary.json"), "calls": len(calls),
                      "errors": summary["errors_total"], "rows": len(rows)}, indent=2), flush=True)


if __name__ == "__main__":
    main()
