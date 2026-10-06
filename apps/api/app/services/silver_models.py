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
        _f("sample_count", "double", ROLE_MEASURE,
           notes="Agregação count devolvida pela Azure; pode ser fracionária em métricas de disco."),
        _f("value", "decimal", ROLE_MEASURE, notes="Valor canônico da série."),
        _f("quality_status", "varchar", ROLE_QUALITY, required=True,
           notes="OK | GAP | NOT_AVAILABLE. Ausência nunca vira zero."),
        _f("quality_reason", "varchar", ROLE_QUALITY),
        _f("source_api_version", "varchar", ROLE_QUALITY),
        _f("_bronze_blob_path", "varchar", ROLE_QUALITY),
        _f("_silver_ingested_at", "varchar", ROLE_QUALITY),
        _f("_silver_owner", "varchar", ROLE_QUALITY,
           notes="Fonte que escreveu a linha; a tabela recebe várias."),
        _f("_processed_at", "varchar", ROLE_QUALITY,
           notes="Gravado pelo writer; declarado para o schema não divergir."),
        _f("_source_connection_id", "bigint", ROLE_QUALITY,
           notes="Conexao que originou a linha."),
        _f("_source_connection_code", "varchar", ROLE_QUALITY,
           notes="Codigo da conexao (azure-4p, azure-ti)."),
        _f("_source_connection_name", "varchar", ROLE_QUALITY,
           notes="Tabela Bronze de origem; identidade estavel."),
        _f("_billing_period", "varchar", ROLE_QUALITY),
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
        _f("_silver_ingested_at", "varchar", ROLE_QUALITY),
        _f("_silver_owner", "varchar", ROLE_QUALITY,
           notes="Fonte que escreveu a linha; a tabela recebe várias."),
        _f("_processed_at", "varchar", ROLE_QUALITY,
           notes="Gravado pelo writer; declarado para o schema não divergir."),
        _f("_source_connection_id", "bigint", ROLE_QUALITY,
           notes="Conexao que originou a linha."),
        _f("_source_connection_code", "varchar", ROLE_QUALITY,
           notes="Codigo da conexao (azure-4p, azure-ti)."),
        _f("_source_connection_name", "varchar", ROLE_QUALITY,
           notes="Tabela Bronze de origem; identidade estavel."),
        _f("_billing_period", "varchar", ROLE_QUALITY),
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
        _f("_silver_owner", "varchar", ROLE_QUALITY,
           notes="Fonte que escreveu a linha."),
        _f("_processed_at", "varchar", ROLE_QUALITY),
        _f("_source_connection_id", "bigint", ROLE_QUALITY,
           notes="Conexao que originou a linha."),
        _f("_source_connection_code", "varchar", ROLE_QUALITY,
           notes="Codigo da conexao (azure-4p, azure-ti)."),
        _f("_source_connection_name", "varchar", ROLE_QUALITY,
           notes="Tabela Bronze de origem; identidade estavel."),
        _f("_billing_period", "varchar", ROLE_QUALITY),
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
        _f("product_id", "varchar", ROLE_DIMENSION,
           notes="ProductId do Price Sheet; ajuda a desambiguar o medidor no escopo da conta."),
        _f("billing_currency", "varchar", ROLE_DIMENSION,
           notes="Moeda de faturamento informada no Price Sheet; distinta da moeda do UnitPrice."),
        _f("snapshot_id", "varchar", ROLE_IDENTITY,
           notes="Identificador do snapshot completo publicado de azure-pricesheet."),
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
        _f("_silver_ingested_at", "varchar", ROLE_QUALITY),
        _f("_silver_owner", "varchar", ROLE_QUALITY),
        # Gravado pelo próprio `write_silver` em toda tabela. Declarado porque
        # schema físico que diverge do modelo falha só na gravação seguinte.
        _f("_processed_at", "varchar", ROLE_QUALITY),
        _f("_source_connection_id", "bigint", ROLE_QUALITY,
           notes="Conexão que originou a linha."),
        _f("_source_connection_code", "varchar", ROLE_QUALITY,
           notes="Código da conexão (azure-4p, azure-ti)."),
        _f("_source_connection_name", "varchar", ROLE_QUALITY,
           notes="Tabela Bronze de origem; identidade estável da conexão."),
        _f("_billing_period", "varchar", ROLE_QUALITY),
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
        _f("_silver_ingested_at", "varchar", ROLE_QUALITY),
        _f("_silver_owner", "varchar", ROLE_QUALITY),
        # Gravado pelo próprio `write_silver` em toda tabela. Declarado porque
        # schema físico que diverge do modelo falha só na gravação seguinte.
        _f("_processed_at", "varchar", ROLE_QUALITY),
        _f("_source_connection_id", "bigint", ROLE_QUALITY,
           notes="Conexão que originou a linha."),
        _f("_source_connection_code", "varchar", ROLE_QUALITY,
           notes="Código da conexão (azure-4p, azure-ti)."),
        _f("_source_connection_name", "varchar", ROLE_QUALITY,
           notes="Tabela Bronze de origem; identidade estável da conexão."),
        _f("_billing_period", "varchar", ROLE_QUALITY),
    ),
)


