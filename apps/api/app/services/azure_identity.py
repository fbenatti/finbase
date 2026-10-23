"""Azure identity helpers compartilhados entre coletores ARM/ARG.

Centraliza a leitura das credenciais persistidas na conexão e o
ClientSecretCredential usado por
azure_ptu_service e azure_resource_graph_service.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from azure.identity import ClientSecretCredential

logger = logging.getLogger(__name__)


def resolve_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Return only configuration persisted in source_connection.config_json."""
    return dict(config or {})


def credential(config: dict[str, Any]) -> ClientSecretCredential:
    """Monta o ClientSecretCredential a partir de uma config resolvida."""
    cfg = resolve_config(config)
    missing = [k for k in ("tenant_id", "client_id", "client_secret") if not cfg.get(k)]
    if missing:
        raise ValueError(
            f"Credenciais Azure incompletas na conexão: faltam {', '.join(missing)}. "
            "Configure tenant_id, client_id e client_secret no banco de dados."
        )
    return ClientSecretCredential(
        tenant_id=cfg["tenant_id"],
        client_id=cfg["client_id"],
        client_secret=cfg["client_secret"],
    )


async def arm_headers(config: dict[str, Any]) -> dict[str, str]:
    """Cabeçalhos de autorização para endpoints management.azure.com."""
    cred = credential(config)
    token = cred.get_token("https://management.azure.com/.default").token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def get_json(client: httpx.AsyncClient, url: str, headers: dict[str, str], params: dict[str, Any] | None = None) -> list[dict]:
    """Percorre paginação (nextLink) de um endpoint ARM e devolve a lista de values."""
    values: list[dict] = []
    next_url: str | None = url
    while next_url:
        resp = await client.get(next_url, headers=headers, params=params if next_url == url else None, timeout=60.0)
        if resp.status_code in (401, 403):
            raise PermissionError(f"Azure ARM {resp.status_code}: acesso negado em {url[:120]}")
        if resp.status_code >= 400:
            body = resp.text[:300]
            raise RuntimeError(f"Azure ARM {resp.status_code} em {url[:120]}: {body}")
        data = resp.json()
        values.extend(data.get("value") or [])
        next_url = data.get("nextLink")
    return values


# Escopo do Azure Monitor Managed Prometheus. É diferente do ARM: pedir token com
# o escopo do ARM devolve 401, que parece falta de permissão mas é escopo errado.
PROMETHEUS_SCOPE = "https://prometheus.monitor.azure.com/.default"


async def prometheus_headers(config: dict[str, Any]) -> dict[str, str]:
    """Cabeçalhos para o endpoint de consulta do Managed Prometheus."""
    cred = credential(config)
    token = cred.get_token(PROMETHEUS_SCOPE).token
    return {"Authorization": f"Bearer {token}"}
