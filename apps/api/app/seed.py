from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select
from app.models.source_connection import SourceConnection
from app.models.rules import BusinessRule, ClassificationRule, EtlMapping
from app.models.destination import DestinationTable
from app.models.pivot_view import PivotView
from app.config import settings


def _build_connections():
    conns = []

    # Azure 4P
    if settings.azure_4p_tenant_id:
        conns.append({
            "code": "azure-4p",
            "name": "Azure 4P Cost Management",
            "connector_type": "azure_blob",
            "cloud_name": "Azure-4P",
            "cloud_scope": "azure",
            "config_json": {
                "tenant_id": settings.azure_4p_tenant_id,
                "client_id": settings.azure_4p_client_id,
                "client_secret": settings.azure_4p_client_secret,
                "storage_account": settings.azure_4p_storage_account,
                "container_name": settings.azure_4p_container,
            },
            "blob_prefixes": [
                "reports/Report4P-actual-cost/",
                "reports/Report4P-amortized-cost/",
            ],
        })

    # Azure TI
    if settings.azure_ti_tenant_id:
        conns.append({
            "code": "azure-ti",
            "name": "Azure TI Cost Management",
            "connector_type": "azure_blob",
            "cloud_name": "Azure-TI",
            "cloud_scope": "azure",
            "config_json": {
                "tenant_id": settings.azure_ti_tenant_id,
                "client_id": settings.azure_ti_client_id,
                "client_secret": settings.azure_ti_client_secret,
                "storage_account": settings.azure_ti_storage_account,
                "container_name": settings.azure_ti_container,
            },
            "blob_prefixes": [
                "reports/ReportTI-actual-cost/",
                "reports/ReportTI-amortized-cost/",
            ],
        })

    # GCP 4P
    if settings.gcp_4p_project_id:
        conns.append({
            "code": "gcp-4p",
            "name": "GCP 4P Billing Export",
            "connector_type": "gcp_bigquery",
            "cloud_name": "GCP-4P",
            "cloud_scope": "gcp",
            "config_json": {
                "project_id": settings.gcp_4p_project_id,
                "dataset_id": settings.gcp_4p_dataset_id,
                "table_name": settings.gcp_4p_table_name,
                "table_name_old": settings.gcp_4p_table_name_old,
            },
            "blob_prefixes": [],
        })

    # GCP TI
    if settings.gcp_ti_project_id:
        conns.append({
            "code": "gcp-ti",
            "name": "GCP TI Billing Export",
            "connector_type": "gcp_bigquery",
            "cloud_name": "GCP-TI",
            "cloud_scope": "gcp",
            "config_json": {
                "project_id": settings.gcp_ti_project_id,
                "dataset_id": settings.gcp_ti_dataset_id,
                "table_name": settings.gcp_ti_table_name,
                "table_name_old": settings.gcp_ti_table_name_old,
            },
            "blob_prefixes": [],
        })

    # MongoDB Atlas Pro  (auth only; PTAX/tax = BusinessRules)
    if settings.atlas_pro_contract_id:
        conns.append({
            "code": "mongodb-atlas-pro",
            "name": "MongoDB Atlas Pro",
            "connector_type": "mongodb_atlas",
            "cloud_name": "Atlas-MongoDB",
            "cloud_scope": "atlas",
            "config_json": {
                "base_url": settings.atlas_base_url,
                "contract_type": "pro",
                "group_id": settings.atlas_pro_group_id or settings.atlas_pro_contract_id.split(":")[0] if settings.atlas_pro_contract_id else "",
                "public_key": settings.atlas_pro_public_key or settings.atlas_public_key,
                "private_key": settings.atlas_pro_private_key or settings.atlas_private_key,
            },
            "blob_prefixes": [],
        })

    # MongoDB Atlas Enterprise  (auth only; PTAX/tax = BusinessRules)
    if settings.atlas_enterprise_contract_id:
        conns.append({
            "code": "mongodb-atlas-enterprise",
            "name": "MongoDB Atlas Enterprise",
            "connector_type": "mongodb_atlas",
            "cloud_name": "Atlas-MongoDB",
            "cloud_scope": "atlas",
            "config_json": {
                "base_url": settings.atlas_base_url,
                "contract_type": "enterprise",
                "group_id": settings.atlas_enterprise_group_id or settings.atlas_enterprise_contract_id.split(":")[0] if settings.atlas_enterprise_contract_id else "",
                "public_key": settings.atlas_enterprise_public_key or settings.atlas_public_key,
                "private_key": settings.atlas_enterprise_private_key or settings.atlas_private_key,
            },
            "blob_prefixes": [],
        })

    # Manual CSV upload source — always available for local CSV loads into Bronze
    conns.append({
        "code": "manual-csv",
        "name": "Upload manual de CSV",
        "connector_type": "manual_csv",
        "cloud_name": None,
        "cloud_scope": None,
        "config_json": {},
        "blob_prefixes": ["manual"],
    })

    return conns


