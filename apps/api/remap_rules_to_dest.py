"""
Remap Classification Rules and Business Rules to use DESTINATION table column names.

After ETL runs, source columns are renamed via SourceDestinationMapping.
Classification and Business rules run AFTER column remapping, so they need to
reference destination column names, not source column names.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import SyncSession
from app.models.rules import ClassificationRule, BusinessRule
from app.models.source_connection import SourceConnection
from app.models.destination import SourceDestinationMapping, DestinationColumn, DestinationTable
from sqlalchemy import select, text

DEST_TABLE_ID = 1

def remap_field(src_to_dest, src_to_dest_ci, field_name):
    """Remap a source column name to destination column name.
    Returns (new_field_name, was_remapped)."""
    if not field_name:
        return field_name, False
    if field_name in src_to_dest:
        new_val = src_to_dest[field_name]
        return (new_val, new_val != field_name)
    # case-insensitive fallback
    ci_key = field_name.lower()
    if ci_key in src_to_dest_ci:
        mapped_src = src_to_dest_ci[ci_key]
        new_val = src_to_dest[mapped_src]
        return (new_val, new_val != field_name)
    return field_name, False


def main():
    session = SyncSession()
    try:
        # 1. Load mappings: source_col -> dest_col
        mappings = session.execute(
            select(SourceDestinationMapping).where(
                SourceDestinationMapping.destination_table_id == DEST_TABLE_ID,
                SourceDestinationMapping.active == True,
            )
        ).scalars().all()

        src_to_dest = {}
        src_to_dest_ci = {}
        for m in mappings:
            if m.source_column not in src_to_dest:
                src_to_dest[m.source_column] = m.destination_column
            src_to_dest_ci[m.source_column.lower()] = m.source_column

        print(f"Loaded {len(src_to_dest)} mappings")

        # 2. Existing destination columns
        dest_cols = session.execute(
            select(DestinationColumn).where(DestinationColumn.destination_table_id == DEST_TABLE_ID)
        ).scalars().all()
        existing_dest_cols = {dc.column_name for dc in dest_cols}
        max_position = max((dc.position for dc in dest_cols), default=0)

        # 3. Collect unmapped fields from classification rules
        all_rules = session.execute(
            select(ClassificationRule).where(
                ClassificationRule.destination_table_id == DEST_TABLE_ID,
            )
        ).scalars().all()
        print(f"\nLoaded {len(all_rules)} classification rules")

        unmapped = set()
        fields_conns = {}

        for rule in all_rules:
            if rule.source_field:
                _, was_remapped = remap_field(src_to_dest, src_to_dest_ci, rule.source_field)
                if not was_remapped and rule.source_field not in src_to_dest:
                    unmapped.add(rule.source_field)
                    if rule.connection_id:
                        fields_conns.setdefault(rule.source_field, set()).add(rule.connection_id)

            if rule.conditions:
                for cond in rule.conditions:
                    field = cond.get("field", "")
                    if field:
                        _, was_remapped = remap_field(src_to_dest, src_to_dest_ci, field)
                        if not was_remapped and field not in src_to_dest:
                            unmapped.add(field)
                            if rule.connection_id:
                                fields_conns.setdefault(field, set()).add(rule.connection_id)

        # 4. Collect unmapped fields from business rules
        biz_rules = session.execute(
            select(BusinessRule).where(
                BusinessRule.destination_table_id == DEST_TABLE_ID,
            )
        ).scalars().all()
        print(f"Loaded {len(biz_rules)} business rules")

        for rule in biz_rules:
            if rule.config_json:
                src_col = rule.config_json.get("source_column", "")
                if src_col:
                    _, was_remapped = remap_field(src_to_dest, src_to_dest_ci, src_col)
                    if not was_remapped and src_col not in src_to_dest:
                        unmapped.add(src_col)
                        if rule.connection_id:
                            fields_conns.setdefault(src_col, set()).add(rule.connection_id)

        print(f"\nUnmapped fields: {len(unmapped)}")
        for f in sorted(unmapped):
            conns = fields_conns.get(f, set())
            print(f"  {f:35s} conns: {conns if conns else '(global)'}")

        # 5. Create dest columns + mappings for unmapped fields
        created_cols = 0
        created_maps = 0

        for field in sorted(unmapped):
            if field not in existing_dest_cols:
                max_position += 1
                session.execute(
                    text("INSERT INTO destination_column (destination_table_id, column_name, data_type, is_required, position) VALUES (:dtid, :name, :dtype, :req, :pos)"),
                    {"dtid": DEST_TABLE_ID, "name": field, "dtype": "TEXT", "req": False, "pos": max_position},
                )
                existing_dest_cols.add(field)
                created_cols += 1
                print(f"  + col: {field}")

            for conn_id in fields_conns.get(field, set()):
                if conn_id is None:
                    continue
                existing = session.execute(
                    text("SELECT id FROM source_destination_mapping WHERE source_connection_id=:cid AND destination_table_id=:did AND source_column=:sc AND destination_column=:dc"),
                    {"cid": conn_id, "did": DEST_TABLE_ID, "sc": field, "dc": field},
                ).fetchone()
                if not existing:
                    session.execute(
                        text("INSERT INTO source_destination_mapping (source_connection_id, destination_table_id, name, source_column, destination_column, transform_type, priority, active) VALUES (:cid, :did, :nm, :sc, :dc, 'RENAME', 0, true)"),
                        {"cid": conn_id, "did": DEST_TABLE_ID, "nm": f"{field}", "sc": field, "dc": field},
                    )
                    src_to_dest[field] = field
                    created_maps += 1
                    print(f"  + map: conn={conn_id} | {field}")

        session.flush()
        print(f"\nCreated: {created_cols} cols, {created_maps} mappings")

        # 6. Remap classification rules
        updated_cls = 0
        for rule in all_rules:
            changed = False

            new_sf, sf_remapped = remap_field(src_to_dest, src_to_dest_ci, rule.source_field)
            if sf_remapped:
                print(f"  Cls#{rule.id}: source_field {rule.source_field} -> {new_sf}")
                rule.source_field = new_sf
                changed = True

            if rule.conditions:
                new_conditions = []
                for cond in rule.conditions:
                    nc = dict(cond)
                    field = cond.get("field", "")
                    if field:
                        new_f, f_remapped = remap_field(src_to_dest, src_to_dest_ci, field)
                        if f_remapped:
                            print(f"  Cls#{rule.id}: cond.field {field} -> {new_f}")
                            nc["field"] = new_f
                            changed = True
                    new_conditions.append(nc)
                if changed:
                    rule.conditions = new_conditions

            if changed:
                updated_cls += 1

        print(f"\nUpdated {updated_cls} classification rules")

        # 7. Remap business rules
        updated_biz = 0
        for rule in biz_rules:
            changed = False
            if rule.config_json:
                cfg = dict(rule.config_json)
                src_col = cfg.get("source_column", "")
                new_sc, sc_remapped = remap_field(src_to_dest, src_to_dest_ci, src_col)
                if sc_remapped:
                    cfg["source_column"] = new_sc
                    rule.config_json = cfg
                    changed = True
                    print(f"  Biz#{rule.id}: {src_col} -> {new_sc}")
                elif src_col and src_col in src_to_dest:
                    # Already correct (case-match)
                    new_sc = src_to_dest[src_col]
                    if new_sc != src_col:
                        cfg["source_column"] = new_sc
                        rule.config_json = cfg
                        changed = True
                        print(f"  Biz#{rule.id}: (exact) {src_col} -> {new_sc}")

            if changed:
                updated_biz += 1

        print(f"Updated {updated_biz} business rules")

        session.commit()
        print("\nDone! Remap complete.")

    except Exception as e:
        session.rollback()
        import traceback
        traceback.print_exc()
    finally:
        session.close()


if __name__ == "__main__":
    main()
