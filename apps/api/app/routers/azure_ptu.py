"""Azure OpenAI PTU operational dashboard and source discovery."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import json
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.source_connection import SourceConnection
from app.models.azure_ptu import AzurePtuProjectMapping, AzurePtuSubproject, AzurePtuUsageEvent, AzurePtuCheckpoint
from app.models.ai_cost import AiCostLink
from app.services.azure_ptu_service import (
    collect_metrics, discover, probe_eventhub, resolve_config, test_connection_checks,
    load_gold_inventory, load_gold_heatmap, DASHBOARD_TRINO_TIMEOUT_S,
    FOCUS_DASHBOARD_TIMEOUT_S,
    _gold_metrics_window,
    _gold_metric_hour_filter,
)
from app.services.azure_ptu_meters import (
    attach_origin_cost_per_million,
    cost_per_million_tokens,
    focus_category_sql_expr,
    focus_date_predicate_sql,
    focus_period_bucket_sql,
    focus_ptu_meter_sql_expr,
    observed_tpm,
    p95 as metric_p95,
    pricing_mode_label,
    unused_capacity_cost,
    unused_capacity_pct,
)
from app.services.ai_cost_attribution import preview_ptu_pool_allocation, load_focus_schema
from app.services.azure_ptu_ops import (
    build_capacity_risk,
    build_executive_summary,
    build_ops_recommendations,
    build_service_quality,
    build_token_consumption,
    metric_field_availability,
    period_metadata,
    peak_utilization_label,
    weighted_utilization,
)
from app.services.azure_ptu_cost import load_dimon_ptu_costs
from app.services.azure_ptu_dimon import load_dimon_metrics, refresh_azure_ptu_dimon
from app.services.aura_brain_vivo_mais import load_project_cost_dashboard
from app.services.sinfonia_dashboard import load_sinfonia_dashboard
from app.services.datamesh_dashboard import load_datamesh_dashboard
from app.services.wisper_dashboard import load as load_wisper_dashboard
from app.services.trino_service import execute as trino_execute

router = APIRouter(prefix="/visoes-consumo-ia", tags=["visoes-consumo-ia"])
logger = logging.getLogger(__name__)

# The dashboard is read-only and the Gold tables are batch-fed. A short cache
# prevents React remounts/filter redraws from issuing the same four Trino
# queries repeatedly while keeping the view fresh for operators.
_OPS_OVERVIEW_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_OPS_OVERVIEW_CACHE_TTL_S = 30.0


class _ClientCancelScope:
    def __init__(self, request: Request, *, timeout_s: float = DASHBOARD_TRINO_TIMEOUT_S):
        self.request = request
        self.timeout_s = timeout_s
        self.cancel_event = asyncio.Event()
        self._watch_task: asyncio.Task | None = None

    async def __aenter__(self) -> _ClientCancelScope:
        async def _watch_disconnect() -> None:
            while not self.cancel_event.is_set():
                if await self.request.is_disconnected():
                    self.cancel_event.set()
                    return
                await asyncio.sleep(0.25)

        self._watch_task = asyncio.create_task(_watch_disconnect())
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._watch_task and not self._watch_task.done():
            self._watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch_task


def _connection_or_404(conn: SourceConnection | None) -> SourceConnection:
    if not conn:
        raise HTTPException(status_code=404, detail="Conexão Azure PTU não encontrada")
    if conn.connector_type != "azure_openai_ptu":
        raise HTTPException(status_code=422, detail="A conexão não é azure_openai_ptu")
    return conn


def _config(conn: SourceConnection) -> dict[str, Any]:
    return resolve_config(dict(conn.config_json or {}))


def _safe_connection(conn: SourceConnection) -> dict[str, Any]:
    cfg = dict(conn.config_json or {})
    cfg.pop("client_secret", None)
    cfg.pop("secret", None)
    cfg["credential_configured"] = bool((conn.config_json or {}).get("client_secret") or cfg.get("credential_profile"))
    return {"id": conn.id, "code": conn.code, "name": conn.name, "connector_type": conn.connector_type,
            "cloud_name": conn.cloud_name, "cloud_scope": conn.cloud_scope, "config": cfg}


class MetricsRequest(BaseModel):
    hours: int = Field(1, ge=1, le=720)


class DashboardFilter(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    date_from: str | None = None
    date_to: str | None = None
    project_keys: list[str] = Field(default_factory=list)
    deployment_names: list[str] = Field(default_factory=list)
    model_names: list[str] = Field(default_factory=list)
    subproject_keys: list[str] = Field(default_factory=list)
    purchase_modes: list[str] = Field(default_factory=list)
    grain: str = "hour"
    hours: int = Field(24 * 90, ge=1, le=3000)


class OverviewRequest(DashboardFilter):
    pass


def _dashboard_date_bounds(body: DashboardFilter) -> tuple[str, str, str, str]:
    """Return (date_from, date_to, metric_hour_from, metric_hour_to) for the dashboard window."""
    end = datetime.now(timezone.utc)
    if body.date_to:
        try:
            end = datetime.fromisoformat(body.date_to.replace("Z", "+00:00"))
        except ValueError:
            pass
    start = end - timedelta(hours=max(1, min(body.hours, 93 * 24)))
    if body.date_from:
        try:
            start = datetime.fromisoformat(body.date_from.replace("Z", "+00:00"))
        except ValueError:
            pass
    date_from = start.strftime("%Y-%m-%d")
    date_to = end.strftime("%Y-%m-%d")
    metric_hour_from = start.strftime("%Y-%m-%dT%H:00:00Z")
    metric_hour_to = end.strftime("%Y-%m-%dT%H:00:00Z")
    return date_from, date_to, metric_hour_from, metric_hour_to


def _focus_date_predicate(date_from: str, date_to: str) -> str:
    """Date filter on chargeperiodstart/billingperiodstart (MM/DD/YYYY in Gold FOCUS)."""
    return focus_date_predicate_sql(date_from, date_to)



async def _query_focus_dashboard_costs(
    date_from: str,
    date_to: str,
    *,
    cancel_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    """PTU vs IA OnDemand (Foundry/tokens) vs VM/Storage/Network for Aura Brain and Vivo Mais."""
    category = focus_category_sql_expr()
    dept = "lower(CAST(\"departamento\" AS VARCHAR))"
    empty = {
        "ptu_capacity_cost": 0.0,
        "billed": 0.0,
        "ia_ondemand": {"aura-brain": 0.0, "vivo-mais": 0.0},
        "shared_infra": {
            "aura-brain": {"vm": 0.0, "storage": 0.0, "network": 0.0, "other_infra": 0.0, "total": 0.0},
            "vivo-mais": {"vm": 0.0, "storage": 0.0, "network": 0.0, "other_infra": 0.0, "total": 0.0},
        },
        "cost_rows": [],
        "cost_composition": {"ptu_capacity": 0.0, "ia_ondemand_total": 0.0, "shared_infra_total": 0.0, "total_ia": 0.0},
    }
    sql = f"""
      SELECT
        CASE
          WHEN {dept} LIKE '%aura brain%' THEN 'aura-brain'
          WHEN {dept} LIKE '%vivo mais%' THEN 'vivo-mais'
          ELSE 'other'
        END AS project_key,
        {category} AS cost_class,
        CAST("servicename" AS VARCHAR) AS service_name,
        COALESCE(SUM(TRY_CAST("realizado" AS DOUBLE)), 0) AS realized
      FROM iceberg.gold.dest_modelo_focus
      WHERE lower(CAST("cloud_origem" AS VARCHAR)) LIKE 'azure%'
        AND {_focus_date_predicate(date_from, date_to)}
        AND ({dept} LIKE '%aura brain%' OR {dept} LIKE '%vivo mais%')
      GROUP BY 1, 2, 3
    """
    try:
        rows = await trino_execute(
            sql,
            timeout_s=FOCUS_DASHBOARD_TIMEOUT_S,
            cancel_event=cancel_event,
        )
    except Exception as exc:
        logger.warning("FOCUS dashboard costs query failed: %s", exc, exc_info=True)
        return empty

    ia_ondemand = {"aura-brain": 0.0, "vivo-mais": 0.0}
    shared_infra = {
        "aura-brain": {"vm": 0.0, "storage": 0.0, "network": 0.0, "other_infra": 0.0, "total": 0.0},
        "vivo-mais": {"vm": 0.0, "storage": 0.0, "network": 0.0, "other_infra": 0.0, "total": 0.0},
    }
    ptu_capacity = 0.0
    cost_rows: list[dict[str, Any]] = []
    for row in rows or []:
        project = str(row.get("project_key") or "other")
        klass = str(row.get("cost_class") or "other_infra")
        amount = float(row.get("realized") or 0)
        service = str(row.get("service_name") or "—")
        if project not in ("aura-brain", "vivo-mais"):
            continue
        cost_rows.append({"project_key": project, "cost_class": klass, "service_name": service, "realized": amount})
        if klass == "ptu":
            ptu_capacity += amount
        elif klass == "ia_ondemand":
            ia_ondemand[project] += amount
        elif klass in ("vm", "storage", "network", "other_infra"):
            shared_infra[project][klass] += amount
            shared_infra[project]["total"] += amount
    ondemand_total = sum(ia_ondemand.values())
    infra_total = sum(v["total"] for v in shared_infra.values())
    return {
        "ptu_capacity_cost": ptu_capacity,
        "billed": ptu_capacity,
        "ia_ondemand": ia_ondemand,
        "shared_infra": shared_infra,
        "cost_rows": cost_rows,
        "cost_composition": {
            "ptu_capacity": ptu_capacity,
            "ia_ondemand_total": ondemand_total,
            "shared_infra_total": infra_total,
            "total_ia": ptu_capacity + ondemand_total,
        },
    }


async def _load_operational_data(
    body: DashboardFilter,
    db: AsyncSession,
    *,
    dashboard_only: bool = False,
    cancel_event: asyncio.Event | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, list[dict[str, Any]]]:
    """Load dashboard metrics from Dimon and the small capacity inventory from Gold."""
    conns = (await db.execute(
        select(SourceConnection).where(SourceConnection.connector_type == "azure_openai_ptu")
    )).scalars().all()
    conn_ids = [c.id for c in conns]
    inventory: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    heatmap: list[dict[str, Any]] = []
    data_source = "dimon"

    # A Dimon é diária. A granularidade horária fica reservada ao heatmap
    # explícito, que é a única ação do dashboard que consulta a Gold pesada.
    effective_grain = "month" if body.grain == "month" else "day"

    metrics_task = load_dimon_metrics(
        hours=body.hours,
        connection_ids=conn_ids,
        project_hints=body.project_keys or None,
        grain=effective_grain,
        timeout_s=DASHBOARD_TRINO_TIMEOUT_S,
    )
    inventory_task = load_gold_inventory(connection_ids=conn_ids)
    dimon_metrics, inventory = await asyncio.gather(metrics_task, inventory_task)

    if dimon_metrics:
        metrics = dimon_metrics
    elif dashboard_only:
        data_source = "stale"
    else:
        data_source = "live"
        for conn in conns:
            discovered = await discover(_config(conn))
            inventory.extend(discovered["deployments"])
            metrics.extend(await collect_metrics(_config(conn), discovered["deployments"], hours=body.hours))

    await _apply_project_mappings(db, inventory)
    inventory = _filtered(inventory, body)
    metrics = _filtered(metrics, body)
    return inventory, metrics, data_source, heatmap


@router.post("/dashboard/ops-heatmap")
async def dashboard_ops_heatmap(body: OverviewRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Consulta sob demanda: único widget operacional que lê a Gold horária."""
    async with _ClientCancelScope(request) as scope:
        conns = (await db.execute(
            select(SourceConnection).where(SourceConnection.connector_type == "azure_openai_ptu")
        )).scalars().all()
        rows = await load_gold_heatmap(
            hours=body.hours,
            connection_ids=[conn.id for conn in conns],
            project_hints=body.project_keys or None,
            timeout_s=DASHBOARD_TRINO_TIMEOUT_S,
        )
        if scope.cancel_event.is_set():
            raise HTTPException(status_code=499, detail="Consulta cancelada pelo cliente")
        return {"heatmap": rows, "source": "gold", "loaded_on_demand": True}


