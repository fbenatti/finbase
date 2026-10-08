"""Gold portal — CRUD for pre-aggregated cost gold cubes, preview, and reprocess."""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.gold_mart_rule import GoldMartRule, GoldMartRuleSource, VALUE_COLUMNS
from app.services.gold_materialization import (
    GOLD,
    rule_target_table,
    load_gold_schema,
    load_gold_schema_typed,
    normalize_code,
    preview_source,
    _quote_ident,
    _quote_literal,
)
from app.services.trino_service import execute

logger = logging.getLogger(__name__)


def _raise_gold_query_error(table: str, exc: Exception) -> None:
    """Expose stable errors instead of leaking Trino protocol wording."""
    message = str(exc)
    lowered = message.lower()
    if "metadata not found" in lowered or "metadata location" in lowered:
        raise HTTPException(status_code=503, detail={
            "code": "GOLD_METADATA_MISSING", "table": table,
            "message": "A materialização do cubo Gold está indisponível e precisa ser recuperada.",
        }) from exc
    if "does not exist" in lowered or "not found" in lowered:
        raise HTTPException(status_code=404, detail={
            "code": "GOLD_NOT_FOUND", "table": table,
            "message": "Gold não cadastrado na Gold.",
        }) from exc
    raise HTTPException(status_code=503, detail={
        "code": "ANALYTICS_UNAVAILABLE", "table": table,
        "message": "Consulta analítica indisponível.",
    }) from exc

router = APIRouter(prefix="/gold", tags=["gold"])


class FilterCondition(BaseModel):
    column: str
    operator: str = "IN"
    values: list[str] = []


class GoldSourceIn(BaseModel):
    # One or more Gold tables with the same structure, unioned into one dataset.
    gold_table: str | None = None  # legacy single-table form
    gold_tables: list[str] = Field(default_factory=list)
    filter_json: dict[str, Any] | list[Any] = Field(default_factory=dict)
    value_column: str = "realizado"
    percent: float = 100.0


class GoldSourceOut(BaseModel):
    id: int
    gold_table: str
    gold_tables: list[str] = Field(default_factory=list)
    filter_json: dict[str, Any]
    value_column: str
    percent: float

    model_config = {"from_attributes": True}


class GoldRuleCreate(BaseModel):
    code: str
    name: str
    description: str | None = None
    active: bool = True
    valid_from: str | None = None
    valid_until: str | None = None
    # Pivot-style: GROUP BY dimensions + measures to SUM; empty -> defaults.
    dimensions: list[str] = Field(default_factory=list)
    measure_columns: list[str] = Field(default_factory=list)
    sql_template: str | None = None
    snapshot_mode: bool = False
    snapshot_col: str = "_snapshot_date"
    sources: list[GoldSourceIn] = Field(default_factory=list)


class GoldRuleUpdate(BaseModel):
    code: str | None = None
    name: str | None = None
    description: str | None = None
    active: bool | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    dimensions: list[str] | None = None
    measure_columns: list[str] | None = None
    sql_template: str | None = None
    snapshot_mode: bool | None = None
    snapshot_col: str | None = None
    sources: list[GoldSourceIn] | None = None


