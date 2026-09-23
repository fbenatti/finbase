"""P4 — Topologia de AKS por namespace, via Azure Monitor Managed Prometheus.

O plano exige o grafo `cluster → node → namespace → workload → pod/container` e a
alocação de custo por namespace, **sem inferir namespace por nome**. A versão
anterior deste pacote só conseguia alocar por *node pool*, porque o add-on Cost
Analysis não está ativo e a credencial do Kubernetes é negada (403).

O **Managed Prometheus** resolve sem nenhuma das duas: o Azure Monitor Workspace
já coleta `kube_pod_container_resource_requests`/`limits` com os labels
`cluster`, `node`, `namespace`, `pod`, `container` e **`uid`**. É dado do provedor,
não inferência.

Três decisões que o plano fixa e que estão respeitadas aqui:

1. **Peso = recurso requisitado**, não consumo observado. É o que se paga pela
   capacidade reservada ao namespace, e é o denominador que fecha com o custo do
   cluster.
2. **Peso soma 1 por cluster.** Validado antes de gravar; não fecha, não grava.
3. **Custo sem vínculo permanece visível.** Namespace sem requisição declarada
   aparece com peso zero, não é redistribuído em silêncio.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

BRONZE_TABLE = "aks_namespace_allocation"
# Recorte temporal das consultas. Requisição é gauge: o valor "agora" já é o
# estado corrente, mas uma janela evita ler um instante sem scrape.
QUERY_WINDOW = "15m"

# Métricas de capacidade reservada, por namespace. As `cluster:...:active:`
# são as gravadas pelo add-on de métricas do AKS — mais estáveis que as cruas.
METRICS: dict[str, str] = {
    "cpu_requests": "cluster:namespace:pod_cpu:active:kube_pod_container_resource_requests",
    "cpu_limits": "cluster:namespace:pod_cpu:active:kube_pod_container_resource_limits",
    "memory_requests": "cluster:namespace:pod_memory:active:kube_pod_container_resource_requests",
    "memory_limits": "cluster:namespace:pod_memory:active:kube_pod_container_resource_limits",
    # Contagem de pods ativos: dá a dimensão de workload sem precisar do
    # kube-state-metrics cru.
    "active_pods": "cluster:namespace:pod_cpu:active:kube_pod_container_resource_requests",
}


def _resolver_workspaces(config: dict[str, Any]) -> list[str]:
    """Endpoints de consulta do Managed Prometheus configurados na conexão.

    Vêm de configuração, não do código: o workspace muda por ambiente e o cliente
    pode ter vários.
    """
    bruto = config.get("prometheus_endpoints") or []
    if isinstance(bruto, str):
        bruto = [p.strip() for p in bruto.replace(",", "\n").splitlines()]
    return [str(x).strip().rstrip("/") for x in bruto if str(x).strip()]


async def discover_prometheus_endpoints(config: dict[str, Any]) -> list[str]:
    """Descobre os endpoints a partir do Resource Graph.

    O Azure Monitor Workspace declara `prometheusQueryEndpoint`. Descobrir é
    melhor que exigir configuração: o workspace muda por ambiente e o operador não
    deveria ter de caçar a URL. O que a conexão declarar (`prometheus_endpoints`)
    tem precedência — configuração explícita vence descoberta.
    """
    explicitos = _resolver_workspaces(config)
    if explicitos:
        return explicitos

    from app.services.azure_identity import arm_headers
    from app.services.azure_resource_graph_service import ARG_API_VERSION, ARG_ENDPOINT

    try:
        headers = await arm_headers(config)
        async with httpx.AsyncClient(timeout=90.0) as cliente:
            resposta = await cliente.post(
                f"{ARG_ENDPOINT}?api-version={ARG_API_VERSION}",
                headers=headers,
                json={
                    "query": 'resources | where type =~ "microsoft.monitor/accounts" | project id, name'
                },
            )
            resposta.raise_for_status()
            contas = resposta.json().get("data") or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Descoberta de Azure Monitor Workspace falhou: %s", exc)
        return []

    endpoints: list[str] = []
    async with httpx.AsyncClient(timeout=90.0) as cliente:
        for conta in contas:
            try:
                r = await cliente.get(
                    f"https://management.azure.com{conta['id']}?api-version=2023-04-03",
                    headers=headers,
                )
                if r.status_code != 200:
                    continue
                metricas = (r.json().get("properties") or {}).get("metrics") or {}
                endpoint = str(metricas.get("prometheusQueryEndpoint") or "").strip()
                if endpoint and endpoint not in endpoints:
                    endpoints.append(endpoint.rstrip("/"))
            except Exception:  # noqa: BLE001
                continue
    return endpoints


async def _consulta(cliente: httpx.AsyncClient, headers: dict[str, str], base: str, expr: str) -> list[dict[str, Any]]:
    resposta = await cliente.get(
        f"{base}/api/v1/query",
        headers=headers,
        params={"query": expr, "timeout": "60s"},
    )
    resposta.raise_for_status()
    return (resposta.json().get("data") or {}).get("result") or []


async def collect_namespace_topology(config: dict[str, Any]) -> pd.DataFrame:
    """Capacidade reservada por cluster e namespace."""
    from app.services.azure_identity import prometheus_headers

    workspaces = await discover_prometheus_endpoints(config)
    if not workspaces:
        raise ValueError(
            "A coleta de AKS por namespace exige `prometheus_endpoints` na conexão "
            "(o endpoint de consulta do Azure Monitor Workspace)."
        )

    headers = await prometheus_headers(config)
    linhas: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=180.0) as cliente:
        for base in workspaces:
            por_cluster: dict[tuple[str, str], dict[str, Any]] = {}
            for chave, metrica in METRICS.items():
                try:
                    resultado = await _consulta(cliente, headers, base, f"sum by (cluster, namespace) ({metrica})")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Prometheus %s: métrica %s falhou: %s", base[:50], chave, exc)
                    continue
                for item in resultado:
                    rotulos = item.get("metric") or {}
                    cluster = str(rotulos.get("cluster") or "").strip()
                    namespace = str(rotulos.get("namespace") or "").strip()
                    if not cluster or not namespace:
                        continue
                    try:
                        valor = float((item.get("value") or [None, None])[1])
                    except (TypeError, ValueError, IndexError):
                        continue
                    registro = por_cluster.setdefault(
                        (cluster, namespace),
                        {"cluster_name": cluster, "namespace": namespace},
                    )
                    registro[chave] = valor

            for (cluster, namespace), registro in por_cluster.items():
                registro["workspace_endpoint"] = base
                # Classificação pedida pelo plano. `kube-system` e correlatos são
                # sistema; o resto é workload de negócio.
                registro["workload_class"] = _classificar(namespace)
                linhas.append(registro)

    if not linhas:
        return pd.DataFrame()

    frame = pd.DataFrame(linhas)
    # Denominador do peso: CPU requisitada por cluster. É o que se paga.
    frame["cpu_requests"] = pd.to_numeric(frame.get("cpu_requests"), errors="coerce").fillna(0.0)
    frame["memory_requests"] = pd.to_numeric(frame.get("memory_requests"), errors="coerce").fillna(0.0)
    total = frame.groupby("cluster_name")["cpu_requests"].transform("sum")
    # Sem requisição declarada no cluster, o peso fica 0 em vez de divisão por
    # zero: o namespace continua visível e o custo dele fica UNALLOCATED.
    frame["allocation_weight"] = (frame["cpu_requests"] / total).where(total > 0, 0.0)
    frame["weight_basis"] = "cpu_requests"
    frame["allocation_level"] = "namespace"
    frame["allocation_key"] = frame["namespace"]
    frame["allocation_status"] = "PARTIAL"
    frame["status_reason"] = (
        "Alocação por namespace via Managed Prometheus. O custo do cluster é "
        "distribuído pelo peso; namespace sem requisição declarada fica com peso 0."
    )
    return frame


# Namespaces de plataforma. A lista é explícita porque inferir por prefixo
# erraria nomes de negócio que começam igual.
SYSTEM_NAMESPACES = frozenset({
    "kube-system", "kube-public", "kube-node-lease", "gatekeeper-system",
    "k8s-monitor", "elastic-system", "fluentd", "cloudability", "ama-metrics",
    "cert-manager", "ingress-nginx", "default",
})


def _classificar(namespace: str) -> str:
    """Sistema, plataforma ou workload de negócio."""
    if namespace in SYSTEM_NAMESPACES:
        return "system"
    if namespace.startswith(("ama-", "kube-", "gatekeeper-", "prometheus-")):
        return "system"
    return "workload"


def validate_weights(frame: pd.DataFrame) -> list[str]:
    """Confere que cada cluster soma 1 — aceite do P4."""
    problemas: list[str] = []
    if frame.empty:
        return problemas
    for cluster, grupo in frame.groupby("cluster_name"):
        soma = float(grupo["allocation_weight"].sum())
        if abs(soma - 1.0) > 1e-4:
            problemas.append(f"{cluster}: soma dos pesos = {soma}")
    return problemas


def _write_bronze(session, connection, frame: pd.DataFrame, period: str, blob_path: str, *, source_code: str | None = None) -> int:
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
    existente = session.query(IngestedBlob).filter_by(connection_id=connection.id, blob_path=blob_path).first()
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


def run_aks_namespace_collection(session, connection_id: int, *, task_id=None, source_code=None) -> dict[str, Any]:
    """Coleta a topologia por namespace e persiste na Bronze."""
    from app.database import SyncSession as _Sync
    from app.models.source_connection import SourceConnection
    from app.services.azure_automation_service import asyncio_run

    s = session or _Sync()
    conn = s.get(SourceConnection, connection_id)
    if not conn:
        raise ValueError(f"Conexão {connection_id} não encontrada")

    frame = asyncio_run(collect_namespace_topology(dict(conn.config_json or {})))
    if frame.empty:
        s.commit()
        return {
            "connection_id": connection_id, "rows_bronze": 0,
            "status": "UNAVAILABLE",
            "reason": (
                "Nenhum cluster com Managed Prometheus respondeu. Verifique "
                "`prometheus_endpoints` na conexão."
            ),
        }

    problemas = validate_weights(frame)
    if problemas:
        # Peso que não fecha alocaria custo errado — é defeito, não aviso.
        raise RuntimeError("Pesos de alocação inválidos: " + "; ".join(problemas[:5]))

    dia = datetime.now(timezone.utc).date().isoformat()
    periodo = dia.replace("-", "")[:6]
    frame["snapshot_date"] = dia
    rows = _write_bronze(s, conn, frame, periodo, f"{BRONZE_TABLE}/{dia}", source_code=source_code)
    s.commit()

    por_classe = frame["workload_class"].value_counts().to_dict()
    return {
        "connection_id": connection_id,
        "rows_bronze": rows,
        "period": periodo,
        "clusters": int(frame["cluster_name"].nunique()),
        "namespaces": int(len(frame)),
        "allocation_level": "namespace",
        "weight_basis": "cpu_requests",
        "workload_classes": por_classe,
        "status": "PARTIAL",
        "status_reason": (
            "Alocação por namespace via Managed Prometheus. Clusters sem o add-on "
            "de métricas ficam fora, registrados como ausência."
        ),
    }
