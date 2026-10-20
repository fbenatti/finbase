"""Consolidação hierárquica de sinais em incidentes."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from app.services.anomaly_v2.detectors import TIER_ALERT


def _parent_key(scope_row: Mapping[str, Any], level: str) -> str:
    cloud = scope_row.get("cloud") or scope_row.get("cloud_origem") or ""
    sub = scope_row.get("subaccountname") or scope_row.get("subaccount_name") or ""
    rg = scope_row.get("resource_group") or ""
    svc = scope_row.get("service_name") or scope_row.get("servicename") or ""
    if level == "cloud":
        return f"cloud={cloud}"
    if level == "subaccount":
        return f"cloud={cloud}|sub={sub}"
    if level == "rg":
        return f"cloud={cloud}|sub={sub}|rg={rg}"
    return f"cloud={cloud}|sub={sub}|rg={rg}|svc={svc}"


def consolidate_alerts(
    alerts: Sequence[Mapping[str, Any]],
    scope_meta: Mapping[str, Mapping[str, Any]],
    *,
    explain_threshold: float = 0.70,
) -> list[dict[str, Any]]:
    """Agrupa alertas folha no escopo pai que explica o desvio."""
    if not alerts:
        return []
    by_parent: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for alert in alerts:
        sid = str(alert["scope_id"])
        meta = scope_meta.get(sid, {})
        parent = _parent_key(meta, "subaccount")
        by_parent[parent].append(alert)

    incidents: list[dict[str, Any]] = []
    for parent, group in by_parent.items():
        total_delta = sum(float(a.get("delta") or 0) for a in group)
        leader = max(group, key=lambda a: float(a.get("delta") or 0))
        contributors = sorted(group, key=lambda a: float(a.get("delta") or 0), reverse=True)
        incidents.append(
            {
                "parent_key": parent,
                "primary_scope_id": leader["scope_id"],
                "total_delta": total_delta,
                "leader": dict(leader),
                "contributors": [dict(c) for c in contributors],
                "scope_level": "subaccount",
            }
        )
    return incidents
