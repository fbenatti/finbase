"""Iceberg metadata maintenance: merge manifests, expire snapshots, drop orphans.

Data files and their partition layout are left untouched; only the metadata
history accumulated by per-batch appends is cleaned up.

Refuses to start while reprocess tasks are queued or running (use --force to
override) and holds the warehouse write lock for the whole run.

Usage:
    PYTHONPATH=apps/api ./.venv/bin/python apps/api/scripts/compact_iceberg_table.py silver.dest_billing_cloud
    PYTHONPATH=apps/api ./.venv/bin/python apps/api/scripts/compact_iceberg_table.py silver.dest_billing_cloud --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import httpx
from sqlalchemy import create_engine, text

from app.services import iceberg_service as iceberg
from app.services.write_lock import exclusive_write_lock

TRINO_URL = os.getenv("TRINO_URL", "http://localhost:8080")
TRINO_USER = os.getenv("TRINO_USER", "finops")


def trino(sql: str, session: str = "") -> None:
    headers = {"X-Trino-User": TRINO_USER, "X-Trino-Catalog": "iceberg"}
    if session:
        headers["X-Trino-Session"] = session
    with httpx.Client(timeout=600.0) as client:
        data = client.post(f"{TRINO_URL}/v1/statement", content=sql, headers=headers).json()
        while True:
            if "error" in data:
                raise RuntimeError(data["error"].get("message", data["error"]))
            next_uri = data.get("nextUri")
            if not next_uri:
                return
            data = client.get(next_uri, headers=headers).json()


def table_stats(namespace: str, name: str) -> str:
    table = iceberg._get_catalog().load_table((namespace, name))
    snapshot = table.current_snapshot()
    manifests = len(snapshot.manifests(table.io)) if snapshot else 0
    summary = snapshot.summary.additional_properties if snapshot else {}
    return (
        f"snapshots={len(table.snapshots())} manifests={manifests} "
        f"files={summary.get('total-data-files')} rows={summary.get('total-records')}"
    )


def active_reprocess_tasks() -> list[int]:
    engine = create_engine(os.environ["DATABASE_URL_SYNC"])
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id FROM etl_task WHERE task_type IN ('reprocess', 'reclassification') "
            "AND status IN ('QUEUED', 'RUNNING') ORDER BY id"
        ))
        return [row[0] for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("table", help="namespace.table, e.g. silver.dest_billing_cloud")
    parser.add_argument("--retention", default="10m")
    parser.add_argument("--dry-run", action="store_true", help="only print table stats")
    parser.add_argument("--force", action="store_true", help="run even with reprocess tasks in the queue")
    args = parser.parse_args()
    namespace, name = args.table.split(".", 1)
    fqtn = f"iceberg.{namespace}.{name}"

    print(f"current: {table_stats(namespace, name)}", flush=True)
    if args.dry_run:
        return 0
    pending = active_reprocess_tasks()
    if pending and not args.force:
        print(f"reprocess tasks still queued/running: {pending}; aborting (use --force)", flush=True)
        return 1

    steps = [
        ("optimize_manifests", f"ALTER TABLE {fqtn} EXECUTE optimize_manifests", ""),
        (
            "expire_snapshots",
            f"ALTER TABLE {fqtn} EXECUTE expire_snapshots(retention_threshold => '{args.retention}')",
            f"iceberg.expire_snapshots_min_retention={args.retention}",
        ),
        (
            "remove_orphan_files",
            f"ALTER TABLE {fqtn} EXECUTE remove_orphan_files(retention_threshold => '{args.retention}')",
            f"iceberg.remove_orphan_files_min_retention={args.retention}",
        ),
    ]
    print("waiting for warehouse write lock...", flush=True)
    with exclusive_write_lock():
        print("lock acquired", flush=True)
        for label, sql, session in steps:
            started = time.monotonic()
            trino(sql, session)
            print(f"{label} ok in {time.monotonic() - started:.1f}s", flush=True)
        print(f"after: {table_stats(namespace, name)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
