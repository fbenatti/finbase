from __future__ import annotations

import io
import re
from collections import defaultdict
from typing import Any

import openpyxl
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.planning import FinancialType, PlanningEntry, PlanningVersion
from app.models.rules import Department, Project

router = APIRouter(prefix="/planning", tags=["planning"])

MONTHS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
LEVELS = [f"Departamento nível {i}" for i in range(1, 6)]
HEADERS = [*LEVELS, "Projeto", *MONTHS]
SYSTEM_CODES = {"realizado", "forecast"}
DEFAULT_TYPES = [("realizado", "Realizado"), ("forecast", "Forecast (projetado)"), ("saldo-contratual", "Saldo contratual"), ("orcamento", "Orçamento"), ("forecast-manual", "Forecast manual")]


class FinancialTypeIn(BaseModel):
    code: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    name: str = Field(min_length=2, max_length=160)
    active: bool = True


class FinancialTypeUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    active: bool = True


class PlanningRowIn(BaseModel):
    entity_type: str
    department_id: int
    project_id: int | None = None
    values: list[float] = Field(min_length=12, max_length=12)


class PlanningSaveIn(BaseModel):
    rows: list[PlanningRowIn]


def _out(row: Any) -> dict[str, Any]:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


async def ensure_default_financial_types(db: AsyncSession) -> None:
    existing = set((await db.execute(select(FinancialType.code))).scalars().all())
    for code, name in DEFAULT_TYPES:
        if code not in existing:
            db.add(FinancialType(code=code, name=name, active=True))
    await db.commit()


