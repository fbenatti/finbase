#!/usr/bin/env python
"""Audita as referências de namespace nos templates SQL persistidos (P1C).

Os templates vivem em `dimon_rule.sql_template` e são executados **verbatim**
pelo materializador — não passam pelo resolvedor central. Por isso uma mudança
de camada no código não os corrige, e uma substituição textual cega é perigosa
(nem toda tabela `dest_*` mudou de camada, e o namespace legado ainda existe).

Este script não altera nada: ele **verifica**. Para cada tabela referenciada como
`iceberg.<namespace>.<tabela>`:

1. confirma se a referência existe no catálogo Iceberg;
2. aponta quando a tabela existe em **outro** namespace (referência divergente);
3. registra, para rollback, o SQL de cada template em `--dump`.

Uso (a partir de `apps/api`):

    PYTHONPATH=. ../../.venv/bin/python scripts/migrate_persisted_sql_templates.py
    PYTHONPATH=. ../../.venv/bin/python scripts/migrate_persisted_sql_templates.py --dump saida.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL_SYNC", "postgresql://finops:finops@localhost:5433/finops")

from sqlalchemy import create_engine, text  # noqa: E402

from app.services import iceberg_service as iceberg  # noqa: E402
from app.services import lakehouse_layers as layers  # noqa: E402

#: `iceberg.<namespace>.<tabela>` — o que o SQL persistido referencia.
REFERENCE = re.compile(r"iceberg\.([a-z_]+(?:\.[a-z_]+)?)\.([a-z_0-9]+)", re.IGNORECASE)


def catalog_tables() -> dict[str, set[str]]:
    """Tabelas por namespace, lidas do catálogo (fonte de verdade)."""
    result: dict[str, set[str]] = {}
    for namespace in layers.KNOWN_LAYERS:
        try:
            result[namespace] = set(iceberg.list_tables(namespace))
        except Exception:
            result[namespace] = set()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", metavar="ARQUIVO", help="grava o SQL atual de cada template")
    args = parser.parse_args()

    engine = create_engine(
        os.environ["DATABASE_URL_SYNC"].replace("postgresql+asyncpg://", "postgresql://"),
        future=True,
    )
    with engine.connect() as conn:
        rules = conn.execute(text(
            "SELECT id, code, sql_template FROM dimon_rule "
            "WHERE sql_template IS NOT NULL ORDER BY id"
        )).all()

    if not rules:
        print("nenhum template SQL persistido")
        return 0

    catalog = catalog_tables()

    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as handle:
            json.dump(
                [{"id": r[0], "code": r[1], "sql_template": r[2]} for r in rules],
                handle, indent=2, ensure_ascii=False,
            )
        print(f"SQL atual de {len(rules)} template(s) salvo em {args.dump} (rollback)")

    problems = 0
    for rule_id, code, sql in rules:
        references = sorted({(m.group(1).lower(), m.group(2).lower())
                             for m in REFERENCE.finditer(sql)})
        print(f"\n=== template {rule_id} · {code} ===")
        if not references:
            print("  (sem referência iceberg.<ns>.<tabela>)")
            continue
        for namespace, table in references:
            plain = namespace.split(".")[0]
            if table in catalog.get(plain, set()):
                print(f"  OK       iceberg.{namespace}.{table}")
                continue
            elsewhere = sorted(ns for ns, tables in catalog.items() if table in tables)
            if elsewhere:
                problems += 1
                print(f"  DIVERGE  iceberg.{namespace}.{table} "
                      f"-> existe em: {', '.join(elsewhere)}")
            else:
                print(f"  AUSENTE  iceberg.{namespace}.{table} (não registrada no catálogo)")

    print(f"\nreferências divergentes: {problems}")
    print("Nenhuma alteração foi feita (script de verificação).")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
