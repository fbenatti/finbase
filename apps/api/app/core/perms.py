"""Compat wrapper: delega a resolução efetiva para app.core.rbac (cache TTL 60s)."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import get_effective_permissions
from app.models.identity import User


def perms_to_json(perms: dict[str, dict[str, set[str | None]]]) -> list[dict]:
    items = []
    for module in sorted(perms):
        for action in sorted(perms[module]):
            for scope in sorted(perms[module][action], key=lambda s: (s is None, s)):
                items.append({"module": module, "action": action, "scope": scope})
    return items


async def compute_effective_perms(db: AsyncSession, user: User) -> dict[str, dict[str, set[str | None]]]:
    return await get_effective_permissions(db, user.id)
