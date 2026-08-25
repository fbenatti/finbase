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
    count_active_licenses,
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
    tool_connection_ids: list[int] = Field(default_factory=list)
    product_name: str
    target_type: Literal["license_pool", "ptu_pool", "payg_ai"] = "license_pool"
    operational_connection_id: int | None = None
    department_id: int | None = None
    project_key: str | None = None
    purchase_mode: str | None = None
    deployment_type: str | None = None
    cost_component: str | None = None
    allocation_level: str | None = None
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
    tool_connection_ids: list[int] | None = None
    product_name: str | None = None
    target_type: Literal["license_pool", "ptu_pool", "payg_ai"] | None = None
    operational_connection_id: int | None = None
    department_id: int | None = None
    project_key: str | None = None
    purchase_mode: str | None = None
    deployment_type: str | None = None
    cost_component: str | None = None
    allocation_level: str | None = None
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
    tool_connection_ids: list[int] = Field(default_factory=list)
    product_name: str
    target_type: str = "license_pool"
    operational_connection_id: int | None = None
    department_id: int | None = None
    project_key: str | None = None
    purchase_mode: str | None = None
    deployment_type: str | None = None
    cost_component: str | None = None
    allocation_level: str | None = None
    license_sku: str | None
    valid_from: str | None
    valid_until: str | None
    active: bool
    distribution_enabled: bool
    distribution_basis: str | None
    distribution_user_key: str | None
    unit_cost: float | None = None
    materialization_table: str | None = None
    materialization_status: str | None = None
    materialization_rows: int | None = None
    materialization_run_id: int | None = None
    materialized_at: str | None = None
    materialization_error: str | None = None
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


def _link_out(link: AiCostLink) -> LinkOut:
    return LinkOut(
        id=link.id,
        name=link.name,
        provider=link.provider,
        tool_connection_ids=list(getattr(link, "tool_connection_ids", None) or []),
        product_name=link.product_name,
        target_type=getattr(link, "target_type", None) or "license_pool",
        operational_connection_id=getattr(link, "operational_connection_id", None),
        department_id=getattr(link, "department_id", None),
        project_key=getattr(link, "project_key", None),
        purchase_mode=getattr(link, "purchase_mode", None),
        deployment_type=getattr(link, "deployment_type", None),
        cost_component=getattr(link, "cost_component", None),
        allocation_level=getattr(link, "allocation_level", None),
        license_sku=link.license_sku,
        valid_from=link.valid_from,
        valid_until=link.valid_until,
        active=link.active,
        distribution_enabled=link.distribution_enabled,
        distribution_basis=link.distribution_basis,
        distribution_user_key=link.distribution_user_key,
        unit_cost=link.unit_cost,
        materialization_table=getattr(link, "materialization_table", None),
        materialization_status=getattr(link, "materialization_status", None),
        materialization_rows=getattr(link, "materialization_rows", None),
        materialization_run_id=getattr(link, "materialization_run_id", None),
        materialized_at=link.materialized_at.isoformat() if getattr(link, "materialized_at", None) else None,
        materialization_error=getattr(link, "materialization_error", None),
        sources=[LinkSourceOut.model_validate(source) for source in (link.sources or [])],
    )


def _validate_link_payload(data: LinkCreate | LinkUpdate, *, creating: bool = False) -> None:
    if creating:
        if not data.name or not data.provider or not data.product_name:  # type: ignore[union-attr]
            raise HTTPException(status_code=422, detail="name, provider e product_name são obrigatórios")
    target_type = getattr(data, "target_type", None)
    if target_type and target_type not in {"license_pool", "ptu_pool", "payg_ai"}:
        raise HTTPException(status_code=422, detail="target_type inválido")
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
        tool_connection_ids=list(data.tool_connection_ids or []),
        product_name=data.product_name.strip(),
        target_type=data.target_type,
        operational_connection_id=data.operational_connection_id,
        department_id=data.department_id,
        project_key=data.project_key,
        purchase_mode=data.purchase_mode,
        deployment_type=data.deployment_type,
        cost_component=data.cost_component,
        allocation_level=data.allocation_level,
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
    link.materialization_status = "PENDING"
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
    for field in ("name", "provider", "product_name", "target_type", "operational_connection_id", "department_id", "project_key", "purchase_mode", "deployment_type", "cost_component", "allocation_level", "license_sku", "valid_from", "valid_until", "active", "distribution_enabled", "distribution_basis", "distribution_user_key", "unit_cost", "tool_connection_ids"):
        value = getattr(data, field)
        if value is not None:
            setattr(link, field, value.strip() if isinstance(value, str) else value)
    if data.sources is not None:
        _replace_sources(link, data.sources)
    link.materialization_status = "PENDING"
    link.materialization_error = None
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
    # The physical Gold is owned exclusively by this relationship ID.
    from app.services.ai_cost_materialization import drop_link_gold_table
    import asyncio
    await asyncio.get_running_loop().run_in_executor(None, drop_link_gold_table, link_id)


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


class PtuPreviewRequest(BaseModel):
    billing_period: str
    sources: list[LinkSourceIn] = Field(default_factory=list)
    project_key: str | None = None
    purchase_mode: str | None = None
    deployment_type: str | None = None
    operational_ptus: float | None = None


