from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.schemas.schemas import PeriodCloseRequest, PeriodConfigUpdate, PeriodStatusOut
from app.services import period_service

router = APIRouter(prefix="/etl/periods", tags=["periods"])


@router.get("/", response_model=list[PeriodStatusOut])
async def list_periods(
    connection_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
):
    return await period_service.get_periods_report(db, connection_id)


@router.post("/close", response_model=PeriodStatusOut)
async def close_period(
    request: PeriodCloseRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await period_service.close_period(
        db, request.connection_id, request.billing_period, closed_by="manual"
    )
    await db.commit()
    report = await period_service.get_periods_report(db, request.connection_id)
    match = [r for r in report if r["billing_period"] == request.billing_period]
    if match:
        return match[0]
    raise HTTPException(status_code=404, detail="Period not found")


@router.post("/reopen", response_model=PeriodStatusOut)
async def reopen_period(
    request: PeriodCloseRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await period_service.reopen_period(
        db, request.connection_id, request.billing_period
    )
    await db.commit()
    report = await period_service.get_periods_report(db, request.connection_id)
    match = [r for r in report if r["billing_period"] == request.billing_period]
    if match:
        return match[0]
    raise HTTPException(status_code=404, detail="Period not found")


@router.post("/auto-close", response_model=list[str])
async def auto_close(
    connection_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
):
    closed = await period_service.auto_close_unchanged_periods(db, connection_id)
    await db.commit()
    return closed


@router.put("/config/{connection_id}/{billing_period}", response_model=PeriodStatusOut)
async def update_period_config(
    connection_id: int,
    billing_period: str,
    data: PeriodConfigUpdate,
    db: AsyncSession = Depends(get_db),
):
    period = await period_service.ensure_period(db, connection_id, billing_period)
    if data.auto_close_days is not None:
        period.auto_close_days = data.auto_close_days
    await db.commit()
    report = await period_service.get_periods_report(db, connection_id)
    match = [r for r in report if r["billing_period"] == billing_period]
    if match:
        return match[0]
    raise HTTPException(status_code=404, detail="Period not found")