class GoldRuleOut(BaseModel):
    id: int
    code: str
    name: str
    description: str | None = None
    active: bool
    valid_from: str | None = None
    valid_until: str | None = None
    dimensions: list[str] = Field(default_factory=list)
    measure_columns: list[str] = Field(default_factory=list)
    sql_template: str | None = None
    snapshot_mode: bool = False
    snapshot_col: str = "_snapshot_date"
    materialization_table: str | None = None
    materialization_status: str | None = None
    materialization_rows: int | None = None
    materialization_run_id: int | None = None
    materialized_at: str | None = None
    materialization_error: str | None = None
    sources: list[GoldSourceOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


def _rule_out(rule: GoldMartRule) -> GoldRuleOut:
    return GoldRuleOut(
        id=rule.id,
        code=rule.code,
        name=rule.name,
        description=getattr(rule, "description", None),
        active=rule.active,
        valid_from=rule.valid_from,
        valid_until=rule.valid_until,
        dimensions=list(getattr(rule, "dimensions", None) or []),
        measure_columns=list(getattr(rule, "measure_columns", None) or []),
        sql_template=getattr(rule, "sql_template", None),
        snapshot_mode=bool(getattr(rule, "snapshot_mode", False)),
        snapshot_col=getattr(rule, "snapshot_col", "_snapshot_date"),
        materialization_table=rule.materialization_table,
        materialization_status=rule.materialization_status,
        materialization_rows=rule.materialization_rows,
        materialization_run_id=rule.materialization_run_id,
        materialized_at=rule.materialized_at.isoformat() if getattr(rule, "materialized_at", None) else None,
        materialization_error=rule.materialization_error,
        sources=[GoldSourceOut.model_validate(source) for source in (rule.sources or [])],
    )


async def _load_rules(db: AsyncSession, rule_ids: list[int] | None = None) -> list[GoldMartRule]:
    stmt = select(GoldMartRule).options(selectinload(GoldMartRule.sources)).order_by(GoldMartRule.id)
    if rule_ids:
        stmt = stmt.where(GoldMartRule.id.in_(rule_ids))
    return list((await db.execute(stmt)).scalars().all())


def _replace_sources(rule: GoldMartRule, sources: list[GoldSourceIn]) -> None:
    rule.sources.clear()
    for item in sources:
        raw = item.filter_json or {}
        if isinstance(raw, list):
            raw = {"conditions": list(raw)}
        gold_tables = [t.strip() for t in (item.gold_tables or []) if t and t.strip()]
        if not gold_tables and item.gold_table and item.gold_table.strip():
            gold_tables = [item.gold_table.strip()]
        if not gold_tables:
            continue
        rule.sources.append(GoldMartRuleSource(
            gold_table=gold_tables[0],
            gold_tables=gold_tables,
            filter_json=raw,
            value_column=item.value_column if item.value_column in VALUE_COLUMNS else "realizado",
            percent=float(item.percent or 100),
        ))


def _validate_sources(sources: list[GoldSourceIn]) -> list[str]:
    """Return human-readable warnings for sources with invalid config."""
    warnings: list[str] = []
    for item in sources:
        golds = [t for t in (item.gold_tables or []) if t and t.strip()]
        if not golds and not (item.gold_table or "").strip():
            warnings.append("Origem sem tabela Gold")
    return warnings


@router.get("/rules", response_model=list[GoldRuleOut])
async def list_rules(db: AsyncSession = Depends(get_db)):
    return [_rule_out(rule) for rule in await _load_rules(db)]


@router.post("/rules", response_model=GoldRuleOut, status_code=201)
async def create_rule(data: GoldRuleCreate, db: AsyncSession = Depends(get_db)):
    code = normalize_code(data.code)
    if not code:
        raise HTTPException(status_code=422, detail="code inválido")
    if not data.name.strip():
        raise HTTPException(status_code=422, detail="name é obrigatório")
    existing = (await db.execute(select(GoldMartRule).where(GoldMartRule.code == code))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Já existe uma regra Gold com o code '{code}'")
    rule = GoldMartRule(
        code=code,
        name=data.name.strip(),
        description=data.description,
        active=data.active,
        valid_from=data.valid_from,
        valid_until=data.valid_until,
        dimensions=list(data.dimensions or []),
        measure_columns=list(data.measure_columns or []),
        sql_template=data.sql_template.strip() if (data.sql_template or "").strip() else None,
        snapshot_mode=data.snapshot_mode,
        snapshot_col=data.snapshot_col or "_snapshot_date",
    )
    _replace_sources(rule, data.sources)
    rule.materialization_status = "PENDING"
    db.add(rule)
    await db.commit()
    rule = (await db.execute(
        select(GoldMartRule).options(selectinload(GoldMartRule.sources)).where(GoldMartRule.id == rule.id)
    )).scalar_one()
    return _rule_out(rule)


@router.get("/rules/{rule_id}", response_model=GoldRuleOut)
async def get_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    rule = (await db.execute(
        select(GoldMartRule).options(selectinload(GoldMartRule.sources)).where(GoldMartRule.id == rule_id)
    )).scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Regra Gold não encontrada")
    return _rule_out(rule)


@router.put("/rules/{rule_id}", response_model=GoldRuleOut)
async def update_rule(rule_id: int, data: GoldRuleUpdate, db: AsyncSession = Depends(get_db)):
    rule = (await db.execute(
        select(GoldMartRule).options(selectinload(GoldMartRule.sources)).where(GoldMartRule.id == rule_id)
    )).scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Regra Gold não encontrada")
    if data.code is not None:
        code = normalize_code(data.code)
        if not code:
            raise HTTPException(status_code=422, detail="code inválido")
        conflict = (await db.execute(
            select(GoldMartRule).where(GoldMartRule.code == code, GoldMartRule.id != rule_id)
        )).scalar_one_or_none()
        if conflict:
            raise HTTPException(status_code=409, detail=f"Já existe uma regra Gold com o code '{code}'")
        rule.code = code
    for field in ("name", "description", "active", "valid_from", "valid_until"):
        value = getattr(data, field)
        if value is not None:
            setattr(rule, field, value.strip() if isinstance(value, str) else value)
    if data.dimensions is not None:
        rule.dimensions = list(data.dimensions)
    if data.measure_columns is not None:
        rule.measure_columns = list(data.measure_columns)
    if data.sql_template is not None:
        rule.sql_template = data.sql_template.strip() if data.sql_template.strip() else None
    if data.snapshot_mode is not None:
        rule.snapshot_mode = data.snapshot_mode
    if data.snapshot_col is not None:
        rule.snapshot_col = data.snapshot_col
    if data.sources is not None:
        _replace_sources(rule, data.sources)
    rule.materialization_status = "PENDING"
    rule.materialization_error = None
    await db.commit()
    rule = (await db.execute(
        select(GoldMartRule).options(selectinload(GoldMartRule.sources)).where(GoldMartRule.id == rule_id)
    )).scalar_one()
    return _rule_out(rule)


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    rule = await db.get(GoldMartRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Regra Gold não encontrada")
    table = rule_target_table(rule.code)
    await db.execute(delete(GoldMartRuleSource).where(GoldMartRuleSource.rule_id == rule_id))
    await db.delete(rule)
    await db.commit()
    # Drop the physical Gold table owned exclusively by this rule.
    import asyncio
    from app.services import iceberg_service as iceberg
    await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: iceberg._force_delete_table(iceberg._table_id(GOLD, table), table),
    )


@router.delete("/tables/{table_name}", status_code=204)
async def delete_table(table_name: str, db: AsyncSession = Depends(get_db)):
    """Drop a physical Gold table (and its rule when one exists for the code)."""
    import asyncio
    from app.services import iceberg_service as iceberg

    if not table_name or "/" in table_name or "\\" in table_name:
        raise HTTPException(status_code=400, detail="Nome de tabela inválido")
    safe = table_name.strip().replace('"', "")
    if not _GOLD_TABLE_RE.fullmatch(safe):
        raise HTTPException(status_code=400, detail="Somente tabelas Gold podem ser removidas")

    # Remove the rule (and its sources) that owns this table, if any.
    code_guess = safe
    if safe.startswith("dest_dm_cost_"):
        code_guess = safe[len("dest_dm_cost_"):]
    rule = (await db.execute(
        select(GoldMartRule).where(GoldMartRule.code == code_guess)
    )).scalar_one_or_none()
    if rule is not None:
        await db.execute(delete(GoldMartRuleSource).where(GoldMartRuleSource.rule_id == rule.id))
        await db.delete(rule)
        await db.commit()
        logger.info("delete_table: removed rule #%s for %s", rule.id, safe)

    # Drop only the physical Gold table. Gold tables are managed by ETL.
    await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: iceberg._force_delete_table(iceberg._table_id(GOLD, safe), safe),
    )


@router.get("/gold-tables")
async def list_gold_tables():
    """Available Gold tables a Gold source can point at."""
    from app.database import SyncSession
    from app.models.destination import DestinationTable
    session = SyncSession()
    try:
        rows = session.execute(
            select(DestinationTable).where(DestinationTable.active == True).order_by(DestinationTable.id)  # noqa: E712
        ).scalars().all()
        items = [{"id": row.id, "name": row.name, "physical_name": row.physical_name} for row in rows]
    finally:
        session.close()
    # Planning pseudo-tables are derived from each Gold (dest_modelo_focus_planning).
    planning = [{"id": -row["id"], "name": f"{row['name']} (planejamento/forecast/rateio)", "physical_name": f"{row['physical_name']}_planning"} for row in items]
    return {"tables": items + planning}


@router.get("/gold-schema")
async def gold_schema(gold_table: str = Query(...)):
    """DESCRIBE a Gold table so the UI can offer valid filter columns."""
    try:
        schema = await load_gold_schema_typed(gold_table)
        return {"gold_table": gold_table, "columns": [{"name": v["name"], "type": v["type"]} for v in schema.values()]}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Tabela Gold não encontrada: {exc}") from exc


class GoldPreviewRequest(BaseModel):
    gold_table: str
    filter_json: dict[str, Any] = Field(default_factory=dict)
    value_column: str = "realizado"


@router.post("/preview")
async def preview(body: GoldPreviewRequest):
    try:
        return await preview_source(
            gold_table=body.gold_table,
            filter_json=body.filter_json,
            value_column=body.value_column,
        )
    except Exception as exc:
        logger.exception("Gold preview failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

class GoldReprocessRequest(BaseModel):
    rule_ids: list[int] = Field(default_factory=list)
    billing_periods: list[str] = Field(default_factory=list)


@router.post("/reprocess")
async def reprocess_gold_cubes(body: GoldReprocessRequest, db: AsyncSession = Depends(get_db)):
    """Queue one independent task per selected Gold rule."""
    from datetime import datetime, timezone
    from app.models.etl_task import EtlTask

    if any(
        not re.fullmatch(r"\d{6}", period)
        or not 1 <= int(period[4:6]) <= 12
        for period in body.billing_periods
    ):
        raise HTTPException(status_code=422, detail="billing_periods deve conter períodos YYYYMM válidos")

    rule_ids = body.rule_ids or None
    rules = await _load_rules(db, rule_ids)
    active = [rule for rule in rules if rule.active]
    if not active:
        raise HTTPException(status_code=422, detail="Nenhuma regra Gold ativa para materializar.")

    from sqlalchemy import select

    running = (
        await db.execute(
            select(EtlTask.id).where(
                EtlTask.task_type == "gold_reprocess",
                EtlTask.status.in_(["QUEUED", "RUNNING"]),
            ).limit(1)
        )
    ).scalar_one_or_none()
    if running is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Já existe materialização Gold ativa (task {running})",
        )

    now = datetime.now(timezone.utc)
    task = EtlTask(task_type="gold_reprocess", status="QUEUED", errors=[])
    period_label = f" ({', '.join(body.billing_periods)})" if body.billing_periods else ""
    task.phase = f"Na fila: materializar {len(active)} cubo(s) Gold{period_label}"
    task.started_at = now
    task.total_blobs = len(active)
    task.result_json = {
        "event_log": [{"at": now.isoformat(), "level": "INFO", "message": task.phase}],
        "rule_ids": [rule.id for rule in active],
        "billing_periods": body.billing_periods,
    }
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return {
        "task_id": task.id,
        "task_ids": [task.id],
        "status": "QUEUED",
        "message": f"Materialização Gold enfileirada ({len(active)} regra(s) em uma tarefa)",
        "rules": len(active),
        "billing_periods": body.billing_periods,
    }


@router.get("/history")
async def gold_history(limit: int = 30, db: AsyncSession = Depends(get_db)):
    from app.models.etl_task import EtlTask
    rows = list((await db.execute(select(EtlTask).where(
        EtlTask.task_type == "gold_reprocess"
    ).order_by(EtlTask.id.desc()).limit(min(max(limit, 1), 100)))).scalars().all())
    return [{
        "id": row.id, "status": row.status, "phase": row.phase, "started_at": row.started_at,
        "finished_at": row.finished_at, "rows_destination": row.rows_destination,
        "result_json": row.result_json or {}, "error": (row.errors or [None])[0],
    } for row in rows]


# ═══════════════════════════════════════════════════════════════════════
#  Leitura da Gold para dashboards (tabelas dest_dm_cost_*)
# ═══════════════════════════════════════════════════════════════════════
import re as _re
from typing import Literal as _Literal

_GOLD_TABLE_RE = _re.compile(r"^dest_dm_[a-z0-9_]+$")
_GOLD_MEASURE_COLUMNS = frozenset({
    "quantity",
    "realizado",
    "realizado_cloud",
    "consumo_realizado",
    "ajuste_contratual",
    "billedcost",
    "effectivecost",
    "consumedquantity",
})
_GOLD_OPERATORS = frozenset({"IN", "NOT_IN", "CONTAINS", "NOT_CONTAINS", "IS_REALIZED"})
# Aliases lógicos → colunas físicas da Gold (ex.: "quantity" da UI = consumedquantity).
_GOLD_COLUMN_ALIASES = {"quantity": "consumedquantity"}


class GoldQueryMeasure(BaseModel):
    column: str = "realizado"
    aggregation: _Literal["SUM", "COUNT"] = "SUM"


class GoldQueryFilter(BaseModel):
    column: str
    operator: str = "IN"
    values: list[str | int | float] = []


class GoldQueryRequest(BaseModel):
    table: str = "dest_dm_cost_visao_completa"
    rows: list[str] = Field(default_factory=list)
    measures: list[GoldQueryMeasure] = Field(default_factory=lambda: [GoldQueryMeasure()])
    filters: list[GoldQueryFilter] = Field(default_factory=list)
    # Grupos alternativos (OR) para caminhos hierárquicos; filtros padrão seguem AND.
    filter_groups: list[list[GoldQueryFilter]] = Field(default_factory=list)
    # Optional breakdown for the date-granularity endpoint; validated against
    # the live Gold schema before interpolation into SQL.
    group_by: str | None = None
    row_limit: int = Field(default=2000, ge=1, le=10000)
    sort_column: str | None = None
    sort_direction: _Literal["asc", "desc"] = "desc"
    day_from: date | None = None
    day_to: date | None = None


class GoldFilterOptionsRequest(BaseModel):
    table: str = "dest_dm_cost_visao_completa"
    column: str
    filters: list[GoldQueryFilter] = Field(default_factory=list)
    filter_groups: list[list[GoldQueryFilter]] = Field(default_factory=list)
    search: str = ""
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=500)


