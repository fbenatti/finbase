"""
Reimport classification & business rules from finopsNew CSV.
Translates source column names → destination column names (post-mapping).
Classification happens AFTER mapping, so conditions reference FOCUS column names.

Usage: cd apps/api && python reimport_rules.py
"""
import sys, os, json
sys.path.insert(0, ".")
os.environ.setdefault("APP_ENV", "development")
import pandas as pd
from app.database import SyncSession
from sqlalchemy import text


CLOUD_TO_CONN_ID = {
    "Azure-4P": 1,  "Azure-TI": 2,  "GCP-4P": 3,  "GCP-TI": 4,
    "Atlas-MongoDB": 5, "Atlas": 5, "MongoDB Atlas": 5,
}


def load_src_to_dest(session):
    """Load source_column → destination_column mapping from DB (MODELO FOCUS, dest_table_id=1)."""
    rows = session.execute(text(
        "SELECT DISTINCT source_column, destination_column FROM source_destination_mapping "
        "WHERE destination_table_id = 1"
    )).all()
    mapping = {}
    for src, dest in rows:
        if src and dest:
            mapping[src.lower().strip()] = dest.strip()
    # Also add identity mappings for fields that exist in destination with same name
    identity = [
        "pricingModel", "chargeType", "resourceGroupName", "meterCategory",
        "meterSubCategory", "subscriptionName", "reservationName", "meterName",
        "ResourceId",
    ]
    for f in identity:
        if f.lower() not in mapping:
            mapping[f.lower()] = f
    return mapping


def translate_conditions(conditions, src_to_dest):
    """Translate condition field names from source → destination column names."""
    translated = []
    for c in conditions:
        field = c["field"].strip()
        # Find best match: exact, case-insensitive, or via mapping
        dest_field = src_to_dest.get(field.lower(), field)
        translated.append({
            "field": dest_field,
            "operator": c["operator"],
            "values": c["values"],
            "logic": c["logic"],
        })
    return translated


def parse_conditions(cond_str):
    if not cond_str or pd.isna(cond_str):
        return []
    conditions = []
    for part in [p.strip() for p in cond_str.split(" ; ") if p.strip()]:
        if "~" in part:
            field, value = part.split("~", 1)
            operator = "~"
        elif "=" in part:
            field, value = part.split("=", 1)
            operator = "="
        else:
            field, value = part, ""
            operator = "="
        field = field.strip()
        values_list = [v.strip() for v in value.strip().split("|") if v.strip()]
        if field and values_list:
            conditions.append({
                "field": field,
                "operator": operator,
                "values": values_list,
                "logic": "AND",
            })
    return conditions


