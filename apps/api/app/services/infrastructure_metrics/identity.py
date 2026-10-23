"""Stable, versioned identities for metric streams and their dimensions."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


DIMENSIONS_HASH_VERSION = "v1"
SERIES_IDENTITY_VERSION = "v2"


def canonical_json(value: Any) -> str:
    """Canonical JSON used for hashes; mapping insertion order is irrelevant."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def dimensions_hash(dimensions: Mapping[str, Any]) -> str:
    payload = f"{DIMENSIONS_HASH_VERSION}:{canonical_json(dimensions)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def series_id(*, provider: str, connection_id: int, account_id: str, region: str,
              resource_type: str, resource_id: str, metric: str,
              dimensions: Mapping[str, Any], namespace: str | None = None,
              metric_scope: str | None = None, source_owner: str | None = None,
              cloud_partition: str | None = None,
              sub_account_id: str | None = None) -> str:
    """Hash full source/resource/metric identity; excludes bucket timestamps."""
    identity = {
        "version": SERIES_IDENTITY_VERSION,
        "provider": provider.strip().lower(),
        "connection_id": int(connection_id),
        "account_id": account_id,
        "region": region,
        "cloud_partition": cloud_partition,
        "sub_account_id": sub_account_id,
        "namespace": namespace,
        "metric_scope": metric_scope,
        "source_owner": source_owner,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "metric": metric,
        "dimensions_hash": dimensions_hash(dimensions),
    }
    return hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
