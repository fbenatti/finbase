"""AI cost origin portal — CRUD for cost links, preview, manual entries, and apply."""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.ai_cost import (
    AiCostAllocation,
    AiCostLink,
    AiCostLinkSource,
    DISTRIBUTION_BASES,
    DISTRIBUTION_USER_KEYS,
    VALUE_COLUMNS,
)
from app.models.destination import DestinationTable
from app.services.ai_cost_attribution import (
    apply_links,
    count_active_licenses,
    link_applies,
    preview_source,
)
from app.services.manual_ai_cost_extractor import (
    build_entry_row,
    ensure_manual_ai_cost_connection,
    write_manual_entry_to_bronze,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-cost-origin", tags=["ai-cost-origin"])


class FilterCondition(BaseModel):
    column: str
    operator: str = "IN"
    values: list[str] = []


class LinkSourceIn(BaseModel):
    connection_id: int | None = None
    filter_json: dict[str, Any] | list[Any] = Field(default_factory=dict)
    value_column: str = "BilledCost"
    percent: float = 100.0


class LinkSourceOut(BaseModel):
    id: int
    connection_id: int | None
    filter_json: dict[str, Any]
    value_column: str
    percent: float

    model_config = {"from_attributes": True}


class LinkCreate(BaseModel):
    name: str
    provider: str
    product_name: str
    license_sku: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    active: bool = True
    distribution_enabled: bool = False
    distribution_basis: Literal["tokens", "requests", "activity_index", "chatturns", "equal"] | None = None
    distribution_user_key: Literal["email", "login"] | None = "email"
    unit_cost: float | None = None
    sources: list[LinkSourceIn] = Field(default_factory=list)


class LinkUpdate(BaseModel):
    name: str | None = None
    provider: str | None = None
    product_name: str | None = None
    license_sku: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    active: bool | None = None
    distribution_enabled: bool | None = None
    distribution_basis: Literal["tokens", "requests", "activity_index", "chatturns", "equal"] | None = None
    distribution_user_key: Literal["email", "login"] | None = None
    unit_cost: float | None = None
    sources: list[LinkSourceIn] | None = None


class LinkOut(BaseModel):
    id: int
    name: str
    provider: str
    product_name: str
    license_sku: str | None
    valid_from: str | None
    valid_until: str | None
    active: bool
    distribution_enabled: bool
    distribution_basis: str | None
    distribution_user_key: str | None
    unit_cost: float | None = None
    sources: list[LinkSourceOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class PreviewRequest(BaseModel):
    billing_period: str
    filter_json: dict[str, Any] = Field(default_factory=dict)
    value_column: str = "BilledCost"
    connection_id: int | None = None
    percent: float = 100.0


class ManualEntryRequest(BaseModel):
    provider: str
    product_name: str
    license_sku: str | None = None
    billing_period: str
    currency: str = "USD"
    unit_price: float
    contract_ref: str | None = None
    charge_description: str | None = None
    trigger_reprocess: bool = True


class ApplyRequest(BaseModel):
    billing_period: str
    link_ids: list[int] | None = None
    dry_run: bool = False


def _link_out(link: AiCostLink) -> LinkOut:
    return LinkOut(
        id=link.id,
        name=link.name,
        provider=link.provider,
        product_name=link.product_name,
        license_sku=link.license_sku,
        valid_from=link.valid_from,
        valid_until=link.valid_until,
        active=link.active,
        distribution_enabled=link.distribution_enabled,
        distribution_basis=link.distribution_basis,
        distribution_user_key=link.distribution_user_key,
        unit_cost=link.unit_cost,
        sources=[LinkSourceOut.model_validate(source) for source in (link.sources or [])],
    )


def _validate_link_payload(data: LinkCreate | LinkUpdate, *, creating: bool = False) -> None:
    if creating:
        if not data.name or not data.provider or not data.product_name:  # type: ignore[union-attr]
            raise HTTPException(status_code=422, detail="name, provider e product_name são obrigatórios")
    basis = getattr(data, "distribution_basis", None)
    if basis and basis not in DISTRIBUTION_BASES:
        raise HTTPException(status_code=422, detail="distribution_basis inválida")
    key = getattr(data, "distribution_user_key", None)
    if key and key not in DISTRIBUTION_USER_KEYS:
        raise HTTPException(status_code=422, detail="distribution_user_key inválida")
    sources = getattr(data, "sources", None) or []
    for source in sources:
        if source.value_column not in VALUE_COLUMNS:
            raise HTTPException(status_code=422, detail=f"value_column inválida: {source.value_column}")


async def _load_links(db: AsyncSession, link_ids: list[int] | None = None) -> list[AiCostLink]:
    stmt = select(AiCostLink).options(selectinload(AiCostLink.sources)).order_by(AiCostLink.id)
    if link_ids:
        stmt = stmt.where(AiCostLink.id.in_(link_ids))
    return list((await db.execute(stmt)).scalars().all())


def _replace_sources(link: AiCostLink, sources: list[LinkSourceIn]) -> None:
    link.sources.clear()
    for item in sources:
        raw = item.filter_json or {}
        if isinstance(raw, list):
            raw = {"conditions": list(raw)}
        link.sources.append(AiCostLinkSource(
            connection_id=item.connection_id,
            filter_json=raw,
            value_column=item.value_column,
            percent=float(item.percent or 100),
        ))


@router.get("/links", response_model=list[LinkOut])
async def list_links(db: AsyncSession = Depends(get_db)):
    return [_link_out(link) for link in await _load_links(db)]


@router.post("/links", response_model=LinkOut, status_code=201)
async def create_link(data: LinkCreate, db: AsyncSession = Depends(get_db)):
    _validate_link_payload(data, creating=True)
    link = AiCostLink(
        name=data.name.strip(),
        provider=data.provider.strip(),
        product_name=data.product_name.strip(),
        license_sku=(data.license_sku or "").strip() or None,
        valid_from=data.valid_from,
        valid_until=data.valid_until,
        active=data.active,
        distribution_enabled=data.distribution_enabled,
        distribution_basis=data.distribution_basis,
        distribution_user_key=data.distribution_user_key,
        unit_cost=data.unit_cost,
    )
    _replace_sources(link, data.sources)
    db.add(link)
    await db.commit()
    link = (await db.execute(
        select(AiCostLink).options(selectinload(AiCostLink.sources)).where(AiCostLink.id == link.id)
    )).scalar_one()
    return _link_out(link)


@router.get("/links/{link_id}", response_model=LinkOut)
async def get_link(link_id: int, db: AsyncSession = Depends(get_db)):
    link = (await db.execute(
        select(AiCostLink).options(selectinload(AiCostLink.sources)).where(AiCostLink.id == link_id)
    )).scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=404, detail="Vínculo não encontrado")
    return _link_out(link)


@router.put("/links/{link_id}", response_model=LinkOut)
async def update_link(link_id: int, data: LinkUpdate, db: AsyncSession = Depends(get_db)):
    _validate_link_payload(data)
    link = (await db.execute(
        select(AiCostLink).options(selectinload(AiCostLink.sources)).where(AiCostLink.id == link_id)
    )).scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=404, detail="Vínculo não encontrado")
    for field in ("name", "provider", "product_name", "license_sku", "valid_from", "valid_until", "active", "distribution_enabled", "distribution_basis", "distribution_user_key", "unit_cost"):
        value = getattr(data, field)
        if value is not None:
            setattr(link, field, value.strip() if isinstance(value, str) else value)
    if data.sources is not None:
        _replace_sources(link, data.sources)
    await db.commit()
    link = (await db.execute(
        select(AiCostLink).options(selectinload(AiCostLink.sources)).where(AiCostLink.id == link_id)
    )).scalar_one()
    return _link_out(link)


