"""Testes do agendamento de anomalias (F1)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import anomaly as anomaly_models  # noqa: F401
from app.models import destination as _destination  # noqa: F401
from app.models import etl_task as _etl_task  # noqa: F401
from app.models import forecast as _forecast  # noqa: F401
from app.models import identity as _identity  # noqa: F401
from app.models import source_connection as _source_connection  # noqa: F401
from app.models.anomaly import AnomalyConfig, AnomalyRun
from app.models.etl_task import EtlTask
from app.services.anomaly_scheduler import (
    ANOMALY_SCHEDULE_TYPES,
    DEFAULT_DUE_SOON_HOURS,
    DETECTION_SCHEDULE_TYPE,
    SLA_SCHEDULE_TYPE,
    _config_ids_from,
    builtin_schedules,
    next_due_soon_window,
    queue_detection_tasks,
)

TABLES = ("anomaly_config", "anomaly_run", "etl_task")


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Base.metadata.tables[name] for name in TABLES])
    factory = sessionmaker(bind=engine)
    db = factory()
    try:
        yield db
    finally:
        db.close()


def schedule(**overrides):
    base = {
        "id": 5,
        "name": "Detecção de anomalias",
        "config_json": {"anomaly_config_ids": []},
        "schedule_type": DETECTION_SCHEDULE_TYPE,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def config(db, *, enabled: bool, name: str = "cfg") -> AnomalyConfig:
    row = AnomalyConfig(
        name=name,
        destination_physical_name="dest_billing_cloud",
        date_column="chargeperiodstart",
        enabled=enabled,
        version=3,
        dimensions=["cloud_origem"],
        family_keys=["cloud_origem"],
    )
    db.add(row)
    db.commit()
    return row


# ---------------------------------------------------------------------------
# Configuração dos agendamentos
# ---------------------------------------------------------------------------
def test_tipos_de_agendamento_registrados():
    assert DETECTION_SCHEDULE_TYPE == "anomaly_detection"
    assert SLA_SCHEDULE_TYPE == "anomaly_sla"
    assert set(ANOMALY_SCHEDULE_TYPES) == {"anomaly_detection", "anomaly_sla"}


def test_agendamentos_padrao():
    schedules = {item["schedule_type"]: item for item in builtin_schedules()}
    # Detecção não tem agendamento próprio: dispara pelo pipeline de coleta.
    assert DETECTION_SCHEDULE_TYPE not in schedules
    # Monitor de prazos nasce ativo: só emite notificação interna.
    assert schedules[SLA_SCHEDULE_TYPE]["active"] is True
    assert schedules[SLA_SCHEDULE_TYPE]["config"]["due_soon_hours"] == DEFAULT_DUE_SOON_HOURS


def test_config_ids_aceita_lista_e_ignora_lixo():
    assert _config_ids_from(schedule(config_json={"anomaly_config_ids": [11, "12", None, "x"]})) == [11, 12]
    assert _config_ids_from(schedule(config_json={})) == []
    assert _config_ids_from(SimpleNamespace(config_json=None)) == []


def test_janela_de_aviso_de_prazo():
    now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    start, end = next_due_soon_window(4, now=now)
    assert start == now
    assert end == now + timedelta(hours=4)
    # Mínimo de 1 hora.
    _, end_zero = next_due_soon_window(0, now=now)
    assert end_zero == now + timedelta(hours=1)


# ---------------------------------------------------------------------------
# Enfileiramento
# ---------------------------------------------------------------------------
def test_nao_enfileira_configuracao_desabilitada(session):
    config(session, enabled=False)
    assert queue_detection_tasks(schedule(), session) == []


def test_enfileira_configuracao_habilitada(session):
    row = config(session, enabled=True)
    queued = queue_detection_tasks(schedule(), session)
    assert len(queued) == 1

    task = session.get(EtlTask, queued[0])
    assert task.task_type == "anomaly_detection"
    assert task.status == "QUEUED"
    assert task.destination_table_id == row.id
    assert task.result_json["schedule_id"] == 5

    run = session.get(AnomalyRun, task.result_json["anomaly_run_id"])
    assert run.anomaly_config_id == row.id
    assert run.trigger_type == "SCHEDULED"
    assert run.source_etl_task_id == task.id
    assert run.config_snapshot["config_version"] == 3


def test_nao_duplica_execucao_em_andamento(session):
    row = config(session, enabled=True)
    session.add(AnomalyRun(anomaly_config_id=row.id, status="RUNNING", trigger_type="MANUAL"))
    session.commit()
    assert queue_detection_tasks(schedule(), session) == []


def test_permite_nova_execucao_apos_concluir(session):
    row = config(session, enabled=True)
    session.add(AnomalyRun(anomaly_config_id=row.id, status="COMPLETED", trigger_type="MANUAL"))
    session.commit()
    assert len(queue_detection_tasks(schedule(), session)) == 1


def test_respeita_selecao_de_configuracoes(session):
    primeira = config(session, enabled=True, name="a")
    config(session, enabled=True, name="b")
    queued = queue_detection_tasks(schedule(config_json={"anomaly_config_ids": [primeira.id]}), session)
    assert len(queued) == 1
    task = session.get(EtlTask, queued[0])
    assert task.destination_table_id == primeira.id


def test_uma_tarefa_por_configuracao_habilitada(session):
    config(session, enabled=True, name="a")
    config(session, enabled=True, name="b")
    config(session, enabled=False, name="c")
    assert len(queue_detection_tasks(schedule(), session)) == 2


def test_sem_configuracao_habilitada_nao_falha(session):
    config(session, enabled=False)
    assert queue_detection_tasks(schedule(), session) == []
