from datetime import date, datetime, timezone
from typing import Any
import re
from fastapi import APIRouter, Depends, HTTPException, Query
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


def _period(value: str, field: str) -> str:
 if not re.fullmatch(r"\d{6}", value or "") or not 1 <= int(value[4:]) <= 12:
  raise HTTPException(422, f"{field} deve estar no formato AAAAMM")
 return value


def _commitment_source_filter(source: str) -> str:
 if source not in {"all", "azure-ti", "azure-4p"}:
  raise HTTPException(422, "source deve ser all, azure-ti ou azure-4p")
 return "" if source == "all" else f"AND source = '{source}'"


@router.get("/commitments-dashboard")
async def commitments_dashboard(
 start_period: str | None = Query(default=None),
 end_period: str | None = Query(default=None),
 source: str = Query(default="all"),
):
 """Compra de compromissos, consumo coberto e baseline OnDemand sintético."""
 today = date.today()
 start = _period(start_period or f"{today.year}01", "start_period")
 end = _period(end_period or f"{today.year}12", "end_period")
 if start > end:
  raise HTTPException(422, "start_period não pode ser posterior a end_period")
 source_filter = _commitment_source_filter(source)
 cube = f"iceberg.{layers.cube_layer()}.dest_dm_cost_commitments_dashboard"
 try:
  cube_rows = await trino_execute(
   f"SELECT period, source, commitment_type, project, scope, ondemand_baseline, "
   f"purchase, saving, forecast_purchase, forecast_return, forecast_saving "
   f"FROM {cube} WHERE period BETWEEN '{start}' AND '{end}' {source_filter}",
   timeout_s=60,
  )
 except Exception as exc:
  detail = str(exc).lower()
  if "not found" in detail or "does not exist" in detail or "table" in detail:
   raise HTTPException(
    503,
    "Cubo Gold do painel de compromissos ainda não foi materializado. "
    "Execute a materialização da regra Gold commitments_dashboard.",
   ) from exc
  raise
 actual_totals = [row for row in cube_rows if row.get("scope") == "ACTUAL_TOTAL"]
 actual_projects = [row for row in cube_rows if row.get("scope") == "ACTUAL_PROJECT"]
 forecast_totals = [row for row in cube_rows if row.get("scope") == "FORECAST_TOTAL"]
 forecast_project_rows = [row for row in cube_rows if row.get("scope") == "FORECAST_PROJECT"]

 def summarize(rows: list[dict[str, Any]]) -> dict[str, float]:
  result = {"ri_purchase": 0.0, "savingplan_purchase": 0.0,
            "ri_ondemand": 0.0, "savingplan_ondemand": 0.0,
            "ri_saving": 0.0, "savingplan_saving": 0.0}
  for row in rows:
   prefix = "ri" if row.get("commitment_type") == "RI" else "savingplan"
   result[f"{prefix}_purchase"] += float(row.get("purchase") or 0)
   result[f"{prefix}_ondemand"] += float(row.get("ondemand_baseline") or 0)
  result["ri_saving"] = result["ri_ondemand"] - result["ri_purchase"]
  result["savingplan_saving"] = result["savingplan_ondemand"] - result["savingplan_purchase"]
  result["ri_purchase_pct_of_ondemand"] = (
   result["ri_purchase"] / result["ri_ondemand"] * 100 if result["ri_ondemand"] else 0.0
  )
  result["savingplan_purchase_pct_of_ondemand"] = (
   result["savingplan_purchase"] / result["savingplan_ondemand"] * 100
   if result["savingplan_ondemand"] else 0.0
  )
  return result

 monthly: dict[str, dict[str, Any]] = {}
 for row in actual_totals:
  period = str(row["period"])
  bucket = monthly.setdefault(period, {"period": period, **{
   key: 0.0 for key in ("ri_purchase", "savingplan_purchase", "ri_ondemand",
    "savingplan_ondemand", "ri_saving", "savingplan_saving")
  }})
  prefix = "ri" if row.get("commitment_type") == "RI" else "savingplan"
  for metric in ("purchase", "ondemand_baseline"):
   key = {"purchase": "purchase", "ondemand_baseline": "ondemand"}[metric]
   bucket[f"{prefix}_{key}"] += float(row.get(metric) or 0)
  bucket[f"{prefix}_saving"] = bucket[f"{prefix}_ondemand"] - bucket[f"{prefix}_purchase"]

 project_totals: dict[tuple[str, str, str], dict[str, Any]] = {}
 project_monthly: dict[tuple[str, str, str, str], dict[str, Any]] = {}
 for row in actual_projects:
  period = str(row["period"])
  source_name = str(row.get("source"))
  commitment_type = str(row.get("commitment_type"))
  project = str(row.get("project") or "Sem projeto")
  project_base = float(row.get("ondemand_baseline") or 0)
  purchase_allocated = float(row.get("purchase") or 0)
  saving = float(row.get("saving") or 0)
  aggregate_key = (source_name, commitment_type, project)
  result = project_totals.setdefault(aggregate_key, {
   "id": f"{source_name}:{commitment_type}:{project}", "project": project,
   "source": source_name, "commitment_type": commitment_type,
   "ondemand_baseline": 0.0, "purchase_allocated": 0.0,
   "estimated_saving": 0.0,
  })
  result["ondemand_baseline"] += project_base
  result["purchase_allocated"] += purchase_allocated
  result["estimated_saving"] += saving
  monthly_project_key = (period, source_name, commitment_type, project)
  monthly_project = project_monthly.setdefault(monthly_project_key, {
   "period": period, "source": source_name, "commitment_type": commitment_type,
   "project": project, "ondemand_baseline": 0.0, "purchase_allocated": 0.0,
   "estimated_saving": 0.0,
  })
  monthly_project["ondemand_baseline"] += project_base
  monthly_project["purchase_allocated"] += purchase_allocated
  monthly_project["estimated_saving"] += saving

 projects = list(project_totals.values())
 for project in projects:
  baseline = float(project["ondemand_baseline"] or 0)
  project["purchase_pct_of_ondemand"] = (
   float(project["purchase_allocated"] or 0) / baseline * 100 if baseline else 0.0
  )
  project["saving_pct_of_ondemand"] = (
   float(project["estimated_saving"] or 0) / baseline * 100 if baseline else 0.0
  )

 forecast: dict[str, dict[str, Any]] = {}
 forecast_projects: dict[tuple[str, str, str, str], dict[str, float | str]] = {}
 for row in forecast_totals:
  period = str(row["period"])
  bucket = forecast.setdefault(period, {"period": period, "usage": 0.0,
   "purchase": 0.0, "return": 0.0, "other": 0.0, "total": 0.0,
   "ri_forecast_purchase": 0.0, "savingplan_forecast_purchase": 0.0,
   "ri_forecast_return": 0.0, "savingplan_forecast_return": 0.0,
   "ri_forecast_saving": 0.0, "savingplan_forecast_saving": 0.0})
  commitment_type = str(row.get("commitment_type") or "")
  prefix = "ri" if commitment_type == "RI" else "savingplan"
  purchase = float(row.get("forecast_purchase") or 0)
  returned = float(row.get("forecast_return") or 0)
  bucket["purchase"] += purchase
  bucket["return"] += returned
  bucket["total"] += purchase + returned
  bucket[f"{prefix}_forecast_purchase"] += purchase
  bucket[f"{prefix}_forecast_return"] += returned
 for row in forecast_project_rows:
  period = str(row["period"])
  commitment_type = str(row.get("commitment_type") or "")
  prefix = "ri" if commitment_type == "RI" else "savingplan"
  project_key = (period, str(row.get("source") or ""), prefix, str(row.get("project") or "Sem projeto"))
  project_bucket = forecast_projects.setdefault(project_key, {
   "period": period, "source": project_key[1],
   "commitment_type": commitment_type, "project": project_key[3],
   "forecast_purchase": 0.0, "forecast_return": 0.0, "forecast_saving": 0.0,
  })
  project_bucket["forecast_purchase"] += float(row.get("forecast_purchase") or 0)
  project_bucket["forecast_return"] += float(row.get("forecast_return") or 0)
  project_bucket["forecast_saving"] += float(row.get("forecast_saving") or 0)
 for bucket in forecast.values():
  for prefix in ("ri", "savingplan"):
   bucket[f"{prefix}_forecast_saving"] = (
    -float(bucket.get(f"{prefix}_forecast_return") or 0)
    - float(bucket.get(f"{prefix}_forecast_purchase") or 0)
   )

 return {
  "start_period": start, "end_period": end, "source": source,
  "currency": "BRL", "value_basis": "realizado_cloud",
  "period_summary": summarize(actual_totals),
  "monthly": sorted(monthly.values(), key=lambda x: x["period"]),
  "projects": sorted(projects, key=lambda x: abs(x["estimated_saving"]), reverse=True),
  "project_monthly": sorted(project_monthly.values(), key=lambda x: (str(x["period"]), -float(x["ondemand_baseline"]))),
  "forecast_monthly": sorted(forecast.values(), key=lambda x: x["period"]),
  "forecast_projects": sorted(forecast_projects.values(), key=lambda x: (str(x["period"]), -abs(float(x["forecast_saving"])))),
  "baseline_note": "Saving estimado = base OnDemand sintética menos compra do mesmo tipo no período. A compra é rateada mensalmente por origem, tipo e projeto, proporcionalmente à base OnDemand do projeto.",
 }

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
