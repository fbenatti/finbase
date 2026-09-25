"""Reset Silver and/or Gold Iceberg layers (Bronze is never touched).

Bronze is the immutable input used to rebuild derived tables after a reset.

Usage:
  PYTHONPATH=apps/api python apps/api/scripts/reset_derived_layers.py --dry-run
  PYTHONPATH=apps/api python apps/api/scripts/reset_derived_layers.py --apply
  PYTHONPATH=apps/api python apps/api/scripts/reset_derived_layers.py --apply --include-forecast
"""

from __future__ import annotations

import argparse

import shutil
from pathlib import Path

from app.services import iceberg_service as iceberg


def _warehouse_paths() -> list[Path]:
    root = Path(iceberg._LOCAL_WAREHOUSE)
    names = ("silver", "gold", "silver.db", "gold.db")
    return [root / name for name in names if (root / name).exists()]


def wipe_warehouse_directories() -> int:
    """Remove all table directories under silver/gold warehouse folders (Bronze untouched)."""
    removed = 0
    for base in _warehouse_paths():
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
                print(f"REMOVED DIR {child}")
            elif child.is_file():
                child.unlink(missing_ok=True)
                removed += 1
                print(f"REMOVED FILE {child}")
    return removed


def purge_catalog_rows(namespaces: tuple[str, ...]) -> int:
    """Delete iceberg_tables rows when PostgreSQL catalog is reachable."""
    try:
        from sqlalchemy import create_engine, text
        from app.config import settings

        engine = create_engine(settings.database_url_sync)
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    "DELETE FROM iceberg_tables WHERE table_namespace = ANY(:namespaces)"
                ),
                {"namespaces": list(namespaces)},
            )
            return int(result.rowcount or 0)
    except Exception as exc:
        print(f"AVISO: catálogo SQL não atualizado ({exc}); rode de novo quando o Postgres estiver acessível.")
        return 0


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
    parser.add_argument("--apply", action="store_true", help="drop derived tables and warehouse files")
    parser.add_argument("--dry-run", action="store_true", help="list targets without dropping")
    parser.add_argument(
        "--include-forecast",
        action="store_true",
        help="also drop iceberg.forecast (not included by default)",
    )
    parser.add_argument(
        "--filesystem-only",
        action="store_true",
        help="skip PyIceberg catalog; only delete silver/gold files under the warehouse",
    )
    args = parser.parse_args()
    if args.apply and args.dry_run:
        parser.error("use either --apply or --dry-run")
    if not args.apply and not args.dry_run:
        parser.error("pass --dry-run or --apply")

    namespaces = ["silver", "gold"]
    if args.include_forecast:
        namespaces.append("forecast")

    planned: list[tuple[str, str]] = []
    if not args.filesystem_only:
        try:
            planned = [
                (namespace, table)
                for namespace in namespaces
                for table in tables_in(namespace)
            ]
        except Exception as exc:
            print(f"Catálogo Iceberg indisponível ({exc}); use --filesystem-only se necessário.")
            if not args.apply:
                return 1
            args.filesystem_only = True

    for namespace, table in planned:
        print(f"{'DROP' if args.apply else 'WOULD DROP'} iceberg.{namespace}.{table}")
    for base in _warehouse_paths():
        print(f"{'CLEAR' if args.apply else 'WOULD CLEAR'} warehouse {base}")
    try:
        bronze_count = len(tables_in("bronze"))
    except Exception:
        bronze_count = "?"
    print(f"Bronze preserved: {bronze_count} catalog table(s); dirs bronze/bronze.db untouched")
    if not args.apply:
        return 0

    dropped = 0
    if not args.filesystem_only:
        for namespace, table in planned:
            iceberg._force_delete_table((namespace, table), str(table))
            dropped += 1
    removed_dirs = wipe_warehouse_directories()
    catalog_rows = purge_catalog_rows(tuple(namespaces))
    print(
        f"Concluído: {dropped} drop(s) via catálogo, {removed_dirs} path(s) no warehouse, "
        f"{catalog_rows} linha(s) em iceberg_tables."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
