"""Compile the existing classification semantics into one Trino CTAS query."""
from __future__ import annotations

import json
from typing import Any

from app.services.classification_engine import _canon

_CLASSIFIED_COLUMNS = {
    "diretoria", "departamento", "centro_custo", "ambiente", "custom_fields",
    "subscription_rg", "projeto_nome", "departamento_nivel_1",
    "departamento_nivel_2", "departamento_nivel_3", "departamento_nivel_4",
    "departamento_nivel_5",
}


def qi(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def lit(value: Any) -> str:
    return "'" + str(value or "").replace("'", "''") + "'"


def canonical(column: str) -> str:
    """SQL equivalent of classification_engine._canon for provider strings."""
    raw = f"CAST(b.{qi(column)} AS VARCHAR)"
    return ("lower(regexp_replace(translate(coalesce(" + raw + ", ''), "
            "'áàãâäéèêëíìîïóòõôöúùûüçñÁÀÃÂÄÉÈÊËÍÌÎÏÓÒÕÔÖÚÙÛÜÇÑ', "
            "'aaaaaeeeeiiiiooooouuuucnAAAAAEEEEIIIIOOOOOUUUUCN'), '[^a-zA-Z0-9]', ''))")


def rule_condition(rule: dict[str, Any], available: dict[str, str]) -> str | None:
    """Return the same match predicate used by _apply_classifications_sync.

    Rules without a Department are deliberately excluded by the Python engine;
    missing condition fields are ignored, and an entirely missing condition set
    cannot match.  Those details prevent a SQL build from silently allocating
    rows differently from the legacy vectorized path.
    """
    if not rule.get("department_id"):
        return None
    conditions = rule.get("conditions") or []
    pieces: list[str] = []
    if conditions:
        for cond in conditions:
            field = str(cond.get("field") or "")
            if not field or field.lower() not in available:
                continue
            field = available[field.lower()]
            values = cond.get("values") or []
            canonical_values = [_canon(v) for v in values if _canon(v)]
            if not canonical_values:
                continue
            col = canonical(field)
            op = cond.get("operator", cond.get("op", "="))
            if op in {"=", "=="}:
                pieces.append(f"{col} IN ({', '.join(lit(v) for v in canonical_values)})")
            elif op == "!=":
                # This follows the engine's OR-between-values behavior.
                pieces.append("(" + " OR ".join(f"{col} <> {lit(v)}" for v in canonical_values) + ")")
            else:  # '~', 'contains', and legacy unknown operators
                pieces.append("(" + " OR ".join(f"strpos({col}, {lit(v)}) > 0" for v in canonical_values) + ")")
        if not pieces:
            return None
        return "(" + " AND ".join(pieces) + ")"

    source = str(rule.get("source_field") or "")
    if not source or source.lower() not in available:
        return None
    source = available[source.lower()]
    raw = f"CAST(b.{qi(source)} AS VARCHAR)"
    pattern, kind = str(rule.get("pattern") or ""), str(rule.get("pattern_type") or "regex")
    if kind == "contains":
        return f"strpos(lower(coalesce({raw}, '')), lower({lit(pattern)})) > 0"
    if kind == "exact":
        return f"coalesce({raw}, '') = {lit(rule.get('match_value'))}"
    # The worker's historical regex fallback is a case-insensitive contains.
    return f"strpos(lower(coalesce({raw}, '')), lower({lit(pattern)})) > 0"


def hierarchy_values(rules: list[dict[str, Any]], departments: list[dict[str, Any]]) -> dict[int, dict[str, str]]:
    by_id = {d["id"]: d for d in departments}
    resolved: dict[int, dict[str, str]] = {}
    for rule in rules:
        dept_id = rule.get("department_id")
        if not dept_id or dept_id not in by_id:
            continue
        chain, current = [], by_id[dept_id]
        while current:
            chain.append(current)
            current = by_id.get(current.get("parent_id"))
        root_to_leaf = list(reversed(chain))
        values = {"diretoria": str(root_to_leaf[0].get("name") or ""),
                  "departamento": str(chain[0].get("name") or "")}
        for key in ("centro_custo", "ambiente"):
            values[key] = next((str(d[key]) for d in root_to_leaf if d.get(key)), "")
        merged = {k: v for d in root_to_leaf for k, v in (d.get("custom_fields") or {}).items()}
        values["custom_fields"] = json.dumps(merged, ensure_ascii=False) if merged else ""
        for index, dept in enumerate(root_to_leaf, 1):
            values[f"departamento_nivel_{index}"] = str(dept.get("name") or "")
        resolved[rule["id"]] = values
    return resolved


def case_for(column: str, rules: list[dict[str, Any]], hierarchy: dict[int, dict[str, str]], available: dict[str, str]) -> str:
    clauses = []
    for rule in rules:
        if rule_condition(rule, available) is None:
            continue
        value = str(rule.get("match_value") or "") if column == (rule.get("target_field") or "") else hierarchy.get(rule.get("id"), {}).get(column, "")
        clauses.append(f"WHEN {int(rule['id'])} THEN {lit(value)}")
    return "CASE c._classification_rule_id " + " ".join(clauses) + " ELSE NULL END"


def build_ctas(base_table: str, target_table: str, base_columns: list[str], rules: list[dict[str, Any]], departments: list[dict[str, Any]], version_id: int, snapshot_id: str | None) -> str:
    available = {c.lower(): c for c in base_columns}
    hierarchy = hierarchy_values(rules, departments)
    expressions = []
    for column in base_columns:
        expressions.append(f"{case_for(column.lower(), rules, hierarchy, available)} AS {qi(column)}" if column.lower() in _CLASSIFIED_COLUMNS else f"c.{qi(column)}")
    for column in sorted(_CLASSIFIED_COLUMNS - set(available)):
        expressions.append(f"{case_for(column, rules, hierarchy, available)} AS {qi(column)}")
    expressions.extend([f"{version_id} AS {qi('_classification_version_id')}", f"{lit(snapshot_id)} AS {qi('_financial_base_snapshot_id')}"])
    winners = [f"WHEN {condition} THEN {int(rule['id'])}" for rule in rules if (condition := rule_condition(rule, available))]
    winner = "CASE " + " ".join(winners) + " ELSE NULL END"
    source = f"iceberg.financial_base.{base_table}"
    # A financial reprocess can publish while classification is running. Iceberg
    # time travel makes this build reproduce the snapshot captured in PostgreSQL.
    if snapshot_id and str(snapshot_id).isdigit():
        source += f" FOR VERSION AS OF {int(snapshot_id)}"
    return (f"CREATE TABLE iceberg.classified.{target_table} WITH (partitioning = ARRAY['_billing_period']) AS "
            f"WITH c AS (SELECT b.*, {winner} AS _classification_rule_id FROM {source} b) "
            f"SELECT {', '.join(expressions)} FROM c")
