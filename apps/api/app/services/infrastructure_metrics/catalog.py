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


def _oci(namespace: str, name: str, unit: str, kind: MetricKind, canonical: str, canonical_unit: str,
         semantics: str, statistics: tuple[str, ...] = _OCI_STATS) -> MetricDescriptor:
    return MetricDescriptor(
        provider="oci", namespace=namespace, name=name, unit=unit, kind=kind, dimensions=("resourceId",),
        canonical_metric=canonical, canonical_unit=canonical_unit, source_sample_period_seconds=60,
        supported_resolutions_seconds=(60, 300, 900), native_statistics=statistics,
        native_percentiles=_OCI_PERCENTILES if statistics == _OCI_STATS else (), semantics=semantics,
        evidence="ListMetrics of the tenancy compartments (23/10/2026).",
    )


_G, _C, _P = MetricKind.GAUGE, MetricKind.COUNTER, MetricKind.PERCENTAGE
_STATUS = ("max",)

# Metrics beyond the agent pilot: hypervisor-side CPU and volume I/O (present
# without the Compute agent), instance health, VNIC traffic and block volumes.
OCI_EXTENDED_METRICS: tuple[MetricDescriptor, ...] = (
    _oci("oci_computeagent", "LoadAverage", "load", _G, "load.average", "processes",
         "Average number of runnable processes (agent)."),
    _oci("oci_computeagent", "MemoryAllocationStalls", "count", _C, "memory.allocation_stalls", "stalls",
         "Times the kernel stalled allocating memory (agent): memory pressure."),
    _oci("oci_vmi_resource_utilization", "CpuUtilization", "percent", _P, "cpu.utilization.hypervisor", "percent",
         "CPU utilization measured by the hypervisor; available without the Compute agent."),
    _oci("oci_compute", "VolumeReadOps", "operations", _C, "instance.volume.read_operations", "operations",
         "Read operations on the instance's attached volumes, seen by the hypervisor."),
    _oci("oci_compute", "VolumeWriteOps", "operations", _C, "instance.volume.write_operations", "operations",
         "Write operations on the instance's attached volumes, seen by the hypervisor."),
    _oci("oci_compute", "VolumeReadThroughput", "bytes", _C, "instance.volume.read_bytes", "bytes",
         "Bytes read from the instance's attached volumes, seen by the hypervisor."),
    _oci("oci_compute", "VolumeWriteThroughput", "bytes", _C, "instance.volume.write_bytes", "bytes",
         "Bytes written to the instance's attached volumes, seen by the hypervisor."),
    _oci("oci_compute_instance_health", "InstanceAccessibilityStatus", "status", _G,
         "instance.accessibility_status", "status", "0 = accessible, 1 = not accessible.", _STATUS),
    _oci("oci_compute_instance_health", "InstanceFileSystemStatus", "status", _G,
         "instance.filesystem_status", "status", "0 = healthy file system, 1 = read-only/unhealthy.", _STATUS),
    _oci("oci_compute_infrastructure_health", "instance_status", "status", _G,
         "instance.infrastructure_status", "status", "0 = running normally, 1 = infrastructure problem.", _STATUS),
    _oci("oci_compute_infrastructure_health", "maintenance_status", "status", _G,
         "instance.maintenance_status", "status", "Scheduled infrastructure maintenance flag.", _STATUS),
    _oci("oci_vcn", "VnicFromNetworkBytes", "bytes", _C, "vnic.in_bytes", "bytes",
         "Bytes received by the VNIC from the network.", ("mean", "max", "sum")),
    _oci("oci_vcn", "VnicToNetworkBytes", "bytes", _C, "vnic.out_bytes", "bytes",
         "Bytes sent by the VNIC to the network.", ("mean", "max", "sum")),
    _oci("oci_vcn", "VnicIngressDropsThrottle", "packets", _C, "vnic.ingress_drops_throttle", "packets",
         "Inbound packets dropped because the VNIC bandwidth limit was reached.", ("max", "sum")),
    _oci("oci_vcn", "VnicEgressDropsThrottle", "packets", _C, "vnic.egress_drops_throttle", "packets",
         "Outbound packets dropped because the VNIC bandwidth limit was reached.", ("max", "sum")),
    _oci("oci_vcn", "VnicConntrackUtilPercent", "percent", _P, "vnic.conntrack_utilization", "percent",
         "Share of the VNIC connection-tracking table in use.", ("mean", "max")),
    _oci("oci_blockstore", "VolumeReadOps", "operations", _C, "volume.read_operations", "operations",
         "Read operations on the block volume.", ("mean", "max", "sum")),
    _oci("oci_blockstore", "VolumeWriteOps", "operations", _C, "volume.write_operations", "operations",
         "Write operations on the block volume.", ("mean", "max", "sum")),
    _oci("oci_blockstore", "VolumeReadThroughput", "bytes", _C, "volume.read_bytes", "bytes",
         "Bytes read from the block volume.", ("mean", "max", "sum")),
    _oci("oci_blockstore", "VolumeWriteThroughput", "bytes", _C, "volume.write_bytes", "bytes",
         "Bytes written to the block volume.", ("mean", "max", "sum")),
    _oci("oci_blockstore", "VolumeThrottledIOs", "operations", _C, "volume.throttled_ios", "operations",
         "I/Os throttled because the volume performance limit was reached.", ("max", "sum")),
)

# Namespace that decides the resource kind of a series in the generic Silver.
OCI_RESOURCE_TYPE_BY_NAMESPACE = {
    "oci_computeagent": "compute.instance",
    "oci_vmi_resource_utilization": "compute.instance",
    "oci_compute": "compute.instance",
    "oci_compute_instance_health": "compute.instance",
    "oci_compute_infrastructure_health": "compute.instance",
    "oci_vcn": "network.vnic",
    "oci_blockstore": "storage.volume",
}

ALL_OCI_METRICS: tuple[MetricDescriptor, ...] = OCI_METRICS + OCI_EXTENDED_METRICS


METRIC_CATALOG: dict[tuple[str, str, str], MetricDescriptor] = {
    (metric.provider, metric.namespace, metric.name): metric for metric in ALL_OCI_METRICS
}


def get_metric(provider: str, namespace: str, name: str) -> MetricDescriptor:
    """Return a known descriptor or fail closed for unknown metric semantics."""
    try:
        return METRIC_CATALOG[(provider.lower(), namespace, name)]
    except KeyError as exc:
        raise LookupError(f"Unknown metric descriptor: {provider}/{namespace}/{name}") from exc
