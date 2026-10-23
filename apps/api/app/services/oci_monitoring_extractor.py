"""OCI Monitoring source: raw metric datapoints into Bronze, as the API returns them.

The connection (``connector_type = "oci_monitoring"``) is independent from the
OCI cost connection but reuses its credentials by reference
(``credentials_connection_code``), so the private key is never copied.

Bronze keeps the response untouched: one row per aggregated datapoint with
the ``MetricData`` fields (``namespace``, ``name``, ``dimensions``,
``metadata``…) plus the request that produced it (``query``, ``aggregation``,
``request_resolution``, window and region — the response does not echo them). Renaming to generic columns happens only
in the catalog mappings, when the Silver is reprocessed.
"""
from __future__ import annotations

import calendar
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import threading
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.ingested_blob import IngestedBlob
from app.models.skipped_blob import SkippedBlob
from app.models.source_connection import SourceConnection
from app.schemas.schemas import BlobScanItem, BlobScanResult
from app.services.infrastructure_metrics.catalog import OCI_RESOURCE_TYPE_BY_NAMESPACE

logger = logging.getLogger(__name__)

DEFAULT_NAMESPACE = "oci_computeagent"
DEFAULT_METRICS = (
    "CpuUtilization", "MemoryUtilization", "DiskIopsRead", "DiskIopsWritten",
    "DiskBytesRead", "DiskBytesWritten", "NetworksBytesIn", "NetworksBytesOut",
)
DEFAULT_STATISTICS = ("mean", "max", "p95", "p99", "count")
# What a source collects when it does not list ``metrics`` itself:
# (namespace, metric, statistics). Utilization gauges keep the percentiles the
# rightsizing engine and charts read; counters keep totals; status flags only
# need their worst value per bucket.
_TOTALS = ("mean", "max", "sum")
DEFAULT_COLLECTION_PLAN: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    *(("oci_computeagent", name, DEFAULT_STATISTICS) for name in DEFAULT_METRICS),
    ("oci_computeagent", "LoadAverage", DEFAULT_STATISTICS),
    ("oci_computeagent", "MemoryAllocationStalls", ("max", "sum")),
    ("oci_vmi_resource_utilization", "CpuUtilization", DEFAULT_STATISTICS),
    *(("oci_compute", name, _TOTALS) for name in ("VolumeReadOps", "VolumeWriteOps", "VolumeReadThroughput", "VolumeWriteThroughput")),
    ("oci_compute_instance_health", "InstanceAccessibilityStatus", ("max",)),
    ("oci_compute_instance_health", "InstanceFileSystemStatus", ("max",)),
    ("oci_compute_infrastructure_health", "instance_status", ("max",)),
    ("oci_compute_infrastructure_health", "maintenance_status", ("max",)),
    ("oci_vcn", "VnicFromNetworkBytes", _TOTALS),
    ("oci_vcn", "VnicToNetworkBytes", _TOTALS),
    ("oci_vcn", "VnicIngressDropsThrottle", ("max", "sum")),
    ("oci_vcn", "VnicEgressDropsThrottle", ("max", "sum")),
    ("oci_vcn", "VnicConntrackUtilPercent", ("mean", "max")),
    *(("oci_blockstore", name, _TOTALS) for name in ("VolumeReadOps", "VolumeWriteOps", "VolumeReadThroughput", "VolumeWriteThroughput")),
    ("oci_blockstore", "VolumeThrottledIOs", ("max", "sum")),
)
AGGREGATIONS = {
    "mean": "mean()", "min": "min()", "max": "max()", "count": "count()", "sum": "sum()",
    "p90": "percentile(0.90)", "p95": "percentile(0.95)", "p99": "percentile(0.99)",
}
RESOLUTIONS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}
# OCI Monitoring rejects 5m/15m buckets starting 30+ days back (measured
# 23/10/2026: 400 "Invalid time range for given interval"); 1m and 1h go further.
MAX_AGE = {"5m": timedelta(days=30), "15m": timedelta(days=30)}
# Units never cross a month, so each Bronze slice belongs to one billing period.
CHUNK_START_DAYS = (1, 8, 15, 22, 29)
BLOB_ROOT = "oci-monitoring"
# Concurrent Monitoring calls per window (OCI allows ~10/s per tenancy).
DEFAULT_PARALLEL_CALLS = 6


