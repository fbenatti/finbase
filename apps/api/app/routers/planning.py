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
from app.models.destination import DestinationTable
from app.models.planning import FinancialType, PlanningEntry, PlanningVersion
from app.models.rules import Department
from app.services.trino_service import execute as trino_execute

router = APIRouter(prefix="/planning", tags=["planning"])

MONTHS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
LEVELS = [f"Departamento nível {i}" for i in range(1, 8)]
HEADERS = [*LEVELS, *MONTHS]
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


async def ensure_planning_paths_synced(db: AsyncSession) -> int:
    departments = {d.id: d for d in (await db.execute(select(Department))).scalars().all()}
    entries = (await db.execute(select(PlanningEntry).where(PlanningEntry.department_id != None))).scalars().all()
    updated = 0
    for entry in entries:
        dept = departments.get(entry.department_id)
        if dept:
            path: list[str] = []
            cur = dept
            seen = set()
            while cur and cur.id not in seen:
                seen.add(cur.id)
                path.append((cur.name or "").strip())
                cur = departments.get(cur.parent_id) if cur.parent_id else None
            path.reverse()
            if entry.department_levels != path:
                entry.department_levels = path
                updated += 1
    if updated:
        await db.commit()
    return updated


async def _type_for_write(code: str, db: AsyncSession) -> FinancialType:
    row = (await db.execute(select(FinancialType).where(FinancialType.code == code))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Tipificação financeira não encontrada")
    if not row.active:
        raise HTTPException(422, "Tipificação financeira inativa")
    if row.code in SYSTEM_CODES:
        raise HTTPException(422, "Realizado e forecast automático não aceitam carga manual")
    return row


async def _organization(db: AsyncSession) -> tuple[dict[int, Department], dict[int | None, list[Department]]]:
    departments = (await db.execute(select(Department))).scalars().all()
    by_id = {d.id: d for d in departments}
    children: dict[int | None, list[Department]] = defaultdict(list)
    for d in departments:
        children[d.parent_id].append(d)
    return by_id, children


def _cloud_origem_sql(columns: dict[str, str]) -> str | None:
    column = columns.get("cloud_origem")
    return f'LOWER(CAST("{column}" AS VARCHAR))' if column else None


def _cloud_filter_sql(cloud: str | None, columns: dict[str, str]) -> str:
    if not cloud:
        return ""
    slug = cloud.strip().lower()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise HTTPException(422, "Filtro de cloud inválido")
    expr = _cloud_origem_sql(columns)
    if not expr:
        raise HTTPException(422, "A tabela Gold não possui cloud_origem")
    if slug in {"gcp", "azure", "atlas"}:
        return f"AND {expr} LIKE '{slug}%'"
    return f"AND {expr} = '{slug}'"


def _levels(department: Department, departments: dict[int, Department]) -> list[str]:
    chain: list[str] = []
    current: Department | None = department
    while current:
        chain.append(current.name)
        current = departments.get(current.parent_id) if current.parent_id else None
    return list(reversed(chain))


async def _validate_rows(rows: list[PlanningRowIn], db: AsyncSession) -> list[dict[str, Any]]:
    departments, _ = await _organization(db)
    prepared: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    errors: list[str] = []
    for pos, row in enumerate(rows, 1):
        entity_type = row.entity_type.lower()
        if entity_type != "department":
            errors.append(f"Linha {pos}: entity_type deve ser department")
            continue
        department = departments.get(row.department_id)
        if not department or not department.active:
            errors.append(f"Linha {pos}: departamento inválido ou inativo")
            continue
        key = (entity_type, department.id)
        if key in seen:
            errors.append(f"Linha {pos}: entidade duplicada")
            continue
        seen.add(key)
        prepared.append({"entity_type": entity_type, "department_id": department.id, "project_id": None, "department_levels": _levels(department, departments), "project_name": None, "values": row.values})
    if errors:
        raise HTTPException(422, {"message": "Dados inválidos", "errors": errors})
    return prepared


async def _publish_version(fin_type: FinancialType, year: int, rows: list[dict[str, Any]], source: str, db: AsyncSession, filename: str | None = None, restored_from: int | None = None) -> PlanningVersion:
    current = (await db.execute(select(PlanningVersion).where(PlanningVersion.financial_type_id == fin_type.id, PlanningVersion.year == year, PlanningVersion.is_current == True))).scalars().all()
    next_version = max((v.version for v in current), default=0)
    all_versions = (await db.execute(select(func.max(PlanningVersion.version)).where(PlanningVersion.financial_type_id == fin_type.id, PlanningVersion.year == year))).scalar_one()
    version = int(all_versions or 0) + 1
    for old in current:
        old.is_current = False
    snapshot = PlanningVersion(financial_type_id=fin_type.id, year=year, version=version, is_current=True, source=source, source_filename=filename, restored_from_id=restored_from, classification_version_id=None)
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
    departments, _ = await _organization(db)
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Cadastro financeiro"; ws.append(HEADERS)
    for department in sorted((d for d in departments.values() if d.active), key=lambda d: _levels(d, departments)):
        levels = _levels(department, departments)
        ws.append([*levels, *([""] * (7 - len(levels))), *([0] * 12)])
    ws.freeze_panes = "A2"; ws.auto_filter.ref = ws.dimensions
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = max(14, min(35, max(len(str(c.value or "")) for c in col) + 2))
    content = io.BytesIO(); wb.save(content); content.seek(0)
    return StreamingResponse(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="modelo-cadastro-financeiro-{year}.xlsx"'})


@router.get("/orcamento/{year}/simulado")
async def download_simulated_budget(
    year: int,
    base_period: str = "202601",
    detailed: bool = False,
    detail_column: str | None = None,
    cloud: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Create a simulated annual forecast from a selected realized month.

    The consolidated file is import-ready.  The detailed file is an optional
    budget-defense view split by FOCUS ServiceName when that field exists.
    Use cloud=gcp to generate only GCP project rows (gcp-4p / gcp-ti).
    """
    if year < 2000 or year > 2100 or not re.fullmatch(r"\d{6}", base_period):
        raise HTTPException(422, "Ano ou período-base inválido")
    active_destinations = (await db.execute(select(DestinationTable).where(DestinationTable.active == True))).scalars().all()
    destination = next((item for item in active_destinations if "focus" in f"{item.name} {item.physical_name}".lower()), None)
    destination = destination or await db.get(DestinationTable, 1)
    if not destination or not destination.active:
        raise HTTPException(404, "Tabela Gold de realizado não encontrada")
    safe_table = destination.physical_name.replace("-", "_").replace(".", "_")
    try:
        schema_rows = await trino_execute(f"DESCRIBE iceberg.gold.{safe_table}", timeout_s=30)
        columns = {str(row.get("Column") or row.get("column_name") or "").lower(): str(row.get("Column") or row.get("column_name") or "") for row in schema_rows}
        if "_billing_period" not in columns or "realizado" not in columns:
            raise HTTPException(422, "A tabela Gold selecionada não possui _billing_period e realizado")
        level_columns = [columns.get(f"departamento_nivel_{level}") for level in range(1, 8)]
        level_select = [f'CAST("{column}" AS VARCHAR) AS departamento_nivel_{index}' if column else f'CAST(NULL AS VARCHAR) AS departamento_nivel_{index}' for index, column in enumerate(level_columns, 1)]
        suggested_column = next((column for key, column in columns.items() if key.replace("_", "") in {"servicename", "metercategory"}), None)
        selected_detail_column = columns.get((detail_column or "").lower()) or suggested_column
        detail_select = f', COALESCE(NULLIF(TRIM(CAST("{selected_detail_column}" AS VARCHAR)), \'\'), \'Sem sugestão FOCUS\') AS focus_detail_suggestion' if detailed and selected_detail_column else ", 'Sem sugestão FOCUS' AS focus_detail_suggestion" if detailed else ""
        type_filter = f"AND (tipo = 'realizado' OR tipo IS NULL)" if "tipo" in columns else ""
        cloud_filter = _cloud_filter_sql(cloud, columns)
        group_by = ", ".join(str(index) for index in range(1, 8 + (1 if detailed else 0)))
        source_rows = await trino_execute(f"""
            SELECT {', '.join(level_select)}{detail_select},
              SUM(COALESCE(TRY_CAST(realizado AS DOUBLE), 0)) AS value
            FROM iceberg.gold.{safe_table}
            WHERE CAST(_billing_period AS VARCHAR) = '{base_period}'
              {type_filter}
              {cloud_filter}
            GROUP BY {group_by}
        """, timeout_s=120)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, f"Não foi possível ler o realizado de {base_period}: {exc}")

    departments, _ = await _organization(db)
    by_path = {tuple(_levels(d, departments)): d for d in departments.values() if d.active}
    baseline_by_department: dict[int, float] = defaultdict(float)
    for row in source_rows:
        path = tuple(str(row.get(f"departamento_nivel_{level}") or "").strip() for level in range(1, 8))
        path = tuple(level for level in path if level)
        department = by_path.get(path)
        if department:
            baseline_by_department[department.id] += float(row.get("value") or 0)

    wb = openpyxl.Workbook(); ws = wb.active
    if detailed:
        ws.title = "Forecast detalhado"
        detail_label = detail_column or suggested_column or "MeterCategory"
        detailed_headers = [*LEVELS, f"{detail_label} FOCUS (sugestão)", "Valor de referência", *MONTHS]
        ws.append(detailed_headers)
        for row in source_rows:
            levels = [str(row.get(f"departamento_nivel_{level}") or "").strip() for level in range(1, 8)]
            value = float(row.get("value") or 0)
            ws.append([*levels, str(row.get("focus_detail_suggestion") or "Sem sugestão FOCUS"), value, *([value] * 12)])
    else:
        ws.title = "Forecast simulado"
        ws.append(HEADERS)
        for department in sorted((d for d in departments.values() if d.active), key=lambda d: _levels(d, departments)):
            levels = _levels(department, departments)
            monthly_value = baseline_by_department.get(department.id, 0.0)
            if cloud and monthly_value == 0:
                continue
            ws.append([*levels, *([""] * (7 - len(levels))), *([monthly_value] * 12)])
    ws.freeze_panes = "A2"; ws.auto_filter.ref = ws.dimensions
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = max(14, min(35, max(len(str(c.value or "")) for c in col) + 2))
    content = io.BytesIO(); wb.save(content); content.seek(0)
    export_kind = "detalhado" if detailed else "consolidado"
    cloud_suffix = f"-{cloud.lower()}" if cloud else ""
    return StreamingResponse(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="forecast-simulado-{export_kind}{cloud_suffix}-{year}-base-{base_period}.xlsx"'},
    )


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
    departments, children = await _organization(db)
    parsed: list[PlanningRowIn] = []
    errors: list[str] = []
    for line, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), 2):
        vals = list(values)
        if not any(v not in (None, "") for v in vals): continue
        names = [_cell_str(v) for v in vals[:7]]
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
        monthly: list[float] = []
        for month, value in zip(MONTHS, vals[7:19]):
            try:
                if value is None or str(value).strip() == "": raise ValueError()
                monthly.append(float(value))
            except (TypeError, ValueError): errors.append(f"Linha {line}: {month} deve ser numérico")
        if len(monthly) == 12: parsed.append(PlanningRowIn(entity_type="department", department_id=department.id, values=monthly))
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
    entries = (await db.execute(select(PlanningEntry).where(PlanningEntry.planning_version_id == version.id, PlanningEntry.entity_type == "department").order_by(PlanningEntry.department_id, PlanningEntry.month))).scalars().all()
    grouped: dict[tuple[str, int, int | None], dict[str, Any]] = {}
    for entry in entries:
        key = (entry.entity_type, entry.department_id, None)
        grouped.setdefault(key, {"entity_type": entry.entity_type, "department_id": entry.department_id, "department_levels": entry.department_levels, "values": [0.0] * 12})["values"][entry.month - 1] = entry.value
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
    entries = (await db.execute(select(PlanningEntry).where(PlanningEntry.planning_version_id == version.id, PlanningEntry.entity_type == "department"))).scalars().all()
    grouped: dict[tuple[str, int, int | None], dict[str, Any]] = {}
    for entry in entries:
        key = (entry.entity_type, entry.department_id, None)
        grouped.setdefault(key, {"entity_type": entry.entity_type, "department_id": entry.department_id, "project_id": entry.project_id, "department_levels": entry.department_levels, "project_name": entry.project_name, "values": [0.0] * 12})["values"][entry.month - 1] = entry.value
    snapshot = await _publish_version(fin_type, year, list(grouped.values()), "RESTORE", db, restored_from=version.id)
    return {"version": _out(snapshot)}
