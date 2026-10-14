"""Agendamento da gestão de anomalias (F1).

Dois tipos de agendamento próprio, ambos editáveis pelo usuário:

* ``anomaly_detection`` — enfileira `task_type='anomaly_detection'` para cada
  configuração **habilitada**, respeitando "uma execução por configuração"
  (não enfileira se já existe execução `QUEUED`/`RUNNING`) e a trava global de
  escrita analítica do worker.
* ``anomaly_sla`` — monitora prazos de ciência: emite `DUE_SOON` para prazos a
  vencer e `OVERDUE` (com cópia na fila FinOps) para prazos vencidos.

A detecção nasce **inativa** (o plano exige piloto manual antes de automatizar).
O monitor de prazos nasce **ativo**: ele só produz notificação interna e é
requisito do §3.3 ("vencimento aparece no dashboard e gera notificação interna
à fila FinOps"), sem escrever dado nem tocar a cloud.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

DETECTION_SCHEDULE_TYPE = "anomaly_detection"
SLA_SCHEDULE_TYPE = "anomaly_sla"

ANOMALY_SCHEDULE_TYPES = (DETECTION_SCHEDULE_TYPE, SLA_SCHEDULE_TYPE)

#: Horas de antecedência do aviso de prazo.
DEFAULT_DUE_SOON_HOURS = 4


def _config_ids_from(schedule_row: Any) -> list[int]:
    payload = schedule_row.config_json if isinstance(schedule_row.config_json, dict) else {}
    raw = payload.get("anomaly_config_ids") or []
    ids: list[int] = []
    for value in raw:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return ids


def queue_detection_tasks(schedule_row: Any, session) -> list[int]:
    """Enfileira uma detecção por configuração habilitada.

    Nunca duplica execução em andamento: se a configuração já tem `anomaly_run`
    em `QUEUED`/`RUNNING`, ela é pulada e o motivo é registrado.
    """
    from sqlalchemy import select

    from app.models.anomaly import AnomalyConfig, AnomalyRun
    from app.models.etl_task import EtlTask

    selected = _config_ids_from(schedule_row)
    query = select(AnomalyConfig).where(AnomalyConfig.enabled.is_(True))
    if selected:
        query = query.where(AnomalyConfig.id.in_(selected))
    configs = session.execute(query.order_by(AnomalyConfig.id)).scalars().all()

    if not configs:
        logger.info(
            "Agendamento de anomalias [%s]: nenhuma configuração habilitada", schedule_row.name
        )
        return []

    queued: list[int] = []
    for config in configs:
        in_flight = session.execute(
            select(AnomalyRun.id)
            .where(
                AnomalyRun.anomaly_config_id == config.id,
                AnomalyRun.status.in_(("QUEUED", "RUNNING")),
            )
            .limit(1)
        ).scalar_one_or_none()
        if in_flight is not None:
            logger.info(
                "Agendamento de anomalias [%s]: configuração %s já tem execução %s em andamento",
                schedule_row.name, config.id, in_flight,
            )
            continue

        run = AnomalyRun(
            anomaly_config_id=config.id,
            config_version=config.version,
            trigger_type="SCHEDULED",
            status="QUEUED",
            phase="Na fila (agendado)",
            config_snapshot={
                "config_id": config.id,
                "config_version": config.version,
                "profile": config.profile,
                "daily_quantile": config.daily_quantile,
                "min_relative_deviation": config.min_relative_deviation,
                "investigate_score": config.investigate_score,
                "materiality_by_currency": config.materiality_by_currency,
                "currency": config.currency,
                "measure_column": config.measure_column,
                "dimensions": list(config.dimensions or []),
                "family_keys": list(config.family_keys or []),
                "horizon_days": config.horizon_days,
                "calibration_days": config.calibration_days,
                "history_days": config.history_days,
            },
        )
        session.add(run)
        session.flush()
        task = EtlTask(
            destination_table_id=config.id,
            task_type="anomaly_detection",
            status="QUEUED",
            phase=f"Agendado: {schedule_row.name}",
            result_json={"anomaly_run_id": run.id, "schedule_id": schedule_row.id},
        )
        session.add(task)
        session.flush()
        run.source_etl_task_id = task.id
        session.flush()
        queued.append(task.id)

    logger.info("Agendamento de anomalias [%s]: tarefas %s", schedule_row.name, queued)
    return queued


def run_sla_job(schedule_row: Any, session) -> dict[str, int]:
    """Emite avisos de prazo (a vencer e vencidos)."""
    from app.database import run_async_isolated

    payload = schedule_row.config_json if isinstance(schedule_row.config_json, dict) else {}
    hours = int(payload.get("due_soon_hours") or DEFAULT_DUE_SOON_HOURS)

    async def _run() -> dict[str, int]:
        from app.database import async_session
        from app.services import anomaly_workflow as workflow

        async with async_session() as db:
            due_soon = await workflow.notify_due_soon(db, horizon_hours=hours)
            overdue = await workflow.expire_overdue_notifications(db)
            await db.commit()
            return {"due_soon": due_soon, "overdue": overdue}

    try:
        result = run_async_isolated(_run())
    except Exception as exc:  # agendamento nunca derruba o scheduler
        logger.error("Monitor de prazos de anomalia falhou: %s", exc)
        return {"due_soon": 0, "overdue": 0, "error": 1}
    logger.info(
        "Monitor de prazos de anomalia [%s]: %s aviso(s) a vencer, %s vencido(s)",
        schedule_row.name, result.get("due_soon"), result.get("overdue"),
    )
    return result


def builtin_schedules() -> list[dict[str, Any]]:
    """Agendamentos padrão da feature (idempotentes, editáveis)."""
    return [
        {
            "name": "Detecção de anomalias",
            "cron": "0 6 * * *",
            "active": False,
            "schedule_type": DETECTION_SCHEDULE_TYPE,
            "config": {"anomaly_config_ids": []},
        },
        {
            "name": "Prazos de anomalias (SLA)",
            "cron": "*/30 * * * *",
            "active": True,
            "schedule_type": SLA_SCHEDULE_TYPE,
            "config": {"due_soon_hours": DEFAULT_DUE_SOON_HOURS},
        },
    ]


def next_due_soon_window(hours: int, *, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Janela de aviso: `[agora, agora + horas]` para prazos pendentes."""
    reference = now or datetime.now(timezone.utc)
    return reference, reference + timedelta(hours=max(1, int(hours)))