class CollectionCancelled(RuntimeError):
    """Raised inside a window when the extraction task was cancelled."""


def _floor(moment: datetime, seconds: int) -> datetime:
    epoch = int(moment.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, tz=timezone.utc)


def month_chunks(month_start: datetime) -> list[tuple[datetime, datetime]]:
    last_day = calendar.monthrange(month_start.year, month_start.month)[1]
    starts = [day for day in CHUNK_START_DAYS if day <= last_day]
    bounds = []
    for index, day in enumerate(starts):
        start = month_start.replace(day=day)
        end = (month_start.replace(day=starts[index + 1]) if index + 1 < len(starts)
               else month_start.replace(day=last_day) + timedelta(days=1))
        bounds.append((start, end))
    return bounds


def blob_path_for(resolution: str, start: datetime, end: datetime, asset: str | None = None) -> str:
    """Unit path: one window, or one asset in one window (``collection_unit = asset``)."""
    path = f"{BLOB_ROOT}/{resolution}/{start:%Y-%m-%d}_{end:%Y-%m-%d}"
    return f"{path}/{asset}" if asset else path


def parse_blob_path(blob_path: str) -> tuple[str, datetime, datetime]:
    resolution, start, end, _asset = parse_unit(blob_path)
    return resolution, start, end


def parse_unit(blob_path: str) -> tuple[str, datetime, datetime, str | None]:
    parts = blob_path.split("/")
    if len(parts) not in (3, 4) or parts[0] != BLOB_ROOT:
        raise ValueError(f"Unidade OCI Monitoring inválida: {blob_path}")
    start_text, end_text = parts[2].split("_")
    start = datetime.fromisoformat(start_text).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(end_text).replace(tzinfo=timezone.utc)
    return parts[1], start, end, (parts[3] if len(parts) == 4 else None)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str, ensure_ascii=False, sort_keys=True)
    return str(value)


