"""Provider registry for usage metrics and rightsizing.

A cloud plugs in by declaring its metric catalog (native names → canonical
metrics/units, used by the catalog mappings), the connector types that land
its usage metrics, how to read a source's scope and its rightsizing provider
(capacity, sizing catalog, price books). The Silver and the engine are shared.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .catalog import OCI_METRICS
from .contracts import MetricDescriptor


@dataclass(frozen=True)
class ProviderRegistration:
    provider: str
    metrics: tuple[MetricDescriptor, ...]
    config_parser: Any = None
    rightsizing: Any = None
    # Connector types that land usage metrics for this provider.
    metric_source_types: tuple[str, ...] = ()


def _oci_monitoring_scope(connection):
    from app.services.oci_monitoring_extractor import monitoring_scope

    return monitoring_scope(connection)


def _oci_rightsizing():
    from .providers.oci_rightsizing import OCI_RIGHTSIZING

    return OCI_RIGHTSIZING


_PROVIDERS: dict[str, ProviderRegistration] = {
    "oci": ProviderRegistration(
        provider="oci", metrics=OCI_METRICS, config_parser=_oci_monitoring_scope,
        rightsizing=_oci_rightsizing, metric_source_types=("oci_monitoring",),
    ),
}


def get_provider(provider: str) -> ProviderRegistration:
    try:
        return _PROVIDERS[provider.lower()]
    except KeyError as exc:
        raise LookupError(f"Unsupported metrics provider: {provider}") from exc


def provider_for_connection(connection) -> ProviderRegistration:
    """Provider of a source: its explicit cloud scope, else the connector family."""
    scope = getattr(connection, "cloud_scope", None) or str(getattr(connection, "connector_type", "") or "").split("_")[0]
    return get_provider(str(scope))


def rightsizing_for(provider: str):
    registration = get_provider(provider)
    if registration.rightsizing is None:
        raise LookupError(f"Rightsizing is not configured for provider: {provider}")
    return registration.rightsizing()


def metric_source_types() -> tuple[str, ...]:
    return tuple(t for registration in _PROVIDERS.values() for t in registration.metric_source_types)
