from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.financial_contract import (
    ContractSettlementMonth,
    FinancialContract,
    FinancialContractBudgetScope,
    FinancialContractConnection,
    ContractSettlementRun,
)
from app.services.contract_settlement import normalize_cloud_scope
from app.models.rules import Department
from app.models.source_connection import SourceConnection

router = APIRouter(prefix="/financial-contracts", tags=["financial-contracts"])
EFFECTIVE_SCOPES = {"POSITIVE_CONSUMPTION_SHARE"}


class BudgetScopeIn(BaseModel):
    cloud_scope: str
    budget_id: str


class FinancialContractIn(BaseModel):
    code: str = Field(min_length=2, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    destination_table_id: int | None = None
    currency: str = "BRL"
    valid_from: str = Field(pattern=r"^\d{6}$")
    valid_until: str = Field(pattern=r"^\d{6}$")
    allocation_method: str = "POSITIVE_CONSUMPTION_SHARE"
    synthetic_department_id: int | None = None
    connection_ids: list[int] = Field(default_factory=list)
    budget_scopes: list[BudgetScopeIn] = Field(default_factory=list)
    active: bool = True


class SettlementRecalculateIn(BaseModel):
    period_from: str | None = Field(default=None, pattern=r"^\d{6}$")
    period_until: str | None = Field(default=None, pattern=r"^\d{6}$")
    recalculate_all: bool = False


def _contract_out(contract: FinancialContract) -> dict[str, Any]:
    return {
        "id": contract.id, "code": contract.code, "name": contract.name,
        "description": contract.description, "destination_table_id": contract.destination_table_id,
        "currency": contract.currency, "valid_from": contract.valid_from,
        "valid_until": contract.valid_until, "allocation_method": contract.allocation_method,
        "credit_expiry_policy": contract.credit_expiry_policy,
        "synthetic_department_id": contract.synthetic_department_id, "active": contract.active,
        "connection_ids": [item.connection_id for item in (contract.connections or [])],
        "budget_scopes": [{"cloud_scope": normalize_cloud_scope(item.cloud_scope), "budget_id": item.budget_id} for item in (contract.budget_scopes or [])],
        "created_at": contract.created_at, "updated_at": contract.updated_at,
    }


def _department_scopes(departments: list[Department]) -> set[tuple[str, str]]:
    by_id = {item.id: item for item in departments}
    result: set[tuple[str, str]] = set()
    for department in departments:
        chain = []
        current = department
        while current:
            chain.append(current)
            current = by_id.get(current.parent_id) if current.parent_id else None
        chain.reverse()
        budget_id = next((item.id_orcamento for item in reversed(chain) if item.id_orcamento), None)
        cloud = normalize_cloud_scope(chain[2].name) if len(chain) >= 3 else None
        if budget_id and cloud:
            result.add((cloud, str(budget_id)))
    return result


async def _validate_contract_scope(body: FinancialContractIn, db: AsyncSession, *, current_id: int | None = None) -> None:
    if body.allocation_method not in EFFECTIVE_SCOPES:
        raise HTTPException(422, "allocation_method inválido")
    if body.valid_from > body.valid_until:
        raise HTTPException(422, "valid_from deve ser menor ou igual a valid_until")
    if not body.connection_ids or not body.budget_scopes:
        raise HTTPException(422, "Informe conexões e pelo menos um ID Orçamento")
    if body.synthetic_department_id is not None and await db.get(Department, body.synthetic_department_id) is None:
        raise HTTPException(422, "Departamento sintético não encontrado")
    connection_count = await db.scalar(select(func.count(SourceConnection.id)).where(SourceConnection.id.in_(body.connection_ids)))
    if int(connection_count or 0) != len(set(body.connection_ids)):
        raise HTTPException(422, "Uma ou mais conexões não foram encontradas")
    departments = list((await db.execute(select(Department).where(Department.active == True))).scalars().all())  # noqa: E712
    valid_scopes = _department_scopes(departments)
    for scope in body.budget_scopes:
        if (normalize_cloud_scope(scope.cloud_scope), scope.budget_id.strip()) not in valid_scopes:
            raise HTTPException(422, f"ID Orçamento não encontrado no organograma: {scope.cloud_scope}/{scope.budget_id}")
    query = select(FinancialContract).options(selectinload(FinancialContract.connections)).where(FinancialContract.active == True)  # noqa: E712
    if current_id:
        query = query.where(FinancialContract.id != current_id)
    for existing in (await db.execute(query)).scalars().all():
        if not {item.connection_id for item in existing.connections or []}.intersection(body.connection_ids):
            continue
        requested_scopes = {(normalize_cloud_scope(item.cloud_scope), item.budget_id.strip()) for item in body.budget_scopes}
        existing_scopes = {(normalize_cloud_scope(item.cloud_scope), item.budget_id.strip()) for item in existing.budget_scopes or []}
        if not requested_scopes.intersection(existing_scopes):
            # The same connection may legitimately participate in separate
            # commitment pools; credits are isolated by cloud + budget ID.
            continue
        if existing.valid_until >= body.valid_from and body.valid_until >= existing.valid_from:
            raise HTTPException(409, f"Conexão já vinculada ao contrato ativo {existing.code} na vigência informada")


async def _load(contract_id: int, db: AsyncSession) -> FinancialContract:
    contract = (await db.execute(select(FinancialContract).options(selectinload(FinancialContract.connections), selectinload(FinancialContract.budget_scopes)).where(FinancialContract.id == contract_id))).scalar_one_or_none()
    if not contract:
        raise HTTPException(404, "Contrato financeiro não encontrado")
    return contract


@router.get("")
async def list_contracts(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(FinancialContract).options(selectinload(FinancialContract.connections), selectinload(FinancialContract.budget_scopes)).order_by(FinancialContract.name))).scalars().all()
    return [_contract_out(row) for row in rows]


