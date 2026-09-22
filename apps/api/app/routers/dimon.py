"""Dimon portal — CRUD for pre-aggregated cost marts, preview, and reprocess."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.dimon_rule import DimonRule, DimonRuleSource, VALUE_COLUMNS
from app.services.dimon_materialization import (
    dimon_table_name,
    load_gold_schema,
    load_gold_schema_typed,
    normalize_code,
    preview_source,
    _quote_ident,
    _quote_literal,
)
from app.services.trino_service import execute

logger = logging.getLogger(__name__)


def _raise_dimon_query_error(table: str, exc: Exception) -> None:
    """Expose stable errors instead of leaking Trino protocol wording."""
    message = str(exc)
    lowered = message.lower()
    if "metadata not found" in lowered or "metadata location" in lowered:
        raise HTTPException(status_code=503, detail={
            "code": "DIMON_METADATA_MISSING", "table": table,
            "message": "A materialização Dimon está indisponível e precisa ser recuperada.",
        }) from exc
    if "does not exist" in lowered or "not found" in lowered:
        raise HTTPException(status_code=404, detail={
            "code": "DIMON_NOT_FOUND", "table": table,
            "message": "Tabela Dimon não cadastrada.",
        }) from exc
    raise HTTPException(status_code=503, detail={
        "code": "ANALYTICS_UNAVAILABLE", "table": table,
        "message": "Consulta analítica indisponível.",
    }) from exc

router = APIRouter(prefix="/dimon", tags=["dimon"])


class FilterCondition(BaseModel):
    column: str
    operator: str = "IN"
    values: list[str] = []


class DimonSourceIn(BaseModel):
    # One or more Gold tables with the same structure, unioned into one dataset.
    gold_table: str | None = None  # legacy single-table form
    gold_tables: list[str] = Field(default_factory=list)
    filter_json: dict[str, Any] | list[Any] = Field(default_factory=dict)
    value_column: str = "realizado"
    percent: float = 100.0


class DimonSourceOut(BaseModel):
    id: int
    gold_table: str
    gold_tables: list[str] = Field(default_factory=list)
    filter_json: dict[str, Any]
    value_column: str
    percent: float

    model_config = {"from_attributes": True}


class DimonRuleCreate(BaseModel):
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
    sources: list[DimonSourceIn] = Field(default_factory=list)


class DimonRuleUpdate(BaseModel):
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
    sources: list[DimonSourceIn] | None = None


class DimonRuleOut(BaseModel):
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
    sources: list[DimonSourceOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


def _rule_out(rule: DimonRule) -> DimonRuleOut:
    return DimonRuleOut(
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
        sources=[DimonSourceOut.model_validate(source) for source in (rule.sources or [])],
    )


async def _load_rules(db: AsyncSession, rule_ids: list[int] | None = None) -> list[DimonRule]:
    stmt = select(DimonRule).options(selectinload(DimonRule.sources)).order_by(DimonRule.id)
    if rule_ids:
        stmt = stmt.where(DimonRule.id.in_(rule_ids))
    return list((await db.execute(stmt)).scalars().all())


def _replace_sources(rule: DimonRule, sources: list[DimonSourceIn]) -> None:
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
        rule.sources.append(DimonRuleSource(
            gold_table=gold_tables[0],
            gold_tables=gold_tables,
            filter_json=raw,
            value_column=item.value_column if item.value_column in VALUE_COLUMNS else "realizado",
            percent=float(item.percent or 100),
        ))


def _validate_sources(sources: list[DimonSourceIn]) -> list[str]:
    """Return human-readable warnings for sources with invalid config."""
    warnings: list[str] = []
    for item in sources:
        golds = [t for t in (item.gold_tables or []) if t and t.strip()]
        if not golds and not (item.gold_table or "").strip():
            warnings.append("Origem sem tabela Gold")
    return warnings


@router.get("/rules", response_model=list[DimonRuleOut])
async def list_rules(db: AsyncSession = Depends(get_db)):
    return [_rule_out(rule) for rule in await _load_rules(db)]


@router.post("/rules", response_model=DimonRuleOut, status_code=201)
async def create_rule(data: DimonRuleCreate, db: AsyncSession = Depends(get_db)):
    code = normalize_code(data.code)
    if not code:
        raise HTTPException(status_code=422, detail="code inválido")
    if not data.name.strip():
        raise HTTPException(status_code=422, detail="name é obrigatório")
    existing = (await db.execute(select(DimonRule).where(DimonRule.code == code))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Já existe uma regra Dimon com o code '{code}'")
    rule = DimonRule(
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
        select(DimonRule).options(selectinload(DimonRule.sources)).where(DimonRule.id == rule.id)
    )).scalar_one()
    return _rule_out(rule)


@router.get("/rules/{rule_id}", response_model=DimonRuleOut)
async def get_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    rule = (await db.execute(
        select(DimonRule).options(selectinload(DimonRule.sources)).where(DimonRule.id == rule_id)
    )).scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Regra Dimon não encontrada")
    return _rule_out(rule)


@router.put("/rules/{rule_id}", response_model=DimonRuleOut)
async def update_rule(rule_id: int, data: DimonRuleUpdate, db: AsyncSession = Depends(get_db)):
    rule = (await db.execute(
        select(DimonRule).options(selectinload(DimonRule.sources)).where(DimonRule.id == rule_id)
    )).scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Regra Dimon não encontrada")
    if data.code is not None:
        code = normalize_code(data.code)
        if not code:
            raise HTTPException(status_code=422, detail="code inválido")
        conflict = (await db.execute(
            select(DimonRule).where(DimonRule.code == code, DimonRule.id != rule_id)
        )).scalar_one_or_none()
        if conflict:
            raise HTTPException(status_code=409, detail=f"Já existe uma regra Dimon com o code '{code}'")
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
        select(DimonRule).options(selectinload(DimonRule.sources)).where(DimonRule.id == rule_id)
    )).scalar_one()
    return _rule_out(rule)


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    rule = await db.get(DimonRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Regra Dimon não encontrada")
    table = dimon_table_name(rule.code)
    await db.execute(delete(DimonRuleSource).where(DimonRuleSource.rule_id == rule_id))
    await db.delete(rule)
    await db.commit()
    # Drop the physical Gold owned exclusively by this code.
    import asyncio
    from app.services import iceberg_service as iceberg
    await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: iceberg._force_delete_table(iceberg._table_id(DIMON_NAMESPACE, table), table),
    )


@router.delete("/tables/{table_name}", status_code=204)
async def delete_table(table_name: str, db: AsyncSession = Depends(get_db)):
    """Drop a physical Dimon table (and its rule when one exists for the code)."""
    import asyncio
    from app.services import iceberg_service as iceberg

    if not table_name or "/" in table_name or "\\" in table_name:
        raise HTTPException(status_code=400, detail="Nome de tabela inválido")
    safe = table_name.strip().replace('"', "")

    # Remove the rule (and its sources) that owns this table, if any.
    code_guess = safe
    if safe.startswith("dest_dm_cost_"):
        code_guess = safe[len("dest_dm_cost_"):]
    rule = (await db.execute(
        select(DimonRule).where(DimonRule.code == code_guess)
    )).scalar_one_or_none()
    if rule is not None:
        await db.execute(delete(DimonRuleSource).where(DimonRuleSource.rule_id == rule.id))
        await db.delete(rule)
        await db.commit()
        logger.info("delete_table: removed rule #%s for %s", rule.id, safe)

    # Drop the physical Iceberg table.
    await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: iceberg._force_delete_table(iceberg._table_id(DIMON_NAMESPACE, safe), safe),
    )


@router.get("/gold-tables")
async def list_gold_tables():
    """Available Gold tables a Dimon source can point at."""
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


class DimonPreviewRequest(BaseModel):
    gold_table: str
    filter_json: dict[str, Any] = Field(default_factory=dict)
    value_column: str = "realizado"


@router.post("/preview")
async def preview(body: DimonPreviewRequest):
    try:
        return await preview_source(
            gold_table=body.gold_table,
            filter_json=body.filter_json,
            value_column=body.value_column,
        )
    except Exception as exc:
        logger.exception("Dimon preview failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

class DimonReprocessRequest(BaseModel):
    rule_ids: list[int] = Field(default_factory=list)


@router.post("/reprocess")
async def reprocess_dimons(body: DimonReprocessRequest, db: AsyncSession = Depends(get_db)):
    """Queue one independent task per selected Dimon rule."""
    from datetime import datetime, timezone
    from app.models.etl_task import EtlTask

    rule_ids = body.rule_ids or None
    rules = await _load_rules(db, rule_ids)
    active = [rule for rule in rules if rule.active]
    if not active:
        raise HTTPException(status_code=422, detail="Nenhuma regra Dimon ativa para materializar.")

    now = datetime.now(timezone.utc)
    tasks = []
    for rule in active:
        task = EtlTask(task_type="dimon_reprocess", status="QUEUED", errors=[])
        task.phase = f"Na fila: materializar {rule.code}"
        task.started_at = now
        task.total_blobs = 1
        task.result_json = {"event_log": [{"at": now.isoformat(), "level": "INFO", "message": task.phase}], "rule_ids": [rule.id]}
        db.add(task)
        tasks.append(task)
    await db.commit()
    return {"task_id": tasks[0].id, "task_ids": [task.id for task in tasks], "status": "QUEUED", "message": f"{len(tasks)} Gold mart(s) colocada(s) em fila individual", "rules": len(tasks)}


@router.get("/history")
async def dimon_history(limit: int = 30, db: AsyncSession = Depends(get_db)):
    from app.models.etl_task import EtlTask
    rows = list((await db.execute(select(EtlTask).where(
        EtlTask.task_type == "dimon_reprocess"
    ).order_by(EtlTask.id.desc()).limit(min(max(limit, 1), 100)))).scalars().all())
    return [{
        "id": row.id, "status": row.status, "phase": row.phase, "started_at": row.started_at,
        "finished_at": row.finished_at, "rows_destination": row.rows_destination,
        "result_json": row.result_json or {}, "error": (row.errors or [None])[0],
    } for row in rows]


# ═══════════════════════════════════════════════════════════════════════
#  Leitura da Dimon para dashboards (tabelas dest_dm_cost_*)
# ═══════════════════════════════════════════════════════════════════════
import re as _re
from typing import Literal as _Literal

_DIMON_TABLE_RE = _re.compile(r"^dest_dm_[a-z0-9_]+$")
_DIMON_MEASURE_COLUMNS = frozenset({"quantity", "realizado", "billedcost", "effectivecost", "consumedquantity"})
_DIMON_OPERATORS = frozenset({"IN", "NOT_IN", "CONTAINS", "NOT_CONTAINS", "IS_REALIZED"})
# Aliases lógicos → colunas físicas da Dimon (ex.: "quantity" da UI = consumedquantity).
_DIMON_COLUMN_ALIASES = {"quantity": "consumedquantity"}


class DimonQueryMeasure(BaseModel):
    column: str = "realizado"
    aggregation: _Literal["SUM", "COUNT"] = "SUM"


class DimonQueryFilter(BaseModel):
    column: str
    operator: str = "IN"
    values: list[str | int | float] = []


class DimonQueryRequest(BaseModel):
    table: str = "dest_dm_cost_visao_completa"
    rows: list[str] = Field(default_factory=list)
    measures: list[DimonQueryMeasure] = Field(default_factory=lambda: [DimonQueryMeasure()])
    filters: list[DimonQueryFilter] = Field(default_factory=list)
    row_limit: int = Field(default=2000, ge=1, le=10000)
    sort_column: str | None = None
    sort_direction: _Literal["asc", "desc"] = "desc"


async def _dimon_schema(table: str) -> dict[str, str]:
    """DESCRIBE a Dimon table, validating the name against the whitelist."""
    if not _DIMON_TABLE_RE.match(table):
        raise HTTPException(status_code=422, detail=f"Nome de tabela Dimon inválido: {table}")
    try:
        rows = await execute(f"DESCRIBE iceberg.{DIMON_NAMESPACE}.{table}", timeout_s=30)
    except Exception as exc:  # noqa: BLE001
        _raise_dimon_query_error(table, exc)
    schema: dict[str, str] = {}
    for row in rows:
        name = str(row.get("Column") or row.get("column_name") or row.get("column") or "")
        if name:
            schema[name.lower()] = name
    if not schema:
        raise HTTPException(status_code=422, detail=f"Tabela Dimon vazia/inexistente: {table}")
    return schema


def _dimon_resolve(schema: dict[str, str], name: str) -> str | None:
    key = str(name or "").lower()
    if key in schema:
        return schema[key]
    # Aliases lógicos → colunas físicas (ex.: quantity → consumedquantity).
    alias = _DIMON_COLUMN_ALIASES.get(key)
    if alias and alias in schema:
        return schema[alias]
    return None


def _dimon_filter_clause(schema: dict[str, str], filters: list[DimonQueryFilter]) -> str:
    clauses: list[str] = []
    for item in filters or []:
        physical = _dimon_resolve(schema, item.column)
        if not physical:
            raise HTTPException(status_code=422, detail=f"Coluna de filtro inexistente na Dimon: {item.column}")
        operator = str(item.operator or "IN").upper()
        if operator not in _DIMON_OPERATORS:
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
            # A Dimon histórica possui linhas de realizado sem ``tipo`` ou
            # com a coluna vazia. Esta é a mesma semântica usada pela Visão
            # Executiva; não reduza realizado a somente tipo='realizado'.
            clauses.append(
                f"({_quote_ident(physical)} IS NULL OR {col_sql} IN ('realizado', ''))"
            )
        elif operator == "IN":
            clauses.append(f"({_quote_ident(physical)} IS NULL OR TRIM(CAST({_quote_ident(physical)} AS VARCHAR)) = '')")
    return " AND ".join(clauses) if clauses else "1=1"


@router.post("/query")
async def query_dimons(body: DimonQueryRequest):
    """Aggregate query over one Dimon table (dest_dm_cost_*) for dashboards.

    Only the whitelisted Dimon measures (quantity, realizado, billedcost) can
    be aggregated; rows must resolve to real Dimon dimension columns.
    """
    schema = await _dimon_schema(body.table)

    rows_physical: list[str] = []
    for name in body.rows or []:
        physical = _dimon_resolve(schema, name)
        if not physical:
            raise HTTPException(status_code=422, detail=f"Coluna de linha inexistente na Dimon: {name}")
        if physical not in rows_physical:
            rows_physical.append(physical)

    measures: list[tuple[str, str]] = []
    for measure in body.measures or []:
        aggregation = str(measure.aggregation or "SUM").upper()
        if aggregation == "COUNT":
            measures.append(("COUNT(*)", "count"))
            continue
        physical = _dimon_resolve(schema, measure.column)
        if not physical or measure.column.lower() not in _DIMON_MEASURE_COLUMNS:
            raise HTTPException(status_code=422, detail=f"Medida não permitida na Dimon: {measure.column}")
        label = str(measure.column).lower()
        measures.append((f"SUM(COALESCE(TRY_CAST({_quote_ident(physical)} AS DOUBLE), 0))", label))

    if not measures:
        measures = [("COUNT(*)", "count")]

    select_parts = [f"{_quote_ident(p)}" for p in rows_physical]
    select_parts.extend(f"{expr} AS {_quote_ident(label)}" for expr, label in measures)
    group_by = ", ".join(_quote_ident(p) for p in rows_physical)
    where = _dimon_filter_clause(schema, body.filters)

    sort_col = None
    if body.sort_column:
        sort_col = _dimon_resolve(schema, body.sort_column)
        if not sort_col:
            sort_col = body.sort_column.lower()
    else:
        # Default: order by the first measure (or count) descending.
        first_label = measures[0][1]
        sort_col = first_label
    direction = "ASC" if str(body.sort_direction or "desc").lower() == "asc" else "DESC"

    sql = (
        f"SELECT {', '.join(select_parts)} FROM iceberg.{DIMON_NAMESPACE}.{body.table} "
        f"WHERE {where}"
    )
    if group_by:
        sql += f" GROUP BY {group_by}"
    sql += f" ORDER BY {_quote_ident(sort_col)} {direction} LIMIT {int(body.row_limit)}"

    try:
        rows = await execute(sql, timeout_s=180)
    except Exception as exc:  # noqa: BLE001
        _raise_dimon_query_error(body.table, exc)
    columns = [*[p.lower() for p in rows_physical], *[label for _, label in measures]]
    return {"table": body.table, "columns": columns, "rows": rows, "row_count": len(rows)}


@router.get("/info")
async def dimon_info(table: str = "dest_dm_cost_visao_completa"):
    """Metadata + coverage of one Dimon table: schema, rows, periods."""
    schema = await _dimon_schema(table)
    try:
        overview = await execute(
            f"SELECT COUNT(*) AS row_count, COUNT(DISTINCT {_quote_ident(schema['_billing_period'])}) AS period_count "
            f"FROM iceberg.{DIMON_NAMESPACE}.{table}",
            timeout_s=120,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_dimon_query_error(table, exc)
    row = overview[0] if overview else {}
    try:
        periods_rows = await execute(
            f"SELECT DISTINCT {_quote_ident(schema['_billing_period'])} AS period "
            f"FROM iceberg.{DIMON_NAMESPACE}.{table} ORDER BY period DESC LIMIT 120",
            timeout_s=60,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_dimon_query_error(table, exc)
    return {
        "table": table,
        "row_count": int(row.get("row_count") or 0),
        "period_count": int(row.get("period_count") or 0),
        "periods": [str(r.get("period")) for r in periods_rows if r.get("period") is not None],
        "columns": [{"name": physical, "type": "dimension" if physical.lower() not in _DIMON_MEASURE_COLUMNS else "measure"} for physical in schema.values()],
        "dimensions": [physical for physical in schema.values() if physical.lower() not in _DIMON_MEASURE_COLUMNS],
        "measures": [physical for physical in schema.values() if physical.lower() in _DIMON_MEASURE_COLUMNS],
    }
