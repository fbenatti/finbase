"""Identidade de VM no Azure (inventário de compute).

Liga cada VM ao seu contexto — assinatura, resource group, região, SKU, estado
de energia, SO e tags. É a base do custo por VM: o custo chega pela fonte de
cost exports e é correlacionado pelo ``resource_id``, que aqui é a chave
canônica.

Segue o padrão do coletor de Resource Graph: uma consulta ARG por assinatura,
paginação por ``$skipToken`` e escrita na Bronze com ``_billing_period`` e
``_blob_path``, o que torna a recarga idempotente.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd

from app.services.azure_resource_graph_service import (
    ARG_API_VERSION,
    ARG_ENDPOINT,
    PAGE_SIZE,
    arm_headers,
)
from app.services.azure_source_registry import sanitized_bronze

logger = logging.getLogger(__name__)

# Só VMs e conjuntos de escala: o inventário de compute não é um dump geral.
VM_QUERY = """
resources
| where type =~ "microsoft.compute/virtualmachines"
| extend power_state = tostring(properties.extended.instanceView.powerState.code)
| project
    resource_id = id,
    resource_name = name,
    resource_type = type,
    subscription_id = subscriptionId,
    resource_group = resourceGroup,
    location = location,
    vm_size = tostring(properties.hardwareProfile.vmSize),
    power_state = power_state,
    os_type = tostring(properties.storageProfile.osDisk.osType),
    os_disk_type = tostring(properties.storageProfile.osDisk.managedDisk.storageAccountType),
    provisioning_state = tostring(properties.provisioningState),
    priority = tostring(properties.priority),
    zones = tostring(zones),
    created_at = tostring(properties.timeCreated),
    tags = tags
"""

# Os conjuntos de escala têm custo próprio e precisam da mesma identidade.
VMSS_QUERY = """
resources
| where type =~ "microsoft.compute/virtualmachinescalesets"
| project
    resource_id = id,
    resource_name = name,
    resource_type = type,
    subscription_id = subscriptionId,
    resource_group = resourceGroup,
    location = location,
    vm_size = tostring(sku.name),
    sku_capacity = tostring(sku.capacity),
    power_state = "",
    os_type = "",
    os_disk_type = "",
    provisioning_state = tostring(properties.provisioningState),
    priority = "",
    zones = tostring(zones),
    created_at = tostring(properties.timeCreated),
    tags = tags
"""

BRONZE_TABLE = "azure_vm_inventory"


def _subscriptions(config: dict[str, Any]) -> list[str]:
    """Assinaturas do escopo. Lista explícita vence; senão, tudo que a
    credencial alcança (a consulta ARG é por assinatura)."""
    raw = config.get("subscription_ids") or config.get("subscriptions") or []
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.replace(",", "\n").splitlines()]
    return [str(item).strip() for item in raw if str(item).strip()]


async def _post_query(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    query: str,
    subscription_ids: list[str] | None,
    *,
    skip_token: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Uma página da consulta ARG."""
    payload: dict[str, Any] = {
        "query": query,
        "options": {"$top": PAGE_SIZE},
    }
    if subscription_ids:
        payload["subscriptions"] = subscription_ids
    if skip_token:
        payload["options"]["$skipToken"] = skip_token

    response = await client.post(
        f"{ARG_ENDPOINT}?api-version={ARG_API_VERSION}",
        headers=headers,
        json=payload,
    )
    response.raise_for_status()
    body = response.json()
    data = body.get("data") or []
    next_token = (body.get("$skipToken") or (body.get("resultTruncated") and body.get("$skipToken"))) or None
    return data, next_token


async def run_query_all(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    query: str,
    subscription_ids: list[str] | None,
) -> list[dict[str, Any]]:
    """Percorre todas as páginas da consulta."""
    rows: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        page, token = await _post_query(client, headers, query, subscription_ids, skip_token=token)
        rows.extend(page)
        if not token:
            return rows


def _flatten_tags(frame: pd.DataFrame) -> pd.DataFrame:
    """As tags vêm como objeto; a Bronze guarda texto para o pivot conseguir
    filtrar sem depender de estrutura aninhada."""
    if "tags" not in frame.columns:
        return frame
    frame["tags_json"] = frame["tags"].apply(
        lambda value: None if value is None else str(value)
    )
    return frame.drop(columns=["tags"])


