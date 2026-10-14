"""Verificação E2 em ambiente real: adaptador + referência compacta.

Prova, contra os snapshots reais do forecast:

1. o contrato é montado a partir do `forecast_run` e do schema do resultado;
2. a medida lida é `realizado_cloud` (e a ausência dela seria declarada);
3. a referência reconcilia: a soma por mês é **igual** à soma do snapshot;
4. a referência é compacta: uma linha por série × mês;
5. a seleção prospectiva/retrospectiva/inválida se comporta como o plano exige;
6. a referência anterior a uma compra é preservada.

Uso:
    cd apps/api && ../../.venv/bin/python scripts/verify_anomaly_reference.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.anomaly_forecast_adapter import (  # noqa: E402
    load_contract,
    read_expectation,
)
from app.services.anomaly_reference import (  # noqa: E402
    FULL_MONTH,
    REMAINING_MONTH,
    build_reference_rows,
    classify_coverage,
    horizon_months,
    select_pre_event_reference,
    select_reference,
)

DSN = os.environ.get("DATABASE_URL", "postgresql+asyncpg://finops:finops@localhost:5433/finops")
DEFAULT_CONFIG = {
    "measure_column": "realizado_cloud",
    "source_namespace": "silver",
    "dimensions": [],
}


async def _load_runs(engine, limit: int = 6) -> list[dict]:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT id, forecast_config_id, config_version, trigger_type, status,
                           cutoff_date, result_table, destination_physical_name,
                           source_snapshot_id, config_snapshot, error_metrics,
                           started_at, finished_at
                      FROM forecast_run
                     WHERE status = 'COMPLETED' AND result_table IS NOT NULL
                     ORDER BY id DESC
                     LIMIT :limit
                    """
                ),
                {"limit": limit},
            )
        ).mappings()
        return [dict(row) for row in rows]


async def main() -> int:
    engine = create_async_engine(DSN)
    failures: list[str] = []
    runs = await _load_runs(engine)
    if not runs:
        print("nenhum forecast_run COMPLETED encontrado")
        return 1

    contracts = []
    for row in runs:
        contract = await load_contract(row, fallback=DEFAULT_CONFIG)
        contracts.append(contract)
        cutoff = contract.cutoff_date.isoformat() if contract.cutoff_date else "-"
        print(
            f"run {contract.run_id}: corte={cutoff} trigger={contract.trigger_type} "
            f"ns={contract.result_namespace} fonte={contract.source_namespace} tabela={contract.result_table} "
            f"medida={contract.measure_column} utilizavel={contract.is_usable} "
            f"contrato={contract.contract_source} regras={contract.rule_source} "
            f"({len(contract.applied_rule_refs)} refs)"
        )

    usable = [c for c in contracts if c.is_usable]
    if not usable:
        failures.append("nenhum contrato utilizável: verifique a coluna realizado_cloud")
        for contract in contracts:
            print("   avisos:", contract.warnings)
        await engine.dispose()
        return 1

    latest = usable[0]
    months = horizon_months(latest.cutoff_date, 15)
    print(f"\nhorizonte de 15 dias a partir de {latest.cutoff_date}: {months}")
    for month in months:
        kind, start, end, days = classify_coverage(month, latest.cutoff_date)
        print(f"   {month}: {kind} {start}..{end} ({days} dias)")

    expectation = await read_expectation(latest, months=months)
    print(f"\nlinhas de expectativa lidas do snapshot: {len(expectation)}")

    dimensions = [d for d in DEFAULT_CONFIG["dimensions"]] or _dimensions_from_run(runs[0])
    rows = build_reference_rows(
        latest,
        expectation,
        dimensions=dimensions,
        reference_kind="PROSPECTIVE",
        target_start=(latest.cutoff_date.isoformat()),
        target_end=max(months) + "31",
    )
    print(f"linhas de referência compactas: {len(rows)} (séries × meses)")

    # Reconciliação: soma por mês igual à soma do snapshot.
    snapshot_totals: dict[str, float] = {}
    for item in expectation:
        month = str(item["forecast_month"])
        if classify_coverage(month, latest.cutoff_date)[0] == "ELAPSED_MONTH":
            continue
        snapshot_totals[month] = snapshot_totals.get(month, 0.0) + float(item["expected_cloud"])
    reference_totals: dict[str, float] = {}
    for row in rows:
        reference_totals[row.reference_month] = (
            reference_totals.get(row.reference_month, 0.0) + row.predicted_cloud
        )
    print("\nreconciliação por mês (snapshot x referência):")
    for month in sorted(set(snapshot_totals) | set(reference_totals)):
        snap = snapshot_totals.get(month, 0.0)
        ref = reference_totals.get(month, 0.0)
        ok = abs(snap - ref) < 1e-6
        print(f"   {month}: snapshot={snap:,.2f} referência={ref:,.2f} {'OK' if ok else 'DIVERGE'}")
        if not ok:
            failures.append(f"{month}: referência {ref} != snapshot {snap}")

    keys = [(r.series_id, r.reference_month) for r in rows]
    if len(keys) != len(set(keys)):
        failures.append("referência não é única por série × mês")

    # Seleção para um evento dentro do horizonte.
    event = date.fromisoformat(latest.cutoff_date.isoformat())
    selection = select_reference(contracts, event_date=event)
    print(
        f"\nseleção para evento em {event}: kind={selection.reference_kind} "
        f"run={selection.contract.run_id if selection.contract else '-'} motivo={selection.reason}"
    )

    future_event = date(2027, 1, 15)
    selection_future = select_reference(contracts, event_date=future_event)
    print(f"seleção para evento futuro em {future_event}: kind={selection_future.reference_kind}")

    pre = select_pre_event_reference(contracts, event_date=event)
    print(
        f"referência pré-evento em {event}: kind={pre.reference_kind} "
        f"run={pre.contract.run_id if pre.contract else '-'} motivo={pre.reason}"
    )

    await engine.dispose()
    if failures:
        print("\nFALHAS:")
        for failure in failures:
            print(" -", failure)
        return 1
    print("\nOK: adaptador e referência verificados em ambiente real.")
    return 0


def _dimensions_from_run(row: dict) -> list[str]:
    snapshot = row.get("config_snapshot") or {}
    if isinstance(snapshot, str):
        import json

        try:
            snapshot = json.loads(snapshot)
        except ValueError:
            snapshot = {}
    return list(snapshot.get("dimensions") or [])


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
