#!/usr/bin/env python
"""Medição e verificação de contaminação do destino FOCUS — P1B.

Lê `iceberg.silver.dest_modelo_focus` via Trino (a tabela não cabe em pandas:
~40M linhas) e responde três perguntas:

1. qual a soma de `realizado` por período e origem;
2. quantas linhas vêm de origens que **não** são de custo;
3. as duas coisas antes e depois de uma limpeza.

Uso (a partir de `apps/api`):

    PYTHONPATH=. ../../.venv/bin/python scripts/measure_focus_contamination.py --baseline
    PYTHONPATH=. ../../.venv/bin/python scripts/measure_focus_contamination.py --verify baseline.json

O JSON de `--baseline` é o contrato de aceite de P1B: a soma de `realizado` não
pode mudar; a contagem de linhas só pode cair exatamente nas linhas não-custo.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL_SYNC", "postgresql://finops:finops@localhost:5433/finops")

from sqlalchemy import create_engine, text  # noqa: E402

from app.services import lakehouse_layers as layers  # noqa: E402

TRINO = os.getenv("TRINO_URL", "http://localhost:8080")
FOCUS_TABLE = os.getenv("OPENFINOPS_FOCUS_TABLE", "dest_modelo_focus")


def trino(sql: str, tries: int = 12) -> list[list]:
    """Executa SQL no Trino com retry — o cluster reinicia workers sozinho."""
    last = ""
    for _ in range(tries):
        try:
            request = urllib.request.Request(
                f"{TRINO}/v1/statement", data=sql.encode(),
                headers={
                    "X-Trino-User": "p1b-audit",
                    "X-Trino-Catalog": "iceberg",
                    "X-Trino-Schema": layers.detail_layer(),
                },
            )
            payload = json.load(urllib.request.urlopen(request, timeout=60))
            rows: list[list] = []
            while True:
                rows.extend(payload.get("data") or [])
                next_uri = payload.get("nextUri")
                if not next_uri:
                    error = payload.get("error")
                    if error:
                        raise RuntimeError(str(error.get("message"))[:300])
                    return rows
                time.sleep(0.4)
                payload = json.load(urllib.request.urlopen(
                    urllib.request.Request(next_uri, headers={"X-Trino-User": "p1b-audit"}),
                    timeout=60,
                ))
        except Exception as exc:  # noqa: BLE001 - retry cobre quedas do Trino
            last = str(exc)
            time.sleep(2)
    raise RuntimeError(f"Trino indisponível após retries: {last}")


def cost_connections() -> set[int]:
    """Conexões marcadas como fonte de custo explícita em `config_json`."""
    url = os.environ["DATABASE_URL_SYNC"].replace("postgresql+asyncpg://", "postgresql://")
    engine = create_engine(url, future=True)
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id FROM source_connection "
            "WHERE config_json->>'cost_source' = 'true' "
            "   OR config_json->>'source_key' = 'cost_exports'"
        ))
        return {int(row[0]) for row in rows}


def measure() -> dict:
    table = f"iceberg.{layers.detail_layer()}.{FOCUS_TABLE}"
    per_period = trino(f"""
        SELECT _billing_period,
               count(*) AS linhas,
               count(realizado) AS com_realizado,
               round(sum(try_cast(realizado AS double)), 2) AS realizado
        FROM {table}
        GROUP BY 1 ORDER BY 1
    """)
    per_source = trino(f"""
        SELECT source_connection_id,
               count(*) AS linhas,
               count(realizado) AS com_realizado,
               round(sum(try_cast(realizado AS double)), 2) AS realizado
        FROM {table}
        GROUP BY 1 ORDER BY 1
    """)
    cost = cost_connections()
    sources = {
        str(int(row[0])): {
            "linhas": int(row[1]),
            "com_realizado": int(row[2]),
            "realizado": None if row[3] is None else round(float(row[3]), 2),
        }
        for row in per_source
    }
    contamined_rows = sum(
        info["linhas"] for source, info in sources.items()
        if cost and int(source) not in cost
    )
    return {
        "tabela": table,
        "cost_connections": sorted(cost),
        "per_period": {
            str(row[0]): {
                "linhas": int(row[1]),
                "com_realizado": int(row[2]),
                "realizado": None if row[3] is None else round(float(row[3]), 2),
            }
            for row in per_period
        },
        "per_source": sources,
        "linhas_origem_nao_custo": contamined_rows,
        "total_linhas": sum(info["linhas"] for info in sources.values()),
        "total_realizado": round(sum(
            info["realizado"] for info in sources.values() if info["realizado"] is not None
        ), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", metavar="ARQUIVO", help="grava a medição como baseline")
    parser.add_argument("--verify", metavar="ARQUIVO", help="compara com um baseline salvo")
    args = parser.parse_args()

    current = measure()

    if args.baseline:
        with open(args.baseline, "w", encoding="utf-8") as handle:
            json.dump(current, handle, indent=2, ensure_ascii=False)
        print(f"baseline gravado em {args.baseline}")
        print(f"  total linhas   : {current['total_linhas']}")
        print(f"  total realizado: {current['total_realizado']}")
        print(f"  linhas não-custo: {current['linhas_origem_nao_custo']}")
        return 0

    if args.verify:
        with open(args.verify, encoding="utf-8") as handle:
            baseline = json.load(handle)
        print(json.dumps(current, indent=2, ensure_ascii=False))
        print("\n=== verificação contra o baseline ===")
        ok = True
        if current["total_realizado"] != baseline["total_realizado"]:
            ok = False
            print(f"FALHA: realizado mudou ({baseline['total_realizado']} -> {current['total_realizado']})")
        else:
            print(f"OK: realizado inalterado em {current['total_realizado']}")
        remaining = {
            source: info["linhas"] for source, info in current["per_source"].items()
            if current["cost_connections"] and int(source) not in current["cost_connections"]
        }
        if remaining:
            ok = False
            print(f"FALHA: ainda há linhas de origem não-custo: {remaining}")
        else:
            print("OK: nenhuma linha de origem não-custo permanece no FOCUS")
        removed = baseline["total_linhas"] - current["total_linhas"]
        expected = baseline["linhas_origem_nao_custo"]
        if removed != expected:
            ok = False
            print(f"FALHA: removidas {removed} linhas; esperado exatamente {expected}")
        else:
            print(f"OK: removidas exatamente {removed} linhas, iguais às linhas não-custo do baseline")
        return 0 if ok else 1

    print(json.dumps(current, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
