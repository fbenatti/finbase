from __future__ import annotations
import os
from datetime import date, datetime
from typing import Any, Literal
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
import io
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.destination import DestinationTable
from app.models.forecast import ForecastConfig, ForecastAdjustment, ForecastBusinessRule, ForecastRun
from app.models.classification import ClassificationVersion
from app.models.classification import ClassificationArtifactBinding
from app.models.etl_task import EtlTask
from app.services.forecast_service import fetch_metadata, validate_config
from app.services.trino_service import execute as trino_execute

router = APIRouter(prefix="/forecast", tags=["forecast"])

class ConfigIn(BaseModel):
    name: str = "Forecast padrão FinOps"
    description: str | None = None
    destination_table_id: int
    measure_column: str
    currency_column: str | None = None
    date_column: str
    period_column: str | None = None
    type_column: str | None = "tipo"
    dimensions: list[str] = []
    cutoff_days: int = Field(2, ge=0, le=31)
    horizon_months: int = Field(12, ge=1, le=36)
    growth_rate: float = 0.0083
    model_mode: str = "AUTO_BACKTEST"
    enabled_windows: list[int] = [7, 14, 28]
    recent_weight: float = Field(0.7, ge=0, le=1)
    minimum_daily_points: int = 7
    minimum_monthly_points: int = 3
    null_dimension_label: str = "Não classificado"
    active: bool = True

class AdjustmentIn(BaseModel):
    name: str
    filters: dict[str, Any] = {}
    adjustment_type: str
    percentage: float | None = None
    fixed_value: float | None = None
    start_period: str | None = None
    end_period: str | None = None
    priority: int = 0
    active: bool = True

class BusinessRuleIn(BaseModel):
    name: str
    identification_column: str
    identification_values: list[str] = []
    charge_type: str = "Usage"
    behavior: str = "RECURRING_MONTHLY"
    reference_column: str | None = None
    reference_values: list[str] = []
    value_column: str | None = None
    start_period: str
    end_period: str | None = None
    growth_rate: float = 0.0
    replacement_value: float | None = None
    cancel_on_refund: bool = False
    scope: dict[str, Any] = {}
    priority: int = 0
    active: bool = True

class HistoryPivotIn(BaseModel):
    rows: list[str] = []
    columns: list[str] = ["forecast_month"]
    # Values may be a list (IN) or {operator, value(s)} for the server-side
    # column filters used by the historical Pivot.
    filters: dict[str, Any] = {}
    comparison: bool = False
    row_limit: int = Field(100, ge=1, le=100)
    offset: int = Field(0, ge=0)
    search: str = Field("", max_length=200)
    sort_field: str | None = None
    sort_direction: str = Field("asc", pattern="^(asc|desc)$")

def _qi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'

