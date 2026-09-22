#!/usr/bin/env python
"""Marca, de forma idempotente, quais conexões são fonte de custo (P1B).

Motivo: o destino FOCUS aceitava mapping de qualquer conexão. Automation e
Resource Graph (fontes operacionais, sem valor financeiro) escreveram 22.747
linhas com `realizado` nulo no livro financeiro. Identificar "fonte de custo"
por prefixo textual (`azure-`) é errado: `azure-automation-*` e
`azure-resource-graph-*` casam com o prefixo e não são custo.

Este script grava o marcador explícito em `source_connection.config_json`:

- `source_key = "cost_exports"` e `cost_source = true` para as conexões que
  produzem linhas financeiras no FOCUS;
- `cost_source = false` para as fontes operacionais conhecidas, para que
  reincidência seja bloqueada na criação de mapping.

Não altera `code`, Bronze, Silver, Gold nem histórico. Rodar duas vezes não
muda nada.

Uso (a partir de `apps/api`):

    PYTHONPATH=. ../../.venv/bin/python scripts/mark_cost_sources.py --dry-run
    PYTHONPATH=. ../../.venv/bin/python scripts/mark_cost_sources.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL_SYNC", "postgresql://finops:finops@localhost:5433/finops")

from sqlalchemy import create_engine, text  # noqa: E402

#: Conexões que produzem linhas de custo no destino FOCUS.
COST_CODES = (
    "azure-4p",
    "azure-ti",
    "azure-marketplace",
    "gcp-4p",
    "gcp-ti",
    "gcp-ttech",
    "mongodb-atlas-pro",
    "mongodb-atlas-enterprise",
    "ibm-ti",
    "oci-prod",
)

#: Fontes operacionais: alimentam inventário/catálogo/governança, nunca o
#: livro financeiro. Mantidas explícitas para que o bloqueio não dependa de
#: heurística de nome.
NON_COST_CODES = (
    "azure-automation-4p",
    "azure-automation-ti",
    "azure-resource-graph-4p",
    "azure-resource-graph-ti",
    "azure-4p-ptu",
)


def _engine():
    url = os.environ["DATABASE_URL_SYNC"].replace("postgresql+asyncpg://", "postgresql://")
    return create_engine(url, future=True)


def plan(engine) -> list[tuple[str, dict]]:
    """Devolve (code, config_novo) para cada conexão que precisa de mudança."""
    changes: list[tuple[str, dict]] = []
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT code, connector_type, config_json FROM source_connection ORDER BY code"
        )).all()
    for code, connector_type, config in rows:
        current = dict(config or {})
        if code in COST_CODES:
            desired = {**current, "source_key": "cost_exports", "cost_source": True}
        elif code in NON_COST_CODES:
            desired = {**current, "cost_source": False}
            desired.pop("source_key", None)
        else:
            continue
        if desired != current:
            changes.append((code, desired))
    return changes


def apply_changes(engine, changes: list[tuple[str, dict]]) -> int:
    with engine.begin() as conn:
        for code, config in changes:
            conn.execute(
                text("UPDATE source_connection SET config_json = CAST(:cfg AS json) WHERE code = :code"),
                {"cfg": json.dumps(config, ensure_ascii=False), "code": code},
            )
    return len(changes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="grava as mudanças")
    args = parser.parse_args()

    engine = _engine()
    changes = plan(engine)
    if not changes:
        print("nada a fazer: marcadores já estão corretos")
        return 0

    for code, config in changes:
        key = config.get("source_key")
        cost = config.get("cost_source")
        print(f"  {code}: cost_source={cost}" + (f" source_key={key}" if key else ""))

    if not args.apply:
        print(f"\n{len(changes)} conexão(ões) seriam atualizadas (dry-run; use --apply)")
        return 0

    total = apply_changes(engine, changes)
    print(f"\n{total} conexão(ões) atualizadas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
