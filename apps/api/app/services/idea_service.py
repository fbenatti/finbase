"""State machine, dwell intervals and derived metrics for Ideas module."""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.idea import IMPACT_WEIGHT, VOTABLE_TYPES, Idea, IdeaStatusInterval

# Valid transitions: from_status -> set of to_status
TRANSITIONS: dict[str, set[str]] = {
    "NOVA": {"EM_VOTACAO", "EM_ANALISE"},
    "EM_VOTACAO": {"EM_ANALISE"},
    "EM_ANALISE": {"BACKLOG", "CANCELADA", "FECHADA"},
    "BACKLOG": {"EM_IMPLEMENTACAO"},
    "EM_IMPLEMENTACAO": {"CONCLUIDA_ANALISE", "CANCELADA"},
    "CONCLUIDA_ANALISE": {"EM_PRODUCAO"},
    "EM_PRODUCAO": {"CONCLUIDA"},
}

BOARD_COLUMNS = [
    "NOVA", "EM_VOTACAO", "EM_ANALISE", "BACKLOG",
    "EM_IMPLEMENTACAO", "CONCLUIDA_ANALISE", "EM_PRODUCAO", "CONCLUIDA",
]

IMPL_STATUSES = ("EM_IMPLEMENTACAO", "CONCLUIDA_ANALISE")
PRE_START_STATUSES = ("NOVA", "EM_VOTACAO", "EM_ANALISE", "BACKLOG")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def initial_status(idea_type: str) -> str:
    if idea_type == "BUG_CRITICO":
        return "EM_ANALISE"
    return "NOVA"


def can_vote(idea_type: str) -> bool:
    return idea_type in VOTABLE_TYPES


def compute_priority_score(votes_count: int, impact: str) -> float:
    return float(votes_count * IMPACT_WEIGHT.get(impact, 1))


def validate_transition(current: str, target: str) -> None:
    allowed = TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ValueError(f"Transição inválida: {current} → {target}")


async def open_interval(
    db: AsyncSession,
    idea_id: int,
    status: str,
    actor_user_id: int | None = None,
    from_status: str | None = None,
    entered_at: datetime | None = None,
) -> IdeaStatusInterval:
    ts = entered_at or _now()
    row = IdeaStatusInterval(
        idea_id=idea_id,
        status=status,
        from_status=from_status,
        entered_at=ts,
        actor_user_id=actor_user_id,
    )
    db.add(row)
    return row


async def close_open_interval(db: AsyncSession, idea_id: int, exited_at: datetime | None = None) -> IdeaStatusInterval | None:
    result = await db.execute(
        select(IdeaStatusInterval)
        .where(IdeaStatusInterval.idea_id == idea_id, IdeaStatusInterval.exited_at.is_(None))
        .order_by(IdeaStatusInterval.entered_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    ts = exited_at or _now()
    row.exited_at = ts
    row.duration_seconds = int((ts - row.entered_at).total_seconds())
    return row


async def transition_idea(
    db: AsyncSession,
    idea: Idea,
    target_status: str,
    actor_user_id: int,
) -> Idea:
    from_status = idea.status
    validate_transition(from_status, target_status)
    ts = _now()
    await close_open_interval(db, idea.id, ts)
    idea.status = target_status
    idea.status_entered_at = ts
    await open_interval(db, idea.id, target_status, actor_user_id, from_status, ts)
    return idea


def _interval_duration_seconds(interval: IdeaStatusInterval, now: datetime) -> int:
    if interval.duration_seconds is not None:
        return interval.duration_seconds
    if interval.exited_at:
        return int((interval.exited_at - interval.entered_at).total_seconds())
    return int((now - interval.entered_at).total_seconds())


def dwell_by_status(intervals: list[IdeaStatusInterval], now: datetime | None = None) -> dict[str, float]:
    ts = now or _now()
    totals: dict[str, int] = {}
    for iv in intervals:
        totals[iv.status] = totals.get(iv.status, 0) + _interval_duration_seconds(iv, ts)
    return {k: round(v / 86400, 2) for k, v in totals.items()}


def _first_entered_at(intervals: list[IdeaStatusInterval], status: str) -> datetime | None:
    matches = [iv.entered_at for iv in intervals if iv.status == status]
    return min(matches) if matches else None


def compute_stage_metrics(intervals: list[IdeaStatusInterval], created_at: datetime, now: datetime | None = None) -> dict[str, float | None]:
    ts = now or _now()
    dwell = dwell_by_status(intervals, ts)
    first_impl = _first_entered_at(intervals, "EM_IMPLEMENTACAO")
    first_prod = _first_entered_at(intervals, "EM_PRODUCAO")

    time_to_start = None
    if first_impl:
        time_to_start = round((first_impl - created_at).total_seconds() / 86400, 2)

    time_in_impl = round(sum(dwell.get(s, 0) for s in IMPL_STATUSES), 2) if first_impl else None

    time_to_prod = None
    if first_prod:
        time_to_prod = round((first_prod - created_at).total_seconds() / 86400, 2)

    exec_to_prod = None
    if first_impl and first_prod:
        exec_to_prod = round((first_prod - first_impl).total_seconds() / 86400, 2)

    open_iv = next((iv for iv in reversed(intervals) if iv.exited_at is None), None)
    days_in_current = round(_interval_duration_seconds(open_iv, ts) / 86400, 2) if open_iv else 0

    return {
        "dwell": dwell,
        "time_to_start_days": time_to_start,
        "time_in_impl_days": time_in_impl,
        "time_to_prod_days": time_to_prod,
        "exec_to_prod_days": exec_to_prod,
        "days_in_current_status": days_in_current,
    }


def _avg_p50(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    return round(sum(values) / len(values), 2), round(float(median(values)), 2)


async def load_intervals(db: AsyncSession, idea_id: int) -> list[IdeaStatusInterval]:
    result = await db.execute(
        select(IdeaStatusInterval)
        .where(IdeaStatusInterval.idea_id == idea_id)
        .order_by(IdeaStatusInterval.entered_at)
    )
    return list(result.scalars().all())


def idea_to_dict(idea: Idea, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    assignees = []
    if idea.assignees:
        for a in idea.assignees:
            u = a.user
            assignees.append({
                "id": a.user_id,
                "username": u.username if u else None,
                "full_name": u.full_name if u else None,
            })
    author = idea.author
    out: dict[str, Any] = {
        "id": idea.id,
        "title": idea.title,
        "description": idea.description,
        "idea_type": idea.idea_type,
        "impact": idea.impact,
        "difficulty": idea.difficulty,
        "due_date": idea.due_date.isoformat() if idea.due_date else None,
        "status": idea.status,
        "author_id": idea.author_id,
        "author": {
            "id": idea.author_id,
            "username": author.username if author else None,
            "full_name": author.full_name if author else None,
        },
        "votes_count": idea.votes_count,
        "priority_score": idea.priority_score,
        "status_entered_at": idea.status_entered_at.isoformat() if idea.status_entered_at else None,
        "created_at": idea.created_at.isoformat() if idea.created_at else None,
        "updated_at": idea.updated_at.isoformat() if idea.updated_at else None,
        "assignees": assignees,
    }
    if metrics:
        out.update(metrics)
    return out