# ── Modelo: Quota de compute ──────────────────────────────────────────────────
# Restrição de elegibilidade: um candidato que caiba no SKU mas não caiba na quota
# não pode ser sugerido. Todas as nuvens têm quota, então o modelo é canônico.
QUOTA = SilverModel(
    name="resource_quota",
    table="resource_quota",
    description="Quota de compute por assinatura e região, com consumo e limite.",
    grain=("cloud_provider", "sub_account_id", "region_id", "quota_name", "snapshot_date"),
    primary_measure="limit_value",
    time_field="snapshot_date",
    fields=(
        _f("cloud_provider", "varchar", ROLE_IDENTITY, required=True),
        _f("sub_account_id", "varchar", ROLE_IDENTITY, required=True),
        _f("region_id", "varchar", ROLE_IDENTITY, required=True),
        _f("quota_name", "varchar", ROLE_IDENTITY, required=True,
           notes="Nome da família: cores, virtualMachines, lowPriorityCores."),
        _f("quota_display_name", "varchar", ROLE_DIMENSION),
        _f("current_value", "long", ROLE_MEASURE, notes="Consumo atual, em unidades."),
        _f("limit_value", "long", ROLE_MEASURE, notes="Limite contratado, em unidades."),
        _f("available_value", "long", ROLE_MEASURE,
           notes="Limite menos consumo; é o que decide elegibilidade."),
        _f("unit", "varchar", ROLE_DIMENSION),
        _f("snapshot_date", "varchar", ROLE_TIME),
        _f("_silver_ingested_at", "varchar", ROLE_QUALITY),
        _f("_silver_owner", "varchar", ROLE_QUALITY),
        # Gravado pelo próprio `write_silver` em toda tabela. Declarado porque
        # schema físico que diverge do modelo falha só na gravação seguinte.
        _f("_processed_at", "varchar", ROLE_QUALITY),
        _f("_source_connection_id", "bigint", ROLE_QUALITY,
           notes="Conexão que originou a linha."),
        _f("_source_connection_code", "varchar", ROLE_QUALITY,
           notes="Código da conexão (azure-4p, azure-ti)."),
        _f("_source_connection_name", "varchar", ROLE_QUALITY,
           notes="Tabela Bronze de origem; identidade estável da conexão."),
        _f("_billing_period", "varchar", ROLE_QUALITY),
    ),
)

# ── Modelo: Política de região/SKU ────────────────────────────────────────────
# Veto, não sugestão: o que o contrato proíbe não entra como candidato.
REGION_POLICY = SilverModel(
    name="region_policy",
    table="region_policy",
    description="Atribuições de política que restringem SKU e região.",
    grain=("cloud_provider", "sub_account_id", "assignment_id"),
    primary_measure="allowed_values",
    time_field="snapshot_date",
    fields=(
        _f("cloud_provider", "varchar", ROLE_IDENTITY, required=True),
        _f("sub_account_id", "varchar", ROLE_IDENTITY, required=True),
        _f("assignment_id", "varchar", ROLE_IDENTITY, required=True),
        _f("assignment_name", "varchar", ROLE_DIMENSION),
        _f("display_name", "varchar", ROLE_DIMENSION),
        _f("policy_definition_id", "varchar", ROLE_DIMENSION),
        _f("scope", "varchar", ROLE_DIMENSION),
        _f("enforcement_mode", "varchar", ROLE_QUALITY,
           notes="Default | DoNotEnforce. Só Default veta."),
        _f("parameters_json", "varchar", ROLE_DIMENSION),
        _f("allowed_values", "varchar", ROLE_MEASURE,
           notes="SKUs ou regiões permitidas, quando a política declara."),
        _f("snapshot_date", "varchar", ROLE_TIME),
        _f("_silver_ingested_at", "varchar", ROLE_QUALITY),
        _f("_silver_owner", "varchar", ROLE_QUALITY),
        # Gravado pelo próprio `write_silver` em toda tabela. Declarado porque
        # schema físico que diverge do modelo falha só na gravação seguinte.
        _f("_processed_at", "varchar", ROLE_QUALITY),
        _f("_source_connection_id", "bigint", ROLE_QUALITY,
           notes="Conexão que originou a linha."),
        _f("_source_connection_code", "varchar", ROLE_QUALITY,
           notes="Código da conexão (azure-4p, azure-ti)."),
        _f("_source_connection_name", "varchar", ROLE_QUALITY,
           notes="Tabela Bronze de origem; identidade estável da conexão."),
        _f("_billing_period", "varchar", ROLE_QUALITY),
    ),
)