class OciMonitoringExtractor:
    def __init__(self, connection: SourceConnection, credentials_connection: SourceConnection | None = None):
        self.connection = connection
        self.config = dict(connection.config_json or {})
        self._credentials_connection = credentials_connection
        self._client = None

    # ---- configuration -------------------------------------------------
    @property
    def resolution(self) -> str:
        value = str(self.config.get("resolution") or "5m")
        if value not in RESOLUTIONS:
            raise ValueError(f"Resolução OCI Monitoring inválida: {value}")
        return value

    def _list(self, key: str, default: tuple[str, ...] = ()) -> list[str]:
        values = self.config.get(key) or list(default)
        if isinstance(values, str):
            values = values.split(",")
        return [str(item).strip() for item in values if str(item).strip()]

    @property
    def discovers_compartment(self) -> bool:
        """Instances are discovered, no allowlist: ``discovery`` = ``compartment`` or ``tenancy``.

        ``compartment`` lists one compartment; ``tenancy`` walks every active
        compartment of the tenancy (the root comes from the credentials).
        """
        return str(self.config.get("discovery") or "") in ("compartment", "tenancy")

    @property
    def discovers_tenancy(self) -> bool:
        return str(self.config.get("discovery") or "") == "tenancy"

    def compartment_ids(self) -> list[str]:
        """Compartments to collect: the configured one, or the whole tenancy tree."""
        if getattr(self, "_compartments", None) is None:
            if not self.discovers_tenancy:
                self._compartments = [str(self.config["compartment_id"])]
            else:
                import oci

                root = str(self.oci_config()["tenancy"])
                identity = oci.identity.IdentityClient(self.oci_config(), timeout=(5, 60))
                # Walk the tree level by level: the subtree flag only works from the root,
                # and this keeps working when a child compartment is configured later.
                found, pending = [root], [root]
                while pending:
                    parent = pending.pop()
                    children = oci.pagination.list_call_get_all_results(
                        identity.list_compartments, parent, lifecycle_state="ACTIVE",
                    ).data
                    for child in children:
                        found.append(child.id)
                        pending.append(child.id)
                self._compartments = found
        return self._compartments

    @property
    def resource_ids(self) -> list[str]:
        if self.discovers_compartment:
            return [instance["resource_id"] for instance in self.instances()]
        return self._list("resource_ids")

    def instances(self) -> list[dict]:
        """Non-terminated instances of the discovered compartments (paginated ListInstances each)."""
        if getattr(self, "_instances", None) is None:
            import oci

            compute = oci.core.ComputeClient(self.oci_config(), timeout=(5, 60))
            self._instances = []
            for compartment_id in self.compartment_ids():
                found = oci.pagination.list_call_get_all_results(compute.list_instances, compartment_id).data
                self._instances.extend(
                    {"resource_id": item.id, "instance": item, "compartment_id": compartment_id}
                    for item in found if item.lifecycle_state not in ("TERMINATED", "TERMINATING")
                )
        return self._instances

    @property
    def history_days(self) -> int:
        return max(1, min(int(self.config.get("history_days") or 30), 60))

    def credentials_connection(self) -> SourceConnection:
        if self._credentials_connection is not None:
            return self._credentials_connection
        code = self.config.get("credentials_connection_code")
        if not code:
            self._credentials_connection = self.connection
            return self.connection
        from app.database import SyncSession

        session = SyncSession()
        try:
            found = session.execute(
                select(SourceConnection).where(SourceConnection.code == code)
            ).scalar_one_or_none()
        finally:
            session.close()
        if found is None:
            raise ValueError(f"Conexão de credenciais OCI '{code}' não encontrada")
        self._credentials_connection = found
        return found

    def oci_config(self) -> dict:
        from app.services.oci_extractor import OciExtractor

        credentials = self.credentials_connection()
        config = OciExtractor(credentials)._get_oci_config()
        region = self.config.get("region") or (credentials.config_json or {}).get("region")
        return {**config, "region": region} if region else config

    def asset_resources(self, instance_id: str) -> tuple[dict[str, list[str]], list[str]]:
        """The instance and its attached VNICs and volumes (boot included), by resource type."""
        import oci

        entry = next((item for item in self.instances() if item["resource_id"] == instance_id), None)
        resources: dict[str, list[str]] = {"compute.instance": [instance_id], "network.vnic": [], "storage.volume": []}
        errors: list[str] = []
        if entry is None:
            return resources, [f"{instance_id}: instância não encontrada na descoberta"]
        instance, compartment = entry["instance"], entry["compartment_id"]
        compute = oci.core.ComputeClient(self.oci_config(), retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY, timeout=(5, 60))
        lookups = (
            ("network.vnic", lambda: compute.list_vnic_attachments(compartment, instance_id=instance_id), "vnic_id"),
            ("storage.volume", lambda: compute.list_volume_attachments(compartment, instance_id=instance_id), "volume_id"),
            ("storage.volume", lambda: compute.list_boot_volume_attachments(
                instance.availability_domain, compartment, instance_id=instance_id), "boot_volume_id"),
        )
        for kind, lookup, field in lookups:
            try:
                # An instance has a handful of attachments: one page holds them.
                for attachment in lookup().data:
                    if getattr(attachment, "lifecycle_state", "ATTACHED") == "ATTACHED" and getattr(attachment, field, None):
                        resources[kind].append(getattr(attachment, field))
            except oci.exceptions.ServiceError as exc:
                errors.append(f"{instance_id}: anexos {field} {exc.status} {exc.code}")
        return resources, errors

    def _new_client(self):
        import oci

        # Root queries over the whole tenancy answer in 30-50 s; 429s back off.
        return oci.monitoring.MonitoringClient(
            self.oci_config(), retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY, timeout=(5, 180),
        )

    def client(self):
        if self._client is None:
            self._client = self._new_client()
        return self._client

    def _validate(self) -> None:
        if not self.config.get("compartment_id") and not self.discovers_tenancy:
            raise ValueError("Informe o compartment_id da fonte OCI Monitoring")
        if not self.discovers_compartment and not self.resource_ids:
            raise ValueError("Informe resource_ids (allowlist) ou ative a descoberta do compartimento")

    # ---- units ---------------------------------------------------------
    def windows(self, now: datetime | None = None) -> list[tuple[datetime, datetime, datetime, datetime]]:
        """(nominal start, nominal end, effective start, effective end) per unit."""
        now = now or datetime.now(timezone.utc)
        seconds = RESOLUTIONS[self.resolution]
        collect_until = _floor(now, seconds)
        oldest = _floor(now - timedelta(days=self.history_days), 86400)
        if self.resolution in MAX_AGE:
            oldest = max(oldest, _floor(now - MAX_AGE[self.resolution] + timedelta(hours=1), seconds))
        month = oldest.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        units = []
        while month < collect_until:
            for start, end in month_chunks(month):
                effective_start, effective_end = max(start, oldest), min(end, collect_until)
                if effective_start < effective_end:
                    units.append((start, end, effective_start, effective_end))
            month = (month + timedelta(days=32)).replace(day=1)
        return units

    @property
    def per_asset(self) -> bool:
        """``collection_unit = asset``: one unit per instance and window, collected in sequence."""
        return str(self.config.get("collection_unit") or "") == "asset"

    def _unit_paths(self, start: datetime, end: datetime) -> list[str]:
        if not self.per_asset:
            return [blob_path_for(self.resolution, start, end)]
        return [blob_path_for(self.resolution, start, end, asset) for asset in self.resource_ids]

    def _build_scan_items(self, existing: dict[str, IngestedBlob], skipped: set[str]) -> list[BlobScanItem]:
        items = []
        windows = self.windows()
        # Per asset the order is asset by asset (all its windows), so each VM
        # is complete in Bronze before the next one starts.
        units = [
            (path, start, effective_end)
            for start, end, _effective_start, effective_end in windows
            for path in self._unit_paths(start, end)
        ]
        if self.per_asset:
            order = {asset: index for index, asset in enumerate(self.resource_ids)}
            units.sort(key=lambda unit: (order.get(parse_unit(unit[0])[3], 0), unit[1]))
        for blob_path, start, effective_end in units:
            if blob_path in skipped:
                continue
            previous = existing.get(blob_path)
            if previous is None:
                status = "NEW"
            elif previous.last_modified and effective_end > previous.last_modified.replace(tzinfo=timezone.utc):
                status = "CHANGED"
            else:
                status = "UNCHANGED"
            items.append(BlobScanItem(
                blob_path=blob_path, blob_prefix=f"{BLOB_ROOT}/{self.resolution}",
                last_modified=effective_end, content_length=None, status=status,
                billing_period=start.strftime("%Y%m"),
            ))
        return items

    def _scan_sync(self, db_session: Session) -> list[BlobScanItem]:
        self._validate()
        existing = {b.blob_path: b for b in db_session.execute(
            select(IngestedBlob).where(IngestedBlob.connection_id == self.connection.id)
        ).scalars().all()}
        skipped = {row[0] for row in db_session.execute(
            select(SkippedBlob.blob_path).where(SkippedBlob.connection_id == self.connection.id)
        )}
        return self._build_scan_items(existing, skipped)

    async def scan(self, db: AsyncSession) -> BlobScanResult:
        self._validate()
        existing = {b.blob_path: b for b in (await db.execute(
            select(IngestedBlob).where(IngestedBlob.connection_id == self.connection.id)
        )).scalars().all()}
        skipped = {row[0] for row in await db.execute(
            select(SkippedBlob.blob_path).where(SkippedBlob.connection_id == self.connection.id)
        )}
        items = self._build_scan_items(existing, skipped)
        return BlobScanResult(
            connection_id=self.connection.id, total=len(items),
            new=sum(item.status == "NEW" for item in items),
            changed=sum(item.status == "CHANGED" for item in items),
            unchanged=sum(item.status == "UNCHANGED" for item in items),
            items=items,
        )

    # ---- acquisition ---------------------------------------------------
    def collection_plan(self) -> list[tuple[str, str, tuple[str, ...]]]:
        """(namespace, metric, statistics) to collect.

        ``metrics`` lists "namespace/Metric" entries; the legacy
        ``namespace`` + ``metric_names`` pair still works; without either, the
        default plan covers agent, hypervisor, health, VNIC and volume metrics.
        """
        statistics = tuple(self._list("statistics", DEFAULT_STATISTICS))
        configured = self._list("metrics")
        if configured:
            plan = []
            for entry in configured:
                namespace, _, name = entry.partition("/")
                default = next((stats for ns, metric, stats in DEFAULT_COLLECTION_PLAN if ns == namespace and metric == name), statistics)
                plan.append((namespace, name, default))
            return plan
        if self.config.get("metric_names"):
            namespace = str(self.config.get("namespace") or DEFAULT_NAMESPACE)
            return [(namespace, name, statistics) for name in self._list("metric_names")]
        return list(DEFAULT_COLLECTION_PLAN)

    def fetch_window(self, blob_path: str, now: datetime | None = None, should_abort=None,
                     progress=None) -> tuple[pd.DataFrame, datetime, list[str]]:
        """Raw datapoints of one unit, plus the effective end and per-call errors."""
        import oci

        self._validate()
        resolution, start, end = parse_blob_path(blob_path)
        match = next((unit for unit in self.windows(now) if unit[0] == start and unit[1] == end), None)
        if match is None:
            raise ValueError(f"Unidade fora da janela coletável: {blob_path}")
        _start, _end, effective_start, effective_end = match
        region = self.oci_config().get("region")
        rows: list[dict[str, Any]] = []
        errors: list[str] = []
        # Tenancy discovery asks once from the root covering every subcompartment;
        # compartment discovery once per compartment; an allowlist per resource.
        # Each answer has one stream per resource.
        asset = parse_unit(blob_path)[3]
        if asset:
            # One asset: every namespace filtered by the asset's own resources,
            # asked from the discovery root so attachments in other
            # compartments are found too.
            root = str(self.oci_config()["tenancy"]) if self.discovers_tenancy else str(self.config["compartment_id"])
            resources, attachment_errors = self.asset_resources(asset)
            errors.extend(attachment_errors)
            calls = [
                (namespace, metric, statistic, root, resource_id, True)
                for namespace, metric, statistics in self.collection_plan()
                for resource_id in resources.get(OCI_RESOURCE_TYPE_BY_NAMESPACE.get(namespace, "compute.instance"), [])
                for statistic in statistics
            ]
        else:
            if self.discovers_tenancy:
                targets = [(str(self.oci_config()["tenancy"]), None, True)]
            elif self.discovers_compartment:
                targets = [(compartment_id, None, False) for compartment_id in
                           sorted({item["compartment_id"] for item in self.instances()})]
            else:
                targets = [(str(self.config["compartment_id"]), resource_id, False) for resource_id in self.resource_ids]
            calls = [
                (namespace, metric, statistic, compartment_id, resource_id, subtree)
                for namespace, metric, statistics in self.collection_plan()
                for compartment_id, resource_id, subtree in targets
                for statistic in statistics
            ]
        local = threading.local()

        def call(spec):
            namespace, metric, statistic, compartment_id, resource_id, subtree = spec
            if should_abort and should_abort():
                raise CollectionCancelled("Coleta cancelada")
            if getattr(local, "client", None) is None:
                # SDK clients are not shared across threads.
                local.client = self._new_client()
            aggregation = AGGREGATIONS[statistic]
            selector = f'{{resourceId = "{resource_id}"}}' if resource_id else ""
            query = f'{metric}[{resolution}]{selector}.{aggregation}'
            details = oci.monitoring.models.SummarizeMetricsDataDetails(
                namespace=namespace, query=query, start_time=effective_start,
                end_time=effective_end, resolution=resolution,
            )
            try:
                response = local.client.summarize_metrics_data(
                    compartment_id, details, compartment_id_in_subtree=subtree,
                )
            except oci.exceptions.ServiceError as exc:
                return [], f"{namespace}/{metric}/{statistic}: {exc.status} {exc.code}"
            request_id = (getattr(response, "headers", {}) or {}).get("opc-request-id")
            found = []
            for stream in oci.util.to_dict(response.data) or []:
                datapoints = stream.pop("aggregated_datapoints", None) or []
                for point in datapoints:
                    row = {key: _text(value) for key, value in stream.items()}
                    row.update({key: _text(value) for key, value in point.items()})
                    row.update({
                        # What was asked: the response repeats names across
                        # namespaces (CpuUtilization exists in two).
                        "requested_metric": f"{namespace}/{metric}",
                        "query": query, "aggregation": aggregation, "request_resolution": resolution,
                        "region": region,
                        "request_start_time": effective_start.isoformat(),
                        "request_end_time": effective_end.isoformat(),
                        "opc_request_id": request_id,
                    })
                    found.append(row)
            return found, None

        workers = max(1, min(int(self.config.get("parallel_calls") or DEFAULT_PARALLEL_CALLS), 10))
        done = 0
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="oci-monitoring") as pool:
            futures = [pool.submit(call, spec) for spec in calls]
            try:
                for future in as_completed(futures):
                    found, error = future.result()
                    rows.extend(found)
                    if error:
                        errors.append(error)
                    done += 1
                    if progress:
                        progress(done, len(calls))
            except CollectionCancelled:
                for future in futures:
                    future.cancel()
                raise
        return pd.DataFrame(rows), effective_end, errors

    def test_connection(self) -> dict:
        try:
            self._validate()
            self.client().list_metrics(
                self.compartment_ids()[0],
                __import__("oci").monitoring.models.ListMetricsDetails(namespace=self.config.get("namespace") or DEFAULT_NAMESPACE),
                limit=1,
            )
            return {"status": "OK", "message": "OCI Monitoring acessível com as credenciais da conexão de referência"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "FAILED", "message": str(exc)[:500]}


@dataclass(frozen=True)
class MonitoringScope:
    """Allowlisted scope of a monitoring source, as the engine and readers see it."""
    enabled: bool
    region: str | None
    resource_ids: tuple[str, ...]
    resolution: str
    extractor: "OciMonitoringExtractor"

    def oci_config(self) -> dict:
        return self.extractor.oci_config()

    def earliest_available_utc(self, now: datetime | None = None) -> datetime | None:
        """Oldest instant the provider still serves at this resolution."""
        windows = self.extractor.windows(now)
        return windows[0][2] if windows else None


def monitoring_scope(connection: SourceConnection) -> MonitoringScope:
    extractor = OciMonitoringExtractor(connection)
    return MonitoringScope(
        enabled=extractor.discovers_compartment or bool(extractor.resource_ids),
        region=extractor.oci_config().get("region"),
        resource_ids=tuple(extractor.resource_ids), resolution=extractor.resolution, extractor=extractor,
    )
