"""Provisionamento JIT e mapeamento de grupos — compartilhado por OIDC e SAML."""
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.identity import Group, User

logger = logging.getLogger(__name__)


async def jit_provision_user(
    db: AsyncSession,
    *,
    auth_provider: str,
    external_id: str,
    email: str | None,
    full_name: str | None,
    group_names: list[str],
    provider_config: dict,
) -> tuple[User, bool]:
    """Localiza ou cria o usuário (JIT) e sincroniza grupos externos.

    Retorna (user, created).
    - Localização por external_id ou email.
    - Grupos com external_id são sincronizados: auto-cria se provider_config
      auto_create_groups; remove vínculos de grupos externos ausentes do claim.
      Vínculos manuais (grupos sem external_id) nunca são tocados.
    """
    user = None
    result = await db.execute(select(User).where(User.external_id == external_id))
    user = result.scalar_one_or_none()
    if user is None and email:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

    created = user is None
    if created:
        user = User(
            username=external_id.lower().replace(" ", "_")[:120],
            email=email,
            full_name=full_name,
            auth_provider=auth_provider,
            external_id=external_id,
            is_active=True,
            must_change_password=False,
        )
        db.add(user)
    else:
        user.full_name = full_name or user.full_name
        user.email = email or user.email
        user.external_id = user.external_id or external_id
        user.is_active = True

    await db.flush()

    # recarrega com grupos carregados (evita lazy fora de greenlet)
    result = await db.execute(
        select(User).options(selectinload(User.groups)).where(User.id == user.id)
    )
    user = result.scalar_one()

    # --- Sincronização de grupos externos ---
    auto_create = bool(provider_config.get("auto_create_groups", True))
    current_external: set[str] = set()
    for name in group_names:
        if not name:
            continue
        result = await db.execute(select(Group).where(Group.external_id == name))
        group = result.scalar_one_or_none()
        if group is None and auto_create:
            group = Group(name=f"{auth_provider}:{name}", external_id=name,
                          description="Grupo criado automaticamente pelo SSO")
            db.add(group)
            await db.flush()
        if group is not None and group not in user.groups:
            user.groups.append(group)
        current_external.add(name)

    # Remove vínculos de grupos externos que sumiram do claim
    for group in list(user.groups):
        if group.external_id and group.external_id not in current_external:
            user.groups.remove(group)
            logger.info("SSO: removendo grupo externo %s do usuário %s", group.external_id, user.username)

    await db.flush()
    return user, created


async def touch_login(db: AsyncSession, user: User) -> None:
    user.last_login_at = datetime.now(timezone.utc)
