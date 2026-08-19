import csv
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from app.database import get_db
from app.models.rules import EtlMapping, BusinessRule, ClassificationRule, Department, Project, OrganizationMoveAudit
from app.models.planning import PlanningEntry
from app.models.destination import DestinationColumn, SourceDestinationMapping
from app.services.classification_engine import map_legacy_field, normalize_rule_conditions
from app.services.cloud_scope import connection_cloud_scope
from app.schemas.schemas import (
    EtlMappingCreate, EtlMappingUpdate, EtlMappingOut,
    BusinessRuleCreate, BusinessRuleUpdate, BusinessRuleOut,
    ClassificationRuleCreate, ClassificationRuleUpdate, ClassificationRuleOut,
    DepartmentCreate, DepartmentUpdate, DepartmentOut,
    ProjectCreate, ProjectUpdate, ProjectOut,
    CsvImportRequest, CsvImportResult,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["rules"])

MAX_DEPARTMENT_LEVELS = 7


def _department_path(department_id: int, departments: dict[int, Department]) -> list[str]:
    path: list[str] = []
    current = departments.get(department_id)
    seen: set[int] = set()
    while current and current.id not in seen:
        seen.add(current.id)
        path.append(current.name)
        current = departments.get(current.parent_id) if current.parent_id else None
    return list(reversed(path))


def _subtree_ids(root_id: int, departments: dict[int, Department]) -> set[int]:
    children: dict[int, list[int]] = {}
    for department in departments.values():
        if department.parent_id is not None:
            children.setdefault(department.parent_id, []).append(department.id)
    ids, pending = set(), [root_id]
    while pending:
        current = pending.pop()
        if current in ids:
            continue
        ids.add(current)
        pending.extend(children.get(current, []))
    return ids


async def _refresh_planning_paths(db: AsyncSession, departments: dict[int, Department], department_ids: set[int]) -> int:
    if not department_ids:
        return 0
    entries = (await db.execute(select(PlanningEntry).where(PlanningEntry.department_id.in_(department_ids)))).scalars().all()
    for entry in entries:
        entry.department_levels = _department_path(entry.department_id, departments) if entry.department_id else []
    return len(entries)


async def _move_department(
    db: AsyncSession, department: Department, new_parent_id: int | None, departments: dict[int, Department],
) -> int:
    if new_parent_id == department.id:
        raise HTTPException(status_code=422, detail="Um departamento não pode ser pai de si mesmo")
    if new_parent_id is not None:
        parent = departments.get(new_parent_id)
        if not parent or not parent.active:
            raise HTTPException(status_code=422, detail="O novo departamento pai deve existir e estar ativo")
        if new_parent_id in _subtree_ids(department.id, departments):
            raise HTTPException(status_code=422, detail="A movimentação criaria um ciclo na hierarquia")
        if len(_department_path(new_parent_id, departments)) + 1 > MAX_DEPARTMENT_LEVELS:
            raise HTTPException(status_code=422, detail="A hierarquia suporta no máximo 7 níveis")
    old_parent_id = department.parent_id
    old_path = _department_path(department.id, departments)
    impacted_ids = _subtree_ids(department.id, departments)
    department.parent_id = new_parent_id
    new_path = _department_path(department.id, departments)
    for department_id in impacted_ids:
        if len(_department_path(department_id, departments)) > MAX_DEPARTMENT_LEVELS:
            department.parent_id = old_parent_id
            raise HTTPException(status_code=422, detail="A movimentação deixaria um descendente acima do nível 7")
    entries_updated = await _refresh_planning_paths(db, departments, impacted_ids)
    db.add(OrganizationMoveAudit(
        department_id=department.id, old_parent_id=old_parent_id, new_parent_id=new_parent_id,
        old_path=old_path, new_path=new_path, planning_entries_updated=entries_updated,
    ))
    return entries_updated

def _rule_output_type(rule_type: str, expression: str | None, conditions: list | None) -> str:
    """Business-rule outputs are always numeric calculation fields."""
    return "NUMERIC"


async def _ensure_rule_output_column(
    db: AsyncSession,
    destination_table_id: int | None,
    target_column: str | None,
    rule_type: str,
    expression: str | None,
    conditions: list | None,
) -> None:
    """Register derived rule fields in destination metadata exactly once."""
    if not destination_table_id or not target_column:
        return
    existing = (await db.execute(
        select(DestinationColumn).where(
            DestinationColumn.destination_table_id == destination_table_id,
            DestinationColumn.column_name == target_column,
        )
    )).scalar_one_or_none()
    if existing:
        # Rules are a calculation layer by contract. Keep old metadata in the
        # same numeric standard when a rule targets an existing output field.
        existing.data_type = "NUMERIC"
        return
    columns = (await db.execute(
        select(DestinationColumn.position).where(
            DestinationColumn.destination_table_id == destination_table_id,
        )
    )).scalars().all()
    db.add(DestinationColumn(
        destination_table_id=destination_table_id,
        column_name=target_column,
        data_type=_rule_output_type(rule_type, expression, conditions),
        position=(max(columns) if columns else 0) + 1,
    ))


