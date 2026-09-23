"""Alocação de custo de AKS.

**O que o dado permite hoje (verificado no spike):**

| Capacidade | Disponibilidade |
|---|---|
| Clusters e node pools (Resource Graph) | 46 clusters / 242 pools |
| Add-on Cost Analysis (`azurepolicy`) | **não ativo** |
| API do Kubernetes (namespaces/pods) | sem credencial configurada |
| Container Insights | 5 de 46 clusters |

Sem add-on nem API do Kubernetes, **não há dado de namespace/pod** para alocar.
O plano é explícito: "ausência de add-on/API fica `PARTIAL/UNAVAILABLE`, **sem
inferir namespace por nome**".

O que este coletor faz, então, é a alocação que o dado sustenta: distribui o custo
do cluster pelos **node pools**, usando a capacidade de cada pool como peso —
nós são o que se paga, e a contagem por pool é declarada pelo provedor.

Atribuição a namespace/workload entra quando houver add-on ou API do Kubernetes;
a estrutura de pesos já está preparada para receber o peso por namespace no lugar
do peso por pool.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

BRONZE_TABLE = "azure_aks_cost_allocation"

# Peso de alocação: o que se paga no cluster é o nó, então a capacidade do pool
# é o denominador natural. Documentado porque é uma **escolha**, não um fato.
WEIGHT_BASIS = "node_capacity"

ALLOCATION_LEVEL = "node_pool"
ALLOCATION_STATUS_FULL = "PARTIAL"


def _normalizar_id(valor: Any) -> str | None:
    texto = str(valor or "").strip().lower()
    return texto or None


async def build_allocation_weights(config: dict[str, Any]) -> pd.DataFrame:
    """Pesos de alocação por cluster e node pool.

    Lê o inventário AKS da Bronze — que já tem uma linha por pool — e calcula a
    fatia de cada pool. O peso é normalizado a 1 por cluster, como o aceite do
    plano exige ("nenhum grupo financeiro soma peso diferente de 1").
    """
    from app.services import iceberg_service as iceberg
    from app.services import lakehouse_layers as layers

    try:
        bronze = iceberg._get_catalog().load_table((layers.BRONZE, "raw_azure_4p_aks_inventory"))
        frame = bronze.scan().to_pandas()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Inventário AKS ilegível na Bronze: {exc}") from exc

    if frame.empty:
        return pd.DataFrame()

    colunas = {str(c).lower(): c for c in frame.columns}
    col_id = colunas.get("resource_id")
    col_pool = colunas.get("pool_name")
    col_count = colunas.get("pool_count")
    col_sku = colunas.get("pool_vm_size")
    col_mode = colunas.get("pool_mode")
    if not col_id or not col_pool:
        raise RuntimeError("A Bronze de AKS não tem `resource_id`/`pool_name`; recolha o inventário.")

    linhas: list[dict[str, Any]] = []
    for cluster_id, grupo in frame.groupby(col_id, dropna=False):
        cluster = _normalizar_id(cluster_id)
        if not cluster:
            continue
        # Capacidade do pool: nº de nós. Sem ele, o pool conta 1 — nunca zero,
        # para não desaparecer da alocação.
        capacidades: list[tuple[Any, float]] = []
        for _, linha in grupo.iterrows():
            bruto = linha.get(col_count) if col_count else None
            try:
                nos = float(bruto) if bruto is not None and str(bruto) != "" else 1.0
            except (TypeError, ValueError):
                nos = 1.0
            capacidades.append((linha, max(nos, 1.0)))
        total = sum(peso for _, peso in capacidades) or 1.0
        for linha, peso in capacidades:
            linhas.append({
                "cluster_resource_id": cluster,
                "allocation_level": ALLOCATION_LEVEL,
                "allocation_key": str(linha.get(col_pool) or "sem-pool"),
                "allocation_weight": round(peso / total, 8),
                "weight_basis": WEIGHT_BASIS,
                "node_count": int(peso),
                "node_sku": str(linha.get(col_sku) or "") or None if col_sku else None,
                "pool_mode": str(linha.get(col_mode) or "") or None if col_mode else None,
                # O grão de namespace fica explicitamente ausente: inferir seria
                # inventar. Quando houver add-on/API, esta coluna é preenchida.
                "namespace": None,
                "allocation_status": ALLOCATION_STATUS_FULL,
                "status_reason": (
                    "Alocação por node pool. Namespace/workload indisponível: "
                    "sem add-on Cost Analysis e sem API do Kubernetes."
                ),
                "weight_validated": "SIM",
            })
    return pd.DataFrame(linhas)


def validate_weights(frame: pd.DataFrame) -> list[str]:
    """Confere que cada cluster soma peso 1 — aceite do P4."""
    problemas: list[str] = []
    if frame.empty:
        return problemas
    for cluster, grupo in frame.groupby("cluster_resource_id"):
        soma = float(grupo["allocation_weight"].sum())
        if abs(soma - 1.0) > 1e-4:
            problemas.append(f"{cluster}: soma dos pesos = {soma}")
    return problemas


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


def run_aks_cost_allocation_collection(
    session,
    connection_id: int,
    *,
    task_id: int | None = None,
    source_code: str | None = None,
) -> dict[str, Any]:
    """Calcula os pesos de alocação e persiste na Bronze."""
    from app.database import SyncSession as _Sync
    from app.models.source_connection import SourceConnection
    from app.services.azure_automation_service import asyncio_run

    s = session or _Sync()
    conn = s.get(SourceConnection, connection_id)
    if not conn:
        raise ValueError(f"Conexão {connection_id} não encontrada")

    frame = asyncio_run(build_allocation_weights(dict(conn.config_json or {})))
    if frame.empty:
        s.commit()
        return {"connection_id": connection_id, "rows_bronze": 0,
                "status": "UNAVAILABLE",
                "reason": "Sem inventário AKS; colete `aks_inventory` antes."}

    problemas = validate_weights(frame)
    if problemas:
        # Peso que não fecha é defeito, não aviso: alocaria custo errado.
        raise RuntimeError("Pesos de alocação inválidos: " + "; ".join(problemas[:5]))

    dia = datetime.now(timezone.utc).date().isoformat()
    periodo = dia.replace("-", "")[:6]
    frame["snapshot_date"] = dia
    rows = _write_bronze(
        s, conn, frame, periodo, f"{BRONZE_TABLE}/{dia}", source_code=source_code
    )
    s.commit()
    return {
        "connection_id": connection_id,
        "rows_bronze": rows,
        "period": periodo,
        "clusters": int(frame["cluster_resource_id"].nunique()),
        "pools": int(len(frame)),
        "allocation_level": ALLOCATION_LEVEL,
        "weight_basis": WEIGHT_BASIS,
        # Estado honesto: a alocação cobre pool, não namespace.
        "status": "PARTIAL",
        "status_reason": (
            "Alocação por node pool. Namespace/workload exige add-on Cost Analysis "
            "ou API do Kubernetes — nenhum disponível nesta assinatura."
        ),
    }
