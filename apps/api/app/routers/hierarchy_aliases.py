"""Catálogo de papéis, regras de alias por escopo e resolver de rótulos de nível."""
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.hierarchy_alias import HierarchyLevelAlias, HierarchyRole
from app.models.rules import Department
from app.services.hierarchy_aliases import load_resolver_inputs, resolve_levels, tree_depth
from app.services.shared_cost import department_path

router = APIRouter(tags=["hierarchy-aliases"])


class RoleIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    label_plural: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=300)
    slug: str | None = Field(default=None, max_length=60)


class RoleOut(BaseModel):
    id: int
    slug: str
    label: str
    label_plural: str | None
    description: str | None

    model_config = {"from_attributes": True}


class AliasIn(BaseModel):
    scope_department_id: int | None = None
    depth: int = Field(ge=1, le=50)
    role_id: int


class AliasOut(BaseModel):
    id: int
    scope_department_id: int | None
    scope_path: list[str]
    depth: int
    role_id: int


def _slugify(value: str) -> str:
    import unicodedata

    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", plain.lower()).strip("_")[:60] or "papel"


async def _departments(db: AsyncSession) -> dict[int, Department]:
    return {d.id: d for d in (await db.execute(select(Department))).scalars().all()}


def _alias_out(alias: HierarchyLevelAlias, departments: dict[int, Department]) -> AliasOut:
    scope_path = department_path(alias.scope_department_id, departments) if alias.scope_department_id else []
    return AliasOut(id=alias.id, scope_department_id=alias.scope_department_id, scope_path=scope_path, depth=alias.depth, role_id=alias.role_id)


async def _validate_alias(body: AliasIn, db: AsyncSession) -> None:
    if not await db.get(HierarchyRole, body.role_id):
        raise HTTPException(422, "Papel não encontrado")
    if body.scope_department_id is not None:
        departments = await _departments(db)
        if body.scope_department_id not in departments:
            raise HTTPException(422, "Nó de escopo não encontrado")
        scope_depth = len(department_path(body.scope_department_id, departments))
        if body.depth < scope_depth:
            raise HTTPException(422, f"O escopo está no nível {scope_depth}; a regra só vale do nível {scope_depth} para baixo")


# ── Catálogo de papéis ──────────────────────────────────────────────────────

@router.get("/governanca/hierarquia/papeis", response_model=list[RoleOut])
async def list_roles(db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(HierarchyRole).order_by(HierarchyRole.label))).scalars().all()


@router.post("/governanca/hierarquia/papeis", response_model=RoleOut, status_code=201)
async def create_role(body: RoleIn, db: AsyncSession = Depends(get_db)):
    role = HierarchyRole(
        slug=_slugify(body.slug or body.label), label=body.label.strip(),
        label_plural=(body.label_plural or "").strip() or None, description=body.description,
    )
    db.add(role)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Já existe um papel com esse identificador")
    await db.refresh(role)
    return role


@router.put("/governanca/hierarquia/papeis/{role_id}", response_model=RoleOut)
async def update_role(role_id: int, body: RoleIn, db: AsyncSession = Depends(get_db)):
    role = await db.get(HierarchyRole, role_id)
    if not role:
        raise HTTPException(404, "Papel não encontrado")
    # O slug é a referência estável do papel: renomear o rótulo não o muda.
    role.label = body.label.strip()
    role.label_plural = (body.label_plural or "").strip() or None
    role.description = body.description
    await db.commit()
    await db.refresh(role)
    return role


@router.delete("/governanca/hierarquia/papeis/{role_id}", status_code=204)
async def delete_role(role_id: int, db: AsyncSession = Depends(get_db)):
    role = await db.get(HierarchyRole, role_id)
    if not role:
        raise HTTPException(404, "Papel não encontrado")
    in_use = (await db.execute(select(HierarchyLevelAlias.id).where(HierarchyLevelAlias.role_id == role_id).limit(1))).scalar()
    if in_use:
        raise HTTPException(409, "Papel em uso por regras de alias; remova as regras antes")
    await db.delete(role)
    await db.commit()


# ── Regras de alias por escopo ──────────────────────────────────────────────

@router.get("/governanca/hierarquia/aliases-niveis", response_model=list[AliasOut])
async def list_aliases(db: AsyncSession = Depends(get_db)):
    departments = await _departments(db)
    aliases = (await db.execute(select(HierarchyLevelAlias))).scalars().all()
    out = [_alias_out(alias, departments) for alias in aliases]
    return sorted(out, key=lambda a: (len(a.scope_path), a.scope_path, a.depth))


@router.post("/governanca/hierarquia/aliases-niveis", response_model=AliasOut, status_code=201)
async def create_alias(body: AliasIn, db: AsyncSession = Depends(get_db)):
    await _validate_alias(body, db)
    alias = HierarchyLevelAlias(**body.model_dump())
    db.add(alias)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Já existe uma regra para esse escopo e nível")
    await db.refresh(alias)
    return _alias_out(alias, await _departments(db))


@router.put("/governanca/hierarquia/aliases-niveis/{alias_id}", response_model=AliasOut)
async def update_alias(alias_id: int, body: AliasIn, db: AsyncSession = Depends(get_db)):
    alias = await db.get(HierarchyLevelAlias, alias_id)
    if not alias:
        raise HTTPException(404, "Regra não encontrada")
    await _validate_alias(body, db)
    for field, value in body.model_dump().items():
        setattr(alias, field, value)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Já existe uma regra para esse escopo e nível")
    await db.refresh(alias)
    return _alias_out(alias, await _departments(db))


@router.delete("/governanca/hierarquia/aliases-niveis/{alias_id}", status_code=204)
async def delete_alias(alias_id: int, db: AsyncSession = Depends(get_db)):
    alias = await db.get(HierarchyLevelAlias, alias_id)
    if not alias:
        raise HTTPException(404, "Regra não encontrada")
    await db.delete(alias)
    await db.commit()


# ── Resolver (leitura dos painéis) ──────────────────────────────────────────

@router.get("/custos/hierarquia/rotulos-niveis")
async def resolve_level_labels(
    caminho: list[str] = Query(default=[], description="Caminho de nomes da raiz até o escopo atual"),
    db: AsyncSession = Depends(get_db),
):
    nodes, rules, roles = await load_resolver_inputs(db)
    return resolve_levels([c for c in caminho if c is not None], nodes, rules, roles, tree_depth(nodes))
