"""Idempotent identity seed: system roles and bootstrap users.

Called on startup after the main business seed. Safe to run on every boot.
"""
import logging
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import hash_password
from app.models.identity import (
    User, Role, RolePermission, ALL_MODULES,
)

FINOPS_MODULE = "finops"
IDEAS_MODULE = "ideas"
ANOMALIES_MODULE = "anomalies"
PLANNING_MODULE = "planning"

logger = logging.getLogger(__name__)


async def seed_identity(db: AsyncSession) -> None:
    # --- Role de sistema: Administrador (todas as permissões) ---
    result = await db.execute(select(Role).where(Role.name == "Administrador"))
    admin_role = result.scalar_one_or_none()
    if admin_role is None:
        admin_role = Role(name="Administrador", description="Acesso total a todos os módulos", is_system=True)
        db.add(admin_role)
        await db.flush()
        for module in ALL_MODULES:
            for action in ("read", "create", "update", "delete"):
                db.add(RolePermission(role_id=admin_role.id, module=module, action=action))
        logger.info("Seed: role 'Administrador' criada com todas as permissões")

    # --- Role de sistema: Leitor Global (read em tudo) ---
    result = await db.execute(select(Role).where(Role.name == "Leitor Global"))
    reader_role = result.scalar_one_or_none()
    if reader_role is None:
        reader_role = Role(name="Leitor Global", description="Somente leitura em todos os módulos", is_system=True)
        db.add(reader_role)
        await db.flush()
        for module in ALL_MODULES:
            db.add(RolePermission(role_id=reader_role.id, module=module, action="read"))
        logger.info("Seed: role 'Leitor Global' criada")

    # Garantir permissões dos módulos adicionados depois da criação das roles de
    # sistema. Um módulo novo em ALL_MODULES não entra nas roles já existentes —
    # sem este backfill, todo mundo perde acesso ao módulo novo e o sintoma é um
    # 403 que parece defeito de RBAC.
    for modulo_backfill in (IDEAS_MODULE, FINOPS_MODULE, PLANNING_MODULE, ANOMALIES_MODULE):
        for role_name, actions in (
            ("Administrador", ("read", "create", "update", "delete")),
            ("Leitor Global", ("read",)),
        ):
            result = await db.execute(select(Role).where(Role.name == role_name))
            role = result.scalar_one_or_none()
            if role is None:
                continue
            for action in actions:
                exists = await db.execute(
                    select(RolePermission).where(
                        RolePermission.role_id == role.id,
                        RolePermission.module == modulo_backfill,
                        RolePermission.action == action,
                        RolePermission.scope.is_(None),
                    )
                )
                if exists.scalar_one_or_none() is None:
                    db.add(
                        RolePermission(
                            role_id=role.id, module=modulo_backfill, action=action
                        )
                    )
                    logger.info(
                        "Seed: permissão %s:%s adicionada à role '%s'",
                        modulo_backfill,
                        action,
                        role_name,
                    )

    # --- Role de sistema: Colaborador de ideias ---
    result = await db.execute(select(Role).where(Role.name == "Colaborador de ideias"))
    collab_role = result.scalar_one_or_none()
    if collab_role is None:
        collab_role = Role(
            name="Colaborador de ideias",
            description="Submete, vota e comenta ideias",
            is_system=True,
        )
        db.add(collab_role)
        await db.flush()
        for action in ("read", "create"):
            db.add(RolePermission(role_id=collab_role.id, module=IDEAS_MODULE, action=action))
        logger.info("Seed: role 'Colaborador de ideias' criada")

    # --- Usuário admin ---
    result = await db.execute(select(User).where(User.username == "admin"))
    if result.scalar_one_or_none() is None:
        password = settings.admin_initial_password
        if not password:
            if settings.env == "production":
                raise RuntimeError(
                    "ADMIN_INITIAL_PASSWORD não configurada e o ambiente é production. "
                    "Defina a senha inicial do usuário admin antes de iniciar."
                )
            password = "admin"  # dev-only fallback
            logger.warning("ADMIN_INITIAL_PASSWORD não definida; usando senha de desenvolvimento 'admin' (troque em seguida)")
        admin_user = User(
            username="admin",
            full_name="Administrador",
            password_hash=hash_password(password),
            auth_provider="local",
            is_active=True,
            must_change_password=True,
        )
        admin_user.roles.append(admin_role)
        db.add(admin_user)
        logger.info("Seed: usuário 'admin' criado")

    # --- Usuário sintético dev-admin (bypass de DEV / token master) ---
    result = await db.execute(select(User).where(User.username == "dev-admin"))
    if result.scalar_one_or_none() is None:
        dev_user = User(
            username="dev-admin",
            full_name="Dev Admin (sintético)",
            password_hash=hash_password(secrets.token_urlsafe(32)),  # inutilizável
            auth_provider="local",
            is_active=True,
            must_change_password=False,
        )
        dev_user.roles.append(admin_role)
        db.add(dev_user)
        logger.info("Seed: usuário 'dev-admin' criado")

    await db.commit()
