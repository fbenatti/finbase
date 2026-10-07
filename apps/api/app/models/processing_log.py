from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, func

from app.database import Base


class ProcessingLog(Base):
    """Canonical operational log for an ETL or reprocessing execution."""

    __tablename__ = "processing_log"
    __table_args__ = (
        Index("ix_processing_log_started", "started_at"),
        Index("ix_processing_log_type_status", "process_type", "status"),
        Index("ix_processing_log_etl_task", "etl_task_id", unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    process_type = Column(String(60), nullable=False, index=True)
    status = Column(String(40), nullable=False, default="QUEUED")
    etl_task_id = Column(Integer, ForeignKey("etl_task.id", ondelete="SET NULL"), nullable=True)
    source_kind = Column(String(60), nullable=True)
    source = Column(JSON, nullable=False, default=dict)
    destination = Column(JSON, nullable=False, default=dict)
    partitions = Column(JSON, nullable=False, default=list)
    parameters = Column(JSON, nullable=False, default=dict)
    counts = Column(JSON, nullable=False, default=dict)
    phase = Column(String(500), nullable=True)
    error = Column(Text, nullable=True)
    details = Column(JSON, nullable=False, default=dict)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
