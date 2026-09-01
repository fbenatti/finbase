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
from app.services.oci_operational_monitoring import GOLD_TABLE, collect


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
    # Keep the latest inventory snapshot per resource_id, but accumulate all metrics.
    for col in ("timestamp", "resource_id", "metric_name", "record_type"):
        if col not in bronze_df.columns:
            bronze_df[col] = None

    metrics_mask = bronze_df.get("record_type") == "metric"
    metrics_df = bronze_df[metrics_mask].copy() if metrics_mask.any() else pd.DataFrame()
    inventory_df = bronze_df[~metrics_mask].copy() if (~metrics_mask).any() else pd.DataFrame()

    if not inventory_df.empty and "collected_at" in inventory_df.columns:
        inventory_df["collected_at"] = pd.to_datetime(inventory_df["collected_at"], errors="coerce", utc=True)
        inventory_df = inventory_df.sort_values("collected_at").drop_duplicates(
            subset=["resource_id", "record_type"], keep="last"
        )

    if not metrics_df.empty:
        metrics_df = metrics_df.drop_duplicates(
            subset=["timestamp", "resource_id", "metric_name", "record_type"], keep="last"
        )

    combined = pd.concat([metrics_df, inventory_df], ignore_index=True)
    if "_billing_period" not in combined.columns:
        combined["_billing_period"] = pd.to_datetime(combined.get("timestamp"), errors="coerce", utc=True).dt.strftime("%Y%m").fillna("unknown")
    if "source_connection_id" not in combined.columns:
        combined["source_connection_id"] = 9
    if "cloud_origem" not in combined.columns:
        combined["cloud_origem"] = "oci-prod"
    if "tipo" not in combined.columns:
        combined["tipo"] = "realizado"

    staged = iceberg.write_destination_staged(
        GOLD_TABLE, combined,
        column_types={"metric_value": "DOUBLE", "unit_count": "DOUBLE"},
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
