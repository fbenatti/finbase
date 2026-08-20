"""
Replicate SourceDestinationMapping rows from a reference GCP connection to the others.

Usage:
  cd apps/api && python scripts/replicate_gcp_relationship_mappings.py
  cd apps/api && python scripts/replicate_gcp_relationship_mappings.py --source gcp-4p --targets gcp-ti,gcp-ttech
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import text

from app.database import SyncSession

DEST_TABLE_ID = 1
DEFAULT_SOURCE = "gcp-4p"


def _adapt_config(
    config: dict | None,
    *,
    source_cloud: str,
    target_cloud: str,
    destination_column: str,
    transform_type: str,
) -> dict:
    cfg = copy.deepcopy(config or {})
    if transform_type != "CONST":
        return cfg
    value = cfg.get("value")
    if destination_column == "BillingAccountName" and value:
        cfg["value"] = target_cloud
    elif isinstance(value, str) and value == source_cloud:
        cfg["value"] = target_cloud
    return cfg


def _mapping_name(source_cloud: str, source_column: str, destination_column: str) -> str:
    return f"GCP {source_cloud}: {source_column} -> {destination_column}"


def replicate_mappings(
    session,
    *,
    source_code: str,
    target_codes: list[str],
    destination_table_id: int = DEST_TABLE_ID,
) -> dict[str, int]:
    source = session.execute(text("""
        SELECT id, code, cloud_name, connector_type
        FROM source_connection
        WHERE code = :code
    """), {"code": source_code}).first()
    if not source:
        raise RuntimeError(f"Source connection not found: {source_code}")

    source_id, _, source_cloud, source_connector = source
    source_cloud = source_cloud or source_code.upper()

    source_rows = session.execute(text("""
        SELECT source_column, destination_column, transform_type, transform_config,
               priority, active, name
        FROM source_destination_mapping
        WHERE source_connection_id = :cid
          AND destination_table_id = :did
        ORDER BY priority, destination_column, source_column
    """), {"cid": source_id, "did": destination_table_id}).all()

    if not source_rows:
        raise RuntimeError(f"No mappings found for {source_code}")

    stats: dict[str, int] = {}
    targets = session.execute(text("""
        SELECT id, code, cloud_name, connector_type
        FROM source_connection
        WHERE code = ANY(:codes)
        ORDER BY code
    """), {"codes": target_codes}).all()

    for target_id, target_code, target_cloud, target_connector in targets:
        if target_id == source_id:
            continue
        if source_connector and target_connector and source_connector != target_connector:
            print(f"  skip {target_code}: connector_type {target_connector} != {source_connector}")
            continue

        target_cloud = target_cloud or target_code.upper()
        upserted = 0

        for src_col, dest_col, ttype, cfg, priority, active, _name in source_rows:
            adapted_cfg = _adapt_config(
                cfg,
                source_cloud=source_cloud,
                target_cloud=target_cloud,
                destination_column=dest_col,
                transform_type=ttype,
            )
            mapping_name = _mapping_name(target_cloud, src_col, dest_col)
            session.execute(text("""
                INSERT INTO source_destination_mapping (
                    source_connection_id, destination_table_id, name,
                    source_column, destination_column, transform_type, transform_config,
                    priority, active
                ) VALUES (
                    :cid, :did, :name,
                    :src, :dest, :ttype, CAST(:cfg AS jsonb),
                    :prio, :active
                )
                ON CONFLICT (source_connection_id, destination_table_id, source_column, destination_column)
                DO UPDATE SET
                    name = EXCLUDED.name,
                    transform_type = EXCLUDED.transform_type,
                    transform_config = EXCLUDED.transform_config,
                    priority = EXCLUDED.priority,
                    active = EXCLUDED.active,
                    updated_at = NOW()
            """), {
                "cid": target_id,
                "did": destination_table_id,
                "name": mapping_name,
                "src": src_col,
                "dest": dest_col,
                "ttype": ttype,
                "cfg": json.dumps(adapted_cfg),
                "prio": priority,
                "active": active,
            })
            upserted += 1

        session.execute(text("""
            DELETE FROM source_destination_mapping AS m
            WHERE m.source_connection_id = :cid
              AND m.destination_table_id = :did
              AND NOT EXISTS (
                SELECT 1
                FROM source_destination_mapping AS s
                WHERE s.source_connection_id = :source_id
                  AND s.destination_table_id = :did
                  AND s.source_column = m.source_column
                  AND s.destination_column = m.destination_column
              )
        """), {
            "cid": target_id,
            "did": destination_table_id,
            "source_id": source_id,
        })
        removed = session.execute(text("""
            SELECT COUNT(*) FROM source_destination_mapping
            WHERE source_connection_id = :cid AND destination_table_id = :did
        """), {"cid": target_id, "did": destination_table_id}).scalar() or 0

        stats[target_code] = upserted
        print(f"  {target_code}: {upserted} mappings synced, total now {removed}")

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Replicate GCP relationship mappings")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="Reference GCP connection code")
    parser.add_argument(
        "--targets",
        default="",
        help="Comma-separated target codes (default: all other gcp_bigquery connections)",
    )
    args = parser.parse_args()

    session = SyncSession()
    try:
        if args.targets.strip():
            target_codes = [c.strip() for c in args.targets.split(",") if c.strip()]
        else:
            rows = session.execute(text("""
                SELECT code FROM source_connection
                WHERE connector_type = 'gcp_bigquery' AND code <> :source
                ORDER BY code
            """), {"source": args.source}).all()
            target_codes = [r[0] for r in rows]

        if not target_codes:
            print("No target GCP connections found.")
            return

        print(f"Replicating mappings from {args.source} -> {', '.join(target_codes)}")
        stats = replicate_mappings(session, source_code=args.source, target_codes=target_codes)
        session.commit()
        print("Done:", stats)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