def main():
    csv_path = "/Users/fernandohenriquebenatti/projetos/OpenFinops/businessRules_download.csv"
    df = pd.read_csv(csv_path, sep=";")
    session = SyncSession()

    try:
        # ── 0. Load source→destination mapping ──
        print("=== Loading source → destination column mapping ===")
        src_to_dest = load_src_to_dest(session)
        print(f"  Loaded {len(src_to_dest)} mappings")
        for src in sorted(src_to_dest)[:10]:
            print(f"    {src} → {src_to_dest[src]}")

        # ── 1. WIPE ──
        print("\n=== Cleaning up existing rules ===")
        session.execute(text("DELETE FROM business_rule"))
        session.execute(text("DELETE FROM classification_rule"))
        session.execute(text("ALTER SEQUENCE IF EXISTS business_rule_id_seq RESTART WITH 1"))
        session.execute(text("ALTER SEQUENCE IF EXISTS classification_rule_id_seq RESTART WITH 1"))
        session.commit()

        # ── 2. CLASSIFICATION RULES (translated to destination columns) ──
        print("\n=== Importing classification rules (destination column names) ===")
        imported = skipped = untranslated = 0
        for _, row in df.iterrows():
            cloud = str(row.get("Cloud", "")).strip()
            conn_id = CLOUD_TO_CONN_ID.get(cloud)
            if conn_id is None:
                skipped += 1
                continue

            projeto = str(row.get("Projeto", "")).strip()
            classificacao = str(row.get("Classificacao", "")).strip()
            cond_str = str(row.get("Condicoes", "")).strip()
            ordem = int(row.get("Ordem", 100))
            conditions = parse_conditions(cond_str)
            if not conditions:
                skipped += 1
                continue

            # Translate field names source → destination
            conditions = translate_conditions(conditions, src_to_dest)

            name = f"{classificacao} → {projeto}" if classificacao and classificacao != "nan" else f"Classifica → {projeto}"

            # source_field is the first condition field (now in destination terms)
            source_field = conditions[0]["field"]

            # Check if any field wasn't translated
            for c in conditions:
                if c["field"] not in src_to_dest.values():
                    untranslated += 1

            conditions_json = json.dumps(conditions, ensure_ascii=False)

            session.execute(text("""
                INSERT INTO classification_rule
                    (connection_id, destination_table_id, name, source_field, pattern, pattern_type,
                     match_value, target_field, conditions, priority, active)
                VALUES
                    (:cid, 1, :name, :sf, :pat, 'multi_condition', :mv, 'projeto',
                     CAST(:cond AS jsonb), :prio, true)
            """), {
                "cid": conn_id, "name": name, "sf": source_field,
                "pat": json.dumps([v for c in conditions for v in c["values"]])[:500],
                "mv": projeto[:200], "cond": conditions_json, "prio": ordem,
            })
            imported += 1

        session.commit()
        print(f"  Imported {imported} classification rules, skipped {skipped}")
        if untranslated > 0:
            print(f"  WARNING: {untranslated} conditions with untranslated fields")

        # ── 3. BUSINESS RULES ──
        print("\n=== Importing business rules ===")
        # NOTE: Business rules are applied on source dataframe BEFORE mapping.
        # source_column must use SOURCE column names (e.g., costInBillingCurrency, not BilledCost).
        br_data = [
            ("PTAX BRL - USD to BRL (R$ 6.20)", "PTAX_BRL", 10, "CostBRL",
             '{"source_column":"costInBillingCurrency","ptax_rate":6.20,"expression":"costInBillingCurrency * 6.20"}',
             "costInBillingCurrency * 6.20", None),
            ("Taxa ISS + PIS/COFINS (18.61%)", "TAX_BASE", 20, "Tax_Base",
             '{"source_column":"CostBRL","tax_rate":0.1861,"expression":"CostBRL * 0.1861"}',
             "CostBRL * 0.1861", None),
            ("Taxa Marketplace (13.83%)", "TAX_BASE", 25, "Tax_Marketplace",
             '{"source_column":"CostBRL","tax_rate":0.1383,"expression":"CostBRL * 0.1383","condition_field":"chargeType","condition_value":"Marketplace"}',
             "CostBRL * 0.1383",
             '[{"field":"chargeType","operator":"=","values":["Marketplace"],"logic":"AND"}]'),
            ("Taxa Extra (0.83%)", "MARKUP", 90, "VL_CUSTO_FINAL_BRL",
             '{"source_column":"CostBRL","tax_rate":0.0083,"expression":"(CostBRL + Tax_Base + Tax_Marketplace) * 1.0083"}',
             "(CostBRL + Tax_Base + Tax_Marketplace) * 1.0083", None),
        ]

        for name, rtype, prio, target, cfg_json, expr, cond in br_data:
            session.execute(text("""
                INSERT INTO business_rule
                    (connection_id, destination_table_id, name, rule_type, target_column,
                     config_json, expression, conditions, priority, active)
                VALUES
                    (NULL, 1, :name, :rtype, :target, CAST(:cfg AS jsonb), :expr,
                     CAST(:cond AS jsonb), :prio, true)
            """), {
                "name": name, "rtype": rtype, "target": target,
                "cfg": cfg_json, "expr": expr, "cond": cond, "prio": prio,
            })
        session.commit()
        print(f"  Imported {len(br_data)} business rules")

        # ── 4. VERIFY ──
        print("\n=== Verification ===")
        br = session.execute(text("SELECT count(*) FROM business_rule")).scalar()
        cr = session.execute(text("SELECT count(*) FROM classification_rule")).scalar()
        print(f"  Business rules: {br}")
        print(f"  Classification rules: {cr}")

        print("\nSample classification rules (with DESTINATION column names):")
        for r in session.execute(text(
            "SELECT name, source_field, conditions "
            "FROM classification_rule ORDER BY priority LIMIT 8"
        )).all():
            conds = r[2] if isinstance(r[2], list) else (json.loads(r[2]) if r[2] else [])
            cond_str = " ; ".join(
                f'{c["field"]}{c["operator"]}{"|".join(c["values"])}'
                for c in conds
            ) if conds else "none"
            print(f"  {r[0][:55]:55s} | field={r[1]:25s} | {cond_str}")

    except Exception as e:
        session.rollback()
        import traceback
        traceback.print_exc()
    finally:
        session.close()


if __name__ == "__main__":
    main()
