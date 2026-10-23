"""Avisos do sistema: a leitura de tarefas do painel vale para etl:read ou notifications:read."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.core import security
from app.core.authz_middleware import RBACMiddleware
from app.core.modules import ALL_MODULES
from app.core.rbac import invalidate_permissions_cache
from app.database import Base
from app.models.identity import Role, RolePermission, User


class AsyncAdapter:
    def __init__(self, session):
        self.session = session

    async def execute(self, *args, **kwargs):
        return self.session.execute(*args, **kwargs)


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    # Pela ordem de dependência: ordenar o metadata inteiro exigiria todos os modelos.
    names = ["user_account", "role", "role_permission", "group_account", "user_role", "user_group", "group_role", "role_role", "user_grant"]
    for name in names:
        Base.metadata.tables[name].create(engine)
    session = Session(engine, expire_on_commit=False)
    roles = {name: Role(name=name) for name in ("avisos", "etl", "nenhum")}
    session.add_all(roles.values())
    session.flush()
    session.add_all([
        RolePermission(role_id=roles["avisos"].id, module="notifications", action="read"),
        RolePermission(role_id=roles["etl"].id, module="etl", action="read"),
        RolePermission(role_id=roles["nenhum"].id, module="planning", action="read"),
    ])
    users = {name: User(id=index, username=name, roles=[role]) for index, (name, role) in enumerate(roles.items(), start=1)}
    session.add_all(users.values())
    session.commit()

    db = AsyncAdapter(session)

    class DBContext:
        async def __aenter__(self): return db
        async def __aexit__(self, *args): return False

    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "env", "test")
    monkeypatch.setattr("app.core.authz_middleware.async_session", DBContext)
    monkeypatch.setattr("app.core.access.engine.access_engine", lambda: "legacy")

    app = FastAPI()

    @app.get("/operacoes/execucoes/tarefas/")
    async def tasks(): return []

    @app.get("/operacoes/execucoes/situacao/{task_id}")
    async def task(task_id: int): return {"id": task_id}

    @app.post("/operacoes/execucoes/cancelar/{task_id}")
    async def cancel(task_id: int): return {"id": task_id}

    @app.get("/operacoes/execucoes/historico")
    async def history(): return []

    app.add_middleware(RBACMiddleware)
    invalidate_permissions_cache()
    headers = {
        name: {"Authorization": f"Bearer {security.create_access_token(user.id, user.username, [])}"}
        for name, user in users.items()
    }
    with TestClient(app) as test_client:
        yield test_client, headers
    invalidate_permissions_cache()
    session.close()
    engine.dispose()


def test_notifications_is_a_known_module():
    assert "notifications" in ALL_MODULES


@pytest.mark.parametrize("path", ["/operacoes/execucoes/tarefas/", "/operacoes/execucoes/situacao/7"])
def test_panel_reads_are_allowed_with_notifications_or_etl(client, path):
    test_client, headers = client
    assert test_client.get(path, headers=headers["avisos"]).status_code == 200
    assert test_client.get(path, headers=headers["etl"]).status_code == 200
    assert test_client.get(path, headers=headers["nenhum"]).status_code == 403


def test_notifications_does_not_open_other_operations(client):
    test_client, headers = client
    # Só as consultas do painel: o resto das operações continua exigindo etl.
    assert test_client.get("/operacoes/execucoes/historico", headers=headers["avisos"]).status_code == 403
    assert test_client.post("/operacoes/execucoes/cancelar/7", headers=headers["avisos"]).status_code == 403
