#!/usr/bin/env python3
"""OCI GPU agent — publishes nvidia-smi metrics to OCI Monitoring.

Why this exists
---------------
OCI publishes GpuUtilization / GpuMemoryUtilization in the ``oci_computeagent``
namespace ONLY for Bare Metal GPU shapes (BM.GPU.*). For VM.GPU shapes
(VM.GPU.A10.1 etc.) the Compute agent does not report GPU metrics, so this
lightweight in-guest agent reads ``nvidia-smi`` directly and POSTs the values to
OCI Monitoring under a CUSTOM namespace (default ``custom_gpu``). The OpenFinops
collector (``oci_operational_monitoring.py``) reads that namespace when the OCI
connection config declares ``custom_metric_namespaces``.

Auth
----
* On an OCI instance: Instance Principal (recommended). Create a dynamic group
  matching the GPU instances and a policy allowing ``metrics:post-metric-data``
  (see README.md).
* Fallback: API-key based config in ``~/.oci/config`` (same layout as the
  ``oci`` CLI). Set ``OCI_AGENT_AUTH=api_key`` to force it.

Metrics published (namespace ``custom_gpu``, one datapoint per GPU per cycle):
  gpu_utilization_percent, gpu_memory_utilization_percent,
  gpu_temperature_celsius, gpu_power_watts, gpu_memory_used_bytes,
  gpu_memory_total_bytes

Dimensions: resourceId (instance OCID), hostname, shape, gpu_index.

Usage:
    python3 oci_gpu_agent.py [--interval 60] [--namespace custom_gpu] [--once]
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any

import oci  # oci python sdk (pip install oci)


def instance_metadata() -> dict[str, str]:
    """Read OCI instance metadata from the link-local endpoint."""
    base = "http://169.254.169.254/opc/v2"
    out: dict[str, str] = {"instance_id": "", "compartment_id": "", "region": ""}
    for key, path in (
        ("instance_id", "/instance"),
        ("compartment_id", "/instance"),
        ("region", "/instance"),
    ):
        try:
            with urllib.request.urlopen(f"{base}{path}", timeout=5) as resp:
                data = json.load(resp)
            if key == "instance_id":
                out["instance_id"] = data.get("id", "")
            elif key == "compartment_id":
                out["compartment_id"] = data.get("compartmentId", "")
            elif key == "region":
                out["region"] = data.get("region", "")
        except Exception:
            pass
    return out


def get_signer(meta: dict[str, str]):
    if os.environ.get("OCI_AGENT_AUTH", "").lower() == "api_key":
        return oci.config.from_file()
    try:
        signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
        region = signer.region or meta.get("region")
        if not region:
            raise RuntimeError("region unknown via Instance Principal")
        signer.region = region
        return signer
    except Exception as exc:
        raise RuntimeError(
            "Failed to build Instance Principal signer. Run on an OCI instance "
            "with a dynamic group, or set OCI_AGENT_AUTH=api_key with ~/.oci/config. "
            f"({exc})"
        ) from exc


def read_nvidia_smi() -> list[dict[str, Any]]:
    """Return one dict per GPU from nvidia-smi."""
    cmd = [
        "nvidia-smi",
        "--query-gpu=index,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
        "--format=csv,noheader,nounits",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"nvidia-smi failed: {proc.stderr.strip()[:200]}")
    gpus: list[dict[str, Any]] = []
    for line in proc.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue
        try:
            gpus.append(
                {
                    "gpu_index": parts[0],
                    "utilization.gpu": float(parts[1]),
                    "memory.used": float(parts[2]),
                    "memory.total": float(parts[3]),
                    "temperature.gpu": float(parts[4]),
                    "power.draw": float(parts[5]),
                }
            )
        except ValueError:
            continue
    return gpus


def publish(monitoring_client: Any, compartment_id: str, namespace: str,
            meta: dict[str, str], gpus: list[dict[str, Any]], now: datetime) -> int:
    """POST one datapoint per metric per GPU."""
    hostname = socket.gethostname()
    shape = os.environ.get("OCI_AGENT_SHAPE", "")
    metric_data: list[Any] = []
    for gpu in gpus:
        dims = {
            "resourceId": meta["instance_id"],
            "hostname": hostname,
            "shape": shape,
            "gpu_index": gpu["gpu_index"],
        }
        for name, value in (
            ("gpu_utilization_percent", gpu["utilization.gpu"]),
            ("gpu_memory_utilization_percent", (gpu["memory.used"] / gpu["memory.total"] * 100) if gpu["memory.total"] else 0.0),
            ("gpu_temperature_celsius", gpu["temperature.gpu"]),
            ("gpu_power_watts", gpu["power.draw"]),
            ("gpu_memory_used_bytes", gpu["memory.used"] * 1024 * 1024),
            ("gpu_memory_total_bytes", gpu["memory.total"] * 1024 * 1024),
        ):
            metric_data.append(
                oci.monitoring.models.MetricDataDetails(
                    namespace=namespace,
                    compartment_id=compartment_id,
                    name=name,
                    dimensions=dims,
                    datapoints=[
                        oci.monitoring.models.Datapoint(
                            timestamp=now,
                            value=value,
                        )
                    ],
                )
            )
    details = oci.monitoring.models.PostMetricDataDetails(metric_data=metric_data)
    resp = monitoring_client.post_metric_data(post_metric_data_details=details)
    return resp.status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=int, default=60, help="poll interval in seconds")
    parser.add_argument("--namespace", default="custom_gpu", help="OCI Monitoring namespace")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    args = parser.parse_args()

    meta = instance_metadata()
    if not meta["instance_id"]:
        print("ERROR: not running on an OCI instance (no link-local metadata).", file=sys.stderr)
        return 2
    os.environ.setdefault("OCI_AGENT_SHAPE", os.environ.get("OCI_AGENT_SHAPE", ""))

    signer = get_signer(meta)
    region = signer.region if hasattr(signer, "region") and signer.region else meta["region"]
    monitoring_client = oci.monitoring.MonitoringClient(
        {"region": region or os.environ.get("OCI_REGION", "sa-saopaulo-1")},
        signer=signer,
    )

    print(f"[oci-gpu-agent] publishing to namespace '{args.namespace}' in compartment {meta['compartment_id']}", flush=True)

    while True:
        cycle_start = time.monotonic()
        try:
            gpus = read_nvidia_smi()
            if not gpus:
                print("[oci-gpu-agent] nvidia-smi returned no GPUs", flush=True)
            else:
                status = publish(
                    monitoring_client, meta["compartment_id"], args.namespace, meta, gpus,
                    datetime.now(timezone.utc),
                )
                print(f"[oci-gpu-agent] published {len(gpus)} GPU(s) -> HTTP {status}", flush=True)
        except Exception as exc:
            print(f"[oci-gpu-agent] error: {exc}", file=sys.stderr, flush=True)

        if args.once:
            return 0
        elapsed = time.monotonic() - cycle_start
        time.sleep(max(1, args.interval - elapsed))


if __name__ == "__main__":
    raise SystemExit(main())
