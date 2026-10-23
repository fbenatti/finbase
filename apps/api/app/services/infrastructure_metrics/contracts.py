"""Provider-neutral contracts for infrastructure metric acquisition.

These types deliberately describe the meaning and provenance of a metric. They
do not contain credentials or provider SDK objects, so they can be serialized
for planning and tests without exposing secrets.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping


class CollectionCancelled(RuntimeError):
    """Raised when a queued collection receives a cooperative cancel signal."""


class MetricKind(str, Enum):
    GAUGE = "GAUGE"
    COUNTER = "COUNTER"
    RATE = "RATE"
    PERCENTAGE = "PERCENTAGE"
    DISTRIBUTION = "DISTRIBUTION"


class PercentileBasis(str, Enum):
    NATIVE_BUCKET = "NATIVE_BUCKET"
    WINDOW_OF_BUCKET_VALUES = "WINDOW_OF_BUCKET_VALUES"
    OBSERVATION_DISTRIBUTION = "OBSERVATION_DISTRIBUTION"


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


@dataclass(frozen=True)
class MetricScope:
    """Explicit collection boundary; resource IDs are always allowlisted."""

    provider: str
    connection_id: int
    account_id: str
    region: str
    resource_ids: tuple[str, ...]
    enabled: bool = False
    max_resources: int = 2

    def validate(self) -> None:
        if not self.provider or not self.account_id or not self.region:
            raise ValueError("provider, account_id and region are required")
        if self.connection_id <= 0:
            raise ValueError("connection_id must be positive")
        if not self.resource_ids:
            raise ValueError("resource allowlist cannot be empty")
        if len(set(self.resource_ids)) != len(self.resource_ids):
            raise ValueError("resource allowlist contains duplicates")
        if self.max_resources < 1 or len(self.resource_ids) > self.max_resources:
            raise ValueError("resource allowlist exceeds max_resources")


@dataclass(frozen=True)
class QueryPlan:
    descriptor: MetricDescriptor
    scope: MetricScope
    start_utc: datetime
    end_utc: datetime
    resolution_seconds: int
    statistics: tuple[str, ...]
    page_token: str | None = None
    request_id: str | None = None

    def validate(self) -> None:
        self.scope.validate()
        if self.start_utc.tzinfo is None or self.end_utc.tzinfo is None:
            raise ValueError("query window must use timezone-aware datetimes")
        if self.start_utc >= self.end_utc:
            raise ValueError("query start must precede query end")
        if self.resolution_seconds not in self.descriptor.supported_resolutions_seconds:
            raise ValueError("resolution is not supported by the metric descriptor")


@dataclass(frozen=True)
class MetricPoint:
    timestamp_utc: datetime
    dimensions: Mapping[str, str]
    values: Mapping[str, float | None]
    source_sample_period_seconds: int | None = None


@dataclass(frozen=True)
class QueryResult:
    plan: QueryPlan
    status_code: int
    points: tuple[MetricPoint, ...] = ()
    raw_payloads: tuple[Mapping[str, Any], ...] = ()
    sanitized_request: Mapping[str, Any] = field(default_factory=dict)
    safe_headers: Mapping[str, str] = field(default_factory=dict)
    page_count: int = 1
    attempt_count: int = 1
    latency_ms: int = 0
    response_bytes: int = 0
    stream_count: int = 0
    error_code: str | None = None
