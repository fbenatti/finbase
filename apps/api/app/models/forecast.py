from sqlalchemy import Column, Integer, String, DateTime, JSON, Boolean, ForeignKey, Float, Date, func, Index
from app.database import Base

class ForecastConfig(Base):
    __tablename__ = "forecast_config"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    description = Column(String(1000), nullable=True)
    destination_table_id = Column(Integer, ForeignKey("destination_table.id", ondelete="CASCADE"), nullable=False, index=True)
    measure_column = Column(String(200), nullable=False)
    currency_column = Column(String(200), nullable=True)
    date_column = Column(String(200), nullable=False)
    period_column = Column(String(200), nullable=True)
    type_column = Column(String(200), nullable=True, default="tipo")
    dimensions = Column(JSON, nullable=False, default=list)
    cutoff_days = Column(Integer, nullable=False, default=2)
    horizon_months = Column(Integer, nullable=False, default=12)
    growth_rate = Column(Float, nullable=False, default=0.0083)
    model_mode = Column(String(30), nullable=False, default="AUTO_BACKTEST")
    enabled_windows = Column(JSON, nullable=False, default=lambda: [7, 14, 28])
    recent_weight = Column(Float, nullable=False, default=0.7)
    minimum_daily_points = Column(Integer, nullable=False, default=7)
    minimum_monthly_points = Column(Integer, nullable=False, default=3)
    null_dimension_label = Column(String(100), nullable=False, default="Não classificado")
    period_allocation = Column(String(30), nullable=False, default="none")
    charge_end_column = Column(String(200), nullable=True)
    connection_allocation_rules = Column(JSON, nullable=False, default=list)
    active = Column(Boolean, nullable=False, default=True)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class ForecastAdjustment(Base):
    __tablename__ = "forecast_adjustment"
    id = Column(Integer, primary_key=True, autoincrement=True)
    forecast_config_id = Column(Integer, ForeignKey("forecast_config.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    filters = Column(JSON, nullable=False, default=dict)
    adjustment_type = Column(String(30), nullable=False)
    percentage = Column(Float, nullable=True)
    fixed_value = Column(Float, nullable=True)
    start_period = Column(String(6), nullable=True)
    end_period = Column(String(6), nullable=True)
    priority = Column(Integer, nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class ForecastBusinessRule(Base):
    """Versioned, metadata-driven rule used after the mathematical forecast."""
    __tablename__ = "forecast_business_rule"
    id = Column(Integer, primary_key=True, autoincrement=True)
    forecast_config_id = Column(Integer, ForeignKey("forecast_config.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    identification_column = Column(String(200), nullable=False, default="")
    identification_values = Column(JSON, nullable=False, default=list)
    identification_clauses = Column(JSON, nullable=False, default=list)
    charge_type = Column(String(40), nullable=False, default="Usage")
    behavior = Column(String(40), nullable=False, default="RECURRING_MONTHLY")
    behavior_options = Column(JSON, nullable=False, default=dict)
    reference_column = Column(String(200), nullable=True)
    reference_values = Column(JSON, nullable=False, default=list)
    value_column = Column(String(200), nullable=True)
    start_period = Column(String(6), nullable=False)
    end_period = Column(String(6), nullable=True)
    growth_rate = Column(Float, nullable=False, default=0.0)
    replacement_value = Column(Float, nullable=True)
    cancel_on_refund = Column(Boolean, nullable=False, default=False)
    scope = Column(JSON, nullable=False, default=dict)
    priority = Column(Integer, nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class ForecastRun(Base):
    __tablename__ = "forecast_run"
    id = Column(Integer, primary_key=True, autoincrement=True)
    # Runs are audit records.  The configuration may be deleted after a run
    # has completed, therefore the relationship must never cascade-delete it.
    forecast_config_id = Column(Integer, ForeignKey("forecast_config.id", ondelete="SET NULL"), nullable=True, index=True)
    config_version = Column(Integer, nullable=False, default=1)
    trigger_type = Column(String(30), nullable=False, default="MANUAL")
    source_etl_task_id = Column(Integer, ForeignKey("etl_task.id"), nullable=True)
    # Data-driven cutoff (MAX data collected − cutoff_days) computed by the
    # forecast engine and persisted after execution. NULL until the run
    # executes — never stamp today's date here, or a delayed report would make
    # "hoje" look like the calculation base of the remaining-days forecast.
    cutoff_date = Column(String(10), nullable=True)
    status = Column(String(30), nullable=False, default="QUEUED")
    phase = Column(String(200), nullable=True)
    total_series = Column(Integer, nullable=False, default=0)
    processed_series = Column(Integer, nullable=False, default=0)
    rows_written = Column(Integer, nullable=False, default=0)
    selected_models = Column(JSON, nullable=False, default=dict)
    error_metrics = Column(JSON, nullable=False, default=dict)
    result_table = Column(String(200), nullable=True)
    # Frozen contract used to generate this result.  It makes old snapshots
    # queryable even after their editable configuration no longer exists.
    config_snapshot = Column(JSON, nullable=False, default=dict)
    destination_table_id_snapshot = Column(Integer, nullable=True, index=True)
    destination_physical_name = Column(String(200), nullable=True)
    source_snapshot_id = Column(String(100), nullable=True)
    classification_version_id = Column(Integer, nullable=True, index=True)
    error = Column(String(2000), nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    __table_args__ = (Index("ix_forecast_run_config_status", "forecast_config_id", "status"),)


class ForecastAck(Base):
    """User acknowledgement of a forecast deviation: the reason why a
    department deviated from the month's simulated forecast.  Frozen metrics
    (projetado/realizado/delta/acuracia) captured at ack time."""
    __tablename__ = "forecast_ack"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, nullable=True, index=True)
    target_month = Column(String(6), nullable=False, index=True)
    department_path = Column(JSON, nullable=False, default=list)
    department_name = Column(String(300), nullable=False)
    forecast_value = Column(Float, nullable=False, default=0)
    actual_value = Column(Float, nullable=False, default=0)
    variance = Column(Float, nullable=False, default=0)
    accuracy = Column(Float, nullable=True)
    category = Column(String(80), nullable=False)
    reason = Column(String(1000), nullable=False)
    # Severidade do desvio para highlights: Informação | Perigo | Crítico | Sucesso.
    severity = Column(String(20), nullable=False, default="Informação")
    # Ordem manual do highlight (menor = aparece primeiro); default pela severidade.
    ordem = Column(Integer, nullable=False, default=0)
    registered_at = Column(Date, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (Index("ix_forecast_ack_month_dept", "target_month", "department_name"),)


class ForecastAckHistory(Base):
    """Audit trail: every previous version of an Ack (category/reason) before
    each edit, so the justificativa evolution is never lost."""
    __tablename__ = "forecast_ack_history"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ack_id = Column(Integer, ForeignKey("forecast_ack.id", ondelete="CASCADE"), nullable=False, index=True)
    category = Column(String(80), nullable=False)
    reason = Column(String(1000), nullable=False)
    changed_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_forecast_ack_history_ack", "ack_id"),)