def _ql(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"

async def _history_context(run_id: int, db: AsyncSession) -> tuple[ForecastRun, dict[str, Any], dict[str, str]]:
    run = await db.get(ForecastRun, run_id)
    if not run:
        raise HTTPException(404, "Execução não encontrada")
    if run.status != "COMPLETED" or not run.result_table:
        raise HTTPException(422, "Esta execução não possui snapshot de forecast publicado")
    snapshot = dict(run.config_snapshot or {})
    if not snapshot:
        # Legacy runs retain their physical result, but cannot safely be
        # compared once the editable configuration has disappeared.
        config = await db.get(ForecastConfig, run.forecast_config_id) if run.forecast_config_id else None
        dest = await db.get(DestinationTable, config.destination_table_id) if config else None
        if config and dest:
            snapshot = _config_snapshot(config, dest)
    try:
        described = await trino_execute(f"DESCRIBE iceberg.forecast.{run.result_table}")
    except Exception as exc:
        raise HTTPException(503, f"Snapshot histórico indisponível no Trino: {exc}")
    schema = {str(r.get("Column") or r.get("column_name") or r.get("column")): str(r.get("Type") or r.get("data_type") or "") for r in described}
    if "forecast_value" not in schema or "forecast_month" not in schema:
        raise HTTPException(422, "Snapshot histórico possui schema incompatível")
    return run, snapshot, schema

def _history_fields(snapshot: dict[str, Any], schema: dict[str, str]) -> set[str]:
    return {"forecast_month", "tipo", "forecast_value", *(snapshot.get("dimensions") or [])}.intersection(schema)

def _validate_history_request(body: HistoryPivotIn, snapshot: dict[str, Any], schema: dict[str, str]) -> tuple[list[str], list[str]]:
    allowed = _history_fields(snapshot, schema)
    requested = [*body.rows, *body.columns, *body.filters]
    invalid = sorted({field for field in requested if field not in allowed})
    if invalid:
        raise HTTPException(422, f"Campos inválidos para este snapshot: {', '.join(invalid)}")
    if set(body.rows).intersection(body.columns):
        raise HTTPException(422, "Uma dimensão não pode estar em linhas e colunas ao mesmo tempo")
    if body.sort_field and body.sort_field not in allowed and body.sort_field not in {"value", "forecast_value", "actual_value", "variance", "variance_pct"}:
        raise HTTPException(422, f"Campo de ordenação inválido: {body.sort_field}")
    return body.rows, body.columns

def _history_where(filters: dict[str, Any], search: str = "", search_fields: list[str] | None = None) -> str:
    clauses = []
    for field, raw in filters.items():
        if isinstance(raw, dict):
            operator = str(raw.get("operator", "equals")).lower()
            values = raw.get("value", raw.get("values", []))
            values = values if isinstance(values, list) else [values]
        else:
            operator, values = "in", raw if isinstance(raw, list) else [raw]
        values = [v for v in values if v is not None and str(v) != ""]
        if not values:
            continue
        expression = _qi(field)
        # MUI DataGrid sends operators such as `is`, `not`,
        # `doesNotContain` and `isAnyOf`; accept their normalized forms too.
        if operator in {"equals", "=", "is", "equal"}:
            clauses.append(f"{expression} = {_ql(values[0])}")
        elif operator in {"notequals", "!=", "not_equals", "not", "isnot", "is_not"}:
            clauses.append(f"({expression} IS NULL OR {expression} <> {_ql(values[0])})")
        elif operator in {"contains", "like"}:
            clauses.append(f"lower(CAST({expression} AS VARCHAR)) LIKE {_ql('%' + str(values[0]).lower() + '%')}")
        elif operator in {"notcontains", "not_contains", "doesnotcontain", "does_not_contain"}:
            clauses.append(f"(NOT lower(CAST({expression} AS VARCHAR)) LIKE {_ql('%' + str(values[0]).lower() + '%')} OR {expression} IS NULL)")
        elif operator in {"in", "isanyof", "is_any_of"}:
            clauses.append(f"{expression} IN ({', '.join(_ql(v) for v in values)})")
        elif operator in {"notin", "not_in"}:
            clauses.append(f"({expression} IS NULL OR {expression} NOT IN ({', '.join(_ql(v) for v in values)}))")
        else:
            raise HTTPException(422, f"Operador de filtro inválido: {operator}")
    if search.strip() and search_fields:
        term = _ql("%" + search.strip().lower() + "%")
        clauses.append("(" + " OR ".join(f"lower(CAST({_qi(field)} AS VARCHAR)) LIKE {term}" for field in search_fields) + ")")
    return " WHERE " + " AND ".join(clauses) if clauses else ""

def _out(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}

def _config_snapshot(config: ForecastConfig, destination: DestinationTable) -> dict[str, Any]:
    """Persist only JSON-safe execution inputs; never depend on a live config."""
    return {
        "config_id": config.id, "config_version": config.version,
        "destination_table_id": destination.id,
        "destination_physical_name": destination.physical_name,
        "measure_column": config.measure_column, "date_column": config.date_column,
        "period_column": config.period_column, "type_column": config.type_column,
        "dimensions": list(config.dimensions or []), "currency_column": config.currency_column,
        "cutoff_days": config.cutoff_days, "horizon_months": config.horizon_months,
    }

async def ensure_default_config(db: AsyncSession) -> None:
    """Create the usable default profile once, from the real Gold schema."""
    exists = (await db.execute(select(ForecastConfig.id).limit(1))).scalar_one_or_none()
    if exists is not None:
        return
    destination = (await db.execute(select(DestinationTable).where(DestinationTable.active == True).order_by(DestinationTable.id).limit(1))).scalar_one_or_none()
    if not destination:
        return
    try:
        metadata = _apply_destination_suggestions(destination, await fetch_metadata(destination.physical_name))
    except Exception:
        return
    if not metadata.get("default_measure") or not metadata.get("default_date"):
        return
    row = ForecastConfig(
        name="Forecast padrão FinOps",
        description="Perfil criado automaticamente a partir do schema Gold",
        destination_table_id=destination.id,
        measure_column=metadata["default_measure"],
        date_column=metadata["default_date"],
        period_column=metadata.get("default_period"),
        type_column="tipo",
        dimensions=metadata.get("recommended_dimensions", []),
    )
    db.add(row)
    await db.commit()

async def ensure_focus_forecast_rules(db: AsyncSession) -> None:
    """Install editable Forecast templates once for existing/new FOCUS configs."""
    configs=(await db.execute(select(ForecastConfig))).scalars().all()
    for config in configs:
        dest=await db.get(DestinationTable, config.destination_table_id)
        if not dest or "focus" not in f"{dest.name} {dest.physical_name}".lower(): continue
        templates=[
            ("Usage variável", "chargecategory", ["Usage"], "Usage", "RECURRING_MONTHLY"),
            ("Return simulado", "chargecategory", ["Return"], "Return", "RECURRING_MONTHLY"),
            ("Purchase recorrente Savings Plan", "chargecategory", ["Purchase"], "Purchase", "RECURRING_MONTHLY"),
            ("Refund cancela compromisso", "chargecategory", ["Refund"], "Refund", "CANCEL_ON_REFUND"),
        ]
        for name, field, values, charge, behavior in templates:
            exists=(await db.execute(select(ForecastBusinessRule.id).where(ForecastBusinessRule.forecast_config_id==config.id, ForecastBusinessRule.name==name))).scalar_one_or_none()
            if not exists:
                db.add(ForecastBusinessRule(forecast_config_id=config.id,name=name,identification_column=field,identification_values=values,charge_type=charge,behavior=behavior,reference_column="commitmentdiscountid",value_column=config.measure_column,start_period="202001",scope={},priority=10))
    await db.commit()

async def _dest_metadata(db, table_id):
    dest = await db.get(DestinationTable, table_id)
    if not dest or not dest.active: raise HTTPException(404, "Tabela Gold não encontrada ou inativa")
    return dest, await fetch_metadata(dest.physical_name)

def _apply_destination_suggestions(dest: DestinationTable, metadata: dict[str, Any]) -> dict[str, Any]:
    """Only the FOCUS destination receives a FOCUS-specific suggestion profile."""
    identity = f"{dest.name} {dest.physical_name}".lower()
    if "focus" not in identity:
        metadata["recommended_dimensions"] = []
        metadata["suggestion_profile"] = None
        return metadata
    available = {c["name"].lower(): c["name"] for c in metadata.get("columns", [])}
    focus_order = ("cloud_origem", "departamento", "servicename", "pricingcategory", "subaccountname", "resourcename")
    metadata["recommended_dimensions"] = [available[name] for name in focus_order if name in available]
    metadata["suggestion_profile"] = "FOCUS"
    return metadata

@router.get("/metadata/{destination_table_id}")
async def metadata(destination_table_id: int, db: AsyncSession = Depends(get_db)):
    dest, result = await _dest_metadata(db, destination_table_id)
    result = _apply_destination_suggestions(dest, result)
    result.update(destination_table_id=destination_table_id, destination_table=dest.physical_name, default_type="realizado")
    return result

@router.get("/configs")
async def list_configs(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ForecastConfig).order_by(ForecastConfig.updated_at.desc()))).scalars().all()
    return [_out(r) for r in rows]