async def _type_for_write(code: str, db: AsyncSession) -> FinancialType:
    row = (await db.execute(select(FinancialType).where(FinancialType.code == code))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Tipificação financeira não encontrada")
    if not row.active:
        raise HTTPException(422, "Tipificação financeira inativa")
    if row.code in SYSTEM_CODES:
        raise HTTPException(422, "Realizado e forecast automático não aceitam carga manual")
    return row


async def _organization(db: AsyncSession) -> tuple[dict[int, Department], dict[int, Project], dict[int | None, list[Department]]]:
    departments = (await db.execute(select(Department))).scalars().all()
    projects = (await db.execute(select(Project))).scalars().all()
    by_id = {d.id: d for d in departments}
    children: dict[int | None, list[Department]] = defaultdict(list)
    for d in departments:
        children[d.parent_id].append(d)
    return by_id, {p.id: p for p in projects}, children


def _levels(department: Department, departments: dict[int, Department]) -> list[str]:
    chain: list[str] = []
    current: Department | None = department
    while current:
        chain.append(current.name)
        current = departments.get(current.parent_id) if current.parent_id else None
    return list(reversed(chain))


async def _validate_rows(rows: list[PlanningRowIn], db: AsyncSession) -> list[dict[str, Any]]:
    departments, projects, _ = await _organization(db)
    prepared: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    errors: list[str] = []
    for pos, row in enumerate(rows, 1):
        entity_type = row.entity_type.lower()
        if entity_type not in {"department", "project"}:
            errors.append(f"Linha {pos}: entity_type deve ser department ou project")
            continue
        department = departments.get(row.department_id)
        if not department or not department.active:
            errors.append(f"Linha {pos}: departamento inválido ou inativo")
            continue
        project = projects.get(row.project_id) if row.project_id else None
        if entity_type == "project":
            if not project or not project.active or project.department_id != department.id:
                errors.append(f"Linha {pos}: projeto inválido, inativo ou não pertence ao departamento")
                continue
            key = (entity_type, project.id)
        else:
            if row.project_id is not None:
                errors.append(f"Linha {pos}: lançamento de departamento não pode ter projeto")
                continue
            key = (entity_type, department.id)
        if key in seen:
            errors.append(f"Linha {pos}: entidade duplicada")
            continue
        seen.add(key)
        prepared.append({"entity_type": entity_type, "department_id": department.id, "project_id": project.id if project else None, "department_levels": _levels(department, departments), "project_name": project.projeto_name if project else None, "values": row.values})
    if errors:
        raise HTTPException(422, {"message": "Dados inválidos", "errors": errors})
    return prepared


async def _publish_version(fin_type: FinancialType, year: int, rows: list[dict[str, Any]], source: str, db: AsyncSession, filename: str | None = None, restored_from: int | None = None) -> PlanningVersion:
    from app.models.destination import DestinationTable
    from app.models.classification import ClassificationVersion
    focus = (await db.execute(select(DestinationTable).where(DestinationTable.active == True, DestinationTable.physical_name.ilike("%focus%")).order_by(DestinationTable.id))).scalars().first()
    classification = None
    if focus:
        classification = (await db.execute(select(ClassificationVersion).where(ClassificationVersion.destination_table_id == focus.id, ClassificationVersion.status == "ACTIVE").order_by(ClassificationVersion.version.desc()))).scalars().first()
    current = (await db.execute(select(PlanningVersion).where(PlanningVersion.financial_type_id == fin_type.id, PlanningVersion.year == year, PlanningVersion.is_current == True))).scalars().all()
    next_version = max((v.version for v in current), default=0)
    all_versions = (await db.execute(select(func.max(PlanningVersion.version)).where(PlanningVersion.financial_type_id == fin_type.id, PlanningVersion.year == year))).scalar_one()
    version = int(all_versions or 0) + 1
    for old in current:
        old.is_current = False
    snapshot = PlanningVersion(financial_type_id=fin_type.id, year=year, version=version, is_current=True, source=source, source_filename=filename, restored_from_id=restored_from, classification_version_id=classification.id if classification else None)
    db.add(snapshot)
    await db.flush()
    for row in rows:
        for month, value in enumerate(row["values"], 1):
            db.add(PlanningEntry(planning_version_id=snapshot.id, entity_type=row["entity_type"], department_id=row["department_id"], project_id=row["project_id"], department_levels=row["department_levels"], project_name=row["project_name"], month=month, value=float(value)))
    await db.commit()
    await db.refresh(snapshot)
    return snapshot


@router.get("/types")
async def list_types(db: AsyncSession = Depends(get_db)):
    await ensure_default_financial_types(db)
    rows = (await db.execute(select(FinancialType).order_by(FinancialType.name))).scalars().all()
    return [_out(r) for r in rows]


@router.post("/types", status_code=201)
async def create_type(body: FinancialTypeIn, db: AsyncSession = Depends(get_db)):
    if (await db.execute(select(FinancialType.id).where(FinancialType.code == body.code))).scalar_one_or_none():
        raise HTTPException(409, "Código de tipificação já existe")
    row = FinancialType(**body.model_dump())
    db.add(row); await db.commit(); await db.refresh(row)
    return _out(row)


@router.put("/types/{type_id}")
async def update_type(type_id: int, body: FinancialTypeUpdate, db: AsyncSession = Depends(get_db)):
    row = await db.get(FinancialType, type_id)
    if not row: raise HTTPException(404, "Tipificação financeira não encontrada")
    row.name, row.active = body.name, body.active
    await db.commit(); await db.refresh(row)
    return _out(row)


@router.get("/template")
async def download_template(year: int, db: AsyncSession = Depends(get_db)):
    if year < 2000 or year > 2100: raise HTTPException(422, "Ano inválido")
    departments, projects, _ = await _organization(db)
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Cadastro financeiro"; ws.append(HEADERS)
    for department in sorted((d for d in departments.values() if d.active), key=lambda d: _levels(d, departments)):
        ws.append([*_levels(department, departments), *([""] * (5 - len(_levels(department, departments)))), "", *([0] * 12)])
    for project in sorted((p for p in projects.values() if p.active), key=lambda p: (_levels(departments[p.department_id], departments), p.projeto_name)):
        levels = _levels(departments[project.department_id], departments)
        ws.append([*levels, *([""] * (5 - len(levels))), project.projeto_name, *([0] * 12)])
    ws.freeze_panes = "A2"; ws.auto_filter.ref = ws.dimensions
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = max(14, min(35, max(len(str(c.value or "")) for c in col) + 2))
    content = io.BytesIO(); wb.save(content); content.seek(0)
    return StreamingResponse(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="modelo-cadastro-financeiro-{year}.xlsx"'})


def _cell_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


async def _parse_excel(raw: bytes, db: AsyncSession) -> list[PlanningRowIn]:
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
        sheet = workbook.active
    except Exception as exc:
        raise HTTPException(422, f"Arquivo Excel inválido: {exc}")
    header = [_cell_str(c) for c in next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), [])]
    if header != HEADERS:
        raise HTTPException(422, {"message": "Modelo Excel inválido", "errors": [f"Cabeçalhos esperados: {', '.join(HEADERS)}"]})
    departments, projects, children = await _organization(db)
    parsed: list[PlanningRowIn] = []
    errors: list[str] = []
    for line, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), 2):
        vals = list(values)
        if not any(v not in (None, "") for v in vals): continue
        names = [_cell_str(v) for v in vals[:5]]
        project_name = _cell_str(vals[5])
        if not names[0]: errors.append(f"Linha {line}: Departamento nível 1 é obrigatório"); continue
        parent: int | None = None; department: Department | None = None; broken = False
        for level, name in enumerate(names, 1):
            if not name:
                if any(names[level:]): errors.append(f"Linha {line}: há lacuna na hierarquia de departamentos"); broken = True
                break
            matches = [d for d in children[parent] if d.active and d.name == name]
            if len(matches) != 1: errors.append(f"Linha {line}: Departamento nível {level} inválido: {name}"); broken = True; break
            department = matches[0]; parent = department.id
        if broken or not department: continue
        project_id = None
        entity_type = "department"
        if project_name:
            matches = [p for p in projects.values() if p.active and p.department_id == department.id and p.projeto_name == project_name]
            if len(matches) != 1: errors.append(f"Linha {line}: projeto inválido para o departamento: {project_name}"); continue
            entity_type, project_id = "project", matches[0].id
        monthly: list[float] = []
        for month, value in zip(MONTHS, vals[6:18]):
            try:
                if value is None or str(value).strip() == "": raise ValueError()
                monthly.append(float(value))
            except (TypeError, ValueError): errors.append(f"Linha {line}: {month} deve ser numérico")
        if len(monthly) == 12: parsed.append(PlanningRowIn(entity_type=entity_type, department_id=department.id, project_id=project_id, values=monthly))
    if errors: raise HTTPException(422, {"message": "Importação rejeitada", "errors": errors})
    return parsed


