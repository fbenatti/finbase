"""Administração de ACLs, escopos e simulação de acesso (Fase 3 e 5, backend).

Todas as rotas ficam sob /access, mapeadas ao módulo `users` no authz. Mutações
são registradas pela auditoria automática.
"""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access.policy import validate_grant_fields, validate_match_mode
from app.core.deps import require
from app.database import get_db
from app.models.access import AccessAcl, AccessAclCondition, AccessScope, UserScope
from app.models.identity import User
from app.services.access_service import decide_for_user, load_scopes

router = APIRouter(prefix="/access", tags=["access"])


class ConditionIn(BaseModel):
    column: str = Field(min_length=1, max_length=120)
    scope_type: Literal["hierarchy", "budget", "cost_center"]
    match_mode: Literal["exact", "subtree"] = "subtree"


class AclIn(BaseModel):
    resource_type: Literal["screen", "table", "field", "rule"]
    resource: str = Field(min_length=1, max_length=200)
    operation: Literal["read", "create", "write", "delete"]
    effect: Literal["allow", "deny"] = "allow"
    conditions: list[ConditionIn] = []


class ScopeIn(BaseModel):
    scope_type: Literal["hierarchy", "budget", "cost_center"]
    value: str = Field(min_length=1, max_length=300)
    label: str | None = None
    node_ref: str | None = None


class UserScopeIn(BaseModel):
    scope_id: int
    match_mode: Literal["exact", "subtree"] = "subtree"
    reason: str = Field(min_length=3, max_length=500)
    expires_at: datetime | None = None


def _acl_out(acl: AccessAcl) -> dict:
    return {
        "id": acl.id, "role_id": acl.role_id, "resource_type": acl.resource_type,
        "resource": acl.resource, "operation": acl.operation, "effect": acl.effect,
        "conditions": [
            {"column": c.column_name, "scope_type": c.scope_type, "match_mode": c.match_mode}
            for c in acl.conditions
        ],
    }


@router.get("/users/{user_id}/decision")
async def simulate_decision(
    user_id: int,
    operation: Literal["read", "create", "write", "delete"],
    resource_type: Literal["screen", "table", "field", "rule"],
    resource: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("users", "read")),
):
    """Simulador "ver como": decisão e motivo para uma pessoa."""
    decision = await decide_for_user(db, user_id, operation, resource_type, resource)
    return {
        "allowed": decision.allowed,
        "reason": decision.reason,
        "row_filter": None if decision.row_filter is None else [
            [{"column": p.column, "scope_type": p.scope_type, "equal_to": list(p.equal_to), "under": list(p.under)}
             for p in clause]
            for clause in decision.row_filter.clauses
        ],
    }


@router.get("/users/{user_id}/scopes")
async def user_scopes(user_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(require("users", "read"))):
    scopes = await load_scopes(db, user_id)
    return [{"scope_type": s.scope_type, "value": s.value, "match_mode": s.match_mode} for s in scopes]


@router.post("/scopes", status_code=201)
async def create_scope(body: ScopeIn, db: AsyncSession = Depends(get_db), _: User = Depends(require("users", "update"))):
    scope = AccessScope(**body.model_dump())
    db.add(scope)
    await db.flush()
    return {"id": scope.id, "scope_type": scope.scope_type, "value": scope.value}


@router.post("/roles/{role_id}/acls", status_code=201)
async def create_acl(role_id: int, body: AclIn, db: AsyncSession = Depends(get_db), _: User = Depends(require("users", "update"))):
    try:
        validate_grant_fields(body.effect, body.resource_type, body.operation)
        for cond in body.conditions:
            validate_match_mode(cond.match_mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    acl = AccessAcl(role_id=role_id, resource_type=body.resource_type, resource=body.resource,
                    operation=body.operation, effect=body.effect)
    acl.conditions = [
        AccessAclCondition(column_name=c.column, scope_type=c.scope_type, match_mode=c.match_mode)
        for c in body.conditions
    ]
    db.add(acl)
    await db.flush()
    return _acl_out(acl)


@router.delete("/acls/{acl_id}", status_code=204)
async def delete_acl(acl_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(require("users", "delete"))):
    await db.execute(delete(AccessAcl).where(AccessAcl.id == acl_id))


@router.post("/users/{user_id}/scopes", status_code=201)
async def grant_user_scope(
    user_id: int,
    body: UserScopeIn,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require("users", "update")),
):
    scope = await db.get(AccessScope, body.scope_id)
    if scope is None:
        raise HTTPException(status_code=404, detail="Escopo não encontrado")
    grant = UserScope(user_id=user_id, scope_id=body.scope_id, match_mode=body.match_mode,
                      granted_by_id=actor.id, reason=body.reason, expires_at=body.expires_at)
    db.add(grant)
    await db.flush()
    return {"id": grant.id, "user_id": user_id, "scope_id": body.scope_id}
