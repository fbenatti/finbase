"""Reservations & Savings Plans dashboard router.

Consulta as tabelas Bronze coletadas pelo azure_reservation_service:
  - iceberg.bronze.azure_reservations_catalog
  - iceberg.bronze.azure_benefit_utilization

Endpoints:
  GET  /reservations/sources              → conexões com coleta habilitada
  POST /reservations/connections/{id}/test → teste de conexão ARM
  POST /reservations/collect              → dispara coleta (task na fila)
  GET  /reservations/dashboard            → KPIs executivos (via Trino)
  GET  /reservations/catalog              → catálogo de benefícios
  GET  /reservations/utilization          → série de utilização
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import date, datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.source_connection import SourceConnection
from app.services.azure_reservation_service import (
    run_reservation_collection, resolve_config, test_connection,
)
from app.services.trino_service import execute as trino_execute
from app.services import lakehouse_layers as layers

router = APIRouter(prefix="/reservations", tags=["reservations"])
logger = logging.getLogger(__name__)

# Base do nome: a conexão entra como sufixo (`raw_azure_4p_...`), como em todas
# as outras fontes. Guardar várias conexões na mesma tabela foi desenho legado.
CATALOG_BASE = "reservations_catalog"
UTILIZATION_BASE = "benefit_utilization"
ADVISOR_BASE = "advisor_recommendations"
BENEFIT_REC_BASE = "benefit_recommendations"
FOCUS_DETAIL_TABLE = f"iceberg.{layers.detail_layer()}.dest_modelo_focus"
TRINO_TIMEOUT_S = 60.0

_CATALOG_COLUMNS = [
    "benefit_type", "benefit_id", "benefit_order_id", "display_name", "sku_name",
    "sku_description", "term", "billing_plan", "quantity", "reserved_resource_type",
    "applied_scope_type", "benefit_start", "benefit_end", "purchase_date", "status",
    "renew", "billing_scope_id", "location", "utilization_latest_pct", "utilization_trend",
    "connection_code", "connection_id", "_billing_period",
]


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


async def _tabelas_da_base(base: str) -> list[str]:
    """Tabelas Bronze das conexões para uma base de nome.

    Cada conexão tem a sua (`raw_azure_4p_advisor_recommendations`,
    `raw_azure_ti_advisor_recommendations`). A leitura une todas — quem consome
    não precisa saber em quantas conexões o dado está.
    """
    try:
        linhas = await trino_execute(
            "SELECT table_name FROM iceberg.information_schema.tables "
            f"WHERE table_schema='bronze' AND table_name LIKE 'raw_azure\_%\_{base}' "
            "ESCAPE '\\' ORDER BY table_name",
            timeout_s=TRINO_TIMEOUT_S,
        )
    except Exception:
        return []
    return [str(r.get("table_name")) for r in (linhas or []) if r.get("table_name")]


async def _table(base: str) -> str:
    """Fonte de leitura da base: a tabela da conexão, ou a união de todas.

    A união preserva o comportamento anterior (uma tabela com todas as conexões)
    já no formato novo, sem exigir que o consumidor mude.
    """
    tabelas = await _tabelas_da_base(base)
    if not tabelas:
        return f"iceberg.bronze.raw_azure_4p_{base}"
    if len(tabelas) == 1:
        return f"iceberg.bronze.{tabelas[0]}"
    return "(" + " UNION ALL ".join(f"SELECT * FROM iceberg.bronze.{t}" for t in tabelas) + ")"


async def _table_exists(base: str) -> bool:
    return bool(await _tabelas_da_base(base))


def _parse_azure_date(raw: Any) -> date | None:
    """Parse Azure ISO datetimes (may carry 7+ fractional digits) → date."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    # Azure can emit microseconds with 7 digits (e.g. .1572925) which
    # datetime.fromisoformat (py<3.11) rejects. Normalize to 6 digits.
    m = text.find(".")
    if m >= 0:
        frac_end = m + 1
        while frac_end < len(text) and text[frac_end].isdigit():
            frac_end += 1
        frac = text[m + 1:frac_end]
        if len(frac) > 6:
            text = text[:m + 1] + frac[:6] + text[frac_end:]
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


