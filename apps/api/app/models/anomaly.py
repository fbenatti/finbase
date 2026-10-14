"""Modelos da gestão de anomalias de custo cloud.

Separação de responsabilidades (plano §4.1):

* PostgreSQL guarda **controle e workflow** — configuração versionada, execuções,
  publicação corrente, casos, histórico imutável, ações e notificações.
* Valores financeiros e séries ficam no Iceberg
  (`silver.dest_modelo_focus_anomaly_reference`, `silver.dest_modelo_focus_anomalies`,
  `gold.dest_dm_cost_anomalies`).

`anomaly_case` pode guardar nota, classificação e os três valores escalares de
referência (observado/esperado/limite) para busca e priorização. Ele **não**
copia séries nem grades diárias.
"""
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    event,
    func,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base

# Estados do workflow (plano §3.1). A ordem do ciclo é:
# NEW -> ASSIGNED -> INVESTIGATING -> DECIDED -> IN_REMEDIATION -> VERIFYING
#     -> RETROSPECTIVE -> CLOSED
CASE_STATES = (
    "NEW",
    "ASSIGNED",
    "INVESTIGATING",
    "DECIDED",
    "IN_REMEDIATION",
    "VERIFYING",
    "RETROSPECTIVE",
    "CLOSED",
)

# Classificação do detector, qualidade dos dados, confirmação humana e
# evolução financeira são dimensões SEPARADAS do estado (plano §3.1).
CASE_DECISIONS = (
    "EXPECTED",
    "DATA_ISSUE",
    "CONFIRMED_ACTION",
    "CONFIRMED_ACCEPTED",
    "LOW_PRIORITY_NO_ACTION",
)

ACTION_STATUS = ("PLANNED", "IN_PROGRESS", "COMPLETED", "CANCELLED")


