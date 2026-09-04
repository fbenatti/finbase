"""Effective permission resolution for RBAC.

Permissões efetivas de um usuário = união de:
  1. roles diretas do usuário (grants nominais)
  2. roles herdadas transitivamente (role_role, profundidade máx. 10, anti-ciclo)
  3. roles dos grupos do usuário (também com herança)
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.identity import User, Role, RolePermission, Group, role_role

MAX_ROLE_DEPTH = 10


async def expand_role_ids(db: AsyncSession, role_ids: set[int]) -> set[int]:
    """Retorna role_ids mais todos os ancestrais (pais) transitivos."""
    expanded = set(role_ids)
    frontier = list(role_ids)
    depth = 0
    while frontier and depth < MAX_ROLE_DEPTH:
        result = await db.execute(
            select(role_role.c.parent_role_id)
            .where(role_role.c.role_id.in_(frontier))
            .where(role_role.c.parent_role_id.notin_(expanded))
        )
        parents = {row[0] for row in result.all()}
        if not parents:
            break
        expanded |= parents
        frontier = list(parents)
        depth += 1
    return expanded


async def compute_effective_perms(db: AsyncSession, user: User) -> set[tuple[str, str]]:
    """Retorna conjunto de (module, action) efetivos para o usuário."""
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.roles),
            selectinload(User.groups).selectinload(Group.roles),
        )
        .where(User.id == user.id)
    )
    loaded = result.scalar_one()
    role_ids: set[int] = {r.id for r in loaded.roles if r.id is not None}
    for g in loaded.groups:
        role_ids |= {r.id for r in g.roles if r.id is not None}
    if not role_ids:
        return set()
    expanded = await expand_role_ids(db, role_ids)
    result = await db.execute(select(RolePermission).where(RolePermission.role_id.in_(expanded)))
    return {(p.module, p.action) for p in result.scalars().all()}


def perms_to_json(perms: set[tuple[str, str]]) -> list[dict]:
    return [{"module": m, "action": a} for m, a in sorted(perms)]
