"""Compat wrapper: delega a resolução efetiva para app.core.rbac (cache TTL 60s)."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import get_effective_permissions
from app.models.identity import User


async def compute_effective_perms(db: AsyncSession, user: User) -> set[tuple[str, str]]:
    perms = await get_effective_permissions(db, user.id)
    return {(m, a) for m, actions in perms.items() for a in actions}


def perms_to_json(perms: set[tuple[str, str]]) -> list[dict]:
    return [{"module": m, "action": a} for m, a in sorted(perms)]
