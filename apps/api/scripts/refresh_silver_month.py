#!/usr/bin/env python
"""Ciclo de refresh de um mês em Silver, com medição e verificação.

Uso (a partir de `apps/api`):

    PYTHONPATH=. ../../.venv/bin/python scripts/refresh_silver_month.py 202609 --measure
    PYTHONPATH=. ../../.venv/bin/python scripts/refresh_silver_month.py 202609 --delete-silver
    PYTHONPATH=. ../../.venv/bin/python scripts/refresh_silver_month.py 202609 --reprocess

Por que existe: durante a migração Gold → Silver é preciso poder limpar e
reconstruir um mês específico da tabela FOCUS repetidamente, sempre a partir do
Bronze (que nunca é tocado), e conferir cada iteração. Fazer isso à mão, com
`DELETE` avulso, foi como a tabela de planejamento acabou reduzida a rateio.

Garantias deste utilitário:

- opera **somente** em `silver.{destination}` e **somente** sobre
  `_billing_period = <mês>`; Bronze, Gold, forecast e demais tabelas ficam fora;
- exclusão por `source_connection_id`, um a um, pelo serviço oficial
  (`iceberg_service.delete_destination_slice`), que registra snapshot Iceberg —
  a operação é reversível, os arquivos antigos continuam referenciados;
- imprime a medição antes e depois, para comparar com o esperado;
- o reprocessamento é enfileirado pela API existente (`POST /etl/reprocess`),
  nunca executado inline.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL_SYNC", "postgresql://finops:finops@localhost:5433/finops")

from sqlalchemy import create_engine, text  # noqa: E402

from app.services import iceberg_service as iceberg  # noqa: E402
from app.services import lakehouse_layers as layers  # noqa: E402

API = os.getenv("OPENFINOPS_API_URL", "http://localhost:8001")
#: Tabela de destino FOCUS detalhada (destination_table.id = 1).
FOCUS_DESTINATION = os.getenv("OPENFINOPS_FOCUS_DESTINATION", "dest_modelo_focus")
FOCUS_DESTINATION_ID = int(os.getenv("OPENFINOPS_FOCUS_DESTINATION_ID", "1"))


def _engine():
    url = os.environ["DATABASE_URL_SYNC"].replace("postgresql+asyncpg://", "postgresql://")
    return create_engine(url, future=True)


def measure(period: str, destination: str = FOCUS_DESTINATION) -> dict:
    """Contagem e soma por origem dentro do mês, direto do Iceberg (sem Trino)."""
    import pandas as pd

    namespace = layers.detail_layer()
    table = iceberg._get_catalog().load_table((namespace, destination))
    frame = table.scan().to_pandas()
    frame = frame[frame["_billing_period"].astype(str) == str(period)]
    if frame.empty:
        return {"period": period, "namespace": namespace, "total_rows": 0, "by_source": {}}
    if "realizado" in frame.columns:
        frame["realizado_num"] = pd.to_numeric(frame["realizado"], errors="coerce")
    else:
        frame["realizado_num"] = None
    grouped = frame.groupby("source_connection_id", dropna=False).agg(
        rows=("_billing_period", "size"),
        realizado=("realizado_num", "sum"),
    )
    by_source = {}
    for idx, row in grouped.iterrows():
        source = "null" if idx != idx else str(int(idx))
        value = row["realizado"]
        by_source[source] = {
            "rows": int(row["rows"]),
            "realizado": None if value != value else round(float(value), 2),
        }
    return {
        "period": period,
        "namespace": namespace,
        "table": f"{namespace}.{destination}",
        "total_rows": int(len(frame)),
        "by_source": by_source,
    }


def delete_silver(period: str, destination: str = FOCUS_DESTINATION) -> None:
    """Remove o mês inteiro de Silver, por origem, via serviço oficial."""
    namespace = layers.detail_layer()
    sources = list(measure(period, destination)["by_source"].keys())
    if not sources:
        print(f"nada a remover em {namespace}.{destination} para {period}")
        return
    for source in sources:
        iceberg.delete_destination_slice(
            destination,
            billing_period=period,
            connection_id=int(source),
            namespace=namespace,
        )
        print(f"  removido {namespace}.{destination} period={period} source_connection_id={source}")


def reprocess(period: str, destination_id: int = FOCUS_DESTINATION_ID) -> int:
    """Enfileira um reprocessamento do mês e devolve o id da task."""
    with _engine().connect() as conn:
        connection_ids = [
            int(row[0])
            for row in conn.execute(text(
                "SELECT DISTINCT source_connection_id FROM source_destination_mapping "
                "WHERE destination_table_id = :dest AND active = true "
                "ORDER BY source_connection_id"
            ), {"dest": destination_id})
        ]
    if not connection_ids:
        raise SystemExit(f"nenhuma conexão mapeada para o destino {destination_id}")
    payload = json.dumps({
        "destination_table_id": destination_id,
        "connection_ids": connection_ids,
        "billing_periods": [period],
        "replace_existing": False,
    }).encode()
    request = urllib.request.Request(
        f"{API}/etl/reprocess", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"reprocess falhou ({exc.code}): {exc.read().decode()[:300]}")
    print(f"task {body.get('task_id', body)} enfileirada para {period} "
          f"({len(connection_ids)} conexões)")
    return int(body.get("task_id") or 0)


def wait_task(task_id: int, timeout_s: int = 3600) -> str:
    """Acompanha a task até terminal; devolve o status final."""
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        with _engine().connect() as conn:
            row = conn.execute(text(
                "SELECT status, phase, rows_destination FROM etl_task WHERE id = :id"
            ), {"id": task_id}).first()
        if row:
            status, phase, rows = row
            line = f"{status} · {rows or 0} linhas · {phase or ''}"
            if line != last:
                print(f"  {line}")
                last = line
            if status in {"COMPLETED", "FAILED", "CANCELLED", "COMPLETED_WITH_ERRORS"}:
                return status
        time.sleep(5)
    return "TIMEOUT"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("period", help="mês no formato YYYYMM")
    parser.add_argument("--destination", default=FOCUS_DESTINATION)
    parser.add_argument("--destination-id", type=int, default=FOCUS_DESTINATION_ID)
    parser.add_argument("--measure", action="store_true", help="mede o mês e sai")
    parser.add_argument("--delete-silver", action="store_true", help="remove o mês de Silver")
    parser.add_argument("--reprocess", action="store_true", help="enfileira o reprocessamento")
    parser.add_argument("--wait", action="store_true", help="aguarda a task terminar")
    args = parser.parse_args()

    if args.measure or not (args.delete_silver or args.reprocess):
        print(json.dumps(measure(args.period, args.destination), indent=2, ensure_ascii=False))
        if not (args.delete_silver or args.reprocess):
            return 0

    if args.delete_silver:
        print(f"removendo {args.period} de Silver…")
        delete_silver(args.period, args.destination)
        print("depois da remoção:")
        print(json.dumps(measure(args.period, args.destination), indent=2, ensure_ascii=False))

    if args.reprocess:
        task_id = reprocess(args.period, args.destination_id)
        if args.wait and task_id:
            status = wait_task(task_id)
            print(f"task {task_id}: {status}")
            print(json.dumps(measure(args.period, args.destination), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
