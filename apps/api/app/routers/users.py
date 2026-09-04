"""Administração de usuários: CRUD local, ativação, reset de senha, grants diretos."""
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import require
from app.core.modules import ALL_MODULES, ALL_ACTIONS
from app.core.rbac import get_effective_permissions, get_effective_roles, invalidate_permissions_cache
from app.core.security import hash_password, validate_password_policy
from app.database import get_db
from app.models.identity import User, Role, UserGrant

router = APIRouter(prefix="/users", tags=["users"])


class UserCreate(BaseModel):
    username: str
    full_name: str | None = None
    email: str | None = None
    password: str | None = None  # se ausente, gera temporária
    role_ids: list[int] = []


class UserUpdate(BaseModel):
    full_name: str | None = None
    email: str | None = None
    is_active: bool | None = None


class ResetPasswordBody(BaseModel):
    new_password: str | None = None  # ausente → gera temporária aleatória


class RolesBody(BaseModel):
    role_ids: list[int]


class GrantCreate(BaseModel):
    module: str
    action: str
    reason: str
    expires_at: datetime | None = None


def _validate_perm(module: str, action: str) -> None:
    if module not in ALL_MODULES:
        raise HTTPException(status_code=422, detail=f"Módulo desconhecido: {module}")
    if action not in ALL_ACTIONS:
        raise HTTPException(status_code=422, detail=f"Ação inválida: {action}")


async def _reload(db: AsyncSession, user_id: int) -> User:
    result = await db.execute(
        select(User)
        .options(selectinload(User.roles), selectinload(User.groups))
        .where(User.id == user_id)
    )
    return result.scalar_one()


def _user_out(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "email": u.email,
        "full_name": u.full_name,
        "auth_provider": u.auth_provider,
        "is_active": u.is_active,
        "must_change_password": u.must_change_password,
        "last_login_at": u.last_login_at,
        "created_at": u.created_at,
        "roles": [{"id": r.id, "name": r.name} for r in (u.roles or [])],
        "groups": [{"id": g.id, "name": g.name} for g in (u.groups or [])],
    }


@router.get("")
async def list_users(db: AsyncSession = Depends(get_db), user: User = Depends(require("users", "read"))):
    result = await db.execute(select(User).options(selectinload(User.roles), selectinload(User.groups)).order_by(User.username))
    return [_user_out(u) for u in result.scalars().all()]


@router.post("", status_code=201)
async def create_user(body: UserCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require("users", "create"))):
    username = body.username.lower().strip()
    if not username:
        raise HTTPException(status_code=422, detail="Usuário é obrigatório")
    result = await db.execute(select(User).where(User.username == username))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=422, detail="Já existe usuário com este nome")
    temp_password = body.password or secrets.token_urlsafe(10)
    new_user = User(
        username=username,
        full_name=body.full_name,
        email=body.email,
        password_hash=hash_password(temp_password),
        auth_provider="local",
        is_active=True,
        must_change_password=True,
    )
    if body.role_ids:
        result = await db.execute(select(Role).where(Role.id.in_(body.role_ids)))
        new_user.roles = list(result.scalars().all())
    db.add(new_user)
    await db.commit()
    invalidate_permissions_cache()
    out = _user_out(new_user)
    if body.password is None:
        out["temporary_password"] = temp_password
    return out


@router.get("/{user_id}")
async def get_user(user_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require("users", "read"))):
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    return _user_out(await _reload(db, user_id))


@router.put("/{user_id}")
async def update_user(user_id: int, body: UserUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(require("users", "update"))):
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if body.full_name is not None:
        target.full_name = body.full_name
    if body.email is not None:
        target.email = body.email
    if body.is_active is not None:
        target.is_active = body.is_active
    await db.commit()
    return _user_out(await _reload(db, target.id))


@router.post("/{user_id}/reset-password")
async def reset_password(user_id: int, body: ResetPasswordBody, db: AsyncSession = Depends(get_db), user: User = Depends(require("users", "update"))):
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    new_password = body.new_password or secrets.token_urlsafe(10)
    if not validate_password_policy(new_password):
        from app.core.security import PASSWORD_POLICY_ERROR
        raise HTTPException(status_code=422, detail=PASSWORD_POLICY_ERROR)
    target.password_hash = hash_password(new_password)
    target.must_change_password = True
    # Encerra sessões do usuário
    from app.models.identity import RefreshToken
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.user_id == target.id, RefreshToken.revoked_at.is_(None))
    )
    now = datetime.now(timezone.utc)
    for row in result.scalars().all():
        row.revoked_at = now
    await db.commit()
    return {"ok": True, "temporary_password": body.new_password is None and new_password or None}


@router.put("/{user_id}/roles")
async def set_user_roles(user_id: int, body: RolesBody, db: AsyncSession = Depends(get_db), user: User = Depends(require("users", "update"))):
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    result = await db.execute(select(Role).where(Role.id.in_(body.role_ids)))
    target.roles = list(result.scalars().all())
    await db.commit()
    invalidate_permissions_cache()
    return {"ok": True}


@router.get("/{user_id}/effective-permissions")
async def effective_permissions(user_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require("users", "read"))):
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    perms = await get_effective_permissions(db, user_id)
    origins = await get_effective_roles(db, user_id)
    return {
        "user_id": user_id,
        "username": target.username,
        "permissions": [
            {"module": m, "action": a}
            for m, actions in sorted(perms.items()) for a in sorted(actions)
        ],
        "origins": origins,
    }


# ── Grants diretos ──────────────────────────────────────────────────────────

@router.get("/{user_id}/grants")
async def list_grants(user_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require("users", "read"))):
    result = await db.execute(select(UserGrant).where(UserGrant.user_id == user_id))
    return [
        {
            "id": g.id, "module": g.module, "action": g.action,
            "reason": g.reason, "expires_at": g.expires_at, "created_at": g.created_at,
            "granted_by_id": g.granted_by_id,
        }
        for g in result.scalars().all()
    ]


@router.post("/{user_id}/grants", status_code=201)
async def create_grant(user_id: int, body: GrantCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require("users", "update"))):
    _validate_perm(body.module, body.action)
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if not body.reason.strip():
        raise HTTPException(status_code=422, detail="Motivo (reason) é obrigatório")
    result = await db.execute(
        select(UserGrant).where(
            UserGrant.user_id == user_id,
            UserGrant.module == body.module,
            UserGrant.action == body.action,
        )
    )
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=422, detail="Já existe grant para este módulo/ação")
    grant = UserGrant(
        user_id=user_id, module=body.module, action=body.action,
        reason=body.reason.strip(), expires_at=body.expires_at, granted_by_id=user.id,
    )
    db.add(grant)
    await db.commit()
    invalidate_permissions_cache(user_id)
    return {"id": grant.id, "module": grant.module, "action": grant.action, "reason": grant.reason}


@router.delete("/grants/{grant_id}")
async def delete_grant(grant_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require("users", "update"))):
    grant = (await db.execute(select(UserGrant).where(UserGrant.id == grant_id))).scalar_one_or_none()
    if grant is None:
        raise HTTPException(status_code=404, detail="Grant não encontrado")
    user_id = grant.user_id
    await db.delete(grant)
    await db.commit()
    invalidate_permissions_cache(user_id)
    return {"ok": True}
