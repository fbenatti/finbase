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

# Default known infrastructure IP addresses for private Azure Blob Storages
_DEFAULT_STORAGE_IPS: dict[str, str] = {
    "reportfinopsti": "20.209.12.41",
    "reportfinopsti.blob.core.windows.net": "20.209.12.41",
    "proazurecostsmanagement": "20.60.37.33",
    "proazurecostsmanagement.blob.core.windows.net": "20.60.37.33",
}

_STORAGE_HOST_MAP: dict[str, str] = dict(_DEFAULT_STORAGE_IPS)
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

    If ``ip`` is None, checks environment variables or pre-configured defaults.
    """
    global _PATCH_INSTALLED
    cleaned = storage_account_or_host.lower().strip()
    account_name = cleaned.split(".")[0]
    full_host = f"{account_name}.blob.core.windows.net"

    target_ip = (
        ip
        or os.getenv(f"AZURE_STORAGE_IP_{account_name.upper()}")
        or os.getenv("AZURE_STORAGE_IP")
        or _DEFAULT_STORAGE_IPS.get(account_name)
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


# Automatically install resolution for known default infrastructure storages on module load
for account, default_ip in _DEFAULT_STORAGE_IPS.items():
    register_storage_ip(account, default_ip)
