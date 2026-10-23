from sqlalchemy import JSON, Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text, func

from app.database import Base


class CommitmentOffer(Base):
    """Price of one commitment instrument × term × payment, market or negotiated.

    Prices are a discount over the on-demand rate: the engine works in
    on-demand-equivalent cost per hour, so one number serves every cloud.
    """
    __tablename__ = "commitment_offer"
    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(20), nullable=False, index=True)
    instrument = Column(String(60), nullable=False, index=True)
    term_months = Column(Integer, nullable=False)
    payment_option = Column(String(20), nullable=False, default="NO_UPFRONT")
    source = Column(String(20), nullable=False, default="MARKET", index=True)
    # DISCOUNT: discount_pct over on-demand. EXTRA_OVER_MARKET: discount_pct on
    # top of the matching market offer (negotiated deals that stack).
    pricing_mode = Column(String(30), nullable=False, default="DISCOUNT")
    discount_pct = Column(Float, nullable=False)
    region = Column(String(80))
    family = Column(String(120))
    platform = Column(String(80))
    scope = Column(String(255))
    valid_from = Column(Date)
    valid_until = Column(Date)
    priority = Column(Integer, nullable=False, default=0)
    burn_down = Column(Boolean, nullable=False, default=False)
    # Spend commitments: yearly amount the deal requires for this discount (tier).
    min_commit_annual = Column(Float)
    contract_id = Column(Integer, ForeignKey("financial_contract.id", ondelete="SET NULL"), index=True)
    currency = Column(String(10))
    notes = Column(Text)
    active = Column(Boolean, nullable=False, default=True, index=True)
    created_by = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CommitmentRecoRun(Base):
    """One deterministic execution of the commitment engine over a frozen hourly window."""
    __tablename__ = "commitment_reco_run"
    id = Column(Integer, primary_key=True, autoincrement=True)
    connection_id = Column(Integer, ForeignKey("source_connection.id"), nullable=False, index=True)
    provider = Column(String(20), nullable=False)
    status = Column(String(30), nullable=False, default="RUNNING", index=True)
    engine_version = Column(String(60), nullable=False)
    policy_version = Column(String(60), nullable=False)
    window_start_utc = Column(DateTime(timezone=True), nullable=False)
    window_end_utc = Column(DateTime(timezone=True), nullable=False)
    keys_analyzed = Column(Integer, nullable=False, default=0)
    recommendations_count = Column(Integer, nullable=False, default=0)
    total_monthly_saving = Column(Float)
    currency = Column(String(10))
    readiness_counts = Column(JSON, nullable=False, default=dict)
    error_message = Column(String(2000))
    created_by = Column(Integer)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True))


class CommitmentRecommendation(Base):
    """Best offer for one commitment key and sizing profile; the engine never buys."""
    __tablename__ = "commitment_recommendation"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("commitment_reco_run.id", ondelete="CASCADE"), nullable=False, index=True)
    connection_id = Column(Integer, nullable=False, index=True)
    provider = Column(String(20), nullable=False, index=True)
    source = Column(String(20), nullable=False, default="ENGINE", index=True)
    commitment_key = Column(JSON, nullable=False)
    key_hash = Column(String(64), nullable=False, index=True)
    instrument = Column(String(60), nullable=False, index=True)
    purchase_path = Column(String(20), nullable=False)
    profile = Column(String(20), nullable=False, index=True)
    action_type = Column(String(30), nullable=False, index=True)
    readiness_status = Column(String(40), nullable=False, index=True)
    state = Column(String(30), nullable=False, default="NEW", index=True)
    confidence = Column(String(10), nullable=False)
    term_months = Column(Integer)
    payment_option = Column(String(20))
    offer_source = Column(String(20))
    offer_id = Column(Integer)
    discount_pct = Column(Float)
    # On-demand-equivalent cost per hour covered by the commitment, and what it costs.
    commit_ondemand_hour = Column(Float)
    commit_cost_hour = Column(Float)
    expected_utilization = Column(Float)
    expected_coverage = Column(Float)
    monthly_saving = Column(Float)
    term_saving = Column(Float)
    upfront_cost = Column(Float)
    npv = Column(Float)
    payback_months = Column(Float)
    saving_if_usage_drops = Column(Float)
    currency = Column(String(10))
    native_reco_ref = Column(String(255))
    rationale = Column(JSON, nullable=False, default=list)
    risks = Column(JSON, nullable=False, default=list)
    evidence = Column(JSON, nullable=False, default=dict)
    inputs_hash = Column(String(64), nullable=False)
    engine_version = Column(String(60), nullable=False)
    state_note = Column(String(2000))
    state_changed_by = Column(Integer)
    state_changed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
