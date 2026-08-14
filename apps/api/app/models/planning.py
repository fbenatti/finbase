from sqlalchemy import Column, Integer, String, DateTime, JSON, Boolean, ForeignKey, Float, func, Index
from app.database import Base


class FinancialType(Base):
    __tablename__ = "financial_type"
    id = Column(Integer, primary_key=True, autoincrement=True)
    # Used by pipelines and snapshots.  It is deliberately never editable.
    code = Column(String(80), nullable=False, unique=True, index=True)
    name = Column(String(160), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PlanningVersion(Base):
    __tablename__ = "planning_version"
    id = Column(Integer, primary_key=True, autoincrement=True)
    financial_type_id = Column(Integer, ForeignKey("financial_type.id"), nullable=False, index=True)
    year = Column(Integer, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    is_current = Column(Boolean, nullable=False, default=True, index=True)
    source = Column(String(30), nullable=False)  # IMPORT_EXCEL, EDICAO_GRID, RESTORE
    source_filename = Column(String(500), nullable=True)
    restored_from_id = Column(Integer, ForeignKey("planning_version.id"), nullable=True)
    classification_version_id = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_planning_version_current", "financial_type_id", "year", "is_current"),)


class PlanningEntry(Base):
    __tablename__ = "planning_entry"
    id = Column(Integer, primary_key=True, autoincrement=True)
    planning_version_id = Column(Integer, ForeignKey("planning_version.id", ondelete="CASCADE"), nullable=False, index=True)
    entity_type = Column(String(20), nullable=False)  # department | project
    department_id = Column(Integer, nullable=True, index=True)
    project_id = Column(Integer, nullable=True, index=True)
    # Frozen labels make a historical version readable after organization edits.
    department_levels = Column(JSON, nullable=False, default=list)
    project_name = Column(String(200), nullable=True)
    month = Column(Integer, nullable=False)
    value = Column(Float, nullable=False, default=0)
    __table_args__ = (Index("ix_planning_entry_version_entity_month", "planning_version_id", "entity_type", "department_id", "project_id", "month"),)
