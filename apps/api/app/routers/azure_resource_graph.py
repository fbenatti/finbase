"""Azure Resource Graph — setup das tabelas destino e endpoints da aba de governança.

Setup idempotente (espelho do scheduled_shutdown): ao criar a conexão do tipo
azure_resource_graph, cria automaticamente:

- dest_cloud_resource_inventory  (MODELO INVENTÁRIO CLOUD)  ← Bronze arg/inventory/
- dest_cloud_orphan_resources    (MODELO RECURSOS ÓRFÃOS)   ← Bronze arg/orphans/<rule>/

A aba "Recursos Órfãos" da Governança consulta a Gold via Trino e sobrepõe as
anotações de governança (PostgreSQL orphan_annotation) — ORFAO (padrão),
EM_ANALISE ou EXCECAO.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.destination import DestinationTable, DestinationColumn, SourceDestinationMapping
from app.models.source_connection import SourceConnection
from app.models.orphan_annotation import OrphanAnnotation
from app.services.trino_service import execute as trino_execute
from app.services import lakehouse_layers as layers
from app.services.gold_materialization import GOLD

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/azure-resource-graph", tags=["azure-resource-graph"])

CONNECTOR_TYPE = "azure_resource_graph"
CLOUD_SCOPE = "azure"

INVENTORY_DEST_NAME = "MODELO INVENTÁRIO CLOUD"
INVENTORY_DEST_PHYSICAL = "dest_cloud_resource_inventory"
ORPHAN_DEST_NAME = "MODELO RECURSOS ÓRFÃOS"
ORPHAN_DEST_PHYSICAL = "dest_cloud_orphan_resources"

_COMMON_INTERNAL = [
    {"column_name": "_billing_period", "data_type": "TEXT", "is_required": True, "position": 1000},
    {"column_name": "source_connection_id", "data_type": "BIGINT", "is_required": True, "position": 1010},
    {"column_name": "cloud_origem", "data_type": "TEXT", "is_required": True, "position": 1020},
    {"column_name": "tipo", "data_type": "TEXT", "is_required": True, "position": 1030},
]

INVENTORY_COLUMNS: list[dict[str, Any]] = [
    {"column_name": "resource_id", "data_type": "TEXT", "is_required": True, "position": 10},
    {"column_name": "resource_name", "data_type": "TEXT", "is_required": False, "position": 20},
    {"column_name": "resource_type", "data_type": "TEXT", "is_required": False, "position": 30},
    {"column_name": "subscription_id", "data_type": "TEXT", "is_required": False, "position": 40},
    {"column_name": "resource_group", "data_type": "TEXT", "is_required": False, "position": 50},
    {"column_name": "location", "data_type": "TEXT", "is_required": False, "position": 60},
    {"column_name": "sku", "data_type": "TEXT", "is_required": False, "position": 70},
    {"column_name": "tags", "data_type": "TEXT", "is_required": False, "position": 80},
    {"column_name": "power_state", "data_type": "TEXT", "is_required": False, "position": 90},
    {"column_name": "created_at", "data_type": "TEXT", "is_required": False, "position": 100},
    {"column_name": "snapshot_date", "data_type": "TEXT", "is_required": True, "position": 110},
    *_COMMON_INTERNAL,
]

ORPHAN_COLUMNS: list[dict[str, Any]] = [
    {"column_name": "resource_id", "data_type": "TEXT", "is_required": True, "position": 10},
    {"column_name": "resource_name", "data_type": "TEXT", "is_required": False, "position": 20},
    {"column_name": "resource_type", "data_type": "TEXT", "is_required": False, "position": 30},
    {"column_name": "subscription_id", "data_type": "TEXT", "is_required": False, "position": 40},
    {"column_name": "resource_group", "data_type": "TEXT", "is_required": False, "position": 50},
    {"column_name": "location", "data_type": "TEXT", "is_required": False, "position": 60},
    {"column_name": "sku", "data_type": "TEXT", "is_required": False, "position": 70},
    {"column_name": "disk_size_gb", "data_type": "TEXT", "is_required": False, "position": 80},
    {"column_name": "ip_address", "data_type": "TEXT", "is_required": False, "position": 90},
    {"column_name": "source_disk", "data_type": "TEXT", "is_required": False, "position": 100},
    {"column_name": "tags", "data_type": "TEXT", "is_required": False, "position": 110},
    {"column_name": "created_at", "data_type": "TEXT", "is_required": False, "position": 120},
    {"column_name": "orphan_rule", "data_type": "TEXT", "is_required": True, "position": 130},
    {"column_name": "orphan_reason", "data_type": "TEXT", "is_required": False, "position": 140},
    {"column_name": "tipo_recurso", "data_type": "TEXT", "is_required": False, "position": 145},
    {"column_name": "age_days", "data_type": "BIGINT", "is_required": False, "position": 150},
    {"column_name": "status_flag", "data_type": "TEXT", "is_required": False, "position": 160},
    {"column_name": "estimated_monthly_cost_brl", "data_type": "DOUBLE", "is_required": False, "position": 170},
    {"column_name": "snapshot_date", "data_type": "TEXT", "is_required": True, "position": 180},
    *_COMMON_INTERNAL,
]

_INVENTORY_SOURCES = [
    "resource_id", "resource_name", "resource_type", "subscription_id",
    "resource_group", "location", "sku", "tags", "power_state", "created_at",
    "snapshot_date",
]
_ORPHAN_SOURCES = [
    "resource_id", "resource_name", "resource_type", "subscription_id",
    "resource_group", "location", "sku", "disk_size_gb", "ip_address",
    "source_disk", "tags", "created_at", "orphan_rule", "orphan_reason",
    "tipo_recurso", "age_days", "status_flag", "estimated_monthly_cost_brl",
    "snapshot_date",
]

ORPHAN_STATUSES = ("ORFAO", "EM_ANALISE", "EXCECAO")


def _mappings_for(sources: list[str]) -> list[dict[str, Any]]:
    return [
        {"source_column": col, "destination_column": col, "transform_type": "RENAME",
         "priority": (i + 1) * 10}
        for i, col in enumerate(sources)
    ]


INVENTORY_MAPPINGS = _mappings_for(_INVENTORY_SOURCES)
ORPHAN_MAPPINGS = _mappings_for(_ORPHAN_SOURCES)

# ── Regra Gold: custo por recurso órfão (join com FOCUS) ──────────────────
# Agrupa os órfãos (Gold coletada via ARG) e cruza com a Gold FOCUS pelo
# resource_id dentro do mesmo billing period. A materialização cria
# dest_dm_cost_<code> pré-agregada — a governança lê custo sem varrer o FOCUS.
ORPHAN_COST_DIMON_CODE = "custo_recursos_orfaos"
ORPHAN_COST_DIMON_TABLE = f"dest_dm_cost_{ORPHAN_COST_DIMON_CODE}"

ORPHAN_COST_SQL_TEMPLATE = """
-- O export FOCUS pode conter o mesmo recurso em duas variantes de case
-- (resourceGroups/PRO-MONITOR vs pro-monitor). Deduplicamos mantendo UMA
-- variante canônica (MIN) por (lower(resourceid), período) para não somar
-- linhas em dobro no join com os órfãos (ARG devolve o case armazenado).
-- O filtro por orphan_ids roda ANTES do window function, evitando varrer o
-- FOCUS inteiro (~15-20M linhas) — apenas as linhas que casam com órfãos.
WITH orphan_ids AS (
        SELECT DISTINCT lower(resource_id) AS rid, "_billing_period",
                     CASE cloud_origem
                         WHEN 'azure-resource-graph-4p' THEN 'azure-4p'
                         WHEN 'azure-resource-graph-ti' THEN 'azure-ti'
                         ELSE cloud_origem
                     END AS focus_cloud_origem
    FROM iceberg.{layers.detail_layer()}.dest_cloud_orphan_resources
    WHERE "_billing_period" = (
        SELECT MAX("_billing_period") FROM iceberg.silver.dest_cloud_orphan_resources
    )
), focus_dedup AS (
    SELECT resourceid, "_billing_period", cloud_origem, billedcost, effectivecost, realizado
    FROM (
        SELECT f.*, MIN(f.resourceid) OVER (
            PARTITION BY lower(f.resourceid), f."_billing_period", f.cloud_origem
        ) AS canonical
        FROM iceberg.{layers.detail_layer()}.dest_modelo_focus f
        JOIN orphan_ids o
          ON lower(f.resourceid) = o.rid
         AND f."_billing_period" = o."_billing_period"
         AND lower(f.cloud_origem) = lower(o.focus_cloud_origem)
    ) WHERE resourceid = canonical
)
SELECT
    o."_billing_period",
    o.resource_id,
    o.resource_name,
    o.orphan_rule,
    o.tipo_recurso,
    o.subscription_id,
    o.resource_group,
    o.disk_size_gb,
    o.age_days,
    o.snapshot_date,
    o.cloud_origem,
    o.source_connection_id,
    COUNT(f.resourceid) AS focus_rows,
    ROUND(SUM(COALESCE(TRY_CAST(f.billedcost AS DOUBLE), 0)), 2) AS billedcost,
    ROUND(SUM(COALESCE(TRY_CAST(f.effectivecost AS DOUBLE), 0)), 2) AS effectivecost,
    -- Custo canônico da plataforma: coluna "realizado" (BRL com taxas/markup
    -- aplicados pelas regras de negócio; tipo='realizado' no FOCUS gold).
    ROUND(SUM(COALESCE(TRY_CAST(f.realizado AS DOUBLE), 0)), 2) AS cost_brl
