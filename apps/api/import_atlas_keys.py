"""
One-shot script: Parse contrato_pro / contrato_enterprise from .env-atlas
and update the existing mongodb_atlas connections with group_id, public_key, private_key.
"""
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path so 'app' is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import SyncSession
from app.models.source_connection import SourceConnection
from app.config import (
    settings,
    _load_env_file,
    _parse_atlas_contract,
)

ATLAS_FILE = Path(__file__).resolve().parent.parent.parent / ".env-atlas"

print(f"Reading {ATLAS_FILE}...")
env_data = _load_env_file(ATLAS_FILE)

# Parse contract strings
pro_raw = env_data.get("contrato_pro", "")
ent_raw = env_data.get("contrato_enterprise", "")

print(f"  contrato_pro found: {bool(pro_raw)}")
print(f"  contrato_enterprise found: {bool(ent_raw)}")

# Build per-contract data
atlas_data = {
    "pro": {"group_id": "", "public_key": "", "private_key": ""},
    "enterprise": {"group_id": "", "public_key": "", "private_key": ""},
}

if pro_raw:
    gid, pub, priv = _parse_atlas_contract(pro_raw)
    atlas_data["pro"]["group_id"] = gid
    atlas_data["pro"]["public_key"] = pub
    atlas_data["pro"]["private_key"] = priv

if ent_raw:
    gid, pub, priv = _parse_atlas_contract(ent_raw)
    atlas_data["enterprise"]["group_id"] = gid
    atlas_data["enterprise"]["public_key"] = pub
    atlas_data["enterprise"]["private_key"] = priv

print("\nParsed keys:")
for ct, d in atlas_data.items():
    print(f"  {ct}: group_id={'OK' if d['group_id'] else 'MISSING'} "
          f"public_key={'OK' if d['public_key'] else 'MISSING'} "
          f"private_key={'OK' if d['private_key'] else 'MISSING'}")

if not any(d["group_id"] for d in atlas_data.values()):
    print("\nERROR: No contract data found in .env-atlas. Nothing to do.")
    sys.exit(1)

# Update database
with SyncSession() as db:
    from sqlalchemy import select

    conns = db.execute(
        select(SourceConnection).where(
            SourceConnection.connector_type == "mongodb_atlas"
        )
    ).scalars().all()

    print(f"\nFound {len(conns)} MongoDB Atlas connection(s) in database.")

    updated = 0
    for conn in conns:
        cfg = conn.config_json or {}
        ct = cfg.get("contract_type", "")
        keys = atlas_data.get(ct)
        if not keys:
            print(f"  SKIP {conn.code}: unknown contract_type='{ct}'")
            continue

        # Only update if any key is missing or different
        needs_update = (
            str(cfg.get("group_id", "")) != keys["group_id"]
            or str(cfg.get("public_key", "")) != keys["public_key"]
            or str(cfg.get("private_key", "")) != keys["private_key"]
        )

        if not needs_update:
            print(f"  SKIP {conn.code}: keys already match .env-atlas")
            continue

        cfg["group_id"] = keys["group_id"]
        cfg["public_key"] = keys["public_key"]
        cfg["private_key"] = keys["private_key"]
        conn.config_json = cfg
        updated += 1
        print(f"  UPDATED {conn.code}: group_id, public_key, private_key set from .env-atlas")

    if updated:
        db.commit()
        print(f"\nCommitted {updated} update(s).")
    else:
        print("\nNo updates needed — all connections already have the correct keys from .env-atlas.")

print("Done.")
