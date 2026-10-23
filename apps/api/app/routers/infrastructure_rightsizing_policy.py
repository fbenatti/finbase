"""Rightsizing engine policy (aggressiveness preset and detailed parameters)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.rbac import get_effective_permissions, has_permission
from app.database import get_db
from app.models.infrastructure_opportunity import InfraRightsizingPolicy
from app.services.infrastructure_metrics.policy import (
    CUSTOM, PARAMETER_KEYS, PEAK_QUANTILES, PRESETS, RightsizingPolicyUpdate, classify, effective_policy,
    latest_revision_query,
)

router = APIRouter(prefix="/politica-rightsizing")


async def _authorize(user, db: AsyncSession, action: str) -> None:
    """The policy is global: reading needs any grant of the module, changing it a module-wide one."""
    permissions = await get_effective_permissions(db, user.id)
    scopes = (permissions.get("infrastructure_metrics") or {}).get(action) or set()
    allowed = bool(scopes) if action == "read" else has_permission(permissions, "infrastructure_metrics", action)
    if not allowed:
        raise HTTPException(403, "Sem permissão para a política de rightsizing")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _revision_dict(row: InfraRightsizingPolicy) -> dict:
    return {
        "version": row.version, "aggressiveness": row.aggressiveness, "params": row.params, "note": row.note,
        "created_by": row.created_by, "created_by_name": row.created_by_name, "created_at": _iso(row.created_at),
    }


def _policy_payload(row: InfraRightsizingPolicy | None) -> dict:
    policy = effective_policy(row)
    return {
        "policy_version": policy["version"],
        "revision": policy["revision"],
        "aggressiveness": policy["aggressiveness"],
        "params": {key: policy[key] for key in PARAMETER_KEYS},
        "note": row.note if row else None,
        "updated_by": row.created_by_name if row else None,
        "updated_at": _iso(row.created_at) if row else None,
        "is_default": row is None,
        "presets": PRESETS,
        "peak_quantiles": list(PEAK_QUANTILES),
    }


@router.get("")
async def get_policy(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _authorize(user, db, "read")
    return _policy_payload((await db.execute(latest_revision_query())).scalar_one_or_none())


@router.put("")
async def update_policy(body: RightsizingPolicyUpdate, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _authorize(user, db, "update")
    params = body.params.model_dump()
    aggressiveness = body.aggressiveness
    if aggressiveness != CUSTOM and classify(params) != aggressiveness:
        # A named preset must carry exactly its values; edited values are custom.
        aggressiveness = CUSTOM
    current = (await db.execute(select(func.max(InfraRightsizingPolicy.version)))).scalar()
    row = InfraRightsizingPolicy(
        version=(current or 0) + 1, aggressiveness=aggressiveness, params=params, note=body.note,
        created_by=int(user.id), created_by_name=getattr(user, "full_name", None) or getattr(user, "username", None),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _policy_payload(row)


@router.get("/historico")
async def policy_history(limit: int = Query(20, ge=1, le=200), user=Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)):
    await _authorize(user, db, "read")
    rows = (await db.execute(
        select(InfraRightsizingPolicy).order_by(InfraRightsizingPolicy.version.desc()).limit(limit)
    )).scalars().all()
    return {"items": [_revision_dict(row) for row in rows]}
