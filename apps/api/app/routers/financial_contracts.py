from __future__ import annotations

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
)
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
    synthetic_department_id: int
    connection_ids: list[int] = Field(default_factory=list)
    budget_scopes: list[BudgetScopeIn] = Field(default_factory=list)
    active: bool = True


def _contract_out(contract: FinancialContract) -> dict[str, Any]:
    return {
        "id": contract.id, "code": contract.code, "name": contract.name,
        "description": contract.description, "destination_table_id": contract.destination_table_id,
        "currency": contract.currency, "valid_from": contract.valid_from,
        "valid_until": contract.valid_until, "allocation_method": contract.allocation_method,
        "credit_expiry_policy": contract.credit_expiry_policy,
        "synthetic_department_id": contract.synthetic_department_id, "active": contract.active,
        "connection_ids": [item.connection_id for item in (contract.connections or [])],
        "budget_scopes": [{"cloud_scope": item.cloud_scope, "budget_id": item.budget_id} for item in (contract.budget_scopes or [])],
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
        cloud = chain[2].name.lower() if len(chain) >= 3 else None
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
    if await db.get(Department, body.synthetic_department_id) is None:
        raise HTTPException(422, "Departamento sintético não encontrado")
    connection_count = await db.scalar(select(func.count(SourceConnection.id)).where(SourceConnection.id.in_(body.connection_ids)))
    if int(connection_count or 0) != len(set(body.connection_ids)):
        raise HTTPException(422, "Uma ou mais conexões não foram encontradas")
    departments = list((await db.execute(select(Department).where(Department.active == True))).scalars().all())  # noqa: E712
    valid_scopes = _department_scopes(departments)
    for scope in body.budget_scopes:
        if (scope.cloud_scope.strip().lower(), scope.budget_id.strip()) not in valid_scopes:
            raise HTTPException(422, f"ID Orçamento não encontrado no organograma: {scope.cloud_scope}/{scope.budget_id}")
    query = select(FinancialContract).options(selectinload(FinancialContract.connections)).where(FinancialContract.active == True)  # noqa: E712
    if current_id:
        query = query.where(FinancialContract.id != current_id)
    for existing in (await db.execute(query)).scalars().all():
        if not {item.connection_id for item in existing.connections or []}.intersection(body.connection_ids):
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
        cloud = chain[2].name.lower() if len(chain) >= 3 else None
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
        db.add(FinancialContractBudgetScope(contract_id=contract.id, cloud_scope=scope.cloud_scope.lower(), budget_id=scope.budget_id.strip()))
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
        db.add(FinancialContractBudgetScope(contract_id=contract_id, cloud_scope=scope.cloud_scope.lower(), budget_id=scope.budget_id.strip()))
    await db.commit()
    return _contract_out(await _load(contract_id, db))


@router.get("/{contract_id}/settlements")
async def list_settlements(contract_id: int, db: AsyncSession = Depends(get_db)):
    await _load(contract_id, db)
    rows = (await db.execute(select(ContractSettlementMonth).where(ContractSettlementMonth.contract_id == contract_id).order_by(ContractSettlementMonth.period, ContractSettlementMonth.cloud_scope, ContractSettlementMonth.budget_id))).scalars().all()
    return [{column.name: getattr(row, column.name) for column in row.__table__.columns} for row in rows]
