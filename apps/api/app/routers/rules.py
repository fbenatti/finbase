import asyncio
import csv
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text, delete, or_
from sqlalchemy.exc import IntegrityError
from app.core.audit import audit
from app.core.audit_sanitize import to_snapshot
from app.core.deps import resolve_user_from_request
from app.database import get_db
from app.models.rules import (
    EtlMapping, BusinessRule, ClassificationRule, ClassificationFieldValue, Department,
    OrganizationMoveAudit, DepartmentSharedCost, DepartmentSharedCostTarget,
)
from app.models.financial_contract import FinancialContract
from app.services.classification_field_values import refresh_destination_values
from app.models.planning import PlanningEntry
from app.models.destination import DestinationColumn, DestinationTable, SourceDestinationMapping
from app.services.classification_engine import map_legacy_field, normalize_rule_conditions
from app.services.cloud_scope import connection_cloud_scope
from app.services.shared_cost import cloud_prefix_for
from app.schemas.schemas import (
    EtlMappingCreate, EtlMappingUpdate, EtlMappingOut,
    BusinessRuleCreate, BusinessRuleUpdate, BusinessRuleOut,
    ClassificationRuleCreate, ClassificationRuleUpdate, ClassificationRuleOut,
    DepartmentCreate, DepartmentUpdate, DepartmentOut,
    CsvImportRequest, CsvImportResult,
    SharedCostOut, SharedCostUpsert, SharedCostTargetOut,
)
from app.services.shared_cost import SHARED_COST_MODES, department_path, subtree_ids, children_map

logger = logging.getLogger(__name__)

router = APIRouter(tags=["rules"])

# Keep references to background refresh tasks so they are not garbage-collected.
_refresh_tasks: set[asyncio.Task] = set()

MAX_DEPARTMENT_LEVELS = 7
FINANCIAL_EFFECTS = {"NONE", "DIRECT_REALIZED", "BUDGET_COMMITMENT"}


async def _validate_financial_effect(data, db: AsyncSession) -> None:
    effect = str(getattr(data, "financial_effect", "NONE") or "NONE").upper()
    contract_id = getattr(data, "financial_contract_id", None)
    if effect not in FINANCIAL_EFFECTS:
        raise HTTPException(422, "financial_effect inválido")
    if effect == "BUDGET_COMMITMENT":
        if not contract_id:
            raise HTTPException(422, "Regra de compromisso exige financial_contract_id")
        if not await db.get(FinancialContract, contract_id):
            raise HTTPException(422, "Contrato financeiro não encontrado")
    elif contract_id:
        raise HTTPException(422, "financial_contract_id só pode ser usado em BUDGET_COMMITMENT")


def _department_path(department_id: int, departments: dict[int, Department]) -> list[str]:
    path: list[str] = []
    current = departments.get(department_id)
    seen: set[int] = set()
    while current and current.id not in seen:
        seen.add(current.id)
        path.append(current.name)
        current = departments.get(current.parent_id) if current.parent_id else None
    return list(reversed(path))


def _department_cloud_scope(department_id: int, departments: dict[int, Department]) -> str | None:
    """Resolve a project's cloud from its explicit filter or provider branch."""
    current = departments.get(department_id)
    seen: set[int] = set()
    while current and current.id not in seen:
        seen.add(current.id)
        scope = (current.cloud_filter or "").strip()
        if scope:
            return connection_cloud_scope(scope) or scope.lower()
        current = departments.get(current.parent_id) if current.parent_id else None
    path = _department_path(department_id, departments)
    if len(path) >= 3 and path[1] in {"Showback", "Chargeback"}:
        return connection_cloud_scope(path[2]) or path[2].lower()
    return None


def _is_project_department(department: Department, departments: dict[int, Department]) -> bool:
    """Treat explicit project nodes and leaves below a cloud provider as projects."""
    custom_fields = department.custom_fields if isinstance(department.custom_fields, dict) else {}
    if str(custom_fields.get("tipo") or "").strip().casefold() == "projeto":
        return True
    return bool(department.parent_id and _department_cloud_scope(department.id, departments))