# Business rules shared across all Azure datasources
AZURE_BUSINESS_RULES = [
    {
        "name": "PTAX BRL - USD to BRL (Azure default)",
        "description": "Convert USD cost to BRL, default PTAX rate",
        "target_column": "CostBRL",
        "rule_type": "PTAX_BRL",
        "config_json": {"source_column": "CostInBillingCurrency", "rate": 5.0},
        "priority": 10,
    },
    {
        "name": "Taxa ISS + PIS/COFINS (18.61%)",
        "description": "ISS + PIS + COFINS on service cost",
        "target_column": "Tax_Base",
        "rule_type": "TAX_BASE",
        "config_json": {"source_column": "CostInBillingCurrency", "rate": 0.186065},
        "priority": 20,
    },
    {
        "name": "Taxa Marketplace (13.83%)",
        "description": "Tax on marketplace purchases (Azure Marketplace)",
        "target_column": "Tax_Marketplace",
        "rule_type": "TAX_BASE",
        "config_json": {"source_column": "CostInBillingCurrency", "rate": 0.1383},
        "priority": 25,
    },
    {
        "name": "Taxa Extra (0.83%)",
        "description": "Multiplier on final BRL cost",
        "target_column": "VL_CUSTO_FINAL_BRL",
        "rule_type": "MARKUP",
        "config_json": {"source_column": "CostBRL", "percentage": 0.0083},
        "priority": 90,
    },
]

# MongoDB Atlas Pro: PTAX rate from .env-atlas (pro_dolar)
MONGO_PRO_BUSINESS_RULES = [
    {
        "name": f"PTAX BRL - USD to BRL (MongoDB Pro: {settings.atlas_pro_usd_to_brl})",
        "description": f"Convert USD to BRL at contract rate {settings.atlas_pro_usd_to_brl}",
        "target_column": "CostBRL",
        "rule_type": "PTAX_BRL",
        "config_json": {"source_column": "CostInBillingCurrency", "rate": settings.atlas_pro_usd_to_brl},
        "priority": 10,
    },
    {
        "name": f"Tax ISS+PIS+COFINS MongoDB Pro ({settings.atlas_pro_tax_rate * 100:.0f}%)",
        "description": "MongoDB Pro contract tax rate",
        "target_column": "Tax_Base",
        "rule_type": "TAX_BASE",
        "config_json": {"source_column": "CostInBillingCurrency", "rate": settings.atlas_pro_tax_rate},
        "priority": 20,
    },
]

# MongoDB Atlas Enterprise: PTAX rate from .env-atlas (enterprise_dolar)
MONGO_ENTERPRISE_BUSINESS_RULES = [
    {
        "name": f"PTAX BRL - USD to BRL (MongoDB Enterprise: {settings.atlas_enterprise_usd_to_brl})",
        "description": f"Convert USD to BRL at contract rate {settings.atlas_enterprise_usd_to_brl}",
        "target_column": "CostBRL",
        "rule_type": "PTAX_BRL",
        "config_json": {"source_column": "CostInBillingCurrency", "rate": settings.atlas_enterprise_usd_to_brl},
        "priority": 10,
    },
    {
        "name": f"Tax ISS+PIS+COFINS MongoDB Enterprise ({settings.atlas_enterprise_tax_rate * 100:.0f}%)",
        "description": "MongoDB Enterprise contract tax rate",
        "target_column": "Tax_Base",
        "rule_type": "TAX_BASE",
        "config_json": {"source_column": "CostInBillingCurrency", "rate": settings.atlas_enterprise_tax_rate},
        "priority": 20,
    },
]

DEFAULT_CLASSIFICATIONS = [
    {
        "name": "Projeto -> Diretoria de Tecnologia",
        "source_field": "Project",
        "pattern": r"tec|tech|it-",
        "pattern_type": "regex",
        "match_value": "Diretoria de Tecnologia",
        "target_field": "Diretoria",
        "priority": 10,
    },
    {
        "name": "Projeto -> Diretoria de Negocios",
        "source_field": "Project",
        "pattern": r"biz|neg|business",
        "pattern_type": "regex",
        "match_value": "Diretoria de Negocios",
        "target_field": "Diretoria",
        "priority": 20,
    },
    {
        "name": "SKU GPT/OpenAI -> Modelo AI",
        "source_field": "MeterCategory",
        "pattern": r"gpt|openai|azure.?openai",
        "pattern_type": "regex",
        "match_value": "OpenAI",
        "target_field": "ModeloAI",
        "priority": 5,
    },
]

