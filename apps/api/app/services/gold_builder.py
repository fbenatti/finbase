"""Dimon agregada gerada DENTRO do reprocess (reutiliza os DataFrames em memória).

Ao final do reprocessamento do destino MODELO FOCUS, além de publicar a Gold,
este módulo acumula a agregação das mesmas linhas já processadas em memória e
publica a tabela pré-agregada ``dest_dm_cost_focus``:

- Apenas as principais colunas (granularidade mensal em ``_billing_period``);
- Medidas somadas: ``quantity`` (ConsumedQuantity), ``realizado`` (BRL),
  ``billedcost`` (BilledCost — moeda de billing).

A publicação é feita por fatia ``(_billing_period, source_connection_id)`` com
semântica atômica de substituição (delete + insert na mesma transação Iceberg),
nunca sobrescrevendo fatias de outros meses/conexões. Se a publicação
incremental falhar, um rebuild completo a partir da Gold é tentado como
fallback para manter a Dimon consistente.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import pandas as pd

from app.services import iceberg_service as iceberg

logger = logging.getLogger(__name__)

# Tabela Dimon principal gerada ao final do reprocess do MODELO FOCUS.
MART_TABLE = os.getenv("DIMON_GOLD_TABLE", "dest_dm_cost_focus")
DIMON_GOLD_SOURCE = os.getenv("DIMON_GOLD_SOURCE", "dest_modelo_focus")

# Dimensões analíticas (colunas físicas da Gold, minúsculas). A agregação usa
# somente as dimensões presentes no frame processado — as demais entram nulas,
# mantendo o schema estável da tabela Dimon.
#
# A Dimon é propositalmente MENOS granular (uma única tabela full agrupada):
# mantém o drilldown organizacional (níveis 1..5), cloud, serviço/categoria,
# região e tipo de carga — e descarta colunas de alta cardinalidade que
# explodiriam a contagem de linhas (SKU, subcategoria, subconta, centro de
# custo, ambiente, níveis 6/7). ``source_connection_id`` permanece como chave
# de fatia para a publicação atômica mês × conexão.
DIMON_DIMENSIONS: tuple[str, ...] = (
    "_billing_period",
    "source_connection_id",
    "cloud_origem",
    "departamento_nivel_1",
    "departamento_nivel_2",
    "departamento_nivel_3",
    "departamento_nivel_4",
    "departamento_nivel_5",
    "servicecategory",
    "servicename",
    "regionname",
    "chargecategory",
)

# Medidas agregadas: nome na Dimon -> coluna física de origem (Gold/frame).
DIMON_MEASURES: dict[str, str] = {
    "quantity": "ConsumedQuantity",
    "realizado": "realizado",
    "billedcost": "BilledCost",
}

DIMON_COLUMN_TYPES: dict[str, str] = {
    "_billing_period": "TEXT",
    "source_connection_id": "BIGINT",
    "cloud_origem": "TEXT",
    "departamento_nivel_1": "TEXT",
    "departamento_nivel_2": "TEXT",
    "departamento_nivel_3": "TEXT",
    "departamento_nivel_4": "TEXT",
    "departamento_nivel_5": "TEXT",
    "servicecategory": "TEXT",
    "servicename": "TEXT",
    "regionname": "TEXT",
    "chargecategory": "TEXT",
    "quantity": "DOUBLE",
    "realizado": "DOUBLE",
    "billedcost": "DOUBLE",
}


def _lookup(frame: pd.DataFrame) -> dict[str, str]:
    """Mapeamento nome-minúsculo -> nome físico da coluna no frame."""
    return {str(col).lower(): str(col) for col in frame.columns}


def aggregate_frame(frame: pd.DataFrame) -> pd.DataFrame | None:
    """Agrega um frame processado (contrato Gold) na granularidade da Dimon.

    Retorna um DataFrame pequeno com todas as dimensões + medidas (colunas
    ausentes preenchidas com None) ou ``None`` quando não há nada a agregar.
    """
    if frame is None or frame.empty:
        return None
    lookup = _lookup(frame)
    present_dims = [d for d in DIMON_DIMENSIONS if d in lookup]
    if not present_dims:
        return None
    dim_cols = [lookup[d] for d in present_dims]

    measures: dict[str, pd.Series] = {}
    for name, source_col in DIMON_MEASURES.items():
        physical = lookup.get(source_col.lower())
        if physical and physical in frame.columns:
            measures[name] = pd.to_numeric(frame[physical], errors="coerce").fillna(0.0)
        else:
            measures[name] = pd.Series(0.0, index=frame.index)

    group = frame[dim_cols].copy()
    group["__q"] = measures["quantity"]
    group["__r"] = measures["realizado"]
    group["__b"] = measures["billedcost"]
    grouped = group.groupby(dim_cols, dropna=False)[["__q", "__r", "__b"]].sum(min_count=1)
    grouped = grouped.reset_index()

    out = pd.DataFrame({d: None for d in DIMON_DIMENSIONS}, index=range(len(grouped)))
    for d in present_dims:
        out[d] = grouped[lookup[d]].values
    out["quantity"] = grouped["__q"].fillna(0.0).values
    out["realizado"] = grouped["__r"].fillna(0.0).values
    out["billedcost"] = grouped["__b"].fillna(0.0).values
    # source_connection_id deve permanecer BIGINT (nunca float64 com NaN).
    out["source_connection_id"] = pd.to_numeric(
        out["source_connection_id"], errors="coerce"
    ).astype("Int64")
    return out


def publish_marts(chunks: list[pd.DataFrame], *, table: str | None = None) -> int:
    """Publica (ou substitui por fatia) a Dimon a partir dos chunks acumulados.

    Chunks são os resultados de ``aggregate_frame`` acumulados durante o
    reprocess — concatena, agrupa novamente (por segurança) e publica.
    Retorna o número de linhas publicadas na tabela Dimon.
    """
    table = (table or MART_TABLE).replace("-", "_").replace(".", "_")
    frames = [c for c in (chunks or []) if c is not None and not c.empty]
    if not frames:
        return 0
    merged = pd.concat(frames, ignore_index=True)
    dims = [d for d in DIMON_DIMENSIONS if d in merged.columns]
    measures = [m for m in DIMON_MEASURES if m in merged.columns]
    if not dims or not measures:
        return 0
    agg = merged.groupby(dims, dropna=False)[measures].sum(min_count=1).reset_index()
    for m in measures:
        agg[m] = agg[m].fillna(0.0)
    agg["source_connection_id"] = pd.to_numeric(
        agg["source_connection_id"], errors="coerce"
    ).astype("Int64")
    agg["_billing_period"] = agg["_billing_period"].astype(str)
    final = agg[[*dims, *measures]].where(pd.notna(agg[[*dims, *measures]]), None)
    return _publish_frame(table, final)


def _publish_frame(table: str, frame: pd.DataFrame) -> int:
    """Escreve o frame agregado na Dimon com publicação atômica por fatia."""
    if frame.empty:
        return 0
    namespace = "gold"
    tid = iceberg._table_id(namespace, table)
    catalog = iceberg._get_catalog()
    try:
        catalog.load_table(tid)
        target_exists = True
    except Exception:
        target_exists = False

    temp_name = iceberg.write_destination_staged(
        table,
        frame,
        partition_col="_billing_period",
        column_types=dict(DIMON_COLUMN_TYPES),
        namespace=namespace,
    )
    if not target_exists:
        # Primeira publicação: renomear o staging inteiro é a troca atômica
        # mais barata e preserva a partition spec.
        return iceberg.swap_staged_table(
            table, temp_name, namespace=namespace, known_row_count=len(frame)
        )

    total = 0
    grouped = frame.groupby(["_billing_period", "source_connection_id"], dropna=False)
    for (bperiod, cid), group in grouped:
        slice_temp = iceberg.write_destination_staged(
            table,
            group,
            partition_col="_billing_period",
            column_types=dict(DIMON_COLUMN_TYPES),
            namespace=namespace,
        )
        try:
            total += iceberg.publish_staged_partition(
                table,
                slice_temp,
                billing_period=str(bperiod),
                connection_id=int(cid),
                namespace=namespace,
            )
        except Exception:
            try:
                iceberg.cleanup_staged_table(slice_temp, namespace=namespace)
            except Exception:  # noqa: BLE001
                pass
            raise
    try:
        iceberg.cleanup_staged_table(temp_name, namespace=namespace)
    except Exception:  # noqa: BLE001
        pass
    logger.info("Dimon %s publicada por fatia: %d linhas", table, total)
    return total


def rebuild_marts_from_gold(*, table: str | None = None, gold: str | None = None) -> int:
    """Fallback: reconstrói a Dimon inteira a partir da Gold via Trino.

    Usado quando a publicação incremental falha, garantindo consistência sem
    depender dos chunks em memória. Retorna o número de linhas escritas.
    """
    from app.services.trino_service import execute_sync

    table = (table or MART_TABLE).replace("-", "_").replace(".", "_")
    gold = (gold or DIMON_GOLD_SOURCE).replace("-", "_").replace(".", "_")

    schema_rows = execute_sync(f"DESCRIBE iceberg.silver.{gold}", timeout_s=60)
    schema = {str(r.get("Column") or "").lower() for r in schema_rows}
    dims = [d for d in DIMON_DIMENSIONS if d in schema]
    if not dims:
        raise RuntimeError(
            f"Nenhuma dimensão Dimon encontrada na Gold {gold} — rebuild impossível"
        )

    select_dims = ", ".join(dims)
    group_by = ", ".join(dims)
    sums = ", ".join(
        f"SUM(COALESCE(TRY_CAST({source} AS DOUBLE), 0)) AS {name}"
        for name, source in DIMON_MEASURES.items()
        if source.lower() in schema
    )
    if not sums:
        raise RuntimeError(f"Nenhuma medida Dimon encontrada na Gold {gold}")

    staging = f"{table}_stg_{time.time_ns()}"
    create_sql = (
        f"CREATE TABLE iceberg.gold.{staging} "
        f"WITH (partitioning = ARRAY['_billing_period']) AS "
        f"SELECT {select_dims}, {sums} FROM iceberg.silver.{gold} "
        f"GROUP BY {group_by}"
    )
    logger.info("Rebuild Dimon %s a partir de %s", table, gold)
    execute_sync(create_sql, timeout_s=3600)
    try:
        result = execute_sync(f"SELECT COUNT(*) AS row_count FROM iceberg.gold.{staging}", timeout_s=180)
        rows = int((result[0] if result else {}).get("row_count") or 0)
        if rows:
            return iceberg.swap_staged_table(table, staging, known_row_count=rows)
        iceberg.cleanup_staged_table(staging)
        return 0
    except Exception:
        try:
            iceberg.cleanup_staged_table(staging)
        except Exception:  # noqa: BLE001
            pass
        raise
