from sqlalchemy import (
    Column, Integer, String, DateTime, JSON, Boolean, ForeignKey, func, Index,
)
from app.database import Base


class EtlMapping(Base):
    __tablename__ = "etl_mapping"

    id = Column(Integer, primary_key=True, autoincrement=True)
    connection_id = Column(Integer, ForeignKey("source_connection.id"), nullable=True, index=True)
    name = Column(String(200), nullable=False)
    source_column = Column(String(200), nullable=False)
    target_column = Column(String(200), nullable=False)
    transform_type = Column(String(50), nullable=False, default="RENAME")
    transform_config = Column(JSON, nullable=True)
    priority = Column(Integer, nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Department(Base):
    """Hierarchical department tree (Diretoria -> Departamento -> Sub-departamento... up to 5 levels)."""
    __tablename__ = "department"

    id = Column(Integer, primary_key=True, autoincrement=True)
    parent_id = Column(Integer, ForeignKey("department.id"), nullable=True, index=True)
    name = Column(String(200), nullable=False)
    centro_custo = Column(String(50), nullable=True)
    ambiente = Column(String(50), nullable=True)
    cloud_filter = Column(String(50), nullable=True)
    custom_fields = Column(JSON, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Project(Base):
    """Project linked to a Department, with subscription/resource-group mapping."""
    __tablename__ = "project"

    id = Column(Integer, primary_key=True, autoincrement=True)
    department_id = Column(Integer, ForeignKey("department.id"), nullable=False, index=True)
    projeto_name = Column(String(200), nullable=False)
    cloud = Column(String(50), nullable=True)
    subscription_rg = Column(String(500), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BusinessRule(Base):
    __tablename__ = "business_rule"

    id = Column(Integer, primary_key=True, autoincrement=True)
    connection_id = Column(Integer, ForeignKey("source_connection.id"), nullable=True, index=True)
    destination_table_id = Column(Integer, ForeignKey("destination_table.id"), nullable=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(String(1000), nullable=True)
    target_column = Column(String(200), nullable=False)
    rule_type = Column(String(50), nullable=False)
    config_json = Column(JSON, nullable=False)
    conditions = Column(JSON, nullable=True)
    expression = Column(String(1000), nullable=True)
    priority = Column(Integer, nullable=False, default=0)
    valid_from = Column(String(6), nullable=True)
    valid_until = Column(String(6), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ClassificationRule(Base):
    __tablename__ = "classification_rule"

    id = Column(Integer, primary_key=True, autoincrement=True)
    connection_id = Column(Integer, ForeignKey("source_connection.id"), nullable=True, index=True)
    destination_table_id = Column(Integer, ForeignKey("destination_table.id"), nullable=True, index=True)
    department_id = Column(Integer, ForeignKey("department.id"), nullable=True, index=True)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=True, index=True)
    name = Column(String(200), nullable=False)
    source_field = Column(String(200), nullable=False)
    pattern = Column(String(500), nullable=False)
    pattern_type = Column(String(20), nullable=False, default="regex")
    match_value = Column(String(200), nullable=False)
    target_field = Column(String(200), nullable=False)
    conditions = Column(JSON, nullable=True)
    priority = Column(Integer, nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_class_rule_dep_prio", "department_id", "priority"),
        Index("ix_class_rule_dest_prio", "destination_table_id", "priority"),
    )
