from sqlalchemy import (
    Column, Integer, String, DateTime, Boolean, ForeignKey, Numeric,
    Text, UniqueConstraint, Index, func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class FinancialContract(Base):
    __tablename__ = "financial_contract"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(80), nullable=False, unique=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    destination_table_id = Column(Integer, ForeignKey("destination_table.id"), nullable=True, index=True)
    currency = Column(String(8), nullable=False, default="BRL")
    valid_from = Column(String(6), nullable=False)
    valid_until = Column(String(6), nullable=False)
    allocation_method = Column(String(40), nullable=False, default="POSITIVE_CONSUMPTION_SHARE")
    credit_expiry_policy = Column(String(30), nullable=False, default="CONTRACT_END")
    # Optional: zero-consumption months keep only the pool ledger (credit),
    # without inventing a realized line in the organizational hierarchy.
    synthetic_department_id = Column(Integer, ForeignKey("department.id"), nullable=True, index=True)
    active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    connections = relationship("FinancialContractConnection", cascade="all, delete-orphan", lazy="selectin")
    budget_scopes = relationship("FinancialContractBudgetScope", cascade="all, delete-orphan", lazy="selectin")


class FinancialContractConnection(Base):
    __tablename__ = "financial_contract_connection"
    __table_args__ = (UniqueConstraint("contract_id", "connection_id", name="uq_fin_contract_connection"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    contract_id = Column(Integer, ForeignKey("financial_contract.id", ondelete="CASCADE"), nullable=False, index=True)
    connection_id = Column(Integer, ForeignKey("source_connection.id", ondelete="CASCADE"), nullable=False, index=True)


class FinancialContractBudgetScope(Base):
    __tablename__ = "financial_contract_budget_scope"
    __table_args__ = (UniqueConstraint("contract_id", "cloud_scope", "budget_id", name="uq_fin_contract_budget_scope"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    contract_id = Column(Integer, ForeignKey("financial_contract.id", ondelete="CASCADE"), nullable=False, index=True)
    cloud_scope = Column(String(50), nullable=False, index=True)
    budget_id = Column(String(80), nullable=False, index=True)


class ContractSettlementRun(Base):
    __tablename__ = "contract_settlement_run"

    id = Column(Integer, primary_key=True, autoincrement=True)
    contract_id = Column(Integer, ForeignKey("financial_contract.id"), nullable=False, index=True)
    period_from = Column(String(6), nullable=False)
    period_until = Column(String(6), nullable=False)
    status = Column(String(20), nullable=False, default="RUNNING", index=True)
    source = Column(String(40), nullable=False, default="REPROCESS")
    input_fingerprint = Column(String(128), nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)


class ContractSettlementMonth(Base):
    __tablename__ = "contract_settlement_month"
    __table_args__ = (
        UniqueConstraint("run_id", "cloud_scope", "budget_id", "period", name="uq_contract_settlement_month"),
        Index("ix_contract_settlement_lookup", "contract_id", "cloud_scope", "budget_id", "period"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("contract_settlement_run.id", ondelete="CASCADE"), nullable=False, index=True)
    contract_id = Column(Integer, ForeignKey("financial_contract.id"), nullable=False, index=True)
    cloud_scope = Column(String(50), nullable=False)
    budget_id = Column(String(80), nullable=False)
    period = Column(String(6), nullable=False)
    commitment = Column(Numeric(20, 6), nullable=False, default=0)
    billed_cost = Column(Numeric(20, 6), nullable=False, default=0)
    consumption = Column(Numeric(20, 6), nullable=False, default=0)
    opening_credit = Column(Numeric(20, 6), nullable=False, default=0)
    credit_generated = Column(Numeric(20, 6), nullable=False, default=0)
    gross_overage = Column(Numeric(20, 6), nullable=False, default=0)
    credit_used = Column(Numeric(20, 6), nullable=False, default=0)
    uncovered_overage = Column(Numeric(20, 6), nullable=False, default=0)
    expired_credit = Column(Numeric(20, 6), nullable=False, default=0)
    closing_credit = Column(Numeric(20, 6), nullable=False, default=0)
    realized = Column(Numeric(20, 6), nullable=False, default=0)
    allocation_factor = Column(Numeric(20, 12), nullable=False, default=0)
    status = Column(String(20), nullable=False, default="SUCCESS")
    message = Column(Text, nullable=True)
