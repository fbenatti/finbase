"""Shared, metadata-only saved configurations for Lakehouse pivots."""
from sqlalchemy import Column, Integer, String, DateTime, JSON, ForeignKey, func, Index
from app.database import Base


class PivotView(Base):
    __tablename__ = "pivot_view"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(160), nullable=False)
    destination_table_id = Column(Integer, ForeignKey("destination_table.id", ondelete="CASCADE"), nullable=False)
    config_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("ix_pivot_view_destination_name", "destination_table_id", "name", unique=True),)