# ── Modelo: Alocação de custo ─────────────────────────────────────────────────
# Ponte com pesos: uma linha de custo pode virar várias alocações **desde que os
# pesos somem 1**. O aceite do P4 depende disso.
ALLOCATION = SilverModel(
    name="resource_allocation",
    table="resource_allocation",
    description="Pesos de alocação de custo por cluster e grupo consumidor.",
    grain=("cloud_provider", "parent_resource_id", "allocation_level", "allocation_key", "snapshot_date"),
    primary_measure="allocation_weight",
    time_field="snapshot_date",
    fields=(
        _f("cloud_provider", "varchar", ROLE_IDENTITY, required=True),
        _f("parent_resource_id", "varchar", ROLE_IDENTITY, required=True,
           notes="Recurso cujo custo é dividido (o cluster)."),
        _f("allocation_level", "varchar", ROLE_IDENTITY,
           notes="node_pool | namespace | workload."),
        _f("allocation_key", "varchar", ROLE_IDENTITY, notes="Grupo consumidor."),
        _f("allocation_weight", "double", ROLE_MEASURE, required=True,
           notes="Peso do grupo; soma 1 por recurso pai."),
        _f("weight_basis", "varchar", ROLE_QUALITY,
           notes="Como o peso foi calculado: node_capacity, consumo, custo."),
        _f("node_count", "bigint", ROLE_MEASURE),
        _f("node_sku", "varchar", ROLE_DIMENSION),
        _f("pool_mode", "varchar", ROLE_DIMENSION),
        _f("namespace", "varchar", ROLE_DIMENSION,
           notes="NULL quando a origem não fornece namespace — nunca inferido."),
        _f("allocation_status", "varchar", ROLE_QUALITY, notes="PARTIAL | AVAILABLE."),
        _f("status_reason", "varchar", ROLE_QUALITY),
        _f("weight_validated", "varchar", ROLE_QUALITY, notes="SIM quando soma 1."),
        _f("snapshot_date", "varchar", ROLE_TIME),
        _f("_silver_ingested_at", "varchar", ROLE_QUALITY),
        _f("_silver_owner", "varchar", ROLE_QUALITY),
        # Gravado pelo próprio `write_silver` em toda tabela. Declarado porque
        # schema físico que diverge do modelo falha só na gravação seguinte.
        _f("_processed_at", "varchar", ROLE_QUALITY),
        _f("_source_connection_id", "bigint", ROLE_QUALITY,
           notes="Conexão que originou a linha."),
        _f("_source_connection_code", "varchar", ROLE_QUALITY,
           notes="Código da conexão (azure-4p, azure-ti)."),
        _f("_source_connection_name", "varchar", ROLE_QUALITY,
           notes="Tabela Bronze de origem; identidade estável da conexão."),
        _f("_billing_period", "varchar", ROLE_QUALITY),
    ),
)