@router.post("/azure-ptu/dimon/reprocess")
async def reprocess_azure_ptu_dimon(days: int = 400):
    """Materializa novamente a Dimon PTU usada pelos widgets do dashboard."""
    if days < 90 or days > 400:
        raise HTTPException(status_code=422, detail="days deve estar entre 90 e 400")
    try:
        return await refresh_azure_ptu_dimon(days=days)
    except Exception as exc:
        logger.exception("Falha ao materializar Dimon Azure PTU")
        raise HTTPException(status_code=503, detail=f"Falha ao materializar Dimon Azure PTU: {exc}") from exc


def _filtered(items: list[dict[str, Any]], f: DashboardFilter) -> list[dict[str, Any]]:
    out = items
    if f.project_keys:
        out = [x for x in out if x.get("project_key") in f.project_keys or x.get("project_hint") in f.project_keys]
    if f.deployment_names:
        out = [x for x in out if x.get("deployment_name") in f.deployment_names]
    if f.model_names:
        out = [x for x in out if x.get("model_name") in f.model_names]
    if f.purchase_modes:
        out = [x for x in out if x.get("purchase_mode") in f.purchase_modes]
    return out


def _util_band(util: float | None) -> str | None:
    if util is None:
        return None
    if util >= 95:
        return "95-100"
    if util >= 80:
        return "80-95"
    if util >= 30:
        return "30-80"
    return "0-30"


def _deployment_key(row: dict[str, Any]) -> str:
    return str(row.get("deployment_name") or row.get("id") or "unknown")


def _pricing_from_inventory(inv: dict[str, Any]) -> dict[str, Any]:
    sku = str(inv.get("sku_name") or "")
    purchase = inv.get("purchase_mode")
    dtype = inv.get("deployment_type")
    sku_l = sku.lower().replace(" ", "")
    if not purchase:
        if "globalprovisioned" in sku_l:
            purchase = "reservation"
        elif "provisioned" in sku_l:
            purchase = "ondemand"
    if not dtype:
        if "global" in sku_l:
            dtype = "global"
        elif "datazone" in sku_l or "data_zone" in sku_l:
            dtype = "data_zone"
        elif "provisioned" in sku_l:
            dtype = "regional"
    return {
        "purchase_mode": purchase,
        "deployment_type": dtype,
        "pricing_label": pricing_mode_label(purchase, sku),
    }


def _aggregate_deployment_utilization(
    metrics: list[dict[str, Any]],
    inventory: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Per-deployment utilization summary for comparison tables."""
    inv_by_name = {_deployment_key(d): d for d in inventory}
    buckets: dict[str, dict[str, Any]] = {}
    for row in metrics:
        key = _deployment_key(row)
        bucket = buckets.setdefault(key, {
            "avg": [], "max": [], "p95": [],
            "avg_weighted_num": 0.0, "avg_weighted_den": 0.0,
            "input": 0.0, "output": 0.0, "hours": 0.0,
            "rpm": [], "ttr": [], "ttlb": [], "ttft": [], "tbt": [],
            "rate_limited": 0.0, "server_errors": 0.0, "client_errors": 0.0, "success": 0.0, "requests": 0.0,
            "quality_anomaly": False,
            "hours_80": 0, "hours_95": 0,
        })
        ptus = float(row.get("provisioned_ptus") or 0)
        util_avg = row.get("utilization_avg")
        if util_avg is not None and ptus > 0:
            bucket["avg_weighted_num"] += float(util_avg) * ptus
            bucket["avg_weighted_den"] += ptus
        if util_avg is not None:
            bucket["avg"].append(float(util_avg))
        if row.get("utilization_max") is not None:
            max_value = float(row["utilization_max"])
            if max_value > 100:
                bucket["quality_anomaly"] = True
            elif max_value >= 0:
                bucket["max"].append(max_value)
            if max_value <= 100 and max_value >= 80:
                bucket["hours_80"] += 1
            if max_value <= 100 and max_value >= 95:
                bucket["hours_95"] += 1
        if row.get("utilization_p95") is not None:
            bucket["p95"].append(float(row["utilization_p95"]))
        bucket["input"] += float(row.get("input_tokens_sum") or row.get("input_tokens") or 0)
        bucket["output"] += float(row.get("output_tokens_sum") or row.get("output_tokens") or 0)
        bucket["hours"] += 1.0
        if row.get("request_peak_per_minute") is not None:
            bucket["rpm"].append(float(row["request_peak_per_minute"]))
        if row.get("time_to_response_avg_ms") is not None:
            bucket["ttr"].append(float(row["time_to_response_avg_ms"]))
        if row.get("time_to_last_byte_avg_ms") is not None:
            bucket["ttlb"].append(float(row["time_to_last_byte_avg_ms"]))
        if row.get("normalized_ttft_avg_ms") is not None:
            bucket["ttft"].append(float(row["normalized_ttft_avg_ms"]))
        if row.get("normalized_tbt_avg_ms") is not None:
            bucket["tbt"].append(float(row["normalized_tbt_avg_ms"]))
        bucket["rate_limited"] += float(row.get("rate_limited_count") or 0)
        bucket["server_errors"] += float(row.get("server_error_count") or 0)
        bucket["client_errors"] += float(row.get("client_error_count") or 0)
        bucket["success"] += float(row.get("success_count") or 0)
        bucket["requests"] += float(row.get("request_count") or 0)
        if row.get("utilization_quality_anomaly"):
            bucket["quality_anomaly"] = True
    output: list[dict[str, Any]] = []
    all_keys = sorted(set(buckets) | set(inv_by_name))
    for key in all_keys:
        inv = inv_by_name.get(key, {})
        vals = buckets.get(key, {
            "avg": [], "max": [], "p95": [], "avg_weighted_num": 0.0, "avg_weighted_den": 0.0,
            "input": 0.0, "output": 0.0, "hours": 0.0,
            "rpm": [], "ttr": [], "ttlb": [], "ttft": [], "tbt": [],
            "rate_limited": 0.0, "server_errors": 0.0, "client_errors": 0.0, "success": 0.0, "requests": 0.0, "quality_anomaly": False,
            "hours_80": 0, "hours_95": 0,
        })
        util_avg = (
            vals["avg_weighted_num"] / vals["avg_weighted_den"]
            if vals["avg_weighted_den"] else (sum(vals["avg"]) / len(vals["avg"]) if vals["avg"] else None)
        )
        util_max = max(vals["max"]) if vals["max"] else None
        peak, peak_label = peak_utilization_label([
            {"utilization_max": v} for v in vals["max"]
        ]) if vals["max"] else (None, "indisponível")
        util_p95 = peak if peak_label == "P95" else (metric_p95(vals["p95"]) if vals["p95"] else peak)
        ptus = float(inv.get("ptus") or inv.get("provisioned_ptus") or 0)
        pricing = _pricing_from_inventory(inv)
        hours = float(vals["hours"] or 0)
        output.append({
            "deployment_name": key,
            "project_key": inv.get("project_hint") or inv.get("project_key"),
            "model_name": inv.get("model_name") or inv.get("model"),
            "model_version": inv.get("model_version"),
            "region": inv.get("region"),
            "sku_name": inv.get("sku_name"),
            "ptus": ptus,
            **pricing,
            "utilization_avg": util_avg,
            "utilization_max": util_max,
            "utilization_p95": util_p95,
            "utilization_peak_label": peak_label,
            "unused_capacity_pct": unused_capacity_pct(util_avg),
            "utilization_band": _util_band(util_avg),
            "peak_utilization": util_max,
            "hours_at_or_above_80": vals["hours_80"],
            "hours_at_or_above_95": vals["hours_95"],
            "utilization_quality_anomaly": vals["quality_anomaly"],
            "input_tokens": vals["input"],
            "output_tokens": vals["output"],
            "input_tpm": observed_tpm(vals["input"], hours),
            "output_tpm": observed_tpm(vals["output"], hours),
            "request_peak_per_minute": max(vals["rpm"]) if vals["rpm"] else None,
            "time_to_response_avg_ms": sum(vals["ttr"]) / len(vals["ttr"]) if vals["ttr"] else None,
            "time_to_last_byte_avg_ms": sum(vals["ttlb"]) / len(vals["ttlb"]) if vals["ttlb"] else None,
            "normalized_ttft_avg_ms": sum(vals["ttft"]) / len(vals["ttft"]) if vals["ttft"] else None,
            "normalized_tbt_avg_ms": sum(vals["tbt"]) / len(vals["tbt"]) if vals["tbt"] else None,
            "rate_limited_count": vals["rate_limited"] or None,
            "request_count": vals["requests"] or None,
            "throttle_rate_pct": (vals["rate_limited"] / vals["requests"] * 100) if vals["requests"] else None,
            "server_error_rate_pct": (vals["server_errors"] / vals["requests"] * 100) if vals["requests"] else None,
            "client_error_rate_pct": (vals["client_errors"] / vals["requests"] * 100) if vals["requests"] else None,
            "risk_score": (vals["hours_95"] * 3) + (vals["hours_80"] * 1) + (vals["rate_limited"] / 10),
            "peak_concurrency_note": "Azure Monitor não expõe concorrência de sessão; usamos pico de requests/minuto.",
        })
    return output


def _deployment_imbalance(deployments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Highlight utilization spread between deployments in the same project."""
    by_project: dict[str, list[dict[str, Any]]] = {}
    for dep in deployments:
        project = dep.get("project_key") or "unattributed"
        by_project.setdefault(project, []).append(dep)
    alerts: list[dict[str, Any]] = []
    for project_key, deps in by_project.items():
        if len(deps) < 2:
            continue
        utils = [float(d["utilization_avg"]) for d in deps if d.get("utilization_avg") is not None]
        if len(utils) < 2:
            continue
        spread = max(utils) - min(utils)
        ptu_split = ", ".join(f"{d.get('deployment_name')}: {d.get('ptus', 0)} PTU" for d in deps)
        alerts.append({
            "project_key": project_key,
            "deployment_count": len(deps),
            "utilization_min": min(utils),
            "utilization_max": max(utils),
            "utilization_spread": spread,
            "ptus_split": ptu_split,
            "severity": "high" if spread >= 35 else "medium" if spread >= 20 else "low",
            "message": f"Desbalanceamento de {spread:.1f} p.p. entre deployments ({ptu_split})",
        })
    return alerts


def _build_token_series(metrics: list[dict[str, Any]], grain: str = "hour") -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, float]] = {}
    for row in metrics:
        hour = str(row.get("metric_hour") or "")
        if not hour:
            continue
        if grain == "day":
            bucket_key = hour[:10]
        elif grain == "month":
            bucket_key = hour[:7]
        else:
            bucket_key = hour
        slot = buckets.setdefault(bucket_key, {"input_tokens": 0.0, "output_tokens": 0.0, "total_tokens": 0.0})
        slot["input_tokens"] += float(row.get("input_tokens_sum") or row.get("input_tokens") or 0)
        slot["output_tokens"] += float(row.get("output_tokens_sum") or row.get("output_tokens") or 0)
        slot["total_tokens"] += float(row.get("total_tokens_sum") or row.get("total_tokens") or 0)
    return [
        {"timestamp": ts, **values}
        for ts, values in sorted(buckets.items())
    ]


