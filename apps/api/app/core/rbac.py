"""RBAC: resolução de permissões efetivas com cache em memória (TTL 60s).

Origens de permissão de um usuário (união):
  1. roles diretas (user_role) — concessão nominal;
  2. roles dos grupos (user_group → group_role);
  3. herança de roles (role_role), transitiva, profundidade máx. 10, anti-ciclo;
  4. grants diretos (user_grant), ignorando expirados.

Cache: dict em memória por user_id com TTL de 60 segundos. Toda mutação em
roles/vínculos/herança/grants deve chamar invalidate_permissions_cache().
"""
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.identity import (
    User, Role, RolePermission, Group, UserGrant,
    role_role,
)

CACHE_TTL_SECONDS = 60
MAX_ROLE_DEPTH = 10

_cache: dict[int, tuple[float, dict[str, set[str]], list[dict]]] = {}


def invalidate_permissions_cache(user_id: int | None = None) -> None:
    """Invalida o cache de permissões. Sem argumento, invalida tudo."""
    if user_id is None:
        _cache.clear()
    else:
        _cache.pop(user_id, None)


async def _expand_with_trace(db: AsyncSession, role_ids: set[int]) -> tuple[set[int], dict[int, int]]:
    """Retorna (expanded_ids, trace) onde trace[parent_id] = child_id que o trouxe."""
    expanded = set(role_ids)
    trace: dict[int, int] = {}
    frontier = list(role_ids)
    depth = 0
    while frontier and depth < MAX_ROLE_DEPTH:
        result = await db.execute(
            select(role_role.c.role_id, role_role.c.parent_role_id)
            .where(role_role.c.role_id.in_(frontier))
            .where(role_role.c.parent_role_id.notin_(expanded))
        )
        pairs = result.all()
        if not pairs:
            break
        next_frontier: list[int] = []
        for child, parent in pairs:
            if parent in expanded:
                continue
            expanded.add(parent)
            trace[parent] = child
            next_frontier.append(parent)
        frontier = next_frontier
        depth += 1
    return expanded, trace


async def _load_user_context(db: AsyncSession, user_id: int) -> tuple[set[int], list[dict]]:
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.roles),
            selectinload(User.groups).selectinload(Group.roles),
        )
        .where(User.id == user_id)
    )
    loaded = result.scalar_one_or_none()
    if loaded is None:
        return set(), []

    role_ids: set[int] = set()
    roles_with_origin: list[dict] = []
    for r in loaded.roles:
        if r.id is not None:
            role_ids.add(r.id)
            roles_with_origin.append({"role_id": r.id, "role": r.name, "origin": "direta"})
    for g in loaded.groups:
        for r in g.roles:
            if r.id is not None:
                role_ids.add(r.id)
                roles_with_origin.append({
                    "role_id": r.id, "role": r.name, "origin": f"grupo:{g.name}",
                })
    return role_ids, roles_with_origin


async def _resolve(db: AsyncSession, user_id: int) -> tuple[dict[str, set[str]], list[dict]]:
    role_ids, roles_with_origin = await _load_user_context(db, user_id)
    perms: dict[str, dict[str, set[str | None]]] = {}

    if role_ids:
        expanded, trace = await _expand_with_trace(db, role_ids)
        result = await db.execute(select(Role.id, Role.name).where(Role.id.in_(expanded)))
        role_names = {row[0]: row[1] for row in result.all()}
        result = await db.execute(select(RolePermission).where(RolePermission.role_id.in_(expanded)))
        for p in result.scalars().all():
            perms.setdefault(p.module, {}).setdefault(p.action, set()).add(p.scope)

        direct_ids = {r["role_id"] for r in roles_with_origin}
        for rid in expanded - direct_ids:
            child_id = trace.get(rid)
            child_name = role_names.get(child_id, str(child_id)) if child_id else "?"
            roles_with_origin.append({
                "role_id": rid,
                "role": role_names.get(rid, str(rid)),
                "origin": f"herdada de: {child_name}",
            })

    # Grants diretos (não expirados)
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(UserGrant).where(
            UserGrant.user_id == user_id,
            (UserGrant.expires_at.is_(None)) | (UserGrant.expires_at > now),
        )
    )
    for g in result.scalars().all():
        perms.setdefault(g.module, {}).setdefault(g.action, set()).add(g.scope)
        scope_txt = f":{g.scope}" if g.scope else ""
        roles_with_origin.append({
            "role_id": None,
            "role": f"grant direto ({g.module}:{g.action}{scope_txt})",
            "origin": f"grant direto — {g.reason}",
        })

    return perms, roles_with_origin


async def get_effective_permissions(db: AsyncSession, user_id: int) -> dict[str, dict[str, set[str | None]]]:
    """module -> {action -> set(scope|None)} efetivos, com cache TTL de 60s."""
    now = time.time()
    hit = _cache.get(user_id)
    if hit is not None and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    perms, origins = await _resolve(db, user_id)
    _cache[user_id] = (now, perms, origins)
    return perms


def has_permission(
    perms: dict[str, dict[str, set[str | None]]],
    module: str,
    action: str,
    scope: str | None = None,
) -> bool:
    """True se o usuário tem (module, action). scope=None exige permissão de
    módulo inteiro; scope definido vale se houver permissão total ou do escopo."""
    module_perms = perms.get(module) or {}
    scopes = module_perms.get(action) or set()
    if not scopes:
        return False
    if scope is None:
        return None in scopes
    return None in scopes or scope in scopes


async def get_effective_roles(db: AsyncSession, user_id: int) -> list[dict]:
    """Roles alcançadas com origem ('direta', 'grupo:<nome>', 'herdada de:<role>', grant direto)."""
    now = time.time()
    hit = _cache.get(user_id)
    if hit is not None and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[2]
    perms, origins = await _resolve(db, user_id)
    _cache[user_id] = (now, perms, origins)
    return origins
