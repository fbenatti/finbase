"""Administração de grupos: CRUD, membros e roles associadas."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import require
from app.core.rbac import invalidate_permissions_cache
from app.database import get_db
from app.models.identity import Group, Role, User, user_group, group_role

router = APIRouter(prefix="/administracao/grupos", tags=["groups"])


class GroupCreate(BaseModel):
    name: str
    description: str | None = None
    external_id: str | None = None


class GroupUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    external_id: str | None = None


class MemberBody(BaseModel):
    user_id: int


class RoleBody(BaseModel):
    role_id: int


def _group_out(g: Group) -> dict:
    return {
        "id": g.id,
        "name": g.name,
        "description": g.description,
        "external_id": g.external_id,
        "created_at": g.created_at,
        "members": [{"id": u.id, "username": u.username, "full_name": u.full_name} for u in (g.users or [])],
        "roles": [{"id": r.id, "name": r.name} for r in (g.roles or [])],
    }


@router.get("")
async def list_groups(db: AsyncSession = Depends(get_db), user: User = Depends(require("groups", "read"))):
    result = await db.execute(select(Group).options(selectinload(Group.users), selectinload(Group.roles)).order_by(Group.name))
    groups = result.scalars().all()
    return [
        {"id": g.id, "name": g.name, "description": g.description, "external_id": g.external_id,
         "created_at": g.created_at,
         "members": [{"id": u.id, "username": u.username, "full_name": u.full_name} for u in (g.users or [])],
         "roles": [{"id": r.id, "name": r.name} for r in (g.roles or [])]}
        for g in groups
    ]


@router.post("", status_code=201)
async def create_group(body: GroupCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require("groups", "create"))):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Nome é obrigatório")
    result = await db.execute(select(Group).where(Group.name == name))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=422, detail="Já existe grupo com este nome")
    group = Group(name=name, description=body.description, external_id=body.external_id)
    db.add(group)
    await db.commit()
    return {"id": group.id, "name": group.name, "description": group.description, "external_id": group.external_id}


@router.get("/{group_id}")
async def get_group(group_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require("groups", "read"))):
    group = (await db.execute(select(Group).options(selectinload(Group.users), selectinload(Group.roles)).where(Group.id == group_id))).scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=404, detail="Grupo não encontrado")
    return _group_out(group)


@router.put("/{group_id}")
async def update_group(group_id: int, body: GroupUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(require("groups", "update"))):
    group = (await db.execute(select(Group).where(Group.id == group_id))).scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=404, detail="Grupo não encontrado")
    if body.name:
        group.name = body.name.strip()
    if body.description is not None:
        group.description = body.description
    if body.external_id is not None:
        group.external_id = body.external_id
    await db.commit()
    return {"id": group.id, "name": group.name}


@router.delete("/{group_id}")
async def delete_group(group_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require("groups", "delete"))):
    group = (await db.execute(select(Group).where(Group.id == group_id))).scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=404, detail="Grupo não encontrado")
    await db.delete(group)
    await db.commit()
    invalidate_permissions_cache()
    return {"ok": True}


@router.post("/{group_id}/membros")
async def add_member(group_id: int, body: MemberBody, db: AsyncSession = Depends(get_db), user: User = Depends(require("groups", "update"))):
    group = (await db.execute(select(Group).options(selectinload(Group.users), selectinload(Group.roles)).where(Group.id == group_id))).scalar_one_or_none()
    member = (await db.execute(select(User).options(selectinload(User.roles), selectinload(User.groups)).where(User.id == body.user_id))).scalar_one_or_none()
    if group is None or member is None:
        raise HTTPException(status_code=404, detail="Grupo ou usuário não encontrado")
    if member not in group.users:
        group.users.append(member)
        await db.commit()
    invalidate_permissions_cache()
    return {"ok": True}


@router.delete("/{group_id}/membros/{user_id}")
async def remove_member(group_id: int, user_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require("groups", "update"))):
    group = (await db.execute(select(Group).options(selectinload(Group.users)).where(Group.id == group_id))).scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=404, detail="Grupo não encontrado")
    member = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if member is not None and member in group.users:
        group.users.remove(member)
        await db.commit()
    invalidate_permissions_cache()
    return {"ok": True}


@router.post("/{group_id}/perfis")
async def add_group_role(group_id: int, body: RoleBody, db: AsyncSession = Depends(get_db), user: User = Depends(require("groups", "update"))):
    group = (await db.execute(select(Group).options(selectinload(Group.roles)).where(Group.id == group_id))).scalar_one_or_none()
    role = (await db.execute(select(Role).where(Role.id == body.role_id))).scalar_one_or_none()
    if group is None or role is None:
        raise HTTPException(status_code=404, detail="Grupo ou role não encontrada")
    if role not in group.roles:
        group.roles.append(role)
        await db.commit()
    invalidate_permissions_cache()
    return {"ok": True}


@router.delete("/{group_id}/perfis/{role_id}")
async def remove_group_role(group_id: int, role_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require("groups", "update"))):
    group = (await db.execute(select(Group).options(selectinload(Group.roles)).where(Group.id == group_id))).scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=404, detail="Grupo não encontrado")
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    if role is not None and role in group.roles:
        group.roles.remove(role)
        await db.commit()
    invalidate_permissions_cache()
    return {"ok": True}