def _duplicate_project_department(
    name: str,
    parent_id: int | None,
    departments: dict[int, Department],
    *,
    exclude_id: int | None = None,
) -> Department | None:
    """Find a same-cloud project name, even when it sits under another director."""
    candidate_scope = _department_cloud_scope(parent_id, departments) if parent_id else None
    normalized_name = (name or "").strip().casefold()
    if not normalized_name or not candidate_scope:
        return None
    for existing in departments.values():
        if existing.id == exclude_id or not existing.active:
            continue
        if (existing.name or "").strip().casefold() != normalized_name:
            continue
        if not _is_project_department(existing, departments):
            continue
        if _department_cloud_scope(existing.id, departments) == candidate_scope:
            return existing
    return None


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
        destination = await db.get(DestinationTable, destination_table_id)
        is_focus_destination = bool(destination and (
            (destination.name or "").strip().upper() == "MODELO FOCUS"
            or (destination.physical_name or "").strip().lower() == "dest_modelo_focus"
        ))
        destination_filter = BusinessRule.destination_table_id == destination_table_id
        if is_focus_destination:
            # FOCUS predates the destination-table model. Keep its legacy
            # global rules visible, but do not expose them in new tables.
            destination_filter = or_(
                destination_filter,
                BusinessRule.destination_table_id.is_(None),
            )
        stmt = stmt.where(destination_filter)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/business-rules/", response_model=BusinessRuleOut, status_code=201)
async def create_business_rule(data: BusinessRuleCreate, request: Request, db: AsyncSession = Depends(get_db)):
    await _validate_financial_effect(data, db)
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

    audit_user = await resolve_user_from_request(request, db)
    await audit(db, request, audit_user, module="rules", method="POST", action_label="create",
                entity_type="business_rule", entity_id=rule.id, entity_label=rule.name,
                after=to_snapshot(rule), status_code=201)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.put("/business-rules/{rule_id}", response_model=BusinessRuleOut)
