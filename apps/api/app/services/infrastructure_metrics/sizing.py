"""Provider-neutral capacity, sizing and pricing contracts for rightsizing.

The engine only speaks vCPU and GB. Each cloud supplies, through the
registry, how to read the current size, which sizes are reachable from it
and what a size costs. Flexible shapes (OCI Flex, GCP custom) and fixed SKU
families (Azure/AWS instance types) are both expressed with these types.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class CapacitySnapshot:
    provider: str
    resource_id: str
    resource_name: str
    sku: str
    vcpus: float
    memory_gb: float
    family: str | None = None
    lifecycle_state: str | None = None
    observed_at_utc: str | None = None
    source: str | None = None
    # Provider billing units of the current size (e.g. OCI {"ocpu": 5}); the
    # price book reads them, the engine never does.
    attributes: Mapping[str, Any] = field(default_factory=dict)
    # Descriptive facts only (processor, burst baseline…), kept for audit.
    details: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SizeOption:
    sku: str
    vcpus: float
    memory_gb: float
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"sku": self.sku, "vcpus": self.vcpus, "memory_gb": self.memory_gb, **dict(self.attributes)}


class CapacityReader(Protocol):
    def __call__(self, connection: Any, config: Any) -> dict[str, CapacitySnapshot | dict]: ...


class SizingCatalog(Protocol):
    def supports(self, current: CapacitySnapshot) -> bool: ...

    def fit(self, current: CapacitySnapshot, required_vcpus: float, required_memory_gb: float) -> SizeOption: ...


class PriceBook(Protocol):
    currency: str | None
    basis: Mapping[str, Any]

    def hourly(self, option: SizeOption) -> float | None: ...


@dataclass(frozen=True)
class FlexRule:
    """Shape whose vCPU/memory are chosen freely within per-unit bounds."""
    min_units: int
    max_units: int
    vcpus_per_unit: int
    min_gb_per_unit: float
    max_gb_per_unit: float
    max_memory_gb: float
    unit_name: str = "vCPU"


class FlexSizingCatalog:
    """Sizes a flexible shape: whole compute units and whole GB."""

    def __init__(self, rules: Mapping[str, FlexRule]):
        self.rules = dict(rules)

    def supports(self, current: CapacitySnapshot) -> bool:
        return current.sku in self.rules

    def fit(self, current: CapacitySnapshot, required_vcpus: float, required_memory_gb: float) -> SizeOption:
        rule = self.rules[current.sku]
        units = min(max(math.ceil(required_vcpus / rule.vcpus_per_unit - 1e-9), rule.min_units), rule.max_units)
        memory = math.ceil(required_memory_gb - 1e-9)
        if memory > units * rule.max_gb_per_unit:
            units = min(math.ceil(memory / rule.max_gb_per_unit), rule.max_units)
        memory = min(max(memory, units * rule.min_gb_per_unit), rule.max_memory_gb, units * rule.max_gb_per_unit)
        return SizeOption(
            sku=current.sku, vcpus=float(units * rule.vcpus_per_unit), memory_gb=float(memory),
            attributes={rule.unit_name.lower(): float(units)} if rule.unit_name != "vCPU" else {},
        )


class SkuSizingCatalog:
    """Fixed sizes of one family; picks the cheapest size that fits."""

    def __init__(self, families: Mapping[str, list[SizeOption]], price_of=None):
        self.families = {name: list(options) for name, options in families.items()}
        self.price_of = price_of

    def supports(self, current: CapacitySnapshot) -> bool:
        return any(option.sku == current.sku for option in self.families.get(current.family or "", ()))

    def fit(self, current: CapacitySnapshot, required_vcpus: float, required_memory_gb: float) -> SizeOption:
        options = [option for option in self.families[current.family or ""]
                   if option.vcpus >= required_vcpus - 1e-9 and option.memory_gb >= required_memory_gb - 1e-9]
        if not options:
            return max(self.families[current.family or ""], key=lambda option: (option.vcpus, option.memory_gb))

        def key(option: SizeOption):
            price = self.price_of(option) if self.price_of else None
            return (price is None, price or 0.0, option.vcpus, option.memory_gb)

        return min(options, key=key)


@dataclass(frozen=True)
class RightsizingProvider:
    """What a cloud must supply so the generic engine can size its resources."""
    provider: str
    read_capacity: CapacityReader
    sizing_catalog: SizingCatalog
    # resource_ids -> {resource_id: PriceBook}
    price_books: Any
