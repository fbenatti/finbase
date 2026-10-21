"""One deployment catalog feeds database registration, navigation and feature access."""
import json
import re
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.rbac import get_effective_permissions, has_permission
from app.models.navigation import NavigationModule, NavigationCustomer, CustomerModule, NavigationMembership


def load_catalog() -> list[dict]:
    entries = json.loads((Path(__file__).parents[1] / "core" / "menu_catalog.json").read_text())
    codes, paths = set(), set()
    for entry in entries:
        entry.setdefault("visible", True)
        code, path = entry["code"], entry["path"]
        if code in codes or path in paths or not path.startswith("/") or any(c in path for c in "?#"):
            raise ValueError(f"Invalid or duplicate menu: {code}")
        if not re.fullmatch(r"[a-z0-9_-]{1,60}", code) or not entry["renderer"]:
            raise ValueError(f"Invalid menu identity: {code}")
        if entry["parent_code"] and entry["parent_code"] not in codes:
            raise ValueError(f"Parent must precede menu: {code}")
        codes.add(code)
        paths.add(path)
    return entries


CATALOG = load_catalog()


async def sync_catalog(db: AsyncSession) -> None:
    existing = {row.code: row for row in (await db.execute(select(NavigationModule))).scalars()}
    for entry in CATALOG:
        row = existing.pop(entry["code"], None)
        if row is None:
            row = NavigationModule(code=entry["code"])
            db.add(row)
        for key, value in entry.items():
            setattr(row, key, value)
        row.active = True
        # Parents must exist before child foreign keys are inserted.
        await db.flush()
    for row in existing.values():
        row.active = False
    if (await db.execute(select(NavigationCustomer).where(NavigationCustomer.slug == "default"))).scalar_one_or_none() is None:
        db.add(NavigationCustomer(name="Cliente padrão", slug="default"))
    await db.commit()


async def customer_for_user(db: AsyncSession, user_id: int) -> int:
    membership = await db.get(NavigationMembership, user_id)
    if membership:
        return membership.customer_id
    default = (await db.execute(select(NavigationCustomer.id).where(NavigationCustomer.slug == "default"))).scalar_one()
    return default


def effective_enabled(code: str, modules: dict, overrides: dict, *, production: bool = False) -> bool:
    seen = set()
    while code:
        if code in seen or code not in modules:
            return False
        seen.add(code)
        row = modules[code]
        if not row.active or (production and row.development_only) or not overrides.get(code, True):
            return False
        code = row.parent_code
    return True


async def module_snapshot(db: AsyncSession, customer_id: int, *, user_id: int | None = None) -> list[dict]:
    rows = list((await db.execute(select(NavigationModule).where(NavigationModule.active.is_(True)).order_by(NavigationModule.position))).scalars())
    modules = {row.code: row for row in rows}
    overrides = {row.module_code: row.enabled for row in (await db.execute(select(CustomerModule).where(CustomerModule.customer_id == customer_id))).scalars()}
    perms = await get_effective_permissions(db, user_id) if user_id is not None else None
    result = []
    for row in rows:
        enabled = effective_enabled(row.code, modules, overrides, production=settings.env == "production")
        allowed = enabled and (perms is None or not row.permission_module or has_permission(perms, row.permission_module, "read", row.code))
        result.append({
            "code": row.code, "parent_code": row.parent_code, "label": row.label, "path": row.path,
            "renderer": row.renderer, "view_params": row.view_params, "legacy_path": row.legacy_path,
            "permission_module": row.permission_module, "permission_scope": row.code,
            "position": row.position, "icon": row.icon, "required": row.required,
            "visible": row.visible, "configured_enabled": overrides.get(row.code, True), "enabled": enabled, "allowed": bool(allowed),
        })
    for item in result:
        if item["renderer"] == "ModuleIndex":
            item["allowed"] = item["enabled"] and any(child["allowed"] for child in result if child["parent_code"] == item["code"] and child["visible"])
    return result


async def require_module_access(db: AsyncSession, user_id: int, code: str) -> dict:
    customer_id = await customer_for_user(db, user_id)
    item = next((m for m in await module_snapshot(db, customer_id, user_id=user_id) if m["code"] == code), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Módulo não encontrado")
    if not item["allowed"]:
        raise HTTPException(status_code=403, detail="Módulo indisponível para este cliente ou usuário.")
    return item
