from sqlalchemy import Column, Integer, String, BigInteger, DateTime, Text, ForeignKey, UniqueConstraint, func
from app.database import Base


class MetricDailyCollection(Base):
    """Persistent per-connection/source/UTC-day reconciliation state."""
    __tablename__ = "metric_daily_collection"
    __table_args__ = (UniqueConstraint("connection_id", "source_key", "day_utc", name="uq_metric_daily_collection_key"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    connection_id = Column(Integer, ForeignKey("source_connection.id", ondelete="CASCADE"), nullable=False)
    source_key = Column(String(80), nullable=False)
    day_utc = Column(String(8), nullable=False)
    status = Column(String(40), nullable=False)
    bronze_rows = Column(BigInteger, nullable=True)
    silver_rows = Column(BigInteger, nullable=True)
    task_id = Column(Integer, ForeignKey("etl_task.id", ondelete="SET NULL"), nullable=True)
    reason = Column(Text, nullable=True)
    absent_reason = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
