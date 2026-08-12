from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text
from app.models.period_status import PeriodStatus
from app.models.ingested_blob import IngestedBlob


async def ensure_period(
    db: AsyncSession,
    connection_id: int,
    billing_period: str,
    auto_close_days: int = 7,
) -> PeriodStatus:
    result = await db.execute(
        select(PeriodStatus).where(
            PeriodStatus.connection_id == connection_id,
            PeriodStatus.billing_period == billing_period,
        )
    )
    period = result.scalar_one_or_none()
    if period is None:
        period = PeriodStatus(
            connection_id=connection_id,
            billing_period=billing_period,
            status="OPEN",
            auto_close_days=auto_close_days,
        )
        db.add(period)
        await db.flush()
    return period


async def can_process_period(
    db: AsyncSession,
    connection_id: int,
    billing_period: str,
    force: bool = False,
) -> bool:
    if force:
        return True

    result = await db.execute(
        select(PeriodStatus).where(
            PeriodStatus.connection_id == connection_id,
            PeriodStatus.billing_period == billing_period,
        )
    )
    period = result.scalar_one_or_none()
    if period is None:
        return True
    return period.status == "OPEN"


async def close_period(
    db: AsyncSession,
    connection_id: int,
    billing_period: str,
    closed_by: str = "system",
) -> PeriodStatus:
    period = await ensure_period(db, connection_id, billing_period)
    period.status = "CLOSED"
    period.closed_at = datetime.now(timezone.utc)
    period.closed_by = closed_by
    await db.flush()
    return period


async def reopen_period(
    db: AsyncSession,
    connection_id: int,
    billing_period: str,
) -> PeriodStatus:
    period = await ensure_period(db, connection_id, billing_period)
    period.status = "OPEN"
    period.closed_at = None
    period.closed_by = None
    await db.flush()
    return period


async def auto_close_unchanged_periods(
    db: AsyncSession,
    connection_id: int,
) -> list[str]:
    closed_periods: list[str] = []

    result = await db.execute(
        select(PeriodStatus).where(
            PeriodStatus.connection_id == connection_id,
            PeriodStatus.status == "OPEN",
        )
    )
    open_periods = result.scalars().all()

    for period in open_periods:
        result = await db.execute(
            select(func.max(IngestedBlob.last_modified)).where(
                IngestedBlob.connection_id == connection_id,
                IngestedBlob.billing_period == period.billing_period,
            )
        )
        last_modified = result.scalar_one()
        if last_modified is None:
            continue

        age_days = (datetime.now(timezone.utc) - last_modified.replace(tzinfo=timezone.utc)).days
        if age_days >= period.auto_close_days:
            period.status = "CLOSED"
            period.closed_at = datetime.now(timezone.utc)
            period.closed_by = "auto"
            closed_periods.append(period.billing_period)

    if closed_periods:
        await db.flush()

    return closed_periods


async def get_periods_report(
    db: AsyncSession,
    connection_id: int,
) -> list[dict]:
    result = await db.execute(
        select(PeriodStatus).where(
            PeriodStatus.connection_id == connection_id
        ).order_by(PeriodStatus.billing_period.desc())
    )
    periods = result.scalars().all()

    report = []
    for period in periods:
        total = await db.execute(
            select(func.coalesce(func.sum(IngestedBlob.row_count), 0)).where(
                IngestedBlob.connection_id == connection_id,
                IngestedBlob.billing_period == period.billing_period,
            )
        )
        total_lines = total.scalar() or 0
        # Classification percentages are computed from Gold/Iceberg, not SQL rows.
        classified_lines = 0

        classified_pct = (classified_lines / total_lines * 100) if total_lines > 0 else 0.0

        report.append({
            "billing_period": period.billing_period,
            "connection_id": connection_id,
            "status": period.status,
            "auto_close_days": period.auto_close_days,
            "closed_at": period.closed_at,
            "total_lines": total_lines,
            "classified_pct": round(classified_pct, 2),
        })

    return report