@router.post("/{type_code}/{year}/import")
async def import_excel(type_code: str, year: int, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    if not file.filename or not file.filename.lower().endswith(".xlsx"): raise HTTPException(422, "Envie um arquivo .xlsx")
    fin_type = await _type_for_write(type_code, db)
    rows = await _parse_excel(await file.read(), db)
    prepared = await _validate_rows(rows, db)
    snapshot = await _publish_version(fin_type, year, prepared, "IMPORT_EXCEL", db, file.filename)
    return {"version": _out(snapshot), "rows": len(prepared)}


@router.get("/{type_code}/{year}")
async def planning_grid(type_code: str, year: int, version_id: int | None = None, db: AsyncSession = Depends(get_db)):
    fin_type = (await db.execute(select(FinancialType).where(FinancialType.code == type_code))).scalar_one_or_none()
    if not fin_type: raise HTTPException(404, "Tipificação financeira não encontrada")
    query = select(PlanningVersion).where(PlanningVersion.financial_type_id == fin_type.id, PlanningVersion.year == year)
    if version_id: query = query.where(PlanningVersion.id == version_id)
    else: query = query.where(PlanningVersion.is_current == True)
    version = (await db.execute(query.order_by(PlanningVersion.version.desc()))).scalars().first()
    if not version: return {"type": _out(fin_type), "version": None, "rows": []}
    entries = (await db.execute(select(PlanningEntry).where(PlanningEntry.planning_version_id == version.id).order_by(PlanningEntry.entity_type, PlanningEntry.department_levels, PlanningEntry.project_name, PlanningEntry.month))).scalars().all()
    grouped: dict[tuple[str, int, int | None], dict[str, Any]] = {}
    for entry in entries:
        key = (entry.entity_type, entry.department_id, entry.project_id)
        grouped.setdefault(key, {"entity_type": entry.entity_type, "department_id": entry.department_id, "project_id": entry.project_id, "department_levels": entry.department_levels, "project_name": entry.project_name, "values": [0.0] * 12})["values"][entry.month - 1] = entry.value
    return {"type": _out(fin_type), "version": _out(version), "rows": list(grouped.values())}


@router.post("/{type_code}/{year}")
async def save_planning(type_code: str, year: int, body: PlanningSaveIn, db: AsyncSession = Depends(get_db)):
    fin_type = await _type_for_write(type_code, db)
    prepared = await _validate_rows(body.rows, db)
    snapshot = await _publish_version(fin_type, year, prepared, "EDICAO_GRID", db)
    return {"version": _out(snapshot), "rows": len(prepared)}


@router.get("/{type_code}/{year}/versions")
async def list_versions(type_code: str, year: int, db: AsyncSession = Depends(get_db)):
    fin_type = (await db.execute(select(FinancialType).where(FinancialType.code == type_code))).scalar_one_or_none()
    if not fin_type: raise HTTPException(404, "Tipificação financeira não encontrada")
    rows = (await db.execute(select(PlanningVersion).where(PlanningVersion.financial_type_id == fin_type.id, PlanningVersion.year == year).order_by(PlanningVersion.version.desc()))).scalars().all()
    return [_out(r) for r in rows]


@router.post("/{type_code}/{year}/versions/{version_id}/restore")
async def restore_version(type_code: str, year: int, version_id: int, db: AsyncSession = Depends(get_db)):
    fin_type = await _type_for_write(type_code, db)
    version = await db.get(PlanningVersion, version_id)
    if not version or version.financial_type_id != fin_type.id or version.year != year: raise HTTPException(404, "Snapshot não encontrado")
    entries = (await db.execute(select(PlanningEntry).where(PlanningEntry.planning_version_id == version.id))).scalars().all()
    grouped: dict[tuple[str, int, int | None], dict[str, Any]] = {}
    for entry in entries:
        key = (entry.entity_type, entry.department_id, entry.project_id)
        grouped.setdefault(key, {"entity_type": entry.entity_type, "department_id": entry.department_id, "project_id": entry.project_id, "department_levels": entry.department_levels, "project_name": entry.project_name, "values": [0.0] * 12})["values"][entry.month - 1] = entry.value
    snapshot = await _publish_version(fin_type, year, list(grouped.values()), "RESTORE", db, restored_from=version.id)
    return {"version": _out(snapshot)}
