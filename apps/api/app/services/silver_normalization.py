"""Normalização da Bronze de métricas para a Silver canônica.

A Bronze guarda o que a fonte entregou; a Silver guarda o que a organização
entende. O esquema abaixo é deliberadamente **agnóstico de nuvem**: uma série de
métrica é sempre (recurso, métrica, instante, valor, qualidade), venha do Azure
Monitor, do CloudWatch ou do Cloud Monitoring.

Campos de identidade seguem o grão do FOCUS onde faz sentido
(`ResourceId`, `SubAccountId`, `ResourceType`, `ResourceGroupId`, `RegionId`),
e o grão temporal usa `bucket_start_utc` em resolução explícita — a mesma
pergunta ("consumo por recurso em cada janela") tem a mesma resposta em qualquer
provider.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# Tabela Silver única para séries de métricas de qualquer cloud.
METRIC_SERIES_TABLE = "resource_metric_series"

# Grão canônico da série.
SERIES_GRAIN = ["cloud_provider", "sub_account_id", "resource_id", "metric", "bucket_start_utc"]

# Mapeamento Bronze → Silver. A ordem define a posição na tabela canônica.
# `role`: identity (chave), dimension (recorte), measure (valor), time, quality.
METRIC_SERIES_MAPPING: list[dict[str, Any]] = [
    {"bronze_field": "resource_id", "silver_field": "resource_id", "silver_type": "varchar", "role": "identity", "required": True},
    {"bronze_field": "resource_name", "silver_field": "resource_name", "silver_type": "varchar", "role": "dimension"},
    {"bronze_field": "subscription_id", "silver_field": "sub_account_id", "silver_type": "varchar", "role": "identity", "required": True,
     "notes": "FOCUS SubAccountId: assinatura, conta ou projeto conforme a cloud."},
    {"bronze_field": "resource_group", "silver_field": "resource_group_id", "silver_type": "varchar", "role": "dimension",
     "notes": "FOCUS ResourceGroupId; vazio quando a cloud não tem agrupamento equivalente."},
    {"bronze_field": "location", "silver_field": "region_id", "silver_type": "varchar", "role": "dimension",
     "notes": "FOCUS RegionId."},
    {"bronze_field": "vm_size", "silver_field": "resource_sku", "silver_type": "varchar", "role": "dimension"},
    {"bronze_field": "os_type", "silver_field": "operating_system", "silver_type": "varchar", "role": "dimension"},
    {"bronze_field": "power_state", "silver_field": "resource_state", "silver_type": "varchar", "role": "dimension"},
    {"bronze_field": "metric", "silver_field": "metric", "silver_type": "varchar", "role": "identity", "required": True},
    {"bronze_field": "unit", "silver_field": "metric_unit", "silver_type": "varchar", "role": "quality"},
    {"bronze_field": "dimension", "silver_field": "dimensions_json", "silver_type": "varchar", "role": "dimension",
     "notes": "Dimensões da métrica serializadas; o conjunto varia por provider."},
    {"bronze_field": "bucket_utc", "silver_field": "bucket_start_utc", "silver_type": "timestamp", "role": "time", "required": True},
    {"bronze_field": "average", "silver_field": "value_average", "silver_type": "decimal", "role": "measure", "unit": "native"},
    {"bronze_field": "minimum", "silver_field": "value_minimum", "silver_type": "decimal", "role": "measure", "unit": "native"},
    {"bronze_field": "maximum", "silver_field": "value_maximum", "silver_type": "decimal", "role": "measure", "unit": "native"},
    {"bronze_field": "total", "silver_field": "value_total", "silver_type": "decimal", "role": "measure", "unit": "native"},
    {"bronze_field": "count", "silver_field": "sample_count", "silver_type": "bigint", "role": "measure"},
    {"bronze_field": "quality_status", "silver_field": "quality_status", "silver_type": "varchar", "role": "quality", "required": True,
     "notes": "OK | GAP | NOT_AVAILABLE. Ausência nunca vira zero."},
    {"bronze_field": "gap_reason", "silver_field": "quality_reason", "silver_type": "varchar", "role": "quality"},
    {"bronze_field": "api_version", "silver_field": "source_api_version", "silver_type": "varchar", "role": "quality"},
    {"bronze_field": "_blob_path", "silver_field": "_bronze_blob_path", "silver_type": "varchar", "role": "quality",
     "notes": "Rastreabilidade: de qual fatia da Bronze a linha veio."},
    {"bronze_field": "_billing_period", "silver_field": "_billing_period", "silver_type": "varchar", "role": "quality"},
]

# Coluna canônica de medida; a primeira disponível é o valor da série.
MEASURE_PREFERENCE = ["value_average", "value_total", "value_maximum", "value_minimum"]

_NUMERIC_TYPES = {"decimal", "bigint", "double", "integer"}


def silver_columns() -> list[tuple[str, str]]:
    """Colunas canônicas na ordem, com o tipo."""
    cols = [("cloud_provider", "varchar"), ("resource_type", "varchar")]
    for item in METRIC_SERIES_MAPPING:
        if item["silver_field"] not in {c for c, _ in cols}:
            cols.append((item["silver_field"], item["silver_type"]))
    cols.append(("value", "decimal"))
    return cols


def _to_decimal(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def normalise_metric_frame(
    frame: pd.DataFrame,
    *,
    cloud_provider: str = "azure",
    resource_type: str = "microsoft.compute/virtualmachines",
) -> pd.DataFrame:
    """Converte a Bronze de métricas no esquema canônico da Silver.

    Não inventa dado: ponto sem valor continua sem valor e mantém a sua
    qualidade (`GAP`), para que a ausência não seja lida como consumo zero.
    """
    if frame is None or frame.empty:
        return pd.DataFrame(columns=[c for c, _ in silver_columns()])

    out = pd.DataFrame(index=frame.index)
    out["cloud_provider"] = cloud_provider
    out["resource_type"] = resource_type

    for item in METRIC_SERIES_MAPPING:
        origem, destino = item["bronze_field"], item["silver_field"]
        if destino in out.columns:
            continue
        if origem not in frame.columns:
            out[destino] = None
            if item.get("required"):
                raise ValueError(
                    f"Campo obrigatório ausente na Bronze: {origem} -> {destino}"
                )
            continue
        valores = frame[origem]
        if item["silver_type"] in _NUMERIC_TYPES:
            out[destino] = _to_decimal(valores)
        else:
            out[destino] = valores.astype("string")

    # O valor canônico da série é a primeira medida disponível.
    out["value"] = None
    for candidato in MEASURE_PREFERENCE:
        if candidato in out.columns:
            faltante = out["value"].isna()
            if not faltante.any():
                break
            out.loc[faltante, "value"] = out.loc[faltante, candidato]

    # O Iceberg (via PyIceberg) não aceita timestamp em nanossegundos; a série
    # é de 15 minutos, então microssegundos é resolução de sobra.
    out["bucket_start_utc"] = pd.to_datetime(
        out["bucket_start_utc"], errors="coerce", utc=True
    ).dt.tz_localize(None).astype("datetime64[us]")
    if "sample_count" in out.columns:
        out["sample_count"] = pd.to_numeric(out["sample_count"], errors="coerce").astype("Int64")

    out["_silver_ingested_at"] = datetime.now(timezone.utc).isoformat()
    return out.reset_index(drop=True)


def mapping_rows(source_key: str, bronze_table: str, silver_table: str = METRIC_SERIES_TABLE) -> list[dict[str, Any]]:
    """Linhas para registrar o correlacionamento na tabela de mapeamento."""
    rows = [{
        "source_key": source_key, "bronze_table": bronze_table, "silver_table": silver_table,
        "bronze_field": "", "silver_field": "cloud_provider", "silver_type": "varchar",
        "role": "identity", "required": True, "notes": "Constante da fonte no normalizador.",
    }, {
        "source_key": source_key, "bronze_table": bronze_table, "silver_table": silver_table,
        "bronze_field": "", "silver_field": "resource_type", "silver_type": "varchar",
        "role": "identity", "required": True, "notes": "Tipo do recurso na cloud de origem.",
    }]
    for item in METRIC_SERIES_MAPPING:
        rows.append({
            "source_key": source_key, "bronze_table": bronze_table, "silver_table": silver_table,
            "bronze_field": item["bronze_field"], "silver_field": item["silver_field"],
            "silver_type": item["silver_type"], "role": item["role"],
            "required": bool(item.get("required")), "unit": item.get("unit"),
            "notes": item.get("notes"),
        })
    return rows


def materialise_metric_series(session, *, source_key: str, bronze_table: str, cloud_provider: str = "azure") -> dict[str, Any]:
    """Lê a Bronze da fonte e grava a Silver canônica `resource_metric_series`.

    A escrita é idempotente por período: reprocessar o mesmo mês substitui as
    linhas daquela fatia em vez de duplicar. O mapeamento Bronze → Silver fica
    registrado em `bronze_silver_mapping`, então a equivalência entre fontes de
    clouds diferentes é auditável — não uma regra escondida no código.
    """
    from app.services import iceberg_service as iceberg
    from app.services import lakehouse_layers as layers

    catalog = iceberg._get_catalog()
    try:
        bronze = catalog.load_table((layers.BRONZE, bronze_table)).scan().to_pandas()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Bronze {bronze_table} ilegível: {exc}") from exc

    if bronze.empty:
        return {"source_key": source_key, "rows_silver": 0, "periods": [], "message": "Bronze vazia"}

    frame = normalise_metric_frame(bronze, cloud_provider=cloud_provider)
    frame["_billing_period"] = frame["_billing_period"].fillna("").astype(str)

    written = 0
    periods: list[str] = []
    for period, chunk in frame.groupby("_billing_period", dropna=False):
        period_key = str(period or "")
        if not period_key:
            continue
        rows = iceberg.write_silver(
            connection_code=source_key,
            df=chunk.drop(columns=["_billing_period"]),
            billing_period=period_key,
            table_name_override=f"silver.{METRIC_SERIES_TABLE}",
        )
        written += int(rows or 0)
        periods.append(period_key)

    _register_mapping(session, source_key=source_key, bronze_table=bronze_table)
    return {
        "source_key": source_key,
        "silver_table": METRIC_SERIES_TABLE,
        "rows_silver": written,
        "periods": sorted(periods),
        "rows_with_value": int(frame["value"].notna().sum()),
        "rows_without_value": int(frame["value"].isna().sum()),
    }


def _register_mapping(session, *, source_key: str, bronze_table: str) -> None:
    """Grava o correlacionamento da fonte (idempotente)."""
    from sqlalchemy import text

    for row in mapping_rows(source_key, bronze_table):
        session.execute(
            text("""
                INSERT INTO bronze_silver_mapping
                    (source_key, bronze_table, silver_table, bronze_field, silver_field,
                     silver_type, unit, role, required, notes)
                VALUES
                    (:source_key, :bronze_table, :silver_table, :bronze_field, :silver_field,
                     :silver_type, :unit, :role, :required, :notes)
                ON CONFLICT (source_key, bronze_field) DO UPDATE SET
                    bronze_table = EXCLUDED.bronze_table,
                    silver_table = EXCLUDED.silver_table,
                    silver_field = EXCLUDED.silver_field,
                    silver_type = EXCLUDED.silver_type,
                    unit = EXCLUDED.unit, role = EXCLUDED.role,
                    required = EXCLUDED.required, notes = EXCLUDED.notes,
                    updated_at = now()
            """),
            {**row, "unit": row.get("unit"), "notes": row.get("notes")},
        )
    session.commit()
