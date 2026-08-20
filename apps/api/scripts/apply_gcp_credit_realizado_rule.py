"""
Apply GCP realizado rule (Option B): credited lines use BilledCost + CreditAmount.

Usage: cd apps/api && python scripts/apply_gcp_credit_realizado_rule.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import text

from app.database import SyncSession

DEST_TABLE_ID = 1
GCP_CODES = ("gcp-4p", "gcp-ti")
CREDIT_AMOUNT_COLUMN = "CreditAmount"
CREDIT_AMOUNT_TYPE = "DOUBLE PRECISION"
RULE_NAME = "Realizado GCP com desconto de credito"
RULE_EXPRESSION = "BilledCost + CreditAmount"
RULE_SCOPE = {"field": "Credits", "op": "=", "value": "CREDIT"}


def ensure_credit_amount_column(session) -> None:
    exists = session.execute(text("""
        SELECT 1 FROM destination_column
        WHERE destination_table_id = :dest_id AND column_name = :name
    """), {"dest_id": DEST_TABLE_ID, "name": CREDIT_AMOUNT_COLUMN}).first()
    if exists:
        return
    max_pos = session.execute(text("""
        SELECT COALESCE(MAX(position), 0) FROM destination_column
        WHERE destination_table_id = :dest_id
    """), {"dest_id": DEST_TABLE_ID}).scalar() or 0
    session.execute(text("""
        INSERT INTO destination_column (
            destination_table_id, column_name, data_type, is_required, position
        ) VALUES (:dest_id, :name, :dtype, false, :pos)
    """), {
        "dest_id": DEST_TABLE_ID,
        "name": CREDIT_AMOUNT_COLUMN,
        "dtype": CREDIT_AMOUNT_TYPE,
        "pos": max_pos + 1,
    })


def ensure_credit_amount_mappings(session) -> int:
    created = 0
    connections = session.execute(text("""
        SELECT id, code FROM source_connection WHERE code = ANY(:codes)
    """), {"codes": list(GCP_CODES)}).all()
    for conn_id, code in connections:
        exists = session.execute(text("""
            SELECT 1 FROM source_destination_mapping
            WHERE source_connection_id = :conn_id
              AND destination_table_id = :dest_id
              AND destination_column = :dest_col
        """), {
            "conn_id": conn_id,
            "dest_id": DEST_TABLE_ID,
            "dest_col": CREDIT_AMOUNT_COLUMN,
        }).first()
        if exists:
            continue
        session.execute(text("""
            INSERT INTO source_destination_mapping (
                source_connection_id, destination_table_id,
                source_column, destination_column,
                transform_type, transform_config, priority, active
            ) VALUES (
                :conn_id, :dest_id,
                'credits', :dest_col,
                'JSON_PATH', CAST(:cfg AS jsonb), 0, true
            )
        """), {
            "conn_id": conn_id,
            "dest_id": DEST_TABLE_ID,
            "dest_col": CREDIT_AMOUNT_COLUMN,
            "cfg": json.dumps({"path": "[0].amount"}),
        })
        created += 1
        print(f"  mapping created: {code} credits -> {CREDIT_AMOUNT_COLUMN}")
    return created


def update_gcp_realizado_rule(session) -> bool:
    row = session.execute(text("""
        SELECT id FROM business_rule
        WHERE cloud_scope = 'gcp'
          AND target_column = 'realizado'
          AND active = true
          AND (
            name = 'Remove creditos GCP'
            OR expression = '0'
            OR config_json->'scope_filter'->>'field' = 'Credits'
          )
        ORDER BY priority, id
        LIMIT 1
    """)).first()
    if not row:
        return False
    session.execute(text("""
        UPDATE business_rule
        SET name = :name,
            expression = :expression,
            config_json = CAST(:config_json AS jsonb),
            updated_at = NOW()
        WHERE id = :rule_id
    """), {
        "rule_id": row[0],
        "name": RULE_NAME,
        "expression": RULE_EXPRESSION,
        "config_json": json.dumps({
            "scope_filter": RULE_SCOPE,
            "source_column": "BilledCost",
            "operation": "col+",
            "operand_column": "CreditAmount",
        }),
    })
    print(f"  business rule #{row[0]} updated -> {RULE_EXPRESSION}")
    return True


def main() -> None:
    session = SyncSession()
    try:
        ensure_credit_amount_column(session)
        created = ensure_credit_amount_mappings(session)
        updated = update_gcp_realizado_rule(session)
        session.commit()
        print(
            f"Done. mappings_created={created}, rule_updated={updated}. "
            "Reprocesse GCP e reinicie o worker se necessario."
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
