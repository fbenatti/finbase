from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, Integer, String, func

from app.database import Base


class InfraOpportunityRun(Base):
    """One deterministic execution of the rightsizing engine over a frozen window."""
    __tablename__ = "infra_opportunity_run"
    id = Column(Integer, primary_key=True, autoincrement=True)
    connection_id = Column(Integer, ForeignKey("source_connection.id"), nullable=False, index=True)
    status = Column(String(30), nullable=False, default="RUNNING", index=True)
    engine_version = Column(String(60), nullable=False)
    policy_version = Column(String(60), nullable=False)
    # Saved policy revision used (NULL = DEFAULT_POLICY) and its exact values.
    policy_revision = Column(Integer)
    policy_aggressiveness = Column(String(20))
    policy_params = Column(JSON)
    window_start_utc = Column(DateTime(timezone=True), nullable=False)
    window_end_utc = Column(DateTime(timezone=True), nullable=False)
    # 60 = NATIVE 1m buckets; 300 = 5m rollups derived from 1m (max preserved).
    resolution_seconds = Column(Integer, nullable=False, default=60, server_default="60")
    resources_analyzed = Column(Integer, nullable=False, default=0)
    opportunities_count = Column(Integer, nullable=False, default=0)
    total_monthly_saving = Column(Float)
    avg_monthly_saving = Column(Float)
    currency = Column(String(10))
    readiness_counts = Column(JSON, nullable=False, default=dict)
    error_message = Column(String(2000))
    created_by = Column(Integer)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True))


class InfraRightsizingPolicy(Base):
    """Append-only revisions of the rightsizing policy; the highest version is active."""
    __tablename__ = "infra_rightsizing_policy"
    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(Integer, nullable=False, unique=True)
    aggressiveness = Column(String(20), nullable=False)
    params = Column(JSON, nullable=False)
    note = Column(String(500))
    created_by = Column(Integer)
    created_by_name = Column(String(255))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class InfraOpportunity(Base):
    """Rightsizing result for one resource in one run, with its decision state."""
    __tablename__ = "infra_opportunity"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("infra_opportunity_run.id", ondelete="CASCADE"), nullable=False, index=True)
    connection_id = Column(Integer, nullable=False, index=True)
    provider = Column(String(20), nullable=False, default="oci")
    resource_id = Column(String(255), nullable=False, index=True)
    resource_name = Column(String(255))
    action_type = Column(String(30), nullable=False, index=True)
    readiness_status = Column(String(40), nullable=False, index=True)
    state = Column(String(30), nullable=False, default="NEW", index=True)
    confidence = Column(String(10), nullable=False)
    from_config = Column(JSON)
    to_config = Column(JSON)
    current_monthly_cost = Column(Float)
    target_monthly_cost = Column(Float)
    monthly_saving = Column(Float)
    currency = Column(String(10))
    rationale = Column(JSON, nullable=False, default=list)
    risks = Column(JSON, nullable=False, default=list)
    evidence = Column(JSON, nullable=False, default=dict)
    inputs_hash = Column(String(64), nullable=False)
    engine_version = Column(String(60), nullable=False)
    state_note = Column(String(2000))
    state_changed_by = Column(Integer)
    state_changed_at = Column(DateTime(timezone=True))
    # Recurring-peak insight (evidence["recurring_peaks"]): the extra saving if
    # the job is capped or rescheduled. Conditional, so it has its own state
    # and stays out of the projected totals until accepted.
    conditional_action_type = Column(String(30))
    conditional_monthly_saving = Column(Float)
    conditional_state = Column(String(30))
    conditional_state_note = Column(String(2000))
    conditional_state_changed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