async def _gold_schema(table: str) -> dict[str, str]:
    """DESCRIBE a Gold cube, validating the name against the whitelist."""
    if not _GOLD_TABLE_RE.match(table):
        raise HTTPException(status_code=422, detail=f"Nome de tabela Gold inválido: {table}")
    try:
        rows = await execute(f"DESCRIBE iceberg.{GOLD}.{table}", timeout_s=30)
    except Exception as exc:  # noqa: BLE001
        _raise_gold_query_error(table, exc)
    schema: dict[str, str] = {}
    for row in rows:
        name = str(row.get("Column") or row.get("column_name") or row.get("column") or "")
        if name:
            schema[name.lower()] = name
    if not schema:
        raise HTTPException(status_code=422, detail=f"Tabela Gold vazia/inexistente: {table}")
    return schema


def _gold_resolve(schema: dict[str, str], name: str) -> str | None:
    key = str(name or "").lower()
    if key in schema:
        return schema[key]
    # Aliases lógicos → colunas físicas (ex.: quantity → consumedquantity).
    alias = _GOLD_COLUMN_ALIASES.get(key)
    if alias and alias in schema:
        return schema[alias]
    return None


def _gold_filter_clause(schema: dict[str, str], filters: list[GoldQueryFilter]) -> str:
    clauses: list[str] = []
    for item in filters or []:
        physical = _gold_resolve(schema, item.column)
        if not physical:
            raise HTTPException(status_code=422, detail=f"Coluna de filtro inexistente na Gold: {item.column}")
        operator = str(item.operator or "IN").upper()
        if operator not in _GOLD_OPERATORS:
            raise HTTPException(status_code=422, detail=f"Operador inválido: {operator}")
        values = [str(v) for v in (item.values or []) if v is not None and str(v).strip() != ""]
        col_sql = f"lower(CAST({_quote_ident(physical)} AS VARCHAR))"
        if operator == "IN" and values:
            literals = ", ".join(_quote_literal(v.lower()) for v in values)
            clauses.append(f"({col_sql} IN ({literals}))")
        elif operator == "NOT_IN" and values:
            literals = ", ".join(_quote_literal(v.lower()) for v in values)
            clauses.append(f"({col_sql} NOT IN ({literals}) OR {_quote_ident(physical)} IS NULL)")
        elif operator == "CONTAINS" and values:
            likes = " OR ".join(f"{col_sql} LIKE {_quote_literal('%' + v.lower() + '%')}" for v in values)
            clauses.append(f"({likes})")
        elif operator == "NOT_CONTAINS" and values:
            likes = " AND ".join(f"{col_sql} NOT LIKE {_quote_literal('%' + v.lower() + '%')}" for v in values)
            clauses.append(f"({likes})")
        elif operator == "IS_REALIZED":
            # A Gold histórica possui linhas de realizado sem ``tipo`` ou
            # com a coluna vazia. Esta é a mesma semântica usada pela Visão
            # Executiva; não reduza realizado a somente tipo='realizado'.
            clauses.append(
                f"({_quote_ident(physical)} IS NULL OR {col_sql} IN ('realizado', ''))"
            )
        elif operator == "IN":
            clauses.append(f"({_quote_ident(physical)} IS NULL OR TRIM(CAST({_quote_ident(physical)} AS VARCHAR)) = '')")
    return " AND ".join(clauses) if clauses else "1=1"


