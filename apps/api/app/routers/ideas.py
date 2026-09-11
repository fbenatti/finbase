from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_current_user
from app.database import get_db
from app.models.idea import (
    IDEA_DIFFICULTIES, IDEA_IMPACTS, IDEA_STATUSES, IDEA_TYPES, TERMINAL_STATUSES, VOTABLE_TYPES,
    Idea, IdeaAssignee, IdeaComment, IdeaStatusInterval, IdeaVote,
)
from app.models.identity import User
from app.services.idea_service import (
    BOARD_COLUMNS, can_vote, compute_priority_score, compute_stage_metrics,
    idea_to_dict, initial_status, load_intervals, open_interval, transition_idea,
)

router = APIRouter(prefix="/ideas", tags=["ideas"])


class IdeaIn(BaseModel):
    title: str = Field(min_length=2, max_length=300)
    description: str | None = None
    idea_type: str
    impact: str = "MEDIO"
    difficulty: str = "M"
    due_date: str | None = None


class IdeaPatch(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=300)
    description: str | None = None
    impact: str | None = None
    difficulty: str | None = None
    due_date: str | None = None


class TransitionIn(BaseModel):
    status: str


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class AssigneesIn(BaseModel):
    user_ids: list[int]


def _validate_enums(body: IdeaIn | IdeaPatch, is_create: bool = False) -> None:
    if is_create:
        if body.idea_type not in IDEA_TYPES:
            raise HTTPException(422, f"Tipo inválido. Use: {', '.join(IDEA_TYPES)}")
    if getattr(body, "impact", None) and body.impact not in IDEA_IMPACTS:
        raise HTTPException(422, f"Impacto inválido. Use: {', '.join(IDEA_IMPACTS)}")
    if getattr(body, "difficulty", None) and body.difficulty not in IDEA_DIFFICULTIES:
        raise HTTPException(422, f"Dificuldade inválida. Use: {', '.join(IDEA_DIFFICULTIES)}")


async def _get_idea(db: AsyncSession, idea_id: int) -> Idea:
    result = await db.execute(
        select(Idea)
        .options(
            selectinload(Idea.author),
            selectinload(Idea.assignees).selectinload(IdeaAssignee.user),
        )
        .where(Idea.id == idea_id)
    )
    idea = result.scalar_one_or_none()
    if not idea:
        raise HTTPException(404, "Ideia não encontrada")
    return idea


async def _idea_with_metrics(db: AsyncSession, idea: Idea) -> dict[str, Any]:
    intervals = await load_intervals(db, idea.id)
    metrics = compute_stage_metrics(intervals, idea.created_at)
    return idea_to_dict(idea, metrics)


@router.get("")
async def list_ideas(
    status: str | None = None,
    idea_type: str | None = None,
    impact: str | None = None,
    assignee_id: int | None = None,
    q: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Idea)
        .options(
            selectinload(Idea.author),
            selectinload(Idea.assignees).selectinload(IdeaAssignee.user),
        )
        .order_by(Idea.priority_score.desc(), Idea.created_at.desc())
    )
    if status:
        stmt = stmt.where(Idea.status == status)
    if idea_type:
        stmt = stmt.where(Idea.idea_type == idea_type)
    if impact:
        stmt = stmt.where(Idea.impact == impact)
    if q:
        stmt = stmt.where(Idea.title.ilike(f"%{q}%"))
    rows = (await db.execute(stmt)).scalars().all()
    if assignee_id:
        rows = [r for r in rows if any(a.user_id == assignee_id for a in r.assignees)]
    return [idea_to_dict(r) for r in rows]


@router.post("", status_code=201)
async def create_idea(
    body: IdeaIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _validate_enums(body, is_create=True)
    due = None
    if body.due_date:
        try:
            due = date.fromisoformat(body.due_date)
        except ValueError:
            raise HTTPException(422, "Data de prazo inválida")
    status = initial_status(body.idea_type)
    now = datetime.now(timezone.utc)
    row = Idea(
        title=body.title,
        description=body.description,
        idea_type=body.idea_type,
        impact=body.impact,
        difficulty=body.difficulty,
        due_date=due,
        status=status,
        author_id=user.id,
        status_entered_at=now,
        priority_score=0,
    )
    db.add(row)
    await db.flush()
    await open_interval(db, row.id, status, user.id, None, now)
    await db.commit()
    await db.refresh(row)
    return await _idea_with_metrics(db, await _get_idea(db, row.id))


@router.get("/board")
async def board(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Idea)
        .options(
            selectinload(Idea.author),
            selectinload(Idea.assignees).selectinload(IdeaAssignee.user),
        )
        .where(Idea.status.in_(BOARD_COLUMNS))
        .order_by(Idea.priority_score.desc(), Idea.created_at.desc())
    )
    ideas = list(result.scalars().all())
    columns: dict[str, list] = {s: [] for s in BOARD_COLUMNS}
    for idea in ideas:
        if idea.status in columns:
            columns[idea.status].append(idea_to_dict(idea))
    return {"columns": [{"status": s, "ideas": columns[s]} for s in BOARD_COLUMNS]}