# ── Modelo: Métricas de container ─────────────────────────────────────────────
# Inventário e desempenho por container/nó, com cobertura explícita: só os
# clusters com Container Insights têm dado, e os outros ficam registrados.
CONTAINER_METRIC = SilverModel(
    name="container_metric_series",
    table="container_metric_series",
    description="Inventário e desempenho de containers por cluster e namespace.",
    grain=("cloud_provider", "cluster_resource_id", "record_kind", "namespace", "computer", "metric_name", "snapshot_date"),
    primary_measure="metric_average",
    time_field="snapshot_date",
    fields=(
        _f("cloud_provider", "varchar", ROLE_IDENTITY, required=True),
        _f("cluster_resource_id", "varchar", ROLE_IDENTITY, required=True),
        _f("cluster_name", "varchar", ROLE_DIMENSION),
        _f("record_kind", "varchar", ROLE_IDENTITY,
           notes="pod_inventory | node_inventory | performance | coverage."),
        _f("computer", "varchar", ROLE_DIMENSION, notes="Nó."),
        _f("namespace", "varchar", ROLE_DIMENSION),
        _f("pod_status", "varchar", ROLE_DIMENSION),
        _f("pods", "bigint", ROLE_MEASURE),
        _f("containers", "bigint", ROLE_MEASURE),
        _f("nodes", "bigint", ROLE_MEASURE),
        _f("kubernetes_version", "varchar", ROLE_DIMENSION),
        _f("metric_name", "varchar", ROLE_DIMENSION),
        _f("metric_average", "double", ROLE_MEASURE),
        _f("samples", "bigint", ROLE_MEASURE),
        _f("coverage_status", "varchar", ROLE_QUALITY, notes="AVAILABLE | UNAVAILABLE."),
        _f("coverage_reason", "varchar", ROLE_QUALITY),
        _f("workspace_id", "varchar", ROLE_DIMENSION),
        _f("snapshot_date", "varchar", ROLE_TIME),
        _f("_silver_ingested_at", "varchar", ROLE_QUALITY),
        _f("_silver_owner", "varchar", ROLE_QUALITY),
        # Gravado pelo próprio `write_silver` em toda tabela. Declarado porque
        # schema físico que diverge do modelo falha só na gravação seguinte.
        _f("_processed_at", "varchar", ROLE_QUALITY),
        _f("_source_connection_id", "bigint", ROLE_QUALITY,
           notes="Conexão que originou a linha."),
        _f("_source_connection_code", "varchar", ROLE_QUALITY,
           notes="Código da conexão (azure-4p, azure-ti)."),
        _f("_source_connection_name", "varchar", ROLE_QUALITY,
           notes="Tabela Bronze de origem; identidade estável da conexão."),
        _f("_billing_period", "varchar", ROLE_QUALITY),
    ),
)


# ── Modelo: PTU (Provisioned Throughput Units) ────────────────────────────────
# Capacidade provisionada de Azure OpenAI: o que se paga é a PTU reservada, e a
# utilização diz se ela está sobrando ou faltando. A proveniência (`source_points`
# contra `expected_points`) é medida, não estimada — lacuna não vira zero.
PTU = SilverModel(
    name="ptu_capacity_series",
    table="ptu_capacity_series",
    description="Capacidade PTU provisionada e sua utilização, por hora.",
    grain=("cloud_provider", "sub_account_id", "resource_id", "deployment_name",
           "model_name", "region_id", "metric_hour"),
    primary_measure="provisioned_ptus",
    time_field="metric_hour",
    fields=(
        _f("cloud_provider", "varchar", ROLE_IDENTITY, required=True),
        _f("resource_id", "varchar", ROLE_IDENTITY, required=True,
           notes="Conta de recurso do Azure OpenAI, normalizada."),
        _f("deployment_name", "varchar", ROLE_IDENTITY, required=True),
        _f("model_name", "varchar", ROLE_DIMENSION),
        _f("model_version", "varchar", ROLE_DIMENSION),
        _f("region_id", "varchar", ROLE_DIMENSION),
        _f("sku_name", "varchar", ROLE_DIMENSION,
           notes="GlobalProvisionedManaged e correlatos."),
        _f("metric_hour", "timestamp", ROLE_TIME, required=True),
        # Capacidade: o que se paga.
        _f("provisioned_ptus", "double", ROLE_MEASURE,
           notes="PTUs reservadas na hora."),
        _f("active_tokens_p95", "double", ROLE_MEASURE),
        _f("utilization_avg", "double", ROLE_MEASURE),
        _f("utilization_p95", "double", ROLE_MEASURE,
           notes="É o que diz se a capacidade está no limite."),
        _f("utilization_max", "double", ROLE_MEASURE),
        # Consumo.
        _f("input_tokens_sum", "double", ROLE_MEASURE),
        _f("output_tokens_sum", "double", ROLE_MEASURE),
        _f("total_tokens_sum", "double", ROLE_MEASURE),
        _f("request_count", "double", ROLE_MEASURE),
        _f("success_count", "double", ROLE_MEASURE),
        _f("client_error_count", "double", ROLE_MEASURE),
        _f("server_error_count", "double", ROLE_MEASURE),
        _f("rate_limited_count", "double", ROLE_MEASURE,
           notes="Requisições recusadas por limite: sinal de PTU insuficiente."),
        _f("spillover_request_count", "double", ROLE_MEASURE),
        _f("time_to_response_p95_ms", "double", ROLE_MEASURE),
        _f("tokens_per_second_avg", "double", ROLE_MEASURE),
        # Proveniência: medida, não estimada.
        _f("source_points", "bigint", ROLE_QUALITY),
        _f("expected_points", "bigint", ROLE_QUALITY),
        _f("completeness_pct", "double", ROLE_QUALITY,
           notes="Pontos coletados sobre esperados; abaixo de 100 há lacuna."),
        _f("project_hint", "varchar", ROLE_DIMENSION,
           notes="Projeto declarado na origem."),
        _f("_silver_ingested_at", "varchar", ROLE_QUALITY),
        _f("_silver_owner", "varchar", ROLE_QUALITY),
        # Gravado pelo próprio `write_silver` em toda tabela. Declarado porque
        # schema físico que diverge do modelo falha só na gravação seguinte.
        _f("_processed_at", "varchar", ROLE_QUALITY),
        _f("_source_connection_id", "bigint", ROLE_QUALITY,
           notes="Conexão que originou a linha."),
        _f("_source_connection_code", "varchar", ROLE_QUALITY,
           notes="Código da conexão (azure-4p, azure-ti)."),
        _f("_source_connection_name", "varchar", ROLE_QUALITY,
           notes="Tabela Bronze de origem; identidade estável da conexão."),
        _f("_billing_period", "varchar", ROLE_QUALITY),
    ),
)


