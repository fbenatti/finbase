#!/usr/bin/env python3
"""One-shot rebuild of gold.dest_oci_operational_metrics.

1. Rebuilds the Gold table from the Bronze operational snapshots (recovers data
   that was written to Bronze but never swapped to Gold because of the
   read_destination() None bug).
2. Runs a full 90-day OCI operational collection to backfill metrics.

Run from repo root:
    python scripts/rebuild_oci_operational_gold.py
"""
from __future__ import annotations
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "api"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rebuild_oci_operational_gold")

import pandas as pd
from app.database import SyncSession
from app.models.source_connection import SourceConnection
from app.services import iceberg_service as iceberg
from app.services.oci_operational_monitoring import GOLD_TABLE, collect, gold_frame_from_bronze


def rebuild_from_bronze() -> int:
    """Read all Bronze operational snapshots and rewrite Gold as a clean snapshot."""
    try:
        bronze_df = iceberg.read_bronze("oci-prod_operational")
    except Exception as exc:
        logger.error("Could not read Bronze operational table: %s", exc)
        return 0
    if bronze_df.empty:
        logger.info("Bronze operational table is empty")
        return 0

    logger.info("Bronze operational rows: %d", len(bronze_df))
    connection = SourceConnection(id=9, code="oci-prod")
    combined = gold_frame_from_bronze(bronze_df, connection=connection, period="unknown")

    staged = iceberg.write_destination_staged(
        GOLD_TABLE, combined,
        column_types={"metric_value": "DOUBLE", "unit_count": "DOUBLE", "unit": "DOUBLE"},
    )
    iceberg.swap_staged_table(GOLD_TABLE, staged, known_row_count=len(combined))
    logger.info("Rebuilt gold.%s with %d rows", GOLD_TABLE, len(combined))
    return len(combined)


def backfill_oci(hours: int = 24 * 90) -> dict:
    session = SyncSession()
    try:
        conn = session.query(SourceConnection).filter(SourceConnection.connector_type == "oci").first()
        if not conn:
            logger.error("No OCI source_connection found")
            return {}
        logger.info("Starting OCI operational backfill: connection=%s hours=%d", conn.code, hours)
        return collect(conn, hours=hours, persist=True)
    finally:
        session.close()


def main() -> int:
    rebuild_from_bronze()
    result = backfill_oci()
    logger.info("Backfill result: inventory=%s metrics=%s errors=%s persisted=%s",
                len(result.get("inventory", [])), len(result.get("metrics", [])),
                len(result.get("errors", [])), result.get("persisted"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
