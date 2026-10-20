"""Verificação E5 em ambiente real: reconstrução histórica.

Prova, contra o forecast real:

1. o plano prefere execuções históricas com corte anterior ao mês;
2. a reconstrução usa o motor compartilhado em modo simulado;
3. o corte efetivo é anterior ao mês reconstruído (dados posteriores não entram);
4. **nenhuma** publicação de Planning acontece;
5. as limitações ficam declaradas.

Uso:
    cd apps/api && ../../.venv/bin/python scripts/verify_anomaly_retrospective.py
    cd apps/api && ../../.venv/bin/python scripts/verify_anomaly_retrospective.py --execute 1
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, datetime, timezone
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.anomaly_forecast_adapter import load_contract  # noqa: E402
from app.services.anomaly_retrospective import (  # noqa: E402
    assert_cutoff_excludes_month,
    assert_no_planning_published,
    limitation_summary,
    month_start,
    plan_reconstruction,
)

DSN = os.environ.get("DATABASE_URL", "postgresql+asyncpg://finops:finops@localhost:5433/finops")
DEFAULT_CONFIG = {"measure_column": "realizado_cloud", "source_namespace": "silver", "dimensions": []}


async def _load_runs(engine) -> list[dict]:
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
                     LIMIT 40
                    """
                )
            )
        ).mappings()
        return [dict(row) for row in rows]


async def _load_config(engine, config_id: int):
    from app.models.forecast import ForecastConfig

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT * FROM forecast_config WHERE id = :id"
                ),
                {"id": config_id},
            )
        ).mappings().first()
    return row


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", type=int, default=0, help="quantos meses reconstruir de verdade")
    parser.add_argument("--months", type=str, default="", help="meses separados por vírgula")
    args = parser.parse_args()

    from app.database import async_session
    from app.models.destination import DestinationTable
    from app.models.forecast import ForecastAdjustment, ForecastBusinessRule, ForecastConfig
    from app.services.forecast_service import fetch_metadata, run_forecast, validate_config
    from worker import _resolve_forecast_source_metadata

    engine = create_async_engine(DSN)
    failures: list[str] = []
    runs = await _load_runs(engine)
    contracts = []
    for row in runs:
        contracts.append(await load_contract(row, fallback=DEFAULT_CONFIG))
    usable = [c for c in contracts if c.is_usable]
    print(f"execuções COMPLETED carregadas: {len(runs)} (utilizáveis: {len(usable)})")

    months = [m.strip() for m in args.months.split(",") if m.strip()] or ["202608", "202609", "202610"]
    plan = plan_reconstruction(
        months,
        contracts=contracts,
        current_config_version=usable[0].config_version if usable else None,
    )
    print("\nplano de reconstrução:")
    for month in plan.months:
        month_plan = plan.plans[month]
        print(
            f"   {month}: kind={month_plan.kind} corte={month_plan.cutoff} "
            f"run={month_plan.source_run_id} as_of={month_plan.simulated_as_of}"
        )
    print("   lotes:", plan.batches)
    print("limitações declaradas:")
    for line in limitation_summary(plan):
        print("   -", line)

    if args.execute <= 0:
        await engine.dispose()
        print("\n(use --execute N para rodar a reconstrução de verdade)")
        return 0 if not failures else 1

    config_id = int(runs[0]["forecast_config_id"])
    config_row = await _load_config(engine, config_id)
    if config_row is None:
        print(f"forecast_config {config_id} não encontrado")
        await engine.dispose()
        return 1

    async with async_session() as session:
        config = await session.get(ForecastConfig, config_id)
        dest = await session.get(DestinationTable, config.destination_table_id)
        adjustments = (
            await session.execute(
                text(
                    "SELECT id FROM forecast_adjustment WHERE forecast_config_id = :id AND active"
                ),
                {"id": config_id},
            )
        ).fetchall()
        rules_rows = (
            await session.execute(
                text(
                    "SELECT * FROM forecast_business_rule WHERE forecast_config_id = :id "
                    "AND active ORDER BY priority, id"
                ),
                {"id": config_id},
            )
        ).mappings().all()

    source_namespace, metadata = await _resolve_forecast_source_metadata(dest.physical_name)
    errors = validate_config(config, metadata)
    if errors:
        print("configuração inválida:", errors)
        await engine.dispose()
        return 1
    config.destination_physical_name = dest.physical_name
    config.source_namespace = source_namespace
    config.gold_metadata = metadata
    config.has_type_column = any(c["name"] == config.type_column for c in metadata["columns"])

    own_rules = [SimpleNamespace(**dict(row)) for row in rules_rows]
    targets = plan.reconstruct_months[: args.execute]
    print(f"\nreconstruindo {len(targets)} mês(es): {targets}")

    async def reconstructor(cfg, adjs, cutoff, progress, business_rules, result_table, simulated_as_of):
        return await run_forecast(
            cfg, adjs, cutoff, business_rules=business_rules,
            result_table=result_table, simulated_as_of=simulated_as_of,
        )

    for month in targets:
        month_plan = plan.plans[month]
        table = f"anomaly_ref_{config.id}_{month_plan.simulated_as_of.strftime('%Y%m%d')}"
        started = datetime.now(timezone.utc)
        print(f"\n--- {month}: as_of={month_plan.simulated_as_of} tabela={table}")
        try:
            result = await reconstructor(
                config, [], None, None, own_rules, table, month_plan.simulated_as_of
            )
        except Exception as exc:
            print(f"   FALHOU: {exc}")
            failures.append(f"{month}: {exc}")
            continue
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        print(
            f"   linhas={result.get('rows')} cutoff_efetivo={result.get('effective_cutoff')} "
            f"planning={result.get('planning_table')} ({elapsed:.0f}s)"
        )
        try:
            assert_no_planning_published(result)
            cutoff = assert_cutoff_excludes_month(result, month)
        except RuntimeError as exc:
            print(f"   VIOLAÇÃO: {exc}")
            failures.append(f"{month}: {exc}")
            continue
        if cutoff >= month_start(month):
            failures.append(f"{month}: corte {cutoff} alcança o mês")
        print(f"   OK: corte {cutoff} < início de {month}; Planning não publicado")
        print(f"   limitações: {month_plan.limitation_codes}")

    await engine.dispose()
    if failures:
        print("\nFALHAS:")
        for failure in failures:
            print(" -", failure)
        return 1
    print("\nOK: reconstrução histórica verificada em ambiente real.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