# ── Modelo: Candidato de rightsizing (P8) ─────────────────────────────────────
# O aceite do P8 é explícito: "candidato SKU proibido/indisponível não é elegível;
# preço contratual e retail não se misturam; ausência de Price Sheet vira status
# explícito". Este modelo carrega esses três estados no próprio dado, mais o
# `decision_hash` que faz duas execuções iguais produzirem a mesma decisão.
RIGHTSIZING_CANDIDATE = SilverModel(
    name="rightsizing_candidate",
    table="rightsizing_candidate",
    description="Candidato de rightsizing com veredito, vetos, DE/PARA e saving.",
    grain=("run_id", "resource_id", "sku_from", "sku_to"),
    primary_measure="saving_monthly",
    time_field="created_at",
    fields=(
        _f("run_id", "varchar", ROLE_IDENTITY, required=True,
           notes="Execução; duas execuções iguais compartilham o decision_hash."),
        _f("decision_hash", "varchar", ROLE_IDENTITY,
           notes="Hash das entradas materiais: mesma entrada, mesma decisão."),
        _f("resource_id", "varchar", ROLE_IDENTITY, required=True),
        _f("resource_name", "varchar", ROLE_DIMENSION),
        _f("region_id", "varchar", ROLE_DIMENSION),
        _f("_source_connection_code", "varchar", ROLE_QUALITY),
        # Verdicto. REQUIRES_VALIDATION existe para o caso em que o dado não
        # permite afirmar — não é indecisão, é honestidade.
        _f("verdict", "varchar", ROLE_QUALITY, required=True,
           notes="VALID | INVALID | REQUIRES_VALIDATION."),
        _f("veto_reason", "varchar", ROLE_QUALITY,
           notes="Por que foi vetado; vazio quando VALID."),
        _f("confidence", "varchar", ROLE_QUALITY, notes="ALTA | MEDIA | BAIXA."),
        # DE / PARA
        _f("sku_from", "varchar", ROLE_IDENTITY, required=True),
        _f("vcpus_from", "bigint", ROLE_DIMENSION),
        _f("memory_gb_from", "double", ROLE_DIMENSION),
        _f("sku_to", "varchar", ROLE_IDENTITY, required=True),
        _f("vcpus_to", "bigint", ROLE_DIMENSION),
        _f("memory_gb_to", "double", ROLE_DIMENSION),
        # Pressão observada (pior caso da janela, não a média).
        _f("cpu_p95", "double", ROLE_MEASURE),
        _f("mem_p95", "double", ROLE_MEASURE),
        _f("observed_days", "bigint", ROLE_QUALITY),
        _f("requested_days", "bigint", ROLE_QUALITY),
        # Finanças por componente: sem preço comparável, o saving fica ausente.
        _f("cost_from_current", "double", ROLE_MEASURE, notes="Custo do recurso na janela."),
        _f("cost_to_estimated", "double", ROLE_MEASURE, notes="Custo projetado do SKU destino."),
        _f("saving_monthly", "double", ROLE_MEASURE,
           notes="NULL quando não há preço comparável — nunca zero fingindo ausência."),
        _f("saving_pct", "double", ROLE_MEASURE),
        _f("price_source_from", "varchar", ROLE_QUALITY,
           notes="CONTRACTED | PUBLIC_RETAIL | NONE."),
        _f("price_source_to", "varchar", ROLE_QUALITY),
        _f("price_comparable", "varchar", ROLE_QUALITY,
           notes="SIM | NAO. NAO => saving não é exibido."),
        _f("created_at", "varchar", ROLE_TIME),
        _f("_silver_ingested_at", "varchar", ROLE_QUALITY),
        _f("_silver_owner", "varchar", ROLE_QUALITY),
        # Gravado pelo próprio `write_silver` em toda tabela. Declarado porque
        # schema físico que diverge do modelo falha só na gravação seguinte.
        _f("_processed_at", "varchar", ROLE_QUALITY),
        _f("_source_connection_id", "bigint", ROLE_QUALITY),
        _f("_source_connection_name", "varchar", ROLE_QUALITY),
        _f("_billing_period", "varchar", ROLE_QUALITY),
    ),
)


