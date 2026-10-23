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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.ingested_blob import IngestedBlob
from app.models.skipped_blob import SkippedBlob
from app.models.source_connection import SourceConnection
from app.schemas.schemas import BlobScanItem, BlobScanResult

logger = logging.getLogger(__name__)

DEFAULT_NAMESPACE = "oci_computeagent"
DEFAULT_METRICS = (
    "CpuUtilization", "MemoryUtilization", "DiskIopsRead", "DiskIopsWritten",
    "DiskBytesRead", "DiskBytesWritten", "NetworksBytesIn", "NetworksBytesOut",
)
DEFAULT_STATISTICS = ("mean", "max", "p95", "p99", "count")
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


def blob_path_for(resolution: str, start: datetime, end: datetime) -> str:
    return f"{BLOB_ROOT}/{resolution}/{start:%Y-%m-%d}_{end:%Y-%m-%d}"


def parse_blob_path(blob_path: str) -> tuple[str, datetime, datetime]:
    root, resolution, window = blob_path.split("/")
    if root != BLOB_ROOT:
        raise ValueError(f"Unidade OCI Monitoring inválida: {blob_path}")
    start_text, end_text = window.split("_")
    start = datetime.fromisoformat(start_text).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(end_text).replace(tzinfo=timezone.utc)
    return resolution, start, end


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
        """``discovery = "compartment"``: every instance of the compartment, no allowlist."""
        return str(self.config.get("discovery") or "") == "compartment"

    @property
    def resource_ids(self) -> list[str]:
        if self.discovers_compartment:
            return [instance["resource_id"] for instance in self.instances()]
        return self._list("resource_ids")

    def instances(self) -> list[dict]:
        """Non-terminated instances of the compartment (one paginated ListInstances)."""
        if getattr(self, "_instances", None) is None:
            import oci

            compute = oci.core.ComputeClient(self.oci_config(), timeout=(5, 60))
            found = oci.pagination.list_call_get_all_results(
                compute.list_instances, self.config["compartment_id"],
            ).data
            self._instances = [
                {"resource_id": item.id, "instance": item}
                for item in found if item.lifecycle_state not in ("TERMINATED", "TERMINATING")
            ]
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

    def client(self):
        if self._client is None:
            import oci

            self._client = oci.monitoring.MonitoringClient(
                self.oci_config(), retry_strategy=oci.retry.NoneRetryStrategy(), timeout=(5, 60),
            )
        return self._client

    def _validate(self) -> None:
        if not self.config.get("compartment_id"):
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

    def _build_scan_items(self, existing: dict[str, IngestedBlob], skipped: set[str]) -> list[BlobScanItem]:
        items = []
        for start, end, _effective_start, effective_end in self.windows():
            blob_path = blob_path_for(self.resolution, start, end)
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
    def fetch_window(self, blob_path: str, now: datetime | None = None, should_abort=None) -> tuple[pd.DataFrame, datetime, list[str]]:
        """Raw datapoints of one unit, plus the effective end and per-call errors."""
        import oci

        self._validate()
        resolution, start, end = parse_blob_path(blob_path)
        match = next((unit for unit in self.windows(now) if unit[0] == start and unit[1] == end), None)
        if match is None:
            raise ValueError(f"Unidade fora da janela coletável: {blob_path}")
        _start, _end, effective_start, effective_end = match
        namespace = str(self.config.get("namespace") or DEFAULT_NAMESPACE)
        compartment_id = str(self.config["compartment_id"])
        region = self.oci_config().get("region")
        rows: list[dict[str, Any]] = []
        errors: list[str] = []
        # Compartment discovery asks once per metric/statistic and receives one
        # stream per instance; an allowlist asks per resource.
        resource_filters = [None] if self.discovers_compartment else self.resource_ids
        for metric in self._list("metric_names", DEFAULT_METRICS):
            for resource_id in resource_filters:
                for statistic in self._list("statistics", DEFAULT_STATISTICS):
                    if should_abort and should_abort():
                        raise RuntimeError("Coleta cancelada")
                    aggregation = AGGREGATIONS[statistic]
                    selector = f'{{resourceId = "{resource_id}"}}' if resource_id else ""
                    query = f'{metric}[{resolution}]{selector}.{aggregation}'
                    details = oci.monitoring.models.SummarizeMetricsDataDetails(
                        namespace=namespace, query=query, start_time=effective_start,
                        end_time=effective_end, resolution=resolution,
                    )
                    try:
                        response = self.client().summarize_metrics_data(compartment_id, details)
                    except oci.exceptions.ServiceError as exc:
                        errors.append(f"{metric}/{statistic}: {exc.status} {exc.code}")
                        continue
                    request_id = (getattr(response, "headers", {}) or {}).get("opc-request-id")
                    for stream in oci.util.to_dict(response.data) or []:
                        datapoints = stream.pop("aggregated_datapoints", None) or []
                        for point in datapoints:
                            row = {key: _text(value) for key, value in stream.items()}
                            row.update({key: _text(value) for key, value in point.items()})
                            row.update({
                                "query": query, "aggregation": aggregation, "request_resolution": resolution,
                                "region": region,
                                "request_start_time": effective_start.isoformat(),
                                "request_end_time": effective_end.isoformat(),
                                "opc_request_id": request_id,
                            })
                            rows.append(row)
        return pd.DataFrame(rows), effective_end, errors

    def test_connection(self) -> dict:
        try:
            self._validate()
            self.client().list_metrics(
                self.config["compartment_id"],
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
