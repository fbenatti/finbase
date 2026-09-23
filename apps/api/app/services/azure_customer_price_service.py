"""Price Sheet contratual (MCA) do Blob de export.

O export do Cost Management entrega o preço **contratado** por medidor, produto e
região. É a fonte preferencial do contrato de preço do plano: retail é apenas
fallback indicativo, e os dois não podem se misturar.

O caminho do export é mensal e a pasta carrega o intervalo:

    reports/report_-pricesheet/{YYYYMMDD}-{YYYYMMDD}/<export_id>/part_N_0001.csv

O mês é parâmetro de configuração (`price_sheet_period`), então carregar outro
mês não exige código novo. A origem é grande (o mês do 4P tem ~1,5 GB e 3,6 M
linhas), por isso a leitura é em blocos e a Bronze é gravada por parte.
"""
from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterator

import pandas as pd

logger = logging.getLogger(__name__)

BRONZE_TABLE = "azure_customer_price"
PRICE_SHEET_PREFIX = "reports/report_-pricesheet"
CSV_ENCODING = "utf-8-sig"          # o export vem com BOM
CHUNK_ROWS = 200_000

# Colunas que interessam ao modelo canônico. Ler só estas evita carregar 25
# colunas de 3,6 M linhas em memória.
COLUNAS = (
    "BillingAccountId", "BillingProfileId", "ServiceFamily", "Product",
    "ProductId", "SkuId", "UnitOfMeasure", "MeterId", "MeterName",
    "MeterType", "MeterCategory", "MeterSubCategory", "MeterRegion",
    "TierMinimumUnits", "EffectiveStartDate", "EffectiveEndDate",
    "UnitPrice", "BasePrice", "MarketPrice", "Currency", "BillingCurrency",
    "Term", "PriceType",
)


def _periodo_para_pasta(periodo: str) -> str:
    """`202609` -> `20260901-20260930` (intervalo do export mensal)."""
    texto = str(periodo or "").strip()
    if len(texto) != 6 or not texto.isdigit():
        raise ValueError(f"Período inválido para o price sheet: {periodo!r} (esperado YYYYMM)")
    ano, mes = int(texto[:4]), int(texto[4:])
    ultimo = (pd.Timestamp(year=ano, month=mes, day=1) + pd.offsets.MonthEnd(0)).day
    return f"{ano:04d}{mes:02d}01-{ano:04d}{mes:02d}{ultimo:02d}"


def resolve_period(connection, config: dict[str, Any]) -> str:
    """Período do price sheet: configuração da conexão ou mês corrente.

    O plano pede que o mês seja parâmetro — assim a carga de um mês específico
    não exige mudança de código nem de conexão.
    """
    explicito = str(config.get("price_sheet_period") or "").strip()
    if explicito:
        return explicito
    return datetime.now(timezone.utc).strftime("%Y%m")


def _listar_partes(container, periodo: str) -> tuple[str, list[str]]:
    """Devolve a pasta do export e os CSVs dela."""
    prefixo = f"{PRICE_SHEET_PREFIX}/{_periodo_para_pasta(periodo)}/"
    partes: list[str] = []
    for blob in container.list_blobs(name_starts_with=prefixo):
        if blob.name.endswith(".csv"):
            partes.append(blob.name)
    partes.sort()
    return prefixo, partes


def _ler_csv_em_blocos(container, blob_name: str) -> Iterator[pd.DataFrame]:
    """Lê um CSV do export em blocos, só com as colunas de interesse."""
    dados = container.download_blob(blob_name).readall()
    texto = io.StringIO(dados.decode(CSV_ENCODING, errors="replace"))
    for bloco in pd.read_csv(
        texto, dtype=str, chunksize=CHUNK_ROWS, low_memory=False,
        usecols=lambda c: c in COLUNAS,
    ):
        yield bloco


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


def discover_price_sheet(config: dict[str, Any], connection) -> dict[str, Any]:
    """Inspeção sem coleta: o que existe no mês configurado."""
    from app.services.azure_extractor import AzureExtractor

    periodo = resolve_period(connection, config)
    ex = AzureExtractor(connection)
    svc = ex._get_blob_service_client()
    container = svc.get_container_client(config["container_name"])
    prefixo, partes = _listar_partes(container, periodo)
    return {
        "period": periodo,
        "prefix": prefixo,
        "files": len(partes),
        "names": [p.split("/")[-1] for p in partes],
    }


def run_customer_price_collection(
    session,
    connection_id: int,
    *,
    task_id: int | None = None,
    source_code: str | None = None,
) -> dict[str, Any]:
    """Carrega o price sheet contratual do período configurado."""
    from app.database import SyncSession as _Sync
    from app.models.source_connection import SourceConnection
    from app.services.azure_extractor import AzureExtractor

    s = session or _Sync()
    conn = s.get(SourceConnection, connection_id)
    if not conn:
        raise ValueError(f"Conexão {connection_id} não encontrada")

    config = dict(conn.config_json or {})
    periodo = resolve_period(conn, config)
    ex = AzureExtractor(conn)
    svc = ex._get_blob_service_client()
    container = svc.get_container_client(config["container_name"])

    prefixo, partes = _listar_partes(container, periodo)
    if not partes:
        raise ValueError(
            f"Price sheet não encontrado para {periodo} em {prefixo} — "
            "confira `price_sheet_period` e o container."
        )

    total = 0
    tipos: dict[str, int] = {}
    for indice, parte in enumerate(partes, start=1):
        if task_id is not None:
            try:
                from app.models.etl_task import EtlTask

                tarefa = s.get(EtlTask, task_id)
                if tarefa:
                    tarefa.phase = f"Price sheet contratual: parte {indice}/{len(partes)}"
                    s.commit()
            except Exception:  # noqa: BLE001
                s.rollback()
        # Acumula a parte inteira antes de gravar. Gravar por bloco com o mesmo
        # `blob_path` fazia o `delete_bronze_slice` de cada bloco apagar o
        # anterior — sobrava só o último, e a carga perdia ~80% das linhas em
        # silêncio (667.019 de 3.667.019).
        acumulado: list[pd.DataFrame] = []
        for bloco in _ler_csv_em_blocos(container, parte):
            if bloco.empty:
                continue
            if "PriceType" in bloco.columns:
                for tipo, quantidade in bloco["PriceType"].value_counts().items():
                    tipos[str(tipo)] = tipos.get(str(tipo), 0) + int(quantidade)
            acumulado.append(bloco)
        if not acumulado:
            continue
        parte_frame = pd.concat(acumulado, ignore_index=True)
        # A fatia é por parte e período, para reexecutar substituir.
        blob_path = f"{BRONZE_TABLE}/{periodo}/{parte.split('/')[-1]}"
        total += _write_bronze(s, conn, parte_frame, periodo, blob_path, source_code=source_code)

    s.commit()
    return {
        "connection_id": connection_id,
        "rows_bronze": total,
        "period": periodo,
        "files": len(partes),
        "price_types": tipos,
        "bronze_table": f"raw_{(source_code or conn.code).replace('-', '_')}",
    }