@router.get("/overview")
async def contracts_overview(db: AsyncSession = Depends(get_db)):
    """Return the latest auditable pool metrics for each contract."""
    contracts = (await db.execute(select(FinancialContract).order_by(FinancialContract.name))).scalars().all()
    result = []
    for contract in contracts:
        latest_run = (await db.execute(
            select(ContractSettlementRun.id)
            .where(ContractSettlementRun.contract_id == contract.id, ContractSettlementRun.status == "SUCCESS")
            .order_by(ContractSettlementRun.id.desc()).limit(1)
        )).scalar_one_or_none()
        latest = (await db.execute(
            select(ContractSettlementMonth)
            .where(ContractSettlementMonth.contract_id == contract.id, ContractSettlementMonth.run_id == latest_run)
            .order_by(ContractSettlementMonth.period.desc(), ContractSettlementMonth.id.desc())
        )).scalars().all()
        latest_period = latest[0].period if latest else None
        current_balance_rows = [row for row in latest if row.period == latest_period]
        run = await db.get(ContractSettlementRun, latest_run) if latest_run else None
        result.append({
            "contract_id": contract.id,
            "code": contract.code,
            "name": contract.name,
            "active": contract.active,
            "last_period": latest_period,
            "run_id": latest_run,
            "last_settlement_at": run.finished_at if run else None,
            "status": run.status if run else "NOT_SETTLED",
            "credit_balance": sum((Decimal(row.closing_credit or 0) for row in current_balance_rows), Decimal("0")),
            "overage_balance": sum((Decimal(row.closing_overage or 0) for row in current_balance_rows), Decimal("0")),
            "uncovered_overage": sum((Decimal(row.closing_overage or 0) for row in current_balance_rows), Decimal("0")),
            "realized": sum((Decimal(row.realized or 0) for row in latest), Decimal("0")),
            "pool_count": len(latest),
        })
    return result


@router.get("/options/budget-scopes")
async def budget_scope_options(db: AsyncSession = Depends(get_db)):
    departments = list((await db.execute(select(Department).where(Department.active == True))).scalars().all())  # noqa: E712
    counts: dict[tuple[str, str], int] = {}
    for department in departments:
        # Reuse the same inherited scope semantics used by planning.
        by_id = {item.id: item for item in departments}
        chain = []
        current = department
        while current:
            chain.append(current)
            current = by_id.get(current.parent_id) if current.parent_id else None
        chain.reverse()
        budget_id = next((item.id_orcamento for item in reversed(chain) if item.id_orcamento), None)
        cloud = normalize_cloud_scope(chain[2].name) if len(chain) >= 3 else None
        if budget_id and cloud:
            key = (cloud, str(budget_id)); counts[key] = counts.get(key, 0) + 1
    return [{"cloud_scope": cloud, "budget_id": budget_id, "project_count": count} for (cloud, budget_id), count in sorted(counts.items())]


@router.get("/{contract_id}")
async def get_contract(contract_id: int, db: AsyncSession = Depends(get_db)):
    return _contract_out(await _load(contract_id, db))