@router.post("/configs", status_code=201)
async def create_config(body: ConfigIn, db: AsyncSession = Depends(get_db)):
    row = ForecastConfig(**body.model_dump()); db.add(row); await db.commit(); await db.refresh(row)
    # A FOCUS configuration is usable out of the box: the editable business
    # rule templates are created with it instead of waiting for an API restart.
    await ensure_focus_forecast_rules(db)
    return _out(row)

@router.put("/configs/{config_id}")
async def update_config(config_id: int, body: ConfigIn, db: AsyncSession = Depends(get_db)):
    row = await db.get(ForecastConfig, config_id)
    if not row: raise HTTPException(404, "Configuração não encontrada")
    for key, value in body.model_dump().items(): setattr(row, key, value)
    row.version += 1; await db.commit(); await db.refresh(row)
    return _out(row)

@router.delete("/configs/{config_id}", status_code=204)
async def delete_config(config_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(ForecastConfig, config_id)
    if not row: raise HTTPException(404, "Configuração não encontrada")
    active_run = (await db.execute(select(ForecastRun.id).where(ForecastRun.forecast_config_id == config_id, ForecastRun.status.in_(["QUEUED", "RUNNING"])).limit(1))).scalar_one_or_none()
    if active_run: raise HTTPException(409, "Não é possível apagar uma configuração com execução ativa")
    # Keep runs queryable even on older databases whose FK migration has not
    # yet been applied by unlinking them explicitly before deleting the config.
    runs = (await db.execute(select(ForecastRun).where(ForecastRun.forecast_config_id == config_id))).scalars().all()
    for run in runs:
        run.forecast_config_id = None
    await db.delete(row); await db.commit()

@router.post("/configs/{config_id}/duplicate", status_code=201)
async def duplicate_config(config_id: int, db: AsyncSession = Depends(get_db)):
    src = await db.get(ForecastConfig, config_id)
    if not src: raise HTTPException(404, "Configuração não encontrada")
    values = {c.name: getattr(src, c.name) for c in src.__table__.columns if c.name not in {"id", "created_at", "updated_at"}}
    values.update(name=f"{src.name} (cópia)", version=1)
    row = ForecastConfig(**values); db.add(row); await db.commit(); await db.refresh(row)
    return _out(row)

@router.post("/configs/{config_id}/validate")
async def validate(config_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(ForecastConfig, config_id)
    if not row: raise HTTPException(404, "Configuração não encontrada")
    _, meta = await _dest_metadata(db, row.destination_table_id)
    errors = validate_config(row, meta)
    fields = {c["name"] for c in meta["columns"]}
    rules = (await db.execute(select(ForecastBusinessRule).where(ForecastBusinessRule.forecast_config_id == config_id, ForecastBusinessRule.active == True))).scalars().all()
    for rule in rules:
        if rule.identification_column not in fields: errors.append(f"Regra '{rule.name}': coluna de identificação inválida: {rule.identification_column}")
        if rule.reference_column and rule.reference_column not in fields: errors.append(f"Regra '{rule.name}': coluna de referência inválida: {rule.reference_column}")
        if rule.value_column and rule.value_column not in fields: errors.append(f"Regra '{rule.name}': coluna de valor inválida: {rule.value_column}")
        if not rule.start_period or len(rule.start_period) != 6: errors.append(f"Regra '{rule.name}': período inicial deve ser YYYYMM")
    return {"valid": not errors, "errors": errors, "metadata": meta}

@router.get("/configs/{config_id}/business-rules")
async def list_business_rules(config_id: int, db: AsyncSession = Depends(get_db)):
    if not await db.get(ForecastConfig, config_id): raise HTTPException(404, "Configuração não encontrada")
    rows = (await db.execute(select(ForecastBusinessRule).where(ForecastBusinessRule.forecast_config_id == config_id).order_by(ForecastBusinessRule.priority, ForecastBusinessRule.id))).scalars().all()
    return [_out(r) for r in rows]

async def _validate_rule_fields(config_id: int, body: BusinessRuleIn, db: AsyncSession):
    config = await db.get(ForecastConfig, config_id)
    if not config: raise HTTPException(404, "Configuração não encontrada")
    dest, meta = await _dest_metadata(db, config.destination_table_id)
    fields = {c["name"] for c in meta["columns"]}
    for field, label in ((body.identification_column, "identificação"), (body.reference_column, "referência"), (body.value_column, "valor")):
        if field and field not in fields: raise HTTPException(422, f"Coluna de {label} não existe na Gold: {field}")
    allowed = {"REPEAT_NEXT_MONTH", "RECURRING_MONTHLY", "RECURRING_UNTIL_END", "CANCEL_ON_REFUND", "REPLACE_VALUE"}
    if body.behavior not in allowed: raise HTTPException(422, "Comportamento de forecast inválido")
    if len(body.start_period) != 6 or not body.start_period.isdigit(): raise HTTPException(422, "start_period deve estar em YYYYMM")
    if body.end_period and (len(body.end_period) != 6 or not body.end_period.isdigit()): raise HTTPException(422, "end_period deve estar em YYYYMM")
    return config

@router.post("/configs/{config_id}/business-rules", status_code=201)
async def create_business_rule(config_id: int, body: BusinessRuleIn, db: AsyncSession = Depends(get_db)):
    await _validate_rule_fields(config_id, body, db)
    row = ForecastBusinessRule(forecast_config_id=config_id, **body.model_dump())
    db.add(row); await db.commit(); await db.refresh(row)
    return _out(row)

@router.put("/business-rules/{rule_id}")
async def update_business_rule(rule_id: int, body: BusinessRuleIn, db: AsyncSession = Depends(get_db)):
    row = await db.get(ForecastBusinessRule, rule_id)
    if not row: raise HTTPException(404, "Regra de forecast não encontrada")
    await _validate_rule_fields(row.forecast_config_id, body, db)
    for key, value in body.model_dump().items(): setattr(row, key, value)
    row.version += 1; await db.commit(); await db.refresh(row)
    return _out(row)

@router.delete("/business-rules/{rule_id}", status_code=204)
async def delete_business_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(ForecastBusinessRule, rule_id)
    if not row: raise HTTPException(404, "Regra de forecast não encontrada")
    await db.delete(row); await db.commit()

@router.get("/configs/{config_id}/adjustments")
async def list_adjustments(config_id: int, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ForecastAdjustment).where(ForecastAdjustment.forecast_config_id == config_id).order_by(ForecastAdjustment.priority))).scalars().all()
    return [_out(r) for r in rows]

