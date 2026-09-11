from __future__ import annotations

import io
import re
from collections import defaultdict
from datetime import date, datetime, timezone
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
DEFAULT_TYPES = [("realizado", "Realizado"), ("forecast", "Forecast (projetado)"), ("saldo-contratual", "Saldo contratual"), ("orcamento", "Orçamento"), ("forecast-manual", "Forecast manual"), ("base-line-contratual", "Base Line Contratual"), ("carta-credito", "Carta de Credito"), ("defesa-orcamentaria", "Defesa Orçamentária")]
EVENT_TYPES = {"CARGA_INICIAL", "DEFESA", "APROVACAO", "CORTE", "AJUSTE", "INCLUSAO_PROJETO", "REALOCACAO", "RESTAURACAO", "OUTRO"}


class PlanningRowIn(BaseModel):
    entity_type: str
    department_id: int
    values: list[float] = Field(min_length=12, max_length=12)


class PlanningSaveIn(BaseModel):
    rows: list[PlanningRowIn]
    title: str
    description: str
    event_type: str
    effective_date: date
    scope_id_orcamento: str | None = None
    scope_cloud: str | None = None


class PlanningRestoreIn(BaseModel):
    title: str
    description: str
    event_type: str = "RESTAURACAO"
    effective_date: date
    scope_id_orcamento: str | None = None
    scope_cloud: str | None = None


class BudgetPublishIn(BaseModel):
    id_orcamento: str
    cloud_scope: str | None = None
    title: str
    description: str
    effective_date: date


def _out(row: Any) -> dict[str, Any]:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


async def ensure_default_financial_types(db: AsyncSession) -> None:
    existing = set((await db.execute(select(FinancialType.code))).scalars().all())
    for code, name in DEFAULT_TYPES:
        if code not in existing:
            db.add(FinancialType(code=code, name=name, active=True))
    await db.commit()


async def ensure_default_budget_ids(db: AsyncSession) -> int:
    """Seed budget IDs on Cloud nodes without replacing user-maintained values."""
    departments = (await db.execute(select(Department))).scalars().all()
    by_id = {item.id: item for item in departments}
    showback = {"azure": "ID2531-AZURE", "gcp": "ID2532-GCP", "aws": "ID2533-AWS", "oci": "ID2534-OCI"}
    updated = 0
    for department in departments:
        parent = by_id.get(department.parent_id)
        if not parent or department.id_orcamento:
            continue
        cloud = (department.name or "").strip().lower()
        if parent.name.strip().lower() == "showback" and cloud in showback:
            department.id_orcamento = showback[cloud]
            updated += 1
        elif parent.name.strip().lower() == "chargeback" and cloud:
            department.id_orcamento = f"EXTERNO-{cloud.upper()}"
            updated += 1
    if updated:
        await db.commit()
    return updated


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


def _department_scope(department: Department, departments: dict[int, Department]) -> tuple[str | None, str | None]:
    chain: list[Department] = []
    current: Department | None = department
    while current:
        chain.append(current)
        current = departments.get(current.parent_id) if current.parent_id else None
    chain.reverse()
    budget_id = next((item.id_orcamento for item in reversed(chain) if item.id_orcamento), None)
    cloud = chain[2].name if len(chain) >= 3 else None
    return budget_id, cloud


async def _scope_rows(rows: list[dict[str, Any]], budget_id: str | None, cloud: str | None, db: AsyncSession) -> list[dict[str, Any]]:
    if not budget_id and not cloud:
        return rows
    departments, _ = await _organization(db)
    return [row for row in rows if row.get("department_id") in departments and (not budget_id or _department_scope(departments[row["department_id"]], departments)[0] == budget_id) and (not cloud or _department_scope(departments[row["department_id"]], departments)[1] == cloud)]


async def _ensure_defense_open(year: int, budget_id: str, cloud: str | None, db: AsyncSession) -> None:
    defense_type_id = (await db.execute(select(FinancialType.id).where(FinancialType.code == "defesa-orcamentaria"))).scalar_one_or_none()
    approved = (await db.execute(select(PlanningVersion.id).where(PlanningVersion.financial_type_id == defense_type_id, PlanningVersion.year == year, PlanningVersion.scope_id_orcamento == budget_id, PlanningVersion.scope_cloud == cloud, PlanningVersion.published_to_budget_at != None).limit(1))).scalar_one_or_none()
    if approved:
        raise HTTPException(422, "A defesa deste ID Orçamento/Cloud já foi aprovada; publique a alteração como revisão no módulo Orçamento")


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
        prepared.append({"entity_type": entity_type, "department_id": department.id, "department_levels": _levels(department, departments), "values": row.values})
    if errors:
        raise HTTPException(422, {"message": "Dados inválidos", "errors": errors})
    return prepared