@router.post("/query")
async def query_gold_cubes(body: GoldQueryRequest):
    """Aggregate query over one Gold cube (dest_dm_cost_*) for dashboards.

    Only the whitelisted Gold measures (quantity, realizado, billedcost) can
    be aggregated; rows must resolve to real Gold dimension columns.
    """
    schema = await _gold_schema(body.table)

    rows_physical: list[str] = []
    for name in body.rows or []:
        physical = _gold_resolve(schema, name)
        if not physical:
            raise HTTPException(status_code=422, detail=f"Coluna de linha inexistente na Gold: {name}")
        if physical not in rows_physical:
            rows_physical.append(physical)

    measures: list[tuple[str, str]] = []
    for measure in body.measures or []:
        aggregation = str(measure.aggregation or "SUM").upper()
        if aggregation == "COUNT":
            measures.append(("COUNT(*)", "count"))
            continue
        physical = _gold_resolve(schema, measure.column)
        if not physical or measure.column.lower() not in _GOLD_MEASURE_COLUMNS:
            raise HTTPException(status_code=422, detail=f"Medida não permitida na Gold: {measure.column}")
        label = str(measure.column).lower()
        measures.append((f"SUM(COALESCE(TRY_CAST({_quote_ident(physical)} AS DOUBLE), 0))", label))

    if not measures:
        measures = [("COUNT(*)", "count")]

    select_parts = [f"{_quote_ident(p)}" for p in rows_physical]
    select_parts.extend(f"{expr} AS {_quote_ident(label)}" for expr, label in measures)
    group_by = ", ".join(_quote_ident(p) for p in rows_physical)
    where = _gold_filter_clause(schema, body.filters)
    if body.filter_groups:
        group_clauses = [
            _gold_filter_clause(schema, group)
            for group in body.filter_groups
            if group
        ]
        if group_clauses:
            where = f"({where}) AND ({' OR '.join(f'({clause})' for clause in group_clauses)})"

    sort_col = None
    if body.sort_column:
        sort_col = _gold_resolve(schema, body.sort_column)
        if not sort_col:
            sort_col = body.sort_column.lower()
    else:
        # Default: order by the first measure (or count) descending.
        first_label = measures[0][1]
        sort_col = first_label
    direction = "ASC" if str(body.sort_direction or "desc").lower() == "asc" else "DESC"

    sql = (
        f"SELECT {', '.join(select_parts)} FROM iceberg.{GOLD}.{body.table} "
        f"WHERE {where}"
    )
    if group_by:
        sql += f" GROUP BY {group_by}"
    sql += f" ORDER BY {_quote_ident(sort_col)} {direction} LIMIT {int(body.row_limit)}"

    try:
        rows = await execute(sql, timeout_s=180)
    except Exception as exc:  # noqa: BLE001
        _raise_gold_query_error(body.table, exc)
    columns = [*[p.lower() for p in rows_physical], *[label for _, label in measures]]
    return {"table": body.table, "columns": columns, "rows": rows, "row_count": len(rows)}


