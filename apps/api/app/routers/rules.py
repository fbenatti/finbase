import csv
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from app.database import get_db
from app.models.rules import EtlMapping, BusinessRule, ClassificationRule, Department, Project
from app.models.destination import DestinationColumn
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
    return result.scalars().all()


@router.post("/classifications/", response_model=ClassificationRuleOut, status_code=201)
async def create_classification(data: ClassificationRuleCreate, db: AsyncSession = Depends(get_db)):
    rule = ClassificationRule(**data.model_dump())
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


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
    update_data = data.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(rule, k, v)
    await db.commit()
    await db.refresh(rule)
    return rule


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
    dept = Department(**data.model_dump())
    db.add(dept)
    await db.commit()
    await db.refresh(dept)
    return DepartmentOut.model_validate(dept)


@router.put("/departments/{dept_id}", response_model=DepartmentOut)
async def update_department(dept_id: int, data: DepartmentUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Department).where(Department.id == dept_id))
    dept = result.scalar_one_or_none()
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found")
    update_data = data.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(dept, k, v)
    await db.commit()
    await db.refresh(dept)
    return DepartmentOut.model_validate(dept)


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


@router.post("/departments/import", response_model=CsvImportResult, status_code=201)
async def import_csv_data(data: CsvImportRequest, db: AsyncSession = Depends(get_db)):
    """Import departments, projects, and business rules from finopsNew CSV files."""
    result = CsvImportResult(departments_created=0, projects_created=0, classification_rules_created=0)

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
    diretorias_path = Path(data.diretorias_path)
    if not diretorias_path.exists():
        result.errors.append(f"File not found: {data.diretorias_path}")
    else:
        direttoria_map: dict[str, int] = {}
        cache: set[str] = set()

        with open(diretorias_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=";")
            rows = list(reader)

        # 1a. Create root departments (Diretorias) — one per unique name
        seen_roots: dict[str, int] = {}
        for row in rows:
            dn = row.get("Diretoria", "").strip()
            if not dn:
                continue
            if dn in seen_roots:
                continue

            dept = await _find_dept(dn, parent_id=None)
            if dept:
                seen_roots[dn] = dept.id
            else:
                new_root = Department(
                    name=dn,
                    centro_custo=row.get("CentrodeCusto", "").strip() or None,
                    cloud_filter=row.get("Cloud", "").strip() or None,
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
                seen_roots[dn] = new_root.id
                result.departments_created += 1

        direttoria_map = seen_roots

        # 1b. Create child departments (Projetos) under their Diretoria
        seen_children: set[str] = set()
        for row in rows:
            dn = row.get("Diretoria", "").strip()
            pn = row.get("Projeto", "").strip()
            if not dn or not pn or pn.upper() == "NA":
                continue

            parent_id = direttoria_map.get(dn)
            if parent_id is None:
                continue

            child_key = f"{parent_id}|{pn}"
            if child_key in seen_children:
                continue
            seen_children.add(child_key)

            dept = await _find_dept(pn, parent_id=parent_id)
            if dept:
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

            # Find department/project from project_name
            dept_id = None
            proj_id = None
            if project_name:
                stmt = select(Department).where(Department.name == project_name, Department.parent_id != None)
                if cloud:
                    stmt = stmt.where(Department.cloud_filter == cloud)
                stmt = stmt.order_by(Department.id).limit(1)
                dr = await db.execute(stmt)
                dept = dr.scalars().first()
                if dept:
                    dept_id = dept.id

                pr = await db.execute(
                    select(Project).where(Project.projeto_name == project_name).limit(1)
                )
                proj = pr.scalars().first()
                if proj:
                    proj_id = proj.id

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
                    department_id=dept_id,
                    project_id=proj_id,
                    name=rule_name,
                    source_field=source_field,
                    pattern=conditions_raw[:500],
                    pattern_type="multi_condition",
                    match_value=match_value[:200] if match_value else source_field[:200],
                    target_field="projeto",
                    conditions=parsed_conditions,
                    priority=priority,
                    active=True,
                ))
                await db.flush()
                result.classification_rules_created += 1

        await db.commit()

    return result