async def _build_cost_series(
    grain: str = "month",
    date_from: str | None = None,
    date_to: str | None = None,
    *,
    cancel_event: asyncio.Event | None = None,
) -> list[dict[str, Any]]:
    if grain == "day":
        period_expr = focus_period_bucket_sql("day")
    elif grain == "hour":
        period_expr = focus_period_bucket_sql("hour")
    else:
        period_expr = focus_period_bucket_sql("month")
    date_filter = ""
    if date_from and date_to:
        date_filter = f" AND {_focus_date_predicate(date_from, date_to)}"
    ptu_expr = focus_ptu_meter_sql_expr()
    try:
        rows = await trino_execute(f"""
          SELECT {period_expr} AS period,
                 COALESCE(SUM(CASE WHEN {ptu_expr} = 1 THEN TRY_CAST("realizado" AS DOUBLE) ELSE 0 END), 0) AS realized_cost,
                 COALESCE(SUM(CASE WHEN {ptu_expr} = 1 THEN TRY_CAST("billedcost" AS DOUBLE) ELSE 0 END), 0) AS billed_cost
          FROM iceberg.gold.dest_modelo_focus
          WHERE lower(CAST("cloud_origem" AS VARCHAR)) LIKE 'azure%'
            AND {ptu_expr} = 1{date_filter}
          GROUP BY 1
          ORDER BY 1
        """, timeout_s=FOCUS_DASHBOARD_TIMEOUT_S, cancel_event=cancel_event)
    except Exception as exc:
        logger.warning("FOCUS cost series query failed: %s", exc, exc_info=True)
        return []
    return [
        {
            "timestamp": row.get("period"),
            "realized_cost": float(row.get("realized_cost") or 0),
            "billed_cost": float(row.get("billed_cost") or 0),
        }
        for row in (rows or [])
        if row.get("period")
    ]


def _dashboard_hours(grain: str) -> int:
    if grain == "hour":
        return 168
    if grain == "day":
        return 720
    return 93 * 24


def _build_metrics_coverage(metrics: list[dict[str, Any]], hours: int) -> dict[str, Any]:
    """Summarize calendar coverage inside the dashboard window (detect backfill gaps)."""
    start_iso, end_iso = _gold_metrics_window(hours)
    start_day = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).date()
    end_day = datetime.fromisoformat(end_iso.replace("Z", "+00:00")).date()
    expected_days = max(1, (end_day - start_day).days + 1)
    distinct_days = sorted({str(m.get("metric_hour") or "")[:10] for m in metrics if m.get("metric_hour")})
    distinct_set = set(distinct_days)
    missing: list[str] = []
    cursor = start_day
    while cursor <= end_day:
        day = cursor.isoformat()
        if day not in distinct_set:
            missing.append(day)
        cursor += timedelta(days=1)
    days_with_data = len(distinct_set)
    return {
        "window_start": start_iso,
        "window_end": end_iso,
        "expected_days": expected_days,
        "days_with_data": days_with_data,
        "missing_days": len(missing),
        "missing_day_samples": missing[:14],
        "coverage_pct": round((days_with_data / expected_days) * 100, 1) if expected_days else None,
        "complete": len(missing) == 0,
    }


def _build_utilization_series(metrics: list[dict[str, Any]], grain: str = "hour") -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for row in metrics:
        hour = str(row.get("metric_hour") or "")
        if not hour:
            continue
        if grain == "day":
            bucket_key = hour[:10]
        elif grain == "month":
            bucket_key = hour[:7]
        else:
            bucket_key = hour
        slot = buckets.setdefault(bucket_key, {"weighted_num": 0.0, "weighted_den": 0.0, "max": [], "p95": []})
        ptus = float(row.get("provisioned_ptus") or 0)
        util_avg = row.get("utilization_avg")
        if util_avg is not None and ptus > 0:
            slot["weighted_num"] += float(util_avg) * ptus
            slot["weighted_den"] += ptus
        elif util_avg is not None:
            slot.setdefault("avg", []).append(float(util_avg))
        if row.get("utilization_max") is not None:
            max_value = float(row["utilization_max"])
            if 0 <= max_value <= 100:
                slot["max"].append(max_value)
        if row.get("utilization_p95") is not None:
            p95_value = float(row["utilization_p95"])
            if 0 <= p95_value <= 100:
                slot["p95"].append(p95_value)
    series = []
    for ts in sorted(buckets):
        vals = buckets[ts]
        util_avg = (
            vals["weighted_num"] / vals["weighted_den"]
            if vals["weighted_den"] else (
                sum(vals.get("avg", [])) / len(vals["avg"]) if vals.get("avg") else None
            )
        )
        peak, peak_label = peak_utilization_label([{"utilization_max": v} for v in vals["max"]]) if vals["max"] else (None, "indisponível")
        series.append({
            "timestamp": ts,
            "utilization_avg": util_avg,
            "utilization_max": max(vals["max"]) if vals["max"] else None,
            "utilization_p95": peak if peak_label == "P95" else (metric_p95(vals["p95"]) if vals["p95"] else peak),
            "utilization_peak_label": peak_label,
            "band": _util_band(util_avg),
        })
    return series


