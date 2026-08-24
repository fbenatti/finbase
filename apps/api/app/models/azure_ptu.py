"""Metadata for Azure OpenAI PTU inventory and project attribution."""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text, func, Index, Float, BigInteger
from app.database import Base


class AzurePtuProjectMapping(Base):
    __tablename__ = "azure_ptu_project_mapping"
    __table_args__ = (Index("ix_azure_ptu_mapping_deployment", "deployment_resource_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    connection_id = Column(Integer, ForeignKey("source_connection.id", ondelete="CASCADE"), nullable=False)
    deployment_resource_id = Column(Text, nullable=False)
    account_resource_id = Column(Text, nullable=True)
    project_key = Column(String(120), nullable=False)
    project_name = Column(String(200), nullable=False)
    department_id = Column(Integer, nullable=True)
    purchase_mode = Column(String(30), nullable=True)
    deployment_type = Column(String(40), nullable=True)
    reservation_ref = Column(String(300), nullable=True)
    valid_from = Column(String(6), nullable=True)
    valid_until = Column(String(6), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AzurePtuSubproject(Base):
    __tablename__ = "azure_ptu_subproject"
    __table_args__ = (Index("ix_azure_ptu_subproject_project", "project_key"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_mapping_id = Column(Integer, ForeignKey("azure_ptu_project_mapping.id", ondelete="CASCADE"), nullable=True)
    project_key = Column(String(120), nullable=False)
    subproject_key = Column(String(160), nullable=False)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    telemetry_client_id = Column(String(200), nullable=True, unique=True)
    department_id = Column(Integer, nullable=True)
    owner = Column(String(320), nullable=True)
    environment = Column(String(40), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    valid_from = Column(String(6), nullable=True)
    valid_until = Column(String(6), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AzurePtuUsageEvent(Base):
    """Sanitized application telemetry; prompts and responses are never accepted."""
    __tablename__ = "azure_ptu_usage_event"
    __table_args__ = (
        Index("ix_azure_ptu_event_deployment_time", "deployment_name", "occurred_at"),
        Index("ix_azure_ptu_event_subproject_time", "subproject_key", "occurred_at"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(160), nullable=False, unique=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    telemetry_client_id = Column(String(200), nullable=False)
    project_key = Column(String(120), nullable=True)
    subproject_key = Column(String(160), nullable=True)
    environment = Column(String(40), nullable=True)
    azure_account_resource_id = Column(Text, nullable=True)
    deployment_name = Column(String(200), nullable=True)
    model_name = Column(String(200), nullable=True)
    model_version = Column(String(100), nullable=True)
    api_operation = Column(String(50), nullable=True)
    stream = Column(Boolean, nullable=True)
    status_code = Column(Integer, nullable=True)
    is_spillover = Column(Boolean, nullable=False, default=False)
    request_count = Column(Integer, nullable=False, default=1)
    input_tokens = Column(BigInteger, nullable=False, default=0)
    cached_input_tokens = Column(BigInteger, nullable=False, default=0)
    output_tokens = Column(BigInteger, nullable=False, default=0)
    reasoning_tokens = Column(BigInteger, nullable=False, default=0)
    total_tokens = Column(BigInteger, nullable=False, default=0)
    latency_ms = Column(Float, nullable=True)
    ingestion_status = Column(String(20), nullable=False, default="accepted")
    quarantine_reason = Column(Text, nullable=True)
    received_at = Column(DateTime(timezone=True), server_default=func.now())


class AzurePtuCheckpoint(Base):
    """Event Hub partition checkpoint and collector consecutive-failure counter."""
    __tablename__ = "azure_ptu_checkpoint"

    id = Column(Integer, primary_key=True, autoincrement=True)
    connection_id = Column(Integer, ForeignKey("source_connection.id", ondelete="CASCADE"), nullable=False)
    partition_id = Column(String(40), nullable=False, default="collector")
    offset = Column(String(80), nullable=True)
    sequence_number = Column(BigInteger, nullable=True)
    consecutive_failures = Column(Integer, nullable=False, default=0)
    last_status = Column(String(30), nullable=True)
    last_message = Column(Text, nullable=True)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