class AnomalyConfig(Base):
    """Configuração versionada da detecção, roteamento e prazos.

    Todas as colunas NOT NULL que têm default carregam também ``server_default``,
    para que a configuração inicial possa ser semeada por SQL direto (a migração
    idempotente no startup) sem depender dos defaults do ORM.
    """

    __tablename__ = "anomaly_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    description = Column(String(1000), nullable=True)
    # Uma configuração por execução (plano §6). Nunca cascade-delete: as runs
    # são snapshots de auditoria e sobrevivem à configuração.
    forecast_config_id = Column(
        Integer, ForeignKey("forecast_config.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Medida observada e esperada. Fixa em `realizado_cloud`: o plano proíbe
    # substituí-la por outra medida quando estiver ausente.
    measure_column = Column(
        String(200), nullable=False, default="realizado_cloud", server_default="realizado_cloud"
    )
    # Moeda EXPLÍCITA da materialidade. Nunca inferida de `BillingCurrency`.
    currency = Column(String(10), nullable=False, default="BRL", server_default="BRL")
    # Origem congelada: mesma projeção de colunas que o forecast lê.
    source_namespace = Column(String(50), nullable=False, default="silver", server_default="silver")
    destination_physical_name = Column(String(200), nullable=False)
    date_column = Column(String(200), nullable=False)
    period_column = Column(String(200), nullable=True)
    type_column = Column(String(200), nullable=True, default="tipo", server_default="tipo")
    dimensions = Column(JSON, nullable=False, default=list, server_default=text("'[]'"))
    # Dimensões que definem "o mesmo gasto" ao consolidar contribuintes.
    family_keys = Column(JSON, nullable=False, default=list, server_default=text("'[]'"))
    # Colunas de contexto financeiro, apenas como procedência.
    connection_column = Column(String(200), nullable=True, default="cloud_origem")
    connection_id_column = Column(String(200), nullable=True, default="source_connection_id")

    # --- Perfil de detecção (plano §2.6) ---
    profile = Column(String(20), nullable=False, default="BALANCED", server_default="BALANCED")
    daily_quantile = Column(Float, nullable=False, default=0.975, server_default=text("0.975"))
    min_relative_deviation = Column(Float, nullable=False, default=0.20, server_default=text("0.20"))
    investigate_score = Column(Integer, nullable=False, default=55, server_default=text("55"))
    # Poucos eventos mensais não sustentam confiança de 99%: tolerância de
    # valor explícita, com confiança limitada (plano §2.6).
    monthly_tolerance_pct = Column(Float, nullable=False, default=0.15, server_default=text("0.15"))
    monthly_min_events = Column(Integer, nullable=False, default=3, server_default=text("3"))
    # Materialidade por moeda: {"BRL": 100.0}
    materiality_by_currency = Column(
        JSON,
        nullable=False,
        default=lambda: {"BRL": 100.0},
        server_default=text("""'{"BRL": 100.0}'"""),
    )

    # --- Histórico e janelas (plano §2.6, §6) ---
    history_days = Column(Integer, nullable=False, default=180, server_default=text("180"))
    calibration_days = Column(Integer, nullable=False, default=60, server_default=text("60"))
    horizon_days = Column(Integer, nullable=False, default=15, server_default=text("15"))
    # Atraso operacional da fonte: 2 mantém o padrão atual D-2.
    data_lag_days = Column(Integer, nullable=False, default=2, server_default=text("2"))
    first_run_days = Column(Integer, nullable=False, default=30, server_default=text("30"))
    reevaluation_days = Column(Integer, nullable=False, default=7, server_default=text("7"))
    bucket_days = Column(Integer, nullable=False, default=1, server_default=text("1"))

    # --- Roteamento e prazos (plano §3.3) ---
    routing_rules = Column(JSON, nullable=False, default=list, server_default=text("'[]'"))
    sla_hours = Column(
        JSON,
        nullable=False,
        default=lambda: {"CRITICAL": 4, "HIGH": 24, "DEFAULT": 72},
        server_default=text("""'{"CRITICAL": 4, "HIGH": 24, "DEFAULT": 72}'"""),
    )
    notification_enabled = Column(Boolean, nullable=False, default=True, server_default=text("true"))

    # Configuração inicial nasce DESABILITADA (E1).
    enabled = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    version = Column(Integer, nullable=False, default=1, server_default=text("1"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AnomalyRun(Base):
    """Execução da detecção com a configuração congelada."""

    __tablename__ = "anomaly_run"

    id = Column(Integer, primary_key=True, autoincrement=True)
    anomaly_config_id = Column(
        Integer, ForeignKey("anomaly_config.id", ondelete="SET NULL"), nullable=True, index=True
    )
    config_version = Column(Integer, nullable=False, default=1)
    trigger_type = Column(String(30), nullable=False, default="MANUAL")
    source_etl_task_id = Column(Integer, ForeignKey("etl_task.id"), nullable=True)
    status = Column(String(30), nullable=False, default="QUEUED")
    phase = Column(String(200), nullable=True)

    # --- Contrato de referência (plano §2.2) ---
    forecast_run_id = Column(Integer, nullable=True, index=True)
    forecast_config_version = Column(Integer, nullable=True)
    source_snapshot_id = Column(String(100), nullable=True)
    data_cutoff = Column(String(10), nullable=True)
    generated_at = Column(DateTime(timezone=True), nullable=True)
    target_start = Column(String(10), nullable=True)
    target_end = Column(String(10), nullable=True)
    reference_kind = Column(String(30), nullable=True)

    # Janela efetivamente avaliada.
    evaluation_start = Column(String(10), nullable=True)
    evaluation_end = Column(String(10), nullable=True)

    reference_table = Column(String(200), nullable=True)
    assessment_table = Column(String(200), nullable=True)
    gold_table = Column(String(200), nullable=True)

    total_series = Column(Integer, nullable=False, default=0)
    processed_series = Column(Integer, nullable=False, default=0)
    rows_written = Column(Integer, nullable=False, default=0)
    cases_created = Column(Integer, nullable=False, default=0)
    cases_updated = Column(Integer, nullable=False, default=0)
    notifications_created = Column(Integer, nullable=False, default=0)

    # Congelado no início da execução: a configuração editável pode mudar ou
    # desaparecer sem afetar a auditoria (§4.1).
    config_snapshot = Column(JSON, nullable=False, default=dict)
    selected_models = Column(JSON, nullable=False, default=dict)
    errors = Column(JSON, nullable=False, default=list)
    error = Column(String(2000), nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_anomaly_run_config_status", "anomaly_config_id", "status"),)


class AnomalyPublication(Base):
    """Publicação válida corrente por configuração.

    Falha preserva a publicação anterior (§6): uma publicação só se torna
    corrente depois de reconciliar e validar a unicidade.
    """

    __tablename__ = "anomaly_publication"

    id = Column(Integer, primary_key=True, autoincrement=True)
    anomaly_config_id = Column(
        Integer, ForeignKey("anomaly_config.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id = Column(Integer, ForeignKey("anomaly_run.id", ondelete="SET NULL"), nullable=True)
    previous_run_id = Column(Integer, nullable=True)
    reference_table = Column(String(200), nullable=False)
    assessment_table = Column(String(200), nullable=False)
    gold_table = Column(String(200), nullable=True)
    data_cutoff = Column(String(10), nullable=True)
    generated_at = Column(DateTime(timezone=True), nullable=True)
    row_count = Column(Integer, nullable=False, default=0)
    series_count = Column(Integer, nullable=False, default=0)
    reference_count = Column(Integer, nullable=False, default=0)
    is_current = Column(Boolean, nullable=False, default=True)
    published_at = Column(DateTime(timezone=True), server_default=func.now())
    published_by_id = Column(Integer, ForeignKey("user_account.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        Index("ix_anomaly_publication_config_current", "anomaly_config_id", "is_current"),
        # Só uma publicação corrente por configuração, garantido no banco.
        Index(
            "uq_anomaly_publication_current",
            "anomaly_config_id",
            unique=True,
            postgresql_where=text("is_current"),
            sqlite_where=text("is_current = 1"),
        ),
    )


class AnomalyCase(Base):
    """Caso operacional: um por episódio, não um por dia (§3.2)."""

    __tablename__ = "anomaly_case"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Identidade determinística do episódio: série + família de evento.
    # Reprocessamento encontra este registro em vez de criar outro.
    case_key = Column(String(300), nullable=False, index=True)
    anomaly_config_id = Column(
        Integer, ForeignKey("anomaly_config.id", ondelete="SET NULL"), nullable=True, index=True
    )
    series_id = Column(String(120), nullable=False, index=True)
    series_dimensions = Column(JSON, nullable=False, default=dict)
    event_family = Column(String(200), nullable=False)
    # DAILY_CONSUMPTION | MONTHLY_EVENT | MONTHLY_ACCUMULATED
    event_type = Column(String(30), nullable=False)
    event_date = Column(String(10), nullable=False, index=True)
    # Mês civil do evento, para agrupamento analítico (§4.3).
    period = Column(String(6), nullable=False, index=True)
    # Competência original, apenas procedência. Não influencia a detecção.
    source_billing_period = Column(String(6), nullable=True)

    first_detected_at = Column(DateTime(timezone=True), server_default=func.now())
    last_evaluated_at = Column(DateTime(timezone=True), nullable=True)

    state = Column(String(30), nullable=False, default="NEW", index=True)
    state_changed_at = Column(DateTime(timezone=True), server_default=func.now())
    severity = Column(String(20), nullable=False, default="LOW", index=True)
    score = Column(Float, nullable=False, default=0.0)
    confidence = Column(String(20), nullable=False, default="UNAVAILABLE")

    # Escalares de referência para priorização; as séries ficam no Iceberg.
    observed_cloud = Column(Float, nullable=True)
    expected_cloud = Column(Float, nullable=True)
    lower_cloud = Column(Float, nullable=True)
    upper_cloud = Column(Float, nullable=True)
    delta_cloud = Column(Float, nullable=True)
    anomalous_cost_cloud = Column(Float, nullable=False, default=0.0)
    evaluation_grain = Column(String(30), nullable=False, default="DAY")

    current_reference_id = Column(String(120), nullable=True)
    current_assessment_id = Column(String(120), nullable=True)
    reference_kind = Column(String(30), nullable=True)
    reason_codes = Column(JSON, nullable=False, default=list)
    applied_rule_refs = Column(JSON, nullable=False, default=list)
    data_quality = Column(String(30), nullable=False, default="OK")

    # --- Atribuição (plano §3.3) ---
    assignee_user_id = Column(
        Integer, ForeignKey("user_account.id", ondelete="SET NULL"), nullable=True, index=True
    )
    routing_rule_id = Column(String(120), nullable=True)
    due_at = Column(DateTime(timezone=True), nullable=True, index=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_by_id = Column(Integer, ForeignKey("user_account.id", ondelete="SET NULL"), nullable=True)

    # --- Investigação e decisão (plano §3.4, §3.5) ---
    investigation_started_at = Column(DateTime(timezone=True), nullable=True)
    cause_category = Column(String(60), nullable=True)
    cause_hypothesis = Column(String(2000), nullable=True)
    decision = Column(String(40), nullable=True)
    decision_note = Column(String(2000), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    decided_by_id = Column(Integer, ForeignKey("user_account.id", ondelete="SET NULL"), nullable=True)

    # --- Remediação, verificação e encerramento (plano §3.6–3.8) ---
    remediation_started_at = Column(DateTime(timezone=True), nullable=True)
    verification_started_at = Column(DateTime(timezone=True), nullable=True)
    verification_reference_id = Column(String(120), nullable=True)
    verification_note = Column(String(2000), nullable=True)
    verification_succeeded_at = Column(DateTime(timezone=True), nullable=True)
    valid_days_streak = Column(Integer, nullable=False, default=0)
    retrospective_at = Column(DateTime(timezone=True), nullable=True)
    learning = Column(String(2000), nullable=True)
    preventive_measure = Column(String(2000), nullable=True)
    no_preventive_measure_reason = Column(String(2000), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    closed_by_id = Column(Integer, ForeignKey("user_account.id", ondelete="SET NULL"), nullable=True)
    reopen_count = Column(Integer, nullable=False, default=0)
    parent_case_id = Column(Integer, ForeignKey("anomaly_case.id", ondelete="SET NULL"), nullable=True)

    # Controle de concorrência otimista: toda transição exige a versão atual.
    case_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("uq_anomaly_case_key", "case_key", unique=True),
        Index("ix_anomaly_case_queue", "state", "assignee_user_id"),
        Index("ix_anomaly_case_config_period", "anomaly_config_id", "period"),
    )


class AnomalyCaseEvent(Base):
    """Histórico imutável de transições, ciência, decisões e reaberturas.

    O workflow histórico não depende da sobrevivência da configuração editável
    (aceite E1). UPDATE e DELETE são bloqueados por listener.
    """

    __tablename__ = "anomaly_case_event"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(
        Integer, ForeignKey("anomaly_case.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type = Column(String(40), nullable=False)
    command = Column(String(40), nullable=True)
    from_state = Column(String(30), nullable=True)
    to_state = Column(String(30), nullable=True)
    actor_user_id = Column(Integer, ForeignKey("user_account.id", ondelete="SET NULL"), nullable=True)
    note = Column(String(2000), nullable=True)
    payload = Column(JSON, nullable=False, default=dict)
    # Versão do caso APÓS o evento: reconstrução do histórico na ordem correta.
    case_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (Index("ix_anomaly_case_event_case_time", "case_id", "created_at"),)


class AnomalyAction(Base):
    """Ação corretiva ou preventiva vinculada ao caso (plano §3.6)."""

    __tablename__ = "anomaly_action"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(
        Integer, ForeignKey("anomaly_case.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action_type = Column(String(30), nullable=False, default="CORRECTIVE")
    description = Column(String(1000), nullable=False)
    owner_user_id = Column(Integer, ForeignKey("user_account.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(30), nullable=False, default="PLANNED")
    due_at = Column(DateTime(timezone=True), nullable=True)
    executed_at = Column(DateTime(timezone=True), nullable=True)
    evidence = Column(String(2000), nullable=True)
    external_reference = Column(String(500), nullable=True)
    # Ação obrigatória pendente impede avançar para verificação.
    mandatory = Column(Boolean, nullable=False, default=True)
    created_by_id = Column(Integer, ForeignKey("user_account.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("ix_anomaly_action_case_status", "case_id", "status"),)


class AnomalyNotification(Base):
    """Notificação interna ao OpenFinops (plano §3.3).

    `recipient_user_id` nulo com `is_finops_queue` verdadeiro representa a fila
    FinOps: caso sem responsável continua visível e monitorado.
    """

    __tablename__ = "anomaly_notification"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(
        Integer, ForeignKey("anomaly_case.id", ondelete="CASCADE"), nullable=False, index=True
    )
    anomaly_config_id = Column(Integer, nullable=True, index=True)
    recipient_user_id = Column(
        Integer, ForeignKey("user_account.id", ondelete="CASCADE"), nullable=True, index=True
    )
    is_finops_queue = Column(Boolean, nullable=False, default=False)
    kind = Column(String(40), nullable=False)
    # Deduplicação por caso + evento + destinatário: um ETL não gera nova
    # notificação para o mesmo fato.
    dedup_key = Column(String(300), nullable=False)
    title = Column(String(300), nullable=False)
    body = Column(String(2000), nullable=True)
    severity = Column(String(20), nullable=False, default="LOW")
    routing_rule_id = Column(String(120), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    read_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("uq_anomaly_notification_dedup", "dedup_key", unique=True),
        Index("ix_anomaly_notification_inbox", "recipient_user_id", "read_at"),
    )


# --------------------------------------------------------------------------
# Imutabilidade do histórico
# --------------------------------------------------------------------------
_IMMUTABLE_MESSAGE = (
    "anomaly_case_event é um histórico imutável: "
    "transições devem ser registradas como novos eventos."
)


def _block_event_mutation(*_args, **_kwargs):
    raise IntegrityError(_IMMUTABLE_MESSAGE, None, Exception(_IMMUTABLE_MESSAGE))


event.listen(AnomalyCaseEvent, "before_update", _block_event_mutation)
event.listen(AnomalyCaseEvent, "before_delete", _block_event_mutation)


def _block_event_session_flush(session: Session, flush_context, instances):  # pragma: no cover - guard
    """Bloqueia UPDATE/DELETE vindos de flush em massa.

    O listener de mapper cobre a maior parte dos casos; este guard cobre
    ``session.execute(update(...))``/``delete(...)`` que não passam por mapper.
    """
    for obj in list(session.dirty) + list(session.deleted):
        if isinstance(obj, AnomalyCaseEvent):
            raise IntegrityError(_IMMUTABLE_MESSAGE, None, Exception(_IMMUTABLE_MESSAGE))


event.listen(Session, "before_flush", _block_event_session_flush)