@router.post("/preview-ptu")
async def preview_ptu(body: PtuPreviewRequest, db: AsyncSession = Depends(get_db)):
    from app.services.azure_ptu_meters import is_azure_openai_ptu_meter, meter_deployment_type
    from app.services.ai_cost_attribution import preview_ptu_pool_allocation
    from app.models.ai_cost import AiCostLink
    from app.services.trino_service import execute as trino_execute

    focus = {"matched_rows": 0, "billed_cost": 0.0, "realized_cost": 0.0, "consumed_quantity": 0, "meters": [], "reservations": []}
    conflicts: list[str] = []
    for source in body.sources:
        raw = source.filter_json if isinstance(source.filter_json, dict) else {"conditions": source.filter_json}
        preview = await preview_source(
            billing_period=body.billing_period,
            filter_json=raw,
            value_column=source.value_column,
            connection_id=source.connection_id,
            percent=source.percent,
        )
        focus["matched_rows"] += int(preview.get("matched_rows") or 0)
        focus["realized_cost"] += float(preview.get("total_value") or 0)
        for sample in preview.get("sample_rows") or []:
            meter = sample.get("skumeter") or sample.get("SkuMeter") or sample.get("meter")
            if meter:
                focus["meters"].append(meter)
            if meter and body.deployment_type:
                focus_type = meter_deployment_type(str(meter))
                if focus_type and focus_type != body.deployment_type:
                    conflicts.append("METER_DEPLOYMENT_TYPE_MISMATCH")
            if meter and not is_azure_openai_ptu_meter(sku_meter=str(meter)):
                conflicts.append("METER_NOT_IN_PTU_ALLOWLIST")

    operational = {"deployments": None, "ptus": body.operational_ptus, "tokens": None, "utilization_avg": None}
    if body.project_key:
        try:
            rows = await trino_execute(f"""
              SELECT COUNT(DISTINCT CAST(deployment_name AS VARCHAR)) AS deployments,
                     COALESCE(SUM(TRY_CAST(provisioned_ptus AS DOUBLE)), 0) AS ptus,
                     COALESCE(SUM(TRY_CAST(total_tokens_sum AS DOUBLE)), 0) AS tokens,
                     AVG(TRY_CAST(utilization_avg AS DOUBLE)) AS utilization_avg
              FROM iceberg.gold.dest_azure_ptu_metrics
              WHERE CAST(_billing_period AS VARCHAR) = '{body.billing_period}'
                AND CAST(project_hint AS VARCHAR) = '{body.project_key}'
            """, timeout_s=90)
            if rows:
                row = rows[0]
                operational = {
                    "deployments": int(row.get("deployments") or 0),
                    "ptus": float(row.get("ptus") or 0) or body.operational_ptus,
                    "tokens": float(row.get("tokens") or 0),
                    "utilization_avg": float(row["utilization_avg"]) if row.get("utilization_avg") is not None else None,
                }
        except Exception:
            pass

    pool_allocations: list[dict[str, Any]] = []
    if body.project_key:
        link = AiCostLink(
            name="preview",
            provider="Azure",
            product_name="Azure OpenAI PTU",
            target_type="ptu_pool",
            project_key=body.project_key,
            purchase_mode=body.purchase_mode,
            deployment_type=body.deployment_type,
            allocation_level="project",
            active=True,
        )
        for source in body.sources:
            link.sources.append(AiCostLinkSource(
                filter_json=source.filter_json if isinstance(source.filter_json, dict) else {"conditions": source.filter_json},
                value_column=source.value_column,
                percent=source.percent,
                connection_id=source.connection_id,
            ))
        pool_allocations = await preview_ptu_pool_allocation(link, body.billing_period)

    financial_ptus = focus.get("consumed_quantity") or None
    operational_ptus = operational.get("ptus")
    difference = None
    if financial_ptus is not None and operational_ptus is not None:
        difference = float(operational_ptus) - float(financial_ptus)
    status = "CONFLICT" if conflicts else ("OK" if difference is not None and abs(difference) < 1 else "PARTIAL")

    return {
        "focus": focus,
        "operational": operational,
        "reconciliation": {
            "status": status,
            "financial_ptus": financial_ptus,
            "financial_cost": focus["realized_cost"],
            "operational_ptus": operational_ptus,
            "operational_utilization_avg": operational.get("utilization_avg"),
            "difference_ptus": difference,
            "conflicts": list(dict.fromkeys(conflicts)),
        },
        "pool_allocations": pool_allocations,
    }


@router.get("/ptu-conflicts")
async def list_ptu_conflicts(db: AsyncSession = Depends(get_db)):
    """Scan PTU pool links for meter allowlist and deployment-type mismatches."""
    from app.services.azure_ptu_meters import is_azure_openai_ptu_meter, meter_deployment_type

    links = await _load_links(db)
    conflicts: list[dict[str, Any]] = []
    for link in links:
        if (getattr(link, "target_type", None) or "license_pool") != "ptu_pool":
            continue
        link_conflicts: list[str] = []
        meters: list[str] = []
        for source in link.sources or []:
            raw = source.filter_json or {}
            conditions = raw.get("conditions", raw) if isinstance(raw, dict) else raw
            if isinstance(conditions, list):
                for cond in conditions:
                    col = str(cond.get("column") or "").lower()
                    if col in {"skumeter", "meter", "skuname"}:
                        for val in cond.get("values") or []:
                            meters.append(str(val))
                            if not is_azure_openai_ptu_meter(sku_meter=str(val)):
                                link_conflicts.append("METER_NOT_IN_PTU_ALLOWLIST")
                            if link.deployment_type:
                                detected = meter_deployment_type(str(val))
                                if detected and detected != link.deployment_type:
                                    link_conflicts.append("METER_DEPLOYMENT_TYPE_MISMATCH")
        if link_conflicts:
            conflicts.append({
                "link_id": link.id,
                "name": link.name,
                "project_key": link.project_key,
                "purchase_mode": link.purchase_mode,
                "deployment_type": link.deployment_type,
                "conflicts": list(dict.fromkeys(link_conflicts)),
                "meters": meters,
            })
    return {"conflicts": conflicts, "count": len(conflicts)}


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
