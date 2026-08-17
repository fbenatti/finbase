"""
Backfill cloud_scope on rules and recreate business rules on FOCUS columns.

Usage: cd apps/api && python seed_cloud_scope_rules.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import text

from app.database import SyncSession
from app.services.cloud_scope import connection_cloud_scope

CONN_CLOUD = {
    1: "Azure-4P",
    2: "Azure-TI",
    3: "GCP-4P",
    4: "GCP-TI",
    5: "Atlas-MongoDB",
}


def ensure_cloud_scope_columns(session) -> None:
    for statement in (
        "ALTER TABLE business_rule ADD COLUMN IF NOT EXISTS cloud_scope VARCHAR(50)",
        "ALTER TABLE classification_rule ADD COLUMN IF NOT EXISTS cloud_scope VARCHAR(50)",
        "CREATE INDEX IF NOT EXISTS ix_business_rule_cloud_scope ON business_rule (cloud_scope)",
        "CREATE INDEX IF NOT EXISTS ix_classification_rule_cloud_scope ON classification_rule (cloud_scope)",
    ):
        session.execute(text(statement))
    session.commit()


def backfill_classification_scope(session) -> None:
    rows = session.execute(text(
        "SELECT id, connection_id, cloud_scope FROM classification_rule"
    )).all()
    for rule_id, connection_id, current in rows:
        if current:
            continue
        cloud_name = CONN_CLOUD.get(connection_id or 0)
        scope = connection_cloud_scope(cloud_name) if cloud_name else None
        if scope:
            session.execute(text(
                "UPDATE classification_rule SET cloud_scope = :scope WHERE id = :id"
            ), {"scope": scope, "id": rule_id})


def recreate_business_rules(session) -> None:
    session.execute(text("DELETE FROM business_rule"))
    rules = [
        {
            "name": "Taxa Marketplace Azure (13.83%)",
            "rule_type": "CONDITIONAL",
            "target_column": "realizado",
            "cloud_scope": "azure",
            "priority": 20,
            "expression": None,
            "conditions": [{
                "field": "ServiceCategory",
                "op": "==",
                "logic": "AND",
                "value": "Azure Marketplace Services",
                "then_field": "realizado",
                "then_value": "BilledCost * 1.1383",
            }],
            "config_json": {},
        },
        {
            "name": "Demais impostos Azure (18.61%)",
            "rule_type": "EXPRESSION",
            "target_column": "realizado",
            "cloud_scope": "azure",
            "priority": 25,
            "expression": "BilledCost * 1.186065",
            "conditions": None,
            "config_json": {},
        },
        {
            "name": "Realizado GCP (sem imposto)",
            "rule_type": "EXPRESSION",
            "target_column": "realizado",
            "cloud_scope": "gcp",
            "priority": 30,
            "expression": "BilledCost",
            "conditions": None,
            "config_json": {},
        },
        # Generic fallback: any provider without a specific tax/adjustment rule
        # still gets ``realizado = BilledCost``. Keeps a new CSV source (IBM,
        # AWS, OCI, etc.) working with zero code/seed changes.
        #
        # Lowest priority on purpose: it runs FIRST and specific provider rules
        # applied later (higher priority) overwrite it for clouds that need a
        # tax/adjustment factor.
        {
            "name": "Realizado padrão (qualquer nuvem, sem imposto)",
            "rule_type": "EXPRESSION",
            "target_column": "realizado",
            "cloud_scope": None,
            "priority": 5,
            "expression": "BilledCost",
            "conditions": None,
            "config_json": {},
        },
    ]
    for rule in rules:
        session.execute(text("""
            INSERT INTO business_rule (
                connection_id, destination_table_id, name, rule_type, target_column,
                config_json, expression, conditions, priority, cloud_scope, active
            ) VALUES (
                NULL, 1, :name, :rule_type, :target_column,
                CAST(:config_json AS jsonb), :expression, CAST(:conditions AS jsonb),
                :priority, :cloud_scope, true
            )
        """), {
            "name": rule["name"],
            "rule_type": rule["rule_type"],
            "target_column": rule["target_column"],
            "config_json": json.dumps(rule["config_json"]),
            "conditions": json.dumps(rule["conditions"]) if rule["conditions"] else None,
            "expression": rule["expression"],
            "priority": rule["priority"],
            "cloud_scope": rule["cloud_scope"],
        })


def main() -> None:
    session = SyncSession()
    try:
        ensure_cloud_scope_columns(session)
        backfill_classification_scope(session)
        recreate_business_rules(session)
        session.commit()
        print("cloud_scope backfill and business rules recreated.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