@router.post("/configs/{config_id}/adjustments", status_code=201)
async def create_adjustment(config_id: int, body: AdjustmentIn, db: AsyncSession = Depends(get_db)):
    allowed = {"PERCENTAGE", "FIXED_VALUE", "ADD_VALUE", "SUBTRACT_VALUE", "ZERO", "CUSTOM_GROWTH"}
    if body.adjustment_type not in allowed: raise HTTPException(422, "Tipo de ajuste inválido")
    if not await db.get(ForecastConfig, config_id): raise HTTPException(404, "Configuração não encontrada")
    row = ForecastAdjustment(forecast_config_id=config_id, **body.model_dump()); db.add(row); await db.commit(); await db.refresh(row)
    return _out(row)

@router.put("/adjustments/{adjustment_id}")
async def update_adjustment(adjustment_id: int, body: AdjustmentIn, db: AsyncSession = Depends(get_db)):
    row = await db.get(ForecastAdjustment, adjustment_id)
    if not row: raise HTTPException(404, "Ajuste não encontrado")
    if body.adjustment_type not in {"PERCENTAGE", "FIXED_VALUE", "ADD_VALUE", "SUBTRACT_VALUE", "ZERO", "CUSTOM_GROWTH"}:
        raise HTTPException(422, "Tipo de ajuste inválido")
    for key, value in body.model_dump().items(): setattr(row, key, value)
    await db.commit(); await db.refresh(row)
    return _out(row)

