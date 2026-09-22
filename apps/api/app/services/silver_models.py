"""Modelos Silver canônicos.

Cada **tipo** de dado tem um modelo — uma tabela Silver com esquema estável. A
Bronze varia por provider; o modelo não. Quando chega uma fonte nova (Azure,
AWS, GCP, OCI), ela não cria tabela nenhuma: declara o mapeamento dos seus campos
para o modelo que já existe, e a partir daí responde às mesmas perguntas que as
outras nuvens.

Regra de ouro: **normalizar é mapear, não programar**. Um tipo novo de dado ganha
um modelo aqui; uma fonte nova ganha só um mapeamento.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Papéis de campo. Definem como o consumidor lê o modelo, não a origem.
ROLE_IDENTITY = "identity"   # compõe a chave / o grão
ROLE_DIMENSION = "dimension"  # recorte
ROLE_MEASURE = "measure"      # valor
ROLE_TIME = "time"            # instante ou validade
ROLE_QUALITY = "quality"      # procedência e confiabilidade


@dataclass(frozen=True)
class SilverField:
    name: str
    type: str                      # varchar | decimal | bigint | timestamp | boolean
    role: str
    required: bool = False
    unit: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class SilverModel:
    """Um modelo canônico e como ele é gravado."""

    name: str                      # nome do modelo (chave estável)
    table: str                     # tabela física na Silver
    description: str
    grain: tuple[str, ...]         # o que torna uma linha única
    fields: tuple[SilverField, ...]
    # Coluna que expressa o valor principal, quando o modelo tem uma.
    primary_measure: str | None = None
    # Fonte de tempo canônica (partição lógica).
    time_field: str | None = None

    def column_names(self) -> list[str]:
        return [f.name for f in self.fields]

    def column_types(self) -> dict[str, str]:
        return {f.name: f.type for f in self.fields}

    def field(self, name: str) -> SilverField | None:
        return next((f for f in self.fields if f.name == name), None)

    def required_fields(self) -> list[str]:
        return [f.name for f in self.fields if f.required]


def _f(name: str, type_: str, role: str, **kw: Any) -> SilverField:
    return SilverField(name=name, type=type_, role=role, **kw)


# ── Modelo: série temporal de métricas ────────────────────────────────────────
# Serve para Azure Monitor, CloudWatch, Cloud Monitoring, OCI Monitoring. A
# pergunta "qual o valor desta métrica neste recurso nesta janela" é idêntica em
# todas elas.

METRIC_SERIES = SilverModel(
    name="resource_metric_series",
    table="resource_metric_series",
    description="Série temporal de métricas de qualquer recurso, em qualquer nuvem.",
    grain=("cloud_provider", "sub_account_id", "resource_id", "metric", "bucket_start_utc"),
    primary_measure="value",
    time_field="bucket_start_utc",
    fields=(
        _f("cloud_provider", "varchar", ROLE_IDENTITY, required=True),
        _f("resource_type", "varchar", ROLE_IDENTITY),
        _f("resource_id", "varchar", ROLE_IDENTITY, required=True),
        _f("resource_name", "varchar", ROLE_DIMENSION),
        _f("sub_account_id", "varchar", ROLE_IDENTITY,
           notes="FOCUS SubAccountId: assinatura, conta ou projeto."),
        _f("resource_group_id", "varchar", ROLE_DIMENSION, notes="FOCUS ResourceGroupId."),
        _f("region_id", "varchar", ROLE_DIMENSION, notes="FOCUS RegionId."),
        _f("resource_sku", "varchar", ROLE_DIMENSION),
        _f("operating_system", "varchar", ROLE_DIMENSION),
        _f("resource_state", "varchar", ROLE_DIMENSION),
        _f("metric", "varchar", ROLE_IDENTITY, required=True),
        _f("metric_unit", "varchar", ROLE_QUALITY),
        _f("dimensions_json", "varchar", ROLE_DIMENSION,
           notes="Dimensões da métrica; o conjunto varia por provider."),
        _f("bucket_start_utc", "timestamp", ROLE_TIME, required=True),
        _f("value_average", "decimal", ROLE_MEASURE),
        _f("value_minimum", "decimal", ROLE_MEASURE),
        _f("value_maximum", "decimal", ROLE_MEASURE),
        _f("value_total", "decimal", ROLE_MEASURE),
        _f("sample_count", "bigint", ROLE_MEASURE),
        _f("value", "decimal", ROLE_MEASURE, notes="Valor canônico da série."),
        _f("quality_status", "varchar", ROLE_QUALITY, required=True,
           notes="OK | GAP | NOT_AVAILABLE. Ausência nunca vira zero."),
        _f("quality_reason", "varchar", ROLE_QUALITY),
        _f("source_api_version", "varchar", ROLE_QUALITY),
        _f("_bronze_blob_path", "varchar", ROLE_QUALITY),
        _f("_billing_period", "varchar", ROLE_QUALITY),
        _f("_silver_ingested_at", "varchar", ROLE_QUALITY),
    ),
)


# ── Modelo: inventário de recursos ────────────────────────────────────────────
# Azure Resource Graph, AWS Config, GCP Cloud Asset, OCI Search. Responde
# "quais recursos existem, com que atributos, a partir de quando".

RESOURCE_INVENTORY = SilverModel(
    name="resource_inventory",
    table="resource_inventory",
    description="Fotografia de recursos de qualquer nuvem, com atributos de identidade e estado.",
    grain=("cloud_provider", "sub_account_id", "resource_id", "valid_from"),
    time_field="valid_from",
    fields=(
        _f("cloud_provider", "varchar", ROLE_IDENTITY, required=True),
        _f("resource_id", "varchar", ROLE_IDENTITY, required=True),
        _f("resource_name", "varchar", ROLE_DIMENSION),
        _f("resource_type", "varchar", ROLE_IDENTITY, required=True),
        _f("sub_account_id", "varchar", ROLE_IDENTITY, notes="FOCUS SubAccountId."),
        _f("resource_group_id", "varchar", ROLE_DIMENSION),
        _f("region_id", "varchar", ROLE_DIMENSION),
        _f("resource_sku", "varchar", ROLE_DIMENSION),
        _f("sku_capacity", "bigint", ROLE_DIMENSION),
        _f("resource_state", "varchar", ROLE_DIMENSION),
        _f("operating_system", "varchar", ROLE_DIMENSION),
        _f("provisioning_state", "varchar", ROLE_QUALITY),
        _f("availability_zones", "varchar", ROLE_DIMENSION),
        _f("tags_json", "varchar", ROLE_DIMENSION),
        _f("valid_from", "timestamp", ROLE_TIME, required=True,
           notes="Data da fotografia: o inventário vale a partir dela."),
        _f("_billing_period", "varchar", ROLE_QUALITY),
        _f("_silver_ingested_at", "varchar", ROLE_QUALITY),
    ),
)


# ── Modelo: custo e uso por recurso ───────────────────────────────────────────
# Modelo no grão do FOCUS. Serve para qualquer export de custo (Azure Cost
# Management, AWS CUR, GCP Billing, OCI Cost). O objetivo é que a pergunta
# "quanto custou este recurso neste mês" não dependa da origem.

RESOURCE_COST = SilverModel(
    name="resource_cost",
    table="resource_cost",
    description="Custo e uso por recurso e período, no grão do FOCUS.",
    grain=("cloud_provider", "sub_account_id", "resource_id", "billing_period", "charge_category"),
    primary_measure="effective_cost",
    time_field="billing_period",
    fields=(
        _f("cloud_provider", "varchar", ROLE_IDENTITY, required=True),
        _f("sub_account_id", "varchar", ROLE_IDENTITY, required=True, notes="FOCUS SubAccountId."),
        _f("resource_id", "varchar", ROLE_IDENTITY, required=True, notes="FOCUS ResourceId."),
        _f("resource_name", "varchar", ROLE_DIMENSION),
        _f("resource_type", "varchar", ROLE_DIMENSION, notes="FOCUS ResourceType."),
        _f("resource_group_id", "varchar", ROLE_DIMENSION),
        _f("region_id", "varchar", ROLE_DIMENSION),
        _f("resource_sku", "varchar", ROLE_DIMENSION, notes="FOCUS SkuId."),
        _f("service_name", "varchar", ROLE_DIMENSION, notes="FOCUS ServiceName."),
        _f("service_category", "varchar", ROLE_DIMENSION, notes="FOCUS ServiceCategory."),
        _f("charge_category", "varchar", ROLE_IDENTITY, notes="FOCUS ChargeCategory: Usage, Purchase, Tax, Credit."),
        _f("charge_description", "varchar", ROLE_DIMENSION),
        _f("pricing_unit", "varchar", ROLE_DIMENSION),
        _f("pricing_quantity", "decimal", ROLE_MEASURE, notes="FOCUS PricingQuantity."),
        _f("pricing_currency", "varchar", ROLE_QUALITY),
        _f("billing_period", "varchar", ROLE_TIME, required=True, notes="FOCUS BillingPeriod (YYYYMM)."),
        _f("charge_period_start", "timestamp", ROLE_TIME),
        _f("charge_period_end", "timestamp", ROLE_TIME),
        _f("list_cost", "decimal", ROLE_MEASURE, notes="FOCUS ListCost."),
        _f("contracted_cost", "decimal", ROLE_MEASURE, notes="FOCUS ContractedCost."),
        _f("effective_cost", "decimal", ROLE_MEASURE, notes="FOCUS EffectiveCost — valor canônico."),
        _f("billed_cost", "decimal", ROLE_MEASURE, notes="FOCUS BilledCost."),
        _f("consumed_quantity", "decimal", ROLE_MEASURE),
        _f("tags_json", "varchar", ROLE_DIMENSION),
        _f("_bronze_blob_path", "varchar", ROLE_QUALITY),
        _f("_silver_ingested_at", "varchar", ROLE_QUALITY),
    ),
)


MODELS: dict[str, SilverModel] = {
    METRIC_SERIES.name: METRIC_SERIES,
    RESOURCE_INVENTORY.name: RESOURCE_INVENTORY,
    RESOURCE_COST.name: RESOURCE_COST,
}


def model(name: str) -> SilverModel:
    try:
        return MODELS[name]
    except KeyError as exc:  # noqa: BLE001
        raise KeyError(
            f"Modelo Silver '{name}' não existe. Disponíveis: {', '.join(sorted(MODELS))}"
        ) from exc


def model_tables() -> dict[str, str]:
    """Tabela física de cada modelo (para inspeção e manutenção)."""
    return {nome: m.table for nome, m in MODELS.items()}