# Default column mappings (Azure cost export standard columns -> normalized)
DEFAULT_MAPPINGS = [
    {"name": "Date -> date", "source_column": "Date", "target_column": "date", "transform_type": "RENAME", "priority": 1},
    {"name": "SubscriptionName -> subscription_name", "source_column": "SubscriptionName", "target_column": "subscription_name", "transform_type": "RENAME", "priority": 2},
    {"name": "ResourceGroup -> resource_group", "source_column": "ResourceGroup", "target_column": "resource_group", "transform_type": "RENAME", "priority": 3},
    {"name": "ServiceName -> service_name", "source_column": "ServiceName", "target_column": "service_name", "transform_type": "RENAME", "priority": 4},
    {"name": "MeterCategory -> meter_category", "source_column": "MeterCategory", "target_column": "meter_category", "transform_type": "RENAME", "priority": 5},
    {"name": "ResourceLocation -> region", "source_column": "ResourceLocation", "target_column": "region", "transform_type": "RENAME", "priority": 6},
    {"name": "ConsumedQuantity -> quantity", "source_column": "ConsumedQuantity", "target_column": "quantity", "transform_type": "RENAME", "priority": 7},
    {"name": "EffectivePrice -> effective_price", "source_column": "EffectivePrice", "target_column": "effective_price", "transform_type": "RENAME", "priority": 8},
    {"name": "CostInBillingCurrency -> cost_usd", "source_column": "CostInBillingCurrency", "target_column": "cost_usd", "transform_type": "RENAME", "priority": 9},
]


def _get_conn_id(db_result, code: str) -> int | None:
    """Find connection ID by code from list of (id, code) tuples."""
    for row in db_result:
        if row[1] == code:
            return row[0]
    return None


async def _seed_ptu_cost_links(db: AsyncSession) -> None:
    from app.models.ai_cost import AiCostLink, AiCostLinkSource
    from app.models.azure_ptu import AzurePtuProjectMapping

    existing = await db.execute(select(AiCostLink).where(AiCostLink.target_type == "ptu_pool"))
    if existing.scalars().first():
        return
    vivo = AiCostLink(
        name="Vivo Mais — PTU reservado",
        provider="Azure",
        product_name="Azure OpenAI PTU",
        target_type="ptu_pool",
        project_key="vivo-mais",
        purchase_mode="reservation",
        deployment_type="global",
        cost_component="capacity",
        allocation_level="project",
        active=True,
    )
    vivo.sources.append(AiCostLinkSource(
        filter_json={"conditions": [
            {"column": "cloud_origem", "operator": "CONTAINS", "values": ["Azure"]},
            {"column": "organograma", "operator": "IN", "values": ["Vivo Mais"]},
            {"column": "skumeter", "operator": "IN", "values": ["Provisioned Managed Global Unit", "Provisioned Managed Unit"]},
            {"column": "ReservationName", "operator": "NOT_EMPTY", "values": []},
        ]},
        value_column="realizado",
        percent=100,
    ))
    aura = AiCostLink(
        name="Aura Brain — PTU OnDemand",
        provider="Azure",
        product_name="Azure OpenAI PTU",
        target_type="ptu_pool",
        project_key="aura-brain",
        purchase_mode="ondemand",
        deployment_type="regional",
        cost_component="capacity",
        allocation_level="project",
        active=True,
    )
    aura.sources.append(AiCostLinkSource(
        filter_json={"conditions": [
            {"column": "cloud_origem", "operator": "CONTAINS", "values": ["Azure"]},
            {"column": "organograma", "operator": "IN", "values": ["Aura Brain"]},
            {"column": "skumeter", "operator": "IN", "values": ["Provisioned Managed Global Unit", "Provisioned Managed Unit"]},
            {"column": "ReservationName", "operator": "EMPTY", "values": []},
        ]},
        value_column="realizado",
        percent=100,
    ))
    db.add_all([vivo, aura])
    mapping_exists = await db.execute(select(AzurePtuProjectMapping))
    if not mapping_exists.scalars().first():
        # Os mappings do PTU apontam para a conexão do ambiente que hospeda a
        # fonte, não para uma conexão exclusiva de PTU (que não existe mais).
        conn_id = await db.execute(text("SELECT id FROM source_connection WHERE code = 'azure-4p'"))
        row = conn_id.fetchone()
        if row:
            db.add_all([
                AzurePtuProjectMapping(connection_id=row[0], deployment_resource_id="vivo-mais-35", project_key="vivo-mais", project_name="Vivo Mais", purchase_mode="reservation", deployment_type="global"),
                AzurePtuProjectMapping(connection_id=row[0], deployment_resource_id="vivo-mais-65", project_key="vivo-mais", project_name="Vivo Mais", purchase_mode="reservation", deployment_type="global"),
                AzurePtuProjectMapping(connection_id=row[0], deployment_resource_id="aura-brain-50a", project_key="aura-brain", project_name="Aura Brain", purchase_mode="ondemand", deployment_type="regional"),
                AzurePtuProjectMapping(connection_id=row[0], deployment_resource_id="aura-brain-50b", project_key="aura-brain", project_name="Aura Brain", purchase_mode="ondemand", deployment_type="regional"),
            ])