async def collect_vm_inventory(config: dict[str, Any]) -> pd.DataFrame:
    """Inventário de VMs e VMSS em todas as assinaturas do escopo."""
    headers = await arm_headers(config)
    subscriptions = _subscriptions(config)
    async with httpx.AsyncClient(timeout=120.0) as client:
        vm_rows = await run_query_all(client, headers, VM_QUERY, subscriptions or None)
        logger.info("Inventário de VM: %d recurso(s)", len(vm_rows))
        try:
            vmss_rows = await run_query_all(client, headers, VMSS_QUERY, subscriptions or None)
        except Exception as exc:  # noqa: BLE001
            # VMSS é complementar: sem ele o inventário de VM ainda é válido.
            logger.warning("Inventário de VMSS indisponível: %s", exc)
            vmss_rows = []
        logger.info("Inventário de VMSS: %d recurso(s)", len(vmss_rows))

    frame = pd.DataFrame(vm_rows + vmss_rows)
    if frame.empty:
        return frame
    frame = _flatten_tags(frame)
    frame["collected_at"] = datetime.now(timezone.utc).isoformat()
    return frame


def _write_bronze(session, connection, frame: pd.DataFrame, period: str, blob_path: str) -> int:
    """Escreve o inventário na Bronze e registra o blob ingerido."""
    if frame.empty:
        return 0
    from app.models.ingested_blob import IngestedBlob
    from app.services import iceberg_service as iceberg

    iceberg.delete_bronze_slice(connection.code, blob_path=blob_path, billing_period=period)
    rows = iceberg.write_bronze(
        connection_code=connection.code,
        df=frame,
        billing_period=period,
        blob_path=blob_path,
    )
    if rows == 0:
        return 0

    existing = (
        session.query(IngestedBlob)
        .filter_by(connection_id=connection.id, blob_path=blob_path)
        .first()
    )
    if existing:
        session.delete(existing)
        session.flush()
    session.add(IngestedBlob(
        connection_id=connection.id,
        blob_path=blob_path,
        blob_prefix=blob_path.split("/")[0],
        billing_period=period,
        last_modified=datetime.now(timezone.utc),
        etag="",
        content_length=0,
        row_count=rows,
        status="INGESTED",
    ))
    session.flush()
    return rows


async def discover_scope(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Assinaturas com VMs — usado pelo teste da conexão, sem coletar."""
    headers = await arm_headers(config)
    async with httpx.AsyncClient(timeout=120.0) as client:
        rows = await run_query_all(
            client,
            headers,
            'resources | where type =~ "microsoft.compute/virtualmachines" '
            "| summarize vms = count() by subscriptionId",
            _subscriptions(config) or None,
        )
    out = [
        {"subscription_id": row.get("subscriptionId") or "", "vm_count": int(row.get("vms") or 0)}
        for row in rows
    ]
    out.sort(key=lambda item: item["vm_count"], reverse=True)
    logger.info("Inventário de VM: %d assinatura(s) com VMs", len(out))
    return out


def run_vm_inventory_collection(
    session,
    connection_id: int,
    *,
    snapshot_date: str | None = None,
    task_id: int | None = None,
) -> dict[str, Any]:
    """Coleta o inventário de VM e persiste na Bronze, ancorado no dia.

    O período é a data da coleta: o inventário é uma fotografia, então a mesma
    data reescrita substitui a anterior em vez de duplicar.
    """
    from app.database import SyncSession as _Sync
    from app.models.source_connection import SourceConnection
    from app.services.azure_automation_service import asyncio_run

    s = session or _Sync()
    conn = s.get(SourceConnection, connection_id)
    if not conn:
        raise ValueError(f"Conexão {connection_id} não encontrada")

    config = dict(conn.config_json or {})
    day = snapshot_date or datetime.now(timezone.utc).date().isoformat()
    period = day.replace("-", "")[:6]
    blob_path = f"{BRONZE_TABLE}/{day}"

    frame = asyncio_run(collect_vm_inventory(config))
    if frame.empty:
        s.commit()
        return {"connection_id": connection_id, "rows_bronze": 0, "blob_path": blob_path, "period": period}

    if "snapshot_date" not in frame.columns:
        frame["snapshot_date"] = day

    rows = _write_bronze(s, conn, frame, period, blob_path)
    conn.last_test_status = "OK"
    conn.last_tested_at = datetime.now(timezone.utc)
    s.commit()
    logger.info("Inventário de VM: %d linha(s) na Bronze (%s)", rows, blob_path)
    return {
        "connection_id": connection_id,
        "rows_bronze": rows,
        "blob_path": blob_path,
        "period": period,
        "bronze_table": sanitized_bronze(conn.code),
    }
