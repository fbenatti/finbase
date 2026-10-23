"""Stable, versioned identities for metric streams and their dimensions."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


IDENTITY_VERSION = "v1"


def canonical_json(value: Any) -> str:
    """Canonical JSON used for hashes; mapping insertion order is irrelevant."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def dimensions_hash(dimensions: Mapping[str, Any]) -> str:
    payload = f"{IDENTITY_VERSION}:{canonical_json(dimensions)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def series_id(*, provider: str, connection_id: int, account_id: str, region: str,
              resource_type: str, resource_id: str, metric: str,
              dimensions: Mapping[str, Any]) -> str:
    """Hash full source/resource/metric identity; excludes bucket timestamps."""
    identity = {
        "version": IDENTITY_VERSION,
        "provider": provider.strip().lower(),
        "connection_id": int(connection_id),
        "account_id": account_id,
        "region": region,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "metric": metric,
        "dimensions_hash": dimensions_hash(dimensions),
    }
    return hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
