from __future__ import annotations
import calendar
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
import io
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.destination import DestinationTable
from app.models.forecast import ForecastConfig, ForecastAdjustment, ForecastBusinessRule, ForecastRun, ForecastAck, ForecastAckHistory
from app.models.rules import Department
from app.models.etl_task import EtlTask
from app.services.forecast_service import fetch_metadata, gold_date_expr, validate_config
from app.services.trino_service import execute as trino_execute

router = APIRouter(prefix="/forecast", tags=["forecast"])

class ConnectionAllocationRuleIn(BaseModel):
    connection_id: int | None = None
    connection_code: str | None = None
    period_allocation: Literal["charge_interval"] = "charge_interval"
    charge_end_column: str | None = None

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
    period_allocation: Literal["none", "charge_interval"] = "none"
    charge_end_column: str | None = None
    connection_allocation_rules: list[ConnectionAllocationRuleIn] = []
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

class IdentificationClauseIn(BaseModel):
    column: str
    values: list[str] = []

class BusinessRuleIn(BaseModel):
    name: str
    identification_column: str = ""
    identification_values: list[str] = []
    identification_clauses: list[IdentificationClauseIn] = []
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
        "period_allocation": config.period_allocation, "charge_end_column": config.charge_end_column,
        "connection_allocation_rules": list(config.connection_allocation_rules or []),
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
        period_allocation=metadata.get("default_period_allocation", "none"),
        charge_end_column=metadata.get("default_charge_end_column"),
        connection_allocation_rules=[],
    )
    db.add(row)
    await db.commit()

async def ensure_focus_forecast_rules(db: AsyncSession) -> None:
    """Backward-compatible alias for provider rule seeding."""
    await ensure_provider_forecast_rules(db)


PROVIDER_RULE_PACKS: dict[str, list[dict[str, Any]]] = {
    "azure_gcp": [
        {
            "name": "Purchase recorrente Savings Plan",
            "identification_column": "chargecategory",
            "identification_values": ["Purchase"],
            "charge_type": "Purchase",
            "behavior": "RECURRING_MONTHLY",
            "reference_column": "commitmentdiscountid",
            "priority": 10,
            "scope": {},
        },
        {
            "name": "Return simulado",
            "identification_column": "chargecategory",
            "identification_values": ["Return"],
            "charge_type": "Return",
            "behavior": "RECURRING_MONTHLY",
            "reference_column": "commitmentdiscountid",
            "priority": 11,
            "scope": {},
        },
        {
            "name": "Refund cancela compromisso",
            "identification_column": "chargecategory",
            "identification_values": ["Refund"],
            "charge_type": "Refund",
            "behavior": "CANCEL_ON_REFUND",
            "reference_column": "commitmentdiscountid",
            "priority": 12,
            "scope": {},
            "cancel_on_refund": True,
        },
    ],
    "oci": [
        {
            "name": "OCI Refund cancela compromisso",
            "identification_column": "chargecategory",
            "identification_values": ["Refund"],
            "charge_type": "Refund",
            "behavior": "CANCEL_ON_REFUND",
            "reference_column": "commitmentdiscountid",
            "priority": 15,
            "scope": {"cloud_origem": "oci-prod"},
            "cancel_on_refund": True,
        },
    ],
    "marketplace": [
        {
            "name": "Marketplace ONESHOT zera forecast",
            "identification_clauses": [{"column": "marketplacebillingmodel", "values": ["ONESHOT"]}],
            "charge_type": "Purchase",
            "behavior": "REPLACE_VALUE",
            "replacement_value": 0.0,
            "priority": 5,
            "scope": {},
        },
        {
            "name": "Marketplace RECURRING mensal",
            "identification_clauses": [{"column": "marketplacebillingmodel", "values": ["RECURRING"]}],
            "charge_type": "Purchase",
            "behavior": "RECURRING_MONTHLY",
            "priority": 6,
            "scope": {},
        },
    ],
}


async def ensure_provider_forecast_rules(
    db: AsyncSession,
    *,
    config_id: int | None = None,
    providers: list[str] | None = None,
) -> dict[str, int]:
    """Install editable provider rule templates for FOCUS forecast configs."""
    selected = providers or list(PROVIDER_RULE_PACKS)
    configs = []
    if config_id is not None:
        row = await db.get(ForecastConfig, config_id)
        if row:
            configs = [row]
    else:
        configs = (await db.execute(select(ForecastConfig))).scalars().all()
    created = 0
    for config in configs:
        dest = await db.get(DestinationTable, config.destination_table_id)
        if not dest or "focus" not in f"{dest.name} {dest.physical_name}".lower():
            continue
        all_templates: list[dict[str, Any]] = []
        for provider in selected:
            all_templates.extend(PROVIDER_RULE_PACKS.get(provider, []))
        for template in all_templates:
            exists = (
                await db.execute(
                    select(ForecastBusinessRule.id).where(
                        ForecastBusinessRule.forecast_config_id == config.id,
                        ForecastBusinessRule.name == template["name"],
                    )
                )
            ).scalar_one_or_none()
            if exists is not None:
                continue
            db.add(
                ForecastBusinessRule(
                    forecast_config_id=config.id,
                    name=template["name"],
                    identification_column=template.get("identification_column", ""),
                    identification_values=list(template.get("identification_values", [])),
                    identification_clauses=list(template.get("identification_clauses", [])),
                    charge_type=template["charge_type"],
                    behavior=template["behavior"],
                    reference_column=template.get("reference_column"),
                    reference_values=template.get("reference_values", []),
                    value_column=config.measure_column,
                    start_period=template.get("start_period", "202001"),
                    end_period=template.get("end_period"),
                    growth_rate=float(template.get("growth_rate", 0) or 0),
                    replacement_value=template.get("replacement_value"),
                    cancel_on_refund=bool(template.get("cancel_on_refund", False)),
                    scope=dict(template.get("scope") or {}),
                    priority=int(template.get("priority", 10)),
                )
            )
            created += 1
    await db.commit()
    return {"created": created}

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
    # ServiceName is the FOCUS service grain used by Azure (consumedService) and GCP (service.description).
    # Do not suggest ResourceName/PricingCategory by default: GCP instance/SKU values explode the series.
    focus_order = (
        "cloud_origem",
        "marketplaceproductname",
        "marketplacebillingmodel",
        "servicename",
        "subaccountname",
    )
    metadata["recommended_dimensions"] = [available[name] for name in focus_order if name in available]
    metadata["suggestion_profile"] = "FOCUS"
    if "chargeperiodend" in available:
        metadata["default_charge_end_column"] = available["chargeperiodend"]
    else:
        metadata["default_charge_end_column"] = None
    metadata["default_period_allocation"] = "none"
    return metadata

@router.get("/metadata/{destination_table_id}")
async def metadata(destination_table_id: int, db: AsyncSession = Depends(get_db)):
    dest, result = await _dest_metadata(db, destination_table_id)
    result = _apply_destination_suggestions(dest, result)
    result.update(destination_table_id=destination_table_id, destination_table=dest.physical_name, default_type="realizado")
    return result

@router.get("/configs")
async def list_configs(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(ForecastConfig, DestinationTable.name)
        .outerjoin(DestinationTable, DestinationTable.id == ForecastConfig.destination_table_id)
        .order_by(ForecastConfig.updated_at.desc())
    )).all()
    return [{**_out(row), "table_name": table_name} for row, table_name in rows]

