"""Reset only derived Iceberg layers.

Bronze is deliberately excluded: it is the immutable input required to rebuild
Silver, Gold, forecast and analytical marts after a contract migration.

Usage:
  PYTHONPATH=apps/api python apps/api/scripts/reset_derived_layers.py --dry-run
  PYTHONPATH=apps/api python apps/api/scripts/reset_derived_layers.py --apply
"""

from __future__ import annotations

import argparse

from app.services import iceberg_service as iceberg


DERIVED_NAMESPACES = ("silver", "gold", "dimon", "forecast")


def tables_in(namespace: str) -> list[str]:
    catalog = iceberg._get_catalog()
    try:
        return sorted(
            item[1] if isinstance(item, tuple) else str(item)
            for item in catalog.list_tables(namespace)
        )
    except Exception:
        return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="drop derived tables")
    parser.add_argument("--dry-run", action="store_true", help="list targets without dropping")
    args = parser.parse_args()
    if args.apply and args.dry_run:
        parser.error("use either --apply or --dry-run")

    catalog = iceberg._get_catalog()
    planned = [(namespace, table) for namespace in DERIVED_NAMESPACES for table in tables_in(namespace)]
    for namespace, table in planned:
        print(f"{'DROP' if args.apply else 'WOULD DROP'} iceberg.{namespace}.{table}")
    print(f"Bronze preserved: {len(tables_in('bronze'))} table(s)")
    if not args.apply:
        return 0
    for namespace, table in planned:
        catalog.drop_table((namespace, table))
    print(f"Dropped {len(planned)} derived table(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
