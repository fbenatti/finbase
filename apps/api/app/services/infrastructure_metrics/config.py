"""Fail-closed pilot configuration read from an OCI SourceConnection."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .catalog import OCI_METRICS
from .contracts import MetricScope


@dataclass(frozen=True)
class InfrastructureMetricsConfig:
    enabled: bool
    compartment_id: str | None
    provider_account_id: str | None
    region: str | None
    resource_ids: tuple[str, ...]
    metric_names: tuple[str, ...]
    resolutions_seconds: tuple[int, ...]
    retention_days: int = 60
    schedule_enabled: bool = False

    def scope(self, connection_id: int) -> MetricScope:
        if not self.enabled:
            raise PermissionError("infrastructure metric collection is disabled")
        if not self.provider_account_id or not self.region:
            raise ValueError("provider_account_id and region must be configured")
        return MetricScope(
            provider="oci", connection_id=connection_id,
            account_id=self.provider_account_id, region=self.region,
            resource_ids=self.resource_ids, enabled=True, max_resources=2,
        )


def config_from_connection(connection: Any) -> InfrastructureMetricsConfig:
    """Read only the `infrastructure_metrics` block; never return credentials."""
    if str(getattr(connection, "connector_type", "")).lower() != "oci":
        raise ValueError("infrastructure metric pilot currently requires an OCI connection")
    root = getattr(connection, "config_json", None) or {}
    raw = root.get("infrastructure_metrics") or {}
    if not isinstance(raw, dict):
        raise ValueError("infrastructure_metrics configuration must be an object")

    enabled = raw.get("enabled") is True
    account_id = raw.get("provider_account_id") or root.get("tenancy_ocid") or root.get("tenancy")
    region = raw.get("region") or root.get("region")
    resource_ids = tuple(str(item).strip() for item in raw.get("resource_ids", ()) if str(item).strip())
    metric_names = tuple(raw.get("metric_names") or [metric.name for metric in OCI_METRICS])
    allowed_metrics = {metric.name for metric in OCI_METRICS}
    unknown_metrics = set(metric_names) - allowed_metrics
    if unknown_metrics:
        raise ValueError(f"unknown OCI infrastructure metrics: {', '.join(sorted(unknown_metrics))}")

    resolutions = tuple(int(value) for value in raw.get("resolutions_seconds", (60,)))
    if not resolutions or set(resolutions) - {60, 300, 900}:
        raise ValueError("resolutions_seconds must contain only 60, 300, or 900")
    if len(set(resolutions)) != len(resolutions):
        raise ValueError("resolutions_seconds contains duplicates")
    retention_days = int(raw.get("retention_days", 60))
    if retention_days != 60:
        raise ValueError("infrastructure metrics retention is fixed at 60 days")
    if raw.get("schedule_enabled") is True:
        raise ValueError("scheduled collection remains disabled during the pilot")
    if enabled:
        if not raw.get("compartment_id"):
            raise ValueError("compartment_id is required when collection is enabled")
        if not resource_ids:
            raise ValueError("resource_ids allowlist is required when collection is enabled")
        if len(resource_ids) > 2 or len(set(resource_ids)) != len(resource_ids):
            raise ValueError("pilot allowlist must contain at most two unique resources")
        if not account_id or not region:
            raise ValueError("provider account and region are required when collection is enabled")

    return InfrastructureMetricsConfig(
        enabled=enabled,
        compartment_id=str(raw["compartment_id"]) if raw.get("compartment_id") else None,
        provider_account_id=str(account_id) if account_id else None,
        region=str(region) if region else None,
        resource_ids=resource_ids,
        metric_names=metric_names,
        resolutions_seconds=resolutions,
        retention_days=retention_days,
        schedule_enabled=False,
    )
