"""Administração de roles: CRUD, permissões, herança (com anti-ciclo), árvore."""
import re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_current_user, require
from app.core.modules import ALL_MODULES, ALL_ACTIONS
from app.core.rbac import invalidate_permissions_cache, get_effective_permissions
from app.database import get_db
from app.models.identity import Role, RolePermission, User, role_role

router = APIRouter(prefix="/administracao/perfis", tags=["roles"])


class RoleCreate(BaseModel):
    name: str
    description: str | None = None


class RoleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class PermissionItem(BaseModel):
    module: str
    action: str
    scope: str | None = None


def _validate_perm(module: str, action: str) -> None:
    if module not in ALL_MODULES:
        raise HTTPException(status_code=422, detail=f"Módulo desconhecido: {module}")
    if action not in ALL_ACTIONS:
        raise HTTPException(status_code=422, detail=f"Ação inválida: {action}")


_SCOPE_RE = re.compile(r"^[a-z0-9_-]{1,60}$")


def _validate_scope(scope: str | None) -> str | None:
    if scope is None or scope == "":
        return None
    if not _SCOPE_RE.match(scope):
        raise HTTPException(status_code=422, detail="scope deve ter 1–60 chars (a-z0-9_-)")
    return scope


def _perm_item(p) -> dict:
    return {"module": p.module, "action": p.action, "scope": p.scope}


def _role_out(role: Role, perms: list[RolePermission] | None = None) -> dict:
    return {
        "id": role.id,
        "name": role.name,
        "description": role.description,
        "is_system": role.is_system,
        "created_at": role.created_at,
        "permissions": [
            _perm_item(p)
            for p in (perms or role.permissions or [])
        ] if perms is not None or role.permissions is not None else [],
    }


async def _would_create_cycle(db: AsyncSession, role_id: int, parent_id: int) -> bool:
    """Ciclo se subir de parent_id alcançar role_id."""
    if role_id == parent_id:
        return True
    seen = {parent_id}
    frontier = [parent_id]
    while frontier:
        result = await db.execute(
            select(role_role.c.parent_role_id).where(role_role.c.role_id.in_(frontier))
        )
        parents = {row[0] for row in result.all()}
        if role_id in parents:
            return True
        new_parents = parents - seen
        if not new_parents:
            return False
        seen |= new_parents
        frontier = list(new_parents)
    return False


@router.get("")
async def list_roles(db: AsyncSession = Depends(get_db), user: User = Depends(require("roles", "read"))):
    result = await db.execute(select(Role).options(selectinload(Role.permissions)).order_by(Role.name))
    roles = result.scalars().all()
    return [
        {"id": r.id, "name": r.name, "description": r.description, "is_system": r.is_system,
         "created_at": r.created_at, "permissions": [_perm_item(p) for p in r.permissions]}
        for r in roles
    ]


@router.post("", status_code=201)
async def create_role(body: RoleCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require("roles", "create"))):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Nome é obrigatório")
    result = await db.execute(select(Role).where(Role.name == name))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=422, detail="Já existe role com este nome")
    role = Role(name=name, description=body.description, is_system=False)
    db.add(role)
    await db.commit()
    invalidate_permissions_cache()
    return {"id": role.id, "name": role.name, "description": role.description, "is_system": role.is_system}


@router.get("/{role_id}")
async def get_role(role_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require("roles", "read"))):
    role = (await db.execute(select(Role).options(selectinload(Role.permissions), selectinload(Role.parents), selectinload(Role.children)).where(Role.id == role_id))).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail="Role não encontrada")
    parents = [{"id": p.id, "name": p.name} for p in role.parents]
    children = [{"id": c.id, "name": c.name} for c in role.children]
    return {
        "id": role.id, "name": role.name, "description": role.description,
        "is_system": role.is_system, "created_at": role.created_at,
        "permissions": [_perm_item(p) for p in role.permissions],
        "inherits_from": parents,
        "inherited_by": children,
    }


@router.put("/{role_id}")
async def update_role(role_id: int, body: RoleUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(require("roles", "update"))):
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail="Role não encontrada")
    if role.is_system and body.name is not None and body.name != role.name:
        raise HTTPException(status_code=422, detail="Role de sistema não pode ser renomeada")
    if body.name:
        role.name = body.name.strip()
    if body.description is not None:
        role.description = body.description
    await db.commit()
    invalidate_permissions_cache()
    return {"id": role.id, "name": role.name, "description": role.description, "is_system": role.is_system}


@router.delete("/{role_id}")
async def delete_role(role_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require("roles", "delete"))):
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail="Role não encontrada")
    if role.is_system:
        raise HTTPException(status_code=422, detail="Role de sistema não pode ser deletada")
    await db.delete(role)
    await db.commit()
    invalidate_permissions_cache()
    return {"ok": True}


@router.put("/{role_id}/permissoes")
async def set_role_permissions(role_id: int, items: list[PermissionItem], db: AsyncSession = Depends(get_db), user: User = Depends(require("roles", "update"))):
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail="Role não encontrada")
    for item in items:
        _validate_perm(item.module, item.action)
        item.scope = _validate_scope(item.scope)
    await db.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
    seen = set()
    for item in items:
        key = (item.module, item.action, item.scope)
        if key in seen:
            continue
        seen.add(key)
        db.add(RolePermission(role_id=role_id, module=item.module, action=item.action, scope=item.scope))
    await db.commit()
    invalidate_permissions_cache()
    return {"ok": True, "count": len(seen)}


@router.post("/{role_id}/heranca/{parent_id}")
async def add_inheritance(role_id: int, parent_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require("roles", "update"))):
    if role_id == parent_id:
        raise HTTPException(status_code=422, detail="Uma role não pode herdar de si mesma")
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    parent = (await db.execute(select(Role).where(Role.id == parent_id))).scalar_one_or_none()
    if role is None or parent is None:
        raise HTTPException(status_code=404, detail="Role não encontrada")
    if await _would_create_cycle(db, role_id, parent_id):
        raise HTTPException(status_code=422, detail="Herança fecharia um ciclo")
    exists = (await db.execute(
        select(role_role).where(role_role.c.role_id == role_id, role_role.c.parent_role_id == parent_id)
    )).first()
    if exists is None:
        await db.execute(role_role.insert().values(role_id=role_id, parent_role_id=parent_id))
        await db.commit()
    invalidate_permissions_cache()
    return {"ok": True}


@router.delete("/{role_id}/heranca/{parent_id}")
async def remove_inheritance(role_id: int, parent_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require("roles", "update"))):
    await db.execute(
        delete(role_role).where(role_role.c.role_id == role_id, role_role.c.parent_role_id == parent_id)
    )
    await db.commit()
    invalidate_permissions_cache()
    return {"ok": True}


@router.get("/{role_id}/arvore")
async def role_tree(role_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require("roles", "read"))):
    """Árvore de herança resolvida (pais transitivos com caminho)."""
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail="Role não encontrada")

    async def build(rid: int, seen: set[int]) -> dict:
        r = (await db.execute(select(Role).options(selectinload(Role.parents)).where(Role.id == rid))).scalar_one()
        node = {"id": r.id, "name": r.name, "parents": []}
        if rid in seen:
            node["cycle"] = True
            return node
        seen = seen | {rid}
        for p in r.parents:
            node["parents"].append(await build(p.id, seen))
        return node

    return await build(role_id, set())
