"""Identidade de escopo (grão folha da detecção)."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

LEAF_KEYS: tuple[str, ...] = (
    "cloud_origem",
    "subaccountname",
    "resource_group",
    "servicename",
    "servicesubcategory",
    "chargecategory",
    "pricingcategory",
    "commitmentdiscounttype",
    "region",
)


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def scope_values_from_row(row: Mapping[str, Any]) -> dict[str, str]:
    rg = (
        row.get("resource_group")
        or row.get("source_resource_group_name")
        or row.get("resourcegroupname")
        or row.get("resource_group_name")
        or ""
    )
    region = row.get("region") or row.get("regionname") or row.get("location") or ""
    return {
        "cloud_origem": _norm(row.get("cloud_origem")),
        "subaccountname": _norm(row.get("subaccountname")),
        "resource_group": _norm(rg),
        "servicename": _norm(row.get("servicename")),
        "servicesubcategory": _norm(row.get("servicesubcategory")),
        "chargecategory": _norm(row.get("chargecategory")),
        "pricingcategory": _norm(row.get("pricingcategory")),
        "commitmentdiscounttype": _norm(row.get("commitmentdiscounttype")),
        "region": _norm(region),
    }


def scope_id_for(values: Mapping[str, Any], *, keys: Sequence[str] | None = None) -> str:
    chosen = list(keys or LEAF_KEYS)
    canonical = json.dumps(
        [[key, _norm(values.get(key))] for key in chosen],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def display_scope(values: Mapping[str, Any]) -> dict[str, str | None]:
    return {
        "cloud": values.get("cloud_origem") or None,
        "subaccount_name": values.get("subaccountname") or None,
        "resource_group": values.get("resource_group") or None,
        "service_name": values.get("servicename") or None,
        "service_category": values.get("servicesubcategory") or None,
        "charge_category": values.get("chargecategory") or None,
        "region": values.get("region") or None,
    }