FROM iceberg.silver.dest_cloud_orphan_resources o
LEFT JOIN focus_dedup f
  -- case-insensitive: o ARG devolve o resource group no case armazenado,
  -- enquanto o export de custo FOCUS normaliza para UPPER/lower.
  ON LOWER(f.resourceid) = LOWER(o.resource_id)
 AND f."_billing_period" = o."_billing_period"
 AND lower(f.cloud_origem) = lower(CASE o.cloud_origem
     WHEN 'azure-resource-graph-4p' THEN 'azure-4p'
     WHEN 'azure-resource-graph-ti' THEN 'azure-ti'
     ELSE o.cloud_origem
 END)
-- Restringe ao período mais recente dos órfãos (poda de partição no FOCUS).
WHERE o."_billing_period" = (
    SELECT MAX("_billing_period") FROM iceberg.silver.dest_cloud_orphan_resources
)
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12
"""


async def seed_orphan_cost_gold(db: AsyncSession) -> dict[str, Any]:
    """Cria (idempotente) a regra Gold de custo por recurso órfão."""
    from app.models.gold_mart_rule import GoldMartRule
    from app.services.gold_materialization import normalize_code

    code = normalize_code(ORPHAN_COST_DIMON_CODE)
    existing = (await db.execute(select(GoldMartRule).where(GoldMartRule.code == code))).scalar_one_or_none()
    dimensions = ["_billing_period", "resource_id", "resource_name", "orphan_rule", "tipo_recurso",
                  "subscription_id", "resource_group", "disk_size_gb", "age_days", "snapshot_date",
                  "cloud_origem", "source_connection_id"]
    measures = ["focus_rows", "billedcost", "effectivecost", "cost_brl"]
    if existing:
        changed = (
            existing.sql_template != ORPHAN_COST_SQL_TEMPLATE
            or existing.dimensions != dimensions
            or existing.measure_columns != measures
        )
        if changed:
            existing.dimensions = dimensions
            existing.measure_columns = measures
            existing.sql_template = ORPHAN_COST_SQL_TEMPLATE
            existing.materialization_status = "PENDING"
            existing.materialization_error = None
            await db.commit()
        return {"status": "UPDATED" if changed else "EXISTS", "code": code, "rule_id": existing.id, "table": existing.materialization_table}
    rule = GoldMartRule(
        code=code,
        name="Custo por recurso órfão (join FOCUS)",
        description=(
            "Custo mensal por recurso órfão coletado via Azure Resource Graph, cruzado "
            "com a Gold FOCUS por resource_id no mesmo billing period (billed/effective/BRL)."
        ),
        active=True,
        dimensions=dimensions,
        measure_columns=measures,
        sql_template=ORPHAN_COST_SQL_TEMPLATE,
        sources=[],
    )
    db.add(rule)
    await db.flush()
    rule.materialization_table = f"dest_dm_cost_{code}"
    await db.commit()
    return {"status": "CREATED", "code": code, "rule_id": rule.id, "table": rule.materialization_table}


async def _ensure_destination(db: AsyncSession, name: str, physical: str, description: str,
                              columns: list[dict[str, Any]]) -> DestinationTable:
    """Cria/atualiza (idempotente) uma destination_table + colunas (async)."""
    dest = (await db.execute(
        select(DestinationTable).where(DestinationTable.physical_name == physical)
    )).scalar_one_or_none()
    if not dest:
        dest = DestinationTable(name=name, physical_name=physical, description=description, active=True)
        db.add(dest)
        await db.flush()
    existing = {
        c.column_name for c in (
            await db.execute(
                select(DestinationColumn).where(DestinationColumn.destination_table_id == dest.id)
            )
        ).scalars().all()
    }
    for col in columns:
        if col["column_name"] not in existing:
            db.add(DestinationColumn(destination_table_id=dest.id, **col))
    await db.flush()
    return dest


async def _setup_for_connection(db: AsyncSession, conn: SourceConnection) -> dict[str, Any]:
    """Setup idempotente: tabelas destino + mappings para uma conexão ARG."""
    inv_dest = await _ensure_destination(
        db, INVENTORY_DEST_NAME, INVENTORY_DEST_PHYSICAL,
        "Inventário de recursos Azure via Resource Graph (multi-cloud no futuro: coluna cloud_scope)",
        INVENTORY_COLUMNS,
    )
    orphan_dest = await _ensure_destination(
        db, ORPHAN_DEST_NAME, ORPHAN_DEST_PHYSICAL,
        "Recursos órfãos do Azure via Resource Graph (discos, VMs, IPs, snapshots, LBs)",
        ORPHAN_COLUMNS,
    )

    for dest, mappings in ((inv_dest, INVENTORY_MAPPINGS), (orphan_dest, ORPHAN_MAPPINGS)):
        existing = {
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
        for m in mappings:
            key = (m["source_column"], m["destination_column"])
            if key not in existing:
                db.add(SourceDestinationMapping(
                    source_connection_id=conn.id,
                    destination_table_id=dest.id,
                    name=f"{m['source_column']} -> {m['destination_column']}",
                    **m,
                    active=True,
                ))

    # Regra Gold de custo por órfão (idempotente) — join com a Gold FOCUS.
    try:
        await seed_orphan_cost_gold(db)
    except Exception:
        logger.exception("Falha ao criar regra Gold de custo de órfãos")

    await db.commit()
    return {
        "connection_id": conn.id,
        "destination_tables": [INVENTORY_DEST_PHYSICAL, ORPHAN_DEST_PHYSICAL],
        "dimon_rule": ORPHAN_COST_DIMON_CODE,
        "message": "Setup concluído: tabelas destino e mappings criados.",
    }


class SetupResponse(BaseModel):
    connection_id: int | None = None
    destination_tables: list[str]
    message: str


class SetupRequest(BaseModel):
    connection_id: int | None = None


@router.post("/setup", response_model=SetupResponse)
async def setup_resource_graph(body: SetupRequest | None = None, db: AsyncSession = Depends(get_db)):
    """Configura tabelas destino + mappings para conexões azure_resource_graph."""
    conn_id = (body and body.connection_id) or None
    if conn_id:
        conn = await db.get(SourceConnection, conn_id)
        if not conn or conn.connector_type != CONNECTOR_TYPE:
            raise HTTPException(status_code=404, detail="Conexão Azure Resource Graph não encontrada")
        result = await _setup_for_connection(db, conn)
        return SetupResponse(
            connection_id=conn.id,
            destination_tables=result["destination_tables"],
            message=result["message"],
        )

    conns = (await db.execute(
        select(SourceConnection).where(SourceConnection.connector_type == CONNECTOR_TYPE)
    )).scalars().all()
    if not conns:
        raise HTTPException(
            status_code=422,
            detail="Nenhuma conexão Azure Resource Graph configurada. Crie uma em Connections (Ferramentas Cloud → Azure Resource Graph).",
        )
    last = None
    for conn in conns:
        last = await _setup_for_connection(db, conn)
    return SetupResponse(connection_id=None, destination_tables=last["destination_tables"], message=last["message"])


@router.post("/seed-gold")
async def seed_dimon_orphan_cost(db: AsyncSession = Depends(get_db)):
    """Cria (idempotente) a regra Gold de custo por recurso órfão."""
    result = await seed_orphan_cost_gold(db)
    if result["status"] == "EXISTS":
        raise HTTPException(status_code=409, detail=f"Regra Gold já existe: {result['code']} (rule {result['rule_id']})")
    return result


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _split_csv(value: str | None) -> list[str] | None:
    """Converte 'A,B' (query param) em lista de valores."""
    if not value:
        return None
    items = [v.strip() for v in value.split(",") if v.strip()]
    return items or None


def _in_clause(column: str, values: list[str], alias: str | None = None) -> str:
    p = f"{alias}." if alias else ""
    quoted = ", ".join(_quote(v) for v in values)
    return f"{p}{column} IN ({quoted})"


def _build_orphan_where(period: str | None, orphan_rules: list[str] | None,
                        subscription_id: str | None, resource_group: str | None,
                        alias: str | None = None, tipos: list[str] | None = None,
                        snapshot_date: str | None = None) -> str:
    p = f"{alias}." if alias else ""
    clauses = []
    if period:
        clauses.append(f'{p}"_billing_period" = {_quote(period)}')
    if orphan_rules:
        clauses.append(_in_clause("orphan_rule", orphan_rules, alias))
    if subscription_id:
        clauses.append(f"{p}subscription_id = {_quote(subscription_id)}")
    if resource_group:
        clauses.append(f"{p}resource_group = {_quote(resource_group)}")
    if tipos:
        clauses.append(_in_clause("tipo_recurso", tipos, alias))
    if snapshot_date:
        clauses.append(f"{p}snapshot_date = {_quote(snapshot_date)}")
    return (" WHERE " + " AND ".join(clauses)) if clauses else ""


def _latest_snapshot_per_origin_where(where: str, alias: str, period: str | None = None) -> str:
    """Restrict a unified orphan view to each origin's newest snapshot."""
    prefix = f"{alias}." if alias else ""
    period_filter = f' AND i."_billing_period" = {_quote(period)}' if period else ""
    clause = (
        f"{prefix}snapshot_date = (SELECT MAX(i.snapshot_date) "
        f"FROM iceberg.{layers.detail_layer()}.{ORPHAN_DEST_PHYSICAL} i "
        f"WHERE i.cloud_origem = {prefix}cloud_origem{period_filter})"
    )
    return where + (" AND " if where else " WHERE ") + clause


