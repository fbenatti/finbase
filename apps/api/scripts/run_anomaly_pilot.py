"""Piloto manual ponta a ponta da detecção de anomalias (F5/E10).

Executa UMA detecção real com o motor publicado:

1. habilita a configuração (decisão explícita; nasce desabilitada);
2. resolve o contrato do forecast, lê a expectativa e o observado;
3. avalia as séries, consolida episódios e publica referência + avaliações;
4. cria/atualiza casos no PostgreSQL;
5. mostra o resumo, os casos criados e o caminho até o encerramento.

Uso:
    cd apps/api && ../../.venv/bin/python scripts/run_anomaly_pilot.py [--keep-disabled]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import anomaly as anomaly_models  # noqa: E402,F401
from app.models import destination as _destination  # noqa: E402,F401
from app.models import etl_task as _etl_task  # noqa: E402,F401
from app.models import forecast as _forecast  # noqa: E402,F401
from app.models import identity as _identity  # noqa: E402,F401
from app.models import source_connection as _source_connection  # noqa: E402,F401
from app.models.anomaly import AnomalyCase, AnomalyConfig, AnomalyPublication, AnomalyRun  # noqa: E402

DSN = os.environ.get("DATABASE_URL", "postgresql+asyncpg://finops:finops@localhost:5433/finops")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-id", type=int, default=0)
    parser.add_argument("--keep-disabled", action="store_true", help="desabilita a configuração no fim")
    args = parser.parse_args()

    from app.database import async_session
    from app.services.anomaly_runner import execute_detection

    failures: list[str] = []
    engine = create_async_engine(DSN)

    async with async_session() as session:
        if args.config_id:
            config = await session.get(AnomalyConfig, args.config_id)
        else:
            config = (
                await session.execute(select(AnomalyConfig).order_by(AnomalyConfig.id).limit(1))
            ).scalar_one_or_none()
        if config is None:
            print("nenhuma configuração de anomalia cadastrada")
            await engine.dispose()
            return 1
        config_id = config.id
        print(f"configuração: id={config_id} nome={config.name!r} perfil={config.profile}")

        if not config.enabled:
            config.enabled = True
            await session.flush()
            print("configuração habilitada para o piloto (nasce desabilitada)")

        # Execução própria, como o worker cria: o piloto é um ensaio fiel.
        run = AnomalyRun(
            anomaly_config_id=config_id,
            config_version=config.version,
            trigger_type="MANUAL",
            status="QUEUED",
            phase="Piloto manual",
            config_snapshot={"config_id": config_id, "config_version": config.version, "pilot": True},
        )
        session.add(run)
        await session.flush()
        pilot_run_id = run.id
        await session.commit()
        print(f"execução criada para o piloto: id={pilot_run_id}")

    # --- execução ---------------------------------------------------------
    def progress(done: int, total: int, name: str) -> None:
        print(f"   [{done}/{total}] {name}")

    print("\nexecutando a detecção (isto pode levar alguns minutos)...")
    try:
        summary = await execute_detection(config_id, run_id=pilot_run_id, progress=progress)
    except Exception as exc:
        print(f"\nFALHOU: {exc}")
        await engine.dispose()
        return 1

    print("\nresumo da execução:")
    for key, value in summary.items():
        if key == "warnings":
            continue
        print(f"   {key} = {value}")
    for warning in summary.get("warnings") or []:
        print(f"   aviso: {warning}")

    # --- estado publicado -------------------------------------------------
    async with async_session() as session:
        run = (
            await session.execute(
                select(AnomalyRun).order_by(AnomalyRun.id.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if run is not None:
            print(
                f"\nexecução registrada: id={run.id} status={run.status} fase={run.phase!r} "
                f"forecast_run={run.forecast_run_id} corte={run.data_cutoff} "
                f"séries={run.processed_series} linhas={run.rows_written} "
                f"casos novos={run.cases_created} atualizados={run.cases_updated}"
            )
            if run.status != "COMPLETED":
                failures.append(f"execução terminou como {run.status}: {run.error}")

        publication = (
            await session.execute(
                select(AnomalyPublication)
                .where(AnomalyPublication.anomaly_config_id == config_id, AnomalyPublication.is_current.is_(True))
                .order_by(AnomalyPublication.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if publication is None:
            failures.append("nenhuma publicação corrente foi registrada")
        else:
            print(
                f"publicação corrente: run={publication.run_id} "
                f"referência={publication.reference_table} ({publication.reference_count} linhas) "
                f"avaliações={publication.assessment_table} ({publication.row_count} linhas) "
                f"séries={publication.series_count}"
            )

        cases = (
            await session.execute(select(AnomalyCase).order_by(AnomalyCase.score.desc()).limit(10))
        ).scalars().all()
        total_cases = (
            await session.execute(text("SELECT count(*) FROM anomaly_case"))
        ).scalar_one()
        print(f"\ncasos no banco: {total_cases} (10 melhores por nota)")
        for case in cases:
            print(
                f"   #{case.id} nota={case.score:.0f} {case.severity} estado={case.state} "
                f"família={case.event_family} data={case.event_date} "
                f"esperado={case.expected_cloud} observado={case.observed_cloud} "
                f"anômalo={case.anomalous_cost_cloud} ref={case.reference_kind}"
            )

    # --- tabelas publicadas no Iceberg -----------------------------------
    if publication is not None:
        from app.services.anomaly_queries import read_expected_events, read_quality
        from app.services.trino_service import execute as trino_execute

        async with async_session() as session:
            config = await session.get(AnomalyConfig, config_id)
        tables = await trino_execute(
            "SELECT table_name FROM iceberg.information_schema.tables "
            f"WHERE table_schema = '{config.source_namespace}' AND table_name IN "
            f"('{publication.reference_table}', '{publication.assessment_table}')",
            timeout_s=120,
        )
        print(f"\ntabelas publicadas encontradas: {[row['table_name'] for row in tables]}")
        for expected in (publication.reference_table, publication.assessment_table):
            if expected not in {row["table_name"] for row in tables}:
                failures.append(f"tabela {expected} não foi criada no Iceberg")

        counts = await trino_execute(
            f"SELECT count(*) AS total FROM iceberg.{config.source_namespace}.\"{publication.assessment_table}\"",
            timeout_s=180,
        )
        print(f"avaliações publicadas: {counts[0]['total'] if counts else 0}")

        statuses = await trino_execute(
            "SELECT assessment_status, count(*) AS n, "
            "sum(anomalous_cost_cloud) AS anomalous "
            f"FROM iceberg.{config.source_namespace}.\"{publication.assessment_table}\" "
            "GROUP BY 1 ORDER BY 2 DESC",
            timeout_s=180,
        )
        print("por status:")
        for row in statuses:
            print(
                f"   {row['assessment_status']}: {row['n']} "
                f"(custo anômalo {float(row['anomalous'] or 0):,.2f})"
            )

        try:
            expected = await read_expected_events(
                namespace=config.source_namespace, table=publication.assessment_table, limit=5
            )
            print(f"eventos esperados: total={expected['total']}")
            for item in expected["items"][:3]:
                print(
                    f"   {item['family']}: {item['occurrences']} ocorrência(s), "
                    f"esperado={item['expected_cloud']:,.2f} observado={item['observed_cloud']:,.2f}"
                )
            quality = await read_quality(
                namespace=config.source_namespace, table=publication.assessment_table
            )
            print("qualidade:")
            for bucket in quality["items"][:5]:
                print(f"   {bucket['reason_code']}: {bucket['count']}")
        except Exception as exc:
            failures.append(f"consultas publicadas falharam: {exc}")

    if args.keep_disabled:
        async with async_session() as session:
            config = await session.get(AnomalyConfig, config_id)
            config.enabled = False
            await session.commit()
        print("\nconfiguração devolvida para desabilitada (--keep-disabled)")

    await engine.dispose()
    if failures:
        print("\nFALHAS:")
        for failure in failures:
            print(" -", failure)
        return 1
    print("\nOK: piloto ponta a ponta concluído.")
    print(json.dumps({"config_id": config_id}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
