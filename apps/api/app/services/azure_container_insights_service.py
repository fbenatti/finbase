"""Container Insights: inventário e desempenho de containers.

O dado vive no workspace do Log Analytics ao qual o cluster foi vinculado. **Só
os clusters com o add-on têm dado** — no 4P, 5 de 46. Os outros 41 ficam com
estado explícito de indisponibilidade, porque o plano proíbe inferir: "ausência
de add-on/API fica `PARTIAL/UNAVAILABLE`, sem inferir namespace por nome".

O coletor descobre os workspaces a partir do próprio Resource Graph (o add-on
declara o workspace), então não há caminho nem workspace chumbado no código.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

BRONZE_TABLE = "azure_container_insights"
ARM = "https://management.azure.com"
QUERY_API_VERSION = "2017-10-01"
LOOKBACK_DAYS = 7
MAX_WORKSPACES = 60

# Tabelas confirmadas no workspace do 4P: `KubePodInventory` (inventário),
# `KubeNodeInventory` (nós, quando existe) e `Perf`. `ContainerInventory` **não
# existe** nesta implantação — usar o nome "padrão" dava erro semântico 400.
QUERY_PODS = """
KubePodInventory
| summarize pods = count(), containers = sum(ContainerRestartCount + 1)
    by Namespace, Computer, PodStatus
"""

QUERY_NODES = """
KubeNodeInventory
| summarize nodes = count() by Computer, KubernetesVersion
"""

QUERY_PERFORMANCE = """
Perf
| where ObjectName == "K8SContainer"
| where CounterName in ("cpuUsageNanoCores", "memoryWorkingSetBytes")
| summarize media = avg(CounterValue), amostras = count()
    by CounterName, Namespace = InstanceName, Computer
"""

# Restrição de conteúdo: o cluster declara o add-on e o workspace.
WORKSPACE_QUERY = """
resources
| where type =~ "microsoft.containerservice/managedclusters"
| extend oms = properties.addonProfiles.omsagent
| extend habilitado = tostring(oms.enabled),
         ws = tostring(oms.config.logAnalyticsWorkspaceResourceID)
| project cluster_id = id, cluster_name = name, subscription_id = subscriptionId,
          location, habilitado, ws