@router.delete("/adjustments/{adjustment_id}", status_code=204)
async def delete_adjustment(adjustment_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(ForecastAdjustment, adjustment_id)
    if not row: raise HTTPException(404, "Ajuste não encontrado")
    await db.delete(row); await db.commit()

@router.post("/runs", status_code=201)
async def create_run(config_id: int, trigger_type: str = "MANUAL", db: AsyncSession = Depends(get_db)):
    config = await db.get(ForecastConfig, config_id)
    if not config: raise HTTPException(404, "Configuração não encontrada")
    active = (await db.execute(select(ForecastRun.id).where(ForecastRun.forecast_config_id == config_id, ForecastRun.status.in_(["QUEUED", "RUNNING"])))).scalar_one_or_none()
    if active: raise HTTPException(409, f"Já existe forecast ativo: {active}")
    heartbeat = os.getenv("OPENFINOPS_WORKER_HEARTBEAT", "/tmp/openfinops-worker.heartbeat")
    pid_file = os.getenv("OPENFINOPS_WORKER_PID", "/tmp/openfinops-worker.pid")
    try:
        worker_pid = int(open(pid_file).read().strip()); os.kill(worker_pid, 0)
        age = datetime.now().timestamp() - os.stat(heartbeat).st_mtime
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError): age = 999
    if age > 15: raise HTTPException(503, "Worker ETL indisponível; forecast não foi criado")
    destination = await db.get(DestinationTable, config.destination_table_id)
    if not destination or not destination.active:
        raise HTTPException(404, "Tabela Gold da configuração não encontrada")
    active_classification = (await db.execute(select(ClassificationVersion).where(
        ClassificationVersion.destination_table_id == destination.id,
        ClassificationVersion.status == "ACTIVE"
    ).order_by(ClassificationVersion.version.desc()))).scalars().first()
    snapshot = _config_snapshot(config, destination)
    source_name, source_namespace = destination.physical_name, "gold"
    if active_classification:
        source_name, source_namespace = active_classification.physical_table, "classified"
        snapshot.update(classification_version_id=active_classification.id,
                        destination_physical_name=source_name, source_namespace=source_namespace)
    row = ForecastRun(
        forecast_config_id=config_id, config_version=config.version, trigger_type=trigger_type,
        cutoff_date=date.today().isoformat(), config_snapshot=snapshot,
        destination_table_id_snapshot=destination.id, destination_physical_name=source_name,
        classification_version_id=active_classification.id if active_classification else None,
    )
    db.add(row); await db.flush()
    db.add(ClassificationArtifactBinding(artifact_type="forecast_run", artifact_id=str(row.id),
        classification_version_id=row.classification_version_id,
        binding_mode="EXPLICIT" if row.classification_version_id else "UNKNOWN"))
    task = EtlTask(task_type="forecast", status="QUEUED", destination_table_id=config_id, result_json={"forecast_run_id": row.id}, errors=[])
    db.add(task); await db.commit(); await db.refresh(row)
    return {"run_id": row.id, "task_id": task.id, "status": row.status}