@router.get("/dashboard")
async def dashboard(
    from_date: str | None = Query(None, alias="from"),
    to_date: str | None = Query(None, alias="to"),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    year = now.year
    start = date.fromisoformat(from_date) if from_date else date(year, 1, 1)
    end = date.fromisoformat(to_date) if to_date else date(year, 12, 31)
    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(end, datetime.max.time(), tzinfo=timezone.utc)

    result = await db.execute(
        select(Idea).options(
            selectinload(Idea.author),
            selectinload(Idea.assignees).selectinload(IdeaAssignee.user),
        )
    )
    ideas = list(result.scalars().all())
    all_intervals: dict[int, list[IdeaStatusInterval]] = {}
    for idea in ideas:
        all_intervals[idea.id] = await load_intervals(db, idea.id)

    shipped: list[tuple[Idea, dict]] = []
    for idea in ideas:
        metrics = compute_stage_metrics(all_intervals[idea.id], idea.created_at, now)
        first_prod = next((iv.entered_at for iv in all_intervals[idea.id] if iv.status == "EM_PRODUCAO"), None)
        if first_prod and start_dt <= first_prod <= end_dt:
            shipped.append((idea, metrics))

    wip = [i for i in ideas if i.status in ("EM_IMPLEMENTACAO", "CONCLUIDA_ANALISE")]
    cancelled = [i for i in ideas if i.status == "CANCELADA" and i.updated_at and start_dt <= i.updated_at <= end_dt]
    closed = [i for i in ideas if i.status == "FECHADA" and i.updated_at and start_dt <= i.updated_at <= end_dt]

    bugs_fixed = [x for x in shipped if x[0].idea_type in ("BUG", "BUG_CRITICO")]
    bugs_critical = [x for x in bugs_fixed if x[0].idea_type == "BUG_CRITICO"]
    features = [x for x in shipped if x[0].idea_type == "FEATURE"]
    panels = [x for x in shipped if x[0].idea_type == "PAINEL"]

    def collect_metric(key: str) -> list[float]:
        vals = []
        for _, m in shipped:
            v = m.get(key)
            if v is not None:
                vals.append(float(v))
        return vals

    avg_start, p50_start = _avg_p50(collect_metric("time_to_start_days"))
    avg_impl, p50_impl = _avg_p50(collect_metric("time_in_impl_days"))
    avg_prod, p50_prod = _avg_p50(collect_metric("time_to_prod_days"))

    dwell_totals: dict[str, list[float]] = {}
    for _, m in shipped:
        for st, days in m.get("dwell", {}).items():
            if st not in TERMINAL_STATUSES:
                dwell_totals.setdefault(st, []).append(days)
    dwell_by_status_out = []
    for st in IDEA_STATUSES:
        vals = dwell_totals.get(st, [])
        if vals:
            avg, p50 = _avg_p50(vals)
            dwell_by_status_out.append({"status": st, "avg_days": avg, "p50_days": p50, "count": len(vals)})

    monthly: dict[str, dict[str, int]] = {}
    for idea, _ in shipped:
        first_prod = next((iv.entered_at for iv in all_intervals[idea.id] if iv.status == "EM_PRODUCAO"), None)
        if not first_prod:
            continue
        month = first_prod.strftime("%Y-%m")
        bucket = monthly.setdefault(month, {})
        bucket[idea.idea_type] = bucket.get(idea.idea_type, 0) + 1

    funnel = [{"status": s, "count": sum(1 for i in ideas if i.status == s)} for s in IDEA_STATUSES if any(i.status == s for i in ideas)]

    aging_now = []
    for idea in ideas:
        if idea.status in TERMINAL_STATUSES:
            continue
        m = compute_stage_metrics(all_intervals[idea.id], idea.created_at, now)
        aging_now.append({
            "idea_id": idea.id,
            "title": idea.title,
            "status": idea.status,
            "days_in_current_status": m["days_in_current_status"],
        })
    aging_now.sort(key=lambda x: x["days_in_current_status"], reverse=True)

    top_voted = sorted(
        [i for i in ideas if i.idea_type in VOTABLE_TYPES and i.status not in TERMINAL_STATUSES and i.status != "EM_PRODUCAO"],
        key=lambda x: x.priority_score,
        reverse=True,
    )[:10]

    def row_dict(idea: Idea, metrics: dict) -> dict:
        d = idea_to_dict(idea, metrics)
        return d

    stage_by_type = _stage_group(shipped)
    stage_by_diff = _stage_group(shipped, key="difficulty")

    return {
        "kpis": {
            "implemented": len(shipped),
            "bugs_fixed": len(bugs_fixed),
            "bugs_critical_fixed": len(bugs_critical),
            "features_shipped": len(features),
            "panels_shipped": len(panels),
            "avg_time_to_start_days": avg_start,
            "p50_time_to_start_days": p50_start,
            "avg_time_in_impl_days": avg_impl,
            "p50_time_in_impl_days": p50_impl,
            "avg_time_to_prod_days": avg_prod,
            "p50_time_to_prod_days": p50_prod,
            "wip": len(wip),
            "cancelled": len(cancelled),
            "closed": len(closed),
        },
        "dwell_by_status": dwell_by_status_out,
        "stage_times_by_type": stage_by_type,
        "stage_times_by_difficulty": stage_by_diff,
        "monthly_shipped": [{"month": m, "by_type": t} for m, t in sorted(monthly.items())],
        "funnel": funnel,
        "aging_now": aging_now[:20],
        "shipped_rows": [row_dict(i, m) for i, m in shipped],
        "wip_rows": [row_dict(i, compute_stage_metrics(all_intervals[i.id], i.created_at, now)) for i in wip],
        "top_voted_open": [idea_to_dict(i) for i in top_voted],
    }


def _avg_p50(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    from statistics import median
    return round(sum(values) / len(values), 2), round(float(median(values)), 2)


def _stage_group(shipped: list, key: str = "idea_type") -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for idea, m in shipped:
        k = getattr(idea, key)
        groups.setdefault(k, []).append(m)
    out = []
    for k, metrics_list in groups.items():
        starts = [m["time_to_start_days"] for m in metrics_list if m.get("time_to_start_days") is not None]
        impls = [m["time_in_impl_days"] for m in metrics_list if m.get("time_in_impl_days") is not None]
        prods = [m["time_to_prod_days"] for m in metrics_list if m.get("time_to_prod_days") is not None]
        field = "type" if key == "idea_type" else key
        out.append({
            field: k,
            "time_to_start": _avg_p50(starts)[0],
            "time_in_impl": _avg_p50(impls)[0],
            "time_to_prod": _avg_p50(prods)[0],
        })
    return out


@router.get("/{idea_id}")
async def get_idea(idea_id: int, db: AsyncSession = Depends(get_db)):
    return await _idea_with_metrics(db, await _get_idea(db, idea_id))


@router.patch("/{idea_id}")
async def patch_idea(
    idea_id: int,
    body: IdeaPatch,
    db: AsyncSession = Depends(get_db),
):
    idea = await _get_idea(db, idea_id)
    if body.title is not None:
        idea.title = body.title
    if body.description is not None:
        idea.description = body.description
    if body.impact is not None:
        if body.impact not in IDEA_IMPACTS:
            raise HTTPException(422, "Impacto inválido")
        idea.impact = body.impact
        idea.priority_score = compute_priority_score(idea.votes_count, idea.impact)
    if body.difficulty is not None:
        if body.difficulty not in IDEA_DIFFICULTIES:
            raise HTTPException(422, "Dificuldade inválida")
        idea.difficulty = body.difficulty
    if body.due_date is not None:
        try:
            idea.due_date = date.fromisoformat(body.due_date) if body.due_date else None
        except ValueError:
            raise HTTPException(422, "Data de prazo inválida")
    await db.commit()
    return await _idea_with_metrics(db, await _get_idea(db, idea_id))


@router.delete("/{idea_id}", status_code=204)
async def delete_idea(idea_id: int, db: AsyncSession = Depends(get_db)):
    idea = await _get_idea(db, idea_id)
    await db.delete(idea)
    await db.commit()


@router.post("/{idea_id}/transition")
async def do_transition(
    idea_id: int,
    body: TransitionIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if body.status not in IDEA_STATUSES:
        raise HTTPException(422, "Status inválido")
    idea = await _get_idea(db, idea_id)
    try:
        await transition_idea(db, idea, body.status, user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await db.commit()
    return await _idea_with_metrics(db, await _get_idea(db, idea_id))


@router.get("/{idea_id}/timeline")
async def timeline(idea_id: int, db: AsyncSession = Depends(get_db)):
    idea = await _get_idea(db, idea_id)
    intervals = await load_intervals(db, idea.id)
    now = datetime.now(timezone.utc)
    metrics = compute_stage_metrics(intervals, idea.created_at, now)
    return {
        "idea_id": idea.id,
        "status": idea.status,
        "intervals": [
            {
                "id": iv.id,
                "status": iv.status,
                "from_status": iv.from_status,
                "entered_at": iv.entered_at.isoformat(),
                "exited_at": iv.exited_at.isoformat() if iv.exited_at else None,
                "duration_seconds": iv.duration_seconds or int((now - iv.entered_at).total_seconds()),
                "duration_days": round((iv.duration_seconds or int((now - iv.entered_at).total_seconds())) / 86400, 2),
            }
            for iv in intervals
        ],
        "metrics": metrics,
    }


@router.post("/{idea_id}/vote", status_code=201)
async def vote(
    idea_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    idea = await _get_idea(db, idea_id)
    if not can_vote(idea.idea_type):
        raise HTTPException(403, "Este tipo de ideia não aceita votação")
    existing = (await db.execute(
        select(IdeaVote).where(IdeaVote.idea_id == idea_id, IdeaVote.user_id == user.id)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "Você já votou nesta ideia")
    db.add(IdeaVote(idea_id=idea_id, user_id=user.id))
    idea.votes_count += 1
    idea.priority_score = compute_priority_score(idea.votes_count, idea.impact)
    await db.commit()
    return await _idea_with_metrics(db, await _get_idea(db, idea_id))


@router.delete("/{idea_id}/vote")
async def unvote(
    idea_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    idea = await _get_idea(db, idea_id)
    row = (await db.execute(
        select(IdeaVote).where(IdeaVote.idea_id == idea_id, IdeaVote.user_id == user.id)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Voto não encontrado")
    await db.delete(row)
    idea.votes_count = max(0, idea.votes_count - 1)
    idea.priority_score = compute_priority_score(idea.votes_count, idea.impact)
    await db.commit()
    return await _idea_with_metrics(db, await _get_idea(db, idea_id))


@router.get("/{idea_id}/comments")
async def list_comments(idea_id: int, db: AsyncSession = Depends(get_db)):
    await _get_idea(db, idea_id)
    rows = (await db.execute(
        select(IdeaComment).where(IdeaComment.idea_id == idea_id).order_by(IdeaComment.created_at)
    )).scalars().all()
    out = []
    for c in rows:
        author = await db.get(User, c.author_id)
        out.append({
            "id": c.id,
            "body": c.body,
            "author_id": c.author_id,
            "author": {"id": c.author_id, "username": author.username if author else None, "full_name": author.full_name if author else None},
            "created_at": c.created_at.isoformat() if c.created_at else None,
        })
    return out


@router.post("/{idea_id}/comments", status_code=201)
async def add_comment(
    idea_id: int,
    body: CommentIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_idea(db, idea_id)
    row = IdeaComment(idea_id=idea_id, author_id=user.id, body=body.body)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {
        "id": row.id,
        "body": row.body,
        "author_id": user.id,
        "author": {"id": user.id, "username": user.username, "full_name": user.full_name},
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.put("/{idea_id}/assignees")
async def set_assignees(
    idea_id: int,
    body: AssigneesIn,
    db: AsyncSession = Depends(get_db),
):
    idea = await _get_idea(db, idea_id)
    for a in list(idea.assignees):
        await db.delete(a)
    for uid in body.user_ids:
        u = await db.get(User, uid)
        if not u:
            raise HTTPException(404, f"Usuário {uid} não encontrado")
        db.add(IdeaAssignee(idea_id=idea_id, user_id=uid))
    await db.commit()
    return await _idea_with_metrics(db, await _get_idea(db, idea_id))
