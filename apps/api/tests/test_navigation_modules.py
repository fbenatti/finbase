"""Customer availability, route registration and direct API enforcement (isolated SQLite)."""
import asyncio
import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.core import security
from app.core.authz_middleware import RBACMiddleware
from app.core.navigation_middleware import NavigationPolicyMiddleware
from app.core.rbac import invalidate_permissions_cache
from app.database import Base, get_db
from app.models.identity import User, Role, RolePermission
from app.models.navigation import NavigationCustomer, CustomerModule, NavigationMembership
from app.routers.modules import router
from app.services.navigation import CATALOG, sync_catalog

STORY = "planejamento_historia_dos_contratos"
BUDGET = "planejamento_orcamentos"


class AsyncAdapter:
    def __init__(self, session):
        self.session = session

    async def execute(self, *args, **kwargs):
        return self.session.execute(*args, **kwargs)

    async def get(self, *args, **kwargs):
        return self.session.get(*args, **kwargs)

    def add(self, value):
        self.session.add(value)

    async def flush(self):
        self.session.flush()

    async def commit(self):
        self.session.commit()


@pytest.fixture
def context(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    names = {"user_account", "role", "role_permission", "group_account", "user_role", "user_group", "group_role", "role_role", "user_grant", "navigation_module", "navigation_customer", "customer_module", "navigation_membership"}
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name in names])
    session = Session(engine, expire_on_commit=False)
    db = AsyncAdapter(session)
    asyncio.run(sync_catalog(db))
    admin_role = Role(name="Admin")
    planning_role = Role(name="Planning")
    scoped_role = Role(name="Contract reader")
    session.add_all([admin_role, planning_role, scoped_role])
    session.flush()
    from app.core.modules import ALL_MODULES
    for module in ALL_MODULES:
        for action in ("read", "create", "update", "delete"):
            session.add(RolePermission(role_id=admin_role.id, module=module, action=action))
    session.add(RolePermission(role_id=planning_role.id, module="planning", action="read"))
    session.add(RolePermission(role_id=scoped_role.id, module="planning", action="read", scope=STORY))
    users = [User(id=1, username="admin-test", roles=[admin_role]), User(id=2, username="customer-x", roles=[planning_role]), User(id=3, username="customer-y", roles=[planning_role]), User(id=4, username="scoped", roles=[scoped_role])]
    session.add_all(users)
    x, y = NavigationCustomer(name="Cliente X", slug="cliente-x"), NavigationCustomer(name="Cliente Y", slug="cliente-y")
    session.add_all([x, y]); session.flush()
    session.add_all([NavigationMembership(user_id=2, customer_id=x.id), NavigationMembership(user_id=3, customer_id=y.id), NavigationMembership(user_id=4, customer_id=x.id)])
    session.commit()
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "env", "test")
    class DBContext:
        async def __aenter__(self): return db
        async def __aexit__(self, *args): return False
    monkeypatch.setattr("app.core.navigation_middleware.async_session", DBContext)
    monkeypatch.setattr("app.core.authz_middleware.async_session", DBContext)
    app = FastAPI()
    async def override_db(): yield db
    app.dependency_overrides[get_db] = override_db
    app.include_router(router)
    @app.get("/planning/contract-story")
    async def secret_contract(): return {"contract": "allowed"}
    app.add_middleware(RBACMiddleware)
    app.add_middleware(NavigationPolicyMiddleware)
    invalidate_permissions_cache()
    headers = {u.id: {"Authorization": f"Bearer {security.create_access_token(u.id, u.username, [])}"} for u in users}
    with TestClient(app) as client:
        yield client, db, session, x.id, y.id, headers
    invalidate_permissions_cache()
    session.close(); engine.dispose()


def test_all_menus_have_unique_dedicated_routes_and_registered_renderers():
    assert len({entry["code"] for entry in CATALOG}) == len(CATALOG)
    assert len({entry["path"] for entry in CATALOG}) == len(CATALOG)
    assert all("?" not in entry["path"] and "#" not in entry["path"] for entry in CATALOG)
    assert any(entry["path"] == "/administracao/modulos" for entry in CATALOG)
    assert not any(entry["path"].startswith("/ia/") or entry["label"] == "Unit Economics" for entry in CATALOG)
    frontend = (Path(__file__).parents[2] / "web" / "src" / "App.tsx").read_text()
    renderer_list = re.search(r"const CATALOG_RENDERERS = \{([^}]+)\}", frontend).group(1)
    registered = {name.strip() for name in renderer_list.split(",")}
    assert {entry["renderer"] for entry in CATALOG} <= registered | {"ModuleIndex"}


