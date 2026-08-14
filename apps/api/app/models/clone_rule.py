from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, func

from app.database import Base


class CloneRule(Base):
    """Metadata-driven synthetic-line rule, evaluated after mapping and before rules."""
    __tablename__ = "clone_rule"
    id = Column(Integer, primary_key=True)
    destination_table_id = Column(Integer, ForeignKey("destination_table.id", ondelete="CASCADE"), nullable=False, index=True)
    connection_id = Column(Integer, ForeignKey("source_connection.id", ondelete="CASCADE"), nullable=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(String(1000), nullable=True)
    provider = Column(String(100), nullable=True)
    conditions = Column(JSON, nullable=False, default=list)
    roles = Column(JSON, nullable=False, default=dict)
    original_changes = Column(JSON, nullable=False, default=dict)
    clone_changes = Column(JSON, nullable=False, default=dict)
    valid_from = Column(String(6), nullable=True)
    valid_until = Column(String(6), nullable=True)
    priority = Column(Integer, nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ClonePricePendencies(Base):
    __tablename__ = "clone_price_pendency"
    id = Column(Integer, primary_key=True)
    clone_rule_id = Column(Integer, ForeignKey("clone_rule.id", ondelete="SET NULL"), nullable=True, index=True)
    destination_table_id = Column(Integer, ForeignKey("destination_table.id", ondelete="CASCADE"), nullable=False, index=True)
    connection_id = Column(Integer, ForeignKey("source_connection.id", ondelete="SET NULL"), nullable=True)
    provider = Column(String(100), nullable=True)
    meter_id = Column(String(300), nullable=True, index=True)
    billing_period = Column(String(6), nullable=True)
    source_record_id = Column(String(100), nullable=True)
    reason = Column(String(500), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