def _validate_event(title: str, description: str, event_type: str, effective_date: date) -> tuple[str, str, str, date]:
    title, description, event_type = title.strip(), description.strip(), event_type.strip().upper()
    if not title or not description or not effective_date:
        raise HTTPException(422, "Título, descrição, tipo do evento e data efetiva são obrigatórios")
    if event_type not in EVENT_TYPES:
        raise HTTPException(422, "Tipo de evento inválido")
    return title, description, event_type, effective_date


async def _publish_version(fin_type: FinancialType, year: int, rows: list[dict[str, Any]], source: str, db: AsyncSession, *, title: str, description: str, event_type: str, effective_date: date, filename: str | None = None, restored_from: int | None = None, scope_id_orcamento: str | None = None, scope_cloud: str | None = None) -> PlanningVersion:
    if year <= 0:
        raise HTTPException(422, "Ano inválido")
    title, description, event_type, effective_date = _validate_event(title, description, event_type, effective_date)
    current_query = select(PlanningVersion).where(PlanningVersion.financial_type_id == fin_type.id, PlanningVersion.year == year, PlanningVersion.is_current == True)
    if fin_type.code == "defesa-orcamentaria" and scope_id_orcamento:
        current_query = current_query.where(PlanningVersion.scope_id_orcamento == scope_id_orcamento, PlanningVersion.scope_cloud == scope_cloud)
    current = (await db.execute(current_query)).scalars().all()
    next_version = max((v.version for v in current), default=0)
    all_versions = (await db.execute(select(func.max(PlanningVersion.version)).where(PlanningVersion.financial_type_id == fin_type.id, PlanningVersion.year == year))).scalar_one()
    version = int(all_versions or 0) + 1
    for old in current:
        old.is_current = False
    snapshot = PlanningVersion(financial_type_id=fin_type.id, year=year, version=version, is_current=True, source=source, source_filename=filename, restored_from_id=restored_from, classification_version_id=None, title=title, description=description, event_type=event_type, effective_date=effective_date, scope_id_orcamento=scope_id_orcamento, scope_cloud=scope_cloud)
    db.add(snapshot)
    await db.flush()
    for row in rows:
        for month, value in enumerate(row["values"], 1):
            db.add(PlanningEntry(planning_version_id=snapshot.id, entity_type=row["entity_type"], department_id=row["department_id"], department_levels=row["department_levels"], month=month, value=float(value)))
    await db.commit()
    await db.refresh(snapshot)
    return snapshot


@router.get("/types")
async def list_types(db: AsyncSession = Depends(get_db)):
    await ensure_default_financial_types(db)
    rows = (await db.execute(select(FinancialType).order_by(FinancialType.name))).scalars().all()
    return [_out(r) for r in rows]


@router.get("/template")
async def download_template(year: int, db: AsyncSession = Depends(get_db)):
    if year <= 0: raise HTTPException(422, "Ano inválido")
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
    if year <= 0 or not re.fullmatch(r"\d{6}", base_period):
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


@router.get("/type-years/{type_code}")
async def list_years(type_code: str, db: AsyncSession = Depends(get_db)):
    fin_type = (await db.execute(select(FinancialType).where(FinancialType.code == type_code))).scalar_one_or_none()
    if not fin_type: raise HTTPException(404, "Tipificação financeira não encontrada")
    rows = (await db.execute(select(PlanningVersion.year).where(PlanningVersion.financial_type_id == fin_type.id).distinct().order_by(PlanningVersion.year.desc()))).scalars().all()
    return rows


@router.get("/scopes/options")
async def planning_scope_options(db: AsyncSession = Depends(get_db)):
    departments, _ = await _organization(db)
    values = {_department_scope(item, departments) for item in departments.values() if item.active}
    return [{"id_orcamento": budget_id, "cloud": cloud} for budget_id, cloud in sorted(values, key=lambda item: ((item[0] or ""), (item[1] or ""))) if budget_id]


