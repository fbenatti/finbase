"""Full-snapshot Azure Price Sheet ingestion (streamed; no local CSV copies)."""
from __future__ import annotations

import codecs
import csv
import io
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Iterator

import pandas as pd
from azure.identity import ClientSecretCredential
from azure.storage.blob import BlobServiceClient

from app.config import settings
from app.core.network_override import register_storage_ip

logger = logging.getLogger(__name__)
PREFIX = "reports/report_-pricesheet/"
BATCH_ROWS = 100_000


def _azure_client(config: dict[str, Any] | None = None) -> tuple[BlobServiceClient, str]:
    """Build the Price Sheet client from its own connection configuration.

    The legacy settings fallback keeps existing deployments operational while
    the independent `azure-pricesheet` connection is configured.
    """
    config = config or {}
    keys = ("tenant_id", "client_id", "client_secret", "storage_account", "container_name")
    configured = [config.get(key) for key in keys]
    if any(configured):
        if not all(configured):
            missing = [key for key, value in zip(keys, configured) if not value]
            raise RuntimeError(
                "Configuração independente do Azure Price Sheet incompleta; faltam: "
                + ", ".join(missing)
            )
        tenant, client_id, secret, account, container = configured
    else:
        # Legacy fallback is selected as a complete credential/storage profile;
        # never mix tenant/client/secret/account across independent profiles.
        profiles = (
            (
                settings.azure_4p_tenant_id,
                settings.azure_4p_client_id,
                settings.azure_4p_client_secret,
                settings.azure_4p_storage_account,
                settings.azure_4p_container,
            ),
            (
                settings.azure_ti_tenant_id,
                settings.azure_ti_client_id,
                settings.azure_ti_client_secret,
                settings.azure_ti_storage_account,
                settings.azure_ti_container,
            ),
        )
        legacy = next((profile for profile in profiles if all(profile)), None)
        if legacy is None:
            raise RuntimeError("Credenciais Azure para azure-pricesheet não estão configuradas")
        tenant, client_id, secret, account, container = legacy
    if not all((tenant, client_id, secret, account, container)):
        raise RuntimeError("Credenciais Azure para azure-pricesheet não estão configuradas")
    # Match the Azure Blob extractor's private-endpoint DNS handling. Keep the
    # account hostname in account_url so TLS SNI and certificate validation
    # remain bound to the real Storage endpoint.
    register_storage_ip(str(account), config.get("storage_ip"))
    credential = ClientSecretCredential(tenant_id=tenant, client_id=client_id, client_secret=secret)
    service = BlobServiceClient(
        account_url=f"https://{account}.blob.core.windows.net", credential=credential,
        connection_timeout=60, read_timeout=300,
    )
    return service, container


