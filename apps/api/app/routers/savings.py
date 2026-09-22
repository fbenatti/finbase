from datetime import date, datetime, timezone
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.saving import SavingAction, SavingMeasurement
from app.models.destination import DestinationTable
from app.services.trino_service import execute as trino_execute
from app.services.saving_service import refresh_actions
from app.services import lakehouse_layers as layers

router = APIRouter(prefix="/savings", tags=["savings"])
class ActionIn(BaseModel):
 name: str = Field(min_length=2); description: str|None=None; category: str|None=None; owner: str|None=None; destination_table_id:int; date_column:str; cost_column:str; currency_column:str|None=None; filters:list[dict[str,Any]]=[]; start_date:str; duration_days:int=Field(ge=1,le=3660); baseline_monthly:float; projected_monthly:float
def out(a): return {c.name:getattr(a,c.name) for c in a.__table__.columns}
@router.get("/metadata/{table_id}")
async def metadata(table_id:int, db:AsyncSession=Depends(get_db)):
 d=await db.get(DestinationTable,table_id)
 if not d: raise HTTPException(404,"Tabela não encontrada")
 rows=await trino_execute(f"DESCRIBE iceberg.{layers.detail_layer()}.{d.physical_name.replace('-', '_').replace('.', '_')}")
 return {"columns":[{"name":str(x.get('Column') or x.get('column_name')),"type":str(x.get('Type') or x.get('data_type') or '')} for x in rows]}
@router.get("/actions")
async def actions(db:AsyncSession=Depends(get_db)):
 rows=(await db.execute(select(SavingAction).order_by(SavingAction.created_at.desc()))).scalars().all(); return [out(x) for x in rows]
@router.post("/actions",status_code=201)
async def create(body:ActionIn,db:AsyncSession=Depends(get_db)):
 if not await db.get(DestinationTable,body.destination_table_id): raise HTTPException(404,"Tabela GOLD não encontrada")
 try: date.fromisoformat(body.start_date)
 except ValueError: raise HTTPException(422,"Data de início inválida")
 status="EM_FILA" if date.fromisoformat(body.start_date)>date.today() else "EM_ANALISE"
 row=SavingAction(**body.model_dump(),status=status); db.add(row); await db.commit(); await db.refresh(row); return out(row)
@router.put("/actions/{action_id}")
async def update(action_id:int,body:ActionIn,db:AsyncSession=Depends(get_db)):
 row=await db.get(SavingAction,action_id)
 if not row: raise HTTPException(404,"Ação não encontrada")
 for k,v in body.model_dump().items(): setattr(row,k,v)
 if row.status in ("ERRO","CANCELADA"): row.status="EM_FILA" if date.fromisoformat(row.start_date)>date.today() else "EM_ANALISE"; row.error_message=None
 await db.commit(); await db.refresh(row); return out(row)
@router.post("/actions/{action_id}/cancel")
async def cancel(action_id:int,db:AsyncSession=Depends(get_db)):
 row=await db.get(SavingAction,action_id)
 if not row: raise HTTPException(404,"Ação não encontrada")
 row.status="CANCELADA"; row.completed_at=datetime.now(timezone.utc); await db.commit(); return out(row)
@router.post("/actions/{action_id}/refresh")
async def refresh(action_id:int,db:AsyncSession=Depends(get_db)):
 # service needs sync session; explicit endpoint uses its own unit of work in a thread-safe sync engine
 from app.database import SyncSession
 s=SyncSession(); refresh_actions(s); s.close()
 row=await db.get(SavingAction,action_id); return out(row)
@router.get("/actions/{action_id}/measurements")
async def measurements(action_id:int,db:AsyncSession=Depends(get_db)):
 rows=(await db.execute(select(SavingMeasurement).where(SavingMeasurement.action_id==action_id).order_by(SavingMeasurement.measured_date))).scalars().all(); return [out(x) for x in rows]
@router.get("/dashboard")
async def dashboard(year:int|None=None,db:AsyncSession=Depends(get_db)):
 acts=(await db.execute(select(SavingAction))).scalars().all(); ms=(await db.execute(select(SavingMeasurement).order_by(SavingMeasurement.measured_date))).scalars().all()
 def calc(items):
  proj=sum(x.projected_saving for x in items); real=sum(x.realized_saving for x in items); growth=sum(-x.realized_saving for x in items if x.realized_saving<0); return {"projected":proj,"realized":real,"growth":growth,"net":real,"assertiveness":(real/proj*100 if proj else 0)}
 historic=calc(ms); selected=[x for x in ms if not year or x.measured_date.startswith(str(year))]; annual=calc(selected)
 months={}
 for x in selected:
  bucket=months.setdefault(x.measured_date[:7],{"month":x.measured_date[:7],"projected":0,"realized":0}); bucket["projected"]+=x.projected_saving; bucket["realized"]+=x.realized_saving
 status={s:sum(1 for a in acts if a.status==s) for s in ["EM_FILA","EM_ANALISE","FINALIZADA","CANCELADA","ERRO"]}
 final=[a for a in acts if a.status=="FINALIZADA"]; historic["completed_count"]=len(final); annual["completed_count"]=sum(1 for a in final if a.completed_at and a.completed_at.year==(year or date.today().year))
 rank=sorted((a for a in final),key=lambda a:a.final_realized or 0)
 return {"historic":historic,"year":annual,"monthly":list(months.values()),"status":status,"top_savings":[out(x) for x in rank[-5:][::-1]],"top_growth":[out(x) for x in rank[:5]]}