@router.post("/filter-options")
async def query_gold_filter_options(body: GoldFilterOptionsRequest):
    """Search distinct dimension values in Trino with bounded pagination."""
    schema = await _gold_schema(body.table)
    physical = _gold_resolve(schema, body.column)
    if not physical:
        raise HTTPException(status_code=422, detail=f"Coluna de filtro inexistente na Gold: {body.column}")
    where = _gold_filter_clause(schema, body.filters)
    if body.filter_groups:
        groups = [_gold_filter_clause(schema, group) for group in body.filter_groups if group]
        if groups:
            where = f"({where}) AND ({' OR '.join(f'({group})' for group in groups)})"
    if body.search.strip():
        where += f" AND lower(CAST({_quote_ident(physical)} AS VARCHAR)) LIKE {_quote_literal('%' + body.search.strip().lower() + '%')}"
    sql = (
        f"SELECT DISTINCT CAST({_quote_ident(physical)} AS VARCHAR) AS value "
        f"FROM iceberg.{GOLD}.{body.table} WHERE ({where}) AND {_quote_ident(physical)} IS NOT NULL "
        f"ORDER BY value LIMIT {int(body.limit)} OFFSET {int(body.offset)}"
    )
    try:
        result = await execute(sql, timeout_s=120)
    except Exception as exc:  # noqa: BLE001
        _raise_gold_query_error(body.table, exc)
    values = [str(row.get("value")) for row in result if row.get("value") is not None]
    return {"table": body.table, "column": physical, "values": values, "offset": body.offset, "limit": body.limit, "has_more": len(values) == body.limit}


