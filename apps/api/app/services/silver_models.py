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
        _f("_silver_owner", "varchar", ROLE_QUALITY,
           notes="Fonte que escreveu a linha; a tabela recebe várias."),
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
        _f("_silver_owner", "varchar", ROLE_QUALITY,
           notes="Fonte que escreveu a linha; a tabela recebe várias."),
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
    # `billing_period` é varchar `YYYYMM` (chave de partição lógica do FOCUS),
    # não um instante. O campo temporal do modelo é o início do período de
    # cobrança; converter `YYYYMM` para timestamp produziria NaT.
    time_field="charge_period_start",
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


# ── Modelo: catálogo de preço ─────────────────────────────────────────────────
# Preço por medidor e região. Azure Retail Prices, AWS Price List, GCP Cloud
# Billing Catalog normalizam para cá: a pergunta "quanto custa esta unidade
# nesta região" não depende do provedor.

PRICE_CATALOG = SilverModel(
    name="price_catalog",
    table="price_catalog",
    description="Preço unitário por medidor, região e tipo de compromisso.",
    grain=("cloud_provider", "meter_id", "region_id", "price_type", "currency", "valid_from"),
    primary_measure="unit_price",
    time_field="valid_from",
    fields=(
        _f("cloud_provider", "varchar", ROLE_IDENTITY, required=True),
        _f("meter_id", "varchar", ROLE_IDENTITY, required=True),
        _f("sku_id", "varchar", ROLE_DIMENSION),
        _f("service_name", "varchar", ROLE_DIMENSION, notes="FOCUS ServiceName."),
        _f("service_family", "varchar", ROLE_DIMENSION),
        _f("product_name", "varchar", ROLE_DIMENSION),
        _f("meter_name", "varchar", ROLE_DIMENSION),
        _f("arm_region", "varchar", ROLE_DIMENSION, notes="Região como o provedor a nomeia."),
        _f("region_id", "varchar", ROLE_IDENTITY, notes="FOCUS RegionId."),
        _f("arm_sku_name", "varchar", ROLE_DIMENSION),
        _f("unit_of_measure", "varchar", ROLE_DIMENSION),
        _f("price_type", "varchar", ROLE_IDENTITY,
           notes="Modelo de preço: Consumption | SavingsPlan | ReservedInstance | DevTest."),
        _f("term", "varchar", ROLE_DIMENSION,
           notes="Prazo do compromisso (P1M, P1Y, P3Y); vazio em Consumption."),
        _f("billing_account_id", "varchar", ROLE_DIMENSION,
           notes="Conta de faturamento do contrato (MCA)."),
        _f("price_source", "varchar", ROLE_IDENTITY, required=True,
           notes="PUBLIC_RETAIL | CONTRACTED | OBSERVED_EFFECTIVE. Preço contratual "
                 "e retail não se misturam: quem consome escolhe a fonte."),
        _f("price_confidence", "varchar", ROLE_QUALITY,
           notes="HIGH (contratual) | MEDIUM (observado comparável) | "
                 "LOW (retail indicativo)."),
        _f("reservation_term", "varchar", ROLE_DIMENSION, notes="1 Year | 3 Years | 5 Years."),
        _f("currency", "varchar", ROLE_IDENTITY, notes="FOCUS PricingCurrency."),
        _f("unit_price", "decimal", ROLE_MEASURE, notes="Preço da unidade de medida."),
        _f("base_price", "decimal", ROLE_MEASURE, notes="Preço base do price sheet."),
        _f("market_price", "decimal", ROLE_MEASURE,
           notes="Preço de mercado; usado para medir desconto sobre o contratado."),
        _f("tier_minimum_units", "decimal", ROLE_DIMENSION),
        _f("effective_start_date", "timestamp", ROLE_TIME),
        _f("valid_from", "timestamp", ROLE_TIME, required=True,
           notes="Data da coleta: o preço vale a partir dela."),
        _f("is_primary_meter", "varchar", ROLE_QUALITY),
        _f("_billing_period", "varchar", ROLE_QUALITY),
        _f("_silver_ingested_at", "varchar", ROLE_QUALITY),
        _f("_silver_owner", "varchar", ROLE_QUALITY),
    ),
)


# ── Modelo: catálogo de SKU de compute ────────────────────────────────────────
# O que dá elegibilidade: quais tamanhos existem, em que regiões e com que
# restrição. Sem isto um candidato pode ser sugerido num SKU que a assinatura
# não pode criar — o próprio aceite do P7 proíbe.

COMPUTE_SKU = SilverModel(
    name="compute_sku_catalog",
    table="compute_sku_catalog",
    description="SKUs de compute disponíveis por região, com restrições e capacidades.",
    grain=("cloud_provider", "sub_account_id", "region_id", "sku_name", "valid_from"),
    time_field="valid_from",
    fields=(
        _f("cloud_provider", "varchar", ROLE_IDENTITY, required=True),
        _f("sub_account_id", "varchar", ROLE_IDENTITY, notes="Assinatura do escopo."),
        _f("region_id", "varchar", ROLE_IDENTITY, required=True, notes="FOCUS RegionId."),
        _f("sku_name", "varchar", ROLE_IDENTITY, required=True),
        _f("sku_tier", "varchar", ROLE_DIMENSION),
        _f("sku_family", "varchar", ROLE_DIMENSION),
        _f("vcpus", "bigint", ROLE_DIMENSION),
        _f("memory_gb", "decimal", ROLE_DIMENSION),
        _f("max_data_disks", "bigint", ROLE_DIMENSION),
        _f("premium_io", "varchar", ROLE_DIMENSION),
        _f("accelerated_networking", "varchar", ROLE_DIMENSION),
        # Elegibilidade: restrição declarada pelo provedor veta o candidato.
        _f("is_available", "varchar", ROLE_QUALITY, required=True,
           notes="SIM | NAO. Derivado das restrições da API."),
        _f("restrictions_json", "varchar", ROLE_QUALITY),
        _f("restriction_reason", "varchar", ROLE_QUALITY),
        _f("zones", "varchar", ROLE_DIMENSION),
        _f("valid_from", "timestamp", ROLE_TIME, required=True),
        _f("_billing_period", "varchar", ROLE_QUALITY),
        _f("_silver_ingested_at", "varchar", ROLE_QUALITY),
        _f("_silver_owner", "varchar", ROLE_QUALITY),
    ),
)


MODELS: dict[str, SilverModel] = {
    METRIC_SERIES.name: METRIC_SERIES,
    RESOURCE_INVENTORY.name: RESOURCE_INVENTORY,
    RESOURCE_COST.name: RESOURCE_COST,
    PRICE_CATALOG.name: PRICE_CATALOG,
    COMPUTE_SKU.name: COMPUTE_SKU,
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
