#!/usr/bin/env python
"""Projeta o catálogo declarativo de fontes Azure na tabela de metadata (P2).

Idempotente: rodar duas vezes não muda nada. Não altera `code`, Bronze, Silver,
Gold nem histórico — apenas metadata do catálogo e o `source_key` das conexões.

Uso (a partir de `apps/api`):

    PYTHONPATH=. ../../.venv/bin/python scripts/seed_azure_source_catalog.py
    PYTHONPATH=. ../../.venv/bin/python scripts/seed_azure_source_catalog.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL_SYNC", "postgresql://finops:finops@localhost:5433/finops")

from sqlalchemy import create_engine, text  # noqa: E402

from app.models.azure_source_catalog import AzureSourceCatalogEntry  # noqa: E402
from app.database import Base  # noqa: E402
from app.services.azure_source_registry import (  # noqa: E402
    AZURE_SOURCE_CATALOG,
    environment_slug,
    source_key_for,
)

#: Colunas que o seed projeta (a tabela é metadata; nada de segredo aqui).
COLUMNS = (
    "source_key", "display_name", "description", "position", "connector_type",
    "code_pattern", "bronze_pattern", "silver_target", "gold_target",
    "is_cost_source", "api_reference", "required_permission",
    "default_window_days", "default_metrics", "implementation_status", "azure_scope",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="grava as mudanças")
    args = parser.parse_args()

    url = os.environ["DATABASE_URL_SYNC"].replace("postgresql+asyncpg://", "postgresql://")
    engine = create_engine(url, future=True)

    # Garante a tabela sem depender do startup da API.
    Base.metadata.create_all(engine, tables=[AzureSourceCatalogEntry.__table__])

    with engine.begin() as conn:
        existing = {
            row[0] for row in conn.execute(
                text("SELECT source_key FROM azure_source_catalog")
            )
        }

    declared_keys = {entry["source_key"] for entry in AZURE_SOURCE_CATALOG}
    to_insert = [e for e in AZURE_SOURCE_CATALOG if e["source_key"] not in existing]
    to_remove = sorted(existing - declared_keys)
    print(f"catálogo: {len(AZURE_SOURCE_CATALOG)} fontes declaradas, {len(existing)} já na base, "
          f"{len(to_insert)} a inserir, {len(to_remove)} obsoletas")
    for entry in to_insert:
        print(f"  + {entry['source_key']:24s} {entry['display_name']}")
    for source_key in to_remove:
        print(f"  - {source_key:24s} metadata obsoleta (a conexão/histórico serão mantidos)")

    # Liga as conexões existentes ao catálogo e grava o slug do ambiente.
    with engine.connect() as conn:
        connections = conn.execute(text(
            "SELECT id, code, connector_type, config_json FROM source_connection "
            "WHERE cloud_scope = 'azure' ORDER BY code"
        )).all()

    import json as _json
    link_changes = []
    for connection_id, code, connector_type, config_json in connections:
        config = dict(config_json or {})
        class _C:  # adaptador mínimo para reusar o resolvedor
            pass
        stub = _C()
        stub.code = code
        stub.connector_type = connector_type
        stub.config_json = config
        key = source_key_for(stub)
        slug = environment_slug(stub)
        changed = {}
        if key and config.get("source_key") != key:
            changed["source_key"] = key
        if config.get("environment_slug") != slug:
            changed["environment_slug"] = slug
        if changed:
            link_changes.append((connection_id, code, changed))

    print(f"\nconexões Azure: {len(connections)}")
    for connection_id, code, changed in link_changes:
        print(f"  ~ {code:26s} {changed}")

    if not args.apply:
        print("\n(dry-run; use --apply para gravar)")
        return 0

    with engine.begin() as conn:
        for source_key in to_remove:
            conn.execute(
                text("DELETE FROM azure_source_catalog WHERE source_key = :source_key"),
                {"source_key": source_key},
            )
        for entry in to_insert:
            values = {
                column: (entry.get("default_metrics") if column == "default_metrics" else entry.get(column))
                for column in COLUMNS
            }
            values["default_metrics"] = _json.dumps(values.get("default_metrics") or [])
            placeholders = ", ".join(f":{c}" for c in COLUMNS)
            conn.execute(
                text(f"INSERT INTO azure_source_catalog ({', '.join(COLUMNS)}) VALUES ({placeholders})"),
                values,
            )
        for connection_id, code, changed in link_changes:
            # Mescla os campos sem perder o resto do config da conexão.
            row = conn.execute(
                text("SELECT config_json FROM source_connection WHERE id = :id"), {"id": connection_id}
            ).scalar()
            merged = dict(row or {})
            merged.update(changed)
            conn.execute(
                text("UPDATE source_connection SET config_json = CAST(:cfg AS json) WHERE id = :id"),
                {"cfg": _json.dumps(merged, ensure_ascii=False), "id": connection_id},
            )

    print(
        f"\n{len(to_insert)} fonte(s) inserida(s), {len(to_remove)} metadata obsoleta(s) removida(s), "
        f"{len(link_changes)} conexão(ões) vinculada(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