@router.post("/{type_code}/{year}/import")
async def import_excel(type_code: str, year: int, file: UploadFile = File(...), title: str = Form(...), description: str = Form(...), event_type: str = Form(...), effective_date: date = Form(...), scope_id_orcamento: str | None = Form(None), scope_cloud: str | None = Form(None), db: AsyncSession = Depends(get_db)):
    if not file.filename or not file.filename.lower().endswith(".xlsx"): raise HTTPException(422, "Envie um arquivo .xlsx")
    fin_type = await _type_for_write(type_code, db)
    rows = await _parse_excel(await file.read(), db)
    prepared = await _validate_rows(rows, db)
    if type_code == "defesa-orcamentaria" and not scope_id_orcamento:
        raise HTTPException(422, "ID Orçamento é obrigatório para publicar uma defesa")
    if type_code == "defesa-orcamentaria": await _ensure_defense_open(year, scope_id_orcamento, scope_cloud, db)
    prepared = await _scope_rows(prepared, scope_id_orcamento, scope_cloud, db)
    if not prepared: raise HTTPException(422, "A carga não possui departamentos no escopo selecionado")
    snapshot = await _publish_version(fin_type, year, prepared, "IMPORT_EXCEL", db, title=title, description=description, event_type=event_type, effective_date=effective_date, filename=file.filename, scope_id_orcamento=scope_id_orcamento, scope_cloud=scope_cloud)
    return {"version": _out(snapshot), "rows": len(prepared)}


async def _timeline(fin_type: FinancialType, year: int, db: AsyncSession) -> list[dict[str, Any]]:
    versions = (await db.execute(select(PlanningVersion).where(PlanningVersion.financial_type_id == fin_type.id, PlanningVersion.year == year).order_by(PlanningVersion.effective_date, PlanningVersion.created_at, PlanningVersion.version))).scalars().all()
    result: list[dict[str, Any]] = []
    previous_total: float | None = None
    for version in versions:
        total = float((await db.execute(select(func.coalesce(func.sum(PlanningEntry.value), 0)).where(PlanningEntry.planning_version_id == version.id))).scalar_one())
        delta = None if previous_total is None else total - previous_total
        result.append({**_out(version), "title": version.title or f"Versão legada v{version.version}", "description": version.description or "Snapshot sem narrativa cadastrada.", "event_type": version.event_type or "OUTRO", "effective_date": version.effective_date or version.created_at.date(), "annual_total": total, "delta": delta, "delta_pct": None if previous_total in (None, 0) else delta / previous_total * 100})
        previous_total = total
    return result


