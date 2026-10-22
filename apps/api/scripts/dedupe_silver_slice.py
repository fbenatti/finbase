#!/usr/bin/env python3
"""DEV helper: idempotent DELETE of a Silver dest_* slice (optional ROW_NUMBER dedupe).

Usage:
  python scripts/dedupe_silver_slice.py --table dest_billing_cloud \\
      --connection-id 4 --billing-period 202501
  python scripts/dedupe_silver_slice.py --table dest_cloud_resource_inventory \\
      --connection-id 2 --snapshot-date 2025-09-01
"""

from __future__ import annotations

import argparse
import sys

from app.services import iceberg_service as iceberg
from app.services.destination_slice import resolve_destination_slice_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Purge/replace Silver slice (DEV)")
    parser.add_argument("--table", required=True, help="physical_name (dest_*)")
    parser.add_argument("--connection-id", type=int, required=True)
    parser.add_argument("--billing-period", default=None)
    parser.add_argument("--snapshot-date", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = resolve_destination_slice_config(args.table, None)
    if config.grain == "snapshot_date" and not args.snapshot_date:
        print("snapshot-date é obrigatório para destinos diários", file=sys.stderr)
        return 2
    if config.grain == "billing_period" and not args.billing_period:
        print("billing-period é obrigatório para destinos mensais", file=sys.stderr)
        return 2

    if args.dry_run:
        print(
            f"DRY RUN delete slice table={args.table} connection={args.connection_id} "
            f"billing_period={args.billing_period} snapshot_date={args.snapshot_date}"
        )
        return 0

    iceberg.delete_destination_slice(
        args.table,
        billing_period=args.billing_period,
        snapshot_date=args.snapshot_date,
        connection_id=args.connection_id,
    )
    print("Slice removida; reenfileire reprocesso para republicar a fatia.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
