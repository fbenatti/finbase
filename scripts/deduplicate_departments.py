#!/usr/bin/env python3
"""
Remove duplicated department subtrees (same parent + name), keeping the canonical tree.

Canonical node per duplicate group:
  1) highest FK usage in subtree (classification_rule, project, planning_entry)
  2) lowest id as tie-break

Usage:
  python scripts/deduplicate_departments.py [--dry-run] [--pg-url URL]
"""

from __future__ import annotations

import argparse
import logging
from collections import defaultdict

import psycopg2
from psycopg2.extras import RealDictCursor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("deduplicate_departments")

DEFAULT_PG_URL = "postgresql://finops:finops@localhost:5433/finops"


def load_departments(cur) -> list[dict]:
    cur.execute("SELECT id, parent_id, name FROM department ORDER BY id")
    return list(cur.fetchall())


def load_ref_counts(cur) -> dict[int, int]:
    counts: dict[int, int] = defaultdict(int)
    for table in ("classification_rule", "project", "planning_entry", "organization_move_audit"):
        cur.execute(
            f"SELECT department_id, COUNT(*) AS c FROM {table} "
            "WHERE department_id IS NOT NULL GROUP BY department_id"
        )
        for row in cur.fetchall():
            counts[row["department_id"]] += row["c"]
    return counts


def subtree_ref_count(dept_id: int, children: dict[int, list[int]], ref_counts: dict[int, int]) -> int:
    total = ref_counts.get(dept_id, 0)
    for child_id in children.get(dept_id, []):
        total += subtree_ref_count(child_id, children, ref_counts)
    return total


def build_children(depts: list[dict]) -> dict[int | None, list[dict]]:
    by_parent: dict[int | None, list[dict]] = defaultdict(list)
    for dept in depts:
        by_parent[dept["parent_id"]].append(dept)
    for key in by_parent:
        by_parent[key].sort(key=lambda d: d["id"])
    return by_parent


def merge_subtrees(
    canonical: dict,
    duplicate: dict,
    by_parent: dict[int | None, list[dict]],
    id_map: dict[int, int],
    reparent: list[tuple[int, int]],
) -> None:
    id_map[duplicate["id"]] = canonical["id"]

    canon_children = {d["name"]: d for d in by_parent.get(canonical["id"], [])}
    for dup_child in by_parent.get(duplicate["id"], []):
        canon_child = canon_children.get(dup_child["name"])
        if canon_child:
            merge_subtrees(canon_child, dup_child, by_parent, id_map, reparent)
        else:
            reparent.append((dup_child["id"], canonical["id"]))


def depth_of(dept_id: int, by_id: dict[int, dict]) -> int:
    depth = 0
    current = by_id.get(dept_id)
    seen = set()
    while current and current["parent_id"] is not None and current["id"] not in seen:
        seen.add(current["id"])
        depth += 1
        current = by_id.get(current["parent_id"])
    return depth


def apply_id_map(cur, id_map: dict[int, int], dry_run: bool) -> None:
    if not id_map:
        return

    def remap(table: str, column: str = "department_id") -> int:
        updated = 0
        for old_id, new_id in id_map.items():
            cur.execute(
                f"SELECT COUNT(*) AS c FROM {table} WHERE {column} = %s",
                (old_id,),
            )
            count = cur.fetchone()["c"]
            if not count:
                continue
            logger.info("UPDATE %s.%s %s -> %s (%d rows)", table, column, old_id, new_id, count)
            if not dry_run:
                cur.execute(
                    f"UPDATE {table} SET {column} = %s WHERE {column} = %s",
                    (new_id, old_id),
                )
            updated += count
        return updated

    remap("classification_rule")
    remap("project")
    remap("planning_entry")
    remap("organization_move_audit")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deduplicate department trees")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pg-url", default=DEFAULT_PG_URL)
    args = parser.parse_args()

    conn = psycopg2.connect(args.pg_url)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=RealDictCursor)

    depts = load_departments(cur)
    by_id = {d["id"]: d for d in depts}
    by_parent = build_children(depts)
    children_ids = {d["id"]: [c["id"] for c in by_parent.get(d["id"], [])] for d in depts}
    ref_counts = load_ref_counts(cur)

    id_map: dict[int, int] = {}
    reparent: list[tuple[int, int]] = []
    duplicate_groups = 0

    for parent_id, siblings in by_parent.items():
        grouped: dict[str, list[dict]] = defaultdict(list)
        for sibling in siblings:
            grouped[sibling["name"]].append(sibling)
        for name, group in grouped.items():
            if len(group) < 2:
                continue
            duplicate_groups += 1
            ranked = sorted(
                group,
                key=lambda d: (-subtree_ref_count(d["id"], children_ids, ref_counts), d["id"]),
            )
            canonical = ranked[0]
            for duplicate in ranked[1:]:
                logger.info(
                    "MERGE duplicate id=%s into canonical id=%s (parent=%s name=%r)",
                    duplicate["id"], canonical["id"], parent_id, name,
                )
                merge_subtrees(canonical, duplicate, by_parent, id_map, reparent)

    logger.info("Duplicate sibling groups: %d", duplicate_groups)
    logger.info("Department id mappings: %d", len(id_map))
    logger.info("Reparent unique nodes: %d", len(reparent))

    apply_id_map(cur, id_map, args.dry_run)

    for child_id, new_parent_id in reparent:
        if child_id in id_map:
            continue
        logger.info("REPARENT department id=%s -> parent_id=%s", child_id, new_parent_id)
        if not args.dry_run:
            cur.execute(
                "UPDATE department SET parent_id = %s, updated_at = NOW() WHERE id = %s",
                (new_parent_id, child_id),
            )

    delete_ids = sorted(id_map.keys(), key=lambda dept_id: depth_of(dept_id, by_id), reverse=True)
    logger.info("Departments to delete: %d", len(delete_ids))
    for dept_id in delete_ids:
        if not args.dry_run:
            cur.execute("DELETE FROM department WHERE id = %s", (dept_id,))
        else:
            cur.execute("SELECT 1 FROM department WHERE id = %s", (dept_id,))

    if args.dry_run:
        conn.rollback()
        cur.execute("SELECT COUNT(*) AS c FROM department")
        logger.info("DRY RUN complete. Current department count: %s", cur.fetchone()["c"])
    else:
        conn.commit()
        cur.execute("SELECT COUNT(*) AS c FROM department")
        logger.info("Committed. Department count now: %s", cur.fetchone()["c"])

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