@router.post("", status_code=201)
async def create_contract(body: FinancialContractIn, db: AsyncSession = Depends(get_db)):
    await _validate_contract_scope(body, db)
    contract = FinancialContract(code=body.code.strip(), name=body.name.strip(), description=body.description, destination_table_id=body.destination_table_id, currency=body.currency.upper(), valid_from=body.valid_from, valid_until=body.valid_until, allocation_method=body.allocation_method, synthetic_department_id=body.synthetic_department_id, active=body.active)
    db.add(contract); await db.flush()
    for connection_id in sorted(set(body.connection_ids)):
        db.add(FinancialContractConnection(contract_id=contract.id, connection_id=connection_id))
    for scope in body.budget_scopes:
        db.add(FinancialContractBudgetScope(contract_id=contract.id, cloud_scope=normalize_cloud_scope(scope.cloud_scope), budget_id=scope.budget_id.strip()))
    await db.commit()
    return _contract_out(await _load(contract.id, db))


@router.put("/{contract_id}")
async def update_contract(contract_id: int, body: FinancialContractIn, db: AsyncSession = Depends(get_db)):
    contract = await _load(contract_id, db)
    await _validate_contract_scope(body, db, current_id=contract_id)
    for field in ("code", "name", "description", "destination_table_id", "currency", "valid_from", "valid_until", "allocation_method", "synthetic_department_id", "active"):
        setattr(contract, field, getattr(body, field).upper() if field == "currency" else getattr(body, field))
    await db.execute(delete(FinancialContractConnection).where(FinancialContractConnection.contract_id == contract_id))
    await db.execute(delete(FinancialContractBudgetScope).where(FinancialContractBudgetScope.contract_id == contract_id))
    for connection_id in sorted(set(body.connection_ids)):
        db.add(FinancialContractConnection(contract_id=contract_id, connection_id=connection_id))
    for scope in body.budget_scopes:
        db.add(FinancialContractBudgetScope(contract_id=contract_id, cloud_scope=normalize_cloud_scope(scope.cloud_scope), budget_id=scope.budget_id.strip()))
    await db.commit()
    return _contract_out(await _load(contract_id, db))


@router.get("/{contract_id}/settlements")
async def list_settlements(contract_id: int, db: AsyncSession = Depends(get_db)):
    await _load(contract_id, db)
    latest_run = (await db.execute(
        select(ContractSettlementRun.id)
        .where(ContractSettlementRun.contract_id == contract_id, ContractSettlementRun.status == "SUCCESS")
        .order_by(ContractSettlementRun.id.desc()).limit(1)
    )).scalar_one_or_none()
    run = await db.get(ContractSettlementRun, latest_run) if latest_run else None
    rows = (await db.execute(select(ContractSettlementMonth).where(ContractSettlementMonth.contract_id == contract_id, ContractSettlementMonth.run_id == latest_run).order_by(ContractSettlementMonth.period, ContractSettlementMonth.cloud_scope, ContractSettlementMonth.budget_id))).scalars().all()
    result = []
    for row in rows:
        item = {column.name: getattr(row, column.name) for column in row.__table__.columns}
        # The dashboard uses a signed adjustment where generated credit is
        # positive and uncovered overage is negative. Derive this from the
        # auditable ledger components instead of the legacy stored adjustment,
        # whose sign may differ in older settlement runs.
        item["contractual_adjustment"] = (
            Decimal(row.credit_generated or 0) - Decimal(row.gross_overage or 0)
        )
        item.update({
            "run_source": run.source if run else None,
            "run_created_at": run.created_at if run else None,
            "run_finished_at": run.finished_at if run else None,
            "budget_version_ids": run.budget_version_ids if run else [],
        })
        result.append(item)
    return result


@router.post("/{contract_id}/settlements/recalculate")
async def recalculate_settlements(
    contract_id: int,
    body: SettlementRecalculateIn,
    db: AsyncSession = Depends(get_db),
):
    """Recompute from Silver and current monthly budgets, never client amounts."""
    contract = await _load(contract_id, db)
    if body.period_from and body.period_until and body.period_from > body.period_until:
        raise HTTPException(422, "period_from deve ser menor ou igual a period_until")
    if not body.recalculate_all and not body.period_from and not body.period_until:
        raise HTTPException(422, "Informe período ou recalculate_all=true")
    if body.period_from and body.period_from < contract.valid_from:
        raise HTTPException(422, "period_from anterior à vigência do contrato")
    from app.services.contract_settlement_materializer import (
        recalculate_contracts_for_connections,
    )
    try:
        results = await recalculate_contracts_for_connections(
            contract.connections and [item.connection_id for item in contract.connections] or [],
            contract_ids=[contract.id],
            source="API_RECALCULATE",
        )
        result = next((item for item in results if item["contract_id"] == contract.id), None)
        if result is None:
            raise ValueError("Contrato sem consumo contratual publicado na Silver")
        return result
    except Exception as exc:
        await db.rollback()
        raise HTTPException(422, f"Liquidação rejeitada: {exc}") from exc
