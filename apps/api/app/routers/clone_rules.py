from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.clone_rule import CloneRule, ClonePricePendencies
from app.models.destination import DestinationColumn
from app.models.destination import DestinationTable, SourceDestinationMapping
from app.models.source_connection import SourceConnection

router = APIRouter(prefix="/clone-rules", tags=["clone-rules"])

class CloneRuleIn(BaseModel):
    destination_table_id: int; connection_id: int | None = None; name: str; description: str | None = None
    provider: str | None = None; conditions: list[dict[str, Any]] = []; roles: dict[str, str] = {}
    original_changes: dict[str, Any] = {}; clone_changes: dict[str, Any] = {}
    valid_from: str | None = None; valid_until: str | None = None; priority: int = 0; active: bool = True

def _out(row): return {c.name: getattr(row, c.name) for c in row.__table__.columns}

async def ensure_focus_templates(db: AsyncSession) -> None:
    """Install editable Azure templates and preserve raw semantic fields once."""
    focus=(await db.execute(select(DestinationTable).where(DestinationTable.active==True))).scalars().all()
    for dest in focus:
        if "focus" not in f"{dest.name} {dest.physical_name}".lower(): continue
        columns=(await db.execute(select(DestinationColumn).where(DestinationColumn.destination_table_id==dest.id))).scalars().all()
        existing={c.column_name for c in columns}; position=max([c.position for c in columns] or [0])
        additions={"source_meter_id":"TEXT","source_unit_price":"NUMERIC","source_exchange_rate":"NUMERIC","source_pricing_model":"TEXT","source_charge_type":"TEXT","source_frequency":"TEXT","source_reservation_id":"TEXT","source_reservation_name":"TEXT","source_record_id":"TEXT","clone_source_record_id":"TEXT","is_synthetic":"BOOLEAN","clone_rule_id":"INTEGER","clone_rule_version":"INTEGER","ondemand_reference_price":"NUMERIC","ondemand_reference_period":"TEXT"}
        for name,typ in additions.items():
            if name not in existing:
                position+=1; db.add(DestinationColumn(destination_table_id=dest.id,column_name=name,data_type=typ,position=position))
        connections=(await db.execute(select(SourceConnection).where(SourceConnection.code.ilike("azure%")))).scalars().all()
        raw_map={"meterId":"source_meter_id","unitPrice":"source_unit_price","exchangeRatePricingToBilling":"source_exchange_rate","pricingModel":"source_pricing_model","chargeType":"source_charge_type","frequency":"source_frequency","reservationId":"source_reservation_id","reservationName":"source_reservation_name"}
        for conn in connections:
            # These legacy mappings conflate unrelated source semantics and
            # make an On-Demand reconstruction impossible.
            stale = (await db.execute(select(SourceDestinationMapping).where(
                SourceDestinationMapping.source_connection_id == conn.id,
                SourceDestinationMapping.destination_table_id == dest.id,
                ((SourceDestinationMapping.source_column == "meterId") & (SourceDestinationMapping.destination_column == "RegionId")) |
                ((SourceDestinationMapping.source_column.in_(["unitPrice", "exchangeRatePricingToBilling"])) & (SourceDestinationMapping.destination_column == "PricingCurrencyContractedUnitPrice")),
            ))).scalars().all()
            for item in stale: await db.delete(item)
            for source,target in raw_map.items():
                exists=(await db.execute(select(SourceDestinationMapping.id).where(SourceDestinationMapping.source_connection_id==conn.id,SourceDestinationMapping.destination_table_id==dest.id,SourceDestinationMapping.source_column==source,SourceDestinationMapping.destination_column==target))).scalar_one_or_none()
                if not exists: db.add(SourceDestinationMapping(source_connection_id=conn.id,destination_table_id=dest.id,name=f"[Clone] {source} → {target}",source_column=source,destination_column=target,priority=900,active=True))
        await db.flush()
        canonical={c.column_name.lower():c.column_name for c in (await db.execute(select(DestinationColumn).where(DestinationColumn.destination_table_id==dest.id))).scalars().all()}
        charge=canonical.get("chargecategory", "ChargeCategory"); commitment=canonical.get("commitmentdiscounttype", "CommitmentDiscountType"); quantity=canonical.get("consumedquantity", "ConsumedQuantity"); billed=canonical.get("billedcost", "BilledCost"); effective=canonical.get("effectivecost", "EffectiveCost")
        roles={"meter_id":"source_meter_id","unit_price":"source_unit_price","quantity":quantity,"exchange_rate":"source_exchange_rate","pricing_model":"source_pricing_model","on_demand_value":"OnDemand","billed_cost":billed,"effective_cost":effective}
        for suffix,model in (("Reservation","Reservation"),("Savings Plan","SavingsPlan")):
            exists=(await db.execute(select(CloneRule.id).where(CloneRule.destination_table_id==dest.id,CloneRule.name==f"Azure {suffix} Usage → Return"))).scalar_one_or_none()
            values={"conditions":[{"field":charge,"op":"=","value":"Usage"},{"field":commitment,"op":"=","value":model},{"field":quantity,"op":">","value":0}],"roles":roles,"clone_changes":{charge:"Return"}}
            if not exists:
                db.add(CloneRule(destination_table_id=dest.id,name=f"Azure {suffix} Usage → Return",description="Template editável: repõe custo On-Demand e cria Return negativo",provider="azure",priority=10 if model=="Reservation" else 11,**values))
            else:
                row=await db.get(CloneRule, exists); row.conditions=values["conditions"]; row.roles=roles; row.clone_changes=values["clone_changes"]
    await db.commit()

