"""Provider registry for infrastructure metrics.

The registry exposes declared capabilities only. A provider is not considered
collectable until an adapter is registered by a later implementation task.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

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
    provider: str
    metrics: tuple[MetricDescriptor, ...]
    adapter: MetricsAdapter | None = None

    @property
    def collectable(self) -> bool:
        return self.adapter is not None


_PROVIDERS: dict[str, ProviderRegistration] = {
    "oci": ProviderRegistration(provider="oci", metrics=OCI_METRICS),
}


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
    )
