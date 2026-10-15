"""Prontidão por nuvem e idempotência de execução."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from typing import Any, Mapping, Sequence


def max_day_by_cloud(daily_rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    best: dict[str, str] = {}
    for row in daily_rows:
        cloud = str(row.get("cloud") or row.get("cloud_origem") or "")
        day = str(row.get("day") or "")[:10]
        if not cloud or not day:
            continue
        if cloud not in best or day > best[cloud]:
            best[cloud] = day
    return best


def readiness_fingerprint(max_by_cloud: Mapping[str, str], config_version: int) -> str:
    payload = {"config_version": config_version, "clouds": dict(sorted(max_by_cloud.items()))}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def clouds_advanced(
    current: Mapping[str, str],
    previous: Mapping[str, str] | None,
) -> bool:
    if not previous:
        return bool(current)
    for cloud, day in current.items():
        if day > (previous.get(cloud) or ""):
            return True
    return False