async def _load_saving_by_order() -> dict[str, dict[str, Any]]:
    """Saving por benefício a partir do FOCUS gold (listcost − effectivecost).

    O commitmentdiscountid no FOCUS referencia o ORDER id
    (…/reservationOrders/{orderId}); o catálogo guarda benefit_order_id com o
    mesmo valor, permitindo o join. Somente linhas com listcost > 0 (uso
    coberto, excluindo compras OneTime) entram no cálculo.
    """
    try:
        rows = await trino_execute(
            f"""
            SELECT
              element_at(split(commitmentdiscountid, '/'), -1) AS order_id,
              round(sum(TRY_CAST(listcost AS DOUBLE) - TRY_CAST(effectivecost AS DOUBLE)), 2) AS saving,
              round(sum(TRY_CAST(listcost AS DOUBLE)), 2) AS list_cost,
              round(sum(TRY_CAST(effectivecost AS DOUBLE)), 2) AS effective_cost,
              count(*) AS rows_n
            FROM {FOCUS_GOLD_TABLE}
            WHERE commitmentdiscountid IS NOT NULL AND commitmentdiscountid != ''
              AND TRY_CAST(listcost AS DOUBLE) > 0
            GROUP BY 1
            """,
            timeout_s=TRINO_TIMEOUT_S,
        )
        return {str(r.get("order_id")): r for r in rows if r.get("order_id")}
    except Exception as exc:
        logger.info("Saving lookup falhou (dados FOCUS indisponíveis): %s", exc)
        return {}


def _safe_connection(conn: SourceConnection) -> dict[str, Any]:
    cfg = dict(conn.config_json or {})
    cfg.pop("client_secret", None)
    cfg.pop("secret", None)
    cfg["credential_configured"] = bool((conn.config_json or {}).get("client_secret") or cfg.get("credential_profile"))
    cfg["collect_reservations"] = bool((conn.config_json or {}).get("collect_reservations"))
    return {
        "id": conn.id, "code": conn.code, "name": conn.name,
        "connector_type": conn.connector_type, "cloud_name": conn.cloud_name,
        "cloud_scope": conn.cloud_scope, "config": cfg,
    }


class CollectRequest(BaseModel):
    connection_ids: list[int] = Field(default_factory=list, description="Vazio = todas com flag habilitado")


