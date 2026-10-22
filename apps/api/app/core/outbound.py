"""Validação de destino de saída (anti-SSRF).

Credenciais (tokens, chaves Atlas) só podem ser enviadas a hosts conhecidos.
Sem esta checagem, quem edita uma conexão aponta o endereço para um servidor
próprio e recebe o token, ou faz a API alcançar a rede interna.

A checagem é só de nome de host (sem resolver DNS). Não cobre DNS rebinding.
"""
import ipaddress
import os
from collections.abc import Iterable
from urllib.parse import urlsplit

# Domínios padrão por tipo de serviço. Subdomínios também são aceitos.
DATABRICKS_HOSTS = (
    "azuredatabricks.net",
    "gcp.databricks.com",
    "cloud.databricks.com",
)
MONGODB_ATLAS_HOSTS = ("cloud.mongodb.com",)


def _env_hosts(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    return tuple(h.strip().lower().lstrip(".") for h in raw.split(",") if h.strip())


def integration_allowed_hosts() -> tuple[str, ...]:
    """Hosts de integrações (ex.: serviço Jev). Configurados por INTEGRATION_ALLOWED_HOSTS."""
    return _env_hosts("INTEGRATION_ALLOWED_HOSTS")


def ensure_allowed_host(host: str | None, allowed: Iterable[str]) -> str:
    """Retorna o host normalizado ou levanta ValueError se não estiver na lista."""
    normalized = (host or "").strip().lower().rstrip(".")
    if not normalized:
        raise ValueError("Host ausente.")
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        pass
    else:
        raise ValueError("Endereço IP direto não é permitido; use o nome do serviço.")
    for suffix in allowed:
        suffix = suffix.lower().lstrip(".")
        if normalized == suffix or normalized.endswith("." + suffix):
            return normalized
    raise ValueError(f"Host '{normalized}' não está na lista de destinos permitidos.")


def ensure_allowed_url(url: str, allowed: Iterable[str]) -> str:
    """Valida esquema https, ausência de credenciais na URL e host permitido."""
    parts = urlsplit(url.strip())
    if parts.scheme != "https":
        raise ValueError("A URL precisa usar https.")
    if parts.username or parts.password:
        raise ValueError("A URL não pode conter usuário ou senha.")
    ensure_allowed_host(parts.hostname, allowed)
    return url.strip()
