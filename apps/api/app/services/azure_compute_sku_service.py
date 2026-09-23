"""Catálogo de SKUs de compute do Azure (Resource Skus).

`Microsoft.Compute/skus` responde o que a assinatura **pode** criar, por região,
com capacidades (vCPU, memória, discos) e restrições. É o que dá elegibilidade:
sem isto um candidato pode ser sugerido num SKU que a região ou a assinatura não
permite — o aceite do P7 proíbe exatamente isso.

A API não pagina por `$skip`; ela devolve `NextLink`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

ARM = "https://management.azure.com"
SKUS_API_VERSION = "2021-07-01"
BRONZE_TABLE = "azure_compute_skus"
MAX_PAGES = 80

# Capacidades que interessam para rightsizing. O nome na API é livre, então a
# leitura é por chave tolerante a variação de caixa.
CAPACIDADES: dict[str, str] = {
    "vcpus": "vCPUs",
    "memorygb": "MemoryGB",
    "maxdatadiskcount": "MaxDataDiskCount",
    "premiumio": "PremiumIO",
    "acceleratednetworkingenabled": "AcceleratedNetworkingEnabled",
    "cpuarchitecture": "CpuArchitecture",
}


def _subscriptions(config: dict[str, Any]) -> list[str]:
    raw = config.get("subscription_ids") or config.get("subscriptions") or []
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.replace(",", "\n").splitlines()]
    return [str(x).strip() for x in raw if str(x).strip()]


def _capacidades(sku: dict[str, Any]) -> dict[str, Any]:
    saida: dict[str, Any] = {}
    for cap in sku.get("capabilities") or []:
        nome = str(cap.get("name") or "").lower()
        chave = CAPACIDADES.get(nome)
        if chave:
            saida[chave] = cap.get("value")
    return saida


async def collect_compute_skus(config: dict[str, Any], subscription_id: str) -> pd.DataFrame:
    """SKUs e restrições de uma assinatura, com uma linha por SKU × região."""
    from app.services.azure_resource_graph_service import arm_headers

    headers = await arm_headers(config)
    linhas: list[dict[str, Any]] = []
    url = (
        f"{ARM}/subscriptions/{subscription_id}/providers/Microsoft.Compute/skus"
        f"?api-version={SKUS_API_VERSION}"
    )
    async with httpx.AsyncClient(timeout=120.0) as cliente:
        for _ in range(MAX_PAGES):
            resposta = await cliente.get(url, headers=headers)
            resposta.raise_for_status()
            corpo = resposta.json()
            for sku in corpo.get("value") or []:
                # Só SKUs de VM: a API devolve também discos, snapshots e
                # "Aligned", que não são candidatos de rightsizing.
                if str(sku.get("resourceType") or "") != "virtualMachines":
                    continue
                nome = str(sku.get("name") or "")
                tier = str(sku.get("tier") or "")
                familia = str(sku.get("family") or "")
                caps = _capacidades(sku)
                restricoes = sku.get("restrictions") or []
                for local in sku.get("locations") or []:
                    regiao = str(local or "").lower().replace(" ", "")
                    # Restrição que cita a região (ou é global) veta o SKU ali.
                    motivos = [
                        str(r.get("reasonCode") or r.get("type") or "")
                        for r in restricoes
                        if not (r.get("restrictionInfo") or {}).get("locations")
                        or regiao
                        in [
                            str(x).lower().replace(" ", "")
                            for x in ((r.get("restrictionInfo") or {}).get("locations") or [])
                        ]
                    ]
                    linhas.append({
                        "subscription_id": subscription_id,
                        "region_id": regiao,
                        "sku_name": nome,
                        "sku_tier": tier,
                        "sku_family": familia,
                        "vcpus": caps.get("vCPUs"),
                        "memory_gb": caps.get("MemoryGB"),
                        "max_data_disks": caps.get("MaxDataDiskCount"),
                        "premium_io": caps.get("PremiumIO"),
                        "accelerated_networking": caps.get("AcceleratedNetworkingEnabled"),
                        "is_available": "NAO" if motivos else "SIM",
                        "restrictions_json": str(restricoes)[:4000] if restricoes else None,
                        "restriction_reason": ", ".join(sorted(set(m for m in motivos if m)))[:400] or None,
                        "zones": None,
                    })
            url = corpo.get("nextLink")
            if not url:
                break
    logger.info("SKUs de compute: %d linha(s) em %s", len(linhas), subscription_id)
    return pd.DataFrame(linhas)


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


def run_compute_sku_collection(
    session,
    connection_id: int,
    *,
    snapshot_date: str | None = None,
    task_id: int | None = None,
    source_code: str | None = None,
) -> dict[str, Any]:
    """Coleta os SKUs de cada assinatura do escopo."""
    from app.database import SyncSession as _Sync
    from app.models.source_connection import SourceConnection
    from app.services.azure_automation_service import asyncio_run

    s = session or _Sync()
    conn = s.get(SourceConnection, connection_id)
    if not conn:
        raise ValueError(f"Conexão {connection_id} não encontrada")

    config = dict(conn.config_json or {})
    assinaturas = _subscriptions(config)
    if not assinaturas:
        raise ValueError(
            "O catálogo de SKUs é por assinatura; informe `subscription_ids` na conexão."
        )

    dia = snapshot_date or datetime.now(timezone.utc).date().isoformat()
    periodo = dia.replace("-", "")[:6]
    total = 0
    disponiveis = 0
    for assinatura in assinaturas:
        frame = asyncio_run(collect_compute_skus(config, assinatura))
        if frame.empty:
            continue
        frame["valid_from"] = dia
        if "snapshot_date" not in frame.columns:
            frame["snapshot_date"] = dia
        blob_path = f"{BRONZE_TABLE}/{dia}/{assinatura}"
        total += _write_bronze(s, conn, frame, periodo, blob_path, source_code=source_code)
        disponiveis += int((frame["is_available"] == "SIM").sum())

    s.commit()
    return {
        "connection_id": connection_id,
        "rows_bronze": total,
        "period": periodo,
        "subscriptions": len(assinaturas),
        "available_rows": disponiveis,
        "bronze_table": f"raw_{(source_code or conn.code).replace('-', '_')}",
    }