@router.post("/daily-consumption")
async def query_gold_daily_consumption(body: GoldQueryRequest):
    """Return realized consumption by its source date, reading the Silver detail.

    Executive Gold cubes are monthly aggregates and cannot supply this series.
    The current executive cube is materialized from ``silver.dest_modelo_focus``;
    query that detail table with the same dimensional scope and billing-period
    filters, then group by the original FOCUS charge date.
    """
    from app.services.forecast_service import gold_date_expr

    # Keep the source selection server-owned: the request only names a validated
    # Gold cube, never an arbitrary Silver table.
    silver_sources = {"dest_dm_cost_visao_completa": "dest_modelo_focus"}
    silver_table = silver_sources.get(body.table)
    if not silver_table:
        raise HTTPException(status_code=422, detail=f"Série diária indisponível para o cubo: {body.table}")
    try:
        described = await execute(f"DESCRIBE iceberg.silver.{silver_table}", timeout_s=30)
    except Exception as exc:  # noqa: BLE001
        _raise_gold_query_error(body.table, exc)
    schema: dict[str, str] = {}
    for row in described:
        name = str(row.get("Column") or row.get("column_name") or row.get("column") or "")
        if name:
            schema[name.lower()] = name
    if not schema:
        raise HTTPException(status_code=503, detail=f"Tabela Silver indisponível: {silver_table}")
    # ChargePeriodStart is the same real date used by the forecast-accuracy
    # daily drill; prefer it over billing-period end or derived month fields.
    date_col = next((schema[name] for name in (
        "chargeperiodstart", "chargedate", "date", "billingperiodstart",
        "usage_date", "usagedate", "chargeperiodend", "billingperiodend",
    ) if name in schema), None)
    if not date_col:
        return {"table": body.table, "available": False, "day_column": None, "rows": [], "row_count": 0}
    value_col = schema.get("realizado") or schema.get("billedcost")
    if not value_col:
        return {"table": body.table, "available": False, "day_column": date_col, "rows": [], "row_count": 0}
    period_col = schema.get("_billing_period")
    day_expr = gold_date_expr(date_col, period_col)
    group_by_physical = _gold_resolve(schema, body.group_by) if body.group_by else None
    if body.group_by and not group_by_physical:
        raise HTTPException(status_code=422, detail=f"Coluna de agrupamento inexistente na Gold: {body.group_by}")
    where = _gold_filter_clause(schema, body.filters)
    if body.day_from and body.day_to:
        if body.day_from > body.day_to:
            raise HTTPException(status_code=422, detail="Data inicial deve ser anterior ou igual à data final")
        effective_from = max(body.day_from, body.day_to - timedelta(days=89))
        where = f"({where}) AND CAST({day_expr} AS DATE) BETWEEN DATE '{effective_from.isoformat()}' AND DATE '{body.day_to.isoformat()}'"
    if body.filter_groups:
        group_clauses = [_gold_filter_clause(schema, group) for group in body.filter_groups if group]
        if group_clauses:
            where = f"({where}) AND ({' OR '.join(f'({clause})' for clause in group_clauses)})"
    tipo = schema.get("tipo")
    if tipo:
        where = f"({where}) AND ({_gold_filter_clause(schema, [GoldQueryFilter(column=tipo, operator='IS_REALIZED')])})"
    group_select = f", {_quote_ident(group_by_physical)} AS {_quote_ident(group_by_physical.lower())}" if group_by_physical else ""
    group_by_sql = "GROUP BY 1, 2" if group_by_physical else "GROUP BY 1"
    sql = (
        f"SELECT CAST({day_expr} AS VARCHAR) AS day{group_select}, "
        f"SUM(COALESCE(TRY_CAST({_quote_ident(value_col)} AS DOUBLE), 0)) AS realizado "
        f"FROM iceberg.silver.{silver_table} WHERE ({where}) AND {day_expr} IS NOT NULL "
        f"{group_by_sql} ORDER BY 1 LIMIT 10000"
    )
    try:
        result = await execute(sql, timeout_s=180)
    except Exception as exc:  # noqa: BLE001
        _raise_gold_query_error(body.table, exc)
    return {"table": body.table, "source": f"silver.{silver_table}", "available": True, "day_column": date_col, "rows": result, "row_count": len(result)}


