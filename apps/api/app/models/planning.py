from sqlalchemy import Column, Integer, String, DateTime, Date, Text, JSON, Boolean, ForeignKey, Float, func, Index
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
    # Business context turns technical snapshots into an auditable financial narrative.
    title = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)
    event_type = Column(String(40), nullable=True)
    effective_date = Column(Date, nullable=True, index=True)
    scope_id_orcamento = Column(String(80), nullable=True, index=True)
    scope_cloud = Column(String(80), nullable=True, index=True)
    # Nó da hierarquia que delimita a grade (substitui scope_id_orcamento; nulo em snapshots antigos).
    scope_hierarquia_id = Column(Integer, ForeignKey("department.id", ondelete="SET NULL"), nullable=True, index=True)
    # When a defense snapshot belongs to a request, "current" is tracked per
    # request instead of per (ID Orçamento, cloud): two projects may defend the
    # same requested line at the same time.
    defense_request_id = Column(Integer, ForeignKey("budget_defense_request.id", ondelete="SET NULL"), nullable=True, index=True)
    published_to_budget_at = Column(DateTime(timezone=True), nullable=True)
    published_budget_version_id = Column(Integer, ForeignKey("planning_version.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_planning_version_current", "financial_type_id", "year", "is_current"),)


class BudgetDefenseRequest(Base):
    """A project's budget defense request: the staging line before it reaches
    the organizational budget.

    The organizational chart (``department``) is never rewritten while a
    request is open.  An existing project whose requested ID Orçamento differs
    from its inherited one gets an *individual line* inside the defense; the
    organogram only receives the final ID Orçamento when the request is
    published, so a project never belongs to two budget lines at once.
    """

    __tablename__ = "budget_defense_request"
    id = Column(Integer, primary_key=True, autoincrement=True)
    year = Column(Integer, nullable=False, index=True)
    department_id = Column(Integer, ForeignKey("department.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_department_id = Column(Integer, ForeignKey("department.id", ondelete="SET NULL"), nullable=True)
    is_new_project = Column(Boolean, nullable=False, default=False)
    requested_id_orcamento = Column(String(80), nullable=False, index=True)
    requested_cloud = Column(String(80), nullable=True, index=True)
    title = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)
    # RASCUNHO | SOLICITADA | APROVADA | PUBLICADA | REJEITADA
    status = Column(String(30), nullable=False, default="RASCUNHO", index=True)
    requested_by_id = Column(Integer, ForeignKey("user_account.id", ondelete="SET NULL"), nullable=True)
    requested_at = Column(DateTime(timezone=True), server_default=func.now())
    decided_by_id = Column(Integer, ForeignKey("user_account.id", ondelete="SET NULL"), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    final_id_orcamento = Column(String(80), nullable=True)
    final_cloud = Column(String(80), nullable=True)
    decision_note = Column(Text, nullable=True)
    defense_version_id = Column(Integer, ForeignKey("planning_version.id", ondelete="SET NULL"), nullable=True)
    published_budget_version_id = Column(Integer, ForeignKey("planning_version.id", ondelete="SET NULL"), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (Index("ix_budget_defense_request_scope", "year", "requested_id_orcamento", "status"),)


class PlanningEntry(Base):
    __tablename__ = "planning_entry"
    id = Column(Integer, primary_key=True, autoincrement=True)
    planning_version_id = Column(Integer, ForeignKey("planning_version.id", ondelete="CASCADE"), nullable=False, index=True)
    entity_type = Column(String(20), nullable=False)  # department
    department_id = Column(Integer, nullable=True, index=True)
    # Frozen labels make a historical version readable after organization edits.
    department_levels = Column(JSON, nullable=False, default=list)
    month = Column(Integer, nullable=False)
    value = Column(Float, nullable=False, default=0)
    __table_args__ = (Index("ix_planning_entry_version_entity_month", "planning_version_id", "entity_type", "department_id", "month"),)
