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


def _commitment_source_filter(source: str, alias: str = "f") -> str:
 if source not in {"all", "azure-ti", "azure-4p"}:
  raise HTTPException(422, "source deve ser all, azure-ti ou azure-4p")
 return "" if source == "all" else f"AND lower({alias}.cloud_origem) = '{source}'"


def _commitment_actual_sql(start: str, end: str, source: str, *, by_project: bool = False) -> str:
 table = f"iceberg.{layers.detail_layer()}.dest_modelo_focus"
 source_filter = _commitment_source_filter(source, "f")
 select_dimensions = (
  "period, source, commitment_type, project, GROUPING(project) AS is_total"
  if by_project else "period, source, commitment_type"
 )
 grouping_clause = (
  "GROUP BY GROUPING SETS ((period, source, commitment_type), (period, source, commitment_type, project))"
  if by_project else f"GROUP BY {select_dimensions}"
 )
 eligible_filter = "AND (f.chargecategory = 'Purchase' OR (f.chargecategory = 'Usage' AND coalesce(f.is_synthetic, FALSE) = FALSE))"
 project_select = (
  "COALESCE(NULLIF(TRIM(f.organograma), ''), NULLIF(TRIM(f.source_resource_group_name), ''), 'Sem projeto') AS project,"
  if by_project else ""
 )
 return f"""
WITH synthetic_returns AS (
 SELECT source_connection_id, _billing_period, clone_source_record_id,
        clone_rule_id, SUM(realizado_cloud) AS return_realized
 FROM {table}
 WHERE _billing_period BETWEEN '{start}' AND '{end}'
   AND is_synthetic = TRUE AND clone_rule_id IN (1, 2)
 GROUP BY 1, 2, 3, 4
), eligible AS (
 SELECT f._billing_period AS period, lower(f.cloud_origem) AS source,
        CASE WHEN lower(f.commitmentdiscounttype) = 'reservation' THEN 'RI' ELSE 'SavingPlan' END AS commitment_type,
        {project_select}
        f.chargecategory, f.realizado_cloud,
        CASE WHEN f.chargecategory = 'Usage' AND r.clone_source_record_id IS NOT NULL THEN -r.return_realized ELSE 0 END AS ondemand_baseline,
        f.source_record_id
 FROM {table} f
 LEFT JOIN synthetic_returns r
   ON f.chargecategory = 'Usage'
  AND r.source_connection_id = f.source_connection_id
  AND r._billing_period = f._billing_period
  AND r.clone_source_record_id = f.source_record_id
  AND r.clone_rule_id = CASE WHEN lower(f.commitmentdiscounttype) = 'reservation' THEN 1 ELSE 2 END
 WHERE f._billing_period BETWEEN '{start}' AND '{end}'
   AND lower(f.commitmentdiscounttype) IN ('reservation', 'savingsplan')
   {eligible_filter}
   {source_filter}
)
SELECT {select_dimensions},
       SUM(CASE WHEN chargecategory = 'Purchase' THEN realizado_cloud ELSE 0 END) AS purchase,
       SUM(CASE WHEN chargecategory = 'Usage' THEN ondemand_baseline ELSE 0 END) AS ondemand_baseline,
       COUNT_IF(chargecategory = 'Usage') AS usage_rows
FROM eligible
{grouping_clause}
"""


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
 _commitment_source_filter(source)
 commitment_rows = await trino_execute(_commitment_actual_sql(start, end, source, by_project=True), timeout_s=120)
 is_total_row = lambda row: row.get("is_total") is True or row.get("is_total") == 1 or str(row.get("is_total")).lower() == "true"
 monthly_rows = [row for row in commitment_rows if is_total_row(row)]
 project_rows = [row for row in commitment_rows if not is_total_row(row)]

 planning = f"iceberg.{layers.detail_layer()}.dest_modelo_focus_planning"
 source_filter = "" if source == "all" else f"AND lower(cloud_origem) = '{source}'"
 forecast_rows = await trino_execute(f"""
 SELECT period, lower(cloud_origem) AS source, lower(commitmentdiscounttype) AS commitment_type,
        chargecategory, pricingcategory,
        SUM(realizado_cloud) AS forecast_value
 FROM {planning}
 WHERE tipo = 'forecast' AND financial_effect = 'DIRECT_REALIZED'
   AND period BETWEEN '{start}' AND '{end}'
   {source_filter}
 GROUP BY 1, 2, 3, 4, 5
 """, timeout_s=120)

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
 for row in monthly_rows:
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
 monthly_commitments = {
  (str(row["period"]), str(row.get("source")), str(row.get("commitment_type"))): row
  for row in monthly_rows
 }
 project_baselines: dict[tuple[str, str, str, str], float] = {}
 for row in project_rows:
  key = (str(row["period"]), str(row.get("source")), str(row.get("commitment_type")))
  project = str(row.get("project") or "Sem projeto")
  project_baselines[(key[0], key[1], key[2], project)] = float(row.get("ondemand_baseline") or 0)

 group_baselines: dict[tuple[str, str, str], float] = {}
 for (period, source_name, commitment_type, _), baseline in project_baselines.items():
  key = (period, source_name, commitment_type)
  group_baselines[key] = group_baselines.get(key, 0.0) + baseline

 for row in project_rows:
  period = str(row["period"])
  source_name = str(row.get("source"))
  commitment_type = str(row.get("commitment_type"))
  project = str(row.get("project") or "Sem projeto")
  key = (period, source_name, commitment_type)
  project_base = float(row.get("ondemand_baseline") or 0)
  usage_rows = int(row.get("usage_rows") or 0)
  if project_base == 0 and usage_rows == 0:
   continue
  total_base = group_baselines.get(key, 0.0)
  commitment = monthly_commitments.get(key, {})
  purchase = float(commitment.get("purchase") or 0)
  purchase_allocated = purchase * project_base / total_base if total_base else 0.0
  aggregate_key = (source_name, commitment_type, project)
  result = project_totals.setdefault(aggregate_key, {
   "id": f"{source_name}:{commitment_type}:{project}", "project": project,
   "source": source_name, "commitment_type": commitment_type,
   "ondemand_baseline": 0.0, "purchase_allocated": 0.0,
   "estimated_saving": 0.0, "usage_rows": 0,
  })
  result["ondemand_baseline"] += project_base
  result["purchase_allocated"] += purchase_allocated
  result["estimated_saving"] += project_base - purchase_allocated
  result["usage_rows"] += usage_rows

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
 forecast_breakdown = []
 for row in forecast_rows:
  period = str(row["period"])
  bucket = forecast.setdefault(period, {"period": period, "usage": 0.0,
   "purchase": 0.0, "return": 0.0, "other": 0.0, "total": 0.0,
   "ri_forecast_purchase": 0.0, "savingplan_forecast_purchase": 0.0,
   "ri_forecast_return": 0.0, "savingplan_forecast_return": 0.0,
   "ri_forecast_saving": 0.0, "savingplan_forecast_saving": 0.0})
  category = str(row.get("chargecategory") or "").lower()
  key = category if category in {"usage", "purchase", "return"} else "other"
  amount = float(row.get("forecast_value") or 0)
  bucket[key] += amount
  bucket["total"] += amount
  commitment_type = str(row.get("commitment_type") or "").lower()
  prefix = "ri" if commitment_type == "reservation" else "savingplan" if commitment_type == "savingsplan" else None
  if prefix and category in {"purchase", "return"}:
   bucket[f"{prefix}_forecast_{category}"] += amount
  forecast_breakdown.append({
   "period": period, "source": row.get("source"), "chargecategory": row.get("chargecategory"),
   "commitment_type": row.get("commitment_type"), "pricingcategory": row.get("pricingcategory"),
   "forecast_value": amount,
  })
 for bucket in forecast.values():
  bucket["ri_forecast_saving"] = -bucket["ri_forecast_return"] - bucket["ri_forecast_purchase"]
  bucket["savingplan_forecast_saving"] = -bucket["savingplan_forecast_return"] - bucket["savingplan_forecast_purchase"]

 return {
  "start_period": start, "end_period": end, "source": source,
  "currency": "BRL", "value_basis": "realizado_cloud",
  "period_summary": summarize(monthly_rows),
  "monthly": sorted(monthly.values(), key=lambda x: x["period"]),
  "projects": sorted(projects, key=lambda x: abs(x["estimated_saving"]), reverse=True),
  "forecast_monthly": sorted(forecast.values(), key=lambda x: x["period"]),
  "forecast_breakdown": forecast_breakdown,
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
