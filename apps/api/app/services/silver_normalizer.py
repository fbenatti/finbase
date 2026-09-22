"""Motor de normalização Bronze → Silver.

Uma fonte **não cria tabela nem código de transformação**: ela declara para qual
modelo canônico ela contribui e como os seus campos brutos chegam lá. Este motor
lê a Bronze, aplica as regras do modelo, resolve o valor canônico, grava a Silver
de forma idempotente e registra o correlacionamento para auditoria.

Adicionar uma nuvem nova é escrever um dicionário. Adicionar um tipo de dado novo
é escrever um modelo em `silver_models`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.services.silver_models import (
    ROLE_MEASURE,
    SilverModel,
    model as get_model,
)

logger = logging.getLogger(__name__)

_NUMERIC = {"decimal", "bigint", "double", "integer"}


@dataclass(frozen=True)
class SourceMapping:
    """Como uma fonte preenche um modelo canônico."""

    source_key: str
    model_name: str
    bronze_table: str
    # Coluna bruta -> coluna do modelo. Ausente = constante ou não fornecida.
    columns: dict[str, str]
    # Colunas do modelo preenchidas com valor fixo (ex.: cloud_provider).
    constants: dict[str, Any] = field(default_factory=dict)
    # Ordem de preferência para o valor canônico, quando o modelo tem um.
    measure_priority: tuple[str, ...] = ()
    # Campo do modelo usado como período de gravação; default `_billing_period`.
    period_field: str = "_billing_period"
    notes: str | None = None


# ── Registro de mapeamentos ───────────────────────────────────────────────────
# Cada entrada liga uma fonte do catálogo a um modelo. É a única coisa que uma
# fonte nova precisa declarar.

_SERIES_COMMON: dict[str, str] = {
    "resource_id": "resource_id",
    "resource_name": "resource_name",
    "subscription_id": "sub_account_id",
    "resource_group": "resource_group_id",
    "location": "region_id",
    "vm_size": "resource_sku",
    "os_type": "operating_system",
    "power_state": "resource_state",
    "metric": "metric",
    "unit": "metric_unit",
    "dimension": "dimensions_json",
    "bucket_utc": "bucket_start_utc",
    "average": "value_average",
    "minimum": "value_minimum",
    "maximum": "value_maximum",
    "total": "value_total",
    "count": "sample_count",
    "quality_status": "quality_status",
    "gap_reason": "quality_reason",
    "api_version": "source_api_version",
    "_blob_path": "_bronze_blob_path",
    "_billing_period": "_billing_period",
}

_INVENTORY_COMMON: dict[str, str] = {
    "resource_id": "resource_id",
    "resource_name": "resource_name",
    "resource_type": "resource_type",
    "subscription_id": "sub_account_id",
    "resource_group": "resource_group_id",
    "location": "region_id",
    "vm_size": "resource_sku",
    "sku_capacity": "sku_capacity",
    "power_state": "resource_state",
    "os_type": "operating_system",
    "provisioning_state": "provisioning_state",
    "zones": "availability_zones",
    "tags_json": "tags_json",
    "snapshot_date": "valid_from",
    "_billing_period": "_billing_period",
}

SOURCE_MAPPINGS: dict[str, SourceMapping] = {
    "monitor_vm_metrics": SourceMapping(
        source_key="monitor_vm_metrics",
        model_name="resource_metric_series",
        bronze_table="raw_azure_{env}_monitor_vm",
        columns=_SERIES_COMMON,
        constants={
            "cloud_provider": "azure",
            "resource_type": "microsoft.compute/virtualmachines",
        },
        measure_priority=("value_average", "value_total", "value_maximum", "value_minimum"),
        notes="Séries de plataforma das VMs (Azure Monitor batch).",
    ),
    "vm_inventory": SourceMapping(
        source_key="vm_inventory",
        model_name="resource_inventory",
        bronze_table="raw_azure_{env}_vm_inventory",
        columns=_INVENTORY_COMMON,
        constants={"cloud_provider": "azure"},
        notes="VMs e VMSS via Resource Graph.",
    ),
    "resource_graph": SourceMapping(
        source_key="resource_graph",
        model_name="resource_inventory",
        bronze_table="raw_azure_{env}_resource_graph",
        columns={
            **_INVENTORY_COMMON,
            # O inventário do Resource Graph traz o tipo em `resource_type` e a
            # fotografia em `snapshot_date`; o resto é igual ao de VM.
            "resource_type": "resource_type",
        },
        constants={"cloud_provider": "azure"},
        notes="Inventário geral e órfãos via Resource Graph.",
    ),
}

# Tipos de recurso por fonte, quando o modelo precisa e a Bronze não traz.
RESOURCE_TYPE_BY_SOURCE: dict[str, str] = {
    "vm_inventory": "microsoft.compute/virtualmachines",
}


def register(mapping: SourceMapping) -> None:
    """Declara (ou substitui) o mapeamento de uma fonte."""
    SOURCE_MAPPINGS[mapping.source_key] = mapping


def mapping_for(source_key: str) -> SourceMapping | None:
    return SOURCE_MAPPINGS.get(source_key)


def mapped_sources() -> list[str]:
    return sorted(SOURCE_MAPPINGS)


def bronze_table_name(mapping: SourceMapping, environment: str | None) -> str:
    """Resolve `{env}` no padrão da tabela Bronze."""
    env = (environment or "").strip()
    if "{env}" not in mapping.bronze_table:
        return mapping.bronze_table
    if not env:
        raise ValueError(
            f"A Bronze de '{mapping.source_key}' depende do ambiente; informe `environment`."
        )
    return mapping.bronze_table.replace("{env}", env)


def _resolve_measure(frame: pd.DataFrame, prioridade: tuple[str, ...], alvo: str) -> None:
    """Preenche o valor canônico com a primeira medida disponível."""
    frame[alvo] = None
    for candidato in prioridade:
        if candidato not in frame.columns:
            continue
        faltante = frame[alvo].isna()
        if not faltante.any():
            return
        frame.loc[faltante, alvo] = frame.loc[faltante, candidato]


def normalise(frame: pd.DataFrame, mapping: SourceMapping, silver_model: SilverModel) -> pd.DataFrame:
    """Aplica o mapeamento e devolve o quadro no esquema do modelo.

    Regras: nenhum campo obrigatório pode faltar; medida ausente continua nula
    (nunca zero); timestamp vai para microssegundos, que é o que o Iceberg aceita.
    """
    if frame is None or frame.empty:
        return pd.DataFrame(columns=silver_model.column_names())

    out = pd.DataFrame(index=frame.index)

    for destino, valor in mapping.constants.items():
        if silver_model.field(destino) is None:
            raise ValueError(f"Constante '{destino}' não existe no modelo {silver_model.name}")
        out[destino] = valor

    for origem, destino in mapping.columns.items():
        campo = silver_model.field(destino)
        if campo is None:
            raise ValueError(
                f"Mapeamento de '{mapping.source_key}': coluna '{destino}' não existe "
                f"no modelo {silver_model.name}"
            )
        if destino in out.columns:
            continue
        if origem not in frame.columns:
            out[destino] = None
            continue
        valores = frame[origem]
        out[destino] = pd.to_numeric(valores, errors="coerce") if campo.type in _NUMERIC else valores.astype("string")

    # Campos do modelo não cobertos ficam nulos (exceto os obrigatórios).
    for campo in silver_model.fields:
        if campo.name not in out.columns:
            if campo.required:
                raise ValueError(
                    f"Campo obrigatório '{campo.name}' do modelo {silver_model.name} "
                    f"não foi mapeado pela fonte '{mapping.source_key}'"
                )
            out[campo.name] = None

    if silver_model.primary_measure and mapping.measure_priority:
        _resolve_measure(out, mapping.measure_priority, silver_model.primary_measure)

    if silver_model.time_field:
        out[silver_model.time_field] = (
            pd.to_datetime(out[silver_model.time_field], errors="coerce", utc=True)
            .dt.tz_localize(None)
            .astype("datetime64[us]")
        )

    out["_silver_ingested_at"] = datetime.now(timezone.utc).isoformat()
    # Ordem do modelo, para a tabela ficar estável entre fontes.
    out = out.reindex(columns=silver_model.column_names()).reset_index(drop=True)
    # Tipo explícito por coluna: a mesma tabela recebe várias fontes, e o tipo
    # não pode depender de qual delas escreveu primeiro. Inteiro vira Int64 (não
    # float) para o Iceberg não gravar double onde o modelo diz bigint.
    for campo in silver_model.fields:
        if campo.name not in out.columns:
            continue
        if campo.type == "bigint":
            out[campo.name] = pd.to_numeric(out[campo.name], errors="coerce").astype("Int64")
        elif campo.type == "decimal":
            out[campo.name] = pd.to_numeric(out[campo.name], errors="coerce").astype("float64")
        elif campo.type == "boolean":
            out[campo.name] = out[campo.name].astype("boolean")
    return out


def mapping_rows(mapping: SourceMapping, silver_model: SilverModel) -> list[dict[str, Any]]:
    """Correlacionamento Bronze → Silver, para a tabela de auditoria."""
    invertido = {v: k for k, v in mapping.columns.items()}
    linhas: list[dict[str, Any]] = []
    for campo in silver_model.fields:
        bruto = invertido.get(campo.name, "")
        constante = campo.name in mapping.constants
        linhas.append({
            "source_key": mapping.source_key,
            "bronze_table": mapping.bronze_table,
            "silver_table": silver_model.table,
            "bronze_field": bruto or ("<constante>" if constante else ""),
            "silver_field": campo.name,
            "silver_type": campo.type,
            "role": campo.role,
            "required": campo.required,
            "unit": campo.unit,
            "notes": campo.notes or (
                f"Constante: {mapping.constants[campo.name]}" if constante else None
            ),
        })
    return linhas


def materialise(
    session,
    *,
    source_key: str,
    environment: str | None = None,
    bronze_table: str | None = None,
) -> dict[str, Any]:
    """Normaliza a Bronze de uma fonte para o seu modelo Silver.

    Idempotente por período: reprocessar o mesmo mês substitui a fatia.
    """
    from app.services import iceberg_service as iceberg
    from app.services import lakehouse_layers as layers
    from app.services.silver_models import model as _model

    mapping = mapping_for(source_key)
    if mapping is None:
        raise KeyError(
            f"Fonte '{source_key}' não declara mapeamento. "
            f"Fontes com modelo: {', '.join(mapped_sources())}"
        )
    silver_model = _model(mapping.model_name)
    tabela_bronze = bronze_table or bronze_table_name(mapping, environment)

    try:
        bronze = iceberg._get_catalog().load_table((layers.BRONZE, tabela_bronze)).scan().to_pandas()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Bronze {tabela_bronze} ilegível: {exc}") from exc

    if bronze.empty:
        return {
            "source_key": source_key, "model": silver_model.name,
            "silver_table": silver_model.table, "rows_silver": 0, "message": "Bronze vazia",
        }

    frame = normalise(bronze, mapping, silver_model)
    periodo_col = mapping.period_field
    if periodo_col not in frame.columns:
        raise ValueError(f"Período '{periodo_col}' ausente no modelo {silver_model.name}")
    frame[periodo_col] = frame[periodo_col].fillna("").astype(str)

    written, periods = 0, []
    for periodo, chunk in frame.groupby(periodo_col, dropna=False):
        chave = str(periodo or "")
        if not chave:
            continue
        _delete_period(silver_model.table, chave)
        written += int(iceberg.write_silver(
            connection_code=source_key,
            df=chunk.drop(columns=[periodo_col]),
            billing_period=chave,
            table_name_override=f"silver.{silver_model.table}",
        ) or 0)
        periods.append(chave)

    _register(session, mapping, silver_model)

    resultado = {
        "source_key": source_key,
        "model": silver_model.name,
        "silver_table": silver_model.table,
        "bronze_table": tabela_bronze,
        "rows_silver": written,
        "periods": sorted(periods),
    }
    if silver_model.primary_measure and silver_model.primary_measure in frame.columns:
        resultado["rows_with_value"] = int(frame[silver_model.primary_measure].notna().sum())
        resultado["rows_without_value"] = int(frame[silver_model.primary_measure].isna().sum())
    return resultado


def _delete_period(table: str, period: str) -> None:
    """Remove a fatia antes de reescrever (write_silver faz append)."""
    from pyiceberg.expressions import EqualTo

    from app.services import iceberg_service as iceberg
    from app.services import lakehouse_layers as layers

    try:
        iceberg._get_catalog().load_table((layers.SILVER, table)).delete(
            EqualTo("_billing_period", period)
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Silver %s: nada a remover em %s (%s)", table, period, type(exc).__name__)


def _register(session, mapping: SourceMapping, silver_model: SilverModel) -> None:
    """Grava/atualiza o correlacionamento da fonte."""
    from sqlalchemy import text

    for linha in mapping_rows(mapping, silver_model):
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
            linha,
        )
    session.commit()
