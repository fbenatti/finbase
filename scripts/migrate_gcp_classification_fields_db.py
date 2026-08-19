#!/usr/bin/env python3
"""
One-shot DB migration: GCP classification rules → SubAccountId (FOCUS).

Updates classification_rule rows with cloud_scope='gcp' that still match on
source_resource_group_name (Azure legacy field) to use SubAccountId instead.

Usage:
  python scripts/migrate_gcp_classification_fields_db.py [--dry-run] [--pg-url URL]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

import psycopg2
from psycopg2.extras import RealDictCursor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate_gcp_classification_fields_db")

DEFAULT_PG_URL = "postgresql://finops:finops@localhost:5433/finops"
OLD_FIELDS = {"source_resource_group_name", "resourceGroupName", "resourcegroupname"}
NEW_FIELD = "SubAccountId"


def _pattern_from_conditions(conditions: list[dict]) -> str:
    return " ; ".join(
        f'{c["field"]}{c.get("operator", "=")}{"|".join(c.get("values", []))}'
        for c in conditions
    )[:500]


def migrate_conditions(conditions: list | None) -> tuple[list[dict] | None, bool]:
    if not conditions or not isinstance(conditions, list):
        return conditions, False
    migrated: list[dict] = []
    changed = False
    for raw in conditions:
        if not isinstance(raw, dict):
            migrated.append(raw)
            continue
        field = str(raw.get("field") or "").strip()
        if field in OLD_FIELDS:
            migrated.append({**raw, "field": NEW_FIELD})
            changed = True
        else:
            migrated.append(raw)
    return migrated, changed


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate GCP classification rules to SubAccountId")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pg-url", default=DEFAULT_PG_URL)
    args = parser.parse_args()

    conn = psycopg2.connect(args.pg_url)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute(
        """
        SELECT id, name, source_field, pattern, conditions
        FROM classification_rule
        WHERE cloud_scope = 'gcp'
        ORDER BY id
        """
    )
    rows = cur.fetchall()
    updated = 0

    for row in rows:
        conditions, cond_changed = migrate_conditions(row.get("conditions"))
        source_changed = str(row.get("source_field") or "") in OLD_FIELDS
        pattern = str(row.get("pattern") or "")
        pattern_changed = any(f"{old}=" in pattern or f"{old}~" in pattern for old in OLD_FIELDS)

        if not cond_changed and not source_changed and not pattern_changed:
            continue

        new_source = NEW_FIELD if source_changed else row.get("source_field")
        if conditions and isinstance(conditions, list) and conditions:
            new_source = conditions[0].get("field", new_source)

        new_pattern = _pattern_from_conditions(conditions) if conditions else pattern
        if not new_pattern and pattern_changed:
            new_pattern = pattern
            for old in OLD_FIELDS:
                new_pattern = new_pattern.replace(f"{old}=", f"{NEW_FIELD}=")
                new_pattern = new_pattern.replace(f"{old}~", f"{NEW_FIELD}~")

        logger.info(
            "UPDATE id=%s name=%r source_field %r -> %r",
            row["id"], row.get("name"), row.get("source_field"), new_source,
        )

        if not args.dry_run:
            cur.execute(
                """
                UPDATE classification_rule
                SET source_field = %s,
                    pattern = %s,
                    pattern_type = 'multi_condition',
                    conditions = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    new_source,
                    new_pattern,
                    json.dumps(conditions) if conditions is not None else None,
                    row["id"],
                ),
            )
        updated += 1

    if args.dry_run:
        conn.rollback()
        logger.info("DRY RUN: would update %d GCP classification rules", updated)
    else:
        conn.commit()
        logger.info("Committed: updated %d GCP classification rules", updated)

    cur.close()
    conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.error("%s", exc)
        sys.exit(1)