@router.get("/configs/grid")
async def list_configs_grid(
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: str | None = None,
    sort_field: str = "updated_at",
    sort_direction: str = Query("desc", pattern="^(asc|desc)$"),
    filters: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Server-side grid over forecast configurations (pagination, search, sort, AND filters)."""
    import json
    from sqlalchemy import or_, String as SAString, cast as sacast, func as safunc
    from datetime import datetime as _dt

    table_join = DestinationTable
    stmt = select(ForecastConfig, table_join.name).join(
        table_join, table_join.id == ForecastConfig.destination_table_id
    )
    conditions = []
    if search and search.strip():
        needle = f"%{search.strip()}%"
        conditions.append(or_(
            ForecastConfig.name.ilike(needle),
            table_join.name.ilike(needle),
            ForecastConfig.measure_column.ilike(needle),
            ForecastConfig.model_mode.ilike(needle),
            ForecastConfig.period_allocation.ilike(needle),
        ))
    fields = {
        "id": ForecastConfig.id,
        "name": ForecastConfig.name,
        "table_name": table_join.name,
        "measure_column": ForecastConfig.measure_column,
        "horizon_months": ForecastConfig.horizon_months,
        "model_mode": ForecastConfig.model_mode,
        "version": ForecastConfig.version,
        "active": ForecastConfig.active,
        "updated_at": ForecastConfig.updated_at,
    }
    try:
        specs = json.loads(filters) if filters else []
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "Filtros inválidos") from exc
    for spec in specs if isinstance(specs, list) else []:
        field = str(spec.get("field", ""))
        value = str(spec.get("value", ""))
        op = str(spec.get("operator", "equals"))
        column = fields.get(field)
        if not column or not value:
            continue
        if field == "updated_at":
            try:
                clean_val = value.replace("Z", "+00:00")
                dt_val = _dt.fromisoformat(clean_val) if "T" in clean_val else _dt.fromisoformat(clean_val + "T00:00:00")
                expr = safunc.date_trunc("minute", column) == safunc.date_trunc("minute", dt_val) if ":" in value else safunc.date(column) == dt_val.date()
            except ValueError:
                continue
        elif field in ("id", "horizon_months", "version"):
            try:
                num_val = int(value)
                expr = column == num_val
            except ValueError:
                continue
        elif field in ("active",):
            truthy = value.strip().lower() in {"true", "1", "yes", "sim", "ativa", "ativo"}
            expr = column.is_(True) if truthy else column.is_(False)
        elif field == "table_name":
            expr = table_join.name == value
        else:
            expr = column == value
        conditions.append(or_(column.is_(None), expr == False) if op == "notEquals" else expr)
    sort = fields.get(sort_field, ForecastConfig.updated_at)
    ordering = sort.desc() if sort_direction == "desc" else sort.asc()
    if sort_field == "table_name":
        ordering = safunc.lower(sort).desc() if sort_direction == "desc" else safunc.lower(sort).asc()
    filtered = stmt.where(*conditions) if conditions else stmt
    total = (await db.execute(select(safunc.count()).select_from(filtered.subquery()))).scalar_one()
    rows = (await db.execute(filtered.order_by(ordering, ForecastConfig.id.desc()).offset(offset).limit(limit))).all()
    items = [{
        "id": row.id,
        "name": row.name,
        "table_name": name,
        "destination_table_id": row.destination_table_id,
        "measure_column": row.measure_column,
        "date_column": row.date_column,
        "period_column": row.period_column,
        "dimensions": row.dimensions or [],
        "horizon_months": row.horizon_months,
        "model_mode": row.model_mode,
        "period_allocation": row.period_allocation,
        "charge_end_column": row.charge_end_column,
        "connection_allocation_rules": row.connection_allocation_rules or [],
        "version": row.version,
        "active": row.active,
        "updated_at": row.updated_at,
    } for row, name in rows]
    return {"items": items, "total": total}

@router.post("/configs", status_code=201)
async def create_config(body: ConfigIn, db: AsyncSession = Depends(get_db)):
    row = ForecastConfig(**body.model_dump()); db.add(row); await db.commit(); await db.refresh(row)
    # A FOCUS configuration is usable out of the box: the editable business
    # rule templates are created with it instead of waiting for an API restart.
    await ensure_provider_forecast_rules(db, config_id=row.id)
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
        for clause in rule.identification_clauses or []:
            column = clause.get("column") if isinstance(clause, dict) else None
            if column and column not in fields:
                errors.append(f"Regra '{rule.name}': coluna de identificação inválida: {column}")
        if rule.identification_column and rule.identification_column not in fields:
            errors.append(f"Regra '{rule.name}': coluna de identificação inválida: {rule.identification_column}")
        if rule.reference_column and rule.reference_column not in fields: errors.append(f"Regra '{rule.name}': coluna de referência inválida: {rule.reference_column}")
        if rule.value_column and rule.value_column not in fields: errors.append(f"Regra '{rule.name}': coluna de valor inválida: {rule.value_column}")
        if not rule.start_period or len(rule.start_period) != 6: errors.append(f"Regra '{rule.name}': período inicial deve ser YYYYMM")
    return {"valid": not errors, "errors": errors, "metadata": meta}

@router.get("/configs/{config_id}/business-rules")
async def list_business_rules(config_id: int, db: AsyncSession = Depends(get_db)):
    if not await db.get(ForecastConfig, config_id): raise HTTPException(404, "Configuração não encontrada")
    await ensure_provider_forecast_rules(db, config_id=config_id)
    rows = (await db.execute(select(ForecastBusinessRule).where(ForecastBusinessRule.forecast_config_id == config_id).order_by(ForecastBusinessRule.priority, ForecastBusinessRule.id))).scalars().all()
    return [_out(r) for r in rows]

@router.get("/configs/{config_id}/business-rules/grid")
async def list_business_rules_grid(
    config_id: int,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: str | None = None,
    sort_field: str = "priority",
    sort_direction: str = Query("asc", pattern="^(asc|desc)$"),
    filters: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Server-side grid over the business rules of one forecast configuration."""
    import json
    from sqlalchemy import or_, func as safunc

    if not await db.get(ForecastConfig, config_id): raise HTTPException(404, "Configuração não encontrada")
    await ensure_provider_forecast_rules(db, config_id=config_id)
    stmt = select(ForecastBusinessRule).where(ForecastBusinessRule.forecast_config_id == config_id)
    conditions = []
    if search and search.strip():
        needle = f"%{search.strip()}%"
        conditions.append(or_(
            ForecastBusinessRule.name.ilike(needle),
            ForecastBusinessRule.charge_type.ilike(needle),
            ForecastBusinessRule.behavior.ilike(needle),
            ForecastBusinessRule.identification_column.ilike(needle),
        ))
    fields = {
        "id": ForecastBusinessRule.id,
        "name": ForecastBusinessRule.name,
        "charge_type": ForecastBusinessRule.charge_type,
        "behavior": ForecastBusinessRule.behavior,
        "start_period": ForecastBusinessRule.start_period,
        "end_period": ForecastBusinessRule.end_period,
        "priority": ForecastBusinessRule.priority,
        "active": ForecastBusinessRule.active,
        "version": ForecastBusinessRule.version,
        "updated_at": ForecastBusinessRule.updated_at,
    }
    try:
        specs = json.loads(filters) if filters else []
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "Filtros inválidos") from exc
    for spec in specs if isinstance(specs, list) else []:
        field = str(spec.get("field", ""))
        value = str(spec.get("value", ""))
        op = str(spec.get("operator", "equals"))
        column = fields.get(field)
        if not column or not value:
            continue
        if field in ("id", "priority", "version"):
            try:
                num_val = int(value)
                expr = column == num_val
            except ValueError:
                continue
        elif field == "active":
            truthy = value.strip().lower() in {"true", "1", "yes", "sim", "ativa", "ativo"}
            expr = column.is_(True) if truthy else column.is_(False)
        else:
            expr = column == value
        conditions.append(or_(column.is_(None), expr == False) if op == "notEquals" else expr)
    sort = fields.get(sort_field, ForecastBusinessRule.priority)
    ordering = sort.desc() if sort_direction == "desc" else sort.asc()
    filtered = stmt.where(*conditions) if conditions else stmt
    total = (await db.execute(select(safunc.count()).select_from(filtered.subquery()))).scalar_one()
    rows = (await db.execute(filtered.order_by(ordering, ForecastBusinessRule.id).offset(offset).limit(limit))).scalars().all()
    items = [_out(r) for r in rows]
    return {"items": items, "total": total}

async def _validate_rule_fields(config_id: int, body: BusinessRuleIn, db: AsyncSession):
    config = await db.get(ForecastConfig, config_id)
    if not config: raise HTTPException(404, "Configuração não encontrada")
    dest, meta = await _dest_metadata(db, config.destination_table_id)
    fields = {c["name"] for c in meta["columns"]}
    for clause in body.identification_clauses or []:
        if clause.column and clause.column not in fields:
            raise HTTPException(422, f"Coluna de identificação não existe na Gold: {clause.column}")
    for field, label in ((body.identification_column, "identificação"), (body.reference_column, "referência"), (body.value_column, "valor")):
        if field and field not in fields: raise HTTPException(422, f"Coluna de {label} não existe na Gold: {field}")
    has_identity = bool(body.identification_clauses) or bool(body.identification_values) or bool(body.scope)
    if body.behavior == "CHARGE_INTERVAL":
        if not body.reference_column:
            raise HTTPException(422, "CHARGE_INTERVAL exige coluna de fim do período (reference_column)")
        if not has_identity:
            raise HTTPException(422, "CHARGE_INTERVAL exige identificação ou escopo (ex.: cloud_origem=ibm-ti)")
    elif not has_identity:
        raise HTTPException(422, "Informe identification_clauses, identification_values ou escopo")
    allowed = {"REPEAT_NEXT_MONTH", "RECURRING_MONTHLY", "RECURRING_UNTIL_END", "CANCEL_ON_REFUND", "REPLACE_VALUE", "CHARGE_INTERVAL", "SOURCE_EXCLUDE_PREFIX", "SOURCE_MEAN_30D_PREFIX"}
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
async def create_run(config_id: int, trigger_type: str = "MANUAL", simulated_as_of: str | None = None, db: AsyncSession = Depends(get_db)):
    config = await db.get(ForecastConfig, config_id)
    if not config: raise HTTPException(404, "Configuração não encontrada")
    simulated_date: date | None = None
    if simulated_as_of:
        try:
            simulated_date = date.fromisoformat(simulated_as_of)
        except ValueError:
            raise HTTPException(422, "simulated_as_of deve estar no formato YYYY-MM-DD")
    active = (await db.execute(select(ForecastRun.id).where(ForecastRun.forecast_config_id == config_id, ForecastRun.status.in_(["QUEUED", "RUNNING"])))).scalar_one_or_none()
    if active: raise HTTPException(409, f"Já existe forecast ativo: {active}")
    from app.services.worker_runtime import any_worker_healthy

    if not any_worker_healthy():
        raise HTTPException(503, "Worker ETL indisponível; forecast não foi criado")
    destination = await db.get(DestinationTable, config.destination_table_id)
    if not destination or not destination.active:
        raise HTTPException(404, "Tabela Gold da configuração não encontrada")
    # Do not create an execution that is guaranteed to fail a few seconds
    # later. The old heartbeat-only check considered the worker healthy while
    # the Trino coordinator was down, producing zero-row failed Forecasts.
    try:
        safe_name = destination.physical_name.replace('"', '""')
        await trino_execute(f'SELECT 1 FROM iceberg.gold."{safe_name}" LIMIT 1', timeout_s=15)
        await fetch_metadata(destination.physical_name, "gold")
    except Exception as exc:
        raise HTTPException(503, f"Trino ou a Gold FOCUS estão indisponíveis: {exc}")
    snapshot = _config_snapshot(config, destination)
    source_name, source_namespace = destination.physical_name, "gold"
    snapshot.update(destination_physical_name=source_name, source_namespace=source_namespace)
    if simulated_date is not None:
        snapshot["simulated_as_of"] = simulated_date.isoformat()
    row = ForecastRun(
        forecast_config_id=config_id, config_version=config.version, trigger_type=trigger_type,
        # The cutoff is a data-driven value (MAX data collected − cutoff_days)
        # computed by the engine and persisted after execution. Simulated runs
        # anchor on the simulated date; real runs start NULL — never "today",
        # which would misrepresent a delayed report as the calculation base.
        cutoff_date=(simulated_date.isoformat() if simulated_date is not None else None),
        config_snapshot=snapshot,
        destination_table_id_snapshot=destination.id, destination_physical_name=source_name,
        classification_version_id=None,
    )
    db.add(row); await db.flush()
    task = EtlTask(task_type="forecast", status="QUEUED", destination_table_id=config_id, result_json={"forecast_run_id": row.id}, errors=[])
    db.add(task); await db.commit(); await db.refresh(row)
    return {"run_id": row.id, "task_id": task.id, "status": row.status}

