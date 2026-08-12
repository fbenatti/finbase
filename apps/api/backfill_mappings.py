"""
Backfill EtlMapping records from existing BusinessRules.
For every BusinessRule that has source_column + target_column + connection_id,
create a corresponding EtlMapping if one doesn't already exist.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import SyncSession
from app.models.rules import BusinessRule, EtlMapping
from sqlalchemy import select

with SyncSession() as db:
    rules = db.execute(select(BusinessRule)).scalars().all()
    print(f"Found {len(rules)} BusinessRules")

    created = 0
    skipped = 0
    for rule in rules:
        cfg = rule.config_json or {}
        src_col = cfg.get("source_column", "")
        tgt_col = rule.target_column
        conn_id = rule.connection_id

        if not src_col or not tgt_col or not conn_id:
            skipped += 1
            continue

        # Check if mapping already exists
        existing = db.execute(
            select(EtlMapping).where(
                EtlMapping.connection_id == conn_id,
                EtlMapping.source_column == str(src_col),
                EtlMapping.target_column == tgt_col,
            )
        ).scalars().first()

        if existing:
            skipped += 1
            continue

        mapping = EtlMapping(
            connection_id=conn_id,
            name=f"[Regra] {rule.name}",
            source_column=str(src_col),
            target_column=tgt_col,
            transform_type="EXPRESSION",
            priority=rule.priority,
            active=rule.active,
        )
        db.add(mapping)
        created += 1
        print(f"  Created: {src_col} → {tgt_col} (conn={conn_id}, rule='{rule.name}')")

    db.commit()
    print(f"\nDone. Created {created}, skipped {skipped} (no src col, or already exists).")
