"""Verificação E6 em PostgreSQL real: publicação e ciclo de vida dos casos.

Prova, dentro de uma transação revertida no final (nada é persistido):

1. a publicação corrente é trocada atomicamente e mantém `previous_run_id`;
2. **falha preserva a publicação anterior** (rollback não altera a corrente);
3. uma única publicação corrente por configuração;
4. reprocessamento **não duplica** caso nem impacto;
5. caso encerrado não reabre por repetição técnica e reabre com continuação;
6. o evento de cada transição fica no histórico imutável.

Uso:
    cd apps/api && ../../.venv/bin/python scripts/verify_anomaly_publication.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Tabelas referenciadas por FK: sem estes imports o metadata não resolve
# `etl_task`, `forecast_config` e `user_account`.
from app.models import destination as _destination_models  # noqa: E402,F401
from app.models import etl_task as _etl_task_models  # noqa: E402,F401
from app.models import forecast as _forecast_models  # noqa: E402,F401
from app.models import identity as _identity_models  # noqa: E402,F401
from app.models.anomaly import (  # noqa: E402
    AnomalyCase,
    AnomalyCaseEvent,
    AnomalyPublication,
    AnomalyRun,
)
from app.services.anomaly_release import (  # noqa: E402
    apply_detection_to_case,
    commit_publication,
    current_publication,
)

DSN = os.environ.get("DATABASE_URL", "postgresql+asyncpg://finops:finops@localhost:5433/finops")


def detection(*, case_key: str, anomalous: float, status: str = "OPEN", severity: str = "MEDIUM", state=None):
    return SimpleNamespace(
        case_key=case_key,
        series_id="serie-verificacao",
        series_dimensions={"cloud_origem": "gcp-4p", "servicename": "Compute"},
        event_family="cloud_origem=gcp-4p",
        event_type="DAILY_CONSUMPTION",
        event_date="2026-10-13",
        period="202610",
        source_billing_period="202610",
        observed_cloud=1500.0,
        expected_cloud=1000.0,
        lower_cloud=900.0,
        upper_cloud=1200.0,
        delta_cloud=500.0,
        anomalous_cost_cloud=anomalous,
        score=75.0,
        severity=severity,
        confidence="MEDIUM",
        assessment_status=status,
        evaluation_grain="DAILY",
        reason_codes=["EXPLICIT_TOLERANCE"],
        reference_id="ref-verificacao",
        reference_kind="PROSPECTIVE",
        applied_rule_refs=[],
        contributors=[],
        components={},
        expected_only=False,
    )


async def main() -> int:
    engine = create_async_engine(DSN)
    failures: list[str] = []
    from app.database import async_session

    async with async_session() as session:
        config_id = (await session.execute(select(AnomalyPublication.anomaly_config_id))).first()
        from app.models.anomaly import AnomalyConfig

        config = (await session.execute(select(AnomalyConfig).order_by(AnomalyConfig.id).limit(1))).scalar_one()
        config_id = config.id
        print(f"configuração de anomalia usada: id={config_id} name={config.name!r}")

        run1 = AnomalyRun(anomaly_config_id=config_id, status="RUNNING", trigger_type="MANUAL")
        run2 = AnomalyRun(anomaly_config_id=config_id, status="RUNNING", trigger_type="MANUAL")
        session.add_all([run1, run2])
        await session.flush()

        # --- publicação corrente ------------------------------------------
        pub1 = await commit_publication(
            session,
            anomaly_config_id=config_id,
            run_id=run1.id,
            reference_table="ref_a",
            assessment_table="assess_a",
            data_cutoff="2026-10-12",
            generated_at=datetime.now(timezone.utc),
            reference_rows=10,
            assessment_rows=3,
            series_count=2,
        )
        print(f"publicação 1: id={pub1.id} corrente={pub1.is_current} anterior={pub1.previous_run_id}")
        if not pub1.is_current:
            failures.append("a primeira publicação deveria ser corrente")

        pub2 = await commit_publication(
            session,
            anomaly_config_id=config_id,
            run_id=run2.id,
            reference_table="ref_b",
            assessment_table="assess_b",
            data_cutoff="2026-10-13",
            generated_at=datetime.now(timezone.utc),
            reference_rows=20,
            assessment_rows=5,
            series_count=4,
        )
        await session.flush()
        current = await current_publication(session, config_id)
        print(
            f"publicação 2: id={pub2.id} corrente={pub2.is_current} "
            f"anterior(run)={pub2.previous_run_id} | corrente agora={current.id}"
        )
        if current.id != pub2.id:
            failures.append("a publicação 2 deveria ser a corrente")
        if pub2.previous_run_id != run1.id:
            failures.append("previous_run_id deveria apontar para a execução anterior")
        first_now = await session.get(AnomalyPublication, pub1.id)
        if first_now.is_current:
            failures.append("a publicação 1 deveria ter deixado de ser corrente")

        total_current = (
            await session.execute(
                select(func.count())
                .select_from(AnomalyPublication)
                .where(
                    AnomalyPublication.anomaly_config_id == config_id,
                    AnomalyPublication.is_current.is_(True),
                )
            )
        ).scalar_one()
        print(f"publicações correntes para a configuração: {total_current}")
        if total_current != 1:
            failures.append(f"esperava exatamente 1 publicação corrente, encontrei {total_current}")

        # --- falha preserva a publicação anterior --------------------------
        # Savepoint: a falha reverte apenas a tentativa, como faria o rollback
        # de uma publicação interrompida.
        try:
            async with session.begin_nested():
                await commit_publication(
                    session,
                    anomaly_config_id=config_id,
                    run_id=run2.id,
                    reference_table="ref_falha",
                    assessment_table="assess_falha",
                )
                await session.flush()
                raise RuntimeError("falha simulada após a troca de corrente")
        except RuntimeError:
            pass
        current_after_failure = await current_publication(session, config_id)
        print(f"após falha simulada, corrente = {current_after_failure.id if current_after_failure else None}")
        if current_after_failure is None or current_after_failure.id != pub2.id:
            failures.append(
                "a falha alterou a publicação corrente: "
                f"esperava {pub2.id}, obtive {getattr(current_after_failure, 'id', None)}"
            )

        # --- casos ---------------------------------------------------------
        case_key = "verificacao-caso-key"
        outcome1 = await apply_detection_to_case(
            session, detection=detection(case_key=case_key, anomalous=900.0), anomaly_config_id=config_id
        )
        await session.flush()
        print(f"caso criado: id={outcome1.case_id} ação={outcome1.action}")
        if outcome1.action != "CREATE" or not outcome1.created:
            failures.append(f"esperava CREATE, obtive {outcome1.action}")

        outcome2 = await apply_detection_to_case(
            session, detection=detection(case_key=case_key, anomalous=400.0), anomaly_config_id=config_id
        )
        await session.flush()
        case = await session.get(AnomalyCase, outcome1.case_id)
        print(
            f"reprocessamento: ação={outcome2.action} impacto={case.anomalous_cost_cloud} "
            f"versão={case.case_version}"
        )
        if outcome2.action != "UPDATE":
            failures.append(f"esperava UPDATE, obtive {outcome2.action}")
        if case.anomalous_cost_cloud != 900.0:
            failures.append(f"impacto deveria ser max(900, 400)=900, obtive {case.anomalous_cost_cloud}")
        count_cases = (
            await session.execute(
                select(func.count()).select_from(AnomalyCase).where(AnomalyCase.case_key == case_key)
            )
        ).scalar_one()
        if count_cases != 1:
            failures.append(f"reprocessamento duplicou caso: {count_cases}")

        # caso encerrado: repetição técnica não reabre
        case.state = "CLOSED"
        await session.flush()
        outcome3 = await apply_detection_to_case(
            session, detection=detection(case_key=case_key, anomalous=900.0), anomaly_config_id=config_id
        )
        await session.flush()
        case = await session.get(AnomalyCase, outcome1.case_id)
        print(f"caso encerrado + repetição: ação={outcome3.action} estado={case.state} reopen_count={case.reopen_count}")
        if outcome3.action != "IGNORE" or case.state != "CLOSED":
            failures.append("caso encerrado não deveria reabrir por repetição técnica")
        if case.anomalous_cost_cloud != 900.0:
            failures.append("o impacto do caso encerrado não deveria mudar")

        # continuação reabre com evento auditado
        outcome4 = await apply_detection_to_case(
            session,
            detection=detection(case_key=case_key, anomalous=1200.0),
            anomaly_config_id=config_id,
            continuation=True,
        )
        await session.flush()
        case = await session.get(AnomalyCase, outcome1.case_id)
        print(f"continuação: ação={outcome4.action} estado={case.state} reopen_count={case.reopen_count}")
        if outcome4.action != "REOPEN" or case.state != "INVESTIGATING":
            failures.append("continuação deveria reabrir o caso")
        if case.anomalous_cost_cloud != 1200.0:
            failures.append(f"impacto após reabertura deveria ser 1200, obtive {case.anomalous_cost_cloud}")

        events = (
            await session.execute(
                select(AnomalyCaseEvent.event_type)
                .where(AnomalyCaseEvent.case_id == case.id)
                .order_by(AnomalyCaseEvent.id)
            )
        ).scalars().all()
        print(f"histórico do caso: {events}")
        for required in ("CASE_CREATED", "CASE_UPDATED", "REPROCESSING_IGNORED", "CASE_REOPENED"):
            if required not in events:
                failures.append(f"histórico sem o evento {required}")

        # evento esperado não cria caso
        outcome5 = await apply_detection_to_case(
            session,
            detection=detection(case_key="esperado", anomalous=0.0, status="EXPECTED_EVENT"),
            anomaly_config_id=config_id,
        )
        await session.flush()
        print(f"evento esperado: ação={outcome5.action} caso={outcome5.case_id}")
        if outcome5.action != "EXPECTED_ONLY" or outcome5.case_id is not None:
            failures.append("evento esperado não deveria abrir caso")

        # nada é persistido
        await session.rollback()

    await engine.dispose()
    if failures:
        print("\nFALHAS:")
        for failure in failures:
            print(" -", failure)
        return 1
    print("\nOK: publicação e ciclo de vida dos casos verificados em PostgreSQL real (rollback).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
