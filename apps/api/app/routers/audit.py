"""Consulta de auditoria: histórico por registro (timeline) e visão global."""
from datetime import datetime
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require
from app.core.modules import ALL_MODULES
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.identity import User

router = APIRouter(prefix="/audit", tags=["audit"])


def _timeline_item(row: AuditLog) -> dict:
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "user": {"id": row.user_id, "username": row.username, "full_name": None},
        "action": row.action_label,
        "method": row.method,
        "module": row.module,
        "status_code": row.status_code,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "entity_label": row.entity_label,
        "changes": row.changes_json,
        "has_full_snapshot": row.before_json is not None or row.after_json is not None,
    }


def _detail(row: AuditLog) -> dict:
    return {
        **_timeline_item(row),
        "module": row.module,
        "path": row.path,
        "status_code": row.status_code,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "before_json": row.before_json,
        "after_json": row.after_json,
        "ip": row.ip,
        "user_agent": row.user_agent,
        "request_id": row.request_id,
        "auth_provider": row.auth_provider,
    }


@router.get("/history/{module}/{entity_type}/{entity_id}")
async def record_history(
    module: str, entity_type: str, entity_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Autorização: read no módulo DO REGISTRO, não no módulo audit
    from app.core.rbac import get_effective_permissions
    if module not in ALL_MODULES:
        raise HTTPException(status_code=404, detail="Módulo desconhecido")
    perms = await get_effective_permissions(db, user.id)
    if "read" not in perms.get(module, set()):
        raise HTTPException(status_code=403, detail="Sem permissão para esta ação.")

    result = await db.execute(
        select(AuditLog)
        .where(
            AuditLog.module == module,
            AuditLog.entity_type == entity_type,
            AuditLog.entity_id == entity_id,
        )
        .order_by(AuditLog.created_at.desc())
        .limit(200)
    )
    return [_timeline_item(r) for r in result.scalars().all()]


@router.get("/history/{module}/{entity_type}/{entity_id}/{event_id}")
async def record_history_detail(
    module: str, entity_type: str, entity_id: str, event_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.core.rbac import get_effective_permissions
    perms = await get_effective_permissions(db, user.id)
    if "read" not in perms.get(module, set()):
        raise HTTPException(status_code=403, detail="Sem permissão para esta ação.")
    row = (await db.execute(select(AuditLog).where(AuditLog.id == event_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Evento não encontrado")
    return _detail(row)


@router.get("/logs")
async def global_logs(
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    user_filter: str | None = Query(None, alias="user"),
    module: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    method: str | None = None,
    action_label: str | None = None,
    sort_field: str = Query("created_at"),
    sort_direction: str = Query("desc", pattern="^(asc|desc)$"),
    filters: str | None = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("audit", "read")),
):
    from datetime import datetime as _dt, timedelta
    query = select(AuditLog)
    if from_ is not None:
        query = query.where(AuditLog.created_at >= from_)
    if to is not None:
        query = query.where(AuditLog.created_at <= to)
    if user_filter:
        query = query.where(AuditLog.username == user_filter)
    if module:
        query = query.where(AuditLog.module == module)
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if entity_id:
        query = query.where(AuditLog.entity_id == entity_id)
    if method:
        query = query.where(AuditLog.method == method)
    if action_label:
        query = query.where(AuditLog.action_label == action_label)

    # Filtros genéricos (AND) no mesmo contrato do grid: [{field, operator, value}]
    sortable = {
        "id": AuditLog.id,
        "created_at": AuditLog.created_at,
        "username": AuditLog.username,
        "module": AuditLog.module,
        "method": AuditLog.method,
        "action_label": AuditLog.action_label,
        "entity_label": AuditLog.entity_label,
        "entity_type": AuditLog.entity_type,
        "entity_id": AuditLog.entity_id,
        "status_code": AuditLog.status_code,
    }
    if filters:
        try:
            specs = json.loads(filters)
        except json.JSONDecodeError as exc:
            raise HTTPException(422, "Filtros inválidos") from exc
        for spec in specs if isinstance(specs, list) else []:
            field = str(spec.get("field", ""))
            op = str(spec.get("operator", "equals"))
            value = str(spec.get("value", ""))
            column = sortable.get(field)
            if column is None or value == "":
                continue
            if field in ("created_at",):
                try:
                    if "T" in value:
                        dt_val = _dt.fromisoformat(value.replace("Z", "+00:00"))
                    else:
                        dt_val = _dt.fromisoformat(value + "T00:00:00")
                except ValueError:
                    continue
                if op == "after":
                    query = query.where(column >= dt_val)
                elif op == "before":
                    query = query.where(column <= dt_val + timedelta(days=1))
                elif op == "notEquals":
                    query = query.where(or_(column.is_(None), column < dt_val, column >= dt_val + timedelta(days=1)))
                else:
                    query = query.where(column >= dt_val, column < dt_val + timedelta(days=1))
                continue
            if op == "notEquals":
                query = query.where(or_(column.is_(None), cast(column, String) != value))
            elif op == "contains":
                query = query.where(cast(column, String).ilike(f"%{value}%"))
            elif op == "notContains":
                query = query.where(or_(column.is_(None), ~cast(column, String).ilike(f"%{value}%")))
            elif op == "after":
                query = query.where(column >= value)
            elif op == "before":
                query = query.where(column <= value)
            else:
                query = query.where(cast(column, String) == value)

    order = sortable.get(sort_field, AuditLog.created_at)
    ordering = order.desc() if sort_direction == "desc" else order.asc()
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    result = await db.execute(query.order_by(ordering).limit(limit).offset(offset))
    rows = result.scalars().all()
    return {"items": [_timeline_item(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/logs/{event_id}")
async def log_detail(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("audit", "read")),
):
    row = (await db.execute(select(AuditLog).where(AuditLog.id == event_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Evento não encontrado")
    return _detail(row)