# ── Modelo: Decisão de rightsizing (P9) ───────────────────────────────────────
# Persiste a decisão com o snapshot DE/PARA **imutável** e o estado de revisão.
# O `evidence_hash` é o que garante o aceite "reexecução cria versão adequada, sem
# duplicação indevida": enquanto as evidências não mudam, o hash é o mesmo e a
# decisão não é duplicada; quando mudam, o hash muda e nasce uma versão nova.
#
# O veredito determinístico é **preservado na linha**: a revisão humana registra
# concordância ou discordância, mas não reescreve o que o motor concluiu.
RIGHTSIZING_DECISION = SilverModel(
    name="rightsizing_decision",
    table="rightsizing_decision",
    description="Decisão de rightsizing com snapshot DE/PARA imutável e revisão.",
    grain=("evidence_hash",),
    primary_measure="saving_monthly",
    time_field="created_at",
    fields=(
        _f("evidence_hash", "varchar", ROLE_IDENTITY, required=True,
           notes="Hash das evidências; mesma evidência não gera versão nova."),
        _f("run_id", "varchar", ROLE_IDENTITY, required=True,
           notes="Execução em que a decisão nasceu."),
        _f("resource_id", "varchar", ROLE_IDENTITY, required=True),
        _f("resource_name", "varchar", ROLE_DIMENSION),
        _f("region_id", "varchar", ROLE_DIMENSION),
        # Veredito do motor. A revisão não o altera.
        _f("deterministic_verdict", "varchar", ROLE_QUALITY, required=True,
           notes="VALID | INVALID | REQUIRES_VALIDATION, do motor."),
        _f("veto_reason", "varchar", ROLE_QUALITY),
        _f("confidence", "varchar", ROLE_QUALITY),
        # Estado de revisão: o que o humano fez com a decisão.
        _f("review_status", "varchar", ROLE_QUALITY, required=True,
           notes="PENDING | APPROVED | REJECTED | DEFERRED."),
        _f("reviewed_by", "varchar", ROLE_DIMENSION),
        _f("reviewed_at", "varchar", ROLE_QUALITY),
        _f("review_note", "varchar", ROLE_QUALITY),
        # Snapshot DE/PARA congelado no momento da decisão.
        _f("sku_from", "varchar", ROLE_DIMENSION),
        _f("vcpus_from", "bigint", ROLE_DIMENSION),
        _f("memory_gb_from", "double", ROLE_DIMENSION),
        _f("cost_from_monthly", "double", ROLE_MEASURE),
        _f("sku_to", "varchar", ROLE_DIMENSION),
        _f("vcpus_to", "bigint", ROLE_DIMENSION),
        _f("memory_gb_to", "double", ROLE_DIMENSION),
        _f("cost_to_monthly", "double", ROLE_MEASURE),
        _f("saving_monthly", "double", ROLE_MEASURE,
           notes="NULL quando não há preço comparável."),
        _f("price_source_from", "varchar", ROLE_QUALITY),
        _f("price_source_to", "varchar", ROLE_QUALITY),
        _f("price_comparable", "varchar", ROLE_QUALITY),
        _f("cpu_p95", "double", ROLE_MEASURE),
        _f("observed_days", "bigint", ROLE_QUALITY),
        # Decisão assistida (P12). Separada do veredito: o Jev opina, não decide.
        _f("assisted_status", "varchar", ROLE_QUALITY,
           notes="OK | UNAVAILABLE. Falha do Jev não invalida a decisão."),
        _f("assisted_model", "varchar", ROLE_DIMENSION),
        _f("assisted_reason", "varchar", ROLE_QUALITY),
        _f("created_at", "varchar", ROLE_TIME, required=True),
        _f("_silver_ingested_at", "varchar", ROLE_QUALITY),
        _f("_silver_owner", "varchar", ROLE_QUALITY),
        # Gravado pelo próprio `write_silver` em toda tabela. Declarado porque
        # schema físico que diverge do modelo falha só na gravação seguinte.
        _f("_processed_at", "varchar", ROLE_QUALITY),

        _f("_source_connection_code", "varchar", ROLE_QUALITY,

           notes="Codigo da conexao."),
        _f("_source_connection_id", "bigint", ROLE_QUALITY),
        _f("_source_connection_name", "varchar", ROLE_QUALITY),
        _f("_billing_period", "varchar", ROLE_QUALITY),
    ),
)


