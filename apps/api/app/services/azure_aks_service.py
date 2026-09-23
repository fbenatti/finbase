"""Inventário e topologia de AKS.

Os clusters e seus node pools vêm do Resource Graph, que já expõe a topologia
declarada: pools, contagem de nós, SKU, zonas, versão do Kubernetes e perfil de
rede. O que o cluster *executa* (namespaces, workloads, pods) exige a API do
Kubernetes e fica para o passo seguinte — aqui a Bronze guarda o que o provedor
declara, que é o que permite alocar custo por pool e por nó.

Mesmo padrão dos demais coletores: consulta por assinatura, escrita na Bronze com
`_billing_period` e `_blob_path`, recarga idempotente.
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

BRONZE_TABLE = "azure_aks_inventory"

# Clusters e a topologia declarada de cada um. `agentPoolProfiles` é uma lista
# aninhada; achatá-la em uma linha por pool é o que permite custo por pool.
CLUSTER_QUERY = """
resources
| where type =~ "microsoft.containerservice/managedclusters"
| mv-expand pool = properties.agentPoolProfiles
| extend
    pool_name = tostring(pool["name"]),
    pool_count = tostring(pool["count"]),
    pool_vm_size = tostring(pool["vmSize"]),
    pool_mode = tostring(pool["mode"]),
    pool_os = tostring(pool["osType"]),
    pool_zones = tostring(pool["availabilityZones"]),
    pool_max_pods = tostring(pool["maxPods"]),
    pool_os_disk_gb = tostring(pool["osDiskSizeGB"])
| project
    resource_id = id,
    resource_name = name,
    resource_type = type,
    subscription_id = subscriptionId,
    resource_group = resourceGroup,
    location = location,
    kubernetes_version = tostring(properties.kubernetesVersion),
    current_version = tostring(properties.currentKubernetesVersion),
    dns_prefix = tostring(properties.dnsPrefix),
    fqdn = tostring(properties.fqdn),
    private_fqdn = tostring(properties.privateFQDN),
    node_resource_group = tostring(properties.nodeResourceGroup),
    network_plugin = tostring(properties.networkProfile.networkPlugin),
    network_policy = tostring(properties.networkProfile.networkPolicy),
    network_dataplane = tostring(properties.networkProfile.networkDataplane),
    pod_cidr = tostring(properties.networkProfile.podCidr),
    service_cidr = tostring(properties.networkProfile.serviceCidr),
    sku_tier = tostring(sku.tier),
    power_state = tostring(properties.powerState.code),
    provisioning_state = tostring(properties.provisioningState),
    aad_enabled = tostring(properties.aadProfile.enableAzureRBAC),
    oidc_issuer = tostring(properties.oidcIssuerProfile.issuerURL),
    identity_type = tostring(identity.type),
    zones = tostring(zones),
    created_at = tostring(properties.timeCreated),
    pool_name = pool_name,
    pool_count = pool_count,
    pool_vm_size = pool_vm_size,
    pool_mode = pool_mode,
    pool_os = pool_os,
    pool_zones = pool_zones,
    pool_max_pods = pool_max_pods,
    pool_os_disk_gb = pool_os_disk_gb,
    tags = tags
"""


def _subscriptions(config: dict[str, Any]) -> list[str]:
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
    payload: dict[str, Any] = {"query": query, "options": {"$top": PAGE_SIZE}}
    if subscription_ids:
        payload["subscriptions"] = subscription_ids
    if skip_token:
        payload["options"]["$skipToken"] = skip_token
    response = await client.post(
        f"{ARG_ENDPOINT}?api-version={ARG_API_VERSION}", headers=headers, json=payload
    )
    response.raise_for_status()
    body = response.json()
    return body.get("data") or [], body.get("$skipToken")


async def run_query_all(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    query: str,
    subscription_ids: list[str] | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        page, token = await _post_query(client, headers, query, subscription_ids, skip_token=token)
        rows.extend(page)
        if not token:
            return rows


async def collect_aks_inventory(config: dict[str, Any]) -> pd.DataFrame:
    """Clusters com um registro por node pool."""
    headers = await arm_headers(config)
    subscriptions = _subscriptions(config)
    async with httpx.AsyncClient(timeout=120.0) as client:
        rows = await run_query_all(client, headers, CLUSTER_QUERY, subscriptions or None)
    logger.info("Inventário AKS: %d registro(s) (cluster × node pool)", len(rows))
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    if "tags" in frame.columns:
        frame["tags_json"] = frame["tags"].apply(lambda v: None if v is None else str(v))
        frame = frame.drop(columns=["tags"])
    frame["collected_at"] = datetime.now(timezone.utc).isoformat()
    return frame


def _write_bronze(
    session, connection, frame: pd.DataFrame, period: str, blob_path: str, *, source_code: str | None = None
) -> int:
    if frame.empty:
        return 0
    from app.models.ingested_blob import IngestedBlob
    from app.services import iceberg_service as iceberg

    code = source_code or connection.code
    iceberg.delete_bronze_slice(code, blob_path=blob_path, billing_period=period)
    rows = iceberg.write_bronze(
        connection_code=code, df=frame, billing_period=period, blob_path=blob_path
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
        connection_id=connection.id, blob_path=blob_path, blob_prefix=blob_path.split("/")[0],
        billing_period=period, last_modified=datetime.now(timezone.utc),
        etag="", content_length=0, row_count=rows, status="INGESTED",
    ))
    session.flush()
    return rows


async def discover_scope(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Assinaturas com clusters AKS — usado no teste da conexão."""
    headers = await arm_headers(config)
    async with httpx.AsyncClient(timeout=120.0) as client:
        rows = await run_query_all(
            client,
            headers,
            'resources | where type =~ "microsoft.containerservice/managedclusters" '
            "| summarize clusters = count() by subscriptionId",
            _subscriptions(config) or None,
        )
    out = [
        {"subscription_id": r.get("subscriptionId") or "", "clusters": int(r.get("clusters") or 0)}
        for r in rows
    ]
    out.sort(key=lambda item: item["clusters"], reverse=True)
    return out


def run_aks_inventory_collection(
    session,
    connection_id: int,
    *,
    snapshot_date: str | None = None,
    task_id: int | None = None,
    source_code: str | None = None,
) -> dict[str, Any]:
    """Coleta o inventário AKS e persiste na Bronze, ancorado no dia."""
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

    frame = asyncio_run(collect_aks_inventory(config))
    if frame.empty:
        s.commit()
        return {"connection_id": connection_id, "rows_bronze": 0, "blob_path": blob_path, "period": period}

    if "snapshot_date" not in frame.columns:
        frame["snapshot_date"] = day
    rows = _write_bronze(s, conn, frame, period, blob_path, source_code=source_code)
    s.commit()
    return {
        "connection_id": connection_id,
        "rows_bronze": rows,
        "blob_path": blob_path,
        "period": period,
        "bronze_table": sanitized_bronze(conn),
        "clusters": int(frame["resource_id"].nunique()) if "resource_id" in frame.columns else 0,
        "pools": int(len(frame)),
    }
