"""Testes de persistência da gestão de anomalias (E1).

Herméticos: SQLite em memória. Verificam o contrato de modelo (tabelas,
colunas obrigatórias, unicidade e imutabilidade), não a migração em PostgreSQL —
essa é validada no ambiente real por `scripts/verify_anomaly_schema.py`.
"""
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import anomaly as models
from app.services.anomaly_migrations import ANOMALY_TABLE_NAMES, ANOMALY_PARTIAL_INDEXES


@pytest.fixture()
def engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine, tables=[Base.metadata.tables[name] for name in ANOMALY_TABLE_NAMES]
    )
    return engine


def test_todas_as_tabelas_do_plano_existem(engine):
    inspector = inspect(engine)
    assert set(ANOMALY_TABLE_NAMES).issubset(set(inspector.get_table_names()))


def test_anomaly_case_guarda_nota_e_classificacao_sem_valores_de_serie(engine):
    columns = {c["name"] for c in inspect(engine).get_columns("anomaly_case")}
    for required in (
        "case_key", "series_id", "series_dimensions", "event_family", "event_type",
        "event_date", "period", "source_billing_period", "state", "severity", "score",
        "confidence", "observed_cloud", "expected_cloud", "lower_cloud", "upper_cloud",
        "delta_cloud", "anomalous_cost_cloud", "evaluation_grain", "current_reference_id",
        "current_assessment_id", "reference_kind", "reason_codes", "applied_rule_refs",
        "data_quality", "assignee_user_id", "routing_rule_id", "due_at",
        "acknowledged_at", "investigation_started_at", "cause_category", "decision",
        "decided_at", "remediation_started_at", "verification_started_at",
        "verification_succeeded_at", "valid_days_streak", "retrospective_at",
        "learning", "preventive_measure", "no_preventive_measure_reason",
        "closed_at", "reopen_count", "parent_case_id", "case_version",
    ):
        assert required in columns, f"anomaly_case sem a coluna {required}"

    # O caso NÃO copia séries nem grades diárias (plano §4.1).
    for forbidden in ("daily_grid", "series", "forecast_values", "reference_grid"):
        assert forbidden not in columns


def test_campos_obrigatorios_da_notificacao_existem(engine):
    columns = {c["name"] for c in inspect(engine).get_columns("anomaly_notification")}
    for required in (
        "notification_created_at" if "notification_created_at" in columns else "created_at",
        "read_at", "acknowledged_at", "recipient_user_id", "routing_rule_id",
        "dedup_key", "is_finops_queue", "kind",
    ):
        assert required in columns


def test_configuracao_inicial_e_desabilitada_e_versionada(engine):
    columns = {c["name"] for c in inspect(engine).get_columns("anomaly_config")}
    for required in (
        "forecast_config_id", "measure_column", "currency", "profile", "daily_quantile",
        "min_relative_deviation", "investigate_score", "monthly_tolerance_pct",
        "materiality_by_currency", "history_days", "calibration_days", "horizon_days",
        "routing_rules", "sla_hours", "enabled", "version",
    ):
        assert required in columns

    with Session(engine) as session:
        config = models.AnomalyConfig(name="teste", destination_physical_name="dest_modelo_focus",
                                      date_column="chargeperiodstart")
        session.add(config)
        session.commit()
        assert config.id is not None
        assert config.enabled is False
        assert config.measure_column == "realizado_cloud"
        assert config.currency == "BRL"
        assert config.materiality_by_currency == {"BRL": 100.0}


def test_publicacao_corrente_e_unica_por_configuracao(engine):
    with Session(engine) as session:
        config = models.AnomalyConfig(name="c", destination_physical_name="t", date_column="d")
        session.add(config)
        session.commit()

        session.add(models.AnomalyPublication(
            anomaly_config_id=config.id, reference_table="r", assessment_table="a", is_current=True))
        session.commit()

        session.add(models.AnomalyPublication(
            anomaly_config_id=config.id, reference_table="r2", assessment_table="a2", is_current=True))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        # Publicações históricas (is_current falso) continuam permitidas.
        session.add(models.AnomalyPublication(
            anomaly_config_id=config.id, reference_table="r3", assessment_table="a3", is_current=False))
        session.commit()


def test_notificacao_deduplica_por_caso_evento_destinatario(engine):
    with Session(engine) as session:
        case = models.AnomalyCase(
            case_key="k1", series_id="s1", event_family="f", event_type="DAILY_CONSUMPTION",
            event_date="2026-10-10", period="202610",
        )
        session.add(case)
        session.commit()

        session.add(models.AnomalyNotification(
            case_id=case.id, kind="ASSIGNMENT", dedup_key="case:1:ASSIGNMENT:user:5",
            title="t", recipient_user_id=None, is_finops_queue=True))
        session.commit()

        session.add(models.AnomalyNotification(
            case_id=case.id, kind="ASSIGNMENT", dedup_key="case:1:ASSIGNMENT:user:5",
            title="t2", recipient_user_id=None, is_finops_queue=True))
        with pytest.raises(IntegrityError):
            session.commit()


