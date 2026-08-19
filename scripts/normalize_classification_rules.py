#!/usr/bin/env python3
"""
Normalize legacy classification_rule rows to the canonical conditions JSONB schema.

Actions:
  1. Fix conditions with legacy op/value keys → operator/values
  2. Derive conditions from pattern when JSONB is empty
  3. Map resourceGroupName → source_resource_group_name (Azure) and GCP legacy field → SubAccountId
  4. Set destination_table_id on rules missing it (default: MODELO FOCUS)
  5. Create per-project rules from project.subscription_rg when no equivalent rule exists

Usage:
  python scripts/normalize_classification_rules.py [--dry-run] [--pg-url URL]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.services.classification_engine import (  # noqa: E402
    map_legacy_field,
    normalize_rule_conditions,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("normalize_classification_rules")

DEFAULT_PG_URL = "postgresql://finops:finops@localhost:5433/finops"
DEFAULT_DEST_FIELD = "source_resource_group_name"
GCP_DEST_FIELD = "SubAccountId"


def migrate_gcp_classification_field(conditions: list[dict], cloud_scope: str | None) -> list[dict]:
    if cloud_scope != "gcp":
        return conditions
    migrated: list[dict] = []
    for cond in conditions:
        item = dict(cond)
        field = str(item.get("field") or "")
        if field in ("source_resource_group_name", "resourceGroupName"):
            item["field"] = GCP_DEST_FIELD
        migrated.append(item)
    return migrated


def default_dest_field_for_cloud(cloud_scope: str | None) -> str:
    if cloud_scope == "gcp":
        return GCP_DEST_FIELD
    return DEFAULT_DEST_FIELD


class RuleRow:
  def __init__(self, row: dict):
    self.id = row["id"]
    self.name = row.get("name") or ""
    self.connection_id = row.get("connection_id")
    self.destination_table_id = row.get("destination_table_id")
    self.department_id = row.get("department_id")
    self.project_id = row.get("project_id")
    self.source_field = row.get("source_field") or ""
    self.pattern = row.get("pattern") or ""
    self.pattern_type = row.get("pattern_type") or ""
    self.match_value = row.get("match_value") or ""
    self.target_field = row.get("target_field") or "departamento"
    self.conditions = row.get("conditions")
    self.priority = row.get("priority") or 0
    self.cloud_scope = row.get("cloud_scope")
    self.active = row.get("active", True)


def _rule_like(row: RuleRow):
    """Minimal duck-type object for normalize_rule_conditions."""

    class _R:
        pass

    r = _R()
    for key in (
        "source_field", "pattern", "pattern_type", "match_value", "conditions",
    ):
        setattr(r, key, getattr(row, key))
    return r


def _conditions_signature(conditions: list[dict]) -> str:
    parts = []
    for c in sorted(conditions, key=lambda x: x.get("field", "")):
        vals = "|".join(sorted(c.get("values") or []))
        parts.append(f"{c.get('field')}:{c.get('operator')}:{vals}")
    return ";".join(parts)


def resolve_focus_table_id(cur) -> int | None:
    cur.execute(
        "SELECT id FROM destination_table WHERE upper(name) = 'MODELO FOCUS' LIMIT 1"
    )
    row = cur.fetchone()
    return row["id"] if row else None


def cloud_scope_from_project_cloud(cloud: str | None) -> str | None:
    if not cloud:
        return None
    lower = cloud.lower()
    if "gcp" in lower:
        return "gcp"
    if "azure" in lower:
        return "azure"
    if "mongo" in lower or "atlas" in lower:
        return "mongodb"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize classification rules")
    parser.add_argument("--dry-run", action="store_true", help="Report only, no writes")
    parser.add_argument("--pg-url", default=DEFAULT_PG_URL, help="PostgreSQL connection URL")
    args = parser.parse_args()

    conn = psycopg2.connect(args.pg_url)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=RealDictCursor)

    focus_id = resolve_focus_table_id(cur)
    if focus_id is None:
        logger.warning("MODELO FOCUS destination table not found; skipping destination_table_id backfill")

    updated_rules = 0
    created_rules = 0
    existing_signatures: set[str] = set()

    cur.execute("SELECT * FROM classification_rule ORDER BY id")
    for raw in cur.fetchall():
        row = RuleRow(raw)
        normalized = normalize_rule_conditions(_rule_like(row))
        if normalized:
            normalized = migrate_gcp_classification_field(normalized, row.cloud_scope)
        if normalized:
            existing_signatures.add(_conditions_signature(normalized))

        needs_update = False
        new_source = map_legacy_field(row.source_field) if row.source_field else (
            normalized[0]["field"] if normalized else row.source_field
        )
        new_pattern = row.pattern
        new_pattern_type = row.pattern_type or "multi_condition"

        if normalized and normalized != (row.conditions or []):
            needs_update = True
        if normalized and not row.conditions:
            needs_update = True
        if new_source != row.source_field:
            needs_update = True
        if focus_id and row.destination_table_id is None:
            needs_update = True
        if normalized and row.pattern_type not in ("multi_condition",):
            needs_update = True
            new_pattern_type = "multi_condition"
            new_pattern = " ; ".join(
                f'{c["field"]}{c.get("operator", "=")}{"|".join(c.get("values", []))}'
                for c in normalized
            )[:500]

        if not needs_update:
            continue

        logger.info(
            "UPDATE rule id=%s name=%r conditions=%s dest_table=%s",
            row.id, row.name, json.dumps(normalized, ensure_ascii=False),
            focus_id if row.destination_table_id is None else row.destination_table_id,
        )
        if not args.dry_run:
            cur.execute(
                """
                UPDATE classification_rule
                SET source_field = %s,
                    pattern = %s,
                    pattern_type = %s,
                    conditions = %s,
                    destination_table_id = COALESCE(destination_table_id, %s),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    new_source or (normalized[0]["field"] if normalized else row.source_field),
                    new_pattern,
                    new_pattern_type,
                    json.dumps(normalized) if normalized else None,
                    focus_id,
                    row.id,
                ),
            )
        updated_rules += 1

    # Create rules from active projects with subscription_rg
    cur.execute(
        """
        SELECT p.id, p.department_id, p.projeto_name, p.cloud, p.subscription_rg,
               d.name AS dept_name
        FROM project p
        JOIN department d ON d.id = p.department_id
        WHERE p.active = true
          AND p.subscription_rg IS NOT NULL
          AND trim(p.subscription_rg) <> ''
        ORDER BY p.id
        """
    )
    for proj in cur.fetchall():
        rg = str(proj["subscription_rg"]).strip()
        cloud_scope = cloud_scope_from_project_cloud(proj.get("cloud"))
        dest_field = default_dest_field_for_cloud(cloud_scope)
        conditions = [{
            "field": dest_field,
            "operator": "=",
            "values": [rg],
            "logic": "AND",
        }]
        sig = _conditions_signature(conditions)
        if sig in existing_signatures:
            continue

        rule_name = f"Project | {rg} -> {proj['dept_name']}"[:200]
        priority = 50

        logger.info(
            "CREATE rule from project id=%s name=%r conditions=%s",
            proj["id"], rule_name, json.dumps(conditions, ensure_ascii=False),
        )
        if not args.dry_run:
            cur.execute(
                """
                INSERT INTO classification_rule (
                    connection_id, destination_table_id, cloud_scope,
                    department_id, project_id, name, source_field, pattern,
                    pattern_type, match_value, target_field, conditions,
                    priority, active, created_at, updated_at
                ) VALUES (
                    NULL, %s, %s,
                    %s, %s, %s, %s, %s,
                    'multi_condition', %s, 'departamento', %s,
                    %s, true, NOW(), NOW()
                )
                """,
                (
                    focus_id,
                    cloud_scope,
                    proj["department_id"],
                    proj["id"],
                    rule_name,
                    dest_field,
                    f"{dest_field}={rg}"[:500],
                    proj["dept_name"],
                    json.dumps(conditions),
                    priority,
                ),
            )
        existing_signatures.add(sig)
        created_rules += 1

    if args.dry_run:
        conn.rollback()
        logger.info("DRY RUN: would update %d rules, create %d project rules", updated_rules, created_rules)
    else:
        conn.commit()
        logger.info("Committed: updated %d rules, created %d project rules", updated_rules, created_rules)

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
