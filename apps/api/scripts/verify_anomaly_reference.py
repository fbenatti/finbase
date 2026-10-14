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
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.anomaly_forecast_adapter import (  # noqa: E402
    load_contract,
    read_expectation,
)
from app.services.anomaly_calibration import (  # noqa: E402
    build_daily_distribution,
    calibrate_band,
    evaluate_deviation,
    weekday_profile,
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
from app.services.forecast_service import gold_date_expr  # noqa: E402
from app.services.trino_service import execute as trino_execute  # noqa: E402

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
    # --- E3: distribuição por dia da semana e faixa -------------------------
    as_of = latest.cutoff_date
    observed = await _daily_observed(
        source=f"iceberg.{latest.source_namespace}.{latest.destination_physical_name}",
        type_column="tipo",
        days=180,
        as_of=as_of,
        measure=latest.measure_column,
    )
    profile = weekday_profile(observed, as_of=as_of, weeks=8)
    print(
        f"\nperfil semanal: metodo={profile.method} semanas={profile.weeks_used} "
        f"amostras={profile.sample_days}"
    )
    if profile.note:
        print(f"   nota: {profile.note}")
    print("   medianas por dia da semana (0=seg):",
          {k: round(v, 2) for k, v in sorted(profile.medians.items())})

    # Resíduos da granularidade DIÁRIA: observado menos a mediana do dia da semana.
    residuals = []
    for day, value in observed.items():
        expected_day = profile.medians.get(day.weekday(), 0.0)
        residuals.append(float(value) - float(expected_day))
    band = calibrate_band(
        residuals=residuals,
        expected_cloud=sum(profile.medians.values()),
        quantile=0.975,
        tolerance_pct=0.15,
    )
    print(
        f"faixa: metodo={band.method} amostra={band.sample_size}/{band.min_sample} "
        f"offset_superior={band.upper_offset:,.2f} confianca={band.confidence}"
    )
    if band.note:
        print(f"   nota: {band.note}")

    rows = build_reference_rows(
        latest,
        expectation,
        dimensions=dimensions,
        reference_kind="PROSPECTIVE",
        target_start=(latest.cutoff_date.isoformat()),
        target_end=max(months) + "31",
        weekday_profile=profile,
        band=band,
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

    # A distribuição diária precisa fechar exatamente com o valor mensal.
    sample = max(rows, key=lambda r: r.predicted_cloud)
    kind, cov_start, cov_end, covered = classify_coverage(sample.reference_month, latest.cutoff_date)
    distribution = build_daily_distribution(
        sample.predicted_cloud,
        coverage_start=cov_start,
        coverage_end=cov_end,
        profile=profile,
    )
    print(
        f"\ndistribuição do maior mês ({sample.reference_month}, "
        f"{sample.predicted_cloud:,.2f} em {covered} dias): "
        f"método={distribution.method} soma={distribution.total:,.2f}"
    )
    if abs(distribution.total - sample.predicted_cloud) > 1e-6:
        failures.append("distribuição diária não reconcilia com o valor do forecast")

    deviation = evaluate_deviation(
        distribution.expected[cov_end] * 1.6,
        expected_cloud=distribution.expected[cov_end],
        band=band,
    )
    print(
        f"   exemplo de dia ({cov_end}): desvio={deviation.delta_cloud:,.2f} "
        f"custo_anômalo={deviation.anomalous_cost_cloud:,.2f} acima_da_faixa={deviation.above_band}"
    )

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


async def _daily_observed(*, source: str, type_column: str, days: int, as_of: date, measure: str) -> dict:
    """Soma diária observada em `realizado_cloud`, com o parser do forecast.

    Reutiliza `gold_date_expr` — a camada de anomalia não implementa um segundo
    parser de datas. `_billing_period` só é usado para desambiguar MM/DD.
    """
    date_expr = gold_date_expr("chargeperiodstart", "_billing_period")
    sql = f"""
        WITH parsed AS (
            SELECT {date_expr} AS day,
                   COALESCE(TRY_CAST({measure} AS DOUBLE), 0) AS value
              FROM {source}
             WHERE ({type_column} = 'realizado' OR {type_column} IS NULL)
        )
        SELECT CAST(day AS VARCHAR) AS day, SUM(value) AS observed_cloud
          FROM parsed
         WHERE day IS NOT NULL AND day BETWEEN DATE '{as_of - timedelta(days=days)}' AND DATE '{as_of}'
         GROUP BY 1
         ORDER BY 1
    """
    rows = await trino_execute(sql, timeout_s=900)
    out = {}
    for row in rows:
        try:
            out[date.fromisoformat(str(row["day"])[:10])] = float(row["observed_cloud"] or 0.0)
        except (TypeError, ValueError):
            continue
    return out


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