@router.get("/runs")
async def list_runs(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ForecastRun).order_by(ForecastRun.started_at.desc()).limit(100))).scalars().all()
    return [_out(r) for r in rows]

@router.get("/runs/{run_id}/history/metadata")
async def history_metadata(run_id: int, db: AsyncSession = Depends(get_db)):
    run, snapshot, schema = await _history_context(run_id, db)
    dimensions = [field for field in snapshot.get("dimensions", []) if field in schema]
    return {
        "run_id": run.id, "result_table": run.result_table, "cutoff_date": run.cutoff_date,
        "snapshot": snapshot, "columns": [{"name": name, "type": typ} for name, typ in schema.items()],
        "dimensions": dimensions, "default_rows": dimensions,
        "default_columns": ["forecast_month"], "types": ["realizado", "forecast"],
    }

@router.post("/runs/{run_id}/history/pivot")
async def history_pivot(run_id: int, body: HistoryPivotIn, db: AsyncSession = Depends(get_db)):
    run, snapshot, schema = await _history_context(run_id, db)
    rows, columns = _validate_history_request(body, snapshot, schema)
    dims = [*rows, *columns]
    selected = [f"{_qi(name)} AS d{i}" for i, name in enumerate(dims)]
    grouped = f" GROUP BY {', '.join(_qi(name) for name in dims)}" if dims else ""
    default_order = f" ORDER BY {', '.join(_qi(name) for name in dims)}" if dims else ""
    sort_name = "value" if body.sort_field in {"value", "forecast_value"} else (body.sort_field if body.sort_field else None)
    ordered = f" ORDER BY {_qi(sort_name)} {body.sort_direction.upper()}" if sort_name else default_order
    value_sql = 'SUM(COALESCE(TRY_CAST("forecast_value" AS DOUBLE), 0)) AS value'
    where = _history_where(body.filters, body.search, list(_history_fields(snapshot, schema) - {"forecast_value"}))
    sql = f"SELECT {', '.join([*selected, value_sql])} FROM iceberg.forecast.{run.result_table}{where}{grouped}{ordered} OFFSET {body.offset} LIMIT {body.row_limit}"
    try:
        records = await trino_execute(sql, timeout_s=90)
    except Exception as exc:
        raise HTTPException(503, f"Falha ao consultar snapshot de forecast: {exc}")
    return {"run_id": run_id, "comparison": False, "rows": rows, "columns": columns, "records": records, "offset": body.offset, "limit": body.row_limit}

