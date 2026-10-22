from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from app.database import get_db
from app.models.fact_cost import FactCost
from app.models.source_connection import SourceConnection
from app.services.fact_service import populate_fact_from_normalized, export_fact_to_parquet

router = APIRouter(prefix="/dados/fato", tags=["fact"])


def _parse_conn_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    try:
        return [int(x.strip()) for x in raw.split(",") if x.strip()]
    except ValueError:
        return []


@router.post("/popular/{connection_id}")
async def populate_fact(
    connection_id: int,
    billing_period: str | None = Query(None, description="Specific billing period (YYYYMM)"),
    db: AsyncSession = Depends(get_db),
):
    try:
        count = await populate_fact_from_normalized(db, connection_id, billing_period)
        return {"status": "OK", "rows_inserted": count, "connection_id": connection_id, "billing_period": billing_period or "all"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/consulta")
async def query_fact(
    connection_ids: str | None = Query(None, description="Comma-separated connection IDs, e.g. '1,2'"),
    billing_period: str | None = Query(None, description="Billing period (YYYYMM)"),
    cost_type: str | None = Query(None, description="actual | amortized"),
    limit: int = Query(100, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Query fact_cost with optional filters. Returns rows as JSON."""
    stmt = select(FactCost)
    conditions = []

    ids = _parse_conn_ids(connection_ids)
    if ids:
        conditions.append(FactCost.connection_id.in_(ids))
    if billing_period:
        conditions.append(FactCost.billing_period == billing_period)
    if cost_type:
        conditions.append(FactCost.cost_type == cost_type)

    if conditions:
        stmt = stmt.where(and_(*conditions))

    count_stmt = select(func.count()).select_from(FactCost)
    if conditions:
        count_stmt = count_stmt.where(and_(*conditions))
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = stmt.order_by(FactCost.billing_period.desc(), FactCost.date).limit(limit).offset(offset)
    result = await db.execute(stmt)
    rows = result.scalars().all()

    # Connection name lookup
    conn_lookup: dict[int, str] = {}
    unique_cids = {r.connection_id for r in rows}
    if unique_cids:
        cn_rows = await db.execute(
            select(SourceConnection.id, SourceConnection.code, SourceConnection.name)
            .where(SourceConnection.id.in_(unique_cids))
        )
        for cid, code, name in cn_rows.all():
            conn_lookup[cid] = f"{name} ({code})"

    items = []
    for r in rows:
        items.append({
            "id": r.id,
            "connection_id": r.connection_id,
            "connection_name": conn_lookup.get(r.connection_id, f"#{r.connection_id}"),
            "billing_period": r.billing_period,
            "cost_type": r.cost_type,
            "date": r.date,
            "subscription_name": r.subscription_name,
            "resource_group": r.resource_group,
            "service_name": r.service_name,
            "meter_category": r.meter_category,
            "resource_type": r.resource_type,
            "region": r.region,
            "currency": r.currency,
            "quantity": r.quantity,
            "effective_price": r.effective_price,
            "cost_usd": r.cost_usd,
            "cost_brl": r.cost_brl,
            "tax_base": r.tax_base,
            "tax_marketplace": r.tax_marketplace,
            "final_cost_brl": r.final_cost_brl,
            "diretoria": r.diretoria,
            "modelo_ai": r.modelo_ai,
            "blob_path": r.blob_path,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    return {"total": total, "limit": limit, "offset": offset, "rows": items}


@router.get("/resumo")
async def summary_fact(
    connection_ids: str | None = Query(None, description="Comma-separated connection IDs"),
    billing_period: str | None = Query(None, description="Billing period (YYYYMM)"),
    db: AsyncSession = Depends(get_db),
):
    """Aggregated summary from fact_cost: totals, by diretoria, by connection."""
    conditions = []
    ids = _parse_conn_ids(connection_ids)
    if ids:
        conditions.append(FactCost.connection_id.in_(ids))
    if billing_period:
        conditions.append(FactCost.billing_period == billing_period)

    def _apply(stmt_base):
        return stmt_base.where(and_(*conditions)) if conditions else stmt_base

    # Totals
    total_row = (await db.execute(_apply(select(
        func.count(FactCost.id),
        func.coalesce(func.sum(FactCost.cost_usd), 0),
        func.coalesce(func.sum(FactCost.cost_brl), 0),
        func.coalesce(func.sum(FactCost.final_cost_brl), 0),
        func.coalesce(func.sum(FactCost.tax_base), 0),
    ).select_from(FactCost)))).one()

    # Available periods
    p_stmt = select(func.distinct(FactCost.billing_period)).order_by(FactCost.billing_period.desc())
    if ids:
        p_stmt = _apply(p_stmt)
    periods = [p for (p,) in (await db.execute(p_stmt)).all()]

    # By diretoria
    dir_results = await db.execute(_apply(select(
        FactCost.diretoria, func.count(FactCost.id), func.coalesce(func.sum(FactCost.cost_brl), 0),
    ).select_from(FactCost).group_by(FactCost.diretoria).order_by(func.sum(FactCost.cost_brl).desc())))
    by_dir = [{"diretoria": d or "UNCLASSIFIED", "count": cnt, "cost_brl": round(float(c), 2)} for d, cnt, c in dir_results.all()]

    # By connection
    conn_results = await db.execute(_apply(select(
        FactCost.connection_id, func.count(FactCost.id), func.coalesce(func.sum(FactCost.cost_brl), 0),
    ).select_from(FactCost).group_by(FactCost.connection_id).order_by(func.sum(FactCost.cost_brl).desc())))
    conn_raw = conn_results.all()

    conn_lookup: dict[int, str] = {}
    unique_cids = {c for c, _, _ in conn_raw}
    if unique_cids:
        cn_rows = await db.execute(select(SourceConnection.id, SourceConnection.code, SourceConnection.name).where(SourceConnection.id.in_(unique_cids)))
        for cid, code, name in cn_rows.all():
            conn_lookup[cid] = f"{name} ({code})"

    by_conn = [{"connection_id": cid, "connection_name": conn_lookup.get(cid, f"#{cid}"), "count": cnt, "cost_brl": round(float(c), 2)} for cid, cnt, c in conn_raw]

    return {
        "total_rows": total_row[0],
        "total_cost_usd": round(float(total_row[1]), 2),
        "total_cost_brl": round(float(total_row[2]), 2),
        "total_final_cost_brl": round(float(total_row[3]), 2),
        "total_tax_base": round(float(total_row[4]), 2),
        "available_periods": periods,
        "by_diretoria": by_dir,
        "by_connection": by_conn,
    }


@router.post("/exportar")
async def export_fact(
    connection_id: int | None = Query(None),
    billing_period: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        file_path = await export_fact_to_parquet(db, connection_id, billing_period)
        if not file_path:
            raise HTTPException(status_code=404, detail="No data found for export")
        return FileResponse(file_path, media_type="application/octet-stream", filename=file_path.split("/")[-1])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
