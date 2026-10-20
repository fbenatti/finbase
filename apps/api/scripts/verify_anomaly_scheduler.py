"""Verificação F1 em ambiente real: agendamentos de anomalia.

O caminho do agendador é **síncrono** (APScheduler → `_run_etl_job` → sessão
síncrona), então esta verificação usa `SyncSession`, igual à produção.

Prova:

1. os dois agendamentos padrão existem (detecção INATIVA, prazos ativo);
2. o monitor de prazos roda de verdade e reporta contagens;
3. o enfileiramento respeita "uma execução por configuração", o estado
   habilitado e a seleção por `anomaly_config_ids`;
4. nada é persistido: tudo é revertido no final.

Uso:
    cd apps/api && ../../.venv/bin/python scripts/verify_anomaly_scheduler.py
"""
from __future__ import annotations

import os
import sys

from sqlalchemy import func, select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import anomaly as anomaly_models  # noqa: E402,F401
from app.models import destination as _destination  # noqa: E402,F401
from app.models import etl_task as _etl_task  # noqa: E402,F401
from app.models import forecast as _forecast  # noqa: E402,F401
from app.models import identity as _identity  # noqa: E402,F401
from app.models import source_connection as _source_connection  # noqa: E402,F401
from app.models.anomaly import AnomalyConfig, AnomalyRun  # noqa: E402
from app.models.etl_task import EtlTask  # noqa: E402
from app.models.scheduled_task import ScheduledTask  # noqa: E402
from app.services.anomaly_scheduler import (  # noqa: E402
    DETECTION_SCHEDULE_TYPE,
    SLA_SCHEDULE_TYPE,
    queue_detection_tasks,
    run_sla_job,
)


def main() -> int:
    from app.database import SyncSession

    failures: list[str] = []
    session = SyncSession()
    try:
        schedules = session.execute(
            select(ScheduledTask).where(
                ScheduledTask.schedule_type.in_((DETECTION_SCHEDULE_TYPE, SLA_SCHEDULE_TYPE))
            )
        ).scalars().all()
        by_type = {row.schedule_type: row for row in schedules}
        print("agendamentos encontrados:")
        for schedule in schedules:
            print(
                f"   id={schedule.id} tipo={schedule.schedule_type} ativo={schedule.active} "
                f"cron={schedule.cron_expression!r} config={schedule.config_json}"
            )
        if DETECTION_SCHEDULE_TYPE not in by_type:
            failures.append("agendamento anomaly_detection ausente")
        elif by_type[DETECTION_SCHEDULE_TYPE].active:
            failures.append("a detecção deveria nascer inativa (piloto manual primeiro)")
        if SLA_SCHEDULE_TYPE not in by_type:
            failures.append("agendamento anomaly_sla ausente")
        elif not by_type[SLA_SCHEDULE_TYPE].active:
            failures.append("o monitor de prazos deveria nascer ativo")

        # --- monitor de prazos roda de verdade -----------------------------
        if SLA_SCHEDULE_TYPE in by_type:
            result = run_sla_job(by_type[SLA_SCHEDULE_TYPE], session)
            print(f"monitor de prazos: {result}")
            if result.get("error"):
                failures.append(f"monitor de prazos falhou: {result}")

        # --- enfileiramento (revertido no final) ---------------------------
        if DETECTION_SCHEDULE_TYPE in by_type:
            config = session.execute(select(AnomalyConfig).order_by(AnomalyConfig.id).limit(1)).scalar_one()
            original_enabled = config.enabled
            runs_before = int(session.execute(select(func.count()).select_from(AnomalyRun)).scalar_one())

            # Desabilitada não enfileira.
            config.enabled = False
            session.flush()
            blocked = queue_detection_tasks(by_type[DETECTION_SCHEDULE_TYPE], session)
            print(f"configuração desabilitada → tarefas={blocked}")
            if blocked:
                failures.append("configuração desabilitada não deveria enfileirar")

            # Habilitada enfileira uma tarefa e uma execução SCHEDULED.
            config.enabled = True
            session.flush()
            queued = queue_detection_tasks(by_type[DETECTION_SCHEDULE_TYPE], session)
            print(f"configuração habilitada → tarefas={queued}")
            if not queued:
                failures.append("nenhuma tarefa enfileirada com configuração habilitada")
            for task_id in queued:
                task = session.get(EtlTask, task_id)
                if task is None or task.task_type != "anomaly_detection":
                    failures.append(f"tarefa {task_id} não é anomaly_detection")
                    continue
                run_id = (task.result_json or {}).get("anomaly_run_id")
                run = session.get(AnomalyRun, run_id)
                print(
                    f"   tarefa {task.id}: run={run_id} trigger={getattr(run, 'trigger_type', None)} "
                    f"status={getattr(run, 'status', None)} config={getattr(run, 'anomaly_config_id', None)}"
                )
                if run is None or run.trigger_type != "SCHEDULED":
                    failures.append(f"run {run_id} não foi criada como SCHEDULED")
                if run is not None and run.source_etl_task_id != task.id:
                    failures.append(f"run {run_id} não aponta para a tarefa {task.id}")

            # Segunda chamada não duplica execução em andamento.
            again = queue_detection_tasks(by_type[DETECTION_SCHEDULE_TYPE], session)
            print(f"segunda chamada (não deve duplicar) → tarefas={again}")
            if again:
                failures.append("a segunda chamada deveria pular a configuração em andamento")

            # Seleção explícita inexistente não enfileira nada.
            from types import SimpleNamespace

            selecionada = queue_detection_tasks(
                SimpleNamespace(
                    id=by_type[DETECTION_SCHEDULE_TYPE].id,
                    name=by_type[DETECTION_SCHEDULE_TYPE].name,
                    config_json={"anomaly_config_ids": [config.id + 100000]},
                ),
                session,
            )
            print(f"seleção inexistente → tarefas={selecionada}")
            if selecionada:
                failures.append("seleção com id inexistente não deveria enfileirar")

            runs_after = int(session.execute(select(func.count()).select_from(AnomalyRun)).scalar_one())
            print(f"runs antes={runs_before} depois={runs_after} (tudo será revertido)")
            config.enabled = original_enabled
            session.flush()
    finally:
        session.rollback()
        session.close()

    if failures:
        print("\nFALHAS:")
        for failure in failures:
            print(" -", failure)
        return 1
    print("\nOK: agendamentos de anomalia verificados em ambiente real (rollback).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