async def _validate(db: AsyncSession, body: CloneRuleIn):
    schema = set((await db.execute(select(DestinationColumn.column_name).where(DestinationColumn.destination_table_id == body.destination_table_id))).scalars().all())
    role_fields = [value for key, value in body.roles.items() if key != "on_demand_value"]
    change_fields = [*body.original_changes.keys(), *body.clone_changes.keys()]
    copy_sources = [
        change.get("value") for change in [*body.original_changes.values(), *body.clone_changes.values()]
        if isinstance(change, dict) and str(change.get("operation", "")).upper() == "COPY_COLUMN"
    ]
    unknown = sorted({field for field in [*(c.get("field") for c in body.conditions), *role_fields, *change_fields, *copy_sources] if field and field not in schema})
    if unknown: raise HTTPException(422, f"Campos inexistentes no destino: {', '.join(unknown)}")
    required = {"meter_id", "quantity", "unit_price", "pricing_model", "billed_cost", "effective_cost"}
    missing = sorted(required - set(body.roles))
    if missing: raise HTTPException(422, f"Papéis obrigatórios ausentes: {', '.join(missing)}")
    supported_ops = {"=", "!=", "IN", "NOT_IN", "CONTAINS", "NOT_CONTAINS", "EMPTY", "NOT_EMPTY", ">", "<"}
    invalid_ops = sorted({str(c.get("op", "=")).upper() for c in body.conditions} - supported_ops)
    if invalid_ops: raise HTTPException(422, f"Operadores não permitidos: {', '.join(invalid_ops)}")
    supported_changes = {"SET", "ZERO", "NEGATE", "ABS", "COPY_COLUMN", "HISTORICAL_ON_DEMAND_COST"}
    invalid_changes = sorted({
        str(change.get("operation", "SET")).upper()
        for change in [*body.original_changes.values(), *body.clone_changes.values()]
        if isinstance(change, dict)
    } - supported_changes)
    if invalid_changes: raise HTTPException(422, f"Transformações não permitidas: {', '.join(invalid_changes)}")
    for label, period in (("valid_from", body.valid_from), ("valid_until", body.valid_until)):
        if period and (len(period) != 6 or not period.isdigit()):
            raise HTTPException(422, f"{label} deve estar no formato AAAAMM")
    if body.valid_from and body.valid_until and body.valid_from > body.valid_until:
        raise HTTPException(422, "O início da vigência não pode ser posterior ao fim")

@router.get("")
async def list_rules(destination_table_id: int | None = Query(None), db: AsyncSession = Depends(get_db)):
    stmt = select(CloneRule).order_by(CloneRule.priority, CloneRule.id)
    if destination_table_id: stmt = stmt.where(CloneRule.destination_table_id == destination_table_id)
    return [_out(x) for x in (await db.execute(stmt)).scalars().all()]

@router.post("", status_code=201)
async def create_rule(body: CloneRuleIn, db: AsyncSession = Depends(get_db)):
    await _validate(db, body); row = CloneRule(**body.model_dump()); db.add(row); await db.commit(); await db.refresh(row); return _out(row)

@router.put("/{rule_id}")
async def update_rule(rule_id: int, body: CloneRuleIn, db: AsyncSession = Depends(get_db)):
    row = await db.get(CloneRule, rule_id)
    if not row: raise HTTPException(404, "Regra de clonagem não encontrada")
    await _validate(db, body)
    for k,v in body.model_dump().items(): setattr(row,k,v)
    row.version += 1; await db.commit(); await db.refresh(row); return _out(row)

@router.post("/{rule_id}/duplicate", status_code=201)
async def duplicate_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    src=await db.get(CloneRule, rule_id)
    if not src: raise HTTPException(404, "Regra de clonagem não encontrada")
    data={c.name:getattr(src,c.name) for c in src.__table__.columns if c.name not in {"id","created_at","updated_at","version"}}
    data["name"] += " (cópia)"; row=CloneRule(**data); db.add(row); await db.commit(); await db.refresh(row); return _out(row)

@router.post("/{rule_id}/validate")
async def validate_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    row=await db.get(CloneRule,rule_id)
    if not row: raise HTTPException(404, "Regra de clonagem não encontrada")
    await _validate(db, CloneRuleIn(**{c.name:getattr(row,c.name) for c in row.__table__.columns if c.name not in {"id","version","created_at","updated_at"}})); return {"valid":True}

@router.post("/{rule_id}/preview")
async def preview_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    row=await db.get(CloneRule,rule_id)
    if not row: raise HTTPException(404, "Regra de clonagem não encontrada")
    return {"rule":_out(row), "message":"A prévia é executada no próximo reprocessamento, usando dados Bronze/Silver e preços históricos."}

@router.delete("/{rule_id}", status_code=204)
async def delete_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    row=await db.get(CloneRule,rule_id)
    if not row: raise HTTPException(404, "Regra de clonagem não encontrada")
    await db.delete(row); await db.commit()

@router.get("/price-pendencies")
async def pendencies(destination_table_id: int | None = Query(None), db: AsyncSession = Depends(get_db)):
    stmt=select(ClonePricePendencies).order_by(ClonePricePendencies.created_at.desc()).limit(1000)
    if destination_table_id: stmt=stmt.where(ClonePricePendencies.destination_table_id==destination_table_id)
    return [_out(x) for x in (await db.execute(stmt)).scalars().all()]