@router.get("/dashboard/overview")
async def dashboard_overview(year: int, type_codes: str | None = None, department_id: int | None = None, db: AsyncSession = Depends(get_db)):
    codes = [code for code in (type_codes.split(",") if type_codes else ["orcamento", "forecast-manual", "base-line-contratual", "carta-credito", "defesa-orcamentaria"]) if code]
    types = (await db.execute(select(FinancialType).where(FinancialType.code.in_(codes)))).scalars().all()
    type_by_code = {item.code: item for item in types}
    departments, children = await _organization(db)
    scoped_department_ids: set[int] | None = None
    scope_path: list[str] | None = None
    if department_id is not None:
        if department_id not in departments:
            raise HTTPException(404, "Departamento não encontrado")
        scoped_department_ids = set()
        pending = [department_id]
        while pending:
            current_id = pending.pop()
            if current_id in scoped_department_ids:
                continue
            scoped_department_ids.add(current_id)
            pending.extend(child.id for child in children.get(current_id, []))
        scope_path = _levels(departments[department_id], departments)
    scoped_clouds = {
        str(_department_scope(departments[item_id], departments)[1] or "").lower()
        for item_id in (scoped_department_ids or set())
    }
    # A seleção de um nó do organograma representa o orçamento herdado por
    # esse nó. Não podemos unir os IDs próprios de todos os descendentes: isso
    # faria, por exemplo, uma defesa EXTERNOS aparecer ao selecionar Azure.
    selected_budget_id = (
        str(_department_scope(departments[department_id], departments)[0] or "")
        if department_id is not None else ""
    )
    scoped_budget_ids = {selected_budget_id} if selected_budget_id else {
        budget_id
        for item_id in (scoped_department_ids or set())
        if (budget_id := str(_department_scope(departments[item_id], departments)[0] or ""))
    }

    async def scoped_total(version_id: int) -> float:
        entries = (await db.execute(select(PlanningEntry).where(PlanningEntry.planning_version_id == version_id))).scalars().all()
        return sum(float(entry.value or 0) for entry in entries if scoped_department_ids is None or entry.department_id in scoped_department_ids)

    cards, series, events = [], {}, []
    for code in codes:
        fin_type = type_by_code.get(code)
        if not fin_type: continue
        # ``year`` é sempre o exercício financiado. A defesa de 2026 pertence
        # a 2026, embora suas decisões tenham effective_date em 2025.
        timeline_year = year
        timeline = await _timeline(fin_type, timeline_year, db)
        if not timeline: continue
        # Há uma versão vigente por ID de orçamento. O cartão deve consolidar
        # todas as versões vigentes que pertencem ao escopo, e não escolher
        # arbitrariamente a última versão de outro ID.
        scoped_timeline = [
            row for row in timeline
            if scoped_department_ids is None
            or str(row.get("scope_id_orcamento") or "") in scoped_budget_ids
        ]
        if not scoped_timeline:
            continue
        current_versions = [row for row in scoped_timeline if row["is_current"]]
        if not current_versions:
            current_versions = [scoped_timeline[-1]]
        current = max(current_versions, key=lambda row: (str(row.get("effective_date") or ""), row["id"]))
        entries = []
        for version in current_versions:
            entries.extend((await db.execute(select(PlanningEntry).where(PlanningEntry.planning_version_id == version["id"]))).scalars().all())
        if scoped_department_ids is not None:
            entries = [entry for entry in entries if entry.department_id in scoped_department_ids]
        monthly = [0.0] * 12
        for entry in entries: monthly[entry.month - 1] += float(entry.value or 0)
        cards.append({"code": code, "name": fin_type.name, "current": current, "annual_total": sum(monthly), "monthly": monthly})
        series[code] = monthly
        previous_total: float | None = None
        for event in scoped_timeline:
            event_cloud = str(event.get("scope_cloud") or "").lower()
            event_budget_id = str(event.get("scope_id_orcamento") or "")
            # Um evento restrito a uma cloud não deve construir uma narrativa
            # artificial em outra. Ex.: defesa/inclusão Azure não aparece em GCP.
            if event_cloud and scoped_department_ids is not None and event_cloud not in scoped_clouds:
                continue
            if event_budget_id and scoped_department_ids is not None and event_budget_id not in scoped_budget_ids:
                continue
            event_total = await scoped_total(event["id"])
            event_delta = None if previous_total is None else event_total - previous_total
            # Eventos de inclusão, aporte ou corte só são relevantes para um
            # nó quando mudam seu orçamento. Isso esconde a inclusão de Aura
            # Pessoas ao navegar por uma diretoria sem esse projeto.
            if event_delta is not None and abs(event_delta) < 0.005 and event.get("event_type") in {"CORTE", "INCLUSAO_PROJETO", "AJUSTE", "REALOCACAO"}:
                continue
            scoped_event = {
                **event,
                "annual_total": event_total,
                "delta": event_delta,
                "delta_pct": None if previous_total in (None, 0) else event_delta / previous_total * 100,
                "type_code": code,
                "type_name": fin_type.name,
                "planning_year": timeline_year,
                "route": {"orcamento": "/budgets", "forecast-manual": "/manual-forecast", "base-line-contratual": "/contractual-baseline", "carta-credito": "/credit-letter", "defesa-orcamentaria": "/budget-defense"}.get(code, "/budgets"),
            }
            events.append(scoped_event)
            previous_total = event_total
    events.sort(key=lambda event: (str(event["effective_date"]), str(event["created_at"])), reverse=True)
    return {"year": year, "cards": cards, "series": series, "events": events, "scope": {"department_id": department_id, "path": scope_path, "included_departments": len(scoped_department_ids or departments)}}