@router.get("/runs")
async def list_runs(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ForecastRun).order_by(ForecastRun.started_at.desc()).limit(100))).scalars().all()
    items = [_out(r) for r in rows]
    for item, run in zip(items, rows):
        item["simulated_as_of"] = (run.config_snapshot or {}).get("simulated_as_of")
    return items

@router.get("/runs/grid")
async def list_runs_grid(
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: str | None = None,
    sort_field: str = "started_at",
    sort_direction: str = Query("desc", pattern="^(asc|desc)$"),
    filters: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Server-side grid over forecast runs: pagination, search, sort and AND filters.

    Mirrors the contract of GET /etl/reprocess-history so the frontend can
    drive a MUI DataGrid with paginationMode/sortingMode/filterMode=server.
    """
    import json
    from sqlalchemy import or_, String as SAString, cast as sacast, func as safunc
    from datetime import datetime as _dt

    config_join = ForecastConfig
    stmt = select(ForecastRun, config_join.name).outerjoin(
        config_join, config_join.id == ForecastRun.forecast_config_id
    )
    conditions = []
    if search and search.strip():
        needle = f"%{search.strip()}%"
        conditions.append(or_(
            ForecastRun.status.ilike(needle),
            ForecastRun.phase.ilike(needle),
            sacast(ForecastRun.result_table, SAString).ilike(needle),
            ForecastRun.error.ilike(needle),
            ForecastRun.trigger_type.ilike(needle),
            config_join.name.ilike(needle),
        ))
    fields = {
        "id": ForecastRun.id,
        "forecast_config_id": ForecastRun.forecast_config_id,
        "status": ForecastRun.status,
        "phase": ForecastRun.phase,
        "rows_written": ForecastRun.rows_written,
        "total_series": ForecastRun.total_series,
        "processed_series": ForecastRun.processed_series,
        "result_table": ForecastRun.result_table,
        "error": ForecastRun.error,
        "started_at": ForecastRun.started_at,
        "finished_at": ForecastRun.finished_at,
        "config_name": config_join.name,
    }
    try:
        specs = json.loads(filters) if filters else []
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "Filtros inválidos") from exc
    for spec in specs if isinstance(specs, list) else []:
        field = str(spec.get("field", ""))
        value = str(spec.get("value", ""))
        op = str(spec.get("operator", "equals"))
        column = fields.get(field)
        if not column or not value:
            continue
        if field in ("started_at", "finished_at"):
            try:
                clean_val = value.replace("Z", "+00:00")
                dt_val = _dt.fromisoformat(clean_val) if "T" in clean_val else _dt.fromisoformat(clean_val + "T00:00:00")
                expr = safunc.date_trunc("minute", column) == safunc.date_trunc("minute", dt_val) if ":" in value else safunc.date(column) == dt_val.date()
            except ValueError:
                continue
        elif field in ("id", "rows_written", "total_series", "processed_series"):
            try:
                num_val = int(value)
                expr = column == num_val
            except ValueError:
                continue
        elif field == "config_name":
            expr = config_join.name == value
        else:
            expr = column == value
        conditions.append(or_(column.is_(None), expr == False) if op == "notEquals" else expr)
    sort = fields.get(sort_field, ForecastRun.started_at)
    ordering = sort.desc() if sort_direction == "desc" else sort.asc()
    if sort_field == "config_name":
        ordering = safunc.lower(sort).desc() if sort_direction == "desc" else safunc.lower(sort).asc()
    filtered = stmt.where(*conditions) if conditions else stmt
    total = (await db.execute(select(safunc.count()).select_from(filtered.subquery()))).scalar_one()
    rows = (await db.execute(filtered.order_by(ordering, ForecastRun.id.desc()).offset(offset).limit(limit))).all()
    items = [{
        "id": run.id,
        "forecast_config_id": run.forecast_config_id,
        "config_name": name,
        "config_version": run.config_version,
        "trigger_type": run.trigger_type,
        "status": run.status,
        "phase": run.phase or "",
        "total_series": run.total_series,
        "processed_series": run.processed_series,
        "rows_written": run.rows_written,
        "result_table": run.result_table,
        "error": run.error or "",
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "simulated_as_of": (run.config_snapshot or {}).get("simulated_as_of"),
    } for run, name in rows]
    return {"items": items, "total": total}

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
    actual_source, dimon_date_expr, measure, type_col, type_filter = _accuracy_actual_context(snapshot)
    # Dimon já resolve date_expr/type_filter (sem chargeperiodstart); a Gold
    # legada usa date_col original e o filtro com prefixo WHERE.
    if "dest_dm_cost_" in actual_source:
        date_expr = dimon_date_expr
    else:
        date_expr = gold_date_expr(date_col, period_col)
        type_col = snapshot.get("type_column")
        type_filter = f" WHERE ({_qi(type_col)} = 'realizado' OR {_qi(type_col)} IS NULL)" if type_col else ""
    actual_dims = ", ".join(_qi(field) for field in series_dims)
    actual_group = (actual_dims + ", ") if actual_dims else ""
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

# ---------------------------------------------------------------------------
# Acurácia de Forecast (snapshots simulados mês a mês)
# ---------------------------------------------------------------------------

def _month_after(as_of: date) -> str:
    """First month projected by a simulation (the target month).

    Convention: ``simulated_as_of`` is the day the snapshot is taken — day 2
    of the target month (D-2).  The engine cutoff then lands on the last day
    of the previous month, so the first projected month is the month of
    ``as_of``.  The +1 day shift also covers the legacy convention where
    ``as_of`` was the last day of the previous month.
    """
    target = as_of + timedelta(days=1)
    return f"{target.year:04d}{target.month:02d}"


def _accuracy_metrics(forecast_value: float | None, actual_value: float | None) -> dict[str, Any]:
    forecast_value = float(forecast_value or 0)
    actual_value = float(actual_value or 0)
    variance = actual_value - forecast_value
    variance_pct = (variance / forecast_value) if forecast_value else None
    wape = (abs(variance) / actual_value) if actual_value else None
    accuracy = (1.0 - wape) if wape is not None else None
    bias = ((forecast_value - actual_value) / actual_value) if actual_value else None
    return {
        "forecast_value": forecast_value,
        "actual_value": actual_value,
        "variance": variance,
        "variance_pct": variance_pct,
        "wape": wape,
        "accuracy": accuracy,
        "bias": bias,
    }


def _accuracy_actual_context(snapshot: dict[str, Any]) -> tuple[str, str, str, str | None, str]:
    """Resolve the 'actual' side of forecast accuracy.

    Reads the pre-aggregated Dimon (dest_dm_cost_*) instead of the 31M-row
    Gold when the snapshot targets the FOCUS table: the Dimon carries the
    same tipo/realizado grain at a fraction of the scan cost.
    Returns (actual_source, date_expr, measure, type_col, type_filter).
    """
    dest = str(snapshot.get("destination_physical_name") or "")
    dimon = "dest_dm_cost_visao_completa"
    if dest.replace("-", "_").replace(".", "_").lower() == "dest_modelo_focus":
        actual_source = f"iceberg.dimon.{dimon}"
        # Dimon não tem chargeperiodstart: o mês vive em _billing_period /
        # period (YYYYMM). O date_expr precisa derivar da própria coluna de
        # período, senão a query referencia uma coluna inexistente.
        period_col = snapshot.get("period_column") or "_billing_period"
        date_expr = gold_date_expr(period_col, period_col)
        measure = snapshot.get("measure_column") or "realizado"
        type_col = "tipo"
        # Dimon only stores tipo='realizado' rows for realized facts.
        type_filter = " AND lower(CAST(\"tipo\" AS VARCHAR)) = 'realizado'"
        return actual_source, date_expr, measure, type_col, type_filter
    actual_source = f"iceberg.{snapshot.get('source_namespace', 'gold')}.{dest}"
    date_expr = gold_date_expr(snapshot["date_column"], snapshot.get("period_column"))
    measure = snapshot.get("measure_column") or "realizado"
    type_col = snapshot.get("type_column")
    type_filter = f" AND ({_qi(type_col)} = 'realizado' OR {_qi(type_col)} IS NULL)" if type_col else ""
    return actual_source, date_expr, measure, type_col, type_filter


def _daily_gold_actual_context(snapshot: dict[str, Any]) -> tuple[str, str, str, str | None, str]:
    """Resolve the non-aggregated Gold source used by the daily drill-down.

    Accuracy totals may use Dimon for the FOCUS model because its monthly grain
    makes those queries much cheaper.  A day-by-day chart cannot use that
    aggregate: its period-derived date maps every value to day 1.  Keep this
    path explicitly on the original Gold table and its configured date column.
    """
    dest = str(snapshot.get("destination_physical_name") or "")
    date_col = str(snapshot.get("date_column") or "")
    measure = str(snapshot.get("measure_column") or "realizado")
    if not dest or not date_col:
        raise HTTPException(422, "Snapshot sem origem Gold ou coluna de data para o detalhamento diário")
    actual_source = f"iceberg.gold.{dest}"
    date_expr = gold_date_expr(date_col, snapshot.get("period_column"))
    type_col = snapshot.get("type_column")
    type_filter = f" AND ({_qi(type_col)} = 'realizado' OR {_qi(type_col)} IS NULL)" if type_col else ""
    return actual_source, date_expr, measure, type_col, type_filter


def _accuracy_snapshot_context(run: ForecastRun, snapshot: dict[str, Any], schema: dict[str, str]) -> tuple[str, str, str, str, str | None, str]:
    """Return (result_table, actual_source, date_expr, measure, type_col, type_filter)."""
    if not snapshot.get("destination_physical_name") or not snapshot.get("measure_column") or not snapshot.get("date_column"):
        raise HTTPException(422, "Snapshot sem contrato congelado; acurácia não disponível")
    actual_source, date_expr, measure, type_col, type_filter = _accuracy_actual_context(snapshot)
    return run.result_table, actual_source, date_expr, measure, type_col, type_filter


def _department_scope_filter(department: str | None) -> str:
    """AND-chain that matches the selected department path prefix against
    departamento_nivel_{1..7} (children are included because deeper levels are
    not constrained) — same semantics as the ExpectedView director filter."""
    if not department or not str(department).strip():
        return ""
    parts = [part.strip() for part in str(department).split(">") if part.strip()]
    if not parts:
        return ""
    checks = [
        f"TRIM({_qi(f'departamento_nivel_{level}')}) = {_ql(name)}"
        for level, name in enumerate(parts, start=1)
        if level <= 7
    ]
    return (" AND " + " AND ".join(checks)) if checks else ""


def _accuracy_mode_filter(mode: str) -> str:
    """showback (default) excludes Chargeback; chargeback keeps only it."""
    if mode == "chargeback":
        return f" AND {_qi('departamento_nivel_2')} = 'Chargeback'"
    if mode == "showback":
        return (
            f" AND ({_qi('departamento_nivel_2')} <> 'Chargeback'"
            f" OR {_qi('departamento_nivel_2')} IS NULL"
            f" OR {_qi('departamento_nivel_2')} = '')"
        )
    return ""


def _driver_level(scope_path: list[str]) -> int:
    return min(len(scope_path) + 1, 7)


async def _accuracy_totals(db: AsyncSession, run: ForecastRun, target_month: str) -> dict[str, Any]:
    run, snapshot, schema = await _history_context(run.id, db)
    table, actual_source, date_expr, measure, _, type_filter = _accuracy_snapshot_context(run, snapshot, schema)
    period_col = snapshot.get("period_column")
    month_filter = (
        f"CAST({_qi(period_col)} AS VARCHAR) = {_ql(target_month)}"
        if period_col else f"date_format({date_expr}, '%Y%m') = {_ql(target_month)}"
    )
    try:
        f_records = await trino_execute(
            f'SELECT COALESCE(SUM(TRY_CAST("forecast_value" AS DOUBLE)),0) AS forecast_value, COUNT(*) AS rows FROM iceberg.forecast.{table} WHERE "forecast_month" = {_ql(target_month)}',
            timeout_s=90,
        )
        a_records = await trino_execute(
            f'SELECT COALESCE(SUM(COALESCE(TRY_CAST({_qi(measure)} AS DOUBLE),0)),0) AS actual_value FROM {actual_source} WHERE {month_filter}{type_filter}',
            timeout_s=120,
        )
    except Exception as exc:
        raise HTTPException(503, f"Falha ao calcular acurácia: {exc}")
    forecast_value = float(f_records[0].get("forecast_value") or 0) if f_records else 0.0
    actual_value = float(a_records[0].get("actual_value") or 0) if a_records else 0.0
    metrics = _accuracy_metrics(forecast_value, actual_value)
    metrics["rows_forecast"] = int(f_records[0].get("rows") or 0) if f_records else 0
    return metrics


def _desvio_metrics(forecast_value: float | None, actual_value: float | None) -> dict[str, Any]:
    """Delta na convenção unificada: realizado − projetado.
    Positivo = realizou acima do projetado = subestimou (gastou mais) = vermelho;
    negativo = superestimou (gastou menos) = verde."""
    return _accuracy_metrics(forecast_value, actual_value)


async def _accuracy_batch_totals(
    pairs: list[tuple[ForecastRun, str]],
    department: str | None = None,
    mode: str = "showback",
) -> tuple[dict[tuple[int, str], tuple[float, int]], dict[str, float]]:
    """Forecast + actual totals for many (run, target_month) pairs using exactly
    two Trino queries regardless of how many simulated runs exist: one UNION ALL
    over the immutable result tables and one partitioned Gold aggregation."""
    if not pairs:
        return {}, {}
    snapshot = dict(pairs[0][0].config_snapshot or {})
    if not snapshot.get("destination_physical_name") or not snapshot.get("measure_column"):
        raise HTTPException(422, "Snapshot sem contrato congelado; acurácia não disponível")
    actual_source, date_expr, measure, type_col, type_filter = _accuracy_actual_context(snapshot)
    scope_filter = _department_scope_filter(department)
    mode_filter = _accuracy_mode_filter(mode)
    periods = sorted({target for _, target in pairs})
    period_col = snapshot.get("period_column")
    month_expr = f"CAST({_qi(period_col)} AS VARCHAR)" if period_col else f"date_format({date_expr}, '%Y%m')"
    period_in = ", ".join(_ql(p) for p in periods)
    unions = " UNION ALL ".join(
        f'SELECT {run.id} AS run_id, CAST("forecast_month" AS VARCHAR) AS fmonth, '
        f'SUM(TRY_CAST("forecast_value" AS DOUBLE)) AS fv, COUNT(*) AS n '
        f'FROM iceberg.forecast.{run.result_table} '
        f'WHERE "forecast_month" IN ({period_in}){scope_filter}{mode_filter} '
        f'GROUP BY "forecast_month"'
        for run, _ in pairs
    )
    try:
        f_records = await trino_execute(
            f"SELECT run_id, fmonth, SUM(fv) AS fv, SUM(n) AS n FROM ({unions}) GROUP BY run_id, fmonth",
            timeout_s=90,
        )
        a_records = await trino_execute(
            f"SELECT {month_expr} AS period, SUM(COALESCE(TRY_CAST({_qi(measure)} AS DOUBLE),0)) AS av "
            f"FROM {actual_source} WHERE {month_expr} IN ({period_in}){type_filter}{scope_filter}{mode_filter} "
            f"GROUP BY {month_expr}",
            timeout_s=180,
        )
    except Exception as exc:
        raise HTTPException(503, f"Falha ao calcular acurácia: {exc}")
    fmap: dict[tuple[int, str], tuple[float, int]] = {}
    for record in f_records:
        fmap[(int(record.get("run_id") or 0), str(record.get("fmonth") or ""))] = (
            float(record.get("fv") or 0),
            int(record.get("n") or 0),
        )
    amap = {str(record.get("period") or ""): float(record.get("av") or 0) for record in a_records}
    return fmap, amap


def _latest_simulated_runs(runs: list[ForecastRun]) -> list[tuple[ForecastRun, str]]:
    """Keep only the most recent simulated run per target month (no Trino)."""
    best: dict[str, ForecastRun] = {}
    for run in runs:
        raw = (run.config_snapshot or {}).get("simulated_as_of")
        if not raw:
            continue
        try:
            as_of = date.fromisoformat(str(raw))
        except ValueError:
            continue
        best[_month_after(as_of)] = run  # later runs win
    return [(run, target) for target, run in sorted(best.items())]


_accuracy_months_cache: dict[tuple[Any, ...], tuple[float, list[dict[str, Any]]]] = {}
# Os dados só mudam quando um lote de simulados conclui ou a Gold é reprocessada —
# 5 minutos evita recomputes frequentes (~10s) que parecem travamento.
_ACCURACY_CACHE_TTL = 300.0


async def _accuracy_months_items(
    config_id: int | None,
    db: AsyncSession,
    department: str | None = None,
    mode: str = "showback",
) -> list[dict[str, Any]]:
    """Latest simulated snapshot per month with only two Trino queries, cached
    briefly so dashboard reloads are instant (data changes only when a new
    simulated batch completes)."""
    stmt = select(ForecastRun).where(
        ForecastRun.status == "COMPLETED",
        ForecastRun.result_table.is_not(None),
        ForecastRun.config_snapshot["simulated_as_of"].as_string() != "",
    )
    if config_id:
        stmt = stmt.where(ForecastRun.forecast_config_id == config_id)
    runs = (await db.execute(stmt.order_by(ForecastRun.started_at.asc()))).scalars().all()
    pairs = _latest_simulated_runs(runs)
    if not pairs:
        return []
    key = (
        config_id or 0,
        str(department or ""),
        mode,
        tuple((run.id, run.result_table, run.cutoff_date) for run, _ in pairs),
    )
    cached = _accuracy_months_cache.get(key)
    if cached is not None and datetime.utcnow().timestamp() - cached[0] < _ACCURACY_CACHE_TTL:
        return cached[1]
    fmap, amap = await _accuracy_batch_totals(pairs, department=department, mode=mode)
    today = date.today().strftime("%Y%m")
    items = []
    for run, target in pairs:
        as_of = date.fromisoformat(str((run.config_snapshot or {}).get("simulated_as_of")))
        forecast_value, rows_forecast = fmap.get((run.id, target), (0.0, 0))
        metrics = _accuracy_metrics(forecast_value, amap.get(target))
        metrics["rows_forecast"] = rows_forecast
        items.append({
            "run_id": run.id,
            "config_id": run.forecast_config_id,
            "simulated_as_of": as_of.isoformat(),
            "target_month": target,
            "cutoff_date": run.cutoff_date,
            "result_table": run.result_table,
            "started_at": run.started_at,
            "closed": target < today,
            **metrics,
        })
    _accuracy_months_cache[key] = (datetime.utcnow().timestamp(), items)
    return items


@router.get("/accuracy/months")
async def accuracy_months(
    config_id: int | None = None,
    department: str | None = None,
    mode: str = "showback",
    db: AsyncSession = Depends(get_db),
):
    """Meses simulados: o snapshot mais recente por mês, com apenas duas queries Trino.
    ``department`` (caminho "N1 > N2 > ...") e ``mode`` (showback/chargeback/all) filtram
    os totais — mesma semântica do filtro de diretor da Visão Esperada."""
    return await _accuracy_months_items(config_id, db, department=department, mode=mode)


@router.get("/accuracy/summary")
async def accuracy_summary(run_id: int, target_month: str | None = None, db: AsyncSession = Depends(get_db)):
    run = await db.get(ForecastRun, run_id)
    if not run:
        raise HTTPException(404, "Execução não encontrada")
    snapshot = dict(run.config_snapshot or {})
    if not snapshot.get("simulated_as_of"):
        raise HTTPException(422, "Esta execução não é um simulado de acurácia")
    target = target_month or _month_after(date.fromisoformat(str(snapshot["simulated_as_of"])))
    metrics = await _accuracy_totals(db, run, target)
    metrics.update({
        "run_id": run.id,
        "config_id": run.forecast_config_id,
        "simulated_as_of": snapshot.get("simulated_as_of"),
        "target_month": target,
        "cutoff_date": run.cutoff_date,
        "result_table": run.result_table,
        "closed": target < date.today().strftime("%Y%m"),
        "compared_at": datetime.utcnow().isoformat() + "Z",
    })
    return metrics


@router.get("/accuracy/dimensions")
async def accuracy_dimensions(
    run_id: int,
    target_month: str | None = None,
    dimension: str | None = None,
    department: str | None = None,
    mode: str = "showback",
    db: AsyncSession = Depends(get_db),
):
    run = await db.get(ForecastRun, run_id)
    if not run:
        raise HTTPException(404, "Execução não encontrada")
    run, snapshot, schema = await _history_context(run.id, db)
    if not snapshot.get("simulated_as_of"):
        raise HTTPException(422, "Esta execução não é um simulado de acurácia")
    target = target_month or _month_after(date.fromisoformat(str(snapshot["simulated_as_of"])))
    table, actual_source, date_expr, measure, _, type_filter = _accuracy_snapshot_context(run, snapshot, schema)
    available = [field for field in snapshot.get("dimensions", []) if field in schema]
    dim = dimension or (available[0] if available else "cloud_origem")
    if dim not in schema:
        raise HTTPException(422, f"Dimensão inválida para este snapshot: {dim}")
    period_col = snapshot.get("period_column")
    month_filter = (
        f"CAST({_qi(period_col)} AS VARCHAR) = {_ql(target)}"
        if period_col else f"date_format({date_expr}, '%Y%m') = {_ql(target)}"
    )
    scope_filter = _department_scope_filter(department)
    mode_filter = _accuracy_mode_filter(mode)
    null_label = str(snapshot.get("null_dimension_label") or "Não classificado")
    dim_expr = f"COALESCE(CAST({_qi(dim)} AS VARCHAR), {_ql(null_label)})"
    sql = f"""
      WITH f AS (
        SELECT {dim_expr} AS dim_v, SUM(TRY_CAST("forecast_value" AS DOUBLE)) AS forecast_value
        FROM iceberg.forecast.{table} WHERE "forecast_month" = {_ql(target)} AND "tipo" = 'forecast'{scope_filter}{mode_filter} GROUP BY {dim_expr}
      ),
      a AS (
        SELECT {dim_expr} AS dim_v, SUM(COALESCE(TRY_CAST({_qi(measure)} AS DOUBLE),0)) AS actual_value
        FROM {actual_source} WHERE {month_filter}{type_filter}{scope_filter}{mode_filter} GROUP BY {dim_expr}
      )
      SELECT COALESCE(f.dim_v, a.dim_v) AS dim_v,
             COALESCE(f.forecast_value, 0) AS forecast_value,
             COALESCE(a.actual_value, 0) AS actual_value,
             COALESCE(a.actual_value, 0) - COALESCE(f.forecast_value, 0) AS variance
      FROM f FULL OUTER JOIN a ON f.dim_v = a.dim_v
    """
    try:
        records = await trino_execute(sql, timeout_s=120)
    except Exception as exc:
        raise HTTPException(503, f"Falha ao consultar dimensões de acurácia: {exc}")
    out = []
    for record in records:
        dim_v = record.get("dim_v")
        if dim_v is None or str(dim_v) == "":
            dim_v = snapshot.get("null_dimension_label") or "Não classificado"
        metrics = _accuracy_metrics(record.get("forecast_value"), record.get("actual_value"))
        metrics["dimension_value"] = dim_v
        out.append(metrics)
    out.sort(key=lambda item: abs(item.get("variance") or 0), reverse=True)
    return {"run_id": run.id, "target_month": target, "dimension": dim, "simulated_as_of": snapshot.get("simulated_as_of"), "records": out}


@router.get("/accuracy/delta")
async def accuracy_delta(
    run_id: int,
    target_month: str | None = None,
    mode: str = "showback",
    department: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Ofensores do desvio (projetado × realizado) no nível abaixo do departamento
    selecionado — mesma semântica do ExpectedView (driver = nível do caminho + 1).
    ``department`` é o caminho "N1 > N2 > ..." e ``mode`` filtra showback/chargeback."""
    run = await db.get(ForecastRun, run_id)
    if not run:
        raise HTTPException(404, "Execução não encontrada")
    snapshot = dict(run.config_snapshot or {})
    if not snapshot.get("simulated_as_of"):
        raise HTTPException(422, "Esta execução não é um simulado de acurácia")
    target = target_month or _month_after(date.fromisoformat(str(snapshot["simulated_as_of"])))
    table, actual_source, date_expr, measure, _, type_filter = _accuracy_snapshot_context(run, snapshot, {})
    scope_path = [part.strip() for part in (department or "").split(">") if part.strip()]
    scope_filter = _department_scope_filter(department)
    mode_filter = _accuracy_mode_filter(mode)

    # Quando o departamento selecionado é o ÚLTIMO nível cadastrado (folha),
    # não existe nível abaixo — o detalhe passa a ser por ServiceSubcategory.
    leaf_mode = False
    if scope_path:
        departments = (await db.execute(select(Department).where(Department.active == True))).scalars().all()
        by_parent: dict[int | None, list[Department]] = defaultdict(list)
        by_id: dict[int, Department] = {}
        for d in departments:
            by_id[d.id] = d
            by_parent[d.parent_id].append(d)

        def path_of(d: Department) -> list[str]:
            parts: list[str] = []
            cur: Department | None = d
            seen: set[int] = set()
            while cur and cur.id not in seen:
                seen.add(cur.id)
                parts.append((cur.name or "").strip())
                cur = by_id.get(cur.parent_id) if cur.parent_id else None
            return list(reversed(parts))

        for d in departments:
            if path_of(d) == scope_path:
                leaf_mode = not by_parent.get(d.id)
                break
    if leaf_mode:
        records = await _desvios_subcat(run, snapshot, target, scope_path, scope_path[:-1])
        out = []
        for r in records:
            metrics = _accuracy_metrics(r["forecast_value"], r["actual_value"])
            out.append({
                "name": r["subcategory"], "subcategory": r["subcategory"], "path": [*scope_path, r["subcategory"]],
                "forecast_value": r["forecast_value"], "actual_value": r["actual_value"],
                "variance": metrics["variance"], "variance_pct": metrics["variance_pct"], "accuracy": metrics["accuracy"],
                "wape": metrics.get("wape"), "bias": metrics.get("bias"),
            })
        out.sort(key=lambda item: abs(item.get("variance") or 0), reverse=True)
        return {
            "run_id": run.id, "target_month": target, "mode": mode,
            "department": department or "", "level": 0, "leaf": True,
            "level_label": "ServiceSubcategory", "dimension": "servicesubcategory", "records": out,
        }

    driver_level = _driver_level(scope_path)
    dim = f"departamento_nivel_{driver_level}"
    null_label = str(snapshot.get("null_dimension_label") or "Não classificado")
    dim_expr = f"COALESCE(CAST({_qi(dim)} AS VARCHAR), {_ql(null_label)})"
    period_col = snapshot.get("period_column")
    actual_month = (
        f"CAST({_qi(period_col)} AS VARCHAR) = {_ql(target)}"
        if period_col else f"date_format({date_expr}, '%Y%m') = {_ql(target)}"
    )
    sql = f"""
      WITH f AS (
        SELECT {dim_expr} AS dim_v, SUM(TRY_CAST("forecast_value" AS DOUBLE)) AS forecast_value
        FROM iceberg.forecast.{table}
        WHERE "forecast_month" = {_ql(target)} AND "tipo" = 'forecast'{scope_filter}{mode_filter}
        GROUP BY {dim_expr}
      ),
      a AS (
        SELECT {dim_expr} AS dim_v, SUM(COALESCE(TRY_CAST({_qi(measure)} AS DOUBLE),0)) AS actual_value
        FROM {actual_source}
        WHERE {actual_month}{type_filter}{scope_filter}{mode_filter}
        GROUP BY {dim_expr}
      )
      SELECT COALESCE(f.dim_v, a.dim_v) AS dim_v,
             COALESCE(f.forecast_value, 0) AS forecast_value,
             COALESCE(a.actual_value, 0) AS actual_value,
             COALESCE(a.actual_value, 0) - COALESCE(f.forecast_value, 0) AS variance
      FROM f FULL OUTER JOIN a ON f.dim_v = a.dim_v
    """
    try:
        records = await trino_execute(sql, timeout_s=120)
    except Exception as exc:
        raise HTTPException(503, f"Falha ao consultar delta de acurácia: {exc}")
    out = []
    for record in records:
        metrics = _accuracy_metrics(record.get("forecast_value"), record.get("actual_value"))
        metrics["name"] = str(record.get("dim_v") or null_label)
        metrics["path"] = [*scope_path, metrics["name"]]
        out.append(metrics)
    out.sort(key=lambda item: abs(item.get("variance") or 0), reverse=True)
    level_labels = {
        2: "Ramo (Showback/Chargeback)", 3: "Provedor", 4: "Diretoria (nivel 4)",
        5: "Projeto (nivel 5)", 6: "Nível 6", 7: "Nível 7",
    }
    return {
        "run_id": run.id,
        "target_month": target,
        "mode": mode,
        "department": department or "",
        "level": driver_level,
        "level_label": level_labels.get(driver_level, f"Nível {driver_level}"),
        "leaf": False,
        "dimension": dim,
        "records": out,
    }


# ---------------------------------------------------------------------------
# Desvios (último nível por departamento) + Acks (motivo do desvio)
# ---------------------------------------------------------------------------

_desvios_grain_cache: dict[tuple[Any, ...], tuple[float, dict[tuple[Any, ...], float]]] = {}
_DESVIOS_GRAIN_TTL = 600.0


def _leaf_paths(departments: list[Department], scope_path: list[str]) -> list[list[str]]:
    """Active leaf departments (deepest level, no children) under the scope."""
    by_parent: dict[int | None, list[Department]] = defaultdict(list)
    by_id: dict[int, Department] = {}
    for department in departments:
        by_id[department.id] = department
        by_parent[department.parent_id].append(department)

    def path_of(department: Department) -> list[str]:
        parts: list[str] = []
        current: Department | None = department
        seen: set[int] = set()
        while current and current.id not in seen:
            seen.add(current.id)
            parts.append((current.name or "").strip())
            current = by_id.get(current.parent_id) if current.parent_id else None
        return list(reversed(parts))

    leaves: list[list[str]] = []
    for department in departments:
        if by_parent.get(department.id):
            continue
        path = path_of(department)
        if not path:
            continue
        if not scope_path or path[:len(scope_path)] == scope_path:
            leaves.append(path)
    return leaves


def _grain_select(group_cols: list[str]) -> tuple[str, str]:
    select = ", ".join(group_cols)
    return select, select


async def _desvios_grain(
    run: ForecastRun,
    snapshot: dict[str, Any],
    target: str,
    scope_path: list[str] | None = None,
) -> tuple[dict[tuple[Any, ...], float], dict[tuple[Any, ...], float]]:
    """(n1..n7) -> forecast/actual totals of one month, restricted to the scope
    prefix when provided (faster scans for narrower department views)."""
    scope_path = scope_path or []
    cache_key = (run.result_table, target, "list", tuple(scope_path))
    cached = _desvios_grain_cache.get(cache_key)
    if cached is not None and datetime.utcnow().timestamp() - cached[0] < _DESVIOS_GRAIN_TTL:
        return cached[1]
    actual_source, date_expr, measure, type_col, type_filter = _accuracy_actual_context(snapshot)
    period_col = snapshot.get("period_column")
    actual_month = (
        f"CAST({_qi(period_col)} AS VARCHAR) = {_ql(target)}"
        if period_col else f"date_format({date_expr}, '%Y%m') = {_ql(target)}"
    )
    scope_filter = "".join(
        f" AND {_qi(f'departamento_nivel_{index + 1}')} = {_ql(name)}"
        for index, name in enumerate(scope_path)
        if index < 7
    )
    levels = ", ".join(_qi(f"departamento_nivel_{level}") for level in range(1, 8))
    try:
        a_records = await trino_execute(
            f"SELECT {levels}, SUM(COALESCE(TRY_CAST({_qi(measure)} AS DOUBLE),0)) AS av "
            f"FROM {actual_source} WHERE {actual_month}{type_filter}{scope_filter} GROUP BY {levels}",
            timeout_s=120,
        )
        f_records = await trino_execute(
            f"SELECT {levels}, SUM(TRY_CAST(\"forecast_value\" AS DOUBLE)) AS fv "
            f"FROM iceberg.forecast.{run.result_table} "
            f"WHERE \"forecast_month\" = {_ql(target)} AND \"tipo\" = 'forecast'{scope_filter} GROUP BY {levels}",
            timeout_s=120,
        )
    except Exception as exc:
        raise HTTPException(503, f"Falha ao agregar desvios: {exc}")

    def key_of(row: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(row.get(f"departamento_nivel_{level}") for level in range(1, 8))

    actual_grain = {key_of(r): float(r.get("av") or 0) for r in a_records}
    forecast_grain = {key_of(r): float(r.get("fv") or 0) for r in f_records}
    _desvios_grain_cache[cache_key] = (datetime.utcnow().timestamp(), (forecast_grain, actual_grain))
    return forecast_grain, actual_grain


def _leaf_totals(
    leaf: list[str],
    forecast_grain: dict[tuple[Any, ...], float],
    actual_grain: dict[tuple[Any, ...], float],
) -> tuple[float, float]:
    """Forecast/actual totals for one leaf path (deeper levels empty)."""
    depth = len(leaf)
    forecast = 0.0
    actual = 0.0
    for levels, value in forecast_grain.items():
        if tuple(leaf) == tuple(str(x or "") for x in levels[:depth]) and all(str(x or "") == "" for x in levels[depth:]):
            forecast += value
    for levels, value in actual_grain.items():
        if tuple(leaf) == tuple(str(x or "") for x in levels[:depth]) and all(str(x or "") == "" for x in levels[depth:]):
            actual += value
    return forecast, actual


async def _desvios_subcat_grain(
    run: ForecastRun,
    snapshot: dict[str, Any],
    target: str,
    scope_path: list[str],
) -> tuple[dict[tuple[Any, ...], float], dict[tuple[Any, ...], float]]:
    """(n1..n7, servicesubcategory) -> (forecast, actual) for one month,
    restricted to the scope prefix.  Computed once per scope and cached, so all
    leaf expansions inside that scope are instant."""
    cache_key = (run.result_table, target, "subcat-grain", tuple(scope_path))
    cached = _desvios_grain_cache.get(cache_key)
    if cached is not None and datetime.utcnow().timestamp() - cached[0] < _DESVIOS_GRAIN_TTL:
        return cached[1]
    actual_source, date_expr, measure, type_col, type_filter = _accuracy_actual_context(snapshot)
    period_col = snapshot.get("period_column")
    actual_month = (
        f"CAST({_qi(period_col)} AS VARCHAR) = {_ql(target)}"
        if period_col else f"date_format({date_expr}, '%Y%m') = {_ql(target)}"
    )
    scope_filter = "".join(
        f" AND {_qi(f'departamento_nivel_{index + 1}')} = {_ql(name)}"
        for index, name in enumerate(scope_path)
        if index < 7
    )
    levels = ", ".join(_qi(f"departamento_nivel_{level}") for level in range(1, 8))
    subcat = _qi("servicesubcategory")
    try:
        a_records = await trino_execute(
            f"SELECT {levels}, {subcat} AS subcat, SUM(COALESCE(TRY_CAST({_qi(measure)} AS DOUBLE),0)) AS av "
            f"FROM {actual_source} WHERE {actual_month}{type_filter}{scope_filter} GROUP BY {levels}, {subcat}",
            timeout_s=180,
        )
        f_records = await trino_execute(
            f"SELECT {levels}, {subcat} AS subcat, SUM(TRY_CAST(\"forecast_value\" AS DOUBLE)) AS fv "
            f"FROM iceberg.forecast.{run.result_table} "
            f"WHERE \"forecast_month\" = {_ql(target)} AND \"tipo\" = 'forecast'{scope_filter} GROUP BY {levels}, {subcat}",
            timeout_s=180,
        )
    except Exception as exc:
        raise HTTPException(503, f"Falha ao agregar subcategorias: {exc}")

    def key_of(row: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(row.get(f"departamento_nivel_{level}") for level in range(1, 8)) + (row.get("subcat"),)

    amap = {key_of(r): float(r.get("av") or 0) for r in a_records}
    fmap = {key_of(r): float(r.get("fv") or 0) for r in f_records}
    _desvios_grain_cache[cache_key] = (datetime.utcnow().timestamp(), (fmap, amap))
    return fmap, amap


async def _desvios_subcat(
    run: ForecastRun,
    snapshot: dict[str, Any],
    target: str,
    leaf: list[str],
    scope_path: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Breakdown por ServiceSubcategory de UMA folha, extraído do grão do escopo."""
    scope_path = scope_path or leaf[:-1]
    depth = len(leaf)
    fmap, amap = await _desvios_subcat_grain(run, snapshot, target, scope_path)
    amap_sub: dict[str, float] = {}
    fmap_sub: dict[str, float] = {}
    for (*levels, subcat), value in fmap.items():
        if tuple(leaf) == tuple(str(x or "") for x in levels[:depth]) and all(str(x or "") == "" for x in levels[depth:]):
            fmap_sub[str(subcat or "")] = fmap_sub.get(str(subcat or ""), 0.0) + value
    for (*levels, subcat), value in amap.items():
        if tuple(leaf) == tuple(str(x or "") for x in levels[:depth]) and all(str(x or "") == "" for x in levels[depth:]):
            amap_sub[str(subcat or "")] = amap_sub.get(str(subcat or ""), 0.0) + value
    rows = []
    for sub in set(amap_sub) | set(fmap_sub):
        metrics = _desvio_metrics(fmap_sub.get(sub), amap_sub.get(sub))
        rows.append({"subcategory": sub or "Não classificado", "forecast_value": fmap_sub.get(sub, 0.0), "actual_value": amap_sub.get(sub, 0.0), **{k: metrics[k] for k in ("variance", "variance_pct", "accuracy")}})
    rows.sort(key=lambda r: abs(r.get("variance") or 0), reverse=True)
    return rows


@router.get("/accuracy/desvios")
async def accuracy_desvios(
    run_id: int,
    target_month: str | None = None,
    department: str | None = None,
    direction: Literal["positive", "negative"] | None = None,
    limit: int = Query(5, ge=0, le=10000),
    db: AsyncSession = Depends(get_db),
):
    """Desvios no último nível de cada departamento (folhas) dentro do escopo:
    top 10 positivos (realizado acima do projetado) e top 10 negativos."""
    run = await db.get(ForecastRun, run_id)
    if not run:
        raise HTTPException(404, "Execução não encontrada")
    snapshot = dict(run.config_snapshot or {})
    if not snapshot.get("simulated_as_of"):
        raise HTTPException(422, "Esta execução não é um simulado de acurácia")
    target = target_month or _month_after(date.fromisoformat(str(snapshot["simulated_as_of"])))
    scope_path = [part.strip() for part in (department or "").split(">") if part.strip()]
    departments = (await db.execute(select(Department).where(Department.active == True))).scalars().all()
    leaves = _leaf_paths(departments, scope_path)
    forecast_grain, actual_grain = await _desvios_grain(run, snapshot, target, scope_path)
    items = []
    for leaf in leaves:
        forecast, actual = _leaf_totals(leaf, forecast_grain, actual_grain)
        if forecast == 0 and actual == 0:
            continue
        metrics = _desvio_metrics(forecast, actual)
        items.append({"path": leaf, "name": leaf[-1] if leaf else "", "forecast_value": forecast, "actual_value": actual, **{k: metrics[k] for k in ("variance", "variance_pct", "accuracy", "wape", "bias")}})
    positive = sorted((i for i in items if i["variance"] > 0), key=lambda i: -i["variance"])
    negative = sorted((i for i in items if i["variance"] < 0), key=lambda i: i["variance"])
    if limit:
        positive = positive[:limit]
        negative = negative[:limit]
    if direction == "positive":
        negative = []
    elif direction == "negative":
        positive = []
    return {
        "run_id": run.id,
        "target_month": target,
        "department": department or "",
        "positive": positive,
        "negative": negative,
    }


@router.get("/accuracy/desvios/subcategories")
async def accuracy_desvios_subcategories(
    run_id: int,
    target_month: str | None = None,
    department: str | None = None,
    scope: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Breakdown por ServiceSubcategory do último nível (folha) selecionado.
    ``scope`` (opcional) é o departamento atual da página — o grão é calculado
    uma vez por escopo e todas as folhas dele expandem instantaneamente."""
    run = await db.get(ForecastRun, run_id)
    if not run:
        raise HTTPException(404, "Execução não encontrada")
    snapshot = dict(run.config_snapshot or {})
    if not snapshot.get("simulated_as_of"):
        raise HTTPException(422, "Esta execução não é um simulado de acurácia")
    target = target_month or _month_after(date.fromisoformat(str(snapshot["simulated_as_of"])))
    leaf = [part.strip() for part in (department or "").split(">") if part.strip()]
    if not leaf:
        raise HTTPException(422, "Informe o caminho do último nível (department)")
    scope_path = [part.strip() for part in (scope or "").split(">") if part.strip()]
    rows = await _desvios_subcat(run, snapshot, target, leaf, scope_path)
    return {"run_id": run.id, "target_month": target, "department": department or "", "records": rows}


def _subcategory_filter(subcategory: str) -> str:
    """servicesubcategory match: 'Não classificado' (frontend label) maps to
    NULL/blank values, any other value matches the exact trimmed string."""
    if not subcategory or subcategory == "Não classificado":
        return (
            " AND (COALESCE(TRIM(CAST(\"servicesubcategory\" AS VARCHAR)), '') = ''"
            " OR \"servicesubcategory\" IS NULL)"
        )
    return f" AND TRIM(CAST(\"servicesubcategory\" AS VARCHAR)) = {_ql(subcategory)}"


@router.get("/accuracy/delta/series")
async def accuracy_delta_series(
    run_id: int,
    target_month: str | None = None,
    department: str | None = None,
    subcategory: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Série diária (acumulada) de Projetado × Realizado para UM ServiceSubcategory
    de um departamento folha.  O projetado é o total mensal do snapshot imutável
    distribuído por rate diário uniforme (mesma lógica MONTHLY_LUMP do engine);
    o realizado vem do Gold agregado por dia.  ``deviation_day`` é o primeiro dia
    em que o realizado acumulado ultrapassa o projetado acumulado (o desvio é
    identificado a partir dali) — None quando nunca cruza (redução pura)."""
    run = await db.get(ForecastRun, run_id)
    if not run:
        raise HTTPException(404, "Execução não encontrada")
    snapshot = dict(run.config_snapshot or {})
    if not snapshot.get("simulated_as_of"):
        raise HTTPException(422, "Esta execução não é um simulado de acurácia")
    if not subcategory:
        raise HTTPException(422, "Informe o subcategory")
    target = target_month or _month_after(date.fromisoformat(str(snapshot["simulated_as_of"])))
    leaf = [part.strip() for part in (department or "").split(">") if part.strip()]
    if not leaf:
        raise HTTPException(422, "Informe o departamento (caminho do último nível)")
    year, month = int(target[:4]), int(target[4:6])
    days_in_month = calendar.monthrange(year, month)[1]

    # Dimon is monthly and intentionally used by the summary tables.  The
    # expanded series must instead retain Gold's real charge date so each day
    # is displayed where the resource was actually consumed.
    actual_source, date_expr, measure, type_col, type_filter = _daily_gold_actual_context(snapshot)
    # The selected billing period is the source of truth for inclusion (and
    # allows Iceberg partition pruning). Gold's charge date only positions the
    # included value on its actual day. Some providers legitimately retain a
    # charge date outside the billing period, which must not make its value
    # disappear from the same delta total shown in the expanded row.
    period_col = snapshot.get("period_column")
    actual_month = (
        f"CAST({_qi(period_col)} AS VARCHAR) = {_ql(target)}"
        if period_col else f"date_format({date_expr}, '%Y%m') = {_ql(target)}"
    )
    depth = len(leaf)
    scope_filter = "".join(
        f" AND TRIM({_qi(f'departamento_nivel_{index + 1}')}) = {_ql(name)}"
        for index, name in enumerate(leaf)
        if index < 7
    )
    # A folha não tem filhos: os níveis abaixo do caminho devem estar vazios,
    # igual à semântica de _desvios_subcat.
    deeper_filter = "".join(
        f" AND TRIM(COALESCE(CAST({_qi(f'departamento_nivel_{level}')} AS VARCHAR), '')) = ''"
        for level in range(depth + 1, 8)
    )
    subcat_filter = _subcategory_filter(subcategory)

    try:
        a_records = await trino_execute(
            f"SELECT day({date_expr}) AS d, SUM(COALESCE(TRY_CAST({_qi(measure)} AS DOUBLE),0)) AS av "
            f"FROM {actual_source} WHERE {actual_month}{type_filter}{scope_filter}{deeper_filter}{subcat_filter} "
            f"GROUP BY day({date_expr})",
            timeout_s=180,
        )
        f_records = await trino_execute(
            f"SELECT SUM(TRY_CAST(\"forecast_value\" AS DOUBLE)) AS fv "
            f"FROM iceberg.forecast.{run.result_table} "
            f"WHERE \"forecast_month\" = {_ql(target)} AND \"tipo\" = 'forecast'"
            f"{scope_filter}{deeper_filter}{subcat_filter}",
            timeout_s=180,
        )
    except Exception as exc:
        raise HTTPException(503, f"Falha ao consultar série diária: {exc}")

    actual_by_day = {int(r.get("d") or 0): float(r.get("av") or 0) for r in a_records}
    forecast_total = float(f_records[0].get("fv") or 0) if f_records else 0.0
    actual_total = sum(actual_by_day.values())
    forecast_rate = forecast_total / days_in_month if days_in_month else 0.0

    points = []
    actual_cum = 0.0
    deviation_day: int | None = None   # acumulado cruza o projetado acumulado
    daily_deviation_day: int | None = None  # diário: realizado do dia > rate projetado
    for day in range(1, days_in_month + 1):
        daily_actual = actual_by_day.get(day, 0.0)
        actual_cum += daily_actual
        forecast_cum = forecast_rate * day
        if deviation_day is None and actual_cum > forecast_cum:
            deviation_day = day
        if daily_deviation_day is None and daily_actual > forecast_rate:
            daily_deviation_day = day
        points.append({
            "day": day,
            "actual": daily_actual,
            "actual_cum": round(actual_cum, 4),
            "forecast_rate": round(forecast_rate, 4),
            "forecast_cum": round(forecast_cum, 4),
        })

    metrics = _accuracy_metrics(forecast_total, actual_total)
    return {
        "run_id": run.id,
        "target_month": target,
        "department": department or "",
        "subcategory": subcategory,
        "days_in_month": days_in_month,
        "forecast_total": forecast_total,
        "actual_total": actual_total,
        "variance": metrics["variance"],
        "variance_pct": metrics["variance_pct"],
        "accuracy": metrics["accuracy"],
        "deviation_day": deviation_day,
        "daily_deviation_day": daily_deviation_day,
        "points": points,
    }


class AckIn(BaseModel):
    run_id: int | None = None
    target_month: str | None = None
    department: str
    category: str
    reason: str
    severity: str = "Informação"
    ordem: int | None = None
    registered_at: str | None = None


def _severity_rank(severity: str | None) -> int:
    """Ranking padrão da severidade: Crítico=0, Perigo=1, Informação=2, Sucesso=3."""
    return {
        "crítico": 0, "critico": 0, "perigo": 1, "informação": 2, "informacao": 2, "sucesso": 3,
    }.get(str(severity or "").strip().lower(), 2)


@router.post("/accuracy/acks", status_code=201)
async def create_ack(body: AckIn, db: AsyncSession = Depends(get_db)):
    """Registra o motivo do desvio (Ack) de um departamento.

    O cadastro livre (sem ``run_id``) aceita uma ``registered_at`` (data de
    referência, inclusive futura) e deriva o ``target_month`` dela — o mês
    segue a mesma regra do mês realizado (YYYYMM).  Sem run simulado não há
    snapshot congelado, então projetado/realizado ficam zerados até o mês
    existir."""
    leaf = [part.strip() for part in body.department.split(">") if part.strip()]
    if not leaf:
        raise HTTPException(422, "Informe o departamento (caminho do último nível)")

    registered_date: date | None = None
    if body.registered_at:
        try:
            registered_date = date.fromisoformat(str(body.registered_at)[:10])
        except ValueError:
            raise HTTPException(422, "registered_at deve ser uma data ISO (YYYY-MM-DD)")
        if not body.target_month:
            body.target_month = f"{registered_date.year:04d}{registered_date.month:02d}"

    forecast = 0.0
    actual = 0.0
    metrics = _accuracy_metrics(0.0, 0.0)
    run: ForecastRun | None = None
    if body.run_id is not None:
        run = await db.get(ForecastRun, body.run_id)
        if not run:
            raise HTTPException(404, "Execução não encontrada")
        snapshot = dict(run.config_snapshot or {})
        target = body.target_month or (snapshot.get("simulated_as_of") and _month_after(date.fromisoformat(str(snapshot["simulated_as_of"]))))
        if not target:
            raise HTTPException(422, "target_month obrigatório")
        body.target_month = target
        forecast_grain, actual_grain = await _desvios_grain(run, snapshot, target, leaf)
        forecast, actual = _leaf_totals(leaf, forecast_grain, actual_grain)
        metrics = _desvio_metrics(forecast, actual)

    if not body.target_month:
        raise HTTPException(422, "Informe o target_month ou a registered_at")

    row = ForecastAck(
        run_id=body.run_id, target_month=body.target_month, department_path=leaf,
        department_name=leaf[-1], forecast_value=forecast, actual_value=actual,
        variance=metrics["variance"], accuracy=metrics["accuracy"],
        category=body.category.strip() or "Outro", reason=body.reason.strip(),
        severity=body.severity.strip() or "Informação",
        ordem=body.ordem if body.ordem is not None else _severity_rank(body.severity),
        registered_at=registered_date,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _out(row)


@router.get("/accuracy/acks")
async def list_acks(
    target_month: str | None = None,
    department: str | None = None,
    config_id: int | None = None,
    active: bool | None = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ForecastAck).order_by(ForecastAck.created_at.desc())
    if target_month:
        stmt = stmt.where(ForecastAck.target_month == target_month)
    if active is not None:
        stmt = stmt.where(ForecastAck.active == active)
    rows = (await db.execute(stmt.limit(500))).scalars().all()
    # ``department`` é o escopo (caminho) selecionado: filtra por prefixo para
    # acompanhar o drill-down (diretória inclui os projetos abaixo dela).
    if department:
        scope = [part.strip() for part in department.split(">") if part.strip()]
        if scope:
            rows = [
                row for row in rows
                if list(row.department_path or [])[:len(scope)] == scope
            ]
    items = [_out(r) for r in rows]
    if items:
        history_rows = (await db.execute(
            select(ForecastAckHistory).where(ForecastAckHistory.ack_id.in_([r.id for r in rows])).order_by(ForecastAckHistory.changed_at.asc())
        )).scalars().all()
        history_by_ack: dict[int, list[dict[str, Any]]] = {}
        for h in history_rows:
            history_by_ack.setdefault(h.ack_id, []).append(_out(h))
        for item in items:
            item["history"] = history_by_ack.get(item["id"], [])
    return {"items": items}


class AckUpdateIn(BaseModel):
    category: str
    reason: str
    severity: str | None = None
    ordem: int | None = None
    registered_at: str | None = None


@router.put("/accuracy/acks/{ack_id}")
async def update_ack(ack_id: int, body: AckUpdateIn, db: AsyncSession = Depends(get_db)):
    """Edita o Ack preservando o histórico da versão anterior."""
    row = await db.get(ForecastAck, ack_id)
    if not row:
        raise HTTPException(404, "Ack não encontrado")
    category = body.category.strip() or "Outro"
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(422, "Informe o motivo")
    if row.category != category or row.reason != reason:
        db.add(ForecastAckHistory(ack_id=row.id, category=row.category, reason=row.reason))
        row.category = category
        row.reason = reason
        await db.commit()
        await db.refresh(row)
    if body.severity and body.severity.strip() and row.severity != body.severity.strip():
        row.severity = body.severity.strip()
        if body.ordem is None:
            row.ordem = _severity_rank(body.severity)
        await db.commit()
        await db.refresh(row)
    if body.ordem is not None and row.ordem != body.ordem:
        row.ordem = body.ordem
        await db.commit()
        await db.refresh(row)
    if body.registered_at:
        try:
            registered_date = date.fromisoformat(str(body.registered_at)[:10])
        except ValueError:
            raise HTTPException(422, "registered_at deve ser uma data ISO (YYYY-MM-DD)")
        if row.registered_at != registered_date:
            row.registered_at = registered_date
            await db.commit()
            await db.refresh(row)
    out = _out(row)
    history = (await db.execute(
        select(ForecastAckHistory).where(ForecastAckHistory.ack_id == row.id).order_by(ForecastAckHistory.changed_at.asc())
    )).scalars().all()
    out["history"] = [_out(h) for h in history]
    return out


@router.post("/accuracy/acks/{ack_id}/deactivate")
async def deactivate_ack(ack_id: int, db: AsyncSession = Depends(get_db)):
    """Desativa (soft-delete) um Ack: deixa de aparecer nos widgets/dashboards
    sem perder o registro nem o histórico de edições."""
    row = await db.get(ForecastAck, ack_id)
    if not row:
        raise HTTPException(404, "Ack não encontrado")
    row.active = False
    await db.commit()
    await db.refresh(row)
    return _out(row)


@router.post("/accuracy/acks/{ack_id}/activate")
async def activate_ack(ack_id: int, db: AsyncSession = Depends(get_db)):
    """Reativa um Ack desativado."""
    row = await db.get(ForecastAck, ack_id)
    if not row:
        raise HTTPException(404, "Ack não encontrado")
    row.active = True
    await db.commit()
    await db.refresh(row)
    return _out(row)


@router.delete("/accuracy/acks/{ack_id}", status_code=204)
async def delete_ack(ack_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(ForecastAck, ack_id)
    if not row:
        raise HTTPException(404, "Ack não encontrado")
    await db.delete(row)
    await db.commit()


@router.get("/accuracy/history")
async def accuracy_history(
    config_id: int | None = None,
    department: str | None = None,
    mode: str = "showback",
    db: AsyncSession = Depends(get_db),
):
    """Evolução da acurácia mês a mês (derivada do mesmo cálculo cacheado de /months)."""
    items = await _accuracy_months_items(config_id, db, department=department, mode=mode)
    return {"items": [
        {key: item[key] for key in (
            "run_id", "simulated_as_of", "target_month", "closed",
            "forecast_value", "actual_value", "variance", "variance_pct",
            "wape", "accuracy", "bias",
        ) if key in item}
        for item in items
    ]}


class AccuracyReprocessIn(BaseModel):
    config_id: int
    months: list[str] = []


def _previous_month(month: str) -> str:
    """Return the YYYYMM immediately before a valid YYYYMM value."""
    year, number = int(month[:4]), int(month[4:])
    if number == 1:
        return f"{year - 1:04d}12"
    return f"{year:04d}{number - 1:02d}"


async def _accuracy_eligible_months(config: ForecastConfig, dest: DestinationTable) -> set[str]:
    """Months that have both the actual to compare and the prior-month
    history required by the simulated forecast cutoff (D-2)."""
    safe_name = dest.physical_name.replace('"', '""')
    period_col = config.period_column or "_billing_period"
    type_filter = (
        f"({_qi(config.type_column)} = 'realizado' OR {_qi(config.type_column)} IS NULL)"
        if config.type_column else "TRUE"
    )
    records = await trino_execute(
        f"SELECT DISTINCT CAST({_qi(period_col)} AS VARCHAR) AS period "
        f"FROM iceberg.gold.\"{safe_name}\" "
        f"WHERE {type_filter} AND CAST({_qi(period_col)} AS VARCHAR) <> ''",
        timeout_s=90,
    )
    actual_months = {
        str(record.get("period") or "") for record in records
        if len(str(record.get("period") or "")) == 6
        and str(record.get("period")).isdigit()
        and 1 <= int(str(record.get("period"))[4:]) <= 12
    }
    # A forecast for MM is produced with data available through the end of
    # MM-1.  Without that prior realized month, regenerating it would fail.
    return {month for month in actual_months if _previous_month(month) in actual_months}


@router.get("/accuracy/available-months")
async def accuracy_available_months(config_id: int | None = None, db: AsyncSession = Depends(get_db)):
    """Months eligible for accuracy reprocessing.

    Each month must have realized values to compare and realized data in the
    preceding month, which is the data available at the simulated D-2 cutoff.
    """
    if config_id:
        config = await db.get(ForecastConfig, config_id)
        if not config:
            raise HTTPException(404, "Configuração não encontrada")
        dest = await db.get(DestinationTable, config.destination_table_id) if config.destination_table_id else None
    else:
        config = None
        dest = None
    months: set[str] = set()
    if dest:
        try:
            months = await _accuracy_eligible_months(config, dest)
        except Exception:
            # Do not expose a stale simulated month when Gold cannot confirm
            # the actual and the prior-cutoff history.
            months = set()
    return {"months": sorted(months)}


@router.post("/accuracy/reprocess", status_code=202)
async def accuracy_reprocess(body: AccuracyReprocessIn, db: AsyncSession = Depends(get_db)):
    """Queue a batch that regenerates the simulated forecast snapshots for the
    given months (simulated_as_of = day 2 of each month).  The accuracy
    dashboard then picks the most recent run per month."""
    config = await db.get(ForecastConfig, body.config_id)
    if not config:
        raise HTTPException(404, "Configuração não encontrada")
    months = sorted({str(m) for m in body.months if len(str(m)) == 6 and str(m).isdigit()})
    if not months:
        raise HTTPException(422, "Informe ao menos um mês no formato YYYYMM")
    dest = await db.get(DestinationTable, config.destination_table_id) if config.destination_table_id else None
    if not dest:
        raise HTTPException(422, "Tabela de destino da configuração não encontrada")
    try:
        eligible_months = await _accuracy_eligible_months(config, dest)
    except Exception as exc:
        raise HTTPException(503, f"Não foi possível validar os meses elegíveis na Gold: {exc}")
    unavailable = sorted(set(months) - eligible_months)
    if unavailable:
        raise HTTPException(
            422,
            "Meses sem realizado no próprio mês ou no mês anterior (cutoff do forecast): "
            + ", ".join(unavailable),
        )
    from app.services.worker_runtime import any_worker_healthy

    if not any_worker_healthy():
        raise HTTPException(503, "Worker ETL indisponível; reprocessamento não foi criado")
    active = (await db.execute(
        select(EtlTask.id).where(
            EtlTask.task_type.in_(["forecast_accuracy", "forecast"]),
            EtlTask.destination_table_id == config.id,
            EtlTask.status.in_(["QUEUED", "RUNNING"]),
        ).limit(1)
    )).scalar_one_or_none()
    if active is not None:
        raise HTTPException(409, f"Já existe reprocessamento de acurácia ativo: task {active}")
    now = datetime.now(timezone.utc)
    task = EtlTask(
        task_type="forecast_accuracy", status="QUEUED", destination_table_id=config.id,
        result_json={
            "config_id": config.id, "months": months, "total_months": len(months),
            "event_log": [{"at": now.isoformat(), "level": "INFO",
                           "message": f"Reprocessar acurácia: {len(months)} meses ({months[0]}–{months[-1]})"}],
        },
        total_blobs=len(months), processed_blobs=0, errors=[], phase=f"Na fila: {len(months)} simulado(s)",
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return {"task_id": task.id, "status": task.status, "months": months}

@router.get("/runs/{run_id}")
async def get_run(run_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(ForecastRun, run_id)
    if not row: raise HTTPException(404, "Execução não encontrada")
    return _out(row)

@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(ForecastRun, run_id)
    if not row: raise HTTPException(404, "Execução não encontrada")
    if row.status in {"QUEUED", "RUNNING"}:
        row.status = "CANCELLED"
        row.phase = "Cancelado pelo usuário"
        row.finished_at = datetime.now().astimezone()
        # The worker owns a persisted ETL task for each forecast run. Keep the
        # two state machines aligned so a queued task cannot be picked up after
        # the user has cancelled its run.
        task_result = await db.execute(
            select(EtlTask).where(
                EtlTask.task_type == "forecast",
                EtlTask.result_json["forecast_run_id"].as_integer() == run_id,
                EtlTask.status.in_(["QUEUED", "RUNNING"]),
            )
        )
        task = task_result.scalar_one_or_none()
        if task:
            task.status = "CANCELLED"
            task.phase = "Cancelado pelo usuário"
            task.finished_at = row.finished_at
        await db.commit()
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
