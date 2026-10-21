"""Fase 3: decisão e filtro de linha contra Postgres real.

Roda só com TEST_ACCESS_DATABASE_URL apontando para um banco descartável, por
exemplo: postgresql+asyncpg://rbac:rbac@localhost:55432/rbac_test
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from sqlalchemy import Column, Integer, MetaData, Table, insert, select, text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.database import Base  # noqa: E402
import app.models.access  # noqa: E402,F401  (registra as tabelas novas)
import app.models.identity  # noqa: E402,F401
import app.models.rules  # noqa: E402,F401
from app.services.access_service import apply_row_filter, decide_for_user  # noqa: E402

URL = os.environ.get("TEST_ACCESS_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_ACCESS_DATABASE_URL não definido")

TABLES = [
    "user_account", "role", "role_role", "user_role", "group_account", "user_group",
    "group_role", "role_permission", "user_grant", "department",
    "access_scope", "access_acl", "access_acl_condition", "user_scope", "group_scope", "access_policy_version",
]

FACT = Table("fact_cost_test", MetaData(), Column("id", Integer, primary_key=True),
             Column("department_id", Integer, nullable=False))


def _tables():
    return [Base.metadata.tables[name] for name in TABLES]


async def _setup(conn):
    # Cria só as tabelas do teste, em ordem de dependência, sem ordenar o metadata inteiro.
    def recreate(c):
        for table in reversed(_tables()):
            table.drop(c, checkfirst=True)
        FACT.drop(c, checkfirst=True)
        for table in _tables():
            table.create(c)
        FACT.create(c)
    await conn.run_sync(recreate)


async def _seed(session):
    await session.execute(text("INSERT INTO role (id, name, is_system) VALUES (1, 'analista', false)"))
    await session.execute(text("INSERT INTO user_account (id, username, is_active, auth_provider, must_change_password, failed_attempts) VALUES (10, 'ana', true, 'local', false, 0), (11, 'sem', true, 'local', false, 0)"))
    await session.execute(text("INSERT INTO user_role (user_id, role_id) VALUES (10, 1)"))
    # árvore: 1 → 2 → 3 ; 4 fora
    await session.execute(text("INSERT INTO department (id, name, parent_id, active) VALUES (1,'raiz',NULL,true),(2,'ti',1,true),(3,'infra',2,true),(4,'rh',NULL,true)"))
    await session.execute(insert(FACT), [
        {"id": 1, "department_id": 1}, {"id": 2, "department_id": 2}, {"id": 3, "department_id": 3}, {"id": 4, "department_id": 4},
    ])
    await session.execute(text(
        "INSERT INTO access_acl (id, role_id, resource_type, resource, operation, effect, active) "
        "VALUES (1, 1, 'table', 'fact_cost_test', 'read', 'allow', true)"
    ))
    await session.execute(text(
        "INSERT INTO access_acl_condition (acl_id, column_name, scope_type, match_mode) "
        "VALUES (1, 'department_id', 'hierarchy', 'subtree')"
    ))
    await session.execute(text("INSERT INTO access_scope (id, scope_type, value) VALUES (1, 'hierarchy', '2')"))
    await session.execute(text(
        "INSERT INTO user_scope (user_id, scope_id, match_mode, reason) VALUES (10, 1, 'subtree', 'teste')"
    ))


def _run(coro_fn):
    async def main():
        engine = create_async_engine(URL)
        try:
            async with engine.begin() as conn:
                await _setup(conn)
            Session = async_sessionmaker(engine, expire_on_commit=False)
            async with Session() as session:
                await _seed(session)
                await session.commit()
            async with Session() as session:
                return await coro_fn(session)
        finally:
            await engine.dispose()
    return asyncio.run(main())


def test_usuario_ve_so_a_subarvore_do_escopo():
    async def scenario(session):
        decision = await decide_for_user(session, 10, "read", "table", "fact_cost_test")
        assert decision.allowed
        stmt = apply_row_filter(select(FACT.c.id).order_by(FACT.c.id), {"department_id": FACT.c.department_id}, decision.row_filter)
        return [r.id for r in (await session.execute(stmt)).all()]
    assert _run(scenario) == [2, 3]


def test_usuario_sem_escopo_e_negado():
    async def scenario(session):
        return (await decide_for_user(session, 11, "read", "table", "fact_cost_test")).allowed
    assert _run(scenario) is False


def test_escopo_vencido_nao_conta():
    async def scenario(session):
        past = datetime.now(timezone.utc) - timedelta(days=1)
        await session.execute(text("UPDATE user_scope SET expires_at = :p"), {"p": past})
        return (await decide_for_user(session, 10, "read", "table", "fact_cost_test")).allowed
    assert _run(scenario) is False


def test_negacao_explicita_vence_no_banco():
    async def scenario(session):
        await session.execute(text(
            "INSERT INTO access_acl (id, role_id, resource_type, resource, operation, effect, active) "
            "VALUES (2, 1, 'table', 'fact_cost_test', 'read', 'deny', true)"
        ))
        return (await decide_for_user(session, 10, "read", "table", "fact_cost_test")).allowed
    assert _run(scenario) is False


def test_cache_respeita_versao_da_politica():
    from app.services import access_cache

    async def scenario(session):
        await session.execute(text("INSERT INTO access_policy_version (id, version) VALUES (1, 0) ON CONFLICT (id) DO NOTHING"))
        access_cache.clear_cache()
        first = await decide_for_user(session, 10, "read", "table", "fact_cost_test", use_cache=True)
        # remove o escopo e confirma que a decisão antiga não volta do cache
        await session.execute(text("DELETE FROM user_scope WHERE user_id = 10"))
        stale = await decide_for_user(session, 10, "read", "table", "fact_cost_test", use_cache=True)
        await access_cache.bump_version(session)
        fresh = await decide_for_user(session, 10, "read", "table", "fact_cost_test", use_cache=True)
        return first.allowed, stale.allowed, fresh.allowed
    assert _run(scenario) == (True, True, False)