@router.get("/{type_code}/{year}")
async def planning_grid(type_code: str, year: int, version_id: int | None = None, scope_id_orcamento: str | None = None, scope_cloud: str | None = None, db: AsyncSession = Depends(get_db)):
    fin_type = (await db.execute(select(FinancialType).where(FinancialType.code == type_code))).scalar_one_or_none()
    if not fin_type: raise HTTPException(404, "Tipificação financeira não encontrada")
    query = select(PlanningVersion).where(PlanningVersion.financial_type_id == fin_type.id, PlanningVersion.year == year)
    if version_id: query = query.where(PlanningVersion.id == version_id)
    else:
        query = query.where(PlanningVersion.is_current == True)
        if scope_id_orcamento: query = query.where(PlanningVersion.scope_id_orcamento == scope_id_orcamento)
        if scope_cloud: query = query.where(PlanningVersion.scope_cloud == scope_cloud)
    versions = (await db.execute(query.order_by(PlanningVersion.effective_date.desc(), PlanningVersion.version.desc()))).scalars().all()
    if not versions: return {"type": _out(fin_type), "version": None, "rows": []}
    # Orçamento/defesa podem ter uma versão vigente por ID de orçamento. Sem
    # filtro de ID, a grade deve compor todas elas; selecionar apenas a primeira
    # fazia os demais projetos parecerem zerados.
    version = versions[0]
    entries = (await db.execute(select(PlanningEntry).where(PlanningEntry.planning_version_id.in_([item.id for item in versions]), PlanningEntry.entity_type == "department").order_by(PlanningEntry.department_id, PlanningEntry.month))).scalars().all()
    grouped: dict[tuple[str, int, int | None], dict[str, Any]] = {}
    for entry in entries:
        key = (entry.entity_type, entry.department_id, None)
        grouped.setdefault(key, {"entity_type": entry.entity_type, "department_id": entry.department_id, "department_levels": entry.department_levels, "values": [0.0] * 12})["values"][entry.month - 1] = entry.value
    return {"type": _out(fin_type), "version": _out(version), "rows": list(grouped.values())}


@router.post("/{type_code}/{year}")
async def save_planning(type_code: str, year: int, body: PlanningSaveIn, db: AsyncSession = Depends(get_db)):
    fin_type = await _type_for_write(type_code, db)
    prepared = await _validate_rows(body.rows, db)
    if type_code == "defesa-orcamentaria" and not body.scope_id_orcamento:
        raise HTTPException(422, "ID Orçamento é obrigatório para publicar uma defesa")
    if type_code == "defesa-orcamentaria": await _ensure_defense_open(year, body.scope_id_orcamento, body.scope_cloud, db)
    prepared = await _scope_rows(prepared, body.scope_id_orcamento, body.scope_cloud, db)
    if not prepared: raise HTTPException(422, "A grade não possui departamentos no escopo selecionado")
    snapshot = await _publish_version(fin_type, year, prepared, "EDICAO_GRID", db, title=body.title, description=body.description, event_type=body.event_type, effective_date=body.effective_date, scope_id_orcamento=body.scope_id_orcamento, scope_cloud=body.scope_cloud)
    return {"version": _out(snapshot), "rows": len(prepared)}


@router.get("/{type_code}/{year}/versions")
async def list_versions(type_code: str, year: int, db: AsyncSession = Depends(get_db)):
    fin_type = (await db.execute(select(FinancialType).where(FinancialType.code == type_code))).scalar_one_or_none()
    if not fin_type: raise HTTPException(404, "Tipificação financeira não encontrada")
    rows = (await db.execute(select(PlanningVersion).where(PlanningVersion.financial_type_id == fin_type.id, PlanningVersion.year == year).order_by(PlanningVersion.version.desc()))).scalars().all()
    return [_out(r) for r in rows]


@router.get("/{type_code}/{year}/timeline")
async def planning_timeline(type_code: str, year: int, db: AsyncSession = Depends(get_db)):
    fin_type = (await db.execute(select(FinancialType).where(FinancialType.code == type_code))).scalar_one_or_none()
    if not fin_type: raise HTTPException(404, "Tipificação financeira não encontrada")
    return await _timeline(fin_type, year, db)