async def update_business_rule(rule_id: int, data: BusinessRuleUpdate, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(BusinessRule).where(BusinessRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Business rule not found")
    merged_effect = data.financial_effect if data.financial_effect is not None else rule.financial_effect
    merged_contract = data.financial_contract_id if data.financial_contract_id is not None else rule.financial_contract_id
    class _FinancialInput:
        financial_effect = merged_effect
        financial_contract_id = merged_contract
    await _validate_financial_effect(_FinancialInput(), db)
    before_snapshot = to_snapshot(rule)
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

    await db.flush()
    await db.refresh(rule)  # recarrega colunas (evita lazy de atributo expirado)
    audit_user = await resolve_user_from_request(request, db)
    await audit(db, request, audit_user, module="rules", method="PUT", action_label="update",
                entity_type="business_rule", entity_id=rule.id, entity_label=rule.name,
                before=before_snapshot, after=to_snapshot(rule), status_code=200)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/business-rules/{rule_id}", status_code=204)
async def delete_business_rule(rule_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(BusinessRule).where(BusinessRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404)
    before_snapshot = to_snapshot(rule)

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



@router.get("/classifications/grid")
async def list_classifications_grid(
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: str | None = None,
    sort_field: str = "priority",
    sort_direction: str = Query("asc", pattern="^(asc|desc)$"),
    filters: str | None = None,
    destination_table_id: int | None = None,
    cloud: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Server-side grid over classification rules (pagination, search, sort, AND filters).

    cloud maps the UI filter: "global" matches rules without cloud_scope,
    "every"/None means no cloud filter, otherwise an exact cloud_scope match.
    """
    import json
    from datetime import datetime as _dt
    from sqlalchemy import or_, func, cast, String

    dept = Department
    stmt = select(ClassificationRule, dept.name).outerjoin(dept, dept.id == ClassificationRule.department_id)
    conditions = []
    if destination_table_id is not None:
        conditions.append(ClassificationRule.destination_table_id == destination_table_id)
    if cloud is not None and cloud != "every":
        if cloud == "global":
            conditions.append(ClassificationRule.cloud_scope.is_(None))
        else:
            conditions.append(ClassificationRule.cloud_scope == cloud)
    # Busca global: qualquer campo textual/numerico da regra, incluindo o
    # JSON de condições. Quando o valor é apagado, não adiciona predicado e a
    # consulta volta naturalmente para todos os registros do escopo.
    search_value = (search or "").strip()
    if search_value:
        needle = f"%{search_value}%"
        conditions.append(or_(
            cast(ClassificationRule.id, String).ilike(needle),
            cast(ClassificationRule.connection_id, String).ilike(needle),
            cast(ClassificationRule.destination_table_id, String).ilike(needle),
            cast(ClassificationRule.department_id, String).ilike(needle),
            dept.name.ilike(needle),
            ClassificationRule.name.ilike(needle),
            ClassificationRule.source_field.ilike(needle),
            ClassificationRule.pattern.ilike(needle),
            ClassificationRule.pattern_type.ilike(needle),
            ClassificationRule.match_value.ilike(needle),
            ClassificationRule.target_field.ilike(needle),
            cast(ClassificationRule.conditions, String).ilike(needle),
            cast(ClassificationRule.priority, String).ilike(needle),
            ClassificationRule.cloud_scope.ilike(needle),
            cast(ClassificationRule.active, String).ilike(needle),
            cast(ClassificationRule.created_at, String).ilike(needle),
            cast(ClassificationRule.updated_at, String).ilike(needle),
        ))
    fields = {
        "id": ClassificationRule.id,
        "name": ClassificationRule.name,
        "department_name": dept.name,
        "target_field": ClassificationRule.target_field,
        "cloud_scope": ClassificationRule.cloud_scope,
        "priority": ClassificationRule.priority,
        "active": ClassificationRule.active,
        "updated_at": ClassificationRule.updated_at,
    }
    try:
        specs = json.loads(filters) if filters else []
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "Filtros inválidos") from exc
    for spec in specs if isinstance(specs, list) else []:
        field = str(spec.get("field", ""))
        value = str(spec.get("value", ""))
        op = str(spec.get("operator", "equals"))
        column = fields.get(field)
        if column is None or not value:
            continue
        if field == "updated_at":
            try:
                clean_val = value.replace("Z", "+00:00")
                dt_val = _dt.fromisoformat(clean_val) if "T" in clean_val else _dt.fromisoformat(clean_val + "T00:00:00")
                expr = func.date_trunc("minute", column) == func.date_trunc("minute", dt_val) if ":" in value else func.date(column) == dt_val.date()
            except ValueError:
                continue
        elif field in ("id", "priority"):
            try:
                num_val = int(value)
                expr = column == num_val
            except ValueError:
                continue
        elif field == "active":
            truthy = value.strip().lower() in {"true", "1", "yes", "sim", "ativa", "ativo"}
            expr = column.is_(True) if truthy else column.is_(False)
        elif field == "cloud_scope":
            expr = column.is_(None) if value.strip().lower() in {"global", "null", "none"} else column == value
        elif field == "department_name":
            expr = dept.name == value
        else:
            expr = column == value
        conditions.append(or_(column.is_(None), expr == False) if op == "notEquals" else expr)
    sort = fields.get(sort_field, ClassificationRule.priority)
    ordering = sort.desc() if sort_direction == "desc" else sort.asc()
    if sort_field == "department_name":
        ordering = func.lower(sort).desc() if sort_direction == "desc" else func.lower(sort).asc()
    filtered = stmt.where(*conditions) if conditions else stmt
    total = (await db.execute(select(func.count()).select_from(filtered.subquery()))).scalar_one()
    rows = (await db.execute(filtered.order_by(ordering, ClassificationRule.id).offset(offset).limit(limit))).all()
    items = [{
        "id": rule.id,
        "connection_id": rule.connection_id,
        "destination_table_id": rule.destination_table_id,
        "department_id": rule.department_id,
        "department_name": department_name,
        "name": rule.name,
        "source_field": rule.source_field,
        "pattern": rule.pattern,
        "pattern_type": rule.pattern_type,
        "match_value": rule.match_value,
        "target_field": rule.target_field,
        "conditions": rule.conditions,
        "priority": rule.priority,
        "cloud_scope": rule.cloud_scope,
        "active": rule.active,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    } for rule, department_name in rows]
    return {"items": items, "total": total}


@router.get("/classifications/field-values")
async def classification_field_values(
    destination_table_id: int = Query(...),
    field: str = Query(..., min_length=1),
    cloud: str | None = Query(None),
    search: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Autocomplete values for a classification condition field (fast Postgres lookup)."""
    stmt = select(ClassificationFieldValue).where(
        ClassificationFieldValue.destination_table_id == destination_table_id,
        ClassificationFieldValue.field == field,
    )
    if cloud and cloud != "global":
        stmt = stmt.where(ClassificationFieldValue.cloud == cloud.strip().lower())
    if search and search.strip():
        stmt = stmt.where(ClassificationFieldValue.value.ilike(f"{search.strip()}%"))
    stmt = (
        stmt.order_by(ClassificationFieldValue.count.desc(), ClassificationFieldValue.value.asc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [{"value": row.value, "count": row.count} for row in rows]


@router.post("/classifications/refresh-field-values", status_code=202)
async def refresh_classification_field_values(
    destination_table_id: int | None = Query(None),
    fields: str | None = Query(None, description="Campos separados por vírgula (opcional)"),
):
    """Backfill the field-value reference from Gold via Trino (background)."""
    field_list = [f.strip() for f in fields.split(",")] if fields else None
    _refresh_tasks.add(
        asyncio.create_task(refresh_destination_values(destination_table_id, field_list))
    )
    return {"status": "started", "destination_table_id": destination_table_id, "fields": field_list}


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


@router.get("/departments/grid")
async def list_departments_grid(
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: str | None = None,
    sort_field: str = "name",
    sort_direction: str = Query("asc", pattern="^(asc|desc)$"),
    filters: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Server-side grid over departments (pagination, search, sort, AND filters).

    Includes the parent department name and the shared-cost flag so the
    DataGrid can render them without extra round-trips.
    """
    import json
    from datetime import datetime as _dt
    from sqlalchemy import or_, func
    from sqlalchemy.orm import aliased

    parent = aliased(Department)
    shared_enabled = (
        select(DepartmentSharedCost.enabled)
        .where(DepartmentSharedCost.department_id == Department.id)
        .correlate(Department)
        .scalar_subquery()
    )
    stmt = select(Department, parent.name, shared_enabled).outerjoin(
        parent, parent.id == Department.parent_id
    )
    conditions = []
    if search and search.strip():
        needle = f"%{search.strip()}%"
        conditions.append(or_(
            Department.name.ilike(needle),
            parent.name.ilike(needle),
            Department.centro_custo.ilike(needle),
            Department.id_orcamento.ilike(needle),
            Department.ambiente.ilike(needle),
            Department.cloud_filter.ilike(needle),
        ))
    fields = {
        "id": Department.id,
        "name": Department.name,
        "parent_name": parent.name,
        "centro_custo": Department.centro_custo,
        "id_orcamento": Department.id_orcamento,
        "ambiente": Department.ambiente,
        "cloud_filter": Department.cloud_filter,
        "shared_cost": shared_enabled,
        "active": Department.active,
        "updated_at": Department.updated_at,
    }
    try:
        specs = json.loads(filters) if filters else []
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "Filtros inválidos") from exc
    for spec in specs if isinstance(specs, list) else []:
        field = str(spec.get("field", ""))
        value = str(spec.get("value", ""))
        op = str(spec.get("operator", "equals"))
        column = fields.get(field)
        if column is None or not value:
            continue
        if field == "updated_at":
            try:
                clean_val = value.replace("Z", "+00:00")
                dt_val = _dt.fromisoformat(clean_val) if "T" in clean_val else _dt.fromisoformat(clean_val + "T00:00:00")
                expr = func.date_trunc("minute", column) == func.date_trunc("minute", dt_val) if ":" in value else func.date(column) == dt_val.date()
            except ValueError:
                continue
        elif field == "id":
            try:
                num_val = int(value)
                expr = column == num_val
            except ValueError:
                continue
        elif field == "active":
            truthy = value.strip().lower() in {"true", "1", "yes", "sim", "ativa", "ativo"}
            expr = column.is_(True) if truthy else column.is_(False)
        elif field == "shared_cost":
            truthy = value.strip().lower() in {"true", "1", "yes", "sim"}
            expr = column.is_(True) if truthy else (or_(column.is_(None), column.is_(False)))
        elif field == "parent_name":
            expr = parent.name == value
        else:
            expr = column == value
        conditions.append(or_(column.is_(None), expr == False) if op == "notEquals" else expr)
    sort = fields.get(sort_field, Department.name)
    ordering = sort.desc() if sort_direction == "desc" else sort.asc()
    filtered = stmt.where(*conditions) if conditions else stmt
    total = (await db.execute(select(func.count()).select_from(filtered.subquery()))).scalar_one()
    rows = (await db.execute(filtered.order_by(ordering, Department.id).offset(offset).limit(limit))).all()
    items = [{
        "id": department.id,
        "parent_id": department.parent_id,
        "name": department.name,
        "parent_name": parent_name,
        "centro_custo": department.centro_custo,
        "id_orcamento": department.id_orcamento,
        "ambiente": department.ambiente,
        "cloud_filter": department.cloud_filter,
        "custom_fields": department.custom_fields,
        "shared_cost": bool(shared_cost),
        "active": department.active,
        "created_at": department.created_at,
        "updated_at": department.updated_at,
    } for department, parent_name, shared_cost in rows]
    return {"items": items, "total": total}


@router.post("/departments/", response_model=DepartmentOut, status_code=201)
async def create_department(data: DepartmentCreate, db: AsyncSession = Depends(get_db)):
    departments = {department.id: department for department in (await db.execute(select(Department))).scalars().all()}
    if data.parent_id is not None:
        if data.parent_id not in departments or not departments[data.parent_id].active:
            raise HTTPException(status_code=422, detail="O departamento pai deve existir e estar ativo")
        if len(_department_path(data.parent_id, departments)) >= MAX_DEPARTMENT_LEVELS:
            raise HTTPException(status_code=422, detail="A hierarquia suporta no máximo 7 níveis")
    if _duplicate_project_department(data.name, data.parent_id, departments):
        raise HTTPException(
            status_code=409,
            detail="Já existe um projeto com esse nome nesta cloud. Escolha outro nome ou atualize o projeto existente.",
        )
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
    target_name = update_data.get("name", dept.name) or ""
    target_parent_id = update_data.get("parent_id", dept.parent_id)
    if _duplicate_project_department(
        target_name, target_parent_id, departments, exclude_id=dept.id,
    ):
        raise HTTPException(
            status_code=409,
            detail="Já existe um projeto com esse nome nesta cloud. Escolha outro nome ou atualize o projeto existente.",
        )
    name_changed = "name" in update_data and (update_data["name"] or "").strip() != (dept.name or "").strip()
    if "parent_id" in update_data and update_data["parent_id"] != dept.parent_id:
        await _move_department(db, dept, update_data.pop("parent_id"), departments)
    for k, v in update_data.items():
        setattr(dept, k, v)
    if name_changed:
        departments[dept.id] = dept
        impacted_ids = _subtree_ids(dept.id, departments)
        await _refresh_planning_paths(db, departments, impacted_ids)
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
    await db.flush()
    config_ids = select(DepartmentSharedCost.id).where(DepartmentSharedCost.department_id == dept_id)
    await db.execute(delete(DepartmentSharedCostTarget).where(or_(
        DepartmentSharedCostTarget.target_department_id == dept_id,
        DepartmentSharedCostTarget.config_id.in_(config_ids),
    )))
    await db.execute(delete(DepartmentSharedCost).where(DepartmentSharedCost.department_id == dept_id))
    await db.execute(delete(OrganizationMoveAudit).where(OrganizationMoveAudit.department_id == dept_id))
    for rule in (await db.execute(select(ClassificationRule).where(ClassificationRule.department_id == dept_id))).scalars().all():
        rule.department_id = None
        rule.active = False
    for entry in (await db.execute(select(PlanningEntry).where(PlanningEntry.department_id == dept_id))).scalars().all():
        entry.department_id = None
    await db.flush()
    await db.delete(dept)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Não foi possível excluir este departamento porque ainda há vínculos (classificação, orçamento ou custo compartilhado).",
        )


def _shared_cost_out(config: DepartmentSharedCost, departments: dict[int, Department]) -> SharedCostOut:
    targets: list[SharedCostTargetOut] = []
    for target in config.targets or []:
        node = departments.get(target.target_department_id)
        path = " > ".join(department_path(target.target_department_id, departments)) if node else None
        targets.append(SharedCostTargetOut(
            department_id=target.target_department_id,
            percent=target.percent,
            name=node.name if node else None,
            path=path,
        ))
    donor = departments.get(config.department_id)
    return SharedCostOut(
        department_id=config.department_id,
        enabled=config.enabled,
        mode=config.mode,
        name=donor.name if donor else None,
        path=" > ".join(department_path(config.department_id, departments)) if donor else None,
        targets=targets,
    )


@router.get("/departments/shared-costs", response_model=list[SharedCostOut])
async def list_shared_costs(db: AsyncSession = Depends(get_db)):
    departments = {department.id: department for department in (await db.execute(select(Department))).scalars().all()}
    configs = (await db.execute(select(DepartmentSharedCost))).scalars().all()
    return [_shared_cost_out(config, departments) for config in configs]


@router.get("/departments/{dept_id}/shared-cost", response_model=SharedCostOut)
async def get_shared_cost(dept_id: int, db: AsyncSession = Depends(get_db)):
    departments = {department.id: department for department in (await db.execute(select(Department))).scalars().all()}
    if dept_id not in departments:
        raise HTTPException(status_code=404, detail="Department not found")
    config = (await db.execute(select(DepartmentSharedCost).where(DepartmentSharedCost.department_id == dept_id))).scalar_one_or_none()
    if not config:
        return SharedCostOut(department_id=dept_id, enabled=False, mode="consumption", targets=[])
    return _shared_cost_out(config, departments)


@router.put("/departments/{dept_id}/shared-cost", response_model=SharedCostOut)
async def upsert_shared_cost(dept_id: int, data: SharedCostUpsert, db: AsyncSession = Depends(get_db)):
    departments = {department.id: department for department in (await db.execute(select(Department))).scalars().all()}
    donor = departments.get(dept_id)
    if not donor:
        raise HTTPException(status_code=404, detail="Department not found")
    mode = (data.mode or "consumption").lower()
    if mode not in SHARED_COST_MODES:
        raise HTTPException(status_code=422, detail="Modo de rateio inválido")
    if data.enabled and mode != "consumption" and not data.targets:
        raise HTTPException(status_code=422, detail="Selecione ao menos um departamento destino")
    donor_tree = subtree_ids(dept_id, children_map(departments))
    seen: set[int] = set()
    percent_sum = 0.0
    clean_targets: list[DepartmentSharedCostTarget] = []
    selected_targets = [] if mode == "consumption" else data.targets
    for target in selected_targets:
        if target.department_id not in departments:
            raise HTTPException(status_code=422, detail="Departamento destino não encontrado")
        if target.department_id in seen:
            continue
        if target.department_id in donor_tree:
            raise HTTPException(status_code=422, detail="O destino não pode estar dentro do próprio custo compartilhado")
        seen.add(target.department_id)
        percent = target.percent
        if mode == "custom":
            if percent is None:
                raise HTTPException(status_code=422, detail="Informe o percentual de cada destino")
            if percent < 0 or percent > 100:
                raise HTTPException(status_code=422, detail="Percentual deve estar entre 0 e 100")
            percent_sum += percent
        else:
            percent = None
        clean_targets.append(DepartmentSharedCostTarget(target_department_id=target.department_id, percent=percent))
    if mode == "custom" and percent_sum > 100 + 1e-6:
        raise HTTPException(status_code=422, detail="A soma dos percentuais não pode passar de 100%")
    config = (await db.execute(select(DepartmentSharedCost).where(DepartmentSharedCost.department_id == dept_id))).scalar_one_or_none()
    if not config:
        config = DepartmentSharedCost(department_id=dept_id, enabled=data.enabled, mode=mode)
        db.add(config)
        await db.flush()
    else:
        config.enabled = data.enabled
        config.mode = mode
        await db.execute(delete(DepartmentSharedCostTarget).where(DepartmentSharedCostTarget.config_id == config.id))
        await db.flush()
    for target in clean_targets:
        target.config_id = config.id
        db.add(target)
    await db.commit()
    config = (await db.execute(select(DepartmentSharedCost).where(DepartmentSharedCost.department_id == dept_id))).scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=500, detail="O custo compartilhado foi gravado, mas não pôde ser recarregado")
    return _shared_cost_out(config, departments)


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


def _normalize_return_conditions(conditions: list[dict]) -> list[dict]:
    """Use destination fields to classify synthetic Return rows.

    Clone rows retain ``source_charge_type`` and ``source_reservation_name``
    from the original purchase.  The clone state itself is represented by
    ``ChargeCategory=Return`` and the destination commitment field, so legacy
    Return rules written against source fields would otherwise never match.
    """
    normalized: list[dict] = []
    for condition in conditions:
        field = str(condition.get("field", "")).strip()
        values = [str(value).strip() for value in condition.get("values", [])]
        is_return = field.lower() in {"source_charge_type", "chargetype"} and any(
            value.lower() == "return" for value in values
        )
        if is_return:
            normalized.append({**condition, "field": "ChargeCategory", "operator": "="})
            continue
        if field.lower() == "source_reservation_name":
            normalized.append({**condition, "field": "CommitmentDiscountName"})
            continue
        normalized.append(condition)
    return normalized


@router.post("/departments/import", response_model=CsvImportResult, status_code=201)
async def import_csv_data(data: CsvImportRequest, db: AsyncSession = Depends(get_db)):
    """Import departments and business rules from finopsNew CSV files."""
    result = CsvImportResult(departments_created=0, classification_rules_created=0)
    focus_table_id = await _default_focus_table_id(db)
    departments_by_id = {
        department.id: department
        for department in (await db.execute(select(Department))).scalars().all()
    }

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
        res = await db.execute(stmt)
        matches = res.scalars().all()
        return matches[0] if len(matches) == 1 else None

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
                    id_orcamento=row.get("IDOrcamento", "").strip() or None,
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

            duplicate = _duplicate_project_department(pn, parent_id, departments)
            if duplicate and duplicate.parent_id != parent_id:
                duplicate_path = " > ".join(_department_path(duplicate.id, departments))
                result.errors.append(
                    f"Projeto '{pn}' duplicado na cloud {cloud}; import ignorado para '{dn}' (já existe em '{duplicate_path}')"
                )
                continue

            dept = await _find_dept(pn, parent_id=parent_id)
            if dept is None:
                same_parent = (await db.execute(
                    select(Department).where(
                        Department.name == pn,
                        Department.parent_id == parent_id,
                    )
                )).scalars().all()
                if len(same_parent) > 1:
                    result.errors.append(
                        f"Projeto '{pn}' duplicado sob a diretoria '{dn}'; import ignorado por ambiguidade"
                    )
                    continue
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
                id_orcamento=row.get("IDOrcamento", "").strip() or None,
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

    # --- Step 2: Import Classification Rules from businessRules_download.csv ---
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
            parsed_conditions = _normalize_return_conditions(parsed_conditions)

            # Find department from project_name (a directory row)
            dept_id = None
            if project_name:
                csv_candidates = directory_department_map.get((cloud, project_name), [])
                dept = None
                if csv_candidates:
                    unique_candidates = sorted(set(csv_candidates))
                    if len(unique_candidates) == 1:
                        dept = await db.get(Department, unique_candidates[0])
                    else:
                        result.errors.append(
                            f"Classification '{project_name}' não importada: nome de projeto duplicado na cloud {cloud}"
                        )
                        continue
                if dept is None:
                    stmt = select(Department).where(
                        Department.name == project_name,
                        Department.parent_id != None,
                    )
                    dr = await db.execute(
                        stmt.order_by(Department.id)
                    )
                    candidates = [
                        candidate for candidate in dr.scalars().all()
                        if cloud and _department_cloud_scope(candidate.id, departments_by_id)
                        == connection_cloud_scope(cloud)
                    ]
                    if len(candidates) == 1:
                        dept = candidates[0]
                    elif len(candidates) > 1:
                        result.errors.append(
                            f"Classification '{project_name}' não importada: nome ambíguo na cloud {cloud}"
                        )
                        continue
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
                    name=rule_name,
                    source_field=source_field,
                    pattern=conditions_raw[:500],
                    pattern_type="multi_condition",
                    match_value=match_value[:200] if match_value else source_field[:200],
                    target_field="organograma",
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
                        name=usage_name,
                        source_field=source_field,
                        pattern="ChargeCategory=Usage ; CommitmentDiscountName~AVS",
                        pattern_type="multi_condition",
                        match_value=match_value[:200] if match_value else source_field[:200],
                        target_field="organograma",
                        conditions=usage_conditions,
                        priority=max(0, priority - 1),
                        active=True,
                    ))
                    await db.flush()
                    result.classification_rules_created += 1

        await db.commit()

    return result


@router.post("/departments/azure-active-classifications-sync")
async def sync_azure_active_classifications(
    apply: bool = Query(False, description="When true, persist departments and classification rules"),
    db: AsyncSession = Depends(get_db),
):
    """Preview or apply Azure classification metadata for RGs with recent consumption.

    Gold is never reprocessed here.  Callers must review the returned audit and
    start a reprocess explicitly after the metadata has been approved.
    """
    from app.services.azure_active_classification_sync import sync_active_azure_classifications

    try:
        report = await sync_active_azure_classifications(db, apply=apply)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"mode": "apply" if apply else "preview", **report.as_dict()}