def test_catalog_sync_is_idempotent_and_preserves_customer_choices(context):
    _, db, session, x, _, _ = context
    session.add(CustomerModule(customer_id=x, module_code=STORY, enabled=False)); session.commit()
    asyncio.run(sync_catalog(db)); asyncio.run(sync_catalog(db))
    assert session.get(CustomerModule, (x, STORY)).enabled is False
    assert len(list(session.scalars(select(NavigationCustomer).where(NavigationCustomer.slug == "default")))) == 1


def test_disabling_contract_history_affects_only_selected_customer(context):
    client, _, _, x, _, headers = context
    response = client.put(f"/modules/customers/{x}/{STORY}", json={"enabled": False}, headers=headers[1])
    assert response.status_code == 200
    assert client.get(f"/navigation/access/{STORY}", headers=headers[2]).status_code == 403
    assert client.get(f"/navigation/access/{STORY}", headers=headers[3]).status_code == 200
    assert client.get(f"/navigation/access/{BUDGET}", headers=headers[2]).status_code == 200
    assert client.get("/planning/contract-story", headers=headers[2]).status_code == 403
    forged = {**headers[2], "X-Finbase-Module": BUDGET}
    assert client.get("/planning/contract-story", headers=forged).status_code == 403


def test_group_disables_children_and_required_admin_cannot_be_disabled(context):
    client, _, _, x, _, headers = context
    assert client.put(f"/modules/customers/{x}/section_planejamento", json={"enabled": False}, headers=headers[1]).status_code == 200
    assert client.get(f"/navigation/access/{STORY}", headers=headers[2]).status_code == 403
    assert client.get(f"/navigation/access/{BUDGET}", headers=headers[2]).status_code == 403
    assert client.put(f"/modules/customers/{x}/administracao_modulos", json={"enabled": False}, headers=headers[1]).status_code == 422
    assert client.put(f"/modules/customers/{x}/section_administracao", json={"enabled": False}, headers=headers[1]).status_code == 422


def test_membership_cannot_be_selected_by_customer_query_and_scope_applies(context):
    client, _, _, x, y, headers = context
    result = client.get(f"/navigation?customer_id={y}", headers=headers[2]).json()
    assert result["customer_id"] == x
    result = client.get("/navigation", headers=headers[4]).json()
    leaves = [m for m in result["items"] if m["allowed"] and m["parent_code"]]
    assert [m["code"] for m in leaves] == [STORY]
    assert client.get("/planning/contract-story", headers={**headers[4], "X-Finbase-Module": STORY}).status_code == 200
    assert client.get("/planning/contract-story", headers={**headers[4], "X-Finbase-Module": BUDGET}).status_code == 403


def test_management_requires_permissions_and_assignment_changes_customer(context):
    client, _, _, x, y, headers = context
    assert client.get("/navigation").status_code == 401
    assert client.get("/modules/customers", headers=headers[2]).status_code == 403
    assert client.put(f"/modules/customers/{x}/{STORY}", json={"enabled": False}, headers=headers[2]).status_code == 403
    assert client.put("/modules/members/2", json={"customer_id": y}, headers=headers[1]).status_code == 200
    assert client.get("/navigation", headers=headers[2]).json()["customer_id"] == y
    assert client.post("/modules/customers", json={"name": "Novo cliente", "slug": "novo-cliente"}, headers=headers[1]).status_code == 201
    assert client.post("/modules/customers", json={"name": "Duplicado", "slug": "novo-cliente"}, headers=headers[1]).status_code == 409


def test_new_catalog_entry_is_registered_for_existing_customers(context, monkeypatch):
    client, db, session, x, _, headers = context
    import app.services.navigation as navigation_service
    added = dict(next(entry for entry in CATALOG if entry["code"] == STORY))
    added.update(code="planejamento_novo_menu", label="Novo menu", path="/planejamento/novo-menu", api_prefixes=[], position=899)
    monkeypatch.setattr(navigation_service, "CATALOG", [*CATALOG, added])
    asyncio.run(sync_catalog(db))
    response = client.get(f"/modules/customers/{x}", headers=headers[1])
    row = next(item for item in response.json() if item["code"] == added["code"])
    assert row["path"] == added["path"]
    assert row["configured_enabled"] is True
    assert client.get(f"/navigation/access/{added['code']}", headers=headers[2]).status_code == 200