@router.post("/runs/{run_id}/history/comparison")
async def history_comparison(run_id: int, body: HistoryPivotIn, db: AsyncSession = Depends(get_db)):
    run, snapshot, schema = await _history_context(run_id, db)
    rows, columns = _validate_history_request(body, snapshot, schema)
    if not snapshot.get("destination_physical_name") or not snapshot.get("measure_column") or not snapshot.get("date_column"):
        raise HTTPException(422, "Execução legada sem contrato congelado; comparação não disponível")
    series_dims = [field for field in snapshot.get("dimensions", []) if field in schema]
    measure, date_col = snapshot["measure_column"], snapshot["date_column"]
    period_col = snapshot.get("period_column")
    actual_source = f"iceberg.{snapshot.get('source_namespace', 'gold')}.{snapshot['destination_physical_name']}"
    # Gold dates are heterogeneous across providers.  This covers ISO and the
    # Azure MM/DD/YYYY format used by the existing forecast engine.
    raw_date = f"CAST({_qi(date_col)} AS VARCHAR)"
    date_expr = f"COALESCE(TRY_CAST({_qi(date_col)} AS DATE), TRY(date_parse({raw_date}, '%m/%d/%Y')), TRY(date_parse({raw_date}, '%d/%m/%Y')))"
    actual_dims = ", ".join(_qi(field) for field in series_dims)
    actual_group = (actual_dims + ", ") if actual_dims else ""
    type_col = snapshot.get("type_column")
    type_filter = f" WHERE ({_qi(type_col)} = 'realizado' OR {_qi(type_col)} IS NULL)" if type_col else ""
    # Union forecast with realized aggregates, then aggregate again at the
    # requested Pivot axes.  This prevents join/null mismatch errors.
    f_dims = ", ".join(_qi(field) for field in series_dims)
    a_dims = f_dims
    combined_dims = ", ".join(_qi(field) for field in series_dims)
    selected = [f"{_qi(name)} AS d{i}" for i, name in enumerate([*rows, *columns])]
    axes = [*rows, *columns]
    group = f" GROUP BY {', '.join(_qi(name) for name in axes)}" if axes else ""
    order = f" ORDER BY {', '.join(_qi(name) for name in axes)}" if axes else ""
    where = _history_where(body.filters, body.search, [*series_dims, "forecast_month"])
    f_select_dims = (f_dims + ", ") if f_dims else ""
    a_select_dims = (a_dims + ", ") if a_dims else ""
    # Both CTEs expose exactly the frozen dimensions plus forecast_month.
    sql = f"""
      WITH combined AS (
        SELECT {f_select_dims}\"forecast_month\", SUM(TRY_CAST(\"forecast_value\" AS DOUBLE)) AS forecast_value, CAST(0 AS DOUBLE) AS actual_value
        FROM iceberg.forecast.{run.result_table} WHERE \"tipo\" = 'forecast'
        GROUP BY {f_select_dims}\"forecast_month\"
        UNION ALL
        SELECT {a_select_dims}date_format({date_expr}, '%Y%m') AS \"forecast_month\", CAST(0 AS DOUBLE), SUM(COALESCE(TRY_CAST({_qi(measure)} AS DOUBLE), 0))
        FROM {actual_source}{type_filter}
        GROUP BY {actual_group}date_format({date_expr}, '%Y%m')
      )
      SELECT {', '.join(selected)},
        SUM(forecast_value) AS forecast_value, SUM(actual_value) AS actual_value,
        SUM(actual_value) - SUM(forecast_value) AS variance,
        CASE WHEN SUM(forecast_value) = 0 THEN NULL ELSE (SUM(actual_value) - SUM(forecast_value)) / SUM(forecast_value) END AS variance_pct,
        CASE WHEN MAX(\"forecast_month\") < date_format(current_date, '%Y%m') THEN TRUE ELSE FALSE END AS closed_period
      FROM combined{where}{group}{(f' ORDER BY {_qi(body.sort_field)} {body.sort_direction.upper()}' if body.sort_field in axes else (f' ORDER BY {_qi(body.sort_field)} {body.sort_direction.upper()}' if body.sort_field in {"forecast_value", "actual_value", "variance", "variance_pct"} else order))} OFFSET {body.offset} LIMIT {body.row_limit}
    """
    try:
        records = await trino_execute(sql, timeout_s=120)
        wape_sql = f"""
          WITH f AS (SELECT {f_select_dims}\"forecast_month\", SUM(TRY_CAST(\"forecast_value\" AS DOUBLE)) forecast_value FROM iceberg.forecast.{run.result_table} WHERE \"tipo\"='forecast' GROUP BY {f_select_dims}\"forecast_month\"),
          a AS (SELECT {a_select_dims}date_format({date_expr}, '%Y%m') AS \"forecast_month\", SUM(COALESCE(TRY_CAST({_qi(measure)} AS DOUBLE),0)) actual_value FROM {actual_source}{type_filter} GROUP BY {actual_group}date_format({date_expr}, '%Y%m'))
          SELECT CASE WHEN SUM(abs(actual_value))=0 THEN NULL ELSE SUM(abs(actual_value-forecast_value))/SUM(abs(actual_value)) END AS wape
          FROM (SELECT * FROM f FULL OUTER JOIN a USING ({combined_dims}{',' if combined_dims else ''}\"forecast_month\"))
          WHERE \"forecast_month\" < date_format(current_date, '%Y%m')
        """
        wape_result = await trino_execute(wape_sql, timeout_s=120)
    except Exception as exc:
        raise HTTPException(503, f"Falha ao comparar forecast com Gold atual: {exc}")
    return {"run_id": run_id, "comparison": True, "rows": rows, "columns": columns, "records": records, "offset": body.offset, "limit": body.row_limit, "wape": (wape_result[0].get("wape") if wape_result else None), "compared_at": datetime.utcnow().isoformat() + "Z"}

