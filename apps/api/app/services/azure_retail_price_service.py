"""Catálogo de preço público do Azure (Retail Prices).

API pública, sem autenticação: `prices.azure.com/api/retail/prices`. Serve como
preço de referência quando não há preço contratado — é o denominador para medir
desconto, e a base para estimar um recurso antes de ele existir.

A varredura é por serviço (`serviceName`): a API pagina por `NextPageLink` e o
escopo é grande, então o coletor registra o progresso para a task não parecer
travada.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

API = "https://prices.azure.com/api/retail/prices"
API_VERSION = "2023-01-01-preview"
PAGE_SIZE = 1000
BRONZE_TABLE = "azure_retail_price"

# Serviços que interessam ao custo de infraestrutura. A API aceita um
# `serviceName` por chamada, então a lista define quantas passagens serão feitas.
DEFAULT_SERVICES: tuple[str, ...] = (
    "Virtual Machines",
    "Storage",
    "Bandwidth",
    "Azure Kubernetes Service",
    "Container Registry",
    "SQL Database",
    "Azure Cosmos DB",
    "App Service",
    "Functions",
    "Azure Monitor",
    "Key Vault",
    "Azure DNS",
    "Load Balancer",
    "Virtual Network",
    "Backup",
    "Azure Database for PostgreSQL",
    "Azure Database for MySQL",
    "Cognitive Services",
    "Machine Learning",
)

DEFAULT_CURRENCIES: tuple[str, ...] = ("BRL", "USD")

# Guarda de segurança: a API é pública e poderia paginar indefinidamente.
MAX_PAGES_PER_QUERY = 40


def _config_list(config: dict[str, Any], chave: str, padrao: tuple[str, ...]) -> tuple[str, ...]:
    valor = config.get(chave)
    if not valor:
        return padrao
    if isinstance(valor, str):
        valor = [p.strip() for p in valor.replace(",", "\n").splitlines()]
    itens = tuple(str(v).strip() for v in valor if str(v).strip())
    return itens or padrao


async def collect_prices(
    config: dict[str, Any],
    *,
    progress: Callable[[str], None] | None = None,
) -> pd.DataFrame:
    """Varre o catálogo público e devolve os itens no formato da API."""
    servicos = _config_list(config, "services", DEFAULT_SERVICES)
    moedas = _config_list(config, "currencies", DEFAULT_CURRENCIES)
    linhas: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=90.0) as cliente:
        for moeda in moedas:
            for servico in servicos:
                # Paginação por `$skip`: seguir o `NextPageLink` da API junto
                # com `$top` produz URL inválida (`$top=-1000`), então o
                # deslocamento é controlado aqui.
                base = (
                    f"{API}?api-version={API_VERSION}&currencyCode={moeda}"
                    f"&$filter=serviceName eq '{servico}'"
                )
                deslocamento = 0
                for _ in range(MAX_PAGES_PER_QUERY):
                    resposta = await cliente.get(f"{base}&$skip={deslocamento}")
                    resposta.raise_for_status()
                    corpo = resposta.json()
                    itens = corpo.get("Items") or []
                    for item in itens:
                        item["_queried_currency"] = moeda
                        item["_queried_service"] = servico
                    linhas.extend(itens)
                    if len(itens) < PAGE_SIZE:
                        break
                    deslocamento += PAGE_SIZE
                if progress:
                    progress(
                        f"Preço público: {servico} ({moeda}) — "
                        f"{len(linhas)} item(ns) acumulado(s)"
                    )
                logger.info(
                    "Retail Prices: %s/%s -> %d item(ns)", servico, moeda, len(linhas)
                )

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


def run_retail_price_collection(
    session,
    connection_id: int,
    *,
    snapshot_date: str | None = None,
    task_id: int | None = None,
    source_code: str | None = None,
) -> dict[str, Any]:
    """Coleta o catálogo público e persiste na Bronze, ancorado no dia."""
    from app.database import SyncSession as _Sync
    from app.models.source_connection import SourceConnection
    from app.services.azure_automation_service import asyncio_run

    s = session or _Sync()
    conn = s.get(SourceConnection, connection_id)
    if not conn:
        raise ValueError(f"Conexão {connection_id} não encontrada")

    config = dict(conn.config_json or {})
    dia = snapshot_date or datetime.now(timezone.utc).date().isoformat()
    periodo = dia.replace("-", "")[:6]
    blob_path = f"{BRONZE_TABLE}/{dia}"

    def _progresso(mensagem: str) -> None:
        if task_id is None:
            return
        try:
            from app.models.etl_task import EtlTask

            tarefa = s.get(EtlTask, task_id)
            if tarefa:
                tarefa.phase = mensagem[:200]
                s.commit()
        except Exception:  # noqa: BLE001 - progresso não derruba a coleta
            s.rollback()

    frame = asyncio_run(collect_prices(config, progress=_progresso))
    if frame.empty:
        s.commit()
        return {"connection_id": connection_id, "rows_bronze": 0, "blob_path": blob_path, "period": periodo}

    if "snapshot_date" not in frame.columns:
        frame["snapshot_date"] = dia
    # Data de vigência do preço: o catálogo é uma fotografia, e a Silver precisa
    # saber a partir de quando o preço vale.
    if "valid_from" not in frame.columns:
        frame["valid_from"] = dia
    rows = _write_bronze(s, conn, frame, periodo, blob_path, source_code=source_code)
    s.commit()
    return {
        "connection_id": connection_id,
        "rows_bronze": rows,
        "blob_path": blob_path,
        "period": periodo,
        "meters": int(frame["meterId"].nunique()) if "meterId" in frame.columns else 0,
        "services": int(frame["serviceName"].nunique()) if "serviceName" in frame.columns else 0,
    }
