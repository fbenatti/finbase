"""Catálogo declarativo das fontes Azure e agrupamento por ambiente.

A lista em `AZURE_SOURCE_CATALOG` é a fonte de verdade do que pode ser ligado
por ambiente. O seed (`scripts/seed_azure_source_catalog.py`) a projeta na tabela
`azure_source_catalog` de forma idempotente.

O agrupamento é **por ambiente/tenant**, resolvido a partir do `code` da conexão
legada — que não é renomeado. Assim o operador vê um grupo Azure só, com todas as
fontes selecionáveis, sem precisar de dezenas de cards.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.cost_sources import COST_EXPORTS_KEY

#: Ordem e descrição das fontes possíveis. `code_pattern` usa `{env}`.
AZURE_SOURCE_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "source_key": COST_EXPORTS_KEY,
        "display_name": "Cost Management (FOCUS)",
        "description": "Export de custo do Cost Management. É o livro financeiro detalhado.",
        "position": 10,
        "connector_type": "azure_blob",
        "code_pattern": "azure-{env}",
        "bronze_pattern": "raw_azure_{env}",
        "silver_target": "silver.dest_modelo_focus",
        "gold_target": "gold.cost_cube",
        "is_cost_source": True,
        "api_reference": "Cost Management Exports (Blob Storage)",
        "required_permission": "Cost Management Reader (billing scope)",
        "azure_scope": "subscription",
        "implementation_status": "implemented",
    },
    {
        "source_key": "resource_graph",
        "display_name": "Resource Graph (inventário e órfãos)",
        "description": "Inventário de recursos ARM e detecção de órfãos.",
        "position": 20,
        "connector_type": "azure_resource_graph",
        "code_pattern": "azure-{env}-resource-graph",
        "bronze_pattern": "raw_azure_{env}_resource_graph",
        "silver_target": "silver.dest_cloud_resource_inventory, silver.dest_cloud_orphan_resources",
        "gold_target": "gold.azure_orphan_cube",
        "is_cost_source": False,
        "api_reference": "Microsoft.ResourceGraph/resources (2022-10-01)",
        "required_permission": "Reader nas subscriptions",
        "azure_scope": "subscription",
        "implementation_status": "implemented",
    },
    {
        "source_key": "automation_jobs",
        "display_name": "Automation (jobs e schedules)",
        "description": "Jobs e schedules dos runbooks de start/stop — evidência de desligue.",
        "position": 30,
        "connector_type": "azure_automation_jobs",
        "code_pattern": "azure-{env}-automation",
        "bronze_pattern": "raw_azure_{env}_automation",
        "silver_target": "silver.dest_modelo_automation, silver.dest_modelo_automation_schedule",
        "gold_target": "gold.azure_automation_cube",
        "is_cost_source": False,
        "api_reference": "Microsoft.Automation automationAccounts (2023-11-01)",
        "required_permission": "Reader no Automation Account",
        "azure_scope": "subscription",
        "implementation_status": "implemented",
    },
    {
        "source_key": "monitor_vm_metrics",
        "display_name": "Azure Monitor — métricas de VM",
        "description": "Série de métricas de plataforma das VMs (15 min), para perfil e rightsizing.",
        "position": 40,
        "connector_type": "azure_monitor_vm_metrics",
        "code_pattern": "azure-{env}-monitor-vm",
        "bronze_pattern": "raw_azure_{env}_monitor_vm",
        "silver_target": "silver.azure_vm_metric_series",
        "gold_target": "gold.azure_vm_profile_cube",
        "is_cost_source": False,
        "api_reference": "{region}.metrics.monitor.azure.com metrics:getBatch (2023-10-01)",
        "required_permission": "Monitoring Reader na subscription + provider Microsoft.Insights",
        "default_window_days": 15,
        "default_metrics": [
            "Percentage CPU",
            "Network In Total",
            "Network Out Total",
            "Disk Read Operations/Sec",
            "Disk Write Operations/Sec",
            "Disk Read Bytes",
            "Disk Write Bytes",
        ],
        "azure_scope": "subscription",
        "implementation_status": "implemented",
    },
    {
        "source_key": "vm_inventory",
        "display_name": "VM — identidade e inventário",
        "description": "VMs e VMSS com assinatura, resource group, região, SKU, estado de energia, SO e tags. Chave do custo por VM.",
        "position": 50,
        "connector_type": "azure_vm_inventory",
        "code_pattern": "azure-{env}-vm-inventory",
        "bronze_pattern": "raw_azure_{env}_vm_inventory",
        "silver_target": "silver.azure_vm_inventory",
        "gold_target": None,
        "is_cost_source": False,
        "api_reference": "Resource Graph resources | where type =~ microsoft.compute/virtualmachines",
        "required_permission": "Reader na subscription",
        "azure_scope": "subscription",
        "implementation_status": "implemented",
    },
    {
        "source_key": "compute_skus",
        "display_name": "Catálogo de SKUs de compute",
        "description": "SKUs disponíveis por região/subscription — elegibilidade de resize.",
        "position": 60,
        "connector_type": "azure_compute_skus",
        "code_pattern": "azure-{env}-compute-skus",
        "bronze_pattern": "raw_azure_{env}_compute_skus",
        "silver_target": "silver.azure_compute_sku_catalog",
        "gold_target": None,
        "is_cost_source": False,
        "api_reference": "Microsoft.Compute/skus (Resource Skus List)",
        "required_permission": "Reader na subscription",
        "azure_scope": "subscription",
        "implementation_status": "planned",
    },
    {
        "source_key": "compute_quota",
        "display_name": "Quota de compute",
        "description": "Quota por família e região — veto de candidato sem quota.",
        "position": 70,
        "connector_type": "azure_compute_quota",
        "code_pattern": "azure-{env}-compute-quota",
        "bronze_pattern": "raw_azure_{env}_compute_quota",
        "silver_target": "silver.azure_compute_quota",
        "gold_target": None,
        "is_cost_source": False,
        "api_reference": "Microsoft.Compute locations/{loc}/usages",
        "required_permission": "Reader na subscription",
        "azure_scope": "subscription",
        "implementation_status": "planned",
    },
    {
        "source_key": "retail_price",
        "display_name": "Preço público (Retail Prices)",
        "description": "Preço público como fallback indicativo quando não há preço contratado.",
        "position": 80,
        "connector_type": "azure_retail_price",
        "code_pattern": "azure-{env}-retail-price",
        "bronze_pattern": "raw_azure_{env}_retail_price",
        "silver_target": "silver.azure_retail_price",
        "gold_target": None,
        "is_cost_source": False,
        "api_reference": "prices.azure.com/api/retail/prices",
        "required_permission": "público (sem autenticação)",
        "azure_scope": "catalog",
        "implementation_status": "planned",
    },
    {
        "source_key": "customer_price",
        "display_name": "Preço contratado (Price Sheet)",
        "description": "Preço do contrato (EA/MCA) quando a API permitir — prioridade sobre o retail.",
        "position": 90,
        "connector_type": "azure_customer_price",
        "code_pattern": "azure-{env}-customer-price",
        "bronze_pattern": "raw_azure_{env}_customer_price",
        "silver_target": "silver.azure_customer_price",
        "gold_target": None,
        "is_cost_source": False,
        "api_reference": "Microsoft.CostManagement Price Sheet (download by billing account)",
        "required_permission": "Billing Reader no billing scope",
        "azure_scope": "catalog",
        "implementation_status": "planned",
    },
    {
        "source_key": "openai_ptu",
        "display_name": "Azure OpenAI PTU",
        "description": "Métricas e capacidade de PTU. Fluxo operacional próprio, preservado.",
        "position": 100,
        "connector_type": "azure_openai_ptu",
        "code_pattern": "azure-{env}-ptu",
        "bronze_pattern": "raw_azure_{env}_ptu",
        "silver_target": None,
        "gold_target": "gold.dest_azure_ptu_metrics, gold.dest_azure_ptu_capacity",
        "is_cost_source": False,
        "api_reference": "microsoft.insights/metrics + Cognitive Services deployments",
        "required_permission": "Monitoring Reader no recurso",
        "azure_scope": "resource",
        "implementation_status": "implemented",
    },
    {
        "source_key": "aks_inventory",
        "display_name": "AKS — inventário e topologia",
        "description": "Cluster → node pool/node → namespace → workload → pod/container.",
        "position": 110,
        "connector_type": "azure_aks_inventory",
        "code_pattern": "azure-{env}-aks-inventory",
        "bronze_pattern": "raw_azure_{env}_aks_inventory",
        "silver_target": "silver.azure_consumption_entity_snapshot, silver.azure_consumption_edge_snapshot",
        "gold_target": "gold.azure_consumption_relationship",
        "is_cost_source": False,
        "api_reference": "Kubernetes API / Container Insights (spike em P4)",
        "required_permission": "a validar no spike",
        "azure_scope": "resource",
        "implementation_status": "planned",
    },
    {
        "source_key": "aks_cost_allocation",
        "display_name": "AKS — alocação de custo",
        "description": "Custo por namespace/workload, reconciliado ao FOCUS.",
        "position": 120,
        "connector_type": "azure_aks_cost_allocation",
        "code_pattern": "azure-{env}-aks-cost-allocation",
        "bronze_pattern": "raw_azure_{env}_aks_cost_allocation",
        "silver_target": "silver.azure_focus_consumption_allocation",
        "gold_target": "gold.azure_consumption_relationship",
        "is_cost_source": False,
        "api_reference": "AKS Cost Analysis / OpenCost (condicionado ao add-on)",
        "required_permission": "a validar no spike",
        "azure_scope": "subscription",
        "implementation_status": "planned",
    },
    {
        "source_key": "container_insights",
        "display_name": "Container Insights",
        "description": "Consumo runtime de node/pod/container, se necessário para a topologia.",
        "position": 130,
        "connector_type": "azure_container_insights",
        "code_pattern": "azure-{env}-container-insights",
        "bronze_pattern": "raw_azure_{env}_container_insights",
        "silver_target": "silver.azure_container_metric_series",
        "gold_target": None,
        "is_cost_source": False,
        "api_reference": "Log Analytics / Container Insights",
        "required_permission": "Log Analytics Reader no workspace",
        "azure_scope": "resource",
        "implementation_status": "planned",
    },
    {
        "source_key": "region_policy",
        "display_name": "Policy de região",
        "description": "Regiões permitidas e restrições que vetam mudança de região.",
        "position": 140,
        "connector_type": "azure_region_policy",
        "code_pattern": "azure-{env}-region-policy",
        "bronze_pattern": "raw_azure_{env}_region_policy",
        "silver_target": "silver.azure_region_policy",
        "gold_target": None,
        "is_cost_source": False,
        "api_reference": "Azure Policy assignments (Reader)",
        "required_permission": "Reader na subscription",
        "azure_scope": "subscription",
        "implementation_status": "planned",
    },
)

#: `source_key` por `connector_type`, para ligar conexões existentes ao catálogo.
CONNECTOR_TO_SOURCE_KEY: dict[str, str] = {
    "azure_blob": COST_EXPORTS_KEY,
    "azure_resource_graph": "resource_graph",
    "azure_automation_jobs": "automation_jobs",
    "azure_monitor_vm_metrics": "monitor_vm_metrics",
    "azure_vm_inventory": "vm_inventory",
    "azure_openai_ptu": "openai_ptu",
    "azure_compute_skus": "compute_skus",
    "azure_compute_quota": "compute_quota",
    "azure_retail_price": "retail_price",
    "azure_customer_price": "customer_price",
    "azure_aks_inventory": "aks_inventory",
    "azure_aks_cost_allocation": "aks_cost_allocation",
    "azure_container_insights": "container_insights",
    "azure_region_policy": "region_policy",
}

#: Sufixos conhecidos nos codes legados que indicam a fonte (não o ambiente).
_SOURCE_SUFFIXES = (
    "resource-graph", "automation", "monitor-vm", "ptu",
    "aks-inventory", "aks-cost-allocation", "container-insights",
    "compute-skus", "compute-quota", "retail-price", "customer-price",
    "region-policy",
)


def environment_slug(connection: Any) -> str:
    """Deriva o slug do ambiente/tenant a partir da conexão.

    O ambiente é a **credencial compartilhada**: conexões com o mesmo
    `tenant_id`/`client_id` pertencem ao mesmo grupo, porque é isso que evita
    repetir segredo. Assim `azure-4p`, `azure-4p-ptu`, `azure-automation-4p` e
    `azure-4p-resource-graph` caem no mesmo grupo `4p`, e o operador configura
    uma vez só.

    Ordem de resolução:

    1. `config_json.environment_slug` explícito (metadata manda);
    2. `cloud_name` normalizado (`Azure-4P` → `4p`) — rótulo de tenant já
       preenchido nas conexões existentes;
    3. `code` sem o prefixo `azure-` e sem o sufixo de fonte.

    Nada é renomeado: `code` continua sendo a identidade do pipeline.
    """
    config = getattr(connection, "config_json", None) or {}
    if isinstance(config, dict) and config.get("environment_slug"):
        return str(config["environment_slug"]).strip().lower()

    cloud_name = str(getattr(connection, "cloud_name", "") or "").strip()
    if cloud_name:
        # `Azure-4P` → `4p`; `Azure Marketplace` → `marketplace`.
        cleaned = re.sub(r"(?i)^azure[\s\-_]*", "", cloud_name).strip()
        cleaned = re.sub(r"[\s_]+", "-", cleaned).lower()
        if cleaned:
            return cleaned

    code = str(getattr(connection, "code", "") or "").strip().lower()
    if not code:
        return "default"
    if code.startswith("azure-"):
        code = code[len("azure-"):]
    tokens = [t for t in code.split("-") if t]
    if not tokens:
        return "default"
    # Remove o sufixo de fonte, se houver, mantendo o resto como ambiente.
    cleaned_tokens = list(tokens)
    for suffix in _SOURCE_SUFFIXES:
        parts = suffix.split("-")
        if len(cleaned_tokens) >= len(parts) and cleaned_tokens[-len(parts):] == parts:
            cleaned_tokens = cleaned_tokens[: -len(parts)]
            break
    # `automation-4p` / `resource-graph-ti`: o ambiente é o último token.
    if cleaned_tokens and cleaned_tokens[0] in {"automation", "resource", "monitor", "aks", "compute"}:
        return cleaned_tokens[-1]
    return "-".join(cleaned_tokens) if cleaned_tokens else "default"


def shared_credential_key(connection: Any) -> str:
    """Identidade da credencial compartilhada (nunca o segredo em si).

    Devolve um marcador estável para agrupar conexões que usam a mesma
    credencial, sem expor nem correlacionar o valor: apenas o tenant e o client
    concatenados. Sem credencial (ex.: fontes de upload manual) devolve string
    vazia, e o agrupamento cai no ambiente.
    """
    config = getattr(connection, "config_json", None) or {}
    if not isinstance(config, dict):
        return ""
    tenant = str(config.get("tenant_id") or "").strip()
    client = str(config.get("client_id") or "").strip()
    if not tenant and not client:
        return ""
    return f"{tenant}:{client}"


def source_key_for(connection: Any) -> str | None:
    """`source_key` da conexão: metadata explícita tem prioridade sobre o tipo."""
    config = getattr(connection, "config_json", None) or {}
    if isinstance(config, dict) and config.get("source_key"):
        return str(config["source_key"])
    return CONNECTOR_TO_SOURCE_KEY.get(str(getattr(connection, "connector_type", "")))


def is_telemetry(source_key: str | None) -> bool:
    """Fontes de telemetria carregam janela e granularidade, não período fiscal."""
    return source_key in {"monitor_vm_metrics", "container_insights"}


def catalog_entry(source_key: str) -> dict[str, Any] | None:
    for entry in AZURE_SOURCE_CATALOG:
        if entry["source_key"] == source_key:
            return entry
    return None


def accepted_code_pattern(entry: dict[str, Any], env: str) -> str:
    return str(entry.get("code_pattern") or "").format(env=env)


def sanitized_bronze(connection: Any) -> str:
    """Nome físico da tabela Bronze, como `iceberg_service._bronze_table_id`."""
    code = re.sub(r"[-.]", "_", str(getattr(connection, "code", "") or ""))
    return f"raw_{code}"

# Sufixo do `code` por fonte. O nome físico da Bronze é `raw_{code}`, então o
# sufixo define a tabela: manter o histórico exige preservar os nomes já em uso
# (raw_azure_4p, raw_azure_4p_monitor_vm, ...). Uma conexão só passa a escrever
# várias tabelas por causa deste mapa — antes cada fonte era um `code` próprio.
SOURCE_CODE_SUFFIX: dict[str, str] = {
    "cost_exports": "",
    "vm_inventory": "vm-inventory",
    "monitor_vm_metrics": "monitor-vm",
    "resource_graph": "resource-graph",
    "automation_jobs": "automation",
    "openai_ptu": "ptu",
    "compute_skus": "compute-skus",
    "compute_quota": "compute-quota",
    "retail_price": "retail-price",
    "customer_price": "customer-price",
    "aks_inventory": "aks-inventory",
    "aks_cost_allocation": "aks-cost-allocation",
    "container_insights": "container-insights",
    "region_policy": "region-policy",
}


def source_code(connection_code: str, source_key: str) -> str:
    """`code` que a coleta de uma fonte usa.

    É a identidade da Bronze daquela fonte: `raw_{source_code}`. Com uma conexão
    coletando várias fontes, cada uma precisa do seu próprio nome para não
    sobrescrever a tabela da outra. Para `cost_exports` o sufixo é vazio, então
    o `code` continua sendo o da conexão e os nomes históricos não mudam.
    """
    suffix = SOURCE_CODE_SUFFIX.get(source_key)
    if suffix is None:
        suffix = re.sub(r"[^a-z0-9]+", "-", source_key.lower()).strip("-")
    if not suffix:
        return connection_code
    return f"{connection_code}-{suffix}"


def bronze_table_for(connection_code: str, source_key: str) -> str:
    """Nome físico da tabela Bronze da fonte."""
    code = re.sub(r"[-.]", "_", source_code(connection_code, source_key))
    return f"raw_{code}"