def _build_utilization_heatmap(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hour-of-day × day-of-week matrix for utilization heatmap."""
    cells: dict[tuple[int, int], list[float]] = {}
    for row in metrics:
        hour_raw = str(row.get("metric_hour") or "")
        if len(hour_raw) < 13 or row.get("utilization_avg") is None:
            continue
        try:
            dt = datetime.fromisoformat(hour_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        key = (dt.weekday(), dt.hour)
        cells.setdefault(key, []).append(float(row["utilization_avg"]))
    return [
        {
            "weekday": wd,
            "hour": hr,
            "utilization_avg": sum(vals) / len(vals),
            "samples": len(vals),
        }
        for (wd, hr), vals in sorted(cells.items())
    ]


def _build_project_stats(
    inventory: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    deployment_stats: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    projects: dict[str, dict[str, Any]] = {}
    for dep in inventory:
        key = dep.get("project_hint") or "unattributed"
        p = projects.setdefault(key, {
            "project_key": key,
            "ptus": 0.0,
            "deployments": 0,
            "tokens": 0.0,
            "input_tokens": 0.0,
            "output_tokens": 0.0,
            "utilization_avg_values": [],
            "utilization_p95_values": [],
        })
        p["ptus"] += float(dep.get("ptus") or 0)
        p["deployments"] += 1
        pricing = _pricing_from_inventory(dep)
        p["purchase_mode"] = pricing["purchase_mode"]
        p["deployment_type"] = pricing["deployment_type"]
        p["pricing_label"] = pricing["pricing_label"]
    for row in metrics:
        key = row.get("project_key") or row.get("project_hint") or "unattributed"
        p = projects.setdefault(key, {
            "project_key": key,
            "ptus": 0.0,
            "deployments": 0,
            "tokens": 0.0,
            "input_tokens": 0.0,
            "output_tokens": 0.0,
            "utilization_avg_values": [],
            "utilization_p95_values": [],
        })
        p["tokens"] += float(row.get("total_tokens") or row.get("total_tokens_sum") or 0)
        p["input_tokens"] += float(row.get("input_tokens") or row.get("input_tokens_sum") or 0)
        p["output_tokens"] += float(row.get("output_tokens") or row.get("output_tokens_sum") or 0)
        if row.get("utilization_avg") is not None:
            p["utilization_avg_values"].append(float(row["utilization_avg"]))
        if row.get("utilization_p95") is not None:
            p["utilization_p95_values"].append(float(row["utilization_p95"]))
    dep_by_project: dict[str, list[dict[str, Any]]] = {}
    for dep in deployment_stats:
        dep_by_project.setdefault(dep.get("project_key") or "unattributed", []).append(dep)
    for key, p in projects.items():
        avg_vals = p.pop("utilization_avg_values")
        p95_vals = p.pop("utilization_p95_values")
        p["utilization_avg"] = sum(avg_vals) / len(avg_vals) if avg_vals else None
        p["utilization_p95"] = metric_p95(p95_vals) if p95_vals else (metric_p95(avg_vals) if avg_vals else None)
        p["unused_capacity_pct"] = unused_capacity_pct(p["utilization_avg"])
        p["utilization_band"] = _util_band(p["utilization_avg"])
        p["deployment_comparison"] = dep_by_project.get(key, [])
    return projects


def _inventory_ptus(row: dict[str, Any]) -> float:
    return float(row.get("ptus") or row.get("provisioned_ptus") or 0)


def _enrich_kpis(
    metrics: list[dict[str, Any]],
    inventory: list[dict[str, Any]],
    total_cost: float,
) -> dict[str, Any]:
    avg_util_vals = [float(x["utilization_avg"]) for x in metrics if x.get("utilization_avg") is not None]
    p95_util_vals = [float(x["utilization_p95"]) for x in metrics if x.get("utilization_p95") is not None]
    max_util_vals = [float(x["utilization_max"]) for x in metrics if x.get("utilization_max") is not None]
    util = (sum(avg_util_vals) / len(avg_util_vals)) if avg_util_vals else None
    util_p95 = metric_p95(p95_util_vals) if p95_util_vals else (metric_p95(avg_util_vals) if avg_util_vals else None)
    util_max = max(max_util_vals) if max_util_vals else None
    total_tokens = sum(float(x.get("total_tokens") or x.get("total_tokens_sum") or 0) for x in metrics)
    def _inv_ptus(row: dict[str, Any]) -> float:
        return _inventory_ptus(row)

    reserved = sum(_inv_ptus(x) for x in inventory if "global" in str(x.get("sku_name") or "").lower())
    ondemand = sum(_inv_ptus(x) for x in inventory) - reserved
    return {
        "total_ptus": sum(_inv_ptus(x) for x in inventory),
        "reserved_ptus": reserved,
        "ondemand_ptus": ondemand,
        "deployment_count": len(inventory),
        "total_tokens": total_tokens,
        "input_tokens": sum(float(x.get("input_tokens") or x.get("input_tokens_sum") or 0) for x in metrics),
        "output_tokens": sum(float(x.get("output_tokens") or x.get("output_tokens_sum") or 0) for x in metrics),
        "realized_cost": total_cost,
        "ptu_cost": total_cost,
        "payg_cost": 0,
        "cost_per_million_tokens": cost_per_million_tokens(total_cost, total_tokens),
        "utilization_avg": util,
        "utilization_p95": util_p95,
        "utilization_max": util_max,
        "peak_utilization": util_max,
        "unused_capacity_pct": unused_capacity_pct(util),
        "unused_capacity_cost_estimate": unused_capacity_cost(total_cost, util),
        "unused_capacity_wording_reservation": "custo de capacidade não aproveitada",
        "unused_capacity_wording_ondemand": "economia potencial de redimensionamento",
        "utilization_bands": {"30": 30, "80": 80, "95": 95, "100": 100},
    }


def _enrich_ops_kpis(metrics: list[dict[str, Any]], inventory: list[dict[str, Any]]) -> dict[str, Any]:
    """Operational KPIs from Gold PTU only — no FOCUS/cost integration."""
    util = weighted_utilization(metrics)
    peak, peak_label = peak_utilization_label(metrics)
    total_tokens = sum(float(x.get("total_tokens") or x.get("total_tokens_sum") or 0) for x in metrics)
    input_tokens = sum(float(x.get("input_tokens") or x.get("input_tokens_sum") or 0) for x in metrics)
    output_tokens = sum(float(x.get("output_tokens") or x.get("output_tokens_sum") or 0) for x in metrics)
    metric_hours = float(len({str(x.get("metric_hour") or "") for x in metrics if x.get("metric_hour")}))
    reserved = sum(_inventory_ptus(x) for x in inventory if "global" in str(x.get("sku_name") or "").lower())
    ondemand = sum(_inventory_ptus(x) for x in inventory) - reserved
    ttr_vals = [float(x["time_to_response_avg_ms"]) for x in metrics if x.get("time_to_response_avg_ms") is not None]
    ttlb_vals = [float(x["time_to_last_byte_avg_ms"]) for x in metrics if x.get("time_to_last_byte_avg_ms") is not None]
    ttft_vals = [float(x["normalized_ttft_avg_ms"]) for x in metrics if x.get("normalized_ttft_avg_ms") is not None]
    rpm_vals = [float(x["request_peak_per_minute"]) for x in metrics if x.get("request_peak_per_minute") is not None]
    hours_80 = sum(int(x.get("hours_at_or_above_80") or 0) for x in metrics)
    hours_95 = sum(int(x.get("hours_at_or_above_95") or 0) for x in metrics)
    return {
        "total_ptus": sum(_inventory_ptus(x) for x in inventory),
        "reserved_ptus": reserved,
        "ondemand_ptus": ondemand,
        "deployment_count": len(inventory),
        "total_tokens": total_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_tpm": observed_tpm(input_tokens, metric_hours),
        "output_tpm": observed_tpm(output_tokens, metric_hours),
        "utilization_avg": util,
        "utilization_p95": peak,
        "utilization_peak_label": peak_label,
        "utilization_max": (
            max([float(x["utilization_max"]) for x in metrics if x.get("utilization_max") is not None])
            if any(x.get("utilization_max") is not None for x in metrics) else None
        ),
        "peak_utilization": peak,
        "unused_capacity_pct": unused_capacity_pct(util),
        "hours_at_or_above_80": hours_80,
        "hours_at_or_above_95": hours_95,
        "rate_limited_count": sum(float(x.get("rate_limited_count") or 0) for x in metrics),
        "request_peak_per_minute": max(rpm_vals) if rpm_vals else None,
        "time_to_response_avg_ms": sum(ttr_vals) / len(ttr_vals) if ttr_vals else None,
        "time_to_last_byte_avg_ms": sum(ttlb_vals) / len(ttlb_vals) if ttlb_vals else None,
        "normalized_ttft_avg_ms": sum(ttft_vals) / len(ttft_vals) if ttft_vals else None,
        "availability_rate_avg": weighted_utilization([
            {**m, "utilization_avg": m.get("availability_rate_avg"), "provisioned_ptus": m.get("provisioned_ptus") or 1}
            for m in metrics if m.get("availability_rate_avg") is not None
        ]),
        "context_cache_match_rate_avg": weighted_utilization([
            {**m, "utilization_avg": m.get("context_cache_match_rate_avg"), "provisioned_ptus": m.get("provisioned_ptus") or 1}
            for m in metrics if m.get("context_cache_match_rate_avg") is not None
        ]),
        "utilization_bands": {"30": 30, "80": 80, "95": 95, "100": 100},
    }


def _build_latency_series(metrics: list[dict[str, Any]], grain: str = "hour") -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, list[float]]] = {}
    for row in metrics:
        hour = str(row.get("metric_hour") or "")
        if not hour:
            continue
        if grain == "day":
            bucket_key = hour[:10]
        elif grain == "month":
            bucket_key = hour[:7]
        else:
            bucket_key = hour
        slot = buckets.setdefault(bucket_key, {"ttr": [], "ttlb": []})
        if row.get("time_to_response_avg_ms") is not None:
            slot["ttr"].append(float(row["time_to_response_avg_ms"]))
        if row.get("time_to_last_byte_avg_ms") is not None:
            slot["ttlb"].append(float(row["time_to_last_byte_avg_ms"]))
    return [
        {
            "timestamp": ts,
            "time_to_response_avg_ms": sum(vals["ttr"]) / len(vals["ttr"]) if vals["ttr"] else None,
            "time_to_last_byte_avg_ms": sum(vals["ttlb"]) / len(vals["ttlb"]) if vals["ttlb"] else None,
        }
        for ts, vals in sorted(buckets.items())
    ]


def _build_rate_limit_series(metrics: list[dict[str, Any]], grain: str = "hour") -> list[dict[str, Any]]:
    buckets: dict[str, float] = {}
    for row in metrics:
        hour = str(row.get("metric_hour") or "")
        if not hour:
            continue
        if grain == "day":
            bucket_key = hour[:10]
        elif grain == "month":
            bucket_key = hour[:7]
        else:
            bucket_key = hour
        buckets[bucket_key] = buckets.get(bucket_key, 0.0) + float(row.get("rate_limited_count") or 0)
    return [{"timestamp": ts, "rate_limited_count": count} for ts, count in sorted(buckets.items())]


def _billing_period(body: OverviewRequest) -> str:
    if body.date_to and len(body.date_to) >= 7:
        return body.date_to.replace("-", "")[:6]
    return datetime.now(timezone.utc).strftime("%Y%m")


async def _apply_project_mappings(db: AsyncSession, inventory: list[dict[str, Any]]) -> None:
    mappings = (await db.execute(
        select(AzurePtuProjectMapping).where(AzurePtuProjectMapping.active.is_(True))
    )).scalars().all()
    if not mappings:
        return
    by_id = {m.deployment_resource_id: m for m in mappings}
    for dep in inventory:
        rid = str(dep.get("id") or dep.get("resource_id") or dep.get("deployment_resource_id") or "")
        name = str(dep.get("name") or dep.get("deployment_name") or "")
        match = by_id.get(rid) or by_id.get(name)
        if match is None:
            for key, cand in by_id.items():
                if key and (key in rid or (name and name in key)):
                    match = cand
                    break
        if match is None:
            continue
        dep["project_key"] = match.project_key
        dep["project_hint"] = match.project_key
        dep["purchase_mode"] = match.purchase_mode
        dep["deployment_type"] = match.deployment_type
        dep["reservation_ref"] = match.reservation_ref


async def _origin_cost_rows(
    db: AsyncSession,
    billing_period: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    metric_hour_from: str | None = None,
    metric_hour_to: str | None = None,
    focus_schema: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    links = (await db.execute(
        select(AiCostLink).where(
            AiCostLink.target_type == "ptu_pool",
            AiCostLink.active.is_(True),
        )
    )).scalars().all()
    if focus_schema is None:
        focus_schema = await load_focus_schema()
    rows: list[dict[str, Any]] = []
    for link in links:
        try:
            preview = await preview_ptu_pool_allocation(
                link,
                billing_period,
                date_from=date_from,
                date_to=date_to,
                metric_hour_from=metric_hour_from,
                metric_hour_to=metric_hour_to,
                schema=focus_schema,
            )
        except Exception:
            preview = []
        amount = sum(float(r.get("allocated_amount") or 0) for r in preview)
        rows.append({
            "link_id": link.id,
            "link_name": link.name,
            "project_key": link.project_key,
            "purchase_mode": link.purchase_mode,
            "deployment_type": link.deployment_type,
            "pricing_label": pricing_mode_label(link.purchase_mode),
            "origin_cost": amount,
            "billing_period": billing_period,
        })
    return rows


class MappingIn(BaseModel):
    connection_id: int
    deployment_resource_id: str
    account_resource_id: str | None = None
    project_key: str
    project_name: str
    department_id: int | None = None
    purchase_mode: str | None = None
    deployment_type: str | None = None
    reservation_ref: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    active: bool = True
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class SubprojectIn(BaseModel):
    project_mapping_id: int | None = None
    project_key: str
    subproject_key: str
    name: str
    description: str | None = None
    telemetry_client_id: str | None = None
    department_id: int | None = None
    owner: str | None = None
    environment: str | None = None
    active: bool = True
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class UsageEventIn(BaseModel):
    """Application contract. Extra fields are rejected to prevent prompt leakage."""
    model_config = ConfigDict(extra="forbid", protected_namespaces=())
    schema_version: str = Field("1.0", max_length=20)
    event_id: str = Field(..., min_length=8, max_length=160)
    occurred_at: datetime
    telemetry_client_id: str = Field(..., min_length=1, max_length=200)
    project_key: str | None = Field(None, max_length=120)
    subproject_key: str | None = Field(None, max_length=160)
    environment: str | None = Field(None, max_length=40)
    azure_account_resource_id: str | None = None
    deployment_name: str | None = Field(None, max_length=200)
    model_name: str | None = Field(None, max_length=200)
    model_version: str | None = Field(None, max_length=100)
    api_operation: str | None = Field(None, max_length=50)
    stream: bool | None = None
    status_code: int | None = Field(None, ge=100, le=599)
    is_spillover: bool = False
    request_count: int = Field(1, ge=0, le=1000)
    input_tokens: int = Field(0, ge=0)
    cached_input_tokens: int = Field(0, ge=0)
    output_tokens: int = Field(0, ge=0)
    reasoning_tokens: int = Field(0, ge=0)
    total_tokens: int = Field(0, ge=0)
    latency_ms: float | None = Field(None, ge=0)


class UsageBatchIn(BaseModel):
    events: list[UsageEventIn] = Field(..., min_length=1, max_length=1000)


@router.get("/connections")
async def list_ptu_connections(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(SourceConnection).where(SourceConnection.connector_type == "azure_openai_ptu").order_by(SourceConnection.code))).scalars().all()
    return [_safe_connection(row) for row in rows]


@router.get("/connections/{connection_id}/discovery")
async def get_discovery(connection_id: int, db: AsyncSession = Depends(get_db)):
    return await discover_connection(connection_id, db)


@router.post("/connections/{connection_id}/eventhub-test")
async def test_eventhub(connection_id: int, db: AsyncSession = Depends(get_db)):
    conn = _connection_or_404(await db.get(SourceConnection, connection_id))
    return await probe_eventhub(_config(conn))


@router.post("/connections/{connection_id}/discover")
async def discover_connection(connection_id: int, db: AsyncSession = Depends(get_db)):
    conn = _connection_or_404(await db.get(SourceConnection, connection_id))
    try:
        result = await discover(_config(conn))
        conn.last_test_status = "OK"
        conn.last_test_message = f"Discovery: {result['ptu_deployment_count']} PTU deployments / {result['total_ptus']:.0f} PTUs"
        conn.last_tested_at = datetime.now(timezone.utc)
        await db.commit()
        return result
    except Exception as exc:
        conn.last_test_status = "FAILED"
        conn.last_test_message = str(exc)[:500]
        conn.last_tested_at = datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(status_code=502, detail=f"Falha na descoberta Azure: {exc}") from exc


@router.post("/connections/{connection_id}/test")
async def test_connection(connection_id: int, db: AsyncSession = Depends(get_db)):
    conn = _connection_or_404(await db.get(SourceConnection, connection_id))
    try:
        result = await test_connection_checks(_config(conn))
        conn.last_test_status = result.get("status")
        conn.last_test_message = str(result.get("message") or result.get("status"))[:500]
        conn.last_tested_at = datetime.now(timezone.utc)
        await db.commit()
        return result
    except Exception as exc:
        conn.last_test_status = "FAILED"
        conn.last_test_message = str(exc)[:500]
        conn.last_tested_at = datetime.now(timezone.utc)
        await db.commit()
        return {"status": "FAILED", "checks": {}, "message": str(exc)[:500]}


@router.post("/connections/{connection_id}/metrics")
async def connection_metrics(connection_id: int, body: MetricsRequest, db: AsyncSession = Depends(get_db)):
    conn = _connection_or_404(await db.get(SourceConnection, connection_id))
    try:
        inventory = await discover(_config(conn))
        return {"inventory": inventory, "metrics": await collect_metrics(_config(conn), inventory["deployments"], hours=body.hours)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Falha na coleta de métricas: {exc}") from exc


@router.get("/project-mappings")
async def list_project_mappings(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(AzurePtuProjectMapping).order_by(AzurePtuProjectMapping.project_key, AzurePtuProjectMapping.id))).scalars().all()
    return [{c.name: getattr(row, c.name) for c in AzurePtuProjectMapping.__table__.columns} for row in rows]


@router.post("/project-mappings", status_code=201)
async def create_project_mapping(body: MappingIn, db: AsyncSession = Depends(get_db)):
    row = AzurePtuProjectMapping(**body.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {c.name: getattr(row, c.name) for c in AzurePtuProjectMapping.__table__.columns}


@router.put("/project-mappings/{mapping_id}")
async def update_project_mapping(mapping_id: int, body: MappingIn, db: AsyncSession = Depends(get_db)):
    row = await db.get(AzurePtuProjectMapping, mapping_id)
    if not row:
        raise HTTPException(status_code=404, detail="Mapeamento não encontrado")
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    await db.commit()
    await db.refresh(row)
    return {c.name: getattr(row, c.name) for c in AzurePtuProjectMapping.__table__.columns}


@router.get("/subprojects")
async def list_subprojects(project_key: str | None = None, db: AsyncSession = Depends(get_db)):
    stmt = select(AzurePtuSubproject).order_by(AzurePtuSubproject.project_key, AzurePtuSubproject.subproject_key)
    if project_key:
        stmt = stmt.where(AzurePtuSubproject.project_key == project_key)
    rows = (await db.execute(stmt)).scalars().all()
    return [{c.name: getattr(row, c.name) for c in AzurePtuSubproject.__table__.columns} for row in rows]


@router.post("/subprojects", status_code=201)
async def create_subproject(body: SubprojectIn, db: AsyncSession = Depends(get_db)):
    payload = body.model_dump()
    if not payload.get("telemetry_client_id"):
        import uuid
        payload["telemetry_client_id"] = f"{payload['project_key']}-{payload['subproject_key']}-{uuid.uuid4().hex[:8]}"
    row = AzurePtuSubproject(**payload)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {c.name: getattr(row, c.name) for c in AzurePtuSubproject.__table__.columns}


@router.put("/subprojects/{subproject_id}")
async def update_subproject(subproject_id: int, body: SubprojectIn, db: AsyncSession = Depends(get_db)):
    row = await db.get(AzurePtuSubproject, subproject_id)
    if not row:
        raise HTTPException(status_code=404, detail="Subprojeto não encontrado")
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    await db.commit()
    await db.refresh(row)
    return {c.name: getattr(row, c.name) for c in AzurePtuSubproject.__table__.columns}


@router.get("/sync-status")
async def sync_status(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(AzurePtuCheckpoint))).scalars().all()
    conns = (await db.execute(select(SourceConnection).where(SourceConnection.connector_type == "azure_openai_ptu"))).scalars().all()
    return {
        "connections": [
            {"id": c.id, "code": c.code, "last_test_status": c.last_test_status, "last_tested_at": c.last_tested_at, "last_test_message": c.last_test_message}
            for c in conns
        ],
        "checkpoints": [{c.name: getattr(row, c.name) for c in AzurePtuCheckpoint.__table__.columns} for row in rows],
    }


@router.post("/usage-events", status_code=202)
async def ingest_usage_events(body: UsageBatchIn, db: AsyncSession = Depends(get_db)):
    """Ingest sanitized, asynchronous application telemetry.

    Unknown telemetry clients and malformed ownership are quarantined; they are
    never silently attributed. Duplicate event_ids are idempotently ignored.
    """
    from app.services.azure_ptu_ingest import ingest_usage_events_async

    result = await ingest_usage_events_async(db, [event.model_dump() for event in body.events])
    await db.commit()
    return {
        "accepted": result.accepted,
        "quarantined": result.quarantined,
        "duplicates": result.duplicates,
        "received": result.received,
        "iceberg": result.iceberg,
    }


@router.get("/usage-events")
async def list_usage_events(status: str | None = None, limit: int = 100, db: AsyncSession = Depends(get_db)):
    limit = max(1, min(limit, 1000))
    stmt = select(AzurePtuUsageEvent).order_by(AzurePtuUsageEvent.occurred_at.desc()).limit(limit)
    if status in {"accepted", "quarantine"}:
        stmt = stmt.where(AzurePtuUsageEvent.ingestion_status == status)
    rows = (await db.execute(stmt)).scalars().all()
    return [{c.name: getattr(row, c.name) for c in AzurePtuUsageEvent.__table__.columns} for row in rows]


@router.get("/usage-events/coverage")
async def usage_events_coverage(project_key: str | None = None, db: AsyncSession = Depends(get_db)):
    """Return instrumented totals vs Azure Monitor (Gold) for coverage."""
    from app.services.azure_ptu_meters import coverage_band, coverage_pct

    filters = [AzurePtuUsageEvent.ingestion_status == "accepted"]
    if project_key:
        filters.append(AzurePtuUsageEvent.project_key == project_key)
    row = (await db.execute(select(
        func.coalesce(func.sum(AzurePtuUsageEvent.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(AzurePtuUsageEvent.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(AzurePtuUsageEvent.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(AzurePtuUsageEvent.request_count), 0).label("requests"),
        func.count(AzurePtuUsageEvent.id).label("events"),
    ).where(*filters))).one()
    application_total = int(row.total_tokens or 0)
    monitor_total: float | None = None
    try:
        project_filter = f" AND CAST(project_hint AS VARCHAR) = '{project_key}'" if project_key else ""
        rows = await trino_execute(
            f"""
            SELECT COALESCE(SUM(TRY_CAST(total_tokens_sum AS DOUBLE)), 0) AS tokens
            FROM iceberg.gold.dest_azure_ptu_metrics
            WHERE 1 = 1{project_filter}
            """,
            timeout_s=90,
        )
        if rows:
            monitor_total = float(rows[0].get("tokens") or 0)
    except Exception:
        monitor_total = None
    cov = coverage_pct(application_total, monitor_total) if monitor_total else None
    return {
        "project_key": project_key,
        "application_events": {k: row._mapping[k] for k in ("events", "requests", "input_tokens", "output_tokens", "total_tokens")},
        "azure_monitor_total_tokens": monitor_total,
        "coverage_pct": cov,
        "coverage_band": coverage_band(cov) if cov is not None else None,
        "note": "Cobertura = tokens instrumentados / tokens Azure Monitor (Gold). Sem Gold, apenas totais de aplicação.",
    }


def _build_recommendations(kpis: dict[str, Any], imbalance: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    util = kpis.get("utilization_avg")
    unused = kpis.get("unused_capacity_pct") or 0
    if util is not None and util < 30:
        recs.append({
            "code": "LOW_UTILIZATION", "severity": "medium", "confidence": 0.7,
            "evidence": f"Utilização média {util:.1f}%", "impact": "Capacidade ociosa",
            "action": "Revisar redução ou consolidação de PTUs",
        })
    if unused > 0.2:
        recs.append({
            "code": "UNUSED_RESERVATION", "severity": "medium", "confidence": 0.65,
            "evidence": f"Capacidade não aproveitada {unused * 100:.1f}%", "impact": "Custo de reserva ociosa",
            "action": "Mover workloads antes de renovar a reserva",
        })
    if util is not None and util > 80:
        recs.append({
            "code": "HIGH_UTILIZATION", "severity": "high", "confidence": 0.8,
            "evidence": f"Utilização média {util:.1f}%", "impact": "Risco de 429/throttling",
            "action": "Revisar capacidade e balanceamento",
        })
    for item in imbalance:
        if item.get("severity") in {"high", "medium"}:
            recs.append({
                "code": "DEPLOYMENT_IMBALANCE", "severity": item.get("severity", "medium"),
                "confidence": 0.75, "evidence": item.get("message", ""), "impact": "Desbalanceamento entre deployments",
                "action": "Redistribuir carga ou ajustar PTUs por deployment",
            })
    return recs


@router.post("/dashboard/overview")
async def dashboard_overview(body: OverviewRequest, request: Request, db: AsyncSession = Depends(get_db)):
    async with _ClientCancelScope(request) as scope:
        cancel = scope.cancel_event
        date_from, date_to, _, _ = _dashboard_date_bounds(body)

        op_task = _load_operational_data(body, db, dashboard_only=True, cancel_event=cancel)
        focus_task = _query_focus_dashboard_costs(date_from, date_to, cancel_event=cancel)
        cost_task = _build_cost_series(body.grain, date_from, date_to, cancel_event=cancel)
        op_result, focus_costs, cost_series = await asyncio.gather(
            op_task, focus_task, cost_task, return_exceptions=True,
        )
        if isinstance(op_result, Exception):
            logger.warning("Azure PTU operational data failed: %s", op_result, exc_info=op_result)
            inventory, metrics, data_source, heatmap = [], [], "stale", []
        else:
            inventory, metrics, data_source, heatmap = op_result
        if isinstance(focus_costs, Exception):
            logger.warning("Azure PTU FOCUS costs failed: %s", focus_costs, exc_info=focus_costs)
            focus_costs = {
                "ptu_capacity_cost": 0.0,
                "billed": 0.0,
                "ia_ondemand": {"aura-brain": 0.0, "vivo-mais": 0.0},
                "shared_infra": {},
                "cost_rows": [],
                "cost_composition": {"ptu_capacity": 0.0, "ia_ondemand_total": 0.0, "shared_infra_total": 0.0, "total_ia": 0.0},
            }
        if isinstance(cost_series, Exception):
            logger.warning("Azure PTU cost series failed: %s", cost_series, exc_info=cost_series)
            cost_series = []

        ptu_capacity_cost = focus_costs["ptu_capacity_cost"]
        billed = focus_costs["billed"]
        ia_ondemand = focus_costs.get("ia_ondemand") or {"aura-brain": 0.0, "vivo-mais": 0.0}
        shared_infra = focus_costs.get("shared_infra") or {}
        cost_rows = focus_costs.get("cost_rows") or []
        cost_composition = focus_costs.get("cost_composition") or {}

        deployment_stats = _aggregate_deployment_utilization(metrics, inventory)
        imbalance = _deployment_imbalance(deployment_stats)
        utilization_series = _build_utilization_series(metrics, body.grain)
        projects = _build_project_stats(inventory, metrics, deployment_stats)

        for key, ondemand_amount in ia_ondemand.items():
            if key in projects:
                ptu_origin = float(projects[key].get("origin_cost") or 0)
                projects[key]["ia_ondemand_cost"] = ondemand_amount
                projects[key]["ptu_capacity_cost"] = ptu_origin or ptu_capacity_cost
                projects[key]["total_ia_cost"] = (ptu_origin or 0) + ondemand_amount

        kpis = _enrich_kpis(metrics, inventory, ptu_capacity_cost)
        kpis["billed_cost"] = billed
        kpis["cost_source"] = "focus_allowlist"
        kpis["billing_period"] = _billing_period(body)
        kpis["ia_ondemand"] = ia_ondemand
        kpis["shared_infra"] = shared_infra
        kpis["cost_composition"] = cost_composition
        mm = attach_origin_cost_per_million(
            origin_cost=float(kpis.get("realized_cost") or 0),
            total_tokens=float(kpis.get("total_tokens") or 0),
            input_tokens=float(kpis.get("input_tokens") or 0),
            output_tokens=float(kpis.get("output_tokens") or 0),
        )
        kpis["cost_per_million_tokens"] = mm["cost_per_million_tokens"]
        kpis["cost_per_million_input"] = mm["cost_per_million_input"]
        kpis["cost_per_million_output"] = mm["cost_per_million_output"]

        token_series = _build_token_series(metrics, body.grain)
        tokens_by_ts = {r["timestamp"]: r.get("total_tokens") or 0 for r in token_series}
        cost_per_million_series = [
            {
                "timestamp": row.get("timestamp"),
                "realized_cost": row.get("realized_cost"),
                "total_tokens": tokens_by_ts.get(row.get("timestamp")) or 0,
                "cost_per_million_tokens": cost_per_million_tokens(
                    float(row.get("realized_cost") or 0),
                    float(tokens_by_ts.get(row.get("timestamp")) or 0),
                ),
            }
            for row in cost_series
            if row.get("timestamp")
        ]

        recommendations = _build_recommendations(kpis, imbalance)

        return {
            "inventory": inventory,
            "metrics": metrics,
            "kpis": kpis,
            "origin_costs": [],
            "ia_ondemand": ia_ondemand,
            "shared_infra": shared_infra,
            "cost_rows": cost_rows,
            "cost_composition": cost_composition,
            "projects": projects,
            "deployment_utilization": deployment_stats,
            "deployments": deployment_stats,
            "deployment_imbalance": imbalance,
            "utilization_series": utilization_series,
            "utilization_heatmap": heatmap,
            "token_series": token_series,
            "cost_series": cost_series,
            "cost_per_million_series": cost_per_million_series,
            "recommendations": recommendations,
            "data_source": data_source,
            "filters": body.model_dump(),
        }


@router.post("/dashboard/ops-overview")
async def dashboard_ops_overview(body: OverviewRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Dashboard PTU leve — Dimon diária; Gold horária somente sob demanda."""
    async with _ClientCancelScope(request) as scope:
        cancel = scope.cancel_event
        cache_key = json.dumps(body.model_dump(mode="json"), sort_keys=True, default=str)
        cached = _OPS_OVERVIEW_CACHE.get(cache_key)
        if cached and time.monotonic() - cached[0] < _OPS_OVERVIEW_CACHE_TTL_S:
            return cached[1]
        date_from, date_to, _, _ = _dashboard_date_bounds(body)
        effective_grain = "month" if body.grain == "month" else "day"

        # Enriquecimento financeiro via Dimon (compras mensais de PTU e custos
        # dos projetos). Roda em paralelo com a carga operacional para não
        # adicionar latência perceptível; nunca derruba o endpoint.
        cost_task = asyncio.create_task(load_dimon_ptu_costs(
            date_from=date_from,
            date_to=date_to,
            project_keys=body.project_keys or None,
            cancel_event=cancel,
        ))

        try:
            inventory, metrics, data_source, heatmap = await _load_operational_data(
                body, db, dashboard_only=True, cancel_event=cancel,
            )
        except Exception as exc:
            logger.warning("Azure PTU ops operational data failed: %s", exc, exc_info=exc)
            inventory, metrics, data_source, heatmap = [], [], "stale", []

        previous_metrics: list[dict[str, Any]] = []
        try:
            prev_body = OverviewRequest(
                **{
                    **body.model_dump(),
                    "hours": body.hours,
                    "date_to": date_from,
                    "date_from": (
                        datetime.fromisoformat(date_from + "T00:00:00+00:00") - timedelta(hours=body.hours)
                    ).strftime("%Y-%m-%d"),
                }
            )
            # O comparativo também usa a Dimon; nenhuma consulta Gold ocorre
            # durante a abertura normal do dashboard.
            connections = (await db.execute(
                select(SourceConnection).where(SourceConnection.connector_type == "azure_openai_ptu")
            )).scalars().all()
            previous_metrics = await load_dimon_metrics(
                hours=prev_body.hours,
                connection_ids=[c.id for c in connections],
                project_hints=prev_body.project_keys or None,
                grain=effective_grain,
                timeout_s=DASHBOARD_TRINO_TIMEOUT_S,
            )
        except Exception:
            previous_metrics = []

        deployment_stats = _aggregate_deployment_utilization(metrics, inventory)
        imbalance = _deployment_imbalance(deployment_stats)
        utilization_series = _build_utilization_series(metrics, effective_grain)
        token_series = _build_token_series(metrics, effective_grain)
        latency_series = _build_latency_series(metrics, effective_grain)
        rate_limit_series = _build_rate_limit_series(metrics, effective_grain)
        projects = _build_project_stats(inventory, metrics, deployment_stats)
        kpis = _enrich_ops_kpis(metrics, inventory)
        period = period_metadata(
            hours=body.hours,
            requested_grain=body.grain,
            effective_grain=effective_grain,
            metrics=metrics,
            date_from=date_from,
            date_to=date_to,
        )
        executive_summary = build_executive_summary(
            metrics, inventory, previous_metrics=previous_metrics, period=period,
        )
        recommendations = build_ops_recommendations(
            metrics, deployment_stats, imbalance, period=period,
        )
        coverage = _build_metrics_coverage(metrics, body.hours)
        metric_availability = metric_field_availability(metrics)
        costs = await cost_task

        # ── Tokens dos últimos 30 dias (base fixa do custo por milhão) ──────
        # A compra de PTU é mensal (a cada 30 dias); o custo por milhão de
        # tokens deve ser balizado sempre pelos últimos 30 dias, independente
        # da janela selecionada (7d/30d/90d) na tela.
        tokens_30d_by_project: dict[str, float] = {}
        if body.hours == 720:
            metrics_30d = metrics
        else:
            try:
                conns_30d = (await db.execute(
                    select(SourceConnection).where(SourceConnection.connector_type == "azure_openai_ptu")
                )).scalars().all()
                metrics_30d = await load_dimon_metrics(
                    hours=720,
                    connection_ids=[c.id for c in conns_30d],
                    project_hints=body.project_keys or None,
                    grain="day",
                    timeout_s=DASHBOARD_TRINO_TIMEOUT_S,
                )
            except Exception:
                logger.warning("Azure PTU 30d tokens load failed: %s", exc_info=True)
                metrics_30d = []
        for _row in metrics_30d:
            _key = str(_row.get("project_key") or _row.get("project_hint") or "unattributed")
            tokens_30d_by_project[_key] = tokens_30d_by_project.get(_key, 0.0) + float(
                _row.get("total_tokens_sum") or _row.get("total_tokens") or 0
            )
        tokens_30d_total = sum(tokens_30d_by_project.values())

        token_consumption = build_token_consumption(
            metrics, deployment_stats, token_series, effective_grain,
        )
        token_consumption["tokens_last_30d"] = {
            "total": tokens_30d_total,
            "by_project": tokens_30d_by_project,
        }

        response_payload = {
            "period": period,
            "executive_summary": executive_summary,
            "costs": costs,
            "token_consumption": token_consumption,
            # Preenchido somente pelo endpoint /ops-heatmap, acionado pelo usuário.
            "token_heatmap": [
                {"weekday": cell["weekday"], "hour": cell["hour"],
                 "input_tokens": cell.get("input_tokens", 0),
                 "output_tokens": cell.get("output_tokens", 0),
                 "total_tokens": cell.get("total_tokens", 0),
                 "samples": cell.get("samples", 0)}
                for cell in heatmap
            ],
            "capacity_risk": build_capacity_risk(
                metrics, deployment_stats, utilization_series, heatmap, effective_grain,
            ),
            "service_quality": build_service_quality(metrics, latency_series, rate_limit_series),
            "metric_availability": metric_availability,
            "inventory": inventory,
            "kpis": kpis,
            "projects": projects,
            "deployment_utilization": deployment_stats,
            "deployments": deployment_stats,
            "deployment_imbalance": imbalance,
            "utilization_series": utilization_series,
            "utilization_heatmap": heatmap,
            "token_series": token_series,
            "latency_series": latency_series,
            "rate_limit_series": rate_limit_series,
            "recommendations": recommendations,
            "data_source": data_source,
            "data_coverage": coverage,
            "filters": body.model_dump(),
        }
        _OPS_OVERVIEW_CACHE[cache_key] = (time.monotonic(), response_payload)
        # Bound memory if many project/date combinations are explored.
        if len(_OPS_OVERVIEW_CACHE) > 64:
            oldest = sorted(_OPS_OVERVIEW_CACHE.items(), key=lambda item: item[1][0])[:16]
            for key, _ in oldest:
                _OPS_OVERVIEW_CACHE.pop(key, None)
        return response_payload


@router.post("/dashboard/executive-overview")
async def dashboard_executive_overview(body: OverviewRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Independent operational payload. Cost joins are defined separately over Gold layers."""
    return await dashboard_ops_overview(body, request, db)


class ProjectCostRequest(BaseModel):
    project_key: str = Field(..., pattern="^(aura-brain|vivo-mais)$")
    period_range: str = Field("current_year", pattern="^(current_year|all)$")


@router.post("/dashboard/project-costs")
async def dashboard_project_costs(body: ProjectCostRequest, request: Request):
    """Dashboard executivo de custo de um projeto (Aura Brain | Vivo Mais).

    Lê a Dimon específica do projeto (incluindo
    ``dest_dm_cost_custo_vivo_mais`` para Vivo Mais) e decompõe o custo
    mensal em PTUs (compra mensal: realizada se existir, senão forecast),
    tokens on-demand e infra (por servicesubcategory), com a série mensal
    realizado + forecast e a distribuição por subconta. O consumo diário/horário
    de capacidade (Provisioned Managed*, 1/Hour) é excluído — já está na compra.
    """
    async with _ClientCancelScope(request) as scope:
        try:
            return await load_project_cost_dashboard(
                project_key=body.project_key,
                period_range=body.period_range,
                cancel_event=scope.cancel_event,
            )
        except Exception as exc:
            logger.warning("Project cost dashboard failed (%s): %s", body.project_key, exc, exc_info=exc)
            raise HTTPException(
                status_code=502,
                detail=f"Falha ao consultar o custo do projeto {body.project_key}: {exc}",
            ) from exc


@router.post("/dashboard/sinfonia")
async def dashboard_sinfonia(request: Request):
    """Visão executiva consolidada IBM + Azure/Fábrica de Agentes."""
    async with _ClientCancelScope(request) as scope:
        try:
            return await load_sinfonia_dashboard(cancel_event=scope.cancel_event)
        except Exception as exc:
            logger.warning("Sinfonia dashboard failed: %s", exc, exc_info=exc)
            raise HTTPException(status_code=502, detail=f"Falha ao consultar o dashboard Sinfonia: {exc}") from exc


@router.post("/dashboard/datamesh")
async def dashboard_datamesh(request: Request):
    """Visão executiva Datamesh: Databricks, dados associados e infraestrutura."""
    async with _ClientCancelScope(request) as scope:
        try:
            return await load_datamesh_dashboard(cancel_event=scope.cancel_event)
        except Exception as exc:
            logger.warning("Datamesh dashboard failed: %s", exc, exc_info=exc)
            raise HTTPException(status_code=502, detail=f"Falha ao consultar o dashboard Datamesh: {exc}") from exc

@router.post("/dashboard/wisper")
async def dashboard_wisper(request: Request):
    async with _ClientCancelScope(request) as scope:
        return await load_wisper_dashboard(cancel_event=scope.cancel_event)


async def _dashboard_data(body: DashboardFilter, db: AsyncSession) -> dict[str, Any]:
    payload = body.model_dump()
    hours = int(payload.get("hours") or _dashboard_hours(str(payload.get("grain") or "day")))
    raw = await dashboard_overview(
        OverviewRequest(**{**payload, "hours": hours}),
        Request({"type": "http", "headers": []}),
        db,
    )
    return raw


@router.post("/dashboard/projects")
async def dashboard_projects(body: DashboardFilter, db: AsyncSession = Depends(get_db)):
    data = await _dashboard_data(body, db)
    return {
        "projects": list((data.get("projects") or {}).values()),
        "origin_costs": data.get("origin_costs") or [],
        "deployment_imbalance": data.get("deployment_imbalance") or [],
        "filters": body.model_dump(),
    }


@router.post("/dashboard/deployments")
async def dashboard_deployments(body: DashboardFilter, db: AsyncSession = Depends(get_db)):
    data = await _dashboard_data(body, db)
    return {
        "deployments": data.get("inventory") or [],
        "deployment_utilization": data.get("deployment_utilization") or [],
        "deployment_imbalance": data.get("deployment_imbalance") or [],
        "metrics": data.get("metrics") or [],
        "filters": body.model_dump(),
    }


@router.post("/dashboard/subprojects")
async def dashboard_subprojects(body: DashboardFilter, db: AsyncSession = Depends(get_db)):
    rows = await list_subprojects(None, db)
    if body.project_keys:
        rows = [r for r in rows if r.get("project_key") in body.project_keys]
    attribution: list[dict[str, Any]] = []
    try:
        project_filter = ""
        if body.project_keys:
            keys = ", ".join(f"'{k}'" for k in body.project_keys)
            project_filter = f" AND CAST(project_key AS VARCHAR) IN ({keys})"
        usage_rows = await trino_execute(f"""
          SELECT CAST(project_key AS VARCHAR) AS project_key,
                 CAST(subproject_key AS VARCHAR) AS subproject_key,
                 COALESCE(SUM(TRY_CAST(total_tokens AS DOUBLE)), 0) AS total_tokens
          FROM iceberg.gold.dest_azure_ptu_subproject_usage
          WHERE 1 = 1{project_filter}
          GROUP BY 1, 2
          ORDER BY 3 DESC
        """, timeout_s=DASHBOARD_TRINO_TIMEOUT_S)
        start_iso, end_iso = _gold_metrics_window(body.hours)
        hour_filter = _gold_metric_hour_filter(start_iso, end_iso)
        monitor_rows = await trino_execute(f"""
          SELECT CAST(project_hint AS VARCHAR) AS project_key,
                 COALESCE(SUM(TRY_CAST(total_tokens_sum AS DOUBLE)), 0) AS monitor_tokens
          FROM iceberg.gold.dest_azure_ptu_metrics
          WHERE {hour_filter}{project_filter.replace('project_key', 'project_hint')}
          GROUP BY 1
        """, timeout_s=DASHBOARD_TRINO_TIMEOUT_S)
        monitor_by_project = {r.get("project_key"): float(r.get("monitor_tokens") or 0) for r in (monitor_rows or [])}
        from app.services.azure_ptu_meters import split_deployment_cost_by_tokens
        for row in usage_rows or []:
            project = row.get("project_key")
            tokens = float(row.get("total_tokens") or 0)
            monitor = monitor_by_project.get(project, 0)
            ptu_equiv = (tokens / monitor * 100) if monitor else None
            attribution.append({
                "project_key": project,
                "subproject_key": row.get("subproject_key"),
                "total_tokens": tokens,
                "ptu_equivalent_pct": round(ptu_equiv, 2) if ptu_equiv is not None else None,
                "note": "Estimativa PTU-equivalente = tokens subprojeto / tokens Monitor do projeto",
            })
    except Exception as exc:
        logger.warning("Azure PTU subproject attribution query failed: %s", exc, exc_info=True)
        attribution = []
    return {
        "subprojects": rows,
        "attribution": attribution,
        "attribution_source": "application_eventhub",
        "note": "Sem eventos instrumentados, o consumo permanece unattributed.",
    }


@router.post("/dashboard/trends")
async def dashboard_trends(body: DashboardFilter, db: AsyncSession = Depends(get_db)):
    """Lightweight trends: Gold metrics + one dated FOCUS cost aggregation (no full overview)."""
    date_from, date_to, _, _ = _dashboard_date_bounds(body)
    inventory, metrics, data_source, heatmap = await _load_operational_data(
        body, db, dashboard_only=True,
    )
    token_series = _build_token_series(metrics, body.grain)
    cost_series = await _build_cost_series(body.grain, date_from, date_to)
    utilization_series = _build_utilization_series(metrics, body.grain)
    deployment_stats = _aggregate_deployment_utilization(metrics, inventory)
    tokens_by_ts = {r["timestamp"]: r.get("total_tokens") or 0 for r in token_series}
    cost_per_million_series = [
        {
            "timestamp": ts,
            "realized_cost": row.get("realized_cost"),
            "total_tokens": tokens_by_ts.get(ts) or 0,
            "cost_per_million_tokens": cost_per_million_tokens(
                float(row.get("realized_cost") or 0),
                float(tokens_by_ts.get(ts) or 0),
            ),
        }
        for row in cost_series
        for ts in [row.get("timestamp")]
        if ts
    ]
    return {
        "series": utilization_series,
        "token_series": token_series,
        "cost_series": cost_series,
        "cost_per_million_series": cost_per_million_series,
        "heatmap": heatmap,
        "deployment_utilization": deployment_stats,
        "grain": body.grain,
        "data_source": data_source,
        "bands": {"30": 30, "80": 80, "95": 95, "100": 100},
        "filters": body.model_dump(),
    }


@router.post("/dashboard/reconciliation")
async def dashboard_reconciliation(body: DashboardFilter, db: AsyncSession = Depends(get_db)):
    data = await _dashboard_data(body, db)
    return {"financial": {"realized_cost": data["kpis"].get("realized_cost")}, "operational": data["kpis"], "status": "PARTIAL", "coverage_pct": None, "message": "Alocação por subprojeto depende da instrumentação das aplicações."}


@router.post("/dashboard/recommendations")
async def dashboard_recommendations(body: DashboardFilter, request: Request, db: AsyncSession = Depends(get_db)):
    data = await dashboard_overview(OverviewRequest(**body.model_dump()), request, db)
    return {"recommendations": data.get("recommendations") or [], "filters": body.model_dump()}