# ── Modelo: Perfil da janela (P6) ─────────────────────────────────────────────
# Era escrito fora do registro — e, sem modelo, qualquer coluna nova no SQL do
# serviço mudava o schema em silêncio. Declarado na ordem exata que o serviço
# produz, para a tabela física e o modelo nunca divergirem.
RESOURCE_PROFILE_WINDOW = SilverModel(
    name="resource_profile_window",
    table="resource_profile_window",
    description="Perfil de uso por recurso e métrica, com cobertura e confiança.",
    grain=("resource_id", "metric", "billing_period"),
    primary_measure="effective_cost",
    time_field=None,
    fields=(
        _f("resource_id", "varchar", ROLE_IDENTITY, required=True),
        _f("resource_name", "varchar", ROLE_DIMENSION),
        _f("resource_sku", "varchar", ROLE_DIMENSION),
        _f("resource_state", "varchar", ROLE_DIMENSION),
        _f("region_id", "varchar", ROLE_DIMENSION),
        _f("sub_account_id", "varchar", ROLE_IDENTITY),
        _f("resource_type", "varchar", ROLE_DIMENSION),
        _f("metric", "varchar", ROLE_IDENTITY, required=True),
        _f("billing_period", "varchar", ROLE_IDENTITY),
        _f("requested_days", "bigint", ROLE_QUALITY,
           notes="Janela pedida; o aceite do P6 compara com a observada."),
        _f("observed_days", "bigint", ROLE_QUALITY),
        _f("samples", "bigint", ROLE_QUALITY),
        _f("value_min", "double", ROLE_MEASURE),
        _f("p50", "double", ROLE_MEASURE),
        _f("p95", "double", ROLE_MEASURE, notes="Pico que o P8 usa para dimensionar."),
        _f("p99", "double", ROLE_MEASURE),
        _f("value_max", "double", ROLE_MEASURE),
        _f("value_avg", "double", ROLE_MEASURE),
        _f("coverage", "double", ROLE_QUALITY, notes="Amostras sobre as esperadas."),
        _f("confidence", "varchar", ROLE_QUALITY, notes="ALTA | MEDIA | BAIXA."),
        _f("effective_cost", "double", ROLE_MEASURE),
        _f("billed_cost", "varchar", ROLE_QUALITY),
        _f("consumed_quantity", "varchar", ROLE_QUALITY),
        _f("pricing_currency", "varchar", ROLE_QUALITY),
        _f("source_connection_name", "varchar", ROLE_QUALITY,
           notes="Tabela Bronze de origem; a proveniência da decisão vem daqui."),
        _f("profile_hash", "varchar", ROLE_QUALITY,
           notes="Mesma entrada, mesmo hash: perfil reproduzível."),
        _f("profile_built_at", "varchar", ROLE_QUALITY),
        # O perfil e gravado por servico dedicado, nao pelo normalizador: ele nao
        # produz _silver_ingested_at/_silver_owner, e declarar o que o writer nao
        # produz causa drift. O que o writer produz: _billing_period e _processed_at.
        _f("_billing_period", "varchar", ROLE_QUALITY),
        _f("_processed_at", "varchar", ROLE_QUALITY),
    ),
)

