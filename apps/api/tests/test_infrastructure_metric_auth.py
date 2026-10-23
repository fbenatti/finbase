from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import infrastructure_metrics


@pytest.mark.asyncio
async def test_connection_scoped_permission_accepts_only_granted_connection(monkeypatch):
    async def permissions(_db, _user_id):
        return {"infrastructure_metrics": {"read": {"connection:12"}}}

    monkeypatch.setattr(infrastructure_metrics, "get_effective_permissions", permissions)
    user = SimpleNamespace(id=7)
    await infrastructure_metrics._authorize_connection(user, object(), 12, "read")
    with pytest.raises(HTTPException) as denied:
        await infrastructure_metrics._authorize_connection(user, object(), 13, "read")
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_module_wide_permission_remains_compatible(monkeypatch):
    async def permissions(_db, _user_id):
        return {"infrastructure_metrics": {"create": {None}}}

    monkeypatch.setattr(infrastructure_metrics, "get_effective_permissions", permissions)
    await infrastructure_metrics._authorize_connection(
        SimpleNamespace(id=7), object(), 12, "create",
    )
