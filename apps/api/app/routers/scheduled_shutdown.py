"""Scheduled Shutdown — saving por desligue programado de VMs Azure.

Este módulo implementa:
1. Setup da fonte Azure Automation (destination table + connection + mappings).
2. Análise de saving cruzando billing FOCUS com jobs de start/stop.
3. CRUD de regras de exceção de desligue.
"""
from __future__ import annotations

import calendar
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.destination import DestinationTable, DestinationColumn, SourceDestinationMapping
from app.models.source_connection import SourceConnection
from app.models.shutdown_exception_rule import ShutdownExceptionRule
from app.services.trino_service import execute
from app.services import lakehouse_layers as layers
from app.services.gold_materialization import MART_NAMESPACE
from app.services.gold_materialization import (
    _baseline_hours,
    _desligue_validation_dates,
    _desligue_window_dates,
    desligue_list_filter_sql,
    evaluate_daily_quality,
    evaluate_vm_lifecycle,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scheduled-shutdown", tags=["scheduled-shutdown"])

CONNECTION_CODE = "azure-automation"
CONNECTION_NAME = "Azure Automation — Jobs de Start/Stop"
CONNECTOR_TYPE = "azure_automation_jobs"
CLOUD_SCOPE = "azure"
DESTINATION_NAME = "MODELO AUTOMATION"
DESTINATION_PHYSICAL = "dest_modelo_automation"
SCHEDULE_DESTINATION_NAME = "MODELO AUTOMATION SCHEDULE"
SCHEDULE_DESTINATION_PHYSICAL = "dest_modelo_automation_schedule"

SCHEDULE_COLUMNS: list[dict[str, Any]] = [
    {"column_name": "SnapshotDate", "data_type": "DATE", "is_required": True, "position": 10},
    {"column_name": "ScheduleName", "data_type": "TEXT", "is_required": True, "position": 20},
    {"column_name": "ScheduleRunbook", "data_type": "TEXT", "is_required": True, "position": 30},
    {"column_name": "ResourceId", "data_type": "TEXT", "is_required": True, "position": 40},
    {"column_name": "VM", "data_type": "TEXT", "is_required": True, "position": 50},
    {"column_name": "ResourceGroup", "data_type": "TEXT", "is_required": True, "position": 60},
    {"column_name": "SubscriptionId", "data_type": "TEXT", "is_required": True, "position": 70},
    {"column_name": "Enabled", "data_type": "BOOLEAN", "is_required": True, "position": 80},
    {"column_name": "NextRunTime", "data_type": "TIMESTAMPTZ", "is_required": False, "position": 90},
    {"column_name": "LastJobTime", "data_type": "TIMESTAMPTZ", "is_required": False, "position": 100},
    {"column_name": "ScheduleStatus", "data_type": "TEXT", "is_required": False, "position": 110},
    {"column_name": "_billing_period", "data_type": "TEXT", "is_required": True, "position": 1000},
    {"column_name": "source_connection_id", "data_type": "BIGINT", "is_required": True, "position": 1010},
    {"column_name": "cloud_origem", "data_type": "TEXT", "is_required": True, "position": 1020},
    {"column_name": "tipo", "data_type": "TEXT", "is_required": True, "position": 1030},
]

SCHEDULE_MAPPINGS: list[dict[str, Any]] = [
    {"source_column": "SnapshotDate", "destination_column": "SnapshotDate", "transform_type": "RENAME", "priority": 10},
    {"source_column": "ScheduleName", "destination_column": "ScheduleName", "transform_type": "RENAME", "priority": 20},
    {"source_column": "ScheduleRunbook", "destination_column": "ScheduleRunbook", "transform_type": "RENAME", "priority": 30},
    {"source_column": "ResourceId", "destination_column": "ResourceId", "transform_type": "RENAME", "priority": 40},
    {"source_column": "VM", "destination_column": "VM", "transform_type": "RENAME", "priority": 50},
    {"source_column": "ResourceGroup", "destination_column": "ResourceGroup", "transform_type": "RENAME", "priority": 60},
    {"source_column": "SubscriptionId", "destination_column": "SubscriptionId", "transform_type": "RENAME", "priority": 70},
    {"source_column": "Enabled", "destination_column": "Enabled", "transform_type": "RENAME", "priority": 80},
    {"source_column": "NextRunTime", "destination_column": "NextRunTime", "transform_type": "RENAME", "priority": 90},
    {"source_column": "LastJobTime", "destination_column": "LastJobTime", "transform_type": "RENAME", "priority": 100},
    {"source_column": "ScheduleStatus", "destination_column": "ScheduleStatus", "transform_type": "RENAME", "priority": 110},
]

AUTOMATION_COLUMNS: list[dict[str, Any]] = [
    {"column_name": "JobId", "data_type": "TEXT", "is_required": True, "position": 10},
    {"column_name": "Time", "data_type": "TIMESTAMPTZ", "is_required": True, "position": 20},
    {"column_name": "RunbookName", "data_type": "TEXT", "is_required": True, "position": 30},
    {"column_name": "Status", "data_type": "TEXT", "is_required": True, "position": 40},
    {"column_name": "VM", "data_type": "TEXT", "is_required": True, "position": 50},
    {"column_name": "ResourceGroup", "data_type": "TEXT", "is_required": True, "position": 60},
    {"column_name": "SubscriptionId", "data_type": "TEXT", "is_required": True, "position": 70},
    {"column_name": "Action", "data_type": "TEXT", "is_required": True, "position": 80},
    {"column_name": "CorrelationId", "data_type": "TEXT", "is_required": False, "position": 90},
    {"column_name": "Caller", "data_type": "TEXT", "is_required": False, "position": 100},
    {"column_name": "ResourceId", "data_type": "TEXT", "is_required": False, "position": 110},
    {"column_name": "_billing_period", "data_type": "TEXT", "is_required": True, "position": 1000},
    {"column_name": "source_connection_id", "data_type": "BIGINT", "is_required": True, "position": 1010},
    {"column_name": "cloud_origem", "data_type": "TEXT", "is_required": True, "position": 1020},
    {"column_name": "tipo", "data_type": "TEXT", "is_required": True, "position": 1030},
]

DEFAULT_MAPPINGS: list[dict[str, Any]] = [
    {"source_column": "JobId", "destination_column": "JobId", "transform_type": "RENAME", "priority": 10},
    {"source_column": "Time", "destination_column": "Time", "transform_type": "RENAME", "priority": 20},
    {"source_column": "RunbookName", "destination_column": "RunbookName", "transform_type": "RENAME", "priority": 30},
    {"source_column": "Status", "destination_column": "Status", "transform_type": "RENAME", "priority": 40},
    {"source_column": "VM", "destination_column": "VM", "transform_type": "RENAME", "priority": 50},
    {"source_column": "ResourceGroup", "destination_column": "ResourceGroup", "transform_type": "RENAME", "priority": 60},
    {"source_column": "SubscriptionId", "destination_column": "SubscriptionId", "transform_type": "RENAME", "priority": 70},
    {"source_column": "Action", "destination_column": "Action", "transform_type": "RENAME", "priority": 80},
    {"source_column": "CorrelationId", "destination_column": "CorrelationId", "transform_type": "RENAME", "priority": 90},
    {"source_column": "Caller", "destination_column": "Caller", "transform_type": "RENAME", "priority": 100},
    {"source_column": "ResourceId", "destination_column": "ResourceId", "transform_type": "RENAME", "priority": 110},
]


def _hours_in_month(yyyy_mm: str) -> int:
    y = int(yyyy_mm[:4])
    m = int(yyyy_mm[4:])
    return 24 * calendar.monthrange(y, m)[1]


class SetupResponse(BaseModel):
    connection_id: int
    destination_table_id: int
    message: str


class SetupRequest(BaseModel):
    connection_id: int | None = None


async def _setup_for_connection(db: AsyncSession, conn: SourceConnection) -> SetupResponse:
    """Idempotent setup of destination table + mappings for one connection."""
    # 1. Destination table (compartilhada por todos os tenants)
    dest = (
        await db.execute(select(DestinationTable).where(DestinationTable.physical_name == DESTINATION_PHYSICAL))
    ).scalar_one_or_none()
    if not dest:
        dest = DestinationTable(
            name=DESTINATION_NAME,
            physical_name=DESTINATION_PHYSICAL,
            description="Jobs do Azure Automation de start/stop de VMs (multi-tenant)",
            active=True,
        )
        db.add(dest)
        await db.flush()

    existing_cols = {
        c.column_name
        for c in (
            await db.execute(
                select(DestinationColumn).where(DestinationColumn.destination_table_id == dest.id)
            )
        ).scalars().all()
    }
    for col in AUTOMATION_COLUMNS:
        if col["column_name"] not in existing_cols:
            db.add(DestinationColumn(destination_table_id=dest.id, **col))
    await db.flush()

    # 1b. Destination table de schedules (snapshots diários)
    sched_dest = (
        await db.execute(select(DestinationTable).where(DestinationTable.physical_name == SCHEDULE_DESTINATION_PHYSICAL))
    ).scalar_one_or_none()
    if not sched_dest:
        sched_dest = DestinationTable(
            name=SCHEDULE_DESTINATION_NAME,
            physical_name=SCHEDULE_DESTINATION_PHYSICAL,
            description="Snapshots diários dos schedules do Azure Automation",
            active=True,
        )
        db.add(sched_dest)
        await db.flush()

    sched_existing_cols = {
        c.column_name
        for c in (
            await db.execute(
                select(DestinationColumn).where(DestinationColumn.destination_table_id == sched_dest.id)
            )
        ).scalars().all()
    }
    for col in SCHEDULE_COLUMNS:
        if col["column_name"] not in sched_existing_cols:
            db.add(DestinationColumn(destination_table_id=sched_dest.id, **col))
    await db.flush()

    # 2. Mappings for this connection (jobs)
    existing_maps = {
        (m.source_column, m.destination_column)
        for m in (
            await db.execute(
                select(SourceDestinationMapping).where(
                    SourceDestinationMapping.source_connection_id == conn.id,
                    SourceDestinationMapping.destination_table_id == dest.id,
                )
            )
        ).scalars().all()
    }
    for m in DEFAULT_MAPPINGS:
        if (m["source_column"], m["destination_column"]) not in existing_maps:
            db.add(
                SourceDestinationMapping(
                    source_connection_id=conn.id,
                    destination_table_id=dest.id,
                    name=f"{m['source_column']} -> {m['destination_column']}",
                    **m,
                    active=True,
                )
            )

    # 2b. Mappings for schedules
    sched_existing_maps = {
        (m.source_column, m.destination_column)
        for m in (
            await db.execute(
                select(SourceDestinationMapping).where(
                    SourceDestinationMapping.source_connection_id == conn.id,
                    SourceDestinationMapping.destination_table_id == sched_dest.id,
                )
            )
        ).scalars().all()
    }
    for m in SCHEDULE_MAPPINGS:
        if (m["source_column"], m["destination_column"]) not in sched_existing_maps:
            db.add(
                SourceDestinationMapping(
                    source_connection_id=conn.id,
                    destination_table_id=sched_dest.id,
                    name=f"{m['source_column']} -> {m['destination_column']}",
                    **m,
                    active=True,
                )
            )
    await db.commit()

    # Cria a regra Dimon padrão de saving por desligue (se ainda não existir).
    try:
        from app.models.gold_mart_rule import GoldMartRule
        from app.services.gold_materialization import normalize_code
        code = normalize_code("saving_desligue_vm")
        exists = (await db.execute(select(GoldMartRule).where(GoldMartRule.code == code))).scalar_one_or_none()
        if not exists:
            rule = GoldMartRule(
                code=code,
                name="Saving por desligue de VMs",
                description="Saving por VM/mês baseado em horas de consumo vs baseline 24x7 (query custom).",
                active=True,
                dimensions=["_billing_period", "connection_code", "resource_id", "resource_name", "subscription_id", "billing_period"],
                measure_columns=["hours_billed", "list_unit_price_hour", "vm_count"],
                sql_template=DEFAULT_SAVING_SQL,
                sources=[],
            )
            db.add(rule)
        code_tags = normalize_code("snapshot_tags_automacao")
        exists_tags = (await db.execute(select(GoldMartRule).where(GoldMartRule.code == code_tags))).scalar_one_or_none()
        if not exists_tags:
            db.add(GoldMartRule(
                code=code_tags,
                name="Snapshot tags shutdown=auto",
                description="Snapshot diário da tag shutdown=auto por recurso (modo snapshot: append datado sem replace).",
                active=True,
                dimensions=["resource_id", "resource_name", "connection_code", "_billing_period", "has_shutdown_auto"],
                measure_columns=[],
                sql_template=TAG_SNAPSHOT_SQL,
                snapshot_mode=True,
                snapshot_col="_snapshot_date",
                sources=[],
            ))
        await db.commit()
    except Exception:
        logger.exception("Falha ao criar regras Dimon padrão")

    return SetupResponse(
        connection_id=conn.id,
        destination_table_id=dest.id,
        message="Setup concluído: tabelas destino e mappings criados.",
    )


@router.post("/setup", response_model=SetupResponse)
async def setup_scheduled_shutdown(
    body: SetupRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Configura tabela destino e mappings para as conexões Azure Automation.

    Se `connection_id` for informado, configura essa conexão (multi-tenant).
    Sem connection_id, configura TODAS as conexões do tipo existentes — nunca
    cria uma conexão fantasma (o usuário cria as conexões na tela Connections).
    """
    conn_id = (body and body.connection_id) or None

    if conn_id:
        conn = await db.get(SourceConnection, conn_id)
        if not conn or conn.connector_type != CONNECTOR_TYPE:
            raise HTTPException(status_code=404, detail="Conexão Azure Automation não encontrada")
        return await _setup_for_connection(db, conn)

    conns = (
        await db.execute(select(SourceConnection).where(SourceConnection.connector_type == CONNECTOR_TYPE))
    ).scalars().all()
    if not conns:
        raise HTTPException(
            status_code=422,
            detail="Nenhuma conexão Azure Automation configurada. Crie uma em Connections (Ferramentas Cloud → Azure Automation).",
        )
    last = None
    for conn in conns:
        last = await _setup_for_connection(db, conn)
    if not last:
        raise HTTPException(status_code=422, detail="Nenhuma conexão Azure Automation configurada.")
    return last


class SavingsFilter(BaseModel):
    billing_periods: list[str] = Field(default_factory=list)
    resource_ids: list[str] = Field(default_factory=list)
    resource_groups: list[str] = Field(default_factory=list)
    subscriptions: list[str] = Field(default_factory=list)


class VmMonthSaving(BaseModel):
    resource_id: str | None
    resource_name: str | None
    subscription_id: str | None
    resource_group: str | None
    billing_period: str
    source_connection_id: int | None
    connection_code: str | None
    hours_billed: float
    baseline_hours: float
    uptime_pct: float
    saved_hours: float
    list_unit_price_hour: float
    saving_brl: float
    age_days: int | None
    is_new_vm: bool
    exception_rule_id: int | None
    exception_rule_name: str | None
    exception_cost_brl: float
    job_count: int
    job_actions: list[str]
    has_automation: bool
    padrao: str | None = None
    is_desligue_programado: bool = False


class SavingsResponse(BaseModel):
    billing_periods: list[str]
    rows: list[VmMonthSaving]
    total_saving_brl: float
    total_saved_hours: float
    vm_count: int
    new_vm_count: int
    vm_with_shutdown: int


@router.post("/savings", response_model=SavingsResponse)
async def calculate_savings(body: SavingsFilter, db: AsyncSession = Depends(get_db)):
    """Calcula saving por VM/mês cruzando billing FOCUS com jobs do Azure Automation.

    A baseline de horas possíveis é proporcional à idade do recurso no mês
    (detectada pelo primeiro ChargePeriodStart). Recursos com histórico menor
    que 60 dias ainda recebem baseline cheia, mas são marcados com
    ``age_days < 60`` para alerta.
    """
    await _ensure_setup(db)

    # Leitura da Dimon materializada (dest_dm_cost_saving_desligue_vm), que já
    # contém baseline por VM/mês calculada no reprocess — muito mais rápido que
    # re-scanear a Gold (31M linhas).
    periods_filter = list(body.billing_periods or [])
    if not periods_filter:
        from datetime import date as _date
        today = _date.today()
        y, m = today.year, today.month
        periods_filter = [f"{y:04d}{m:02d}"]
        prev = (m - 1) or 12
        py = y if m > 1 else y - 1
        periods_filter.append(f"{py:04d}{prev:02d}")
    literals = ", ".join(f"'{p}'" for p in periods_filter)

    dimon_sql = f"""
    SELECT
        resource_id,
        resource_name,
        connection_code,
        billing_period,
        total_hours,
        effective_price_hour,
        baseline_hours,
        age_days,
        is_new_vm,
        saved_hours,
        saving_brl,
        padrao,
        is_desligue_programado
    FROM iceberg.{MART_NAMESPACE}.dest_dm_cost_saving_desligue_vm
    WHERE _billing_period IN ({literals})
      AND is_desligue_programado = TRUE
    ORDER BY saving_brl DESC
    LIMIT 5000
    """

    totals_sql = f"""
    SELECT
        COUNT(*) AS rows_count,
        COUNT(DISTINCT resource_id) AS vm_count,
        COUNT(DISTINCT CASE WHEN is_new_vm THEN resource_id END) AS new_vm_count,
        COUNT(DISTINCT CASE WHEN saved_hours > 0 THEN resource_id END) AS vm_with_shutdown,
        COALESCE(SUM(saved_hours), 0) AS total_saved_hours,
        COALESCE(SUM(saving_brl), 0) AS total_saving_brl
    FROM iceberg.{MART_NAMESPACE}.dest_dm_cost_saving_desligue_vm
    WHERE _billing_period IN ({literals})
      AND is_desligue_programado = TRUE
    """
    try:
        rows = await execute(dimon_sql, timeout_s=120)
        totals_rows = await execute(totals_sql, timeout_s=120)
    except Exception as exc:
        # Dimon ainda não materializada: recua para a query lenta da Gold.
        if "does not exist" in str(exc):
            return await _savings_from_gold_fallback(body, db)
        raise
    totals = (totals_rows[0] or {}) if totals_rows else {}

    # Automação: usa o INVENTÁRIO Bronze (4P + TI) — cada recurso com schedule
    # Start/Stop ativo no snapshot mais recente está "na automação". A Gold de
    # jobs antiga não preenche ResourceId, então não é usada aqui.
    inv_sql = """
    SELECT
        inferred_resource_name AS resource_name,
        tenant AS connection_code,
        MAX(CASE WHEN LOWER(inferred_action) = 'stop' AND LOWER(COALESCE(schedule_enabled, 'false')) = 'true' THEN 1 ELSE 0 END) AS has_stop,
        MAX(CASE WHEN LOWER(inferred_action) = 'start' AND LOWER(COALESCE(schedule_enabled, 'false')) = 'true' THEN 1 ELSE 0 END) AS has_start
    FROM (
        SELECT inferred_resource_name, tenant, inferred_action, schedule_enabled, _snapshot_date
        FROM iceberg.bronze.raw_azure_automation_4p
        WHERE _blob_path LIKE '%inventory%'
        UNION ALL
        SELECT inferred_resource_name, tenant, inferred_action, schedule_enabled, _snapshot_date
        FROM iceberg.bronze.raw_azure_automation_ti
        WHERE _blob_path LIKE '%inventory%'
    ) t
    WHERE inferred_resource_name IS NOT NULL
      AND _snapshot_date = (
          SELECT MAX(_snapshot_date) FROM (
              SELECT _snapshot_date FROM iceberg.bronze.raw_azure_automation_4p WHERE _blob_path LIKE '%inventory%'
              UNION ALL
              SELECT _snapshot_date FROM iceberg.bronze.raw_azure_automation_ti WHERE _blob_path LIKE '%inventory%'
          ) s
      )
    GROUP BY inferred_resource_name, tenant
    """
    try:
        inv_rows = await execute(inv_sql, timeout_s=60)
    except Exception:
        inv_rows = []
    automation_keys: dict[tuple[str, str], dict] = {}
    for j in inv_rows:
        name = str(j.get("resource_name") or "").strip().lower()
        tenant = str(j.get("connection_code") or "").strip().lower().replace("azure-", "")
        if name:
            automation_keys[(name, tenant)] = {
                "has_stop": bool(j.get("has_stop")),
                "has_start": bool(j.get("has_start")),
            }

    out: list[VmMonthSaving] = []
    exception_rules = (await db.execute(select(ShutdownExceptionRule).where(ShutdownExceptionRule.is_active == True))).scalars().all()

    # Lookup connection code por id (as conexões são multi-tenant: 4P, TI...).
    conn_rows = (await db.execute(select(SourceConnection.id, SourceConnection.code))).all()
    conn_code_by_id = {r[0]: r[1] for r in conn_rows}

    def _matches_exception(rule: ShutdownExceptionRule, row: dict[str, Any]) -> bool:
        rid = str(row.get("resource_id") or "").strip().lower()
        sub = str(row.get("subscription_id") or "").strip().lower()
        val = str(rule.scope_value or "").strip().lower()
        if rule.scope_type == "resource" and rid and val and rid == val:
            return True
        if rule.scope_type == "subscription" and sub and val and sub == val:
            return True
        return False

    for r in rows:
        bp = str(r["billing_period"])
        baseline = float(r["baseline_hours"] or _hours_in_month(bp))
        hours_billed = float(r["total_hours"] or 0)
        price = float(r["effective_price_hour"] or 0)
        saved_hours = float(r["saved_hours"] or 0)
        saving_brl = float(r["saving_brl"] or 0)
        matched = next((e for e in exception_rules if _matches_exception(e, r)), None)
        # Automação pelo inventário (nome da VM + tenant normalizado).
        auto_key = (str(r["resource_name"] or "").strip().lower(), str(r.get("connection_code") or "").strip().lower().replace("azure-", ""))
        auto_info = automation_keys.get(auto_key) or {}
        has_auto = bool(auto_info.get("has_stop") or auto_info.get("has_start"))
        auto_actions: list[str] = []
        if auto_info.get("has_stop"):
            auto_actions.append("Stop")
        if auto_info.get("has_start"):
            auto_actions.append("Start")
        out.append(
            VmMonthSaving(
                resource_id=r["resource_id"],
                resource_name=r["resource_name"],
                subscription_id=None,
                resource_group=None,
                billing_period=bp,
                source_connection_id=None,
                connection_code=r.get("connection_code"),
                hours_billed=round(hours_billed, 2),
                baseline_hours=baseline,
                uptime_pct=round((hours_billed / baseline * 100) if baseline else 0, 2),
                saved_hours=round(saved_hours, 2),
                list_unit_price_hour=price,
                saving_brl=round(saving_brl, 2),
                age_days=int(r["age_days"]) if r.get("age_days") is not None else None,
                is_new_vm=bool(r.get("is_new_vm")),
                exception_rule_id=matched.id if matched else None,
                exception_rule_name=matched.name if matched else None,
                exception_cost_brl=saving_brl if matched else 0.0,
                job_count=1 if has_auto else 0,
                job_actions=auto_actions,
                has_automation=has_auto,
                padrao=r.get("padrao"),
                is_desligue_programado=bool(r.get("is_desligue_programado")),
            )
        )

    return SavingsResponse(
        billing_periods=sorted({r.billing_period for r in out}),
        rows=out,
        total_saving_brl=round(float(totals.get("total_saving_brl") or 0), 2),
        total_saved_hours=round(float(totals.get("total_saved_hours") or 0), 2),
        vm_count=int(totals.get("vm_count") or 0),
        new_vm_count=int(totals.get("new_vm_count") or 0),
        vm_with_shutdown=int(totals.get("vm_with_shutdown") or 0),
    )


async def _savings_from_gold_fallback(body: SavingsFilter, db: AsyncSession) -> SavingsResponse:
    """Fallback lento: calcula o saving direto da Gold quando a Dimon ainda
    não foi materializada. Mantém compatibilidade com o dashboard."""
    periods_filter = list(body.billing_periods or [])
    if not periods_filter:
        from datetime import date as _date
        today = _date.today()
        y, m = today.year, today.month
        periods_filter = [f"{y:04d}{m:02d}"]
        prev = (m - 1) or 12
        periods_filter.append(f"{y if m > 1 else y - 1:04d}{prev:02d}")
    literals = ", ".join(f"'{p}'" for p in periods_filter)

    sql = f"""
    WITH vm_usage AS (
        SELECT
            "ResourceId" AS resource_id,
            MAX("ResourceName") AS resource_name,
            _billing_period AS billing_period,
            SUM(COALESCE(TRY_CAST("ConsumedQuantity" AS DOUBLE), 0)) AS hours_billed,
            SUM(COALESCE(TRY_CAST("EffectiveCost" AS DOUBLE), 0)) AS total_effective_cost,
            MIN(TRY(date_parse("ChargePeriodStart", '%m/%d/%Y'))) AS first_seen
        FROM iceberg.{layers.detail_layer()}.dest_modelo_focus
        WHERE LOWER(CAST("ServiceSubcategory" AS VARCHAR)) LIKE '%virtual machines%'
          AND ("ChargeCategory" = 'Usage' OR "ChargeCategory" IS NULL)
          AND LOWER(COALESCE(CAST("ResourceName" AS VARCHAR), '')) NOT LIKE '%savings plan%'
          AND LOWER(COALESCE(CAST("ResourceName" AS VARCHAR), '')) NOT LIKE '%reserved vm instance%'
          AND _billing_period IN ({literals})
        GROUP BY "ResourceId", _billing_period
    ),
    baseline AS (
        SELECT
            resource_id,
            resource_name,
            billing_period,
            hours_billed,
            total_effective_cost,
            date_parse(billing_period || '01', '%Y%m%d') AS month_start_date,
            last_day_of_month(date_parse(billing_period || '01', '%Y%m%d')) AS month_end_date,
            date_diff('day', date_parse(billing_period || '01', '%Y%m%d'), CAST(COALESCE(first_seen, date_parse(billing_period || '01', '%Y%m%d')) AS DATE)) AS age_days
        FROM vm_usage
    )
    SELECT
        resource_id,
        resource_name,
        billing_period,
        hours_billed,
        total_effective_cost,
        age_days,
        CASE WHEN age_days < 60 THEN TRUE ELSE FALSE END AS is_new_vm,
        (date_diff('day', CAST(GREATEST(month_start_date, COALESCE(first_seen, month_start_date)) AS DATE), month_end_date) + 1) * 24 AS baseline_hours,
        CASE WHEN hours_billed > 0 THEN total_effective_cost / hours_billed ELSE 0.0 END AS effective_price_hour
    FROM baseline
    """
    rows = await execute(sql, timeout_s=600)

    # Ciclo de vida diário por período (janela 30d com folga de 15d).
    lifecycle_by_key: dict[tuple[str, str], tuple[bool, bool]] = {}
    for period in periods_filter:
        window_start, window_end = _desligue_window_dates(period)
        life_sql = f"""
        SELECT
            LOWER(TRIM("ResourceName")) AS resource_name,
            MIN(CASE
                WHEN COALESCE(TRY_CAST("ConsumedQuantity" AS DOUBLE), 0) > 0
                THEN date_parse("ChargePeriodStart", '%m/%d/%Y')
            END) AS first_seen,
            MAX(CASE
                WHEN COALESCE(TRY_CAST("ConsumedQuantity" AS DOUBLE), 0) > 0
                THEN date_parse("ChargePeriodStart", '%m/%d/%Y')
            END) AS last_seen
        FROM iceberg.{layers.detail_layer()}.dest_modelo_focus
        WHERE _billing_period = '{period}'
          AND "ChargePeriodStart" IS NOT NULL
          AND LOWER(CAST("ServiceSubcategory" AS VARCHAR)) LIKE '%virtual machines%'
          AND ("ChargeCategory" = 'Usage' OR "ChargeCategory" IS NULL)
          AND date_parse("ChargePeriodStart", '%m/%d/%Y')
              BETWEEN DATE '{window_start}' AND DATE '{window_end}'
        GROUP BY 1
        """
        try:
            life_rows = await execute(life_sql, timeout_s=120)
        except Exception:
            life_rows = []
        for lr in life_rows:
            fs = lr.get("first_seen")
            ls = lr.get("last_seen")
            fs_s = fs.isoformat() if hasattr(fs, "isoformat") else (str(fs)[:10] if fs else None)
            ls_s = ls.isoformat() if hasattr(ls, "isoformat") else (str(ls)[:10] if ls else None)
            born, died, _ = evaluate_vm_lifecycle(fs_s, ls_s, window_start, window_end)
            lifecycle_by_key[(str(lr.get("resource_name") or "").strip().lower(), period)] = (born, died)

    exception_rules = (await db.execute(select(ShutdownExceptionRule).where(ShutdownExceptionRule.is_active == True))).scalars().all()

    def _matches_exception(rule: ShutdownExceptionRule, row: dict[str, Any]) -> bool:
        rid = str(row.get("resource_id") or "").strip().lower()
        val = str(rule.scope_value or "").strip().lower()
        return rule.scope_type == "resource" and rid and val and rid == val

    out: list[VmMonthSaving] = []
    for r in rows:
        bp = str(r["billing_period"])
        rname = str(r.get("resource_name") or "").strip().lower()
        born, died = lifecycle_by_key.get((rname, bp), (False, False))
        if born or died:
            continue
        baseline = float(r["baseline_hours"] or _hours_in_month(bp))
        hours_billed = float(r["hours_billed"] or 0)
        price = float(r["effective_price_hour"] or 0)
        saved_hours = max(0.0, baseline - hours_billed)
        saving_brl = round(saved_hours * price, 2)
        matched = next((e for e in exception_rules if _matches_exception(e, r)), None)
        out.append(VmMonthSaving(
            resource_id=r["resource_id"],
            resource_name=r["resource_name"],
            subscription_id=None,
            resource_group=None,
            billing_period=bp,
            source_connection_id=None,
            connection_code=None,
            hours_billed=round(hours_billed, 2),
            baseline_hours=baseline,
            uptime_pct=round((hours_billed / baseline * 100) if baseline else 0, 2),
            saved_hours=round(saved_hours, 2),
            list_unit_price_hour=price,
            saving_brl=saving_brl,
            age_days=int(r["age_days"]) if r.get("age_days") is not None else None,
            is_new_vm=bool(r.get("is_new_vm")),
            exception_rule_id=matched.id if matched else None,
            exception_rule_name=matched.name if matched else None,
            exception_cost_brl=saving_brl if matched else 0.0,
            job_count=0,
            job_actions=[],
            has_automation=False,
        ))

    return SavingsResponse(
        billing_periods=sorted({r.billing_period for r in out}),
        rows=out,
        total_saving_brl=round(sum(r.saving_brl for r in out), 2),
        total_saved_hours=round(sum(r.saved_hours for r in out), 2),
        vm_count=len({r.resource_id for r in out if r.resource_id}),
        new_vm_count=len({r.resource_id for r in out if r.resource_id and r.is_new_vm}),
        vm_with_shutdown=len({r.resource_id for r in out if r.resource_id and r.saved_hours > 0}),
    )


class ScheduleDeltaItem(BaseModel):
    resource_id: str | None
    vm: str | None
    schedule_name: str | None = None
    subscription_id: str | None
    source_connection_id: int | None
    connection_code: str | None
    change_type: str  # REMOVIDO | ADICIONADO | DESABILITADO | REABILITADO | INALTERADO | ATUAL
    snapshot_date: str | None
    previous_enabled: bool | None = None
    current_enabled: bool | None = None
    enabled: bool | None = None
    days_out: int | None = None
    cost_since_removal: float = 0.0
    is_exception: bool = False
    exception_rule_id: int | None = None


class ScheduleDeltaResponse(BaseModel):
    latest_snapshot: str | None
    previous_snapshot: str | None
    rows: list[ScheduleDeltaItem]
    total_removed: int
    total_disabled: int
    total_added: int
    total_cost_since_removal: float


@router.get("/schedule-delta", response_model=ScheduleDeltaResponse)
async def schedule_delta(db: AsyncSession = Depends(get_db)):
    """Compara o snapshot mais recente do inventário de schedules com o anterior (D vs D-1).

    O inventário vem da Bronze (raw_azure_automation_4p/ti, blob inventory),
    unificado 4P + TI. Identifica recursos que ENTRARAM ou SAÍRAM do desligue
    programado (REMOVIDO / DESABILITADO / ADICIONADO / REABILITADO) e estima o
    custo desde a saída usando o preço on-demand do billing.
    """
    # Data de snapshot mais recente no inventário (Bronze 4P + TI).
    dates_sql = """
    SELECT DISTINCT _snapshot_date AS snapshot_date
    FROM (
        SELECT _snapshot_date FROM iceberg.bronze.raw_azure_automation_4p
        UNION ALL
        SELECT _snapshot_date FROM iceberg.bronze.raw_azure_automation_ti
    )
    WHERE _snapshot_date IS NOT NULL
    ORDER BY 1 DESC
    """
    try:
        dates_rows = await execute(dates_sql, timeout_s=60)
    except Exception:
        return ScheduleDeltaResponse(latest_snapshot=None, previous_snapshot=None, rows=[], total_removed=0, total_disabled=0, total_added=0, total_cost_since_removal=0.0)

    dates = [str(r["snapshot_date"])[:10] for r in dates_rows if r.get("snapshot_date")]
    if not dates:
        return ScheduleDeltaResponse(latest_snapshot=None, previous_snapshot=None, rows=[], total_removed=0, total_disabled=0, total_added=0, total_cost_since_removal=0.0)

    latest = dates[0]
    previous = dates[1] if len(dates) > 1 else None

    # Regras de exceção ativas por resource_id (para marcar recursos que já
    # saíram e foram registrados como exceção).
    exc_rows = (await db.execute(
        select(ShutdownExceptionRule).where(ShutdownExceptionRule.is_active == True)  # noqa: E712
    )).scalars().all()
    exc_by_rid = {str(e.scope_value).strip().lower(): e.id for e in exc_rows if e.scope_type == "resource" and e.scope_value}

    # Resumo por recurso no snapshot D: tem Stop ativo? tem Start ativo?
    # Unifica 4P + TI na mesma consulta.
    inventory_union = f"""
    SELECT _snapshot_date, tenant, automation_account,
           inferred_resource_name, inferred_action, schedule_enabled
    FROM iceberg.bronze.raw_azure_automation_4p
    WHERE _blob_path LIKE '%inventory%'
    UNION ALL
    SELECT _snapshot_date, tenant, automation_account,
           inferred_resource_name, inferred_action, schedule_enabled
    FROM iceberg.bronze.raw_azure_automation_ti
    WHERE _blob_path LIKE '%inventory%'
    """

    if previous is None:
        # Apenas um snapshot: mostra o inventário ATUAL (recursos com schedule).
        inv_sql = f"""
        SELECT
            inferred_resource_name AS resource_name,
            tenant AS connection_code,
            MAX(CASE WHEN LOWER(inferred_action) = 'stop' THEN 1 ELSE 0 END) AS has_stop,
            MAX(CASE WHEN LOWER(inferred_action) = 'start' THEN 1 ELSE 0 END) AS has_start,
            MAX(CASE WHEN LOWER(inferred_action) = 'stop' AND LOWER(COALESCE(schedule_enabled, 'false')) = 'true' THEN 1 ELSE 0 END) AS stop_enabled,
            MAX(CASE WHEN LOWER(inferred_action) = 'start' AND LOWER(COALESCE(schedule_enabled, 'false')) = 'true' THEN 1 ELSE 0 END) AS start_enabled
        FROM ({inventory_union}) t
        WHERE inferred_resource_name IS NOT NULL
          AND CAST(_snapshot_date AS VARCHAR) LIKE '{latest}%'
        GROUP BY inferred_resource_name, tenant
        """
        inv_rows = []
        try:
            inv_rows = await execute(inv_sql, timeout_s=60)
        except Exception:
            inv_rows = []
        items: list[ScheduleDeltaItem] = []
        for r in inv_rows:
            rid = str(r.get("resource_name") or "").strip().lower()
            exc_id = exc_by_rid.get(rid)
            active = bool(r.get("stop_enabled") or r.get("start_enabled"))
            items.append(ScheduleDeltaItem(
                resource_id=str(r.get("resource_name") or ""),
                vm=str(r.get("resource_name") or ""),
                schedule_name=f"{r.get('resource_name')} (Start/Stop)",
                subscription_id=None,
                source_connection_id=None,
                connection_code=r.get("connection_code"),
                change_type="ATUAL",
                snapshot_date=latest,
                enabled=active,
                is_exception=exc_id is not None,
                exception_rule_id=exc_id,
            ))
        return ScheduleDeltaResponse(
            latest_snapshot=latest,
            previous_snapshot=None,
            rows=items,
            total_removed=0,
            total_disabled=0,
            total_added=0,
            total_cost_since_removal=0.0,
        )

    # Delta D vs D-1 por recurso: considera o recurso "no desligue" se tem
    # schedule Stop OU Start ativo (presente no inventário).
    delta_sql = f"""
    WITH hoje AS (
        SELECT inferred_resource_name AS resource_name,
               tenant AS connection_code,
               MAX(CASE WHEN LOWER(inferred_action) = 'stop' AND LOWER(COALESCE(schedule_enabled, 'false')) = 'true' THEN 1
                        WHEN LOWER(inferred_action) = 'start' AND LOWER(COALESCE(schedule_enabled, 'false')) = 'true' THEN 1
                        ELSE 0 END) AS active
        FROM ({inventory_union}) t
        WHERE inferred_resource_name IS NOT NULL
          AND CAST(_snapshot_date AS VARCHAR) LIKE '{latest}%'
        GROUP BY inferred_resource_name, tenant
    ),
    ontem AS (
        SELECT inferred_resource_name AS resource_name,
               tenant AS connection_code,
               MAX(CASE WHEN LOWER(inferred_action) = 'stop' AND LOWER(COALESCE(schedule_enabled, 'false')) = 'true' THEN 1
                        WHEN LOWER(inferred_action) = 'start' AND LOWER(COALESCE(schedule_enabled, 'false')) = 'true' THEN 1
                        ELSE 0 END) AS active
        FROM ({inventory_union}) t
        WHERE inferred_resource_name IS NOT NULL
          AND CAST(_snapshot_date AS VARCHAR) LIKE '{previous}%'
        GROUP BY inferred_resource_name, tenant
    )
    SELECT
        COALESCE(h.resource_name, o.resource_name) AS resource_name,
        COALESCE(h.connection_code, o.connection_code) AS connection_code,
        o.active AS previous_active,
        h.active AS current_active,
        CASE
            WHEN o.resource_name IS NULL THEN 'ADICIONADO'
            WHEN h.resource_name IS NULL THEN 'REMOVIDO'
            WHEN h.active = 1 AND COALESCE(o.active, 0) = 0 THEN 'REABILITADO'
            WHEN COALESCE(h.active, 0) = 0 AND o.active = 1 THEN 'DESABILITADO'
            ELSE 'INALTERADO'
        END AS change_type
    FROM hoje h
    FULL OUTER JOIN ontem o ON LOWER(h.resource_name) = LOWER(o.resource_name)
    """
    rows = await execute(delta_sql, timeout_s=120)

    items: list[ScheduleDeltaItem] = []
    total_cost = 0.0
    for r in rows:
        change_type = str(r["change_type"] or "INALTERADO")
        cost_since = 0.0
        days_out: int | None = None
        rid = str(r.get("resource_name") or "").strip().lower()
        if change_type in ("REMOVIDO", "DESABILITADO"):
            # Custo desde a saída: estima com o preço on-demand médio das VMs
            # (busca pelo nome da VM no billing).
            cost_since, days_out = await _cost_since_removal_by_name(
                str(r.get("resource_name") or ""),
                latest,
            )
            total_cost += cost_since
        exc_id = exc_by_rid.get(rid)
        items.append(
            ScheduleDeltaItem(
                resource_id=str(r.get("resource_name") or ""),
                vm=str(r.get("resource_name") or ""),
                schedule_name=f"{r.get('resource_name')} (Start/Stop)",
                subscription_id=None,
                source_connection_id=None,
                connection_code=r.get("connection_code"),
                change_type=change_type,
                snapshot_date=latest if change_type != "REMOVIDO" else previous,
                previous_enabled=bool(r.get("previous_active")),
                current_enabled=bool(r.get("current_active")),
                days_out=days_out,
                cost_since_removal=round(cost_since, 2),
                is_exception=exc_id is not None,
                exception_rule_id=exc_id,
            )
        )

    removed = sum(1 for i in items if i.change_type == "REMOVIDO")
    disabled = sum(1 for i in items if i.change_type == "DESABILITADO")
    added = sum(1 for i in items if i.change_type == "ADICIONADO")

    return ScheduleDeltaResponse(
        latest_snapshot=latest,
        previous_snapshot=previous,
        rows=items,
        total_removed=removed,
        total_disabled=disabled,
        total_added=added,
        total_cost_since_removal=round(total_cost, 2),
    )


async def _cost_since_removal_by_name(vm_name: str, snapshot_date: str) -> tuple[float, int]:
    """Estima o custo de uma VM desde a data em que saiu do schedule (por nome)."""
    if not vm_name:
        return 0.0, 0
    try:
        from datetime import date as _date
        target = _date.fromisoformat(snapshot_date[:10])
    except Exception:
        target = None

    price_sql = f"""
    SELECT
        AVG(COALESCE(TRY_CAST("BilledCost" AS DOUBLE), 0) / NULLIF(COALESCE(TRY_CAST("ConsumedQuantity" AS DOUBLE), 0), 0)) AS avg_price_hour
    FROM iceberg.{layers.detail_layer()}.dest_modelo_focus
    WHERE LOWER(TRIM(element_at(split(CAST("ResourceId" AS VARCHAR), '/'), -1))) = LOWER(TRIM('{vm_name.replace("'", "''")}'))
      AND LOWER(CAST("ServiceSubcategory" AS VARCHAR)) LIKE '%virtual machines%'
      AND ("ChargeCategory" = 'Usage' OR "ChargeCategory" IS NULL)
    """
    try:
        price_rows = await execute(price_sql, timeout_s=60)
        avg_price = float((price_rows[0] or {}).get("avg_price_hour") or 0) if price_rows else 0.0
    except Exception:
        avg_price = 0.0

    days_out = 0
    if target:
        days_out = (datetime.now(timezone.utc).date() - target).days
        if days_out < 0:
            days_out = 0

    # Custo estimado: VM ligada 24x7 desde a saída.
    cost = days_out * 24 * avg_price
    return cost, days_out


async def _cost_since_removal(resource_id: str, subscription_id: str, snapshot_date: str) -> tuple[float, int]:
    """Estima o custo de uma VM desde a data em que saiu do schedule.

    Usa o preço on-demand médio por hora do billing e o uptime no período,
    assumindo que a VM ficou ligada 24×7 desde a saída.
    """
    if not resource_id:
        return 0.0, 0
    try:
        from datetime import date as _date
        target = _date.fromisoformat(snapshot_date[:10])
    except Exception:
        target = None

    price_sql = f"""
    SELECT
        AVG(COALESCE(TRY_CAST("ListUnitPrice" AS DOUBLE), 0)) AS avg_price_hour
    FROM iceberg.{layers.detail_layer()}.dest_modelo_focus
    WHERE LOWER(TRIM("ResourceId")) = LOWER(TRIM('{resource_id.replace("'", "''")}'))
      AND LOWER(CAST("ServiceSubcategory" AS VARCHAR)) LIKE '%virtual machines%'
      AND ("ChargeCategory" = 'Usage' OR "ChargeCategory" IS NULL)
    """
    try:
        price_rows = await execute(price_sql, timeout_s=60)
        avg_price = float((price_rows[0] or {}).get("avg_price_hour") or 0) if price_rows else 0.0
    except Exception:
        avg_price = 0.0

    days_out = 0
    if target:
        days_out = (datetime.now(timezone.utc).date() - target).days
        if days_out < 0:
            days_out = 0

    # Custo estimado: VM ligada 24x7 desde a saída.
    cost = days_out * 24 * avg_price
    return cost, days_out


class ExceptionRuleCreate(BaseModel):
    name: str
    description: str | None = None
    owner_email: str | None = None
    scope_type: str = "resource"
    scope_value: str | None = None
    tag_key: str | None = None
    tag_value: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    is_active: bool = True


class ExceptionRuleOut(ExceptionRuleCreate):
    id: int

    model_config = {"from_attributes": True}
    id: int

    model_config = {"from_attributes": True}


@router.post("/exceptions", response_model=ExceptionRuleOut, status_code=201)
async def create_exception(data: ExceptionRuleCreate, db: AsyncSession = Depends(get_db)):
    rule = ShutdownExceptionRule(**data.model_dump())
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return ExceptionRuleOut.model_validate(rule)


@router.get("/exceptions", response_model=list[ExceptionRuleOut])
async def list_exceptions(active_only: bool = False, db: AsyncSession = Depends(get_db)):
    stmt = select(ShutdownExceptionRule)
    if active_only:
        stmt = stmt.where(ShutdownExceptionRule.is_active == True)  # noqa: E712
    stmt = stmt.order_by(ShutdownExceptionRule.created_at.desc())
    return [ExceptionRuleOut.model_validate(r) for r in (await db.execute(stmt)).scalars().all()]


@router.put("/exceptions/{rule_id}", response_model=ExceptionRuleOut)
async def update_exception(rule_id: int, data: ExceptionRuleCreate, db: AsyncSession = Depends(get_db)):
    rule = await db.get(ShutdownExceptionRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Regra não encontrada")
    for k, v in data.model_dump().items():
        setattr(rule, k, v)
    await db.commit()
    await db.refresh(rule)
    return ExceptionRuleOut.model_validate(rule)


@router.delete("/exceptions/{rule_id}", status_code=204)
async def delete_exception(rule_id: int, db: AsyncSession = Depends(get_db)):
    rule = await db.get(ShutdownExceptionRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Regra não encontrada")
    await db.delete(rule)
    await db.commit()


class ExceptionFromDeltaCreate(BaseModel):
    resource_id: str
    resource_name: str | None = None
    connection_code: str | None = None
    subscription_name: str | None = None
    reason: str = "Saiu do schedule de desligue programado"


@router.post("/exceptions/from-delta", response_model=ExceptionRuleOut)
async def create_exception_from_delta(data: ExceptionFromDeltaCreate, db: AsyncSession = Depends(get_db)):
    """Registra uma regra de exceção para um recurso que SAIU do schedule de
    desligue (REMOVIDO/DESABILITADO no delta de snapshots).

    A exceção impede que o saving desse recurso volte a ser contabilizado como
    desligue programado, já que ele não está mais sob automação. Se já existir
    uma exceção ativa para o mesmo resource_id, retorna a existente.
    """
    rid = data.resource_id.strip().lower()
    if not rid:
        raise HTTPException(status_code=422, detail="resource_id é obrigatório")

    existing = (await db.execute(
        select(ShutdownExceptionRule).where(
            ShutdownExceptionRule.is_active == True,  # noqa: E712
            ShutdownExceptionRule.scope_type == "resource",
            ShutdownExceptionRule.scope_value == data.resource_id.strip(),
        ).limit(1)
    )).scalar_one_or_none()
    if existing:
        return ExceptionRuleOut.model_validate(existing)

    name = f"Saiu do desligue — {data.resource_name or rid}"
    desc_parts = [data.reason]
    if data.connection_code:
        desc_parts.append(f"Tenant: {data.connection_code}")
    if data.subscription_name:
        desc_parts.append(f"Subscrição: {data.subscription_name}")
    desc_parts.append(f"ResourceId: {data.resource_id.strip()}")

    rule = ShutdownExceptionRule(
        name=name[:200],
        description=" | ".join(desc_parts),
        scope_type="resource",
        scope_value=data.resource_id.strip(),
        is_active=True,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return ExceptionRuleOut.model_validate(rule)


class SavingsMonthlyRow(BaseModel):
    billing_period: str
    resources: int
    saved_hours: float
    saving_brl: float


class SavingsMonthlyResponse(BaseModel):
    rows: list[SavingsMonthlyRow]


@router.get("/savings-monthly")
async def savings_monthly():
    """Resumo mensal de saving (agregado na Dimon) — para o gráfico de barras.

    MESMO ESCOPO do /savings e da tabela: soma apenas os padrões validados
    (DESLIGUE_PROGRAMADO, USO_IRREGULAR, ANALISAR) — exclui POUCO_HISTORICO
    (sem 60 dias de histórico p/ validar), VM_DESLIGADA (não é desligue
    programado) e LIGADA_24x7 (sem desligue). Dinâmico: retorna todos os
    períodos da Dimon a partir de 202601 (dez/2025 excluído — fatura não
    confiável); meses sem padrão validado (ex.: jan/2026) aparecem com 0.
    """
    # Todos os períodos disponíveis na Dimon, a partir de 202601.
    periods_sql = """
    SELECT DISTINCT _billing_period AS p
    FROM iceberg.{MART_NAMESPACE}.dest_dm_cost_saving_desligue_vm
    WHERE _billing_period >= '202601'
    ORDER BY 1
    """
    try:
        periods_rows = await execute(periods_sql, timeout_s=60)
    except Exception:
        return SavingsMonthlyResponse(rows=[])
    all_periods = [str(r["p"]) for r in periods_rows if r.get("p")]

    sql = """
    SELECT
        _billing_period AS billing_period,
        COUNT(DISTINCT resource_id) AS resources,
        COALESCE(SUM(saved_hours), 0) AS saved_hours,
        COALESCE(SUM(saving_brl), 0) AS saving_brl
    FROM iceberg.{MART_NAMESPACE}.dest_dm_cost_saving_desligue_vm
    WHERE saved_hours > 0
      AND is_desligue_programado = TRUE
    GROUP BY 1
    ORDER BY 1
    """
    rows = await execute(sql, timeout_s=120)
    by_period = {str(r["billing_period"]): r for r in rows}
    # Garante que todos os meses apareçam (meses sem padrão validado → 0).
    out_rows = []
    for p in all_periods:
        r = by_period.get(p)
        out_rows.append(SavingsMonthlyRow(
            billing_period=p,
            resources=int((r or {}).get("resources") or 0),
            saved_hours=round(float((r or {}).get("saved_hours") or 0), 0),
            saving_brl=round(float((r or {}).get("saving_brl") or 0), 2),
        ))
    return SavingsMonthlyResponse(rows=out_rows)


class PadroesItem(BaseModel):
    padrao: str
    resources: int
    saved_hours: float
    saving_brl: float
    desligue_programado: bool


class PadroesResponse(BaseModel):
    billing_period: str
    rows: list[PadroesItem]


@router.get("/padroes-periodos")
async def padroes_periodos():
    """Lista todos os períodos disponíveis na Dimon de saving (para o seletor
    de data do dashboard — inclui meses sem padrões relevantes, como jan/2026
    que é 100% POUCO_HISTORICO)."""
    list_filter = desligue_list_filter_sql()
    sql = f"""
    SELECT _billing_period AS period,
           COUNT(*) AS resources
    FROM iceberg.{MART_NAMESPACE}.dest_dm_cost_saving_desligue_vm
    WHERE {list_filter}
    GROUP BY 1
    ORDER BY 1
    """
    try:
        rows = await execute(sql, timeout_s=120)
    except Exception:
        return {"periods": []}
    return {
        "periods": [
            {
                "period": str(r["period"]),
                "resources": int(r["resources"] or 0),
            }
            for r in rows
        ]
    }


@router.get("/padroes")
async def padroes_distribuicao(period: str | None = None):
    """Distribuição de padrões de desligue identificados (Dimon).

    ``period`` opcional (YYYYMM). Default: mês mais recente disponível.
    """
    if not period:
        try:
            pr = await execute(
                "SELECT MAX(_billing_period) AS p FROM iceberg.{MART_NAMESPACE}.dest_dm_cost_saving_desligue_vm",
                timeout_s=60,
            )
            period = str((pr[0] or {}).get("p") or "") if pr else ""
        except Exception:
            pass
    if not period:
        return PadroesResponse(billing_period="", rows=[])
    # Lista apenas padrões relevantes — exclui efêmeras (CICLO_CURTO, AKS) e 24×7.
    list_filter = desligue_list_filter_sql()
    sql = f"""
    SELECT
        COALESCE(padrao, 'SEM_PADRAO') AS padrao,
        COUNT(*) AS resources,
        COALESCE(SUM(saved_hours), 0) AS saved_hours,
        COALESCE(SUM(saving_brl), 0) AS saving_brl,
        MAX(is_desligue_programado) AS dp
    FROM iceberg.{MART_NAMESPACE}.dest_dm_cost_saving_desligue_vm
    WHERE _billing_period = '{period}'
      AND {list_filter}
    GROUP BY 1
    ORDER BY saving_brl DESC
    """
    try:
        rows = await execute(sql, timeout_s=120)
    except Exception:
        return PadroesResponse(billing_period=period, rows=[])
    return PadroesResponse(
        billing_period=period,
        rows=[
            PadroesItem(
                padrao=str(r["padrao"]),
                resources=int(r["resources"] or 0),
                saved_hours=round(float(r["saved_hours"] or 0), 0),
                saving_brl=round(float(r["saving_brl"] or 0), 2),
                desligue_programado=bool(r["dp"]),
            )
            for r in rows
        ],
    )


class PadraoRecursoItem(BaseModel):
    resource_name: str
    connection_code: str | None = None
    subscription_name: str | None = None
    total_hours: float
    saved_hours: float
    saving_brl: float
    is_desligue_programado: bool


class PadraoRecursosResponse(BaseModel):
    billing_period: str
    padrao: str
    rows: list[PadraoRecursoItem]


@router.get("/padroes-recursos", response_model=PadraoRecursosResponse)
async def padroes_recursos(period: str | None = None, padrao: str | None = None):
    """Lista os recursos de um padrão de desligue num período (ex.: ANALISAR).

    Usado pelo dashboard para o usuário clicar em "ANALISAR" e ver as VMs que
    precisam de revisão.
    """
    if not period:
        try:
            pr = await execute(
                "SELECT MAX(_billing_period) AS p FROM iceberg.{MART_NAMESPACE}.dest_dm_cost_saving_desligue_vm",
                timeout_s=60,
            )
            period = str((pr[0] or {}).get("p") or "") if pr else ""
        except Exception:
            pass
    if not period or not padrao:
        return PadraoRecursosResponse(billing_period=period or "", padrao=padrao or "", rows=[])
    list_filter = desligue_list_filter_sql()
    sql = f"""
    SELECT
        resource_name,
        connection_code,
        subscription_name,
        total_hours,
        saved_hours,
        saving_brl,
        is_desligue_programado
    FROM iceberg.{MART_NAMESPACE}.dest_dm_cost_saving_desligue_vm
    WHERE _billing_period = '{period}'
      AND padrao = '{padrao.replace("'", "''")}'
      AND {list_filter}
    ORDER BY saving_brl DESC
    """
    try:
        rows = await execute(sql, timeout_s=120)
    except Exception:
        return PadraoRecursosResponse(billing_period=period, padrao=padrao, rows=[])
    return PadraoRecursosResponse(
        billing_period=period,
        padrao=padrao,
        rows=[
            PadraoRecursoItem(
                resource_name=str(r["resource_name"]),
                connection_code=r.get("connection_code"),
                subscription_name=r.get("subscription_name"),
                total_hours=round(float(r["total_hours"] or 0), 2),
                saved_hours=round(float(r["saved_hours"] or 0), 2),
                saving_brl=round(float(r["saving_brl"] or 0), 2),
                is_desligue_programado=bool(r["is_desligue_programado"]),
            )
            for r in rows
        ],
    )


class SavingDepartamentoItem(BaseModel):
    departamento_nivel_4: str | None
    departamento_nivel_5: str | None
    vms: int
    saved_hours: float
    saving_brl: float


class VmDailyUsageItem(BaseModel):
    day: str
    hours: float
    cost_brl: float


class VmDailyUsageResponse(BaseModel):
    resource_name: str
    days: int
    total_hours: float
    total_cost_brl: float
    window_start: str | None = None
    window_end: str | None = None
    validation_start: str | None = None
    validation_end: str | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    lifecycle: str = "ok"
    is_recently_born: bool = False
    is_recently_died: bool = False
    active_days: int = 0
    window_hours: float = 0
    window_uptime_pct: float = 0
    monthly_uptime_pct: float | None = None
    min_active_days: int = 5
    min_active_hours_per_day: int = 4
    max_uptime_pct: float = 0.75
    padrao: str | None = None
    is_desligue_programado: bool = False
    rows: list[VmDailyUsageItem]


@router.get("/vm-usage-daily", response_model=VmDailyUsageResponse)
async def vm_usage_daily(resource_name: str, days: int = 30):
    """Consumo diário de uma VM nos últimos N dias (Gold FOCUS).

    Usado pelo dialog de detalhe do recurso — mostra o padrão de consumo dia a
    dia (horas e custo), permitindo ver quando a VM foi desligada/ligada.
    Inclui flags de ciclo de vida (nascimento/morte na janela de 30 dias).
    """
    from datetime import date as _date, timedelta as _td
    import calendar as _cal
    name = resource_name.strip()
    empty = VmDailyUsageResponse(
        resource_name=name,
        days=0,
        total_hours=0,
        total_cost_brl=0,
        rows=[],
    )
    if not name:
        return empty
    days = min(max(int(days), 7), 90)

    # Sempre usa o MÊS REALIZADO (anterior ao atual — fechado), nunca o mês
    # corrente (parcial). Ex.: em ago/2026, exibe julho/2026 completo.
    today = _date.today()
    done_m = today.month - 1 or 12
    done_y = today.year if today.month > 1 else today.year - 1
    done_period = f"{done_y:04d}{done_m:02d}"
    end_date = _date(done_y, done_m, _cal.monthrange(done_y, done_m)[1])  # fim do mês realizado
    start = (end_date - _td(days=days - 1)).strftime("%Y-%m-%d")
    window_start, window_end = _desligue_window_dates(done_period)
    validation_start, validation_end = _desligue_validation_dates(done_period)
    baseline_hours = float(_baseline_hours(done_period))

    # Períodos: realizado + 1 anterior (a janela de 30 dias termina no fim do
    # mês realizado e pode cruzar até ~2 dias do mês anterior).
    periods = [done_period]
    mm = done_m - 1 or 12
    yy = done_y if done_m > 1 else done_y - 1
    periods.append(f"{yy:04d}{mm:02d}")
    period_literals = ", ".join(f"'{p}'" for p in periods)

    # Lê da Dimon de CONSUMO DIÁRIO (regra consumo_diario_vm, materializada na
    # Camada Dimon de Custo — tela Tables → cost-relationship). A regra gera
    # dest_dm_cost_consumo_diario_vm com o consumo dia a dia de cada VM do mês
    # realizado; NUNCA varre a Gold por requisição (evita OOM no Trino).
    sql = f"""
    SELECT day, hours, cost_brl
    FROM iceberg.{MART_NAMESPACE}.dest_dm_cost_consumo_diario_vm
    WHERE LOWER(TRIM(resource_name)) = LOWER(TRIM('{name.replace("'", "''")}'))
      AND _billing_period IN ({period_literals})
      AND day >= '{start}' AND day <= '{end_date.isoformat()}'
    ORDER BY day
    """
    try:
        rows = await execute(sql, timeout_s=120)
    except Exception:
        rows = []
    active_rows = [r for r in rows if float(r.get("hours") or 0) > 0]
    first_seen = str(active_rows[0]["day"]) if active_rows else None
    last_seen = str(active_rows[-1]["day"]) if active_rows else None
    is_born, is_died, lifecycle = evaluate_vm_lifecycle(
        first_seen, last_seen, window_start, window_end
    )
    quality = evaluate_daily_quality(
        rows,
        validation_start=validation_start,
        validation_end=validation_end,
        total_hours=sum(float(r.get("hours") or 0) for r in rows),
        baseline_hours=baseline_hours,
    )
    padrao = None
    is_desligue = False
    try:
        mart_rows = await execute(
            f"""
            SELECT padrao, is_desligue_programado, active_days, window_hours,
                   window_uptime_pct, monthly_uptime_pct
            FROM iceberg.{MART_NAMESPACE}.dest_dm_cost_saving_desligue_vm
            WHERE _billing_period = '{done_period}'
              AND LOWER(TRIM(resource_name)) = LOWER(TRIM('{name.replace("'", "''")}'))
            LIMIT 1
            """,
            timeout_s=60,
        )
        if mart_rows:
            dimon = mart_rows[0]
            padrao = str(gold_mart.get("padrao") or "") or None
            is_desligue = bool(gold_mart.get("is_desligue_programado"))
            if gold_mart.get("active_days") is not None:
                quality["active_days"] = int(gold_mart.get("active_days") or 0)
            if gold_mart.get("window_hours") is not None:
                quality["window_hours"] = round(float(gold_mart.get("window_hours") or 0), 2)
            if gold_mart.get("window_uptime_pct") is not None:
                quality["window_uptime_pct"] = round(float(gold_mart.get("window_uptime_pct") or 0), 4)
            if gold_mart.get("monthly_uptime_pct") is not None:
                quality["monthly_uptime_pct"] = round(float(gold_mart.get("monthly_uptime_pct") or 0), 4)
    except Exception:
        pass
    total_h = sum(float(r["hours"] or 0) for r in rows)
    total_c = sum(float(r["cost_brl"] or 0) for r in rows)
    return VmDailyUsageResponse(
        resource_name=name,
        days=days,
        total_hours=round(total_h, 2),
        total_cost_brl=round(total_c, 2),
        window_start=window_start,
        window_end=window_end,
        validation_start=validation_start,
        validation_end=validation_end,
        first_seen=first_seen,
        last_seen=last_seen,
        lifecycle=lifecycle,
        is_recently_born=is_born,
        is_recently_died=is_died,
        active_days=int(quality["active_days"]),
        window_hours=float(quality["window_hours"]),
        window_uptime_pct=float(quality["window_uptime_pct"]),
        monthly_uptime_pct=quality.get("monthly_uptime_pct"),
        min_active_days=int(quality["min_active_days"]),
        min_active_hours_per_day=int(quality["min_active_hours_per_day"]),
        max_uptime_pct=float(quality["max_uptime_pct"]),
        padrao=padrao,
        is_desligue_programado=is_desligue,
        rows=[
            VmDailyUsageItem(
                day=str(r["day"]),
                hours=round(float(r["hours"] or 0), 2),
                cost_brl=round(float(r["cost_brl"] or 0), 2),
            )
            for r in rows
        ],
    )


class SavingDepartamentoResponse(BaseModel):
    billing_period: str
    rows: list[SavingDepartamentoItem]


@router.get("/saving-departamento", response_model=SavingDepartamentoResponse)
async def saving_departamento(period: str | None = None):
    """Saving por desligue agrupado por Departamento Nível 4 e 5 (Gold FOCUS).

    Junta a Dimon de saving (por resource_id) com a Gold dest_modelo_focus para
    obter o departamento_nivel_4/5 de cada recurso (1 departamento por recurso,
    via MAX na Gold). ``period`` opcional (YYYYMM); default = mês mais recente.
    """
    if not period:
        try:
            pr = await execute(
                "SELECT MAX(_billing_period) AS p FROM iceberg.{MART_NAMESPACE}.dest_dm_cost_saving_desligue_vm",
                timeout_s=60,
            )
            period = str((pr[0] or {}).get("p") or "") if pr else ""
        except Exception:
            pass
    if not period:
        return SavingDepartamentoResponse(billing_period="", rows=[])
    # Junta a Dimon (saving) com a Gold para obter departamento_nivel_4/5 por
    # recurso. O join é restrito aos resource_ids da Dimon (pequeno), então o
    # scan da Gold é filtrado pelo mês — sem varrer tudo por requisição.
    sql = f"""
    WITH dimon_vms AS (
        SELECT d.resource_id, d.saved_hours, d.saving_brl
        FROM iceberg.{MART_NAMESPACE}.dest_dm_cost_saving_desligue_vm d
        WHERE d._billing_period = '{period}'
          AND d.is_desligue_programado = TRUE
    ),
    gold_dept AS (
        SELECT
            LOWER(TRIM(g."ResourceId")) AS rid,
            MAX(g.departamento_nivel_4) AS n4,
            MAX(g.departamento_nivel_5) AS n5
        FROM iceberg.{layers.detail_layer()}.dest_modelo_focus g
        JOIN dimon_vms d ON LOWER(TRIM(g."ResourceId")) = LOWER(TRIM(d.resource_id))
        WHERE g._billing_period = '{period}'
        GROUP BY 1
    )
    SELECT
        COALESCE(g.n4, 'SEM DEPARTAMENTO') AS departamento_nivel_4,
        COALESCE(g.n5, 'SEM PROJETO') AS departamento_nivel_5,
        COUNT(DISTINCT d.resource_id) AS vms,
        COALESCE(SUM(d.saved_hours), 0) AS saved_hours,
        COALESCE(SUM(d.saving_brl), 0) AS saving_brl
    FROM dimon_vms d
    LEFT JOIN gold_dept g ON g.rid = LOWER(TRIM(d.resource_id))
    GROUP BY 1, 2
    ORDER BY saving_brl DESC
    """
    try:
        rows = await execute(sql, timeout_s=180)
    except Exception:
        return SavingDepartamentoResponse(billing_period=period, rows=[])
    return SavingDepartamentoResponse(
        billing_period=period,
        rows=[
            SavingDepartamentoItem(
                departamento_nivel_4=str(r["departamento_nivel_4"]),
                departamento_nivel_5=str(r["departamento_nivel_5"]),
                vms=int(r["vms"] or 0),
                saved_hours=round(float(r["saved_hours"] or 0), 0),
                saving_brl=round(float(r["saving_brl"] or 0), 2),
            )
            for r in rows
        ],
    )


@router.post("/reprocess-history")
async def reprocess_saving_history(db: AsyncSession = Depends(get_db)):
    """Materializa a Dimon de saving por desligue MÊS A MÊS (jan → mês atual).

    IMPORTANTE: a query compara cada mês alvo com os meses anteriores para
    classificar o padrão de desligue, por isso DEVE ser reprocessada mês a mês.
    Este endpoint roda a materialização histórica completa (janeiro até o mês
    anterior ao atual) e pode demorar vários minutos.
    """
    from app.services.gold_materialization import SAVING_DIMON_CODE, materialize_saving_history
    from app.models.gold_mart_rule import GoldMartRule
    from app.models.etl_task import EtlTask

    task = EtlTask(
        task_type="dimon_reprocess",
        status="RUNNING",
        phase="Reprocessando saving por desligue mês a mês (histórico)",
        started_at=datetime.now(timezone.utc),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    try:
        saving_rule = (await db.execute(
            select(GoldMartRule)
            .options(selectinload(GoldMartRule.sources))
            .where(GoldMartRule.code == SAVING_DIMON_CODE)
        )).scalar_one_or_none()
        summary = await materialize_saving_history(
            sources=list(saving_rule.sources) if saving_rule else None
        )
        task.status = "COMPLETED"
        task.phase = f"Saving por desligue: {len(summary['periods'])} meses materializados"
        task.rows_destination = sum(summary["rows"].values())
        task.result_json = {"event_log": [{"at": datetime.now(timezone.utc).isoformat(), "level": "INFO", "message": "Histórico materializado mês a mês"}], **summary}
        task.finished_at = datetime.now(timezone.utc)
        await db.commit()
        return {"task_id": task.id, "status": "COMPLETED", **summary}
    except Exception as e:
        logger.error("Erro no reprocess histórico de saving: %s", e)
        task.status = "FAILED"
        task.phase = f"Erro: {str(e)[:300]}"
        task.finished_at = datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(status_code=500, detail=str(e))


class OpportunityItem(BaseModel):
    resource_id: str | None
    resource_name: str | None
    resource_type: str  # VM | VMSS
    connection_code: str | None
    subscription_name: str | None
    billing_period: str
    total_hours: float
    baseline_hours: float
    uptime_pct: float
    potential_saved_hours: float
    effective_price_hour: float
    opportunity_brl: float
    is_new_vm: bool


class OpportunityResponse(BaseModel):
    billing_period: str
    subscriptions: list[str]
    rows: list[OpportunityItem]
    total_opportunity_brl: float
    vm_count: int
    vmss_count: int


@router.get("/opportunity-subscriptions")
async def opportunity_subscriptions():
    """Lista as subscriptions disponíveis na Dimon de saving (mês corrente) com
    contagem de recursos, para o seletor do dashboard."""
    from datetime import date as _date
    today = _date.today()
    period = f"{today.year:04d}{today.month:02d}"
    sql = f"""
    SELECT
        subscription_name,
        COUNT(*) AS resources,
        COALESCE(SUM(CASE WHEN resource_type = 'VMSS' THEN 1 ELSE 0 END), 0) AS vmss
    FROM iceberg.{MART_NAMESPACE}.dest_dm_cost_saving_desligue_vm
    WHERE _billing_period = '{period}'
      AND LOWER(COALESCE(CAST(resource_name AS VARCHAR), '')) NOT LIKE 'azure plan%'
    GROUP BY subscription_name
    ORDER BY resources DESC
    """
    rows = await execute(sql, timeout_s=120)
    return {
        "billing_period": period,
        "subscriptions": [
            {"name": str(r["subscription_name"] or "Sem subscription"), "resources": int(r["resources"] or 0), "vmss": int(r["vmss"] or 0)}
            for r in rows
        ],
    }


@router.post("/opportunity", response_model=OpportunityResponse)
async def opportunity_savings(body: SavingsFilter, db: AsyncSession = Depends(get_db)):
    """Oportunidade de saving: recursos que são VMs/VMSS e poderiam ser desligados.

    Filtros: `subscriptions` = nomes exatos de subscriptions selecionadas no
    dashboard (vazio → keywords hml/dev/hmp); `resource_groups` = tipos
    (VM/VMSS). Exclui recursos 'Azure plan...' (não são VMs desligáveis) e
    recursos sem horas potenciais. Ordena do maior potencial para o menor.
    """
    selected_subs = [str(k).strip().lower() for k in (body.subscriptions or []) if str(k).strip()]
    use_keywords = not selected_subs
    keywords = ["hml", "dev", "hmp"]
    rtypes = [str(t).upper() for t in (body.resource_groups or []) if str(t).strip().upper() in ("VM", "VMSS")]

    from datetime import date as _date
    today = _date.today()
    period = f"{today.year:04d}{today.month:02d}"

    sql = f"""
    SELECT
        resource_id,
        resource_name,
        resource_type,
        connection_code,
        subscription_name,
        billing_period,
        total_hours,
        effective_price_hour,
        baseline_hours,
        saved_hours,
        saving_brl,
        is_new_vm
    FROM iceberg.{MART_NAMESPACE}.dest_dm_cost_saving_desligue_vm
    WHERE _billing_period = '{period}'
      AND LOWER(COALESCE(CAST(resource_name AS VARCHAR), '')) NOT LIKE 'azure plan%'
    """
    try:
        rows = await execute(sql, timeout_s=120)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Dimon de saving indisponível: {str(exc)[:300]}")

    out: list[OpportunityItem] = []
    subs_seen: set[str] = set()
    for r in rows:
        sub = str(r.get("subscription_name") or "").strip()
        rtype = str(r.get("resource_type") or "VM").upper()
        if sub:
            low = sub.lower()
            if use_keywords:
                if not any(k in low for k in keywords):
                    continue
            elif low not in selected_subs:
                continue
        elif use_keywords:
            continue  # sem subscription e filtrando por keywords → fora
        if rtypes and rtype not in rtypes:
            continue
        baseline = float(r.get("baseline_hours") or 0)
        hours = float(r.get("total_hours") or 0)
        price = float(r.get("effective_price_hour") or 0)
        pot_hours = max(0.0, baseline - hours)
        if pot_hours <= 0:
            continue  # sem horas potenciais não é oportunidade
        opp = pot_hours * price
        if sub:
            subs_seen.add(sub)
        out.append(OpportunityItem(
            resource_id=r.get("resource_id"),
            resource_name=r.get("resource_name"),
            resource_type=rtype,
            connection_code=r.get("connection_code"),
            subscription_name=sub or None,
            billing_period=str(r.get("billing_period") or period),
            total_hours=round(hours, 2),
            baseline_hours=baseline,
            uptime_pct=round((hours / baseline * 100) if baseline else 0, 2),
            potential_saved_hours=round(pot_hours, 2),
            effective_price_hour=price,
            opportunity_brl=round(opp, 2),
            is_new_vm=bool(r.get("is_new_vm")),
        ))

    out.sort(key=lambda i: i.opportunity_brl, reverse=True)
    return OpportunityResponse(
        billing_period=period,
        subscriptions=sorted(subs_seen),
        rows=out,
        total_opportunity_brl=round(sum(i.opportunity_brl for i in out), 2),
        vm_count=sum(1 for i in out if i.resource_type == "VM"),
        vmss_count=sum(1 for i in out if i.resource_type == "VMSS"),
    )


async def _ensure_setup(db: AsyncSession) -> None:
    conn = (
        await db.execute(select(SourceConnection).where(SourceConnection.connector_type == CONNECTOR_TYPE).limit(1))
    ).scalar_one_or_none()
    if not conn:
        raise HTTPException(
            status_code=422,
            detail="Nenhuma conexão Azure Automation configurada. Crie uma conexão em Connections (Ferramentas Cloud → Azure Automation).",
        )


DEFAULT_SAVING_SQL = """
SELECT
    b._billing_period,
    b.cloud_origem AS connection_code,
    b."ResourceId" AS resource_id,
    b."ResourceName" AS resource_name,
    b."SubAccountId" AS subscription_id,
    _billing_period AS billing_period,
    SUM(COALESCE(TRY_CAST(b."ConsumedQuantity" AS DOUBLE), 0)) AS hours_billed,
    SUM(COALESCE(TRY_CAST(b."EffectiveCost" AS DOUBLE), 0)) AS total_effective_cost,
    COUNT(DISTINCT b."ResourceId") AS vm_count
FROM iceberg.{layers.detail_layer()}.dest_modelo_focus b
WHERE LOWER(CAST(b."ServiceSubcategory" AS VARCHAR)) LIKE '%virtual machines%'
  AND (b."ChargeCategory" = 'Usage' OR b."ChargeCategory" IS NULL)
  AND LOWER(COALESCE(CAST(b."ResourceName" AS VARCHAR), '')) NOT LIKE '%savings plan%'
  AND LOWER(COALESCE(CAST(b."ResourceName" AS VARCHAR), '')) NOT LIKE '%reserved vm instance%'
GROUP BY
    b._billing_period,
    b.cloud_origem,
    b."ResourceId",
    b."ResourceName",
    b."SubAccountId",
    _billing_period
"""


@router.post("/seed-dimon")
async def seed_saving_dimon(db: AsyncSession = Depends(get_db)):
    """Cria (se não existir) a regra Dimon de saving por desligue de VM."""
    from app.models.gold_mart_rule import GoldMartRule
    from app.services.gold_materialization import normalize_code

    code = normalize_code("saving_desligue_vm")
    existing = (await db.execute(select(GoldMartRule).where(GoldMartRule.code == code))).scalar_one_or_none()
    if existing:
        return {"status": "EXISTS", "code": code, "rule_id": existing.id, "message": "Regra Dimon já existe."}

    rule = GoldMartRule(
        code=code,
        name="Saving por desligue de VMs",
        description="Saving por VM/mês baseado em horas de consumo vs baseline 24x7 (query custom).",
        active=True,
        dimensions=["_billing_period", "connection_code", "resource_id", "resource_name", "subscription_id", "billing_period"],
        measure_columns=["hours_billed", "list_unit_price_hour", "vm_count"],
        sql_template=DEFAULT_SAVING_SQL,
        sources=[],
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return {"status": "CREATED", "code": code, "rule_id": rule.id, "message": "Regra Dimon criada."}


TAG_SNAPSHOT_SQL = """
WITH vm_tags AS (
    SELECT
        "ResourceId" AS resource_id,
        MAX("ResourceName") AS resource_name,
        MAX(cloud_origem) AS connection_code,
        _billing_period AS billing_period,
        MAX(
            CASE
                WHEN LOWER(COALESCE(json_extract_scalar("Tags", '$.shutdown'), '')) = 'auto' THEN TRUE
                WHEN LOWER(COALESCE(json_extract_scalar("Tags", '$.Shutdown'), '')) = 'auto' THEN TRUE
                WHEN LOWER(COALESCE(json_extract_scalar("Tags", '$.shutdownSchedule'), '')) = 'auto' THEN TRUE
                WHEN LOWER(COALESCE(json_extract_scalar("Tags", '$.startStop'), '')) = 'auto' THEN TRUE
                WHEN LOWER(COALESCE(json_extract_scalar("Tags", '$.schedule'), '')) IN ('auto', 'desligue', 'shutdown') THEN TRUE
                ELSE FALSE
            END
        ) AS has_shutdown_auto
    FROM iceberg.{layers.detail_layer()}.dest_modelo_focus
    WHERE LOWER(CAST("ServiceSubcategory" AS VARCHAR)) LIKE '%virtual machines%'
      AND LOWER(COALESCE(CAST("ResourceName" AS VARCHAR), '')) NOT LIKE '%savings plan%'
      AND LOWER(COALESCE(CAST("ResourceName" AS VARCHAR), '')) NOT LIKE '%reserved vm instance%'
      AND _billing_period = date_format(date_trunc('month', CURRENT_DATE), '%Y%m')
    GROUP BY "ResourceId", _billing_period
)
SELECT
    resource_id,
    resource_name,
    connection_code,
    billing_period AS _billing_period,
    has_shutdown_auto
FROM vm_tags
"""


@router.post("/seed-tag-dimon")
async def seed_tag_snapshot_dimon(db: AsyncSession = Depends(get_db)):
    """Cria a Dimon de snapshot de tags shutdown=auto por recurso (modo snapshot)."""
    from app.models.gold_mart_rule import GoldMartRule
    from app.services.gold_materialization import normalize_code

    code = normalize_code("snapshot_tags_automacao")
    existing = (await db.execute(select(GoldMartRule).where(GoldMartRule.code == code))).scalar_one_or_none()
    if existing:
        return {"status": "EXISTS", "code": code, "rule_id": existing.id, "message": "Regra Dimon de tags já existe."}

    rule = GoldMartRule(
        code=code,
        name="Snapshot tags shutdown=auto",
        description="Snapshot diário da tag shutdown=auto por recurso (modo snapshot: append datado sem replace).",
        active=True,
        dimensions=["resource_id", "resource_name", "connection_code", "_billing_period", "has_shutdown_auto"],
        measure_columns=[],
        sql_template=TAG_SNAPSHOT_SQL,
        snapshot_mode=True,
        snapshot_col="_snapshot_date",
        sources=[],
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return {"status": "CREATED", "code": code, "rule_id": rule.id, "message": "Regra Dimon de tags criada (modo snapshot)."}


class TagDeltaItem(BaseModel):
    resource_id: str | None
    resource_name: str | None
    connection_code: str | None
    change_type: str  # SAIU_DA_AUTOMACAO | ENTROU_NA_AUTOMACAO | MANTIDA
    latest_snapshot: str | None
    previous_snapshot: str | None
    days_out: int | None
    cost_since_removal: float = 0.0


class TagDeltaResponse(BaseModel):
    latest_snapshot: str | None
    previous_snapshot: str | None
    rows: list[TagDeltaItem]
    total_exited: int
    total_entered: int
    total_cost_since_removal: float
    total_resources: int = 0
    total_with_tag: int = 0


@router.get("/tag-delta", response_model=TagDeltaResponse)
async def tag_delta(db: AsyncSession = Depends(get_db)):
    """Compara os snapshots de tags (Dimon snapshot_tags_automacao) mais recentes.

    Recurso que tinha `shutdown=auto` no snapshot anterior e não tem mais no
    atual (ou sumiu) = SAIU da automação. Estima o custo desde a saída usando o
    preço on-demand médio da VM no billing.
    """
    dates_sql = """
    SELECT DISTINCT CAST(_snapshot_date AS VARCHAR) AS d
    FROM iceberg.{MART_NAMESPACE}.dest_dm_cost_snapshot_tags_automacao
    ORDER BY d DESC
    """
    try:
        dates_rows = await execute(dates_sql, timeout_s=60)
    except Exception:
        return TagDeltaResponse(latest_snapshot=None, previous_snapshot=None, rows=[], total_exited=0, total_entered=0, total_cost_since_removal=0.0)

    dates = [str(r["d"])[:10] for r in dates_rows if r.get("d")]
    if len(dates) < 2:
        # Apenas um snapshot: mostra o estado ATUAL (quantos recursos com a tag
        # shutdown=auto) em vez de vazio. O delta D vs D-1 aparece com 2 coletas.
        latest = dates[0] if dates else None
        st = {"total": 0, "with_tag": 0}
        if latest:
            try:
                st_rows = await execute(f"""
                SELECT COUNT(*) AS total,
                       COUNT(CASE WHEN has_shutdown_auto THEN 1 END) AS with_tag
                FROM iceberg.{MART_NAMESPACE}.dest_dm_cost_snapshot_tags_automacao
                WHERE CAST(_snapshot_date AS VARCHAR) LIKE '{latest}%'
                """, timeout_s=60)
                if st_rows:
                    st = {"total": int(st_rows[0].get("total") or 0), "with_tag": int(st_rows[0].get("with_tag") or 0)}
            except Exception:
                pass
        return TagDeltaResponse(
            latest_snapshot=latest,
            previous_snapshot=None,
            rows=[],
            total_exited=0,
            total_entered=0,
            total_cost_since_removal=0.0,
            total_resources=st["total"],
            total_with_tag=st["with_tag"],
        )

    latest, previous = dates[0], dates[1]

    delta_sql = f"""
    WITH hoje AS (
        SELECT resource_id, resource_name, connection_code, has_shutdown_auto
        FROM iceberg.{MART_NAMESPACE}.dest_dm_cost_snapshot_tags_automacao
        WHERE CAST(_snapshot_date AS VARCHAR) LIKE '{latest}%'
    ),
    ontem AS (
        SELECT resource_id, resource_name, connection_code, has_shutdown_auto
        FROM iceberg.{MART_NAMESPACE}.dest_dm_cost_snapshot_tags_automacao
        WHERE CAST(_snapshot_date AS VARCHAR) LIKE '{previous}%'
    )
    SELECT
        COALESCE(h.resource_id, o.resource_id) AS resource_id,
        COALESCE(h.resource_name, o.resource_name) AS resource_name,
        COALESCE(h.connection_code, o.connection_code) AS connection_code,
        CASE
            WHEN COALESCE(o.has_shutdown_auto, FALSE) AND NOT COALESCE(h.has_shutdown_auto, FALSE) THEN 'SAIU_DA_AUTOMACAO'
            WHEN NOT COALESCE(o.has_shutdown_auto, FALSE) AND COALESCE(h.has_shutdown_auto, FALSE) THEN 'ENTROU_NA_AUTOMACAO'
            ELSE 'MANTIDA'
        END AS change_type
    FROM hoje h
    FULL OUTER JOIN ontem o ON LOWER(TRIM(h.resource_id)) = LOWER(TRIM(o.resource_id))
    WHERE COALESCE(o.has_shutdown_auto, FALSE) <> COALESCE(h.has_shutdown_auto, FALSE)
    """
    rows = await execute(delta_sql, timeout_s=120)

    items: list[TagDeltaItem] = []
    total_cost = 0.0
    for r in rows:
        change_type = str(r["change_type"] or "MANTIDA")
        cost_since = 0.0
        days_out: int | None = None
        if change_type == "SAIU_DA_AUTOMACAO":
            cost_since, days_out = await _cost_since_removal(str(r["resource_id"] or ""), "", latest)
            total_cost += cost_since
        items.append(TagDeltaItem(
            resource_id=r["resource_id"],
            resource_name=r["resource_name"],
            connection_code=r["connection_code"],
            change_type=change_type,
            latest_snapshot=latest,
            previous_snapshot=previous,
            days_out=days_out,
            cost_since_removal=round(cost_since, 2),
        ))

    exited = sum(1 for i in items if i.change_type == "SAIU_DA_AUTOMACAO")
    entered = sum(1 for i in items if i.change_type == "ENTROU_NA_AUTOMACAO")
    return TagDeltaResponse(
        latest_snapshot=latest,
        previous_snapshot=previous,
        rows=items,
        total_exited=exited,
        total_entered=entered,
        total_cost_since_removal=round(total_cost, 2),
        total_resources=len(items) + 0,
        total_with_tag=sum(1 for i in items if i.change_type != "SAIU_DA_AUTOMACAO"),
    )


# ---------------------------------------------------------------------------
# Coleta de INVENTÁRIO Azure Automation (jobSchedules + schedules + runbooks)
# ---------------------------------------------------------------------------

@router.post("/collect-inventory")
async def collect_automation_inventory_endpoint(
    connection_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Dispara coleta de inventário de Automation Accounts (jobSchedules, schedules, runbooks).
    
    Persiste na Bronze como snapshot diário para análise de savings.
    """
    from app.services.azure_automation_service import run_automation_inventory_collection
    from app.models.etl_task import EtlTask
    
    # Criar task
    task = EtlTask(
        connection_id=connection_id,
        task_type="azure_automation_inventory",
        status="RUNNING",
        phase="Iniciando coleta de inventário Azure Automation",
        started_at=datetime.now(timezone.utc),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    
    # Executar coleta síncrona (pode demorar)
    try:
        result = run_automation_inventory_collection(
            session=None,
            connection_id=connection_id,
            task_id=task.id,
        )
        return {
            "task_id": task.id,
            "status": "COMPLETED",
            **result,
        }
    except Exception as e:
        logger.error("Erro na coleta de inventário: %s", e)
        task.status = "FAILED"
        task.phase = f"Erro: {str(e)[:200]}"
        task.finished_at = datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(status_code=500, detail=str(e))
