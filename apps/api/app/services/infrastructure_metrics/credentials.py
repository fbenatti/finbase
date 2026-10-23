"""Resolve an OCI Monitoring client through the existing connection resolver."""
from __future__ import annotations

from typing import Any, Callable

from .config import InfrastructureMetricsConfig
from .providers.oci import OciMetricsAdapter


def build_oci_adapter(
    connection: Any, config: InfrastructureMetricsConfig, *,
    client_factory: Callable[[dict], Any] | None = None,
) -> OciMetricsAdapter:
    """Build a regional client without returning or logging the credential map."""
    if not config.enabled:
        raise PermissionError("infrastructure metric collection is disabled")
    if not config.compartment_id or not config.region or not config.provider_account_id:
        raise ValueError("OCI metric scope is incomplete")

    from app.services.oci_extractor import OciExtractor

    oci_config = OciExtractor(connection)._get_oci_config()
    resolved_account = oci_config.get("tenancy")
    if resolved_account and resolved_account != config.provider_account_id:
        raise ValueError("configured provider account does not match OCI credentials")
    regional_config = {**oci_config, "region": config.region}
    if client_factory is None:
        try:
            import oci
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("OCI SDK is required for infrastructure metrics") from exc
        client_factory = lambda values: oci.monitoring.MonitoringClient(
            values, retry_strategy=oci.retry.NoneRetryStrategy(), timeout=(5, 30),
        )
    monitoring_client = client_factory(regional_config)
    return OciMetricsAdapter(monitoring_client, config.compartment_id)
