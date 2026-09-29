"""CPU-bound reprocess chunk transforms for ProcessPool workers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def _namespace(data: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(**data)


def serialize_business_rule(rule: Any) -> dict[str, Any]:
    return {
        "id": rule.id,
        "name": rule.name,
        "target_column": rule.target_column,
        "rule_type": rule.rule_type,
        "config_json": rule.config_json,
        "conditions": rule.conditions,
        "expression": rule.expression,
        "priority": rule.priority,
        "valid_from": rule.valid_from,
        "valid_until": rule.valid_until,
        "cloud_scope": rule.cloud_scope,
        "financial_effect": getattr(rule, "financial_effect", "NONE"),
        "financial_contract_id": getattr(rule, "financial_contract_id", None),
        "active": rule.active,
    }


def serialize_classification_rule(rule: Any) -> dict[str, Any]:
    return {
        "id": rule.id,
        "department_id": rule.department_id,
        "name": rule.name,
        "source_field": rule.source_field,
        "pattern": rule.pattern,
        "pattern_type": rule.pattern_type,
        "match_value": rule.match_value,
        "target_field": rule.target_field,
        "conditions": rule.conditions,
        "priority": rule.priority,
        "cloud_scope": rule.cloud_scope,
        "active": rule.active,
    }


def serialize_mapping(mapping: Any) -> dict[str, Any]:
    return {
        "id": mapping.id,
        "source_column": mapping.source_column,
        "destination_column": mapping.destination_column,
        "transform_type": mapping.transform_type,
        "transform_config": mapping.transform_config,
        "priority": mapping.priority,
    }


def serialize_department(dept: Any) -> dict[str, Any]:
    return {
        "id": dept.id,
        "parent_id": dept.parent_id,
        "name": dept.name,
        "centro_custo": dept.centro_custo,
        "id_orcamento": getattr(dept, "id_orcamento", None),
        "ambiente": dept.ambiente,
        "cloud_filter": dept.cloud_filter,
        "custom_fields": dept.custom_fields,
        "active": dept.active,
    }


def serialize_hierarchy_cache(hierarchy_cache: dict[int, tuple]) -> dict[str, dict[str, Any]]:
    serialized: dict[str, dict[str, Any]] = {}
    for dept_id, entry in hierarchy_cache.items():
        if not entry:
            serialized[str(dept_id)] = {"leaf": None, "root": None, "chain": []}
            continue
        leaf, root, chain = entry
        serialized[str(dept_id)] = {
            "leaf": serialize_department(leaf) if leaf else None,
            "root": serialize_department(root) if root else None,
            "chain": [serialize_department(item) for item in chain],
        }
    return serialized


def deserialize_hierarchy_cache(
    payload: dict[str, dict[str, Any]],
) -> dict[int, tuple[Any, Any, list[Any]]]:
    hierarchy_cache: dict[int, tuple[Any, Any, list[Any]]] = {}
    for key, entry in payload.items():
        dept_id = int(key)
        leaf = _namespace(entry["leaf"]) if entry.get("leaf") else None
        root = _namespace(entry["root"]) if entry.get("root") else None
        chain = [_namespace(item) for item in entry.get("chain") or []]
        hierarchy_cache[dept_id] = (leaf, root, chain)
    return hierarchy_cache


def build_chunk_payload(
    *,
    records: list[dict[str, Any]],
    connection_id: int,
    connection_code: str,
    billing_period: str,
    brules: list[Any],
    crules: list[Any],
    cmapps: list[Any],
    dept_cache: dict[int, Any],
    hierarchy_cache: dict[int, tuple],
    dest_col_names: set[str],
) -> dict[str, Any]:
    return {
        "records": records,
        "connection_id": connection_id,
        "connection_code": connection_code,
        "billing_period": str(billing_period),
        "brules": [serialize_business_rule(rule) for rule in brules],
        "crules": [serialize_classification_rule(rule) for rule in crules],
        "cmapps": [serialize_mapping(mapping) for mapping in cmapps],
        "dept_cache": {str(key): serialize_department(value) for key, value in dept_cache.items()},
        "hierarchy_cache": serialize_hierarchy_cache(hierarchy_cache),
        "dest_col_names": sorted(dest_col_names),
    }


def transform_reprocess_chunk(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Transform one Bronze chunk without I/O; safe for ProcessPool workers."""
    import pandas as pd

    from app.services.mapping_transforms import apply_mappings
    from app.services.task_runner import _apply_business_rule_sync, _apply_classifications_sync

    chunk = pd.DataFrame(payload["records"])
    df_chunk = chunk.drop(
        columns=[c for c in ["_billing_period", "_blob_path", "_ingested_at"] if c in chunk.columns]
    )
    cmapps = [_namespace(item) for item in payload["cmapps"]]
    brules = [_namespace(item) for item in payload["brules"]]
    crules = [_namespace(item) for item in payload["crules"]]
    dept_cache = {int(key): _namespace(value) for key, value in payload["dept_cache"].items()}
    hierarchy_cache = deserialize_hierarchy_cache(payload["hierarchy_cache"])
    dest_col_names = set(payload["dest_col_names"])
    billing_period = payload["billing_period"]

    target = apply_mappings(df_chunk, cmapps)
    target["cloud_origem"] = payload["connection_code"]
    target["source_connection_id"] = payload["connection_id"]
    target["_billing_period"] = billing_period
    target["tipo"] = "realizado"

    if "TotalCostOnDemandBRL" in dest_col_names:
        def _num(col: str) -> pd.Series:
            return pd.to_numeric(
                target[col] if col in target.columns else pd.Series([None] * len(target), index=target.index),
                errors="coerce",
            )

        direct_cost = _num("BilledCost")
        quantity = _num("ConsumedQuantity")
        unit_price = _num("source_unit_price")
        exchange = _num("source_exchange_rate")
        calculated = unit_price * quantity * exchange.fillna(1)
        charge = target.get("ChargeCategory", pd.Series("", index=target.index)).astype(str).str.lower()
        contractual = charge.isin({"purchase", "refund", "roundingadjustment"})
        target["TotalCostOnDemandBRL"] = calculated.where(
            calculated.notna() & calculated.ne(0) & ~contractual,
            direct_cost,
        )

    for rule in brules:
        _apply_business_rule_sync(target, rule, billing_period)

    if "consumo_realizado" in target.columns:
        target["realizado"] = pd.to_numeric(target["consumo_realizado"], errors="coerce")
        target["ajuste_contratual"] = 0.0

    if crules:
        _apply_classifications_sync(
            target,
            crules,
            None,
            dept_map=dept_cache,
            hierarchy_cache=hierarchy_cache,
        )

    if target.empty:
        return []
    return target.to_dict(orient="records")