@router.post("/defesa-orcamentaria/{year}/versions/{version_id}/publish-budget")
async def publish_defense_to_budget(year: int, version_id: int, body: BudgetPublishIn, db: AsyncSession = Depends(get_db)):
    defense_type = (await db.execute(select(FinancialType).where(FinancialType.code == "defesa-orcamentaria"))).scalar_one_or_none()
    budget_type = (await db.execute(select(FinancialType).where(FinancialType.code == "orcamento"))).scalar_one_or_none()
    defense = await db.get(PlanningVersion, version_id)
    if not defense_type or not budget_type or not defense or defense.financial_type_id != defense_type.id or defense.year != year:
        raise HTTPException(404, "Defesa orçamentária não encontrada")
    if defense.published_to_budget_at:
        raise HTTPException(422, "Esta defesa já foi carregada no orçamento; novas alterações devem ser revisões orçamentárias")
    await _ensure_defense_open(year, body.id_orcamento, body.cloud_scope, db)
    departments, _ = await _organization(db)
    scope_ids = {item.id for item in departments.values() if _department_scope(item, departments) == (body.id_orcamento, body.cloud_scope) or (body.cloud_scope is None and _department_scope(item, departments)[0] == body.id_orcamento)}
    if not scope_ids:
        raise HTTPException(422, "Nenhum departamento encontrado para o ID Orçamento e Cloud informados")

    def group(entries: list[PlanningEntry]) -> dict[int, dict[str, Any]]:
        grouped: dict[int, dict[str, Any]] = {}
        for entry in entries:
            if entry.department_id is None: continue
            grouped.setdefault(entry.department_id, {"entity_type": "department", "department_id": entry.department_id, "department_levels": entry.department_levels, "values": [0.0] * 12})["values"][entry.month - 1] = float(entry.value or 0)
        return grouped

    current_budget = (await db.execute(select(PlanningVersion).where(PlanningVersion.financial_type_id == budget_type.id, PlanningVersion.year == year, PlanningVersion.is_current == True))).scalars().first()
    budget_entries = (await db.execute(select(PlanningEntry).where(PlanningEntry.planning_version_id == current_budget.id))).scalars().all() if current_budget else []
    defense_entries = (await db.execute(select(PlanningEntry).where(PlanningEntry.planning_version_id == defense.id, PlanningEntry.department_id.in_(scope_ids)))).scalars().all()
    if not defense_entries:
        raise HTTPException(422, "A defesa não possui valores no escopo selecionado")
    merged = group(budget_entries)
    for department_id in scope_ids: merged.pop(department_id, None)
    merged.update(group(defense_entries))
    snapshot = await _publish_version(budget_type, year, list(merged.values()), "DEFESA_APROVADA", db, title=body.title, description=body.description, event_type="APROVACAO", effective_date=body.effective_date)
    snapshot.scope_id_orcamento = body.id_orcamento
    snapshot.scope_cloud = body.cloud_scope
    defense.scope_id_orcamento = body.id_orcamento
    defense.scope_cloud = body.cloud_scope
    defense.published_to_budget_at = datetime.now(timezone.utc)
    defense.published_budget_version_id = snapshot.id
    await db.commit()
    await db.refresh(snapshot)
    await db.refresh(defense)
    return {"budget_version": _out(snapshot), "defense_version": _out(defense), "rows": len(defense_entries)}


@router.post("/{type_code}/{year}/versions/{version_id}/restore")
async def restore_version(type_code: str, year: int, version_id: int, body: PlanningRestoreIn, db: AsyncSession = Depends(get_db)):
    fin_type = await _type_for_write(type_code, db)
    version = await db.get(PlanningVersion, version_id)
    if not version or version.financial_type_id != fin_type.id or version.year != year: raise HTTPException(404, "Snapshot não encontrado")
    entries = (await db.execute(select(PlanningEntry).where(PlanningEntry.planning_version_id == version.id, PlanningEntry.entity_type == "department"))).scalars().all()
    grouped: dict[tuple[str, int], dict[str, Any]] = {}
    for entry in entries:
        key = (entry.entity_type, entry.department_id)
        grouped.setdefault(key, {"entity_type": entry.entity_type, "department_id": entry.department_id, "department_levels": entry.department_levels, "values": [0.0] * 12})["values"][entry.month - 1] = entry.value
    snapshot = await _publish_version(fin_type, year, list(grouped.values()), "RESTORE", db, title=body.title, description=body.description, event_type=body.event_type, effective_date=body.effective_date, restored_from=version.id, scope_id_orcamento=body.scope_id_orcamento or version.scope_id_orcamento, scope_cloud=body.scope_cloud or version.scope_cloud)
    return {"version": _out(snapshot)}
