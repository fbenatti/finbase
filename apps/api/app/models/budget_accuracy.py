from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, func

from app.database import Base


class BudgetAccuracyAck(Base):
    __tablename__ = "budget_accuracy_ack"
    id = Column(Integer, primary_key=True, autoincrement=True)
    target_month = Column(String(6), nullable=False, index=True)
    planning_version_id = Column(Integer, ForeignKey("planning_version.id", ondelete="SET NULL"), nullable=True, index=True)
    department_id = Column(Integer, nullable=True, index=True)
    department_path = Column(JSON, nullable=False, default=list)
    department_name = Column(String(300), nullable=False)
    budget_value = Column(Float, nullable=False, default=0)
    actual_value = Column(Float, nullable=False, default=0)
    variance = Column(Float, nullable=False, default=0)
    category = Column(String(80), nullable=False)
    reason = Column(Text, nullable=False)
    severity = Column(String(20), nullable=False, default="Informação")
    ordem = Column(Integer, nullable=False, default=0)
    registered_at = Column(Date, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (Index("ix_budget_accuracy_ack_month_department", "target_month", "department_id"),)


class BudgetAccuracyAckHistory(Base):
    __tablename__ = "budget_accuracy_ack_history"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ack_id = Column(Integer, ForeignKey("budget_accuracy_ack.id", ondelete="CASCADE"), nullable=False, index=True)
    category = Column(String(80), nullable=False)
    reason = Column(Text, nullable=False)
    changed_at = Column(DateTime(timezone=True), server_default=func.now())