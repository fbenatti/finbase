"""Declarative description of a provider metric and its canonical meaning.

The metric catalog uses these types to map native names (e.g. OCI
``CpuUtilization``) to the canonical metric and unit stored in the generic
usage Silver. They hold no credentials or SDK objects.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class MetricKind(str, Enum):
    GAUGE = "GAUGE"
    COUNTER = "COUNTER"
    RATE = "RATE"
    PERCENTAGE = "PERCENTAGE"
    DISTRIBUTION = "DISTRIBUTION"


@dataclass(frozen=True)
class MetricDescriptor:
    provider: str
    namespace: str
    name: str
    unit: str
    kind: MetricKind
    dimensions: tuple[str, ...]
    canonical_metric: str
    canonical_unit: str
    source_sample_period_seconds: int | None = None
    supported_resolutions_seconds: tuple[int, ...] = ()
    native_statistics: tuple[str, ...] = ()
    native_percentiles: tuple[int, ...] = ()
    scope: str = "resource"
    semantics: str = ""
    verified_at: datetime | None = None
    evidence: str | None = None
