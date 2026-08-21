"""
Seed business rules that derive DaysSinceLastActivity and ActivityStatus on dest_licencas_ia.

Usage: cd apps/api && python seed_ai_business_rules.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import select, text

from app.database import SyncSession
from app.models.destination import DestinationTable
from app.models.rules import BusinessRule
from app.models.source_connection import SourceConnection  # noqa: F401 — register FK metadata


DEFAULT_THRESHOLDS = {
    "active_days": 30,
    "inactive_30_days": 60,
    "inactive_60_days": 90,
}


def _destination_id(session) -> int | None:
    row = session.execute(
        select(DestinationTable.id).where(DestinationTable.physical_name == "dest_licencas_ia")
    ).scalar_one_or_none()
    return int(row) if row else None


def ensure_ai_license_business_rules(session) -> dict[str, int]:
    dest_id = _destination_id(session)
    if dest_id is None:
        print("dest_licencas_ia not found — run seed_ai_destinations.py first")
        return {"created": 0, "updated": 0}

    specs = [
        {
            "name": "IA — dias desde última atividade",
            "description": "Calcula DaysSinceLastActivity a partir de LastActivityAt",
            "target_column": "DaysSinceLastActivity",
            "rule_type": "DATE_DIFF",
            "config_json": {
                "source_column": "LastActivityAt",
                "unit": "days",
            },
            "priority": 10,
        },
        {
            "name": "IA — status de atividade",
            "description": "Deriva ActivityStatus (NUNCA_USOU, ATIVO, INATIVO_30D, INATIVO_60D, INATIVO_90D)",
            "target_column": "ActivityStatus",
            "rule_type": "AI_ACTIVITY_STATUS",
            "config_json": {
                "last_activity_column": "LastActivityAt",
                "days_column": "DaysSinceLastActivity",
                "thresholds": DEFAULT_THRESHOLDS,
                "values": {
                    "never": "NUNCA_USOU",
                    "active": "ATIVO",
                    "inactive_30": "INATIVO_30D",
                    "inactive_60": "INATIVO_60D",
                    "inactive_90": "INATIVO_90D",
                },
            },
            "priority": 20,
        },
    ]

    created = updated = 0
    for spec in specs:
        existing = session.execute(
            select(BusinessRule).where(
                BusinessRule.destination_table_id == dest_id,
                BusinessRule.name == spec["name"],
            )
        ).scalar_one_or_none()
        if existing:
            for key, value in spec.items():
                setattr(existing, key, value)
            existing.destination_table_id = dest_id
            existing.active = True
            updated += 1
        else:
            session.add(BusinessRule(
                destination_table_id=dest_id,
                connection_id=None,
                active=True,
                **spec,
            ))
            created += 1
    session.commit()
    print(f"AI license business rules: created={created}, updated={updated}, destination_id={dest_id}")
    return {"created": created, "updated": updated, "destination_id": dest_id}


def main() -> None:
    session = SyncSession()
    try:
        session.execute(text(
            "ALTER TABLE business_rule ADD COLUMN IF NOT EXISTS cloud_scope VARCHAR(50)"
        ))
        session.commit()
        ensure_ai_license_business_rules(session)
    finally:
        session.close()


if __name__ == "__main__":
    main()