@router.delete("/links/{link_id}", status_code=204)
async def delete_link(link_id: int, db: AsyncSession = Depends(get_db)):
    link = await db.get(AiCostLink, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Vínculo não encontrado")
    await db.execute(delete(AiCostAllocation).where(AiCostAllocation.link_id == link_id))
    await db.delete(link)
    await db.commit()


@router.get("/unlinked")
async def list_unlinked(
    billing_period: str | None = Query(None, description="YYYYMM — defaults to latest in licenses table"),
    db: AsyncSession = Depends(get_db),
):
    from app.services.trino_service import execute as trino_execute

    period_filter = ""
    if billing_period:
        period_filter = f"WHERE CAST(\"_billing_period\" AS VARCHAR) = '{billing_period}'"
    sql = f"""
      SELECT DISTINCT
             CAST("Provider" AS VARCHAR) AS provider,
             CAST("ProductName" AS VARCHAR) AS product_name,
             CAST("LicenseSkuName" AS VARCHAR) AS license_sku,
             CAST("_billing_period" AS VARCHAR) AS billing_period,
             COUNT(DISTINCT "UserEmail") AS license_count
      FROM iceberg.gold.dest_licencas_ia
      {period_filter}
      GROUP BY 1, 2, 3, 4
      ORDER BY 1, 2, 3
    """
    try:
        license_rows = await trino_execute(sql, timeout_s=120)
    except Exception as exc:
        logger.warning("Unlinked license query failed: %s", exc)
        license_rows = []

    links = await _load_links(db)
    covered: set[tuple[str, str, str | None]] = set()
    for link in links:
        if not link.active:
            continue
        key = (link.provider.lower(), link.product_name.lower(), (link.license_sku or "").lower() or None)
        covered.add(key)

    unlinked = []
    linked = []
    for row in license_rows:
        sku = (row.get("license_sku") or "").strip() or None
        key = (str(row.get("provider") or "").lower(), str(row.get("product_name") or "").lower(), (sku or "").lower() or None)
        is_linked = key in covered
        item = {
            "provider": row.get("provider"),
            "product_name": row.get("product_name"),
            "license_sku": sku,
            "billing_period": row.get("billing_period"),
            "license_count": int(row.get("license_count") or 0),
            "has_link": is_linked,
        }
        if is_linked:
            linked.append(item)
        else:
            unlinked.append(item)
    return {"unlinked": unlinked, "linked": linked, "billing_period": billing_period}


@router.post("/preview")
async def preview_filter(body: PreviewRequest):
    if body.value_column not in VALUE_COLUMNS:
        raise HTTPException(status_code=422, detail="value_column inválida")
    try:
        return await preview_source(
            billing_period=body.billing_period,
            filter_json=body.filter_json,
            value_column=body.value_column,
            connection_id=body.connection_id,
            percent=body.percent,
        )
    except Exception as exc:
        logger.exception("Preview failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc



@router.post("/manual-entry/preview")
async def preview_manual_entry(body: ManualEntryRequest):
    """Preview manual license cost without persisting anything."""
    active_count = await count_active_licenses(
        billing_period=body.billing_period,
        provider=body.provider,
        product_name=body.product_name,
        license_sku=body.license_sku,
    )
    total = float(body.unit_price or 0) * active_count
    return {
        "billing_period": body.billing_period,
        "provider": body.provider,
        "product_name": body.product_name,
        "license_sku": body.license_sku,
        "active_license_count": active_count,
        "unit_cost": float(body.unit_price or 0),
        "total_cost": total,
    }


@router.post("/manual-entry")
async def create_manual_entry(body: ManualEntryRequest, db: AsyncSession = Depends(get_db)):
    from app.database import SyncSession

    active_count = await count_active_licenses(
        billing_period=body.billing_period,
        provider=body.provider,
        product_name=body.product_name,
        license_sku=body.license_sku,
    )
    row = build_entry_row(
        provider=body.provider,
        product_name=body.product_name,
        license_sku=body.license_sku,
        billing_period=body.billing_period,
        currency=body.currency,
        unit_price=body.unit_price,
        active_licenses=active_count,
        contract_ref=body.contract_ref,
        charge_description=body.charge_description,
    )

    def _persist() -> dict[str, Any]:
        session = SyncSession()
        try:
            conn = ensure_manual_ai_cost_connection(session)
            result = write_manual_entry_to_bronze(session, conn, row)
            session.commit()
            return result
        finally:
            session.close()

    import asyncio
    loop = asyncio.get_running_loop()
    bronze_result = await loop.run_in_executor(None, _persist)

    reprocess_task_id = None
    if body.trigger_reprocess:
        focus = (await db.execute(
            select(DestinationTable).where(DestinationTable.physical_name == "dest_modelo_focus", DestinationTable.active == True)  # noqa: E712
        )).scalar_one_or_none()
        if focus:
            from app.models.etl_task import EtlTask
            from app.services.task_runner import _inmemory_tasks

            task = EtlTask(
                task_type="reprocess",
                status="QUEUED",
                destination_table_id=focus.id,
                connection_ids=[bronze_result["connection_id"]],
                billing_periods=[body.billing_period],
            )
            db.add(task)
            await db.flush()
            reprocess_task_id = task.id
            _inmemory_tasks[task.id] = {
                "id": task.id,
                "task_type": "reprocess",
                "status": "QUEUED",
                "destination_table_id": focus.id,
            }
            await db.commit()

    return {
        "bronze": bronze_result,
        "active_licenses": active_count,
        "preview": {
            "unit_price": body.unit_price,
            "billed_cost": row["billed_cost"],
            "consumed_quantity": row["consumed_quantity"],
        },
        "reprocess_task_id": reprocess_task_id,
    }


@router.post("/apply")
async def apply_cost_links(body: ApplyRequest, db: AsyncSession = Depends(get_db)):
    links = await _load_links(db, body.link_ids)
    active_links = [link for link in links if link_applies(link, body.billing_period)]
    if body.link_ids and not active_links:
        raise HTTPException(status_code=404, detail="Nenhum vínculo aplicável encontrado")

    from app.database import SyncSession

    def _apply_sync() -> dict[str, Any]:
        session = SyncSession()
        try:
            sync_links = session.query(AiCostLink).options(selectinload(AiCostLink.sources)).all()
            if body.link_ids:
                wanted = set(body.link_ids)
                sync_links = [link for link in sync_links if link.id in wanted]
            import asyncio
            result = asyncio.run(apply_links(session, sync_links, body.billing_period, dry_run=body.dry_run))
            if not body.dry_run:
                session.commit()
            else:
                session.rollback()
            return {
                "billing_period": result.billing_period,
                "links_processed": result.links_processed,
                "total_cost": result.total_cost,
                "allocations_written": result.allocations_written,
                "conflicts": result.conflicts,
                "unmapped_links": result.unmapped_links,
                "dry_run": body.dry_run,
            }
        finally:
            session.close()

    import asyncio
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _apply_sync)
    except Exception as exc:
        logger.exception("Apply failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc