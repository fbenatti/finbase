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
)

#: `source_key` por `connector_type`, para ligar conexões existentes ao catálogo.
CONNECTOR_TO_SOURCE_KEY: dict[str, str] = {
    "azure_blob": COST_EXPORTS_KEY,
    "azure_resource_graph": "resource_graph",
    "azure_automation_jobs": "automation_jobs",
    "azure_openai_ptu": "openai_ptu",
}

#: Sufixos conhecidos nos codes legados que indicam a fonte (não o ambiente).
_SOURCE_SUFFIXES = (
    "resource-graph", "automation", "ptu",
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
    return False


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
    "resource_graph": "resource-graph",
    "automation_jobs": "automation",
    "openai_ptu": "ptu",
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