@router.get("/runs/{run_id}")
async def get_run(run_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(ForecastRun, run_id)
    if not row: raise HTTPException(404, "Execução não encontrada")
    return _out(row)

@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(ForecastRun, run_id)
    if not row: raise HTTPException(404, "Execução não encontrada")
    if row.status in {"QUEUED", "RUNNING"}: row.status = "CANCELLED"; row.phase = "Cancelado pelo usuário"; await db.commit()
    return {"run_id": run_id, "status": row.status}

@router.get("/runs/{run_id}/results")
async def run_results(run_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(ForecastRun, run_id)
    if not row: raise HTTPException(404, "Execução não encontrada")
    return {"run_id": run_id, "status": row.status, "table": row.result_table, "rows_written": row.rows_written, "selected_models": row.selected_models, "metrics": row.error_metrics}

@router.get("/runs/{run_id}/backtest")
async def run_backtest(run_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(ForecastRun, run_id)
    if not row: raise HTTPException(404, "Execução não encontrada")
    return {"run_id": run_id, "models": row.selected_models or {}, "metrics": row.error_metrics or {}}

@router.get("/runs/{run_id}/export")
async def export_run(run_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(ForecastRun, run_id)
    if not row or not row.result_table: raise HTTPException(404, "Resultado de forecast não encontrado")
    from app.services.iceberg_service import get_catalog
    try:
        table = get_catalog().load_table(("forecast", row.result_table))
        csv_bytes = table.scan().to_pandas().to_csv(index=False).encode("utf-8")
    except Exception as exc:
        raise HTTPException(503, f"Falha ao ler resultado: {exc}")
    return StreamingResponse(io.BytesIO(csv_bytes), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=forecast-{run_id}.csv"})
