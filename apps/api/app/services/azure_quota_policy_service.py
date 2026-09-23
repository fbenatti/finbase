"""Quota de compute e policy de região.

Duas fontes que dependem de assinatura e que o P7/P8 usam como **restrição**:

- **Quota** (`Microsoft.Compute locations/{loc}/usages`): quanto resta de cada
  família na região. Um candidato de rightsizing que caiba no SKU mas não caiba
  na quota não é elegível.
- **Policy** (`Microsoft.Authorization/policyAssignments`): o que o contrato
  proíbe — tipicamente SKU e região. É veto, não sugestão.

Ambas entram na Bronze como o provedor devolve; a decisão de elegibilidade é
derivada depois, a partir do dado.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

ARM = "https://management.azure.com"
USAGES_API_VERSION = "2024-07-01"
POLICY_API_VERSION = "2023-04-01"

QUOTA_BRONZE = "azure_compute_quota"
POLICY_BRONZE = "azure_region_policy"
MAX_PAGES = 40


def _subscriptions(config: dict[str, Any]) -> list[str]:
    raw = config.get("subscription_ids") or config.get("subscriptions") or []
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.replace(",", "\n").splitlines()]
    return [str(x).strip() for x in raw if str(x).strip()]


def _regions(config: dict[str, Any]) -> list[str]:
    raw = config.get("regions") or []
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.replace(",", "\n").splitlines()]
    # Sem lista explícita, as regiões onde já há recurso — evita varrer todas.
    return [str(x).strip().lower().replace(" ", "") for x in raw if str(x).strip()]


async def collect_quota(config: dict[str, Any], subscription_id: str, region: str) -> pd.DataFrame:
    """Uso e limite de compute de uma região."""
    from app.services.azure_resource_graph_service import arm_headers

    headers = await arm_headers(config)
    url = (
        f"{ARM}/subscriptions/{subscription_id}/providers/Microsoft.Compute"
        f"/locations/{region}/usages?api-version={USAGES_API_VERSION}"
    )
    linhas: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=90.0) as cliente:
        for _ in range(MAX_PAGES):
            resposta = await cliente.get(url, headers=headers)
            resposta.raise_for_status()
            corpo = resposta.json()
            for item in corpo.get("value") or []:
                nome = item.get("name") or {}
                linhas.append({
                    "subscription_id": subscription_id,
                    "region_id": region,
                    "quota_name": nome.get("value") if isinstance(nome, dict) else str(nome),
                    "localized_name": nome.get("localizedValue") if isinstance(nome, dict) else None,
                    "current_value": item.get("currentValue"),
                    "limit": item.get("limit"),
                    "unit": item.get("unit"),
                })
            url = corpo.get("nextLink")
            if not url:
                break
    return pd.DataFrame(linhas)


async def collect_policies(config: dict[str, Any], subscription_id: str) -> pd.DataFrame:
    """Atribuições de policy da assinatura, com os parâmetros declarados."""
    from app.services.azure_resource_graph_service import arm_headers

    headers = await arm_headers(config)
    url = (
        f"{ARM}/subscriptions/{subscription_id}/providers/Microsoft.Authorization"
        f"/policyAssignments?api-version={POLICY_API_VERSION}"
    )
    linhas: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=90.0) as cliente:
        for _ in range(MAX_PAGES):
            resposta = await cliente.get(url, headers=headers)
            resposta.raise_for_status()
            corpo = resposta.json()
            for item in corpo.get("value") or []:
                props = item.get("properties") or {}
                linhas.append({
                    "subscription_id": subscription_id,
                    "assignment_id": item.get("id"),
                    "assignment_name": item.get("name"),
                    "display_name": props.get("displayName"),
                    "policy_definition_id": props.get("policyDefinitionId"),
                    "scope": props.get("scope"),
                    "enforcement_mode": props.get("enforcementMode"),
                    "parameters_json": str(props.get("parameters") or {})[:4000],
                    # A restrição efetiva (SKU/região proibidos) sai dos parâmetros.
                    "allowed_values": str(
                        (props.get("parameters") or {}).get("listOfAllowedSKUs", {}).get("value")
                        or (props.get("parameters") or {}).get("listOfAllowedLocations", {}).get("value")
                        or ""
                    )[:2000] or None,
                })
            url = corpo.get("nextLink")
            if not url:
                break
    return pd.DataFrame(linhas)


def _limitar_textos(frame: pd.DataFrame, limite: int = 3800) -> pd.DataFrame:
    """Trunca campos de texto longos antes da Bronze.

    A coluna física da Bronze é dimensionada pela primeira escrita, e uma
    assinatura pode devolver um parâmetro de policy bem maior que a amostra —
    o `StringDataRightTruncation` derrubava a coleta inteira por causa de um
    campo acessório. O limite fica abaixo do teto típico de `varchar`.
    """
    if frame.empty:
        return frame
    for coluna in frame.columns:
        if frame[coluna].dtype == object:
            frame[coluna] = frame[coluna].apply(
                lambda v: v[:limite] if isinstance(v, str) and len(v) > limite else v
            )
    return frame


def _write_bronze(
    session, connection, frame: pd.DataFrame, period: str, blob_path: str, *, source_code: str | None = None
) -> int:
    if frame.empty:
        return 0
    frame = _limitar_textos(frame)
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


def _coleta(session, connection_id: int, tabela: str, coletor, *, source_code, task_id) -> dict[str, Any]:
    """Executa um coletor por assinatura (e região, quando aplicável)."""
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
            f"A fonte é por assinatura; informe `subscription_ids` na conexão."
        )

    dia = datetime.now(timezone.utc).date().isoformat()
    periodo = dia.replace("-", "")[:6]
    total = 0
    regioes = _regions(config) or ["brazilsouth", "eastus"]
    # Uma assinatura fora do alcance da credencial (401) é estado do ambiente,
    # não defeito: registra e segue, em vez de perder a coleta inteira.
    sem_acesso: list[dict[str, Any]] = []
    coletadas = 0
    for assinatura in assinaturas:
        try:
            if coletor is collect_quota:
                for regiao in regioes:
                    frame = asyncio_run(coletor(config, assinatura, regiao))
                    if frame.empty:
                        continue
                    frame["snapshot_date"] = dia
                    total += _write_bronze(
                        s, conn, frame, periodo,
                        f"{tabela}/{dia}/{assinatura}/{regiao}", source_code=source_code,
                    )
                    coletadas += 1
            else:
                frame = asyncio_run(coletor(config, assinatura))
                if frame.empty:
                    continue
                frame["snapshot_date"] = dia
                total += _write_bronze(
                    s, conn, frame, periodo, f"{tabela}/{dia}/{assinatura}", source_code=source_code,
                )
                coletadas += 1
        except Exception as exc:  # noqa: BLE001
            sem_acesso.append({"subscription_id": assinatura, "reason": str(exc)[:200]})
            logger.info("%s: assinatura %s sem acesso (%s)", tabela, assinatura[:8], type(exc).__name__)
    s.commit()
    return {
        "connection_id": connection_id,
        "rows_bronze": total,
        "period": periodo,
        "subscriptions": len(assinaturas),
        "subscriptions_collected": coletadas,
        "subscriptions_without_access": len(sem_acesso),
        # Estado honesto sobre a cobertura, como o plano exige.
        "status": "PARTIAL" if sem_acesso else "AVAILABLE",
        "status_reason": (
            f"{len(sem_acesso)} assinatura(s) fora do alcance da credencial."
            if sem_acesso else None
        ),
        "bronze_table": f"raw_{(source_code or conn.code).replace('-', '_')}",
    }


def run_compute_quota_collection(session, connection_id: int, *, task_id=None, source_code=None) -> dict[str, Any]:
    return _coleta(session, connection_id, QUOTA_BRONZE, collect_quota, source_code=source_code, task_id=task_id)


def run_region_policy_collection(session, connection_id: int, *, task_id=None, source_code=None) -> dict[str, Any]:
    return _coleta(session, connection_id, POLICY_BRONZE, collect_policies, source_code=source_code, task_id=task_id)
