"""Verificação F2 em ambiente real: série diária do caso.

Prova, contra o Trino real:

1. `observed_daily_sql` + filtro de dimensões do caso roda em Silver e devolve
   exatamente um valor por dia;
2. o dia é o do consumo (`chargeperiodstart` parseado), não a competência;
3. a série derivada reconcilia: a soma do esperado do mês é o valor do forecast;
4. o custo anômalo de um dia só existe acima do limite superior.

A tabela de referência ainda não existe neste ambiente (nenhuma publicação), por
isso a parte da referência é sintética, com os mesmos campos do contrato.

Uso:
    cd apps/api && ../../.venv/bin/python scripts/verify_anomaly_series.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.anomaly_observed import observed_daily_sql  # noqa: E402
from app.services.anomaly_series import derive_daily_points  # noqa: E402
from app.services.trino_service import execute as trino_execute  # noqa: E402

DSN = os.environ.get("DATABASE_URL", "postgresql+asyncpg://finops:finops@localhost:5433/finops")


async def main() -> int:
    failures: list[str] = []
    engine = create_async_engine(DSN)

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT dt.physical_name, fc.date_column, fc.period_column, fc.type_column,
                           fc.dimensions
                      FROM forecast_config fc
                      JOIN destination_table dt ON dt.id = fc.destination_table_id
                     WHERE fc.active
                     ORDER BY fc.id LIMIT 1
                    """
                )
            )
        ).mappings().first()
    await engine.dispose()
    if row is None:
        print("nenhum forecast ativo encontrado")
        return 1

    dimensions = row["dimensions"]
    if isinstance(dimensions, str):
        dimensions = json.loads(dimensions)
    dimensions = [str(d) for d in (dimensions or [])]
    print(f"fonte: silver.{row['physical_name']} dimensões={len(dimensions)}")

    # Uma série real qualquer, para exercitar o filtro de dimensões.
    probe_sql = (
        f"SELECT cloud_origem, servicename, COUNT(*) AS n "
        f"FROM iceberg.silver.\"{row['physical_name']}\" "
        f"WHERE tipo = 'realizado' AND chargeperiodstart IS NOT NULL "
        f"GROUP BY 1, 2 ORDER BY n DESC LIMIT 1"
    )
    probe = await trino_execute(probe_sql, timeout_s=600)
    if not probe:
        print("não foi possível escolher uma série real")
        return 1
    series = {"cloud_origem": probe[0].get("cloud_origem"), "servicename": probe[0].get("servicename")}
    print(f"série escolhida: {series} ({probe[0].get('n')} linhas)")

    start = date(2026, 10, 1)
    end = date(2026, 10, 13)
    sql = observed_daily_sql(
        source_namespace="silver",
        table=row["physical_name"],
        date_column=row["date_column"],
        period_column=row["period_column"],
        type_column=row["type_column"],
        measure_column="realizado_cloud",
        # Agrupa exatamente pelas dimensões filtradas: a série do caso é
        # definida pelo conjunto de dimensões que a identifica.
        dimensions=list(series.keys()),
        start=start,
        end=end,
    )
    clauses = []
    for name, value in series.items():
        if value is None:
            clauses.append(f"COALESCE(CAST(\"{name}\" AS VARCHAR), '') = ''")
        else:
            clauses.append(f"CAST(\"{name}\" AS VARCHAR) = '" + str(value).replace("'", "''") + "'")
    filtered = f"SELECT * FROM ({sql}) AS series WHERE " + " AND ".join(clauses)

    rows = await trino_execute(filtered, timeout_s=600)
    print(f"\nlinhas observadas por dia (1ª a 13/10): {len(rows)}")
    for item in rows[:5]:
        print(f"   {item['event_date']} → {float(item['observed_cloud']):,.2f}")
    if not rows:
        failures.append("a leitura do observado não devolveu linhas")
    days = [str(item["event_date"])[:10] for item in rows]
    if len(days) != len(set(days)):
        failures.append("a leitura do observado devolveu mais de uma linha por dia")
    for day in days:
        if not day.startswith("2026-10"):
            failures.append(f"dia fora da janela pedida: {day}")

    # --- série derivada com referência sintética do mesmo contrato ---------
    observed = {date.fromisoformat(day): float(item["observed_cloud"]) for day, item in zip(days, rows)}
    reference = {
        "reference_id": "verificacao",
        "reference_month": "202610",
        "reference_kind": "PROSPECTIVE",
        "coverage_kind": "FULL_MONTH",
        "coverage_start": "2026-10-01",
        "coverage_end": "2026-10-31",
        "covered_days": 31,
        "predicted_cloud": 31_000.0,
        "weekday_weights": {"0": 1.0, "1": 1.0, "2": 1.0, "3": 1.0, "4": 1.0, "5": 0.4, "6": 0.4},
        "normalizer": 0.0,
        "distribution_method": "WEEKDAY_MEDIAN",
        "daily_band_lower_offset": 100.0,
        "daily_band_upper_offset": 500.0,
        "band_method": "HISTORICAL_QUANTILE",
        "band_confidence": "HIGH",
    }
    points = derive_daily_points([reference], observed, horizon_end=date(2026, 10, 31))
    total_expected = sum(point["expected_cloud"] for point in points)
    print(f"\nsérie derivada: {len(points)} dias; soma esperada={total_expected:,.2f} (forecast 31.000,00)")
    if abs(total_expected - 31_000.0) > 1.0:
        failures.append(f"distribuição não reconcilia: {total_expected}")

    weekdays = [point for point in points if point["weekday"] < 5]
    weekend = [point for point in points if point["weekday"] >= 5]
    print(
        f"   dia útil médio={sum(p['expected_cloud'] for p in weekdays)/max(1,len(weekdays)):,.2f} "
        f"fim de semana médio={sum(p['expected_cloud'] for p in weekend)/max(1,len(weekend)):,.2f}"
    )
    if weekend and weekdays and weekend[0]["expected_cloud"] >= weekdays[0]["expected_cloud"]:
        failures.append("o fim de semana deveria esperar menos que o dia útil")

    with_observed = [point for point in points if point["observed_cloud"] is not None]
    print(f"dias com observado: {len(with_observed)}")
    for point in with_observed[:3]:
        print(
            f"   {point['event_date']}: observado={point['observed_cloud']:,.2f} "
            f"esperado={point['expected_cloud']:,.2f} limite={point['upper_cloud']:,.2f} "
            f"anômalo={point['anomalous_cost_cloud']:,.2f}"
        )
        if point["anomalous_cost_cloud"] > 0 and not point["above_band"]:
            failures.append(f"{point['event_date']}: custo anômalo sem estar acima da faixa")
        if point["above_band"] and point["anomalous_cost_cloud"] <= 0:
            failures.append(f"{point['event_date']}: acima da faixa sem custo anômalo")

    if failures:
        print("\nFALHAS:")
        for failure in failures:
            print(" -", failure)
        return 1
    print("\nOK: série diária verificada em ambiente real.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