@router.get("/info")
async def gold_info(table: str = "dest_dm_cost_visao_completa"):
    """Metadata + coverage of one Gold table: schema, rows, periods."""
    schema = await _gold_schema(table)
    try:
        overview = await execute(
            f"SELECT COUNT(*) AS row_count, COUNT(DISTINCT {_quote_ident(schema['_billing_period'])}) AS period_count "
            f"FROM iceberg.{GOLD}.{table}",
            timeout_s=120,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_gold_query_error(table, exc)
    row = overview[0] if overview else {}
    try:
        periods_rows = await execute(
            f"SELECT DISTINCT {_quote_ident(schema['_billing_period'])} AS period "
            f"FROM iceberg.{GOLD}.{table} ORDER BY period DESC LIMIT 120",
            timeout_s=60,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_gold_query_error(table, exc)
    return {
        "table": table,
        "row_count": int(row.get("row_count") or 0),
        "period_count": int(row.get("period_count") or 0),
        "periods": [str(r.get("period")) for r in periods_rows if r.get("period") is not None],
        "columns": [{"name": physical, "type": "dimension" if physical.lower() not in _GOLD_MEASURE_COLUMNS else "measure"} for physical in schema.values()],
        "dimensions": [physical for physical in schema.values() if physical.lower() not in _GOLD_MEASURE_COLUMNS],
        "measures": [physical for physical in schema.values() if physical.lower() in _GOLD_MEASURE_COLUMNS],
    }
