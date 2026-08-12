"""Scheduled ETL task — cron-like scheduling for automated extraction & reprocessing."""
from sqlalchemy import Column, Integer, String, DateTime, Boolean, JSON, func
from app.database import Base


class ScheduledTask(Base):
    __tablename__ = "scheduled_task"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    connection_id = Column(Integer, nullable=False)
    destination_reprocess = Column(Boolean, nullable=False, default=True)
    cron_expression = Column(String(100), nullable=False)  # e.g. "0 3 * * *" (daily at 3am)
    timezone = Column(String(50), nullable=False, default="America/Sao_Paulo")
    active = Column(Boolean, nullable=False, default=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    next_run_at = Column(DateTime(timezone=True), nullable=True)
    config_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
