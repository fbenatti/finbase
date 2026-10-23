"""Declarative metric catalog established by the bounded OCI pilot."""
from __future__ import annotations

from .contracts import MetricDescriptor, MetricKind


_OCI_COMPUTEAGENT = "oci_computeagent"
_OCI_STATS = ("mean", "min", "max", "count", "sum")
_OCI_PERCENTILES = (90, 95, 99)


OCI_METRICS: tuple[MetricDescriptor, ...] = (
    MetricDescriptor(
        provider="oci", namespace=_OCI_COMPUTEAGENT, name="CpuUtilization",
        unit="percent", kind=MetricKind.PERCENTAGE, dimensions=("resourceId",),
        canonical_metric="cpu.utilization", canonical_unit="percent",
        source_sample_period_seconds=10, supported_resolutions_seconds=(60, 300, 900),
        native_statistics=_OCI_STATS, native_percentiles=_OCI_PERCENTILES,
        semantics="CPU utilization percentage reported by OCI Compute agent.",
        evidence="OCI infrastructure metric pilot: 1m, 5m, 15m; mean/min/max/count/sum and p90/p95/p99 queries.",
    ),
    MetricDescriptor(
        provider="oci", namespace=_OCI_COMPUTEAGENT, name="MemoryUtilization",
        unit="percent", kind=MetricKind.PERCENTAGE, dimensions=("resourceId",),
        canonical_metric="memory.utilization", canonical_unit="percent",
        source_sample_period_seconds=10, supported_resolutions_seconds=(60, 300, 900),
        native_statistics=_OCI_STATS, native_percentiles=_OCI_PERCENTILES,
        semantics="Memory utilization percentage; emitted only where agent telemetry is available.",
        evidence="OCI infrastructure metric pilot query; per-resource emission must be checked before collection.",
    ),
    MetricDescriptor(
        provider="oci", namespace=_OCI_COMPUTEAGENT, name="DiskIopsRead",
        unit="operations", kind=MetricKind.COUNTER, dimensions=("resourceId",),
        canonical_metric="disk.read_operations_counter", canonical_unit="operations",
        source_sample_period_seconds=10, supported_resolutions_seconds=(60, 300, 900),
        native_statistics=_OCI_STATS, native_percentiles=_OCI_PERCENTILES,
        semantics="Cumulative read I/O operation counter; derive a rate only from deltas and mark counter resets.",
        evidence="OCI infrastructure metric pilot query; verify metric emission and unit metadata per resource.",
    ),
    MetricDescriptor(
        provider="oci", namespace=_OCI_COMPUTEAGENT, name="DiskIopsWritten",
        unit="operations", kind=MetricKind.COUNTER, dimensions=("resourceId",),
        canonical_metric="disk.write_operations_counter", canonical_unit="operations",
        source_sample_period_seconds=10, supported_resolutions_seconds=(60, 300, 900),
        native_statistics=_OCI_STATS, native_percentiles=_OCI_PERCENTILES,
        semantics="Cumulative write I/O operation counter; derive a rate only from deltas and mark counter resets.",
        evidence="OCI infrastructure metric pilot query; verify metric emission and unit metadata per resource.",
    ),
    MetricDescriptor(
        provider="oci", namespace=_OCI_COMPUTEAGENT, name="DiskBytesRead",
        unit="bytes", kind=MetricKind.COUNTER, dimensions=("resourceId",),
        canonical_metric="disk.read_bytes_counter", canonical_unit="bytes",
        source_sample_period_seconds=10, supported_resolutions_seconds=(60, 300, 900),
        native_statistics=_OCI_STATS, native_percentiles=_OCI_PERCENTILES,
        semantics="Cumulative disk read bytes; derive a throughput rate only from deltas and mark counter resets.",
        evidence="OCI infrastructure metric pilot query; verify metric emission and unit metadata per resource.",
    ),
    MetricDescriptor(
        provider="oci", namespace=_OCI_COMPUTEAGENT, name="DiskBytesWritten",
        unit="bytes", kind=MetricKind.COUNTER, dimensions=("resourceId",),
        canonical_metric="disk.write_bytes_counter", canonical_unit="bytes",
        source_sample_period_seconds=10, supported_resolutions_seconds=(60, 300, 900),
        native_statistics=_OCI_STATS, native_percentiles=_OCI_PERCENTILES,
        semantics="Cumulative disk write bytes; derive a throughput rate only from deltas and mark counter resets.",
        evidence="OCI infrastructure metric pilot query; verify metric emission and unit metadata per resource.",
    ),
    MetricDescriptor(
        provider="oci", namespace=_OCI_COMPUTEAGENT, name="NetworksBytesIn",
        unit="bytes", kind=MetricKind.COUNTER, dimensions=("resourceId",),
        canonical_metric="network.in_bytes_counter", canonical_unit="bytes",
        source_sample_period_seconds=10, supported_resolutions_seconds=(60, 300, 900),
        native_statistics=_OCI_STATS, native_percentiles=_OCI_PERCENTILES,
        semantics="Cumulative network bytes received; derive a rate only from deltas and mark counter resets.",
        evidence="OCI infrastructure metric pilot query; verify metric emission and unit metadata per resource.",
    ),
    MetricDescriptor(
        provider="oci", namespace=_OCI_COMPUTEAGENT, name="NetworksBytesOut",
        unit="bytes", kind=MetricKind.COUNTER, dimensions=("resourceId",),
        canonical_metric="network.out_bytes_counter", canonical_unit="bytes",
        source_sample_period_seconds=10, supported_resolutions_seconds=(60, 300, 900),
        native_statistics=_OCI_STATS, native_percentiles=_OCI_PERCENTILES,
        semantics="Cumulative network bytes sent; derive a rate only from deltas and mark counter resets.",
        evidence="OCI infrastructure metric pilot query; verify metric emission and unit metadata per resource.",
    ),
)


METRIC_CATALOG: dict[tuple[str, str, str], MetricDescriptor] = {
    (metric.provider, metric.namespace, metric.name): metric for metric in OCI_METRICS
}


def get_metric(provider: str, namespace: str, name: str) -> MetricDescriptor:
    """Return a known descriptor or fail closed for unknown metric semantics."""
    try:
        return METRIC_CATALOG[(provider.lower(), namespace, name)]
    except KeyError as exc:
        raise LookupError(f"Unknown metric descriptor: {provider}/{namespace}/{name}") from exc
