"""Provider registry for infrastructure metrics.

The registry exposes declared capabilities only. A provider is not considered
collectable until an adapter is registered by a later implementation task.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .catalog import OCI_METRICS
from .contracts import MetricDescriptor, MetricScope, QueryPlan, QueryResult


class MetricsAdapter(Protocol):
    provider: str

    def discover(self, scope: MetricScope) -> tuple[str, ...]: ...

    def plan_queries(self, scope: MetricScope, descriptors: tuple[MetricDescriptor, ...],
                     start_utc, end_utc, resolution_seconds: int) -> tuple[QueryPlan, ...]: ...

    def fetch(self, query: QueryPlan) -> QueryResult: ...


@dataclass(frozen=True)
class ProviderRegistration:
    """Everything a cloud plugs in; the Silver, profiles and engine are shared.

    - ``metrics``: native names mapped to canonical metrics/units;
    - ``config_parser``: connection -> allowlisted scope configuration;
    - ``rightsizing``: capacity reader, sizing catalog and price books.
    """
    provider: str
    metrics: tuple[MetricDescriptor, ...]
    adapter: MetricsAdapter | None = None
    config_parser: Any = None
    rightsizing: Any = None
    # Connector types that land usage metrics for this provider.
    metric_source_types: tuple[str, ...] = ()

    @property
    def collectable(self) -> bool:
        return self.adapter is not None


def _oci_config(connection):
    if str(getattr(connection, "connector_type", "")) == "oci_monitoring":
        from app.services.oci_monitoring_extractor import monitoring_scope

        return monitoring_scope(connection)
    from .config import config_from_connection

    return config_from_connection(connection)


def _oci_rightsizing():
    from .providers.oci_rightsizing import OCI_RIGHTSIZING

    return OCI_RIGHTSIZING


_PROVIDERS: dict[str, ProviderRegistration] = {
    "oci": ProviderRegistration(
        provider="oci", metrics=OCI_METRICS, config_parser=_oci_config, rightsizing=_oci_rightsizing,
        metric_source_types=("oci_monitoring",),
    ),
}


def provider_for_connection(connection) -> ProviderRegistration:
    """Provider of a source: its explicit cloud scope, else the connector family."""
    scope = getattr(connection, "cloud_scope", None) or str(getattr(connection, "connector_type", "") or "").split("_")[0]
    return get_provider(str(scope))


def rightsizing_for(provider: str):
    registration = get_provider(provider)
    if registration.rightsizing is None:
        raise LookupError(f"Rightsizing is not configured for provider: {provider}")
    return registration.rightsizing()


def get_provider(provider: str) -> ProviderRegistration:
    try:
        return _PROVIDERS[provider.lower()]
    except KeyError as exc:
        raise LookupError(f"Unsupported metrics provider: {provider}") from exc


def register_adapter(adapter: MetricsAdapter) -> None:
    """Attach an adapter to a declared provider without replacing its catalog."""
    key = adapter.provider.lower()
    if key not in _PROVIDERS:
        raise LookupError(f"Provider has no metric catalog: {adapter.provider}")
    current = _PROVIDERS[key]
    if current.adapter is not None:
        raise ValueError(f"Metrics adapter already registered: {key}")
    _PROVIDERS[key] = ProviderRegistration(
        provider=current.provider, metrics=current.metrics, adapter=adapter,
        config_parser=current.config_parser, rightsizing=current.rightsizing,
        metric_source_types=current.metric_source_types,
    )


def metric_source_types() -> tuple[str, ...]:
    return tuple(t for registration in _PROVIDERS.values() for t in registration.metric_source_types)
