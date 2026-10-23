"""Network overrides to bypass private network DNS resolution issues.

Allows mapping Azure Storage hostnames directly to known IP addresses while
preserving TLS SNI hostname validation and Host HTTP headers.
"""
from __future__ import annotations

import logging
import os
import socket
from typing import Any

logger = logging.getLogger(__name__)

_STORAGE_HOST_MAP: dict[str, str] = {}
_ORIGINAL_GETADDRINFO = socket.getaddrinfo
_PATCH_INSTALLED = False


def _custom_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
    if isinstance(host, str):
        target_ip = _STORAGE_HOST_MAP.get(host.lower().strip())
        if target_ip:
            host = target_ip
    return _ORIGINAL_GETADDRINFO(host, *args, **kwargs)


def register_storage_ip(storage_account_or_host: str, ip: str | None = None) -> str | None:
    """Register direct IP resolution for an Azure Storage account or hostname.

    If ``ip`` is None, checks environment variables. Hosts without an explicit
    override use the system resolver.
    """
    global _PATCH_INSTALLED
    cleaned = storage_account_or_host.lower().strip()
    account_name = cleaned.split(".")[0]
    full_host = f"{account_name}.blob.core.windows.net"

    target_ip = (
        ip
        or os.getenv(f"AZURE_STORAGE_IP_{account_name.upper()}")
        or os.getenv("AZURE_STORAGE_IP")
    )

    if target_ip:
        _STORAGE_HOST_MAP[account_name] = target_ip
        _STORAGE_HOST_MAP[full_host] = target_ip
        if not _PATCH_INSTALLED:
            socket.getaddrinfo = _custom_getaddrinfo
            _PATCH_INSTALLED = True
            logger.info("Direct IP resolver installed for Azure Blob Storages")
        logger.info("Storage host '%s' mapped directly to IP '%s'", full_host, target_ip)
        return target_ip

    return None
