"""Read-only OCI Compute capacity snapshot for allowlisted instances."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from .config import InfrastructureMetricsConfig


def _snapshot(instance: Any) -> dict:
    shape_config = getattr(instance, "shape_config", None)
    return {
        "resource_id": instance.id,
        "display_name": instance.display_name,
        "shape": instance.shape,
        "lifecycle_state": instance.lifecycle_state,
        "ocpus": float(shape_config.ocpus) if shape_config and shape_config.ocpus is not None else None,
        "memory_gb": float(shape_config.memory_in_gbs) if shape_config and shape_config.memory_in_gbs is not None else None,
        "vcpus": int(shape_config.vcpus) if shape_config and shape_config.vcpus is not None else None,
        "processor_description": getattr(shape_config, "processor_description", None) if shape_config else None,
        "baseline_ocpu_utilization": getattr(shape_config, "baseline_ocpu_utilization", None) if shape_config else None,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "oci.core.ComputeClient.get_instance",
    }


def read_oci_capacity(
    connection: Any, config: InfrastructureMetricsConfig, *,
    client_factory: Callable[[dict], Any] | None = None,
) -> dict[str, dict]:
    """Return one capacity snapshot per allowlisted instance.

    Failures stay per resource (`error`) so a missing capacity becomes the
    MISSING_CAPACITY readiness state instead of an invented size.
    """
    if not config.region:
        raise ValueError("OCI region is required to read capacity")
    from app.services.oci_extractor import OciExtractor

    oci_config = {**OciExtractor(connection)._get_oci_config(), "region": config.region}
    if client_factory is None:
        import oci

        client_factory = lambda values: oci.core.ComputeClient(
            values, retry_strategy=oci.retry.NoneRetryStrategy(), timeout=(5, 30),
        )
    client = client_factory(oci_config)
    snapshots: dict[str, dict] = {}
    for resource_id in config.resource_ids:
        try:
            snapshots[resource_id] = _snapshot(client.get_instance(resource_id).data)
        except Exception as exc:  # noqa: BLE001 - recorded as missing capacity
            snapshots[resource_id] = {"resource_id": resource_id, "error": type(exc).__name__}
    return snapshots