def test_historico_de_eventos_e_imutavel(engine):
    with Session(engine) as session:
        case = models.AnomalyCase(
            case_key="k2", series_id="s2", event_family="f", event_type="DAILY_CONSUMPTION",
            event_date="2026-10-10", period="202610",
        )
        session.add(case)
        session.commit()

        event = models.AnomalyCaseEvent(
            case_id=case.id, event_type="STATE_TRANSITION", command="assign",
            from_state="NEW", to_state="ASSIGNED", case_version=2,
        )
        session.add(event)
        session.commit()
        event_id = event.id

        # UPDATE via ORM é bloqueado.
        event.note = "alterado"
        with pytest.raises(IntegrityError, match="imutável"):
            session.commit()
        session.rollback()

        # DELETE via ORM é bloqueado.
        loaded = session.get(models.AnomalyCaseEvent, event_id)
        session.delete(loaded)
        with pytest.raises(IntegrityError, match="imutável"):
            session.commit()
        session.rollback()

        # O evento original permanece intacto.
        fresh = session.get(models.AnomalyCaseEvent, event_id)
        assert fresh is not None
        assert fresh.note is None


def test_case_key_e_unica(engine):
    with Session(engine) as session:
        session.add(models.AnomalyCase(
            case_key="dup", series_id="s", event_family="f", event_type="DAILY_CONSUMPTION",
            event_date="2026-10-10", period="202610"))
        session.commit()
        session.add(models.AnomalyCase(
            case_key="dup", series_id="s2", event_family="f2", event_type="DAILY_CONSUMPTION",
            event_date="2026-10-11", period="202610"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_indice_parcial_da_publicacao_corrente_esta_declarado():
    # O índice também é recriado pela migração idempotente em PostgreSQL.
    assert any("uq_anomaly_publication_current" in stmt for stmt in ANOMALY_PARTIAL_INDEXES)
    declared = {
        index.name
        for table in Base.metadata.tables.values()
        for index in table.indexes
    }
    assert "uq_anomaly_publication_current" in declared


def test_caso_aponta_para_avaliacao_e_referencia_atuais(engine):
    """O caso referencia a avaliação atual sem duplicar o financeiro."""
    with Session(engine) as session:
        case = models.AnomalyCase(
            case_key="k3", series_id="s3", event_family="f", event_type="MONTHLY_EVENT",
            event_date="2026-10-01", period="202610", current_assessment_id="a-1",
            current_reference_id="r-1", reference_kind="PROSPECTIVE",
        )
        session.add(case)
        session.commit()
        assert case.current_assessment_id == "a-1"
        assert case.reference_kind == "PROSPECTIVE"
        assert case.reason_codes == []
        assert case.applied_rule_refs == []


def test_acoes_e_historico_sobrevivem_a_configuracao_removida(engine):
    """Aceite E1: o workflow histórico não depende da configuração editável."""
    with Session(engine) as session:
        config = models.AnomalyConfig(name="c", destination_physical_name="t", date_column="d")
        session.add(config)
        session.commit()
        config_id = config.id

        case = models.AnomalyCase(
            case_key="k4", anomaly_config_id=config_id, series_id="s4",
            event_family="f", event_type="DAILY_CONSUMPTION", event_date="2026-10-10",
            period="202610",
        )
        session.add(case)
        session.commit()
        case_id = case.id
        session.add(models.AnomalyCaseEvent(case_id=case_id, event_type="CREATED", case_version=1))
        session.commit()

        session.execute(text("UPDATE anomaly_case SET anomaly_config_id = NULL WHERE id = :id"), {"id": case_id})
        session.execute(text("DELETE FROM anomaly_config WHERE id = :id"), {"id": config_id})
        session.commit()

        assert session.get(models.AnomalyCase, case_id) is not None
        assert session.query(models.AnomalyCaseEvent).filter_by(case_id=case_id).count() == 1


def test_execucao_guarda_o_contrato_de_referencia(engine):
    columns = {c["name"] for c in inspect(engine).get_columns("anomaly_run")}
    for required in (
        "forecast_run_id", "forecast_config_version", "source_snapshot_id", "data_cutoff",
        "generated_at", "target_start", "target_end", "reference_kind", "config_snapshot",
        "reference_table", "assessment_table", "gold_table", "evaluation_start", "evaluation_end",
    ):
        assert required in columns, f"anomaly_run sem a coluna {required}"
