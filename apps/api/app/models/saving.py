from sqlalchemy import Column, Integer, String, DateTime, JSON, Float, ForeignKey, func, Index, UniqueConstraint
from app.database import Base


class SavingAction(Base):
    __tablename__ = "saving_action"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    description = Column(String(2000))
    category = Column(String(100))
    owner = Column(String(160))
    destination_table_id = Column(Integer, ForeignKey("destination_table.id"), nullable=False, index=True)
    date_column = Column(String(200), nullable=False)
    cost_column = Column(String(200), nullable=False)
    currency_column = Column(String(200))
    filters = Column(JSON, nullable=False, default=list)
    start_date = Column(String(10), nullable=False, index=True)
    duration_days = Column(Integer, nullable=False, default=30)
    baseline_monthly = Column(Float, nullable=False)
    projected_monthly = Column(Float, nullable=False)
    status = Column(String(30), nullable=False, default="EM_FILA", index=True)
    outcome = Column(String(40))
    final_realized = Column(Float)
    final_projected = Column(Float)
    error_message = Column(String(2000))
    last_measured_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SavingMeasurement(Base):
    __tablename__ = "saving_measurement"
    id = Column(Integer, primary_key=True, autoincrement=True)
    action_id = Column(Integer, ForeignKey("saving_action.id", ondelete="CASCADE"), nullable=False, index=True)
    measured_date = Column(String(10), nullable=False, index=True)
    actual_cost = Column(Float, nullable=False, default=0)
    baseline_value = Column(Float, nullable=False, default=0)
    projected_saving = Column(Float, nullable=False, default=0)
    realized_saving = Column(Float, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("action_id", "measured_date", name="uq_saving_measurement_day"), Index("ix_saving_measurement_day", "measured_date"))