# Perfil diário separado do agregado legado mensal. A tabela antiga permanece
# intacta para auditoria e não pode ser confundida com a nova série 4P.
RESOURCE_PROFILE_DAILY_WINDOW = SilverModel(
    name="resource_profile_daily_window",
    table="resource_profile_daily_window",
    description="Perfil de uso 4P calculado sobre partições diárias UTC e com custo mensal alocado explicitamente.",
    grain=("resource_id", "metric", "window_from_day", "window_to_day", "series_source_connection_id", "series_owner"),
    primary_measure="effective_cost",
    time_field="window_to_day",
    fields=(
        _f("resource_id", "varchar", ROLE_IDENTITY, required=True),
        _f("resource_name", "varchar", ROLE_DIMENSION),
        _f("resource_sku", "varchar", ROLE_DIMENSION),
        _f("resource_state", "varchar", ROLE_DIMENSION),
        _f("region_id", "varchar", ROLE_DIMENSION),
        _f("sub_account_id", "varchar", ROLE_IDENTITY),
        _f("resource_type", "varchar", ROLE_DIMENSION),
        _f("metric", "varchar", ROLE_IDENTITY, required=True),
        _f("billing_period", "varchar", ROLE_IDENTITY, notes="Mês de custo associado; distinto da janela de perfil."),
        _f("window_from_day", "varchar", ROLE_TIME, required=True),
        _f("window_to_day", "varchar", ROLE_TIME, required=True),
        _f("requested_days", "bigint", ROLE_QUALITY),
        _f("observed_days", "bigint", ROLE_QUALITY),
        _f("samples", "bigint", ROLE_QUALITY),
        _f("value_min", "double", ROLE_MEASURE),
        _f("p50", "double", ROLE_MEASURE),
        _f("p95", "double", ROLE_MEASURE),
        _f("p99", "double", ROLE_MEASURE),
        _f("value_max", "double", ROLE_MEASURE),
        _f("value_avg", "double", ROLE_MEASURE),
        _f("coverage", "double", ROLE_QUALITY),
        _f("confidence", "varchar", ROLE_QUALITY),
        _f("cost_window_amount", "double", ROLE_MEASURE,
           notes="Custo mensal rateado pelos dias de cada mês que intersectam a janela UTC."),
        _f("effective_cost", "double", ROLE_MEASURE,
           notes="Run rate mensal: custo alocado na janela / dias da janela × 30,4375."),
        _f("cost_proration_rule", "varchar", ROLE_QUALITY),
        _f("billed_cost", "varchar", ROLE_QUALITY),
        _f("consumed_quantity", "varchar", ROLE_QUALITY),
        _f("pricing_currency", "varchar", ROLE_QUALITY),
        _f("profile_hash", "varchar", ROLE_QUALITY),
        _f("profile_built_at", "varchar", ROLE_TIME),
        _f("series_source_connection_id", "bigint", ROLE_QUALITY, required=True),
        _f("series_owner", "varchar", ROLE_QUALITY, required=True),
        _f("_billing_period", "varchar", ROLE_QUALITY),
        _f("_processed_at", "varchar", ROLE_QUALITY),
    ),
)


MODELS: dict[str, SilverModel] = {
    METRIC_SERIES.name: METRIC_SERIES,
    RESOURCE_INVENTORY.name: RESOURCE_INVENTORY,
    RESOURCE_COST.name: RESOURCE_COST,
    PRICE_CATALOG.name: PRICE_CATALOG,
    COMPUTE_SKU.name: COMPUTE_SKU,
    QUOTA.name: QUOTA,
    REGION_POLICY.name: REGION_POLICY,
    ALLOCATION.name: ALLOCATION,
    CONTAINER_METRIC.name: CONTAINER_METRIC,
    PTU.name: PTU,
    RIGHTSIZING_CANDIDATE.name: RIGHTSIZING_CANDIDATE,
    RIGHTSIZING_DECISION.name: RIGHTSIZING_DECISION,
    RESOURCE_PROFILE_WINDOW.name: RESOURCE_PROFILE_WINDOW,
    RESOURCE_PROFILE_DAILY_WINDOW.name: RESOURCE_PROFILE_DAILY_WINDOW,
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
