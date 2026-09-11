"""OCI Generative AI and GPU operational collection (read-only OCI APIs)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from typing import Any

import oci
import pandas as pd

from app.models.source_connection import SourceConnection
from app.services import iceberg_service as iceberg
from app.services.oci_extractor import OciExtractor

GOLD_TABLE = "dest_oci_operational_metrics"
METRICS = {
    "oci_generativeai": ["InputTokenCount", "OutputTokenCount", "TotalInvocationCount", "InvocationLatency", "ClientErrorCount", "ServerErrorCount"],
    "oci_computeagent": ["GpuUtilization", "GpuMemoryUtilization", "CpuUtilization", "MemoryUtilization", "NetworkBytesIn", "NetworkBytesOut"],
}


def configured_compartments(connection: SourceConnection) -> list[str]:
    values = (connection.config_json or {}).get("generative_ai_compartment_ids") or []
    if isinstance(values, str):
        values = [x.strip() for x in values.split(",") if x.strip()]
    return [str(x).strip() for x in values if str(x).strip()]


def configured_regions(connection: SourceConnection) -> list[str]:
    values = (connection.config_json or {}).get("operational_regions") or (connection.config_json or {}).get("region") or []
    if isinstance(values, str):
        values = [x.strip() for x in values.split(",") if x.strip()]
    return list(dict.fromkeys(str(x).strip() for x in values if str(x).strip()))


def custom_metric_namespaces(connection: SourceConnection) -> dict[str, list[str]]:
    """Custom Monitoring namespaces published by in-guest agents (e.g. GPU via nvidia-smi).

    Format in the connection config::

        "custom_metric_namespaces": {
            "custom_gpu": ["gpu_utilization_percent", "gpu_memory_utilization_percent",
                           "gpu_temperature_celsius", "gpu_power_watts"]
        }

    Metrics are queried for GPU compute instances only (shapes containing ".GPU"),
    the same hosts where the agent (scripts/oci-gpu-agent) publishes them.
    """
    raw = (connection.config_json or {}).get("custom_metric_namespaces") or {}
    if isinstance(raw, str):
        raw = {}
    result: dict[str, list[str]] = {}
    for ns, names in raw.items():
        if isinstance(names, list):
            result[str(ns).strip()] = [str(n).strip() for n in names if str(n).strip()]
    return result


def discover_compartments(connection: SourceConnection) -> dict[str, Any]:
    """Return every compartment visible to the configured OCI principal.

    This is deliberately read-only.  The result can be persisted in the
    connection configuration before an operational collection is started.
    """
    extractor = OciExtractor(connection)
    try:
        cfg = extractor._get_oci_config()
        tenancy = cfg["tenancy"]
        identity = oci.identity.IdentityClient(cfg)
        response = identity.list_compartments(
            compartment_id=tenancy,
            compartment_id_in_subtree=True,
            access_level="ACCESSIBLE",
            limit=1000,
        )
        items = [
            {"id": tenancy, "name": "Tenancy root", "lifecycle_state": "ACTIVE", "is_root": True},
            *[
                {"id": item.id, "name": item.name, "lifecycle_state": item.lifecycle_state, "is_root": False}
                for item in response.data
                if str(item.lifecycle_state).upper() == "ACTIVE"
            ],
        ]
        return {"available": True, "compartments": items}
    except Exception as exc:
        return {"available": False, "compartments": [], "reason": f"{type(exc).__name__}: {str(exc)[:300]}"}


def _resource_rows(client: Any, compartment_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []; errors: list[str] = []
    for method, resource_type in (("list_dedicated_ai_clusters", "dedicated_ai_cluster"), ("list_endpoints", "endpoint")):
        try:
            response = getattr(client, method)(compartment_id=compartment_id, limit=1000)
            for item in response.data.items:
                payload = oci.util.to_dict(item)
                rows.append({"record_type": "inventory", "resource_type": resource_type, "resource_id": payload.get("id"), "resource_name": payload.get("display_name"), "compartment_id": compartment_id, "compartment_name": None, "region": None, "model_id": payload.get("model_id") or payload.get("base_model_id"), "cluster_id": payload.get("dedicated_ai_cluster_id"), "unit_shape": payload.get("unit_shape"), "unit_count": payload.get("unit_count"), "lifecycle_state": payload.get("lifecycle_state"), "collected_at": None, "raw_json": str(payload)})
        except Exception as exc:
            errors.append(f"{resource_type}: {type(exc).__name__}: {str(exc)[:180]}")
    return rows, errors


def gold_frame_from_bronze(bronze: pd.DataFrame, *, connection: SourceConnection, period: str) -> pd.DataFrame:
    """Build the operational Gold snapshot from its immutable Bronze history.

    The hourly collector only asks OCI for a small rolling window.  Rebuilding
    Gold from the current in-memory response (or from a failed/empty previous
    Gold read) therefore silently drops older token points.  Bronze is the
    durable history, so every Gold publication must be derived from it.
    """
    if bronze.empty:
        return pd.DataFrame()

    frame = bronze.copy()
    for col in ("timestamp", "resource_id", "metric_name", "record_type"):
        if col not in frame.columns:
            frame[col] = None

    metric_mask = frame["record_type"].eq("metric")
    metrics = frame[metric_mask].drop_duplicates(
        subset=["timestamp", "resource_id", "metric_name", "record_type"], keep="last"
    )
    inventory = frame[~metric_mask].copy()
    if not inventory.empty:
        if "collected_at" in inventory.columns:
            inventory["_sort_collected_at"] = pd.to_datetime(
                inventory["collected_at"], errors="coerce", utc=True
            )
            # Legacy snapshots may lack collected_at.  They must sort before
            # dated snapshots; otherwise ``keep='last'`` selects the undated
            # row and makes the dashboard's latest-snapshot filter return no
            # inventory at all.
            inventory = inventory.sort_values("_sort_collected_at", na_position="first")
        inventory = inventory.drop_duplicates(subset=["resource_id", "record_type"], keep="last")
        inventory = inventory.drop(columns=["_sort_collected_at"], errors="ignore")

    frame = pd.concat([metrics, inventory], ignore_index=True)
    # Legacy Gold defined ``unit`` as DOUBLE. Bronze keeps raw fields as text,
    # so normalize it before staging instead of letting schema evolution turn a
    # recoverable historical rebuild into a failed publication.
    if "unit" in frame.columns:
        frame["unit"] = pd.to_numeric(frame["unit"], errors="coerce")
    timestamps = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
    frame["_billing_period"] = timestamps.dt.strftime("%Y%m").fillna(period)
    frame["source_connection_id"] = connection.id
    frame["cloud_origem"] = connection.code
    frame["tipo"] = "realizado"
    return frame


def collect(connection: SourceConnection, *, hours: int = 72, persist: bool = True) -> dict[str, Any]:
    """Collect inventory and metric series, retaining partial results on IAM gaps."""
    import oci

    compartments = configured_compartments(connection)
    if not compartments:
        return {"available": False, "reason": "Configure generative_ai_compartment_ids na conexão OCI.", "inventory": [], "metrics": [], "errors": []}
    extractor = OciExtractor(connection); cfg = extractor._get_oci_config()
    now = datetime.now(timezone.utc); start = now - timedelta(hours=max(1, min(hours, 24 * 90)))
    collected_at = now.isoformat()
    metrics_config = {**METRICS, **custom_metric_namespaces(connection)}
    inventory: list[dict[str, Any]] = []; metrics: list[dict[str, Any]] = []; errors: list[str] = []
    try:
        for region in configured_regions(connection):
            regional_cfg = {**cfg, "region": region}
            genai = oci.generative_ai.GenerativeAiClient(regional_cfg); monitoring = oci.monitoring.MonitoringClient(regional_cfg); compute = oci.core.ComputeClient(regional_cfg)
            for compartment_id in compartments:
                rows, row_errors = _resource_rows(genai, compartment_id)
                for row in rows:
                    row["region"] = region
                    row["collected_at"] = collected_at
                inventory.extend(rows); errors.extend(f"{region}.{e}" for e in row_errors)
                resource_ids: list[tuple[str, str]] = [("oci_generativeai", str(r["resource_id"])) for r in rows]
                try:
                    instances = compute.list_instances(compartment_id=compartment_id).data
                    for instance in instances:
                        payload = oci.util.to_dict(instance); shape = str(payload.get("shape") or "")
                        inventory.append({"record_type": "inventory", "resource_type": "compute_instance", "resource_id": payload.get("id"), "resource_name": payload.get("display_name"), "compartment_id": compartment_id, "compartment_name": None, "region": region, "model_id": None, "cluster_id": None, "unit_shape": shape, "unit_count": None, "lifecycle_state": payload.get("lifecycle_state"), "collected_at": collected_at, "raw_json": str(payload)})
                        if ".GPU" in shape.upper():
                            resource_ids.append(("oci_computeagent", str(payload.get("id"))))
                            # Custom in-guest namespaces (GPU agent) are queried on the same hosts
                            for custom_ns in metrics_config:
                                if custom_ns not in METRICS:
                                    resource_ids.append((custom_ns, str(payload.get("id"))))
                except Exception as exc:
                    errors.append(f"{region}.compute_instance: {type(exc).__name__}: {str(exc)[:180]}")
                for namespace, resource_id in resource_ids:
                    metric_names = metrics_config.get(namespace, [])
                    for metric_name in metric_names:
                        try:
                            query = f'{metric_name}[1h]{{resourceId = "{resource_id}"}}.mean()'
                            details = oci.monitoring.models.SummarizeMetricsDataDetails(namespace=namespace, query=query, start_time=start, end_time=now)
                            streams = monitoring.summarize_metrics_data(compartment_id=compartment_id, summarize_metrics_data_details=details).data
                            for stream in streams:
                                for point in stream.aggregated_datapoints or []:
                                    metrics.append({"record_type": "metric", "timestamp": point.timestamp.isoformat(), "resource_type": "generative_ai" if namespace == "oci_generativeai" else "compute_instance", "resource_id": resource_id, "resource_name": None, "compartment_id": compartment_id, "compartment_name": None, "region": region, "metric_namespace": namespace, "metric_name": metric_name, "metric_value": point.value, "unit": None, "model_id": None, "cluster_id": None, "unit_shape": None, "unit_count": None, "lifecycle_state": None, "collected_at": collected_at, "raw_json": None})
                        except Exception as exc:
                            errors.append(f"{region}.{namespace}.{metric_name}: {type(exc).__name__}: {str(exc)[:140]}")
    finally:
        if extractor._temp_key_file:
            try:
                os.unlink(extractor._temp_key_file)
            except OSError:
                pass

    # Resolve compartment names for inventory rows (best-effort, cached per run)
    if inventory:
        try:
            identity = oci.identity.IdentityClient(cfg)
            name_cache: dict[str, str | None] = {}
            for row in inventory:
                cid = str(row.get("compartment_id") or "")
                if not cid:
                    continue
                if cid not in name_cache:
                    try:
                        name_cache[cid] = identity.get_compartment(cid).data.name
                    except Exception:
                        name_cache[cid] = None
                row["compartment_name"] = name_cache[cid]
        except Exception:
            pass

    records = inventory + metrics
    persisted = {"bronze_rows": 0, "gold_rows": 0}
    if persist and records:
        frame = pd.DataFrame(records); period = now.strftime("%Y%m")
        persisted["bronze_rows"] = iceberg.write_bronze(f"{connection.code}_operational", frame, period, blob_path=f"oci-operational://{connection.code}/{now:%Y%m%d%H}")
        bronze = iceberg.read_bronze(f"{connection.code}_operational")
        combined = gold_frame_from_bronze(bronze, connection=connection, period=period)
        if "collected_at" not in combined.columns:
            combined["collected_at"] = collected_at
        staged = iceberg.write_destination_staged(GOLD_TABLE, combined, column_types={"metric_value": "DOUBLE", "unit_count": "DOUBLE", "unit": "DOUBLE"})
        iceberg.swap_staged_table(GOLD_TABLE, staged); persisted["gold_rows"] = len(combined)
    return {"available": True, "inventory": inventory, "metrics": metrics, "errors": errors, "persisted": persisted, "window": {"start": start.isoformat(), "end": now.isoformat()}, "compartments": compartments, "regions": configured_regions(connection)}