def _walk(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _manifest_parts(document: dict[str, Any]) -> list[dict[str, Any]]:
    parts = []
    for item in _walk(document):
        name = next((item.get(k) for k in ("name", "blobName", "fileName", "path", "blob") if isinstance(item.get(k), str)), None)
        size = next((item.get(k) for k in ("byteCount", "contentLength", "size", "bytes") if item.get(k) is not None), None)
        if name and str(name).lower().endswith(".csv") and size is not None:
            parts.append({"name": str(name), "size": int(size)})
    unique = {part["name"]: part for part in parts}
    return list(unique.values())


def _declared_rows(document: dict[str, Any]) -> int | None:
    for item in _walk(document):
        for key in ("dataRowCount", "rowCount", "recordCount"):
            value = item.get(key)
            if isinstance(value, (int, float)) and value >= 0:
                return int(value)
    return None


def _declared_bytes(document: dict[str, Any]) -> int | None:
    for key in ("byteCount", "totalBytes", "contentLength"):
        value = document.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
    return None


def _latest_valid_manifest(container: Any) -> tuple[str, str, dict[str, Any], list[dict[str, Any]], str]:
    manifests = sorted(
        (blob for blob in container.list_blobs(name_starts_with=PREFIX) if blob.name.lower().endswith("manifest.json")),
        key=lambda item: (item.last_modified or datetime.min.replace(tzinfo=timezone.utc), item.name),
        reverse=True,
    )
    failures = []
    for item in manifests:
        try:
            data = json.loads(container.get_blob_client(item.name).download_blob().readall())
            parts = _manifest_parts(data)
            expected_rows = _declared_rows(data)
            if not parts or expected_rows is None:
                raise ValueError("manifest sem lista de CSVs ou contagem total de linhas")
            expected_bytes = _declared_bytes(data)
            if expected_bytes is not None and sum(part["size"] for part in parts) != expected_bytes:
                raise ValueError("soma dos tamanhos das partes diverge do byteCount total")
            for part in parts:
                properties = container.get_blob_client(part["name"]).get_blob_properties()
                if int(properties.size) != part["size"]:
                    raise ValueError(f"tamanho divergente para {part['name']}")
            period_match = re.search(r"/(\d{6})\d{2}-\d{6}\d{2}/", item.name)
            period = period_match.group(1) if period_match else datetime.now(timezone.utc).strftime("%Y%m")
            snapshot_id = str(item.etag or item.name).strip('"')
            return period, item.name, data, parts, f"{snapshot_id}:{expected_rows}"
        except Exception as exc:  # Continue to the newest complete valid snapshot.
            failures.append(f"{item.name}: {exc}")
    raise RuntimeError("Nenhum manifest Price Sheet completo e válido: " + "; ".join(failures[-5:]))


def _decoded_lines(chunks: Iterator[bytes]) -> Iterator[str]:
    decoder = codecs.getincrementaldecoder("utf-8-sig")("replace")
    pending = ""
    for chunk in chunks:
        pending += decoder.decode(chunk)
        lines = pending.splitlines(keepends=True)
        pending = ""
        if lines and not lines[-1].endswith(("\n", "\r")):
            pending = lines.pop()
        yield from lines
    pending += decoder.decode(b"", final=True)
    if pending:
        yield pending


def refresh_snapshot(connection_id: int, connection_code: str, connection_name: str) -> dict[str, Any]:
    """Stream the full Price Sheet snapshot to Bronze and activate after validation."""
    from app.services.iceberg_service import _append_or_create_table, _get_catalog
    from app.database import SyncSession
    from app.models.source_connection import SourceConnection

    sync = SyncSession()
    try:
        connection = sync.get(SourceConnection, connection_id)
        connection_config = dict(connection.config_json or {}) if connection else {}
    finally:
        sync.close()
    service, container_name = _azure_client(connection_config)
    raw_count = 0
    try:
        container = service.get_container_client(container_name)
        period, manifest_name, manifest, parts, snapshot_tag = _latest_valid_manifest(container)
        expected = _declared_rows(manifest)
        snapshot_id = manifest_name + "#" + snapshot_tag.split(":", 1)[0]
        sync = SyncSession()
        try:
            conn = sync.get(SourceConnection, connection_id)
            active = (conn.config_json or {}).get("active_snapshot_id") if conn else None
            if active == snapshot_id:
                return {"snapshot_id": snapshot_id, "period": period, "rows": expected, "already_active": True}
        finally:
            sync.close()

        # A failed previous attempt may have left non-active partial rows. They
        # are removed by snapshot key before retry; the active snapshot remains.
        catalog = _get_catalog()
        for table_id in (("bronze", "raw_azure_pricesheet"),):
            try:
                table = catalog.load_table(table_id)
            except Exception as exc:
                from pyiceberg.exceptions import NoSuchTableError
                if isinstance(exc, NoSuchTableError):
                    continue
                raise
            from pyiceberg.expressions import EqualTo
            table.delete(EqualTo("snapshot_id", snapshot_id))

        raw_batch: list[dict[str, Any]] = []
        for part in parts:
            downloader = container.get_blob_client(part["name"]).download_blob(max_concurrency=2)
            reader = csv.DictReader(_decoded_lines(iter(downloader.chunks())))
            part_rows = 0
            for source_row in reader:
                if not source_row or not any(value not in (None, "") for value in source_row.values()):
                    continue
                part_rows += 1
                # Keep every CSV value in Bronze; normalize header casing only
                # so Trino can query stable field names across report parts.
                raw_row = {str(key).strip().casefold(): value for key, value in source_row.items() if key is not None}
                if len(raw_row) != len([key for key in source_row if key is not None]):
                    raise ValueError(f"Cabeçalho ambíguo após normalização em {part['name']}")
                raw_batch.append({**raw_row, "snapshot_id": snapshot_id, "_blob_path": part["name"], "_billing_period": period, "_ingested_at": datetime.now(timezone.utc).isoformat()})
                if len(raw_batch) >= BATCH_ROWS:
                    _append_or_create_table("bronze", "raw_azure_pricesheet", pd.DataFrame(raw_batch), "_billing_period")
                    raw_count += len(raw_batch); raw_batch.clear()
            declared_part_rows = next((int(x[k]) for x in _walk(manifest) if any(x.get(k) == part["name"] for k in ("name", "blobName", "fileName", "path", "blob")) for k in ("dataRowCount", "rowCount", "recordCount") if x.get(k) is not None), None)
            if declared_part_rows is not None and part_rows != declared_part_rows:
                raise ValueError(f"{part['name']}: contagem lida {part_rows} difere do manifest {declared_part_rows}")
        if raw_batch:
            _append_or_create_table("bronze", "raw_azure_pricesheet", pd.DataFrame(raw_batch), "_billing_period")
            raw_count += len(raw_batch)
        if raw_count != expected:
            raise ValueError(f"Snapshot incompleto: manifest={expected:,}, Bronze={raw_count:,}")

        sync = SyncSession()
        try:
            conn = sync.get(SourceConnection, connection_id)
            conn.config_json = {**(conn.config_json or {}), "active_snapshot_id": snapshot_id, "active_snapshot_period": period, "active_snapshot_rows": expected, "active_snapshot_manifest": manifest_name}
            conn.last_test_status = "OK"
            conn.last_test_message = f"Price Sheet ativo: {expected:,} linhas ({period})"
            conn.last_tested_at = datetime.now(timezone.utc)
            sync.commit()
        finally:
            sync.close()
        return {"snapshot_id": snapshot_id, "period": period, "rows": expected, "bronze_rows": raw_count, "silver_rows": 0, "parts": len(parts)}
    finally:
        service.close()