class DashboardFilter(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    connection_ids: list[int] = Field(default_factory=list)
    benefit_types: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    days_to_expiry: int | None = Field(None, ge=0)


@router.get("/sources")
async def list_sources(db: AsyncSession = Depends(get_db)):
    """Conexões com coleta de reservas habilitada (flag collect_reservations)."""
    rows = (await db.execute(
        select(SourceConnection).order_by(SourceConnection.name)
    )).scalars().all()
    enabled = [c for c in rows if (c.config_json or {}).get("collect_reservations")]
    return {
        "enabled": [_safe_connection(c) for c in enabled],
        "all": [_safe_connection(c) for c in rows],
    }


@router.post("/connections/{connection_id}/test")
async def test_connection_endpoint(connection_id: int, db: AsyncSession = Depends(get_db)):
    conn = await db.get(SourceConnection, connection_id)
    if not conn:
        raise HTTPException(404, "Conexão não encontrada")
    cfg = resolve_config(dict(conn.config_json or {}))
    result = await test_connection(cfg)
    conn.last_test_status = result.get("status")
    conn.last_test_message = result.get("message")
    conn.last_tested_at = datetime.now(timezone.utc)
    await db.commit()
    return result


@router.post("/collect", status_code=202)
async def collect(body: CollectRequest, db: AsyncSession = Depends(get_db)):
    """Enfileira coleta de reservas para as conexões selecionadas (ou todas com flag)."""
    from app.models.etl_task import EtlTask

    if body.connection_ids:
        conns = []
        for cid in body.connection_ids:
            conn = await db.get(SourceConnection, cid)
            if conn:
                conns.append(conn)
    else:
        rows = (await db.execute(
            select(SourceConnection).where(SourceConnection.config_json["collect_reservations"].astext == "true")
        )).scalars().all()
        conns = list(rows)

    if not conns:
        raise HTTPException(422, "Nenhuma conexão com collect_reservations habilitado")

    queued = []
    for conn in conns:
        active = (await db.execute(
            select(EtlTask.id).where(
                EtlTask.connection_id == conn.id,
                EtlTask.task_type == "reservation_collection",
                EtlTask.status.in_(["QUEUED", "RUNNING"]),
            ).limit(1)
        )).scalar_one_or_none()
        if active:
            queued.append({"id": conn.id, "name": conn.name, "status": "JA_ATIVO", "task_id": active})
            continue
        task = EtlTask(
            connection_id=conn.id,
            task_type="reservation_collection",
            status="QUEUED",
            connection_ids=[conn.id],
            billing_periods=[],
            selected_blobs=[],
            phase="Coleta de reservas Azure enfileirada",
            result_json={"collect_reservations": True},
        )
        db.add(task)
        await db.flush()
        queued.append({"id": conn.id, "name": conn.name, "status": "QUEUED", "task_id": task.id})
    await db.commit()
    return {"queued": queued}


@router.get("/dashboard")
async def dashboard(
    benefit_types: str | None = None,
    connection_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """KPIs executivos consolidados (catálogo + utilização + custo no bronze)."""
    if not await _table_exists(CATALOG_BASE):
        return {
            "available": False,
            "message": "Nenhum dado de reservas coletado ainda. Use POST /reservations/collect.",
            "catalog": [], "utilization": [], "kpis": {}, "expiring": [], "by_type": [],
        }

    where = []
    if connection_id:
        where.append(f"{_ident('connection_id')} = {int(connection_id)}")
    if benefit_types:
        vals = ", ".join(f"'{v}'" for v in benefit_types.split(","))
        where.append(f"{_ident('benefit_type')} IN ({vals})")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    catalog = await trino_execute(
        f"SELECT * FROM {await _table(CATALOG_BASE)}{where_sql}",
        timeout_s=TRINO_TIMEOUT_S,
    )
    utilization = []
    if await _table_exists(UTILIZATION_BASE):
        util_where = []
        if connection_id:
            util_where.append(f"{_ident('connection_id')} = {int(connection_id)}")
        util_sql = (" WHERE " + " AND ".join(util_where)) if util_where else ""
        utilization = await trino_execute(
            f"SELECT * FROM {await _table(UTILIZATION_BASE)}{util_sql}",
            timeout_s=TRINO_TIMEOUT_S,
        )

    today = date.today()
    saving_by_order = await _load_saving_by_order()
    rows: list[dict[str, Any]] = []
    for r in catalog:
        end_date = _parse_azure_date(r.get("benefit_end"))
        r["_end_date"] = end_date
        r["_days_left"] = (end_date - today).days if end_date else None
        cost = saving_by_order.get(str(r.get("benefit_order_id") or ""))
        r["_saving"] = float(cost["saving"]) if cost and cost.get("saving") is not None else None
        r["_list_cost"] = float(cost["list_cost"]) if cost and cost.get("list_cost") is not None else None
        r["_effective_cost"] = float(cost["effective_cost"]) if cost and cost.get("effective_cost") is not None else None
        rows.append(r)

    active = [r for r in rows if str(r.get("status") or "").lower() in ("succeeded", "active", "")]
    total_commit = sum(float(r.get("quantity") or 0) for r in active if r.get("benefit_type") == "reserved_instance")
    total_sp_commit = sum(float(r.get("commitment_amount") or 0) for r in rows if r.get("benefit_type") == "savings_plan")
    util_values = [float(r.get("util_1d") or 0) for r in rows if r.get("util_1d") is not None]
    util_summary = [float(u.get("avg_utilization_pct") or 0) for u in utilization if u.get("avg_utilization_pct") is not None]
    saving_values = [r["_saving"] for r in rows if r["_saving"] is not None]

    expiring = sorted(
        (r for r in rows if r.get("_end_date") and r.get("_days_left") is not None and r["_days_left"] >= 0),
        key=lambda r: r["_days_left"],
    )
    by_type: dict[str, dict[str, Any]] = {}
    for r in rows:
        bt = r.get("benefit_type") or "unknown"
        bucket = by_type.setdefault(bt, {"type": bt, "count": 0, "active": 0, "utilization_sum": 0.0, "utilization_n": 0, "saving_sum": 0.0, "saving_n": 0})
        bucket["count"] += 1
        if str(r.get("status") or "").lower() in ("succeeded", "active", ""):
            bucket["active"] += 1
        if r.get("util_1d") is not None:
            bucket["utilization_sum"] += float(r["util_1d"])
            bucket["utilization_n"] += 1
        if r["_saving"] is not None:
            bucket["saving_sum"] += r["_saving"]
            bucket["saving_n"] += 1

    for bucket in by_type.values():
        bucket["avg_utilization"] = round(bucket["utilization_sum"] / bucket["utilization_n"], 1) if bucket["utilization_n"] else None
        bucket["saving_total"] = round(bucket["saving_sum"], 2) if bucket["saving_n"] else None

    return {
        "available": True,
        "kpis": {
            "benefits_total": len(rows),
            "benefits_active": len(active),
            "expiring_30d": sum(1 for r in rows if r.get("_days_left") is not None and 0 <= r["_days_left"] <= 30),
            "expiring_60d": sum(1 for r in rows if r.get("_days_left") is not None and 0 <= r["_days_left"] <= 60),
            "expiring_90d": sum(1 for r in rows if r.get("_days_left") is not None and 0 <= r["_days_left"] <= 90),
            "expired": sum(1 for r in rows if r.get("_days_left") is not None and r["_days_left"] < 0),
            "ri_quantity_total": total_commit,
            "sp_commitment_total": total_sp_commit,
            "avg_utilization_catalog": round(sum(util_values) / len(util_values), 1) if util_values else None,
            "avg_utilization_daily": round(sum(util_summary) / len(util_summary), 1) if util_summary else None,
            "utilization_days": len(util_summary),
            "saving_total": round(sum(saving_values), 2) if saving_values else None,
            "saving_benefits": len(saving_values),
            "collected_at": (rows[0].get("_collected_at") or rows[0].get("_ingested_at")) if rows else None,
        },
        "expiring": [
            {
                "benefit_id": r.get("benefit_id"), "benefit_type": r.get("benefit_type"),
                "display_name": r.get("display_name"), "status": r.get("status"),
                "benefit_end": r.get("benefit_end"), "days_left": r.get("_days_left"),
                "util_1d": r.get("util_1d"), "util_7d": r.get("util_7d"), "util_30d": r.get("util_30d"),
                "saving": r["_saving"],
                "connection_code": r.get("connection_code"), "term": r.get("term"),
            }
            for r in expiring[:50]
        ],
        "by_type": list(by_type.values()),
        "catalog_count": len(rows),
        "utilization_count": len(utilization),
    }


@router.get("/catalog")
async def catalog(
    benefit_type: str | None = None,
    status: str | None = None,
    connection_id: int | None = None,
    limit: int = Query(500, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
):
    """Catálogo de benefícios (RI + Savings Plans)."""
    if not await _table_exists(CATALOG_BASE):
        return {"available": False, "rows": []}
    where = []
    if benefit_type:
        where.append(f"{_ident('benefit_type')} = '{benefit_type}'")
    if status:
        where.append(f"{_ident('status')} = '{status}'")
    if connection_id:
        where.append(f"{_ident('connection_id')} = {int(connection_id)}")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = await trino_execute(
        f"SELECT * FROM {await _table(CATALOG_BASE)}{where_sql} ORDER BY {_ident('benefit_end')} LIMIT {int(limit)}",
        timeout_s=TRINO_TIMEOUT_S,
    )
    return {"available": True, "rows": rows}


@router.get("/utilization")
async def utilization(
    connection_id: int | None = None,
    benefit_id: str | None = None,
    days: int = Query(90, ge=1, le=730),
    db: AsyncSession = Depends(get_db),
):
    """Série diária de utilização consolidada (benefitUtilizationSummaries)."""
    if not await _table_exists(UTILIZATION_BASE):
        return {"available": False, "rows": []}
    where = []
    if connection_id:
        where.append(f"{_ident('connection_id')} = {int(connection_id)}")
    if benefit_id:
        where.append(f"{_ident('benefit_id')} = '{benefit_id}'")
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    where.append(f"{_ident('usage_date')} >= '{cutoff}'")
    where_sql = " WHERE " + " AND ".join(where)
    rows = await trino_execute(
        f"SELECT * FROM {await _table(UTILIZATION_BASE)}{where_sql} "
        f"ORDER BY {_ident('usage_date')}",
        timeout_s=TRINO_TIMEOUT_S,
    )
    return {"available": True, "rows": rows}


@router.get("/advisor")
async def advisor_recommendations(
    category: str | None = None,
    connection_id: int | None = None,
    limit: int = Query(500, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
):
    """Recomendações do Azure Advisor (filtro por categoria, ex. Cost)."""
    if not await _table_exists(ADVISOR_BASE):
        return {"available": False, "rows": [], "categories": []}
    where = []
    if category:
        where.append(f"{_ident('category')} = '{category}'")
    if connection_id:
        where.append(f"{_ident('connection_id')} = {int(connection_id)}")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = await trino_execute(
        f"SELECT * FROM {await _table(ADVISOR_BASE)}{where_sql} ORDER BY {_ident('impact')} LIMIT {int(limit)}",
        timeout_s=TRINO_TIMEOUT_S,
    )
    cats = await trino_execute(
        f"SELECT {_ident('category')}, count(*) AS n FROM {await _table(ADVISOR_BASE)} GROUP BY 1 ORDER BY 2 DESC",
        timeout_s=TRINO_TIMEOUT_S,
    )
    return {"available": True, "rows": rows, "categories": cats}


@router.get("/recommendations")
async def benefit_recommendations(
    kind: str | None = None,
    connection_id: int | None = None,
    limit: int = Query(500, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
):
    """Recomendações de benefício (Savings Plan / RI) com savings estimado."""
    if not await _table_exists(BENEFIT_REC_BASE):
        return {"available": False, "rows": [], "summary": {}}
    where = []
    if kind:
        where.append(f"{_ident('kind')} = '{kind}'")
    if connection_id:
        where.append(f"{_ident('connection_id')} = {int(connection_id)}")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = await trino_execute(
        f"SELECT * FROM {await _table(BENEFIT_REC_BASE)}{where_sql} "
        f"ORDER BY {_ident('savings_amount')} DESC LIMIT {int(limit)}",
        timeout_s=TRINO_TIMEOUT_S,
    )
    summary = await trino_execute(
        f"""
        SELECT
          count(*) AS n,
          round(sum(TRY_CAST(savings_amount AS DOUBLE)), 2) AS savings_total,
          round(avg(TRY_CAST(savings_pct AS DOUBLE)), 1) AS avg_savings_pct,
          round(sum(TRY_CAST(commitment_amount AS DOUBLE)), 2) AS commitment_total
        FROM {await _table(BENEFIT_REC_BASE)}{where_sql}
        """,
        timeout_s=TRINO_TIMEOUT_S,
    )
    return {"available": True, "rows": rows, "summary": summary[0] if summary else {}}
