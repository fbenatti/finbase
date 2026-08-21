"""AI cost origin — links license pools to FOCUS fact rows and optional per-user allocation."""

from sqlalchemy import (
    Column, Integer, String, DateTime, JSON, Boolean, Float, ForeignKey, Text, func, Index,
)
from sqlalchemy.orm import relationship
from app.database import Base

DISTRIBUTION_BASES = frozenset({"tokens", "requests", "activity_index", "chatturns", "equal"})
DISTRIBUTION_USER_KEYS = frozenset({"email", "login"})
VALUE_COLUMNS = frozenset({"BilledCost", "EffectiveCost", "TotalCostOnDemandBRL"})


class AiCostLink(Base):
    """Binds a license product (provider + SKU) to one or more FOCUS cost sources."""
    __tablename__ = "ai_cost_link"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    provider = Column(String(100), nullable=False, index=True)
    product_name = Column(String(200), nullable=False)
    license_sku = Column(String(200), nullable=True)
    valid_from = Column(String(6), nullable=True)
    valid_until = Column(String(6), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    distribution_enabled = Column(Boolean, nullable=False, default=False)
    distribution_basis = Column(String(30), nullable=True)
    distribution_user_key = Column(String(30), nullable=True)
    unit_cost = Column(Float, nullable=True)  # R$/licença/mês — custo unitário manual (ex.: Claude)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    sources = relationship(
        "AiCostLinkSource",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        order_by="AiCostLinkSource.id",
    )
    allocations = relationship(
        "AiCostAllocation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class AiCostLinkSource(Base):
    """One FOCUS filter (+ optional connection scope) contributing a percent of link cost."""
    __tablename__ = "ai_cost_link_source"
    __table_args__ = (Index("ix_ai_cost_link_source_link", "link_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    link_id = Column(Integer, ForeignKey("ai_cost_link.id", ondelete="CASCADE"), nullable=False)
    connection_id = Column(Integer, ForeignKey("source_connection.id", ondelete="SET NULL"), nullable=True, index=True)
    filter_json = Column(JSON, nullable=False, default=dict)
    value_column = Column(String(50), nullable=False, default="BilledCost")
    percent = Column(Float, nullable=False, default=100.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AiCostAllocation(Base):
    """Audit trail of cost distributed to a user when distribution is enabled."""
    __tablename__ = "ai_cost_allocation"
    __table_args__ = (
        Index("ix_ai_cost_allocation_period", "billing_period"),
        Index("ix_ai_cost_allocation_link_period", "link_id", "billing_period"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    link_id = Column(Integer, ForeignKey("ai_cost_link.id", ondelete="CASCADE"), nullable=False)
    billing_period = Column(String(6), nullable=False)
    user_email = Column(String(320), nullable=True)
    provider = Column(String(100), nullable=False)
    product_name = Column(String(200), nullable=False)
    allocated_amount = Column(Float, nullable=False, default=0.0)
    allocation_basis = Column(String(30), nullable=True)
    source_refs = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())