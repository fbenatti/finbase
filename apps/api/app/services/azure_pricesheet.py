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

logger = logging.getLogger(__name__)
PREFIX = "reports/report_-pricesheet/"
BATCH_ROWS = 100_000


def _azure_client() -> tuple[BlobServiceClient, str]:
    tenant = settings.azure_4p_tenant_id or settings.azure_ti_tenant_id
    client_id = settings.azure_4p_client_id or settings.azure_ti_client_id
    secret = settings.azure_4p_client_secret or settings.azure_ti_client_secret
    account = settings.azure_4p_storage_account or settings.azure_ti_storage_account
    container = settings.azure_4p_container if settings.azure_4p_storage_account else settings.azure_ti_container
    if not all((tenant, client_id, secret, account, container)):
        raise RuntimeError("Credenciais Azure para azure-pricesheet não estão configuradas")
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


def _latest_valid_manifest(container: Any) -> tuple[str, dict[str, Any], list[dict[str, Any]], str]:
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
            for part in parts:
                properties = container.get_blob_client(part["name"]).get_blob_properties()
                if int(properties.size) != part["size"]:
                    raise ValueError(f"tamanho divergente para {part['name']}")
            period_match = re.search(r"/(\d{6})\d{2}-\d{6}\d{2}/", item.name)
            period = period_match.group(1) if period_match else datetime.now(timezone.utc).strftime("%Y%m")
            snapshot_id = str(item.etag or item.name).strip('"')
            return period, data, parts, f"{snapshot_id}:{expected_rows}"
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


def _header(row: dict[str, Any], name: str) -> Any:
    target = name.casefold()
    return next((value for key, value in row.items() if str(key).strip().casefold() == target), None)


def _canonical(row: dict[str, Any], snapshot_id: str, period: str, connection_id: int, connection_code: str, connection_name: str) -> dict[str, Any]:
    get = lambda name: _header(row, name)
    date_value = get("EffectiveStartDate") or get("EffectiveStartTime")
    valid_from = pd.to_datetime(date_value, utc=True, errors="coerce")
    def number(name: str):
        value = get(name)
        try:
            return float(value) if value not in (None, "") else None
        except (ValueError, TypeError):
            return None
    return {
        "cloud_provider": "azure", "meter_id": get("MeterId"), "sku_id": get("SkuId"),
        "service_name": get("ServiceName"), "service_family": get("ServiceFamily"),
        "product_name": get("ProductName"), "product_id": get("ProductId"),
        "meter_name": get("MeterName"), "arm_region": get("MeterRegion"),
        "region_id": get("MeterRegion"), "arm_sku_name": get("SkuName"),
        "unit_of_measure": get("UnitOfMeasure"), "price_type": get("PriceType"),
        "term": get("Term"), "billing_account_id": get("BillingAccountId"),
        "billing_currency": get("BillingCurrency"), "price_source": "CONTRACTED",
        "price_confidence": "HIGH", "reservation_term": get("ReservationTerm"),
        "currency": get("Currency") or get("CurrencyCode"),
        # UnitPrice already contains the negotiated contract price. No further
        # percentage discount is applied here.
        "unit_price": number("UnitPrice"), "base_price": number("BasePrice"),
        "market_price": number("MarketPrice"), "tier_minimum_units": number("TierMinimumUnits"),
        "effective_start_date": valid_from, "valid_from": valid_from,
        "is_primary_meter": str(get("IsPrimaryMeter") or ""),
        "snapshot_id": snapshot_id, "_silver_ingested_at": datetime.now(timezone.utc).isoformat(),
        "_silver_owner": "azure-pricesheet", "_source_connection_id": connection_id,
        "_source_connection_code": connection_code, "_source_connection_name": connection_name,
        "_billing_period": period,
    }


