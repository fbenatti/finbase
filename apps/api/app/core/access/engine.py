"""Fase 6: escolha do motor de autorização de tela (legacy, shadow ou acl).

ACCESS_ENGINE:
- legacy: só o RBAC antigo decide. É o rollback.
- shadow: o RBAC antigo decide, e o motor novo é comparado e divergências são logadas.
- acl: o motor novo decide. Se não houver nenhuma ACL no banco, volta ao legado.

O padrão é `shadow`: nada muda para o usuário. O corte para `acl` só acontece
depois da revisão das divergências.
"""
import logging
import os
import time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access.legacy import legacy_screen_allowed
from app.core.access.policy import Decision
from app.models.access import AccessAcl
from app.services import access_cache
from app.services.access_service import load_grants

logger = logging.getLogger("access.shadow")

ENGINE_ENV = "ACCESS_ENGINE"
MODES = ("legacy", "shadow", "acl")
DEFAULT_MODE = "shadow"
_POPULATED_TTL_S = 60.0
_populated: tuple[float, bool] | None = None


def access_engine() -> str:
    value = os.getenv(ENGINE_ENV, DEFAULT_MODE).strip().lower()
    return value if value in MODES else DEFAULT_MODE


async def _has_acls(db: AsyncSession) -> bool:
    global _populated
    now = time.monotonic()
    if _populated is not None and now - _populated[0] < _POPULATED_TTL_S:
        return _populated[1]
    try:
        found = (await db.execute(select(func.count(AccessAcl.id)))).scalar_one() > 0
    except Exception:
        # Tabela ausente (migração não aplicada): fica no legado, sem derrubar a requisição.
        logger.warning("tabela access_acl indisponível; mantendo o RBAC legado", exc_info=True)
        found = False
    _populated = (now, found)
    return found


async def legacy_screen_decision(db: AsyncSession, user_id: int, module: str, legacy_action: str, scope: str | None) -> Decision:
    """Decisão de tela pelo motor novo, com cache versionado (access_cache)."""
    version = await access_cache.current_version(db)
    key_resource = f"{module}:{scope or ''}"
    cached = access_cache.get_cached(version, user_id, legacy_action, "legacy-screen", key_resource)
    if cached is not None:
        return cached
    grants = await load_grants(db, user_id)
    decision = legacy_screen_allowed(grants, module, legacy_action, scope)
    access_cache.put_cached(version, user_id, legacy_action, "legacy-screen", key_resource, decision)
    return decision


async def resolve_screen_access(
    db: AsyncSession,
    user_id: int,
    module: str,
    legacy_action: str,
    scope: str | None,
    legacy_allowed: bool,
) -> bool:
    """Resultado final para a requisição, segundo o modo configurado."""
    mode = access_engine()
    if mode == "legacy" or not await _has_acls(db):
        return legacy_allowed
    decision = await legacy_screen_decision(db, user_id, module, legacy_action, scope)
    if decision.allowed != legacy_allowed:
        logger.warning(
            "divergência de acesso user=%s module=%s action=%s scope=%s legacy=%s acl=%s motivo=%s",
            user_id, module, legacy_action, scope, legacy_allowed, decision.allowed, decision.reason,
        )
    return legacy_allowed if mode == "shadow" else decision.allowed
