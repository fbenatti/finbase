from sqlalchemy import (
    BigInteger, Column, Date, DateTime, Float, ForeignKey, Index, Integer,
    JSON, String, Text, UniqueConstraint, func,
)

from app.database import Base


class AzureTagPolicySnapshot(Base):
    """Immutable per-subscription tag state derived from one Resource Graph inventory."""

    __tablename__ = "azure_tag_policy_snapshot"
    __table_args__ = (
        UniqueConstraint("source_blob_id", "subscription_id", name="uq_azure_tag_snapshot_blob_subscription"),
        Index("ix_azure_tag_snapshot_connection_time", "connection_id", "snapshot_at"),
        Index("ix_azure_tag_snapshot_subscription_time", "subscription_id", "snapshot_at"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    connection_id = Column(Integer, ForeignKey("source_connection.id", ondelete="CASCADE"), nullable=False)
    source_blob_id = Column(Integer, ForeignKey("ingested_blob.id", ondelete="RESTRICT"), nullable=False)
    snapshot_at = Column(DateTime(timezone=True), nullable=False)
    snapshot_date = Column(Date, nullable=False)
    subscription_id = Column(String(36), nullable=False)
    subscription_name = Column(String(300), nullable=False)
    tag_keys = Column(JSON, nullable=False)
    taggable_items = Column(Integer, nullable=False, default=0)
    fully_tagged = Column(Integer, nullable=False, default=0)
    partially_tagged = Column(Integer, nullable=False, default=0)
    without_managed_tags = Column(Integer, nullable=False, default=0)
    taggable_resources = Column(Integer, nullable=False, default=0)
    taggable_resource_groups = Column(Integer, nullable=False, default=0)
    adherence_percent = Column(Float, nullable=True)
    source_blob_path = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AzureTagPolicyEvent(Base):
    """Azure Activity Log events related to tag policy lifecycle and remediation."""

    __tablename__ = "azure_tag_policy_event"
    __table_args__ = (
        UniqueConstraint("connection_id", "event_key", name="uq_azure_tag_event_connection_key"),
        Index("ix_azure_tag_event_connection_time", "connection_id", "event_at"),
        Index("ix_azure_tag_event_subscription_time", "subscription_id", "event_at"),
        Index("ix_azure_tag_event_correlation", "correlation_id"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    connection_id = Column(Integer, ForeignKey("source_connection.id", ondelete="CASCADE"), nullable=False)
    event_key = Column(String(64), nullable=False)
    source_event_id = Column(Text, nullable=False)
    event_at = Column(DateTime(timezone=True), nullable=False)
    subscription_id = Column(String(36), nullable=False)
    operation = Column(String(300), nullable=False)
    status = Column(String(80), nullable=True)
    resource_id = Column(Text, nullable=True)
    policy_assignment_id = Column(Text, nullable=True)
    remediation_id = Column(Text, nullable=True)
    correlation_id = Column(String(100), nullable=True)
    details = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