"""


def _chave_conexao(config: dict[str, Any]) -> list[str]:
    raw = config.get("subscription_ids") or config.get("subscriptions") or []
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.replace(",", "\n").splitlines()]
    return [str(x).strip() for x in raw if str(x).strip()]


async def discover_workspaces(config: dict[str, Any]) -> pd.DataFrame:
    """Clusters e o workspace do Container Insights de cada um."""
    from app.services.azure_resource_graph_service import (
        ARG_API_VERSION, ARG_ENDPOINT, arm_headers,
    )

    headers = await arm_headers(config)
    async with httpx.AsyncClient(timeout=90.0) as cliente:
        resposta = await cliente.post(
            f"{ARG_ENDPOINT}?api-version={ARG_API_VERSION}",
            headers=headers,
            json={"query": WORKSPACE_QUERY, "options": {"$top": 1000}},
        )
        resposta.raise_for_status()
        linhas = resposta.json().get("data") or []
    return pd.DataFrame(linhas)


async def _consultar_workspace(
    cliente: httpx.AsyncClient, headers: dict[str, str], workspace_id: str, consulta: str
) -> list[dict[str, Any]]:
    """Executa uma consulta KQL no workspace e devolve as linhas como dicts."""
    resposta = await cliente.post(
        f"{ARM}{workspace_id}/query?api-version={QUERY_API_VERSION}",
        headers=headers,
        json={"query": consulta, "timespan": f"P{LOOKBACK_DAYS}D"},
    )
    resposta.raise_for_status()
    corpo = resposta.json()
    saida: list[dict[str, Any]] = []
    for tabela in corpo.get("tables") or []:
        colunas = [c.get("name") for c in (tabela.get("columns") or [])]
        for linha in tabela.get("rows") or []:
            saida.append(dict(zip(colunas, linha)))
    return saida


async def collect_container_insights(config: dict[str, Any]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Inventário e desempenho dos containers, por cluster com add-on.

    Devolve também a **cobertura**: quais clusters não têm o add-on, para que a
    ausência fique registrada em vez de silenciosa.
    """
    from app.services.azure_resource_graph_service import arm_headers

    clusters = await discover_workspaces(config)
    if clusters.empty:
        return pd.DataFrame(), []

    headers = await arm_headers(config)
    linhas: list[dict[str, Any]] = []
    cobertura: list[dict[str, Any]] = []
    workspaces: dict[str, list[dict[str, Any]]] = {}

    for _, cluster in clusters.iterrows():
        habilitado = str(cluster.get("habilitado") or "").lower() == "true"
        ws = str(cluster.get("ws") or "").strip()
        if not habilitado or not ws:
            cobertura.append({
                "cluster_id": str(cluster.get("cluster_id") or "").lower(),
                "cluster_name": cluster.get("cluster_name"),
                "coverage_status": "UNAVAILABLE",
                "coverage_reason": "Cluster sem add-on Container Insights (omsagent desabilitado).",
            })
            continue
        workspaces.setdefault(ws, []).append({
            "cluster_id": str(cluster.get("cluster_id") or "").lower(),
            "cluster_name": cluster.get("cluster_name"),
            "subscription_id": cluster.get("subscription_id"),
            "location": cluster.get("location"),
        })

    async with httpx.AsyncClient(timeout=180.0) as cliente:
        for ws, donos in list(workspaces.items())[:MAX_WORKSPACES]:
            try:
                pods = await _consultar_workspace(cliente, headers, ws, QUERY_PODS)
                # `KubeNodeInventory` pode não existir em workspace antigo; a
                # ausência é do workspace, não defeito do coletor.
                try:
                    nos = await _consultar_workspace(cliente, headers, ws, QUERY_NODES)
                except Exception:  # noqa: BLE001
                    nos = []
                desempenho = await _consultar_workspace(cliente, headers, ws, QUERY_PERFORMANCE)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Container Insights: workspace %s falhou: %s", ws[:60], exc)
                for dono in donos:
                    cobertura.append({
                        "cluster_id": dono["cluster_id"], "cluster_name": dono["cluster_name"],
                        "coverage_status": "UNAVAILABLE",
                        "coverage_reason": f"Falha ao consultar o workspace: {str(exc)[:200]}",
                    })
                continue

            # Um workspace pode servir vários clusters; a tabela `Computer` traz o
            # nó, então a linha é replicada por cluster apenas quando há um só.
            cluster_nome = donos[0]["cluster_name"]
            for item in pods:
                linhas.append({
                    "cluster_id": donos[0]["cluster_id"],
                    "cluster_name": cluster_nome,
                    "computer": item.get("Computer"),
                    "namespace": item.get("Namespace"),
                    "pod_status": item.get("PodStatus"),
                    "pods": item.get("pods"),
                    "containers": item.get("containers"),
                    "nodes": None,
                    "record_kind": "pod_inventory",
                    "workspace_id": ws,
                })
            for item in nos:
                linhas.append({
                    "cluster_id": donos[0]["cluster_id"],
                    "cluster_name": cluster_nome,
                    "computer": item.get("Computer"),
                    "namespace": None,
                    "pod_status": None,
                    "pods": None,
                    "containers": None,
                    "nodes": item.get("nodes"),
                    "kubernetes_version": item.get("KubernetesVersion"),
                    "record_kind": "node_inventory",
                    "workspace_id": ws,
                })
            for item in desempenho:
                linhas.append({
                    "cluster_id": donos[0]["cluster_id"],
                    "cluster_name": cluster_nome,
                    "computer": item.get("Computer"),
                    "namespace": item.get("Namespace"),
                    "metric_name": item.get("CounterName"),
                    "metric_average": item.get("media"),
                    "samples": item.get("amostras"),
                    "record_kind": "performance",
                    "workspace_id": ws,
                })
            for dono in donos:
                cobertura.append({
                    "cluster_id": dono["cluster_id"], "cluster_name": dono["cluster_name"],
                    "coverage_status": "AVAILABLE",
                    "coverage_reason": None,
                })

    return pd.DataFrame(linhas), cobertura


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
    existente = (
        session.query(IngestedBlob)
        .filter_by(connection_id=connection.id, blob_path=blob_path)
        .first()
    )
    if existente:
        session.delete(existente)
        session.flush()
    session.add(IngestedBlob(
        connection_id=connection.id, blob_path=blob_path, blob_prefix=blob_path.split("/")[0],
        billing_period=period, last_modified=datetime.now(timezone.utc),
        etag="", content_length=0, row_count=rows, status="INGESTED",
    ))
    session.flush()
    return rows


def run_container_insights_collection(
    session, connection_id: int, *, task_id: int | None = None, source_code: str | None = None
) -> dict[str, Any]:
    """Coleta inventário e desempenho dos clusters com Container Insights."""
    from app.database import SyncSession as _Sync
    from app.models.source_connection import SourceConnection
    from app.services.azure_automation_service import asyncio_run

    s = session or _Sync()
    conn = s.get(SourceConnection, connection_id)
    if not conn:
        raise ValueError(f"Conexão {connection_id} não encontrada")

    frame, cobertura = asyncio_run(collect_container_insights(dict(conn.config_json or {})))
    dia = datetime.now(timezone.utc).date().isoformat()
    periodo = dia.replace("-", "")[:6]

    total = 0
    if not frame.empty:
        frame["snapshot_date"] = dia
        total += _write_bronze(
            s, conn, frame, periodo, f"{BRONZE_TABLE}/{dia}/dados", source_code=source_code
        )
    # A cobertura é dado, não log: registra quem ficou de fora e por quê.
    if cobertura:
        cobertura_frame = pd.DataFrame(cobertura)
        cobertura_frame["snapshot_date"] = dia
        total += _write_bronze(
            s, conn, cobertura_frame, periodo, f"{BRONZE_TABLE}/{dia}/cobertura", source_code=source_code
        )
    s.commit()

    com_dado = sum(1 for c in cobertura if c["coverage_status"] == "AVAILABLE")
    sem_dado = sum(1 for c in cobertura if c["coverage_status"] != "AVAILABLE")
    return {
        "connection_id": connection_id,
        "rows_bronze": total,
        "period": periodo,
        "clusters_with_data": com_dado,
        "clusters_without_data": sem_dado,
        "status": "PARTIAL" if sem_dado else "AVAILABLE",
        "status_reason": (
            f"{sem_dado} cluster(s) sem o add-on Container Insights; "
            "a ausência está registrada na Bronze, sem inferência."
        ) if sem_dado else None,
    }
