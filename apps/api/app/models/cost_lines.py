from sqlalchemy import (
    BigInteger, Column, Integer, String, DateTime, ForeignKey, JSON, func, Text, Index,
)
from app.database import Base


class RawCostLine(Base):
    __tablename__ = "raw_cost_line"
    __table_args__ = (Index("idx_raw_blob_id", "blob_id"),)

    id = Column(BigInteger, primary_key=True, autoincrement=True)  # noqa: F821
    blob_id = Column(Integer, ForeignKey("ingested_blob.id"), nullable=False)
    billing_period = Column(String(6), nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class NormalizedCostLine(Base):
    __tablename__ = "normalized_cost_line"
    __table_args__ = (
        Index("idx_norm_blob_id", "blob_id"),
        Index("idx_norm_raw_id", "raw_id"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)  # noqa: F821
    blob_id = Column(Integer, ForeignKey("ingested_blob.id"), nullable=False)
    raw_id = Column(BigInteger, ForeignKey("raw_cost_line.id"), nullable=True)  # noqa: F821
    billing_period = Column(String(6), nullable=False)
    cost_type = Column(String(20), nullable=False, default="actual")
    data = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
