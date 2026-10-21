"""Revisão do modo sombra: compara o RBAC legado com o motor novo para todos os usuários.

Só lê o banco. Usada pelo teste de integração (dados de teste) e pelo script
scripts/access_shadow_report.py (dados reais, somente leitura).
"""
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access.engine import legacy_screen_decision
from app.core.rbac import get_effective_permissions, has_permission, invalidate_permissions_cache
from app.models.identity import User

ACTIONS = ("read", "create", "update", "delete")


@dataclass(frozen=True)
class Row:
    username: str
    module: str
    action: str
    scope: str | None
    legacy: bool
    acl: bool

    @property
    def kind(self) -> str:
        if self.legacy and not self.acl:
            return "negação inesperada (legado permite, ACL nega)"
        if self.acl and not self.legacy:
            return "liberação extra (ACL permite, legado nega)"
        return "igual"


async def compare_all(db: AsyncSession, modules: list[str], scopes_by_module: dict[str, list[str | None]]) -> list[Row]:
    """Compara todas as combinações usuário × módulo × ação × escopo."""
    rows: list[Row] = []
    users = (await db.execute(select(User).where(User.is_active.is_(True)).order_by(User.id))).scalars().all()
    for user in users:
        invalidate_permissions_cache(user.id)
        legacy_perms = await get_effective_permissions(db, user.id)
        for module in modules:
            for action in ACTIONS:
                for scope in scopes_by_module.get(module, [None]):
                    legacy_ok = has_permission(legacy_perms, module, action, scope)
                    acl_ok = (await legacy_screen_decision(db, user.id, module, action, scope)).allowed
                    rows.append(Row(user.username, module, action, scope, legacy_ok, acl_ok))
    return rows


def divergences(rows: list[Row]) -> list[Row]:
    return [r for r in rows if r.legacy != r.acl]
