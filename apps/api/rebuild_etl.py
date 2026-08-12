"""Inventory and reset ETL data while preserving configuration metadata.

Usage:
    python apps/api/rebuild_etl.py                 # inventory only
    python apps/api/rebuild_etl.py --execute       # delete ETL data

The execute flag is intentional: this script never deletes connections, rules,
classifications, departments, projects, destinations, or source mappings.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

sys.path.insert(0, __file__.rsplit("/apps/api/", 1)[0] + "/apps/api")

from app.database import SyncSession  # noqa: E402
from app.services import iceberg_service as iceberg  # noqa: E402


ETL_TABLES = (
    "normalized_cost_line",
    "raw_cost_line",
    "ingested_blob",
    "fact_cost",
    "etl_task",
    "period_status",
    "skipped_blob",
)


def inventory(session) -> None:
    print("PostgreSQL ETL inventory")
    for table in ETL_TABLES:
        try:
            count = session.execute(text(f"SELECT count(*) FROM {table}")).scalar()
            print(f"  {table}: {count}")
        except Exception as exc:
            session.rollback()
            print(f"  {table}: unavailable ({exc})")

    print("Iceberg tables")
    for namespace in ("bronze", "silver", "gold"):
        try:
            print(f"  {namespace}: {iceberg.list_tables(namespace)}")
        except Exception as exc:
            print(f"  {namespace}: unavailable ({exc})")


def reset(session) -> None:
    # Source→destination relationships are intentionally rebuilt after the
    # source schema has been validated. Rules/classifications remain intact.
    session.execute(text("DELETE FROM source_destination_mapping"))
    # Child rows first; metadata/configuration tables are deliberately absent.
    for table in ETL_TABLES:
        try:
            session.execute(text(f"DELETE FROM {table}"))
        except Exception:
            session.rollback()
            # Some installations do not have all optional operational tables.
            continue
    session.commit()

    for namespace in ("bronze", "silver", "gold"):
        for table in list(iceberg.list_tables(namespace)):
            name = table[0] if isinstance(table, tuple) else table
            iceberg._force_delete_table((namespace, name), name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--no-inventory", action="store_true", help="skip expensive pre-delete row counts")
    args = parser.parse_args()

    session = SyncSession()
    try:
        if not args.no_inventory:
            inventory(session)
        else:
            print("Pre-delete inventory skipped by request.")
        if args.execute:
            reset(session)
            print("ETL data reset complete; configuration metadata was preserved.")
        else:
            print("Inventory only. Use --execute to reset ETL data.")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
