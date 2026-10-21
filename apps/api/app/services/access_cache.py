"""Fase 7: cache de decisões de acesso, invalidado por versão global.

- A versão fica em access_policy_version. Toda alteração de ACL ou escopo
  chama bump_version(). Cada leitura compara a versão, então a invalidação
  vale para todos os workers, sem depender de TTL.
- Mudanças de pertencimento a perfil ou grupo continuam passando pelo cache
  de RBAC de 60 s (rbac.py). O TTL deste cache é o mesmo, então a defasagem
  máxima é 60 s para essas mudanças.
"""
import time

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access.policy import Decision
from app.models.access import AccessPolicyVersion

CACHE_TTL_SECONDS = 60.0
_MAX_ENTRIES = 10_000

_cache: dict[tuple, tuple[float, Decision]] = {}


async def current_version(db: AsyncSession) -> int:
    row = (await db.execute(select(AccessPolicyVersion.version).where(AccessPolicyVersion.id == 1))).scalar_one_or_none()
    return int(row or 0)


async def bump_version(db: AsyncSession) -> int:
    await db.execute(update(AccessPolicyVersion).where(AccessPolicyVersion.id == 1).values(version=AccessPolicyVersion.version + 1))
    version = await current_version(db)
    _cache.clear()
    return version


def _key(version: int, user_id: int, operation: str, resource_type: str, resource: str) -> tuple:
    return (version, user_id, operation, resource_type, resource)


def get_cached(version: int, user_id: int, operation: str, resource_type: str, resource: str) -> Decision | None:
    entry = _cache.get(_key(version, user_id, operation, resource_type, resource))
    if entry is None or time.monotonic() - entry[0] > CACHE_TTL_SECONDS:
        return None
    return entry[1]


def put_cached(version: int, user_id: int, operation: str, resource_type: str, resource: str, decision: Decision) -> None:
    if len(_cache) >= _MAX_ENTRIES:
        _cache.clear()
    _cache[_key(version, user_id, operation, resource_type, resource)] = (time.monotonic(), decision)


def clear_cache() -> None:
    _cache.clear()