async def _load_annotations(db: AsyncSession, orphan_rule: str | None = None,
                            snapshot_date: str | None = None) -> dict[tuple[str, str, str], OrphanAnnotation]:
    q = select(OrphanAnnotation)
    if orphan_rule:
        q = q.where(OrphanAnnotation.orphan_rule == orphan_rule)
    if snapshot_date:
        q = q.where(OrphanAnnotation.snapshot_date == snapshot_date)
    rows = (await db.execute(q)).scalars().all()
    return {(a.resource_id, a.orphan_rule, a.snapshot_date): a for a in rows}


@router.get("/summary")
async def orphan_summary(
    period: str | None = None,
    snapshot_date: str | None = None,
    orphan_rule: str | None = None,
    tipo_recurso: str | None = None,
    subscription_id: str | None = None,
    resource_group: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Resumo da Gold de órfãos respeitando os filtros ativos (widgets da UI)."""
    rules = _split_csv(orphan_rule)
    tipos = _split_csv(tipo_recurso)
    try:
        periods_rows = await trino_execute(
            'SELECT DISTINCT "_billing_period" FROM iceberg.silver.dest_cloud_orphan_resources '
            'ORDER BY "_billing_period" DESC'
        )
        snapshot_period_where = _build_orphan_where(period, None, None, None)
        snapshots_rows = await trino_execute(
            'SELECT DISTINCT snapshot_date FROM iceberg.silver.dest_cloud_orphan_resources'
            + snapshot_period_where + ' ORDER BY snapshot_date DESC'
        )
        snapshot_dates = [r.get("snapshot_date") for r in snapshots_rows if r.get("snapshot_date")]
        selected_snapshot = snapshot_date
        latest_snapshot = snapshot_dates[0] if snapshot_dates else None
        where = _build_orphan_where(
            period, rules, subscription_id, resource_group,
            tipos=tipos, snapshot_date=selected_snapshot,
        )
        if not selected_snapshot:
            where = _latest_snapshot_per_origin_where(where, "", period)
        by_rule = await trino_execute(
            "SELECT orphan_rule, COUNT(*) AS cnt FROM iceberg.silver.dest_cloud_orphan_resources"
            + where + " GROUP BY orphan_rule ORDER BY cnt DESC"
        )
        by_tipo = await trino_execute(
            "SELECT tipo_recurso, COUNT(*) AS cnt FROM iceberg.silver.dest_cloud_orphan_resources"
            + where + " GROUP BY tipo_recurso ORDER BY cnt DESC"
        )
        cost_where = _build_orphan_where(
            period, rules, subscription_id, resource_group,
            tipos=tipos, snapshot_date=selected_snapshot,
        )
        if not selected_snapshot:
            cost_where = _latest_snapshot_per_origin_where(cost_where, "", period)
        cost_total = await trino_execute(
            "SELECT COALESCE(SUM(cost_brl), 0) AS cost_brl, COUNT(*) AS n "
            f"FROM iceberg.{GOLD}.{ORPHAN_COST_DIMON_TABLE}"
            + cost_where
        )
    except Exception as exc:  # noqa: BLE001 — Trino pode estar fora/ tabela ainda não criada
        logger.warning("Trino summary indisponível: %s", exc)
        return {
            "periods": [], "snapshot_dates": [], "by_rule": [], "by_tipo": [], "by_status": [],
            "total": 0, "annotated": 0, "cost_brl": 0, "dimon_ready": False,
            "error": str(exc)[:300],
        }

    periods = [r.get("_billing_period") for r in periods_rows if r.get("_billing_period")]
    # Status efetivo: cada linha sem anotação é ORFAO (padrão); anotações
    # sobrepõem para EM_ANALISE / EXCECAO no snapshot selecionado.
    annotations = await _load_annotations(db, snapshot_date=selected_snapshot)
    annotated = len(annotations)
    total = sum(int(row.get("cnt") or 0) for row in by_rule)

    status_counter: dict[str, int] = {"ORFAO": max(0, total - annotated)}
    for ann in annotations.values():
        status_counter[ann.status] = status_counter.get(ann.status, 0) + 1

    by_status = [{"status": s, "count": status_counter[s]} for s in ORPHAN_STATUSES if status_counter.get(s, 0) > 0]
    dimon_cost = float((cost_total[0] if cost_total else {}).get("cost_brl") or 0)

    return {
        "periods": periods,
        "snapshot_dates": snapshot_dates,
        "latest_snapshot": latest_snapshot,
        "selected_snapshot": selected_snapshot,
        "by_rule": by_rule,
        "by_tipo": by_tipo,
        "by_status": by_status,
        "total": total,
        "annotated": annotated,
        "cost_brl": dimon_cost,
        "dimon_ready": bool(cost_total),
    }


@router.get("/orphans")
async def list_orphans(
    period: str | None = None,
    snapshot_date: str | None = None,
    orphan_rule: str | None = None,
    tipo_recurso: str | None = None,
    subscription_id: str | None = None,
    resource_group: str | None = None,
    status: str | None = None,
    limit: int = 500,
    db: AsyncSession = Depends(get_db),
):
    """Lista os recursos órfãos da Gold com status de governança (anotações)."""
    if limit < 1 or limit > 5000:
        limit = 500
    rules = _split_csv(orphan_rule)
    tipos = _split_csv(tipo_recurso)
    # Status de governança vive nas anotações (PostgreSQL), não na Gold — o
    # filtro é aplicado em Python após o overlay, não no SQL.
    if not snapshot_date:
        snapshot_where = _build_orphan_where(period, None, None, None)
        try:
            latest = await trino_execute(
                "SELECT MAX(snapshot_date) AS snapshot_date "
                "FROM iceberg.silver.dest_cloud_orphan_resources" + snapshot_where
            )
            snapshot_date = None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Snapshot de órfãos indisponível: %s", exc)
    where = _build_orphan_where(
        period, rules, subscription_id, resource_group,
        alias="o", tipos=tipos, snapshot_date=snapshot_date,
    )
    if not snapshot_date:
        where = _latest_snapshot_per_origin_where(where, "o", period)
    columns = (
        "o.resource_id, o.resource_name, o.resource_type, o.subscription_id, o.resource_group, o.location, o.sku, "
        "o.disk_size_gb, o.ip_address, o.source_disk, o.tags, o.created_at, o.orphan_rule, o.orphan_reason, "
        "o.tipo_recurso, o.age_days, o.status_flag, o.estimated_monthly_cost_brl, o.snapshot_date, "
        "o.\"_billing_period\", o.cloud_origem, "
        "COALESCE(c.focus_rows, 0) AS focus_rows, COALESCE(c.billedcost, 0) AS billedcost, "
        "COALESCE(c.effectivecost, 0) AS effectivecost, COALESCE(c.cost_brl, 0) AS cost_brl"
    )
    # LEFT JOIN com a Gold pré-agregada (custo via FOCUS) quando existir.
    # Tabela pode não existir ainda (materialização não executada) → custo 0.
    sql = (
        "SELECT " + columns + " FROM iceberg.silver.dest_cloud_orphan_resources o "
        f"LEFT JOIN iceberg.{GOLD}.{ORPHAN_COST_DIMON_TABLE} c "
        "ON c.resource_id = o.resource_id AND c.orphan_rule = o.orphan_rule "
        "AND c.\"_billing_period\" = o.\"_billing_period\" "
        "AND c.snapshot_date = o.snapshot_date "
        "AND c.cloud_origem = o.cloud_origem "
        "AND c.source_connection_id = o.source_connection_id"
        + where
        + " ORDER BY cost_brl DESC, o.age_days DESC NULLS LAST LIMIT " + str(limit)
    )
    try:
        rows = await trino_execute(sql)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Orphans list indisponível: %s", exc)
        return {"rows": [], "total": 0, "error": str(exc)[:300]}

    annotations = await _load_annotations(db)
    merged: list[dict[str, Any]] = []
    for row in rows:
        rid = row.get("resource_id") or ""
        rule = row.get("orphan_rule") or ""
        snap = row.get("snapshot_date") or ""
        ann = annotations.get((rid, rule, snap))
        out = dict(row)
        out["governance_status"] = ann.status if ann else (row.get("status_flag") or "ORFAO")
        out["governance_reason"] = ann.reason if ann else None
        merged.append(out)
    # Filtro de status de governança em Python (anotações são metadata no PG).
    if status and status in ORPHAN_STATUSES:
        merged = [row for row in merged if row.get("governance_status") == status]
    return {"rows": merged, "total": len(merged)}


def _saving_generated(monthly_cost: float, disappeared_on: str, as_of: str) -> tuple[int, float]:
    """Prorate a monthly avoided cost over the observed days since deletion."""
    start = date.fromisoformat(disappeared_on)
    end = date.fromisoformat(as_of)
    days = max(0, (end - start).days + 1)
    return days, round(float(monthly_cost or 0) * days / 30.4375, 2)


@router.get("/orphan-savings")
async def orphan_savings(
    orphan_rule: str | None = None,
    tipo_recurso: str | None = None,
    subscription_id: str | None = None,
    resource_group: str | None = None,
    limit: int = 1000,
):
    """Resources present in one daily snapshot and absent from the next one.

    A later reappearance cancels the disappearance, protecting the report from
    transient/incomplete collections. The last observed monthly FOCUS cost is
    used as the avoided-cost baseline.
    """
    limit = min(max(limit, 1), 5000)
    rules = _split_csv(orphan_rule)
    tipos = _split_csv(tipo_recurso)
    presence_where = _build_orphan_where(
        None, rules, subscription_id, resource_group, alias="o", tipos=tipos,
    )
    presence_filter = presence_where.replace(" WHERE ", " WHERE ", 1)
    sql = f"""
      WITH snapshots AS (
                SELECT cloud_origem, snapshot_date,
                             LEAD(snapshot_date) OVER (
                                 PARTITION BY cloud_origem ORDER BY snapshot_date
                             ) AS next_snapshot
        FROM (
                    SELECT DISTINCT cloud_origem, snapshot_date
          FROM iceberg.{layers.detail_layer()}.{ORPHAN_DEST_PHYSICAL}
        )
      ), presence_ranked AS (
        SELECT o.*,
               ROW_NUMBER() OVER (
                 PARTITION BY lower(o.resource_id), o.orphan_rule, o.snapshot_date,
                              o.cloud_origem
                 ORDER BY o.source_connection_id
               ) AS rn
        FROM iceberg.{layers.detail_layer()}.{ORPHAN_DEST_PHYSICAL} o
        {presence_filter}
      ), presence AS (
        SELECT * FROM presence_ranked WHERE rn = 1
      ), costs AS (
        SELECT lower(resource_id) AS rid, orphan_rule, snapshot_date,
               MAX(COALESCE(cost_brl, 0)) AS cost_brl
        FROM iceberg.{GOLD}.{ORPHAN_COST_DIMON_TABLE}
        GROUP BY 1, 2, 3
      )
      SELECT p.resource_id, p.resource_name, p.resource_type, p.subscription_id,
             p.resource_group, p.location, p.orphan_rule, p.orphan_reason,
             p.tipo_recurso, p.cloud_origem, p.snapshot_date AS last_seen_date,
             s.next_snapshot AS disappeared_on, p."_billing_period",
             COALESCE(c.cost_brl, 0) AS monthly_saving_brl
      FROM presence p
            JOIN snapshots s
                ON s.cloud_origem = p.cloud_origem
             AND s.snapshot_date = p.snapshot_date
      LEFT JOIN presence n
        ON lower(n.resource_id) = lower(p.resource_id)
       AND n.orphan_rule = p.orphan_rule
        AND n.cloud_origem = p.cloud_origem
       AND n.snapshot_date = s.next_snapshot
      LEFT JOIN costs c
        ON c.rid = lower(p.resource_id)
       AND c.orphan_rule = p.orphan_rule
       AND c.snapshot_date = p.snapshot_date
      WHERE s.next_snapshot IS NOT NULL
        AND n.resource_id IS NULL
        AND NOT EXISTS (
          SELECT 1 FROM presence later
          WHERE lower(later.resource_id) = lower(p.resource_id)
            AND later.orphan_rule = p.orphan_rule
                        AND later.cloud_origem = p.cloud_origem
            AND later.snapshot_date >= s.next_snapshot
        )
      ORDER BY disappeared_on DESC, monthly_saving_brl DESC
      LIMIT {limit}
    """
    try:
        rows = await trino_execute(sql, timeout_s=180)
        latest_rows = await trino_execute(
            f"SELECT MAX(snapshot_date) AS snapshot_date FROM iceberg.{layers.detail_layer()}.{ORPHAN_DEST_PHYSICAL}"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Saving de órfãos indisponível: %s", exc)
        return {"rows": [], "total": 0, "monthly_saving_brl": 0, "saving_generated_brl": 0, "error": str(exc)[:300]}

    as_of = str((latest_rows[0] if latest_rows else {}).get("snapshot_date") or "")
    output = []
    for row in rows:
        item = dict(row)
        days, generated = _saving_generated(
            float(item.get("monthly_saving_brl") or 0), str(item["disappeared_on"]), as_of,
        )
        item["days_saving"] = days
        item["saving_generated_brl"] = generated
        output.append(item)
    return {
        "as_of": as_of,
        "rows": output,
        "total": len(output),
        "monthly_saving_brl": round(sum(float(r.get("monthly_saving_brl") or 0) for r in output), 2),
        "saving_generated_brl": round(sum(float(r.get("saving_generated_brl") or 0) for r in output), 2),
    }


class AnnotationRequest(BaseModel):
    resource_id: str = Field(..., min_length=1)
    orphan_rule: str = Field(..., min_length=1)
    snapshot_date: str = Field(..., min_length=8, max_length=10)
    status: str = "EM_ANALISE"
    reason: str | None = None
    updated_by: str | None = None


@router.post("/annotations")
async def upsert_annotation(body: AnnotationRequest, db: AsyncSession = Depends(get_db)):
    """Sobrepõe o status de governança de um órfão. status=ORFAO remove a anotação."""
    if body.status not in ORPHAN_STATUSES:
        raise HTTPException(status_code=422, detail=f"status deve ser um de {', '.join(ORPHAN_STATUSES)}")
    existing = (await db.execute(
        select(OrphanAnnotation).where(
            OrphanAnnotation.resource_id == body.resource_id,
            OrphanAnnotation.orphan_rule == body.orphan_rule,
            OrphanAnnotation.snapshot_date == body.snapshot_date,
        )
    )).scalar_one_or_none()

    if body.status == "ORFAO":
        if existing:
            await db.delete(existing)
            await db.commit()
        return {"status": "ORFAO", "removed": True}

    if existing:
        existing.status = body.status
        existing.reason = body.reason
        existing.updated_by = body.updated_by
    else:
        db.add(OrphanAnnotation(
            resource_id=body.resource_id,
            orphan_rule=body.orphan_rule,
            snapshot_date=body.snapshot_date,
            status=body.status,
            reason=body.reason,
            updated_by=body.updated_by,
        ))
    await db.commit()
    return {"status": body.status, "removed": False}