# ---- EtlMapping ----

@router.get("/mappings/", response_model=list[EtlMappingOut])
async def list_mappings(
    connection_id: int | None = Query(None, description="Filter by connection/datasource"),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(EtlMapping).order_by(EtlMapping.priority)
    if connection_id is not None:
        stmt = stmt.where(
            (EtlMapping.connection_id == connection_id) | (EtlMapping.connection_id == None)
        )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/mappings/", response_model=EtlMappingOut, status_code=201)
async def create_mapping(data: EtlMappingCreate, db: AsyncSession = Depends(get_db)):
    mapping = EtlMapping(**data.model_dump())
    db.add(mapping)
    await db.commit()
    await db.refresh(mapping)
    return mapping


@router.put("/mappings/{mapping_id}", response_model=EtlMappingOut)
async def update_mapping(mapping_id: int, data: EtlMappingUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(EtlMapping).where(EtlMapping.id == mapping_id))
    mapping = result.scalar_one_or_none()
    if not mapping:
        raise HTTPException(status_code=404, detail="Mapping not found")
    update_data = data.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(mapping, k, v)
    await db.commit()
    await db.refresh(mapping)
    return mapping


@router.delete("/mappings/{mapping_id}", status_code=204)
async def delete_mapping(mapping_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(EtlMapping).where(EtlMapping.id == mapping_id))
    mapping = result.scalar_one_or_none()
    if not mapping:
        raise HTTPException(status_code=404)
    await db.delete(mapping)
    await db.commit()


# ---- BusinessRule ----

@router.get("/business-rules/", response_model=list[BusinessRuleOut])
async def list_business_rules(
    connection_id: int | None = Query(None, description="Filter by connection/datasource"),
    destination_table_id: int | None = Query(None, description="Filter by destination table"),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(BusinessRule).order_by(BusinessRule.priority)
    if connection_id is not None:
        stmt = stmt.where(
            (BusinessRule.connection_id == connection_id) | (BusinessRule.connection_id == None)
        )
    if destination_table_id is not None:
        stmt = stmt.where(
            (BusinessRule.destination_table_id == destination_table_id) | (BusinessRule.destination_table_id == None)
        )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/business-rules/", response_model=BusinessRuleOut, status_code=201)
async def create_business_rule(data: BusinessRuleCreate, db: AsyncSession = Depends(get_db)):
    rule = BusinessRule(**data.model_dump())
    db.add(rule)
    await db.flush()
    await _ensure_rule_output_column(
        db, rule.destination_table_id, rule.target_column,
        rule.rule_type, rule.expression, rule.conditions,
    )

    # Auto-create EtlMapping for the source→target column relationship
    cfg = data.config_json or {}
    src_col = cfg.get("source_column", "")
    # For CONDITIONAL rules, use the first condition's field as source
    if not src_col and data.conditions:
        src_col = str(data.conditions[0].get("field", ""))
    tgt_col = data.target_column

    if src_col and tgt_col and data.connection_id:
        # EtlMapping (legacy)
        mapping = EtlMapping(
            connection_id=data.connection_id,
            name=f"[Regra] {data.name}",
            source_column=str(src_col),
            target_column=tgt_col,
            transform_type="EXPRESSION",
            priority=data.priority,
            active=data.active,
        )
        db.add(mapping)

        # Business rules must not create source→destination relationships.
        # Relationships are explicit user configuration and are created only
        # from the Relationship screen.

    await db.commit()
    await db.refresh(rule)
    return rule


@router.put("/business-rules/{rule_id}", response_model=BusinessRuleOut)
async def update_business_rule(rule_id: int, data: BusinessRuleUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(BusinessRule).where(BusinessRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Business rule not found")
    update_data = data.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(rule, k, v)
    await db.flush()
    await _ensure_rule_output_column(
        db, rule.destination_table_id, rule.target_column,
        rule.rule_type, rule.expression, rule.conditions,
    )

    # Sync EtlMapping for source→target relationship
    cfg = (data.config_json or rule.config_json) or {}
    src_col = cfg.get("source_column", "")
    # For CONDITIONAL rules, use the first condition's field
    if not src_col and (data.conditions or rule.conditions):
        conds = data.conditions or rule.conditions
        if conds:
            src_col = str(conds[0].get("field", ""))
    tgt_col = data.target_column or rule.target_column
    conn_id = data.connection_id or rule.connection_id

    if src_col and tgt_col and conn_id:
        # Find existing mapping matching this rule's source+target for this connection
        map_result = await db.execute(
            select(EtlMapping).where(
                EtlMapping.connection_id == conn_id,
                EtlMapping.source_column == str(src_col),
                EtlMapping.target_column == tgt_col,
            )
        )
        existing_map = map_result.scalars().first()

        if existing_map:
            existing_map.name = f"[Regra] {rule.name}"
            existing_map.priority = rule.priority
            existing_map.active = rule.active
        else:
            db.add(EtlMapping(
                connection_id=conn_id,
                name=f"[Regra] {rule.name}",
                source_column=str(src_col),
                target_column=tgt_col,
                transform_type="EXPRESSION",
                priority=rule.priority,
                active=rule.active,
            ))

    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/business-rules/{rule_id}", status_code=204)
async def delete_business_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(BusinessRule).where(BusinessRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404)

    # Delete the auto-created EtlMapping for this rule's source→target
    cfg = (rule.config_json or {})
    src_col = cfg.get("source_column", "")
    if not src_col and rule.conditions:
        src_col = str(rule.conditions[0].get("field", "")) if isinstance(rule.conditions, list) and rule.conditions else ""
    tgt_col = rule.target_column
    conn_id = rule.connection_id
    if src_col and tgt_col and conn_id:
        map_result = await db.execute(
            select(EtlMapping).where(
                EtlMapping.connection_id == conn_id,
                EtlMapping.source_column == str(src_col),
                EtlMapping.target_column == tgt_col,
            )
        )
        auto_map = map_result.scalars().first()
        if auto_map:
            await db.delete(auto_map)

    await db.delete(rule)
    await db.commit()


# ---- ClassificationRule ----

def _sync_rule_pattern_fields(data: dict) -> dict:
    """Mirror conditions into legacy source_field/pattern for backward compatibility."""
    conditions = data.get("conditions")
    if not conditions or not isinstance(conditions, list):
        return data
    normalized = []
    for raw in conditions:
        field = map_legacy_field(str(raw.get("field", "")).strip())
        operator = str(raw.get("operator") or raw.get("op") or "=").strip()
        values = raw.get("values")
        if values is None and raw.get("value") is not None:
            values = [v.strip() for v in str(raw.get("value")).split("|") if v.strip()]
        elif isinstance(values, list):
            values = [str(v).strip() for v in values if str(v).strip()]
        else:
            values = []
        if field and values:
            normalized.append({
                "field": field,
                "operator": operator,
                "values": values,
                "logic": str(raw.get("logic") or "AND"),
            })
    if not normalized:
        return data
    data = {**data, "conditions": normalized}
    data["source_field"] = normalized[0]["field"]
    data["pattern_type"] = "multi_condition"
    data["pattern"] = " ; ".join(
        f'{c["field"]}{c.get("operator", "=")}{"|".join(c.get("values", []))}'
        for c in normalized
    )[:500]
    return data


def _classification_rule_out(rule: ClassificationRule) -> ClassificationRuleOut:
    out = ClassificationRuleOut.model_validate(rule)
    normalized = normalize_rule_conditions(rule)
    if normalized:
        return out.model_copy(update={"conditions": normalized})
    return out


async def _default_focus_table_id(db: AsyncSession) -> int | None:
    return (await db.execute(
        text("SELECT id FROM destination_table WHERE upper(name) = 'MODELO FOCUS' LIMIT 1")
    )).scalar()


@router.get("/classifications/", response_model=list[ClassificationRuleOut])
async def list_classifications(
    connection_id: int | None = Query(None, description="Filter by connection/datasource"),
    destination_table_id: int | None = Query(None, description="Filter by destination table"),
    department_id: int | None = Query(None, description="Filter by department"),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ClassificationRule).order_by(ClassificationRule.priority)
    if connection_id is not None:
        stmt = stmt.where(
            (ClassificationRule.connection_id == connection_id)
            | (ClassificationRule.connection_id == None)
        )
    if destination_table_id is not None:
        stmt = stmt.where(
            (ClassificationRule.destination_table_id == destination_table_id)
            | (ClassificationRule.destination_table_id == None)
        )
    if department_id is not None:
        stmt = stmt.where(ClassificationRule.department_id == department_id)
    result = await db.execute(stmt)
    return [_classification_rule_out(r) for r in result.scalars().all()]


@router.post("/classifications/", response_model=ClassificationRuleOut, status_code=201)
async def create_classification(data: ClassificationRuleCreate, db: AsyncSession = Depends(get_db)):
    department = (await db.execute(
        select(Department).where(
            Department.id == data.department_id,
            Department.active == True,
        )
    )).scalars().first()
    if department is None:
        raise HTTPException(status_code=422, detail="An active department is required for a classification rule")
    payload = _sync_rule_pattern_fields(data.model_dump())
    if payload.get("destination_table_id") is None:
        focus_id = await _default_focus_table_id(db)
        if focus_id is not None:
            payload["destination_table_id"] = focus_id
    rule = ClassificationRule(**payload)
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return _classification_rule_out(rule)


@router.put("/classifications/{rule_id}", response_model=ClassificationRuleOut)
async def update_classification(
    rule_id: int, data: ClassificationRuleUpdate, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(ClassificationRule).where(ClassificationRule.id == rule_id)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Classification rule not found")
    update_data = _sync_rule_pattern_fields(data.model_dump(exclude_unset=True))
    for k, v in update_data.items():
        setattr(rule, k, v)
    if rule.active and not rule.department_id:
        raise HTTPException(status_code=422, detail="An active classification rule must be associated with a department")
    if rule.department_id:
        department = (await db.execute(
            select(Department).where(
                Department.id == rule.department_id,
                Department.active == True,
            )
        )).scalars().first()
        if department is None:
            raise HTTPException(status_code=422, detail="Classification rule department was not found or is inactive")
    await db.commit()
    await db.refresh(rule)
    return _classification_rule_out(rule)


@router.delete("/classifications/{rule_id}", status_code=204)
async def delete_classification(rule_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ClassificationRule).where(ClassificationRule.id == rule_id)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404)
    await db.delete(rule)
    await db.commit()


# ---- Department ----

@router.get("/departments/", response_model=list[DepartmentOut])
async def list_departments(
    parent_id: int | None = Query(None, description="Filter by parent department"),
    flat: bool = Query(False, description="Return flat list instead of tree"),
    db: AsyncSession = Depends(get_db),
):
    if flat:
        stmt = select(Department)
        if parent_id is not None:
            stmt = stmt.where(Department.parent_id == parent_id)
        stmt = stmt.order_by(Department.name)
        result = await db.execute(stmt)
        deps = result.scalars().all()
        return [DepartmentOut.model_validate(d) for d in deps]

    all_result = await db.execute(select(Department).order_by(Department.name))
    all_deps = list(all_result.scalars().all())

    dep_map: dict[int, DepartmentOut] = {}
    for d in all_deps:
        dep_map[d.id] = DepartmentOut.model_validate(d)

    roots: list[DepartmentOut] = []
    for d in all_deps:
        dto = dep_map[d.id]
        if d.parent_id and d.parent_id in dep_map:
            dep_map[d.parent_id].children.append(dto)
        else:
            roots.append(dto)
        if parent_id is not None and d.id == parent_id:
            return [dto]

    if parent_id is not None:
        return []

    return roots


@router.post("/departments/", response_model=DepartmentOut, status_code=201)
async def create_department(data: DepartmentCreate, db: AsyncSession = Depends(get_db)):
    if data.parent_id is not None:
        departments = {department.id: department for department in (await db.execute(select(Department))).scalars().all()}
        if data.parent_id not in departments or not departments[data.parent_id].active:
            raise HTTPException(status_code=422, detail="O departamento pai deve existir e estar ativo")
        if len(_department_path(data.parent_id, departments)) >= MAX_DEPARTMENT_LEVELS:
            raise HTTPException(status_code=422, detail="A hierarquia suporta no máximo 7 níveis")
    dept = Department(**data.model_dump())
    db.add(dept)
    await db.commit()
    await db.refresh(dept)
    return DepartmentOut.model_validate(dept)


@router.put("/departments/{dept_id}", response_model=DepartmentOut)
async def update_department(dept_id: int, data: DepartmentUpdate, db: AsyncSession = Depends(get_db)):
    departments = {department.id: department for department in (await db.execute(select(Department))).scalars().all()}
    dept = departments.get(dept_id)
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found")
    update_data = data.model_dump(exclude_unset=True)
    if "parent_id" in update_data and update_data["parent_id"] != dept.parent_id:
        await _move_department(db, dept, update_data.pop("parent_id"), departments)
    for k, v in update_data.items():
        setattr(dept, k, v)
    await db.commit()
    await db.refresh(dept)
    return DepartmentOut.model_validate(dept)


@router.post("/departments/bootstrap-cloud-organization")
async def bootstrap_cloud_organization(db: AsyncSession = Depends(get_db)):
    """Create the standard cloud hierarchy and stage current roots under Showback/Azure.

    This deliberately does not trigger ETL reprocessing.  Existing department IDs,
    classification rules and planning values are preserved.
    """
    departments = list((await db.execute(select(Department))).scalars().all())
    by_id = {department.id: department for department in departments}

    def child(parent_id: int | None, name: str) -> Department:
        existing = next((department for department in by_id.values() if department.parent_id == parent_id and department.name == name), None)
        if existing:
            return existing
        department = Department(parent_id=parent_id, name=name, active=True)
        db.add(department)
        departments.append(department)
        return department

    consumption = child(None, "Consumo Cloud")
    await db.flush()
    by_id = {department.id: department for department in departments}
    showback = child(consumption.id, "Showback")
    chargeback = child(consumption.id, "Chargeback")
    await db.flush()
    providers = ("Azure", "GCP", "AWS", "OCI", "IBM")
    for branch in (showback, chargeback):
        for provider in providers:
            child(branch.id, provider)
    await db.flush()
    by_id = {department.id: department for department in departments}
    showback_azure = next(department for department in by_id.values() if department.parent_id == showback.id and department.name == "Azure")

    protected_ids = {consumption.id, showback.id, chargeback.id}
    protected_ids.update(department.id for department in by_id.values() if department.parent_id in {showback.id, chargeback.id})
    roots_to_move = [department for department in by_id.values() if department.parent_id is None and department.id not in protected_ids]
    moved: list[dict[str, object]] = []
    for department in roots_to_move:
        old_path = _department_path(department.id, by_id)
        department.parent_id = showback_azure.id
        moved.append({"id": department.id, "name": department.name, "old_path": old_path, "new_path": _department_path(department.id, by_id)})
    planning_entries_updated = await _refresh_planning_paths(db, by_id, set(by_id)) if moved else 0
    for item in moved:
        db.add(OrganizationMoveAudit(
            department_id=int(item["id"]), old_parent_id=None, new_parent_id=showback_azure.id,
            old_path=item["old_path"], new_path=item["new_path"], planning_entries_updated=0,
        ))
    await db.commit()
    return {
        "message": "Organograma preparado; nenhum reprocessamento foi executado.",
        "moved_directories": moved,
        "planning_entries_updated": planning_entries_updated,
        "reprocess_required": True,
    }


@router.delete("/departments/{dept_id}", status_code=204)
async def delete_department(dept_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Department).where(Department.id == dept_id))
    dept = result.scalar_one_or_none()
    if not dept:
        raise HTTPException(status_code=404)
    children_result = await db.execute(
        select(Department).where(Department.parent_id == dept_id)
    )
    for child in children_result.scalars().all():
        child.parent_id = None
    await db.delete(dept)
    await db.commit()


# ---- Project ----

@router.get("/projects/", response_model=list[ProjectOut])
async def list_projects(
    department_id: int | None = Query(None, description="Filter by department"),
    cloud: str | None = Query(None, description="Filter by cloud"),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Project)
    if department_id is not None:
        stmt = stmt.where(Project.department_id == department_id)
    if cloud is not None:
        stmt = stmt.where(Project.cloud == cloud)
    stmt = stmt.order_by(Project.projeto_name)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/projects/", response_model=ProjectOut, status_code=201)
async def create_project(data: ProjectCreate, db: AsyncSession = Depends(get_db)):
    proj = Project(**data.model_dump())
    db.add(proj)
    await db.commit()
    await db.refresh(proj)
    return proj


@router.put("/projects/{proj_id}", response_model=ProjectOut)
async def update_project(proj_id: int, data: ProjectUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).where(Project.id == proj_id))
    proj = result.scalar_one_or_none()
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    update_data = data.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(proj, k, v)
    await db.commit()
    await db.refresh(proj)
    return proj


@router.delete("/projects/{proj_id}", status_code=204)
async def delete_project(proj_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).where(Project.id == proj_id))
    proj = result.scalar_one_or_none()
    if not proj:
        raise HTTPException(status_code=404)
    await db.delete(proj)
    await db.commit()


# ---- CSV Import ----

def _parse_classification_conditions(condicoes_str: str) -> list[dict]:
    """Parse the businessRules CSV condition format into structured conditions.
    Format: 'field1=val1|val2 ; field2~val3' or 'field=value'
    Operators: = (exact), ~ (contains), | (OR between values), ; (AND between fields)
    """
    if not condicoes_str or condicoes_str.strip() == "":
        return []

    conditions = []
    # Split by ; (with or without surrounding spaces) for AND between fields
    import re
    and_parts = re.split(r"\s*;\s*", condicoes_str)

    for part in and_parts:
        part = part.strip()
        if not part:
            continue
        # Determine operator: ~ (contains) or = (exact)
        if "~" in part:
            field, _, rest = part.partition("~")
        elif "=" in part:
            field, _, rest = part.partition("=")
        else:
            continue

        operator = "~" if "~" in part else "="
        # Split values by | for OR
        values = [v.strip() for v in rest.split("|") if v.strip()]

        if field.strip() and values:
            conditions.append({
                "field": field.strip(),
                "operator": operator,
                "values": values,
                "logic": "AND",
            })

    return conditions


CLOUD_TO_CONNECTION: dict[str, int] = {
    "Azure-TI": 2,
    "Azure-4P": 1,
    "GCP-TI": 4,
    "GCP-4P": 3,
    "Atlas-MongoDB": 5,
}


def _adapt_conditions_to_destination(
    conditions: list[dict],
    relationship_map: dict[str, str],
) -> list[dict]:
    """Translate legacy source field names to destination column names."""
    identity_fields = {
        "pricingmodel", "chargetype", "resourcegroupname", "metercategory",
        "metersubcategory", "subscriptionname", "reservationname", "metername",
        "resourceid", "subaccountname", "subaccountid", "pricingcategory", "chargecategory",
        "servicesubcategory", "source_resource_group_name",
    }
    adapted: list[dict] = []
    for condition in conditions:
        field = str(condition.get("field", "")).strip()
        dest_field = relationship_map.get(field.lower(), field)
        if dest_field == field and field.lower() in identity_fields:
            dest_field = field
        adapted.append({
            **condition,
            "field": dest_field,
        })
    return adapted


@router.post("/departments/import", response_model=CsvImportResult, status_code=201)
async def import_csv_data(data: CsvImportRequest, db: AsyncSession = Depends(get_db)):
    """Import departments, projects, and business rules from finopsNew CSV files."""
    result = CsvImportResult(departments_created=0, projects_created=0, classification_rules_created=0)
    focus_table_id = await _default_focus_table_id(db)

    # Load the current relationship contract once.  A classification rule is
    # evaluated on Gold, never on the original provider header.
    relationship_rows = (await db.execute(
        select(SourceDestinationMapping).where(SourceDestinationMapping.active == True)
    )).scalars().all()
    relationship_by_connection: dict[int, dict[str, str]] = {}
    for mapping in relationship_rows:
        bucket = relationship_by_connection.setdefault(mapping.source_connection_id, {})
        # Keep the first mapping for an ambiguous source field. Explicit
        # technical overrides above handle the fields that must be preserved.
        bucket.setdefault(str(mapping.source_column).lower(), mapping.destination_column)

    # --- Helper: find a single department by name + parent ---
    async def _find_dept(name: str, parent_id: int | None = None) -> Department | None:
        stmt = select(Department).where(Department.name == name)
        if parent_id is None:
            stmt = stmt.where(Department.parent_id == None)
        else:
            stmt = stmt.where(Department.parent_id == parent_id)
        stmt = stmt.limit(1)
        res = await db.execute(stmt)
        return res.scalars().first()

    # --- Step 1: Import Departments hierarchy from diretorias_download.csv ---
    # Keep the exact CSV association (Cloud + Projeto -> Department) for the
    # classification import below.  Names alone are not enough because the
    # directory can legitimately contain the same project below two roots.
    directory_department_map: dict[tuple[str, str], list[int]] = {}
    directory_roots_by_cloud: dict[str, list[int]] = {}
    diretorias_path = Path(data.diretorias_path)
    if not diretorias_path.exists():
        result.errors.append(f"File not found: {data.diretorias_path}")
    else:
        direttoria_map: dict[str, int] = {}
        cache: set[str] = set()

        with open(diretorias_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=";")
            rows = list(reader)

        # 1a. Create root departments (Diretorias)
        seen_roots: dict[tuple[str, ...], int] = {}
        for row in rows:
            dn = row.get("Diretoria", "").strip()
            cloud_name = row.get("Cloud", "").strip()
            if not dn:
                continue
            root_key = (cloud_name, dn) if cloud_name in (data.cloud_root_map or {}) else (dn,)
            if root_key in seen_roots:
                continue

            parent_id = (data.cloud_root_map or {}).get(cloud_name) if cloud_name else None
            dept = await _find_dept(dn, parent_id=parent_id)
            if dept:
                seen_roots[root_key] = dept.id
            else:
                new_root = Department(
                    parent_id=parent_id,
                    name=dn,
                    centro_custo=row.get("CentrodeCusto", "").strip() or None,
                    cloud_filter=cloud_name or None,
                    ambiente=row.get("Origem", "").strip() or None,
                    custom_fields={
                        "tipo": "Diretoria",
                        "DiretorResp": row.get("DiretorResp", "").strip(),
                        "VP": row.get("VP", "").strip(),
                        "GerCDG": row.get("GerCDG", "").strip(),
                        "RespFinops": row.get("RespFinops", "").strip(),
                        "RespFinanceiro": row.get("RespFinanceiro", "").strip(),
                    },
                )
                db.add(new_root)
                await db.flush()
                seen_roots[root_key] = new_root.id
                result.departments_created += 1

            if cloud_name:
                directory_roots_by_cloud.setdefault(cloud_name, [])
                if seen_roots[root_key] not in directory_roots_by_cloud[cloud_name]:
                    directory_roots_by_cloud[cloud_name].append(seen_roots[root_key])

        direttoria_map = seen_roots

        # 1b. Create child departments (Projetos) under their Diretoria
        seen_children: set[str] = set()
        for row in rows:
            dn = row.get("Diretoria", "").strip()
            pn = row.get("Projeto", "").strip()
            cloud = row.get("Cloud", "").strip()
            if not dn or not pn or pn.upper() == "NA":
                continue

            parent_id = direttoria_map.get(
                (cloud, dn) if cloud in (data.cloud_root_map or {}) else (dn,)
            )
            if parent_id is None:
                continue

            child_key = f"{parent_id}|{pn}"
            if child_key in seen_children:
                continue
            seen_children.add(child_key)

            dept = await _find_dept(pn, parent_id=parent_id)
            if dept:
                key = (row.get("Cloud", "").strip(), pn)
                directory_department_map.setdefault(key, [])
                if dept.id not in directory_department_map[key]:
                    directory_department_map[key].append(dept.id)
                continue
            child = Department(
                parent_id=parent_id,
                name=pn,
                centro_custo=row.get("CentrodeCusto", "").strip() or None,
                cloud_filter=row.get("Cloud", "").strip() or None,
                ambiente=row.get("Origem", "").strip() or None,
                custom_fields={
                    "tipo": "Projeto",
                    "DiretorResp": row.get("DiretorResp", "").strip(),
                    "GerSR": row.get("GerSR", "").strip(),
                    "ContaContabil": row.get("ContaContabil", "").strip(),
                    "IDOrcamento": row.get("IDOrcamento", "").strip(),
                    "MatriculaResp": row.get("MatriculaResp", "").strip(),
                },
            )
            db.add(child)
            await db.flush()
            result.departments_created += 1
            key = (row.get("Cloud", "").strip(), pn)
            directory_department_map.setdefault(key, [])
            directory_department_map[key].append(child.id)

        await db.commit()

    # --- Step 2: Import Projects from projetos_download.csv ---
    projetos_path = Path(data.projetos_path)
    if not projetos_path.exists():
        result.errors.append(f"File not found: {data.projetos_path}")
    else:
        with open(projetos_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=";")
            proj_rows = list(reader)

        seen_proj: set[str] = set()
        for row in proj_rows:
            pn = row.get("Projeto", "").strip()
            cloud = row.get("Cloud", "").strip()
            sub_rg = row.get("SubscriptionRG", "").strip()

            if not pn:
                continue

            # Find department (child) by name — try cloud filter then fallback
            stmt = select(Department).where(Department.name == pn, Department.parent_id != None)
            if cloud:
                stmt = stmt.where(Department.cloud_filter == cloud)
            stmt = stmt.order_by(Department.id).limit(1)
            res = await db.execute(stmt)
            dept = res.scalars().first()

            if dept is None and cloud:
                stmt = select(Department).where(Department.name == pn, Department.parent_id != None)
                stmt = stmt.order_by(Department.id).limit(1)
                res = await db.execute(stmt)
                dept = res.scalars().first()

            if dept is None:
                continue

            proj_key = f"{dept.id}|{pn}|{sub_rg}"
            if proj_key in seen_proj:
                continue
            seen_proj.add(proj_key)

            existing = await db.execute(
                select(Project).where(Project.department_id == dept.id, Project.projeto_name == pn).limit(1)
            )
            if existing.scalars().first() is None:
                db.add(Project(
                    department_id=dept.id,
                    projeto_name=pn,
                    cloud=cloud,
                    subscription_rg=sub_rg,
                ))
                await db.flush()
                result.projects_created += 1

        await db.commit()

    # --- Step 3: Import Classification Rules from businessRules_download.csv ---
    br_path = Path(data.business_rules_path)
    if not br_path.exists():
        result.errors.append(f"File not found: {data.business_rules_path}")
    else:
        with open(br_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=";")
            br_rows = list(reader)

        seen_rules: set[str] = set()
        for row in br_rows:
            cloud = row.get("Cloud", "").strip()
            project_name = row.get("Projeto", "").strip()
            conditions_raw = row.get("Condicoes", "").strip()
            classificacao = row.get("Classificacao", "").strip() or ""
            ordem = row.get("Ordem", "").strip()

            if not conditions_raw:
                continue

            parsed_conditions = _parse_classification_conditions(conditions_raw)
            if not parsed_conditions:
                continue

            connection_id = CLOUD_TO_CONNECTION.get(cloud)
            cloud_scope = connection_cloud_scope(cloud) if cloud else None
            parsed_conditions = _adapt_conditions_to_destination(
                parsed_conditions,
                relationship_by_connection.get(connection_id, {}),
            )

            # Find department/project from project_name
            dept_id = None
            proj_id = None
            if project_name:
                csv_candidates = directory_department_map.get((cloud, project_name), [])
                dept = None
                if csv_candidates:
                    # The source directory order is deterministic and is the
                    # only available tie-breaker when identical rule
                    # conditions were registered for multiple cost objects.
                    dept = await db.get(Department, csv_candidates[0])
                if dept is None:
                    stmt = select(Department).where(Department.name == project_name, Department.parent_id != None)
                    if cloud:
                        stmt = stmt.where(Department.cloud_filter == cloud)
                    stmt = stmt.order_by(Department.id).limit(1)
                    dr = await db.execute(stmt)
                    dept = dr.scalars().first()
                # Some department master-data is intentionally shared across
                # providers and has no cloud_filter.  Do not leave a rule
                # unlinked merely because the CSV carries a cloud value.
                if dept is None and cloud:
                    dr = await db.execute(
                        select(Department).where(
                            Department.name == project_name,
                            Department.parent_id != None,
                        ).order_by(Department.id).limit(1)
                    )
                    dept = dr.scalars().first()
                # Atlas rules occasionally identify a more specific object
                # than the directory CSV.  When that CSV has one and only
                # one owning Diretoria for the provider, create the missing
                # child there instead of emitting an unlinked rule.
                if dept is None:
                    roots = directory_roots_by_cloud.get(cloud, [])
                    if len(roots) == 1:
                        dept = Department(
                            parent_id=roots[0], name=project_name,
                            cloud_filter=cloud or None,
                            custom_fields={"source": "businessRules_download.csv", "association": "provider_directory_fallback"},
                        )
                        db.add(dept)
                        await db.flush()
                        result.departments_created += 1
                if dept:
                    dept_id = dept.id

                pr = await db.execute(
                    select(Project).where(Project.projeto_name == project_name).limit(1)
                )
                proj = pr.scalars().first()
                if proj:
                    proj_id = proj.id

            # A project label alone is not an organizational allocation.  Do
            # not import a new active rule unless its target Department was
            # resolved from the directory hierarchy.
            if dept_id is None:
                result.errors.append(
                    f"Classification '{project_name or classificacao}' was not imported: department association was not found"
                )
                continue

            # Build rule name and priority
            match_value = classificacao or project_name
            priority = int(ordem) if ordem and ordem.isdigit() else 0

            # Derive source_field from the first condition field
            source_field = parsed_conditions[0]["field"]

            rule_key = f"{cloud}|{project_name}|{conditions_raw}"
            if rule_key in seen_rules:
                continue
            seen_rules.add(rule_key)

            rule_name = f"{project_name or cloud}_{classificacao or 'class'}".strip("_")[:200]

            existing_r = await db.execute(
                select(ClassificationRule).where(
                    ClassificationRule.name == rule_name,
                    ClassificationRule.match_value == match_value,
                ).limit(1)
            )
            if existing_r.scalars().first() is None:
                db.add(ClassificationRule(
                    connection_id=connection_id,
                    destination_table_id=focus_table_id,
                    department_id=dept_id,
                    project_id=proj_id,
                    name=rule_name,
                    source_field=source_field,
                    pattern=conditions_raw[:500],
                    pattern_type="multi_condition",
                    match_value=match_value[:200] if match_value else source_field[:200],
                    target_field="departamento",
                    conditions=parsed_conditions,
                    priority=priority,
                    cloud_scope=cloud_scope,
                    active=True,
                ))
                await db.flush()
                result.classification_rules_created += 1

            # The cloning stage converts reservation purchases into a
            # realized Usage row plus a synthetic Return row.  Preserve the
            # source CSV rule for Purchase, but also materialize its Usage
            # equivalent when it is an AVS reservation so the original row is
            # not captured by a broader fallback classification.
            has_purchase = any(
                c.get("field") == "ChargeCategory" and c.get("operator") == "="
                and "Purchase" in c.get("values", [])
                for c in parsed_conditions
            )
            has_avs = any(
                c.get("field") == "CommitmentDiscountName" and c.get("operator") == "~"
                and any(str(v).strip().lower() == "avs" for v in c.get("values", []))
                for c in parsed_conditions
            )
            if has_purchase and has_avs:
                usage_conditions = [dict(c) for c in parsed_conditions]
                for condition in usage_conditions:
                    if condition.get("field") == "ChargeCategory":
                        condition["values"] = ["Usage"]
                usage_name = f"{rule_name} - Usage"[:200]
                usage_exists = await db.execute(
                    select(ClassificationRule).where(
                        ClassificationRule.name == usage_name,
                        ClassificationRule.connection_id == connection_id,
                    ).limit(1)
                )
                if usage_exists.scalars().first() is None:
                    db.add(ClassificationRule(
                        connection_id=connection_id,
                        destination_table_id=focus_table_id,
                        department_id=dept_id,
                        project_id=proj_id,
                        name=usage_name,
                        source_field=source_field,
                        pattern="ChargeCategory=Usage ; CommitmentDiscountName~AVS",
                        pattern_type="multi_condition",
                        match_value=match_value[:200] if match_value else source_field[:200],
                        target_field="departamento",
                        conditions=usage_conditions,
                        priority=max(0, priority - 1),
                        active=True,
                    ))
                    await db.flush()
                    result.classification_rules_created += 1

        await db.commit()

    return result