def refresh_snapshot(connection_id: int, connection_code: str, connection_name: str) -> dict[str, Any]:
    """Stream manifest parts to Bronze and Silver, activating only after validation."""
    from app.services.iceberg_service import _append_or_create_table, _get_catalog, write_silver
    from app.services.trino_service import execute_sync
    from app.database import SyncSession
    from app.models.source_connection import SourceConnection

    service, container_name = _azure_client()
    raw_count = silver_count = 0
    try:
        container = service.get_container_client(container_name)
        period, manifest, parts, snapshot_tag = _latest_valid_manifest(container)
        expected = _declared_rows(manifest)
        manifest_blob = max(
            (blob for blob in container.list_blobs(name_starts_with=PREFIX) if blob.name.lower().endswith("manifest.json")),
            key=lambda item: item.last_modified or datetime.min.replace(tzinfo=timezone.utc),
        )
        snapshot_id = manifest_blob.name + "#" + snapshot_tag.split(":", 1)[0]
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
        for table_id in (("bronze", "raw_azure_pricesheet"), ("silver", "price_catalog")):
            try:
                table = catalog.load_table(table_id)
                from pyiceberg.expressions import EqualTo
                table.delete(EqualTo("snapshot_id", snapshot_id))
            except Exception:
                pass

        raw_batch: list[dict[str, Any]] = []
        silver_batch: list[dict[str, Any]] = []
        for part in parts:
            downloader = container.get_blob_client(part["name"]).download_blob(max_concurrency=2)
            reader = csv.DictReader(_decoded_lines(iter(downloader.chunks())))
            part_rows = 0
            for source_row in reader:
                if not source_row or not any(value not in (None, "") for value in source_row.values()):
                    continue
                part_rows += 1
                raw_batch.append({**source_row, "snapshot_id": snapshot_id, "_blob_path": part["name"], "_billing_period": period, "_ingested_at": datetime.now(timezone.utc).isoformat()})
                silver_batch.append(_canonical(source_row, snapshot_id, period, connection_id, connection_code, connection_name))
                if len(raw_batch) >= BATCH_ROWS:
                    _append_or_create_table("bronze", "raw_azure_pricesheet", pd.DataFrame(raw_batch), "_billing_period")
                    raw_count += len(raw_batch); raw_batch.clear()
                if len(silver_batch) >= BATCH_ROWS:
                    frame = pd.DataFrame(silver_batch)
                    write_silver(connection_code, frame, period, column_types={"unit_price":"DOUBLE","base_price":"DOUBLE","market_price":"DOUBLE","tier_minimum_units":"DOUBLE","_source_connection_id":"BIGINT"}, table_name_override="price_catalog")
                    silver_count += len(silver_batch); silver_batch.clear()
            declared_part_rows = next((int(x[k]) for x in _walk(manifest) if any(x.get(k) == part["name"] for k in ("name", "blobName", "fileName", "path", "blob")) for k in ("dataRowCount", "rowCount", "recordCount") if x.get(k) is not None), None)
            if declared_part_rows is not None and part_rows != declared_part_rows:
                raise ValueError(f"{part['name']}: contagem lida {part_rows} difere do manifest {declared_part_rows}")
        if raw_batch:
            _append_or_create_table("bronze", "raw_azure_pricesheet", pd.DataFrame(raw_batch), "_billing_period")
            raw_count += len(raw_batch)
        if silver_batch:
            frame = pd.DataFrame(silver_batch)
            write_silver(connection_code, frame, period, column_types={"unit_price":"DOUBLE","base_price":"DOUBLE","market_price":"DOUBLE","tier_minimum_units":"DOUBLE","_source_connection_id":"BIGINT"}, table_name_override="price_catalog")
            silver_count += len(silver_batch)
        if raw_count != expected or silver_count != expected:
            raise ValueError(f"Snapshot incompleto: manifest={expected:,}, Bronze={raw_count:,}, Silver={silver_count:,}")

        sync = SyncSession()
        try:
            conn = sync.get(SourceConnection, connection_id)
            conn.config_json = {**(conn.config_json or {}), "active_snapshot_id": snapshot_id, "active_snapshot_period": period, "active_snapshot_rows": expected, "active_snapshot_manifest": manifest_blob.name}
            conn.last_test_status = "OK"
            conn.last_test_message = f"Price Sheet ativo: {expected:,} linhas ({period})"
            conn.last_tested_at = datetime.now(timezone.utc)
            sync.commit()
        finally:
            sync.close()
        return {"snapshot_id": snapshot_id, "period": period, "rows": expected, "bronze_rows": raw_count, "silver_rows": silver_count, "parts": len(parts)}
    finally:
        service.close()
