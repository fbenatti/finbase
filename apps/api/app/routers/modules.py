"""Administration of customer modules; the customer identity comes from the session."""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require
from app.database import get_db
from app.models.identity import User
from app.models.navigation import NavigationModule, NavigationCustomer, CustomerModule, NavigationMembership
from app.services.navigation import customer_for_user, module_snapshot, require_module_access

router = APIRouter(tags=["modules"])


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    slug: str = Field(min_length=1, max_length=100)


class ModuleUpdate(BaseModel):
    enabled: bool


class CatalogLabelUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=160)


class MembershipUpdate(BaseModel):
    customer_id: int = Field(gt=0)


async def customer_or_404(db, customer_id):
    customer = await db.get(NavigationCustomer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    return customer


@router.get("/navegacao")
async def navigation(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    customer_id = await customer_for_user(db, user.id)
    customer = await customer_or_404(db, customer_id)
    return {"customer_id": customer_id, "customer_name": customer.name, "items": await module_snapshot(db, customer_id, user_id=user.id)}


@router.get("/navegacao/acessos/{code}")
async def access(code: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await require_module_access(db, user.id, code)


@router.get("/administracao/modulos/clientes")
async def customers(db: AsyncSession = Depends(get_db), user: User = Depends(require("modules", "read"))):
    return [{"id": c.id, "name": c.name, "slug": c.slug} for c in (await db.execute(select(NavigationCustomer).order_by(NavigationCustomer.name))).scalars()]


@router.post("/administracao/modulos/clientes", status_code=201)
async def create_customer(body: CustomerCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require("modules", "create"))):
    name = body.name.strip()
    slug = body.slug.strip().lower()
    if not name or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise HTTPException(status_code=422, detail="Informe um nome e um identificador com letras, números e hífens.")
    if (await db.execute(select(NavigationCustomer).where(NavigationCustomer.slug == slug))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Identificador de cliente já cadastrado")
    customer = NavigationCustomer(name=name, slug=slug)
    db.add(customer)
    await db.commit()
    return {"id": customer.id, "name": customer.name, "slug": customer.slug}


@router.get("/administracao/modulos/clientes/{customer_id}")
async def customer_modules(customer_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require("modules", "read"))):
    await customer_or_404(db, customer_id)
    return await module_snapshot(db, customer_id)


@router.put("/administracao/modulos/clientes/{customer_id}/{code}")
async def update_module(customer_id: int, code: str, body: ModuleUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(require("modules", "update"))):
    await customer_or_404(db, customer_id)
    module = await db.get(NavigationModule, code)
    if module is None or not module.active:
        raise HTTPException(status_code=404, detail="Módulo não encontrado")
    if module.required and not body.enabled:
        raise HTTPException(status_code=422, detail="A administração de módulos deve permanecer disponível.")
    override = await db.get(CustomerModule, (customer_id, code))
    if override is None:
        override = CustomerModule(customer_id=customer_id, module_code=code)
        db.add(override)
    override.enabled = body.enabled
    override.updated_by = user.id
    await db.commit()
    return {"code": code, "enabled": body.enabled}


@router.put("/administracao/modulos/catalogo/{code}")
async def update_catalog_label(code: str, body: CatalogLabelUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(require("modules", "update"))):
    module = await db.get(NavigationModule, code)
    if module is None or not module.active:
        raise HTTPException(status_code=404, detail="Módulo não encontrado")
    label = (body.label or "").strip() or None
    if label is not None and len(label) < 2:
        raise HTTPException(status_code=422, detail="Informe pelo menos 2 caracteres.")
    module.custom_label = label
    await db.commit()
    return {"code": code, "label": label or module.label}


@router.get("/administracao/modulos/membros")
async def members(db: AsyncSession = Depends(get_db), user: User = Depends(require("modules", "read"))):
    memberships = {m.user_id: m.customer_id for m in (await db.execute(select(NavigationMembership))).scalars()}
    default = (await db.execute(select(NavigationCustomer.id).where(NavigationCustomer.slug == "default"))).scalar_one()
    return [{"user_id": u.id, "username": u.username, "customer_id": memberships.get(u.id, default)} for u in (await db.execute(select(User).order_by(User.username))).scalars()]


@router.put("/administracao/modulos/membros/{user_id}")
async def assign_member(user_id: int, body: MembershipUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(require("modules", "update"))):
    await customer_or_404(db, body.customer_id)
    if await db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    membership = await db.get(NavigationMembership, user_id)
    if membership is None:
        membership = NavigationMembership(user_id=user_id)
        db.add(membership)
    membership.customer_id = body.customer_id
    await db.commit()
    return {"user_id": user_id, "customer_id": body.customer_id}
