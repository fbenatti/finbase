from sqlalchemy import (
    Column, Integer, String, DateTime, JSON, Boolean, Float, ForeignKey, UniqueConstraint, func, Index,
)
from sqlalchemy.orm import relationship
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
    """Hierarchical department tree materialized in seven reporting levels."""
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


class DepartmentSharedCost(Base):
    """Shared-cost allocation configured on a donor department."""
    __tablename__ = "department_shared_cost"

    id = Column(Integer, primary_key=True, autoincrement=True)
    department_id = Column(Integer, ForeignKey("department.id", ondelete="CASCADE"), nullable=False, unique=True)
    enabled = Column(Boolean, nullable=False, default=False)
    mode = Column(String(20), nullable=False, default="consumption")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    targets = relationship(
        "DepartmentSharedCostTarget",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class DepartmentSharedCostTarget(Base):
    __tablename__ = "department_shared_cost_target"
    __table_args__ = (UniqueConstraint("config_id", "target_department_id", name="uq_shared_cost_target"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    config_id = Column(Integer, ForeignKey("department_shared_cost.id", ondelete="CASCADE"), nullable=False, index=True)
    target_department_id = Column(Integer, ForeignKey("department.id", ondelete="CASCADE"), nullable=False, index=True)
    percent = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class OrganizationMoveAudit(Base):
    """Auditable record of organizational moves and their budget-path refresh."""
    __tablename__ = "organization_move_audit"

    id = Column(Integer, primary_key=True, autoincrement=True)
    department_id = Column(Integer, ForeignKey("department.id"), nullable=False, index=True)
    old_parent_id = Column(Integer, nullable=True)
    new_parent_id = Column(Integer, nullable=True)
    old_path = Column(JSON, nullable=False, default=list)
    new_path = Column(JSON, nullable=False, default=list)
    planning_entries_updated = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())



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
    cloud_scope = Column(String(50), nullable=True, index=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ClassificationFieldValue(Base):
    """Distinct values per destination table / cloud / field, used for rule autocomplete."""
    __tablename__ = "classification_field_value"
    __table_args__ = (
        UniqueConstraint("destination_table_id", "cloud", "field", "value", name="uq_classification_field_value"),
        Index("ix_classification_field_value_lookup", "destination_table_id", "cloud", "field"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    destination_table_id = Column(Integer, ForeignKey("destination_table.id"), nullable=False, index=True)
    cloud = Column(String(50), nullable=False)
    field = Column(String(200), nullable=False)
    value = Column(String(500), nullable=False)
    count = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ClassificationRule(Base):
    __tablename__ = "classification_rule"

    id = Column(Integer, primary_key=True, autoincrement=True)
    connection_id = Column(Integer, ForeignKey("source_connection.id"), nullable=True, index=True)
    destination_table_id = Column(Integer, ForeignKey("destination_table.id"), nullable=True, index=True)
    department_id = Column(Integer, ForeignKey("department.id"), nullable=True, index=True)
    name = Column(String(200), nullable=False)
    source_field = Column(String(200), nullable=False)
    pattern = Column(String(500), nullable=False)
    pattern_type = Column(String(20), nullable=False, default="regex")
    match_value = Column(String(200), nullable=False)
    target_field = Column(String(200), nullable=False)
    conditions = Column(JSON, nullable=True)
    priority = Column(Integer, nullable=False, default=0)
    cloud_scope = Column(String(50), nullable=True, index=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_class_rule_dep_prio", "department_id", "priority"),
        Index("ix_class_rule_dest_prio", "destination_table_id", "priority"),
    )
