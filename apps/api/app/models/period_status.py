from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, UniqueConstraint, func
from app.database import Base


class PeriodStatus(Base):
    __tablename__ = "period_status"
    __table_args__ = (UniqueConstraint("connection_id", "billing_period"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    connection_id = Column(Integer, ForeignKey("source_connection.id"), nullable=False)
    billing_period = Column(String(6), nullable=False)
    status = Column(String(20), nullable=False, default="OPEN")
    auto_close_days = Column(Integer, nullable=False, default=7)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    closed_by = Column(String(200), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