async def seed(db: AsyncSession):
    if settings.skip_seed:
        return

    # O PTU deixou de ser conexão própria: é uma **fonte** dentro da conexão do
    # ambiente (`azure_connection_source`), escolhida pelo operador. Recriar
    # `azure-4p-ptu` aqui duplicava as fontes da 4P a cada startup.
    await _seed_ptu_cost_links(db)

    existing = await db.execute(
        text("SELECT id FROM source_connection WHERE code = 'azure-4p'")
    )
    if existing.fetchone():
        await db.commit()
        await _seed_pivot_views(db)
        return

    # Insert connections
    connections = _build_connections()
    for conn_data in connections:
        db.add(SourceConnection(**conn_data))
    await db.flush()

    # Fetch all connection IDs
    result = await db.execute(text("SELECT id, code FROM source_connection"))
    all_conns = result.all()

    azure_4p_id = _get_conn_id(all_conns, "azure-4p")
    azure_ti_id = _get_conn_id(all_conns, "azure-ti")
    mongo_pro_id = _get_conn_id(all_conns, "mongodb-atlas-pro")
    mongo_enterprise_id = _get_conn_id(all_conns, "mongodb-atlas-enterprise")

    # Azure 4P: mappings + business rules + classifications
    if azure_4p_id:
        for m in DEFAULT_MAPPINGS:
            db.add(EtlMapping(connection_id=azure_4p_id, **m))
        for r in AZURE_BUSINESS_RULES:
            db.add(BusinessRule(connection_id=azure_4p_id, **r))
        for c in DEFAULT_CLASSIFICATIONS:
            db.add(ClassificationRule(connection_id=azure_4p_id, **c))

    # Azure TI: same mappings + business rules + classifications
    if azure_ti_id:
        for m in DEFAULT_MAPPINGS:
            db.add(EtlMapping(connection_id=azure_ti_id, **m))
        for r in AZURE_BUSINESS_RULES:
            db.add(BusinessRule(connection_id=azure_ti_id, **r))
        for c in DEFAULT_CLASSIFICATIONS:
            db.add(ClassificationRule(connection_id=azure_ti_id, **c))

    # MongoDB Pro: own PTAX rate + empty tax
    if mongo_pro_id:
        for r in MONGO_PRO_BUSINESS_RULES:
            db.add(BusinessRule(connection_id=mongo_pro_id, **r))

    # MongoDB Enterprise: own PTAX rate + empty tax
    if mongo_enterprise_id:
        for r in MONGO_ENTERPRISE_BUSINESS_RULES:
            db.add(BusinessRule(connection_id=mongo_enterprise_id, **r))

    await db.commit()
    await _seed_pivot_views(db)


async def _seed_pivot_views(db: AsyncSession):
    """Shared FinOps templates. They are config only, never financial rows."""
    focus = (await db.execute(text("SELECT id FROM destination_table WHERE upper(name) = 'MODELO FOCUS' LIMIT 1"))).scalar()
    if not focus:
        return
    templates = [
        ("Departamento / Projeto / Origem por mês", ["organograma", "projeto_nome", "cloud_origem"], ["_billing_period"]),
        ("Serviço por mês", ["ServiceName"], ["_billing_period"]),
        ("Região por mês", ["RegionName"], ["_billing_period"]),
        ("Assinatura por mês", ["SubscriptionName"], ["_billing_period"]),
        ("Custo por provider", ["cloud_origem"], []),
    ]
    for name, rows, columns in templates:
        exists = (await db.execute(select(PivotView.id).where(PivotView.destination_table_id == focus, PivotView.name == name))).scalar()
        if not exists:
            db.add(PivotView(name=name, destination_table_id=focus, config_json={
                "destination_table_id": focus, "rows": rows, "columns": columns,
                "measures": [{"column": "EffectiveCost", "aggregation": "SUM", "label": None}],
                "filters": [], "row_limit": 500, "column_limit": 60, "include_totals": True,
            }))
    await db.commit()
