from sqlalchemy import Column, Integer, BigInteger, String, DateTime, JSON, ForeignKey, Boolean, Text, func, Index
from app.database import Base


class FinancialBaseVersion(Base):
    __tablename__ = "financial_base_version"
    id = Column(Integer, primary_key=True, autoincrement=True)
    destination_table_id = Column(Integer, ForeignKey("destination_table.id"), nullable=False, index=True)
    physical_name = Column(String(200), nullable=False)
    iceberg_snapshot_id = Column(String(200), nullable=True)
    row_count = Column(BigInteger, nullable=False, default=0)
    schema_hash = Column(String(128), nullable=True)
    status = Column(String(40), nullable=False, default="ACTIVE")
    source_task_id = Column(Integer, ForeignKey("etl_task.id"), nullable=True)
    periods = Column(JSON, nullable=False, default=list)
    connection_ids = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ClassificationRulesetSnapshot(Base):
    __tablename__ = "classification_ruleset_snapshot"
    id = Column(Integer, primary_key=True, autoincrement=True)
    destination_table_id = Column(Integer, ForeignKey("destination_table.id"), nullable=False, index=True)
    rules_json = Column(JSON, nullable=False, default=list)
    organization_json = Column(JSON, nullable=False, default=dict)
    ruleset_hash = Column(String(128), nullable=False, unique=True)
    classifier_version = Column(String(80), nullable=False, default="v1")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ClassificationVersion(Base):
    __tablename__ = "classification_version"
    id = Column(Integer, primary_key=True, autoincrement=True)
    destination_table_id = Column(Integer, ForeignKey("destination_table.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    financial_base_version_id = Column(Integer, ForeignKey("financial_base_version.id"), nullable=False)
    ruleset_snapshot_id = Column(Integer, ForeignKey("classification_ruleset_snapshot.id"), nullable=False)
    physical_table = Column(String(255), nullable=False, unique=True)
    mode = Column(String(20), nullable=False, default="FULL")
    source_task_id = Column(Integer, ForeignKey("etl_task.id"), nullable=True)
    status = Column(String(50), nullable=False, default="QUEUED", index=True)
    row_count = Column(BigInteger, nullable=False, default=0)
    classified_rows = Column(BigInteger, nullable=False, default=0)
    unclassified_rows = Column(BigInteger, nullable=False, default=0)
    metrics_json = Column(JSON, nullable=False, default=dict)
    error = Column(Text, nullable=True)
    created_by = Column(String(200), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    published_at = Column(DateTime(timezone=True), nullable=True)
    __table_args__ = (Index("ix_classification_version_dest_version", "destination_table_id", "version", unique=True),)


class ClassificationPublication(Base):
    __tablename__ = "classification_publication"
    id = Column(Integer, primary_key=True, autoincrement=True)
    destination_table_id = Column(Integer, ForeignKey("destination_table.id"), nullable=False, index=True)
    previous_version_id = Column(Integer, ForeignKey("classification_version.id"), nullable=True)
    version_id = Column(Integer, ForeignKey("classification_version.id"), nullable=False)
    operation = Column(String(30), nullable=False, default="PUBLISH")
    view_name = Column(String(255), nullable=False)
    previous_definition = Column(Text, nullable=True)
    new_definition = Column(Text, nullable=True)
    status = Column(String(30), nullable=False, default="COMPLETED")
    correlation_id = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ClassificationArtifactBinding(Base):
    __tablename__ = "classification_artifact_binding"
    id = Column(Integer, primary_key=True, autoincrement=True)
    artifact_type = Column(String(50), nullable=False)
    artifact_id = Column(String(120), nullable=False)
    classification_version_id = Column(Integer, ForeignKey("classification_version.id"), nullable=True, index=True)
    binding_mode = Column(String(30), nullable=False, default="EXPLICIT")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_classification_artifact_unique", "artifact_type", "artifact_id", unique=True),)
