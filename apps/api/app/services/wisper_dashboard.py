"""Financial executive payload for Wisper OCI AI (v2).

This service builds an analytics-ready payload for the "Wisper — OCI AI" view.
It reads the canonical financial source (gold FOCUS) directly so that daily
grain, unit prices and resource IDs are available. Operational data comes from
the OCI telemetry gold table and is explicitly filtered to the Wisper scope.
"""
from __future__ import annotations

import asyncio
import calendar
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from app.services.trino_service import execute as trino_execute


# Source of truth for Wisper financials: FOCUS gold filtered by SubAccountName
FOCUS_TABLE = "iceberg.gold.dest_modelo_focus"
# Operational/inventory source
OP_METRICS_TABLE = "iceberg.gold.dest_oci_operational_metrics"
# Legacy dimon table (kept only for current-month forecast comparison)
DIMON_TABLE = "iceberg.dimon.dest_dm_cost_custo_wisper"

WISPER_SUBACCOUNT_RE = "whisper|llm|poc-stt"


def _s(v: Any) -> str:
    return str(v or "").strip()


def _label(period: str) -> str:
    names = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
    return f"{names[int(period[4:6]) - 1]}/{period[:4]}"


def _money(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _bucket(service: str, subcategory: str, sku: str) -> str:
    service, subcategory, sku = service.upper(), subcategory.upper(), sku.upper()
    if service == "GENERATIVE_AI":
        return "Generative AI"
    if subcategory == "GPU HOURS" or sku in {"B95909", "B97384", "B97385"}:
        return "Hospedagem GPU"
    if service in {"OBJECTSTORE", "DATABASE", "BLOCK_STORAGE"}:
        return "Dados e armazenamento"
    return "Infraestrutura"


def _month_label(period: str) -> str:
    return _label(period)


def _now_period() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m")


def _period_date(period: str, day: int) -> str:
    return f"{period[:4]}-{period[4:6]}-{day:02d}"


def _add_insight(name: str, value: Any, note: str = "") -> dict[str, Any]:
    return {"name": name, "value": value, "note": note}


async def _financials(
    scope_sql: str,
    current_period: str,
    cancel_event: asyncio.Event | None,
) -> dict[str, Any]:
    """Return monthly series, buckets, SKUs, subaccounts and daily series."""

    # 1. Monthly series (realizado) — used by the chart and to pick the closed period
    monthly_rows = await trino_execute(
        f"""
        SELECT _billing_period AS period,
               SUM(billedcost) AS total,
               servicename,
               servicesubcategory,
               skumeter,
               SUM(billedcost) AS bucket_cost,
               SUM(consumedquantity) AS bucket_qty
        FROM {FOCUS_TABLE}
        WHERE {scope_sql}
        GROUP BY 1, 3, 4, 5
        ORDER BY 1, 3, 4, 5
        """,
        timeout_s=120,
        cancel_event=cancel_event,
    )

    # Build period bucket map
    by_period: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    period_total: dict[str, float] = defaultdict(float)
    for r in monthly_rows or []:
        p = _s(r.get("period"))
        if not p:
            continue
        bucket = _bucket(
            _s(r.get("servicename")),
            _s(r.get("servicesubcategory")),
            _s(r.get("skumeter")),
        )
        cost = _money(r.get("bucket_cost"))
        by_period[p][bucket] += cost
        period_total[p] += cost

    all_periods = sorted(by_period.keys())
    if not all_periods:
        return {"available": True, "empty": True}

    # 2. Determine closed period (last complete month before current)
    closed_candidates = [p for p in all_periods if p < current_period]
    closed_period = closed_candidates[-1] if closed_candidates else all_periods[-1]

    # 3. SKUs / capacity for the closed period
    sku_rows = await trino_execute(
        f"""
        SELECT servicename,
               servicesubcategory,
               skumeter,
               consumedunit,
               MAX(regionname) AS regionname,
               SUM(billedcost) AS cost,
               SUM(consumedquantity) AS quantity,
               SUM(COALESCE(listcost, 0)) AS list_cost,
               SUM(COALESCE(effectivecost, 0)) AS effective_cost,
               COUNT(DISTINCT resourceid) AS resources
        FROM {FOCUS_TABLE}
        WHERE {scope_sql}
          AND _billing_period = '{closed_period}'
        GROUP BY 1, 2, 3, 4
        ORDER BY 5 DESC
        """,
        timeout_s=120,
        cancel_event=cancel_event,
    )

    # MoM per SKU (cost in previous closed period)
    prev_period = _prev_period(closed_period)
    prev_sku_rows = await trino_execute(
        f"""
        SELECT skumeter, SUM(billedcost) AS cost
        FROM {FOCUS_TABLE}
        WHERE {scope_sql}
          AND _billing_period = '{prev_period}'
        GROUP BY 1
        """,
        timeout_s=60,
        cancel_event=cancel_event,
    )
    prev_sku_cost = {_s(r.get("skumeter")): _money(r.get("cost")) for r in prev_sku_rows or []}

    # Shapes per SKU (via operational inventory resource_id -> unit_shape)
    shapes_by_sku: dict[str, list[tuple[str, int]]] = defaultdict(list)
    try:
        shape_rows = await trino_execute(
            f"""
            SELECT f.skumeter AS sku,
                   COALESCE(i.unit_shape, '') AS shape,
                   COUNT(DISTINCT f.resourceid) AS recursos
            FROM {FOCUS_TABLE} f
            LEFT JOIN (
                SELECT DISTINCT resource_id, unit_shape
                FROM {OP_METRICS_TABLE}
                WHERE record_type = 'inventory'
            ) i ON i.resource_id = f.resourceid
            WHERE {scope_sql}
              AND f._billing_period = '{closed_period}'
              AND f.skumeter IS NOT NULL
            GROUP BY 1, 2
            """,
            timeout_s=120,
            cancel_event=cancel_event,
        )
        for r in shape_rows or []:
            sku = _s(r.get("sku"))
            shape = _s(r.get("shape"))
            recursos = int(r.get("recursos") or 0)
            if sku and shape:
                shapes_by_sku[sku].append((shape, recursos))
    except Exception:
        pass

    # Fallback: fleet shapes of the project's Dedicated AI Clusters (accessible
    # inventory) for Generative AI SKUs when the billed resource is not inventoried
    # (billing compartments return 404 without IAM read on compute/generativeai).
    fleet_shapes: list[tuple[str, int]] = []
    try:
        fleet_rows = await trino_execute(
            f"""
            SELECT unit_shape, SUM(unit_count) AS units
            FROM {OP_METRICS_TABLE}
            WHERE record_type = 'inventory'
              AND resource_type = 'dedicated_ai_cluster'
            GROUP BY 1
            """,
            timeout_s=30,
            cancel_event=cancel_event,
        )
        fleet_shapes = [
            (_s(r.get("unit_shape")), int(r.get("units") or 0))
            for r in fleet_rows or []
            if _s(r.get("unit_shape"))
        ]
    except Exception:
        pass

    skus: list[dict[str, Any]] = []
    gpu_hours = ai_unit_hours = ocpu_hours = gb_hours = 0.0
    for r in sku_rows or []:
        sku = _s(r.get("skumeter")) or "Não informado"
        qty = _money(r.get("quantity"))
        cost = _money(r.get("cost"))
        list_cost = _money(r.get("list_cost"))
        effective_cost = _money(r.get("effective_cost"))
        resources = int(r.get("resources") or 0)
        prev_cost = prev_sku_cost.get(sku)
        shapes = sorted(shapes_by_sku.get(sku, []), key=lambda x: -x[1])
        if shapes:
            shapes_text = ", ".join(f"{shape} × {n}" for shape, n in shapes)
        elif _s(r.get("servicename")).upper() == "GENERATIVE_AI" and fleet_shapes:
            shapes_text = ", ".join(f"{shape} × {units} (dedicado)" for shape, units in fleet_shapes)
        else:
            shapes_text = ""
        skus.append(
            {
                "sku": sku,
                "service": _s(r.get("servicename")),
                "description": f"{_s(r.get('servicename'))} — {_s(r.get('servicesubcategory'))}",
                "subcategory": _s(r.get("servicesubcategory")),
                "quantity": qty,
                "unit": _s(r.get("consumedunit")) or "un",
                "cost": cost,
                "unit_price": round(cost / qty, 4) if qty else None,
                "region": _s(r.get("regionname")) or "—",
                "resources": resources,
                "shapes": shapes_text,
                "list_cost": round(list_cost, 2),
                "effective_cost": round(effective_cost, 2),
                "discount_pct": round((1 - effective_cost / list_cost) * 100, 2) if list_cost else None,
                "mom_delta_pct": round((cost - prev_cost) / prev_cost * 100, 2) if prev_cost else None,
            }
        )
        if sku == "B95909" or _s(r.get("servicesubcategory")).lower() == "gpu hours":
            gpu_hours += qty
        if sku == "B108085":
            ai_unit_hours += qty
        if sku == "B97384":
            ocpu_hours += qty
        if sku == "B97385":
            gb_hours += qty

    hours_in_closed = 24 * calendar.monthrange(int(closed_period[:4]), int(closed_period[4:6]))[1]

    # 4. Daily series for closed period + current partial (used for run-rate and insights).
    # Only the dates of the two displayed months — cross-month usage (e.g. start 2026-06-30
    # billed in July) stays out of the chart so the bars reconcile with the month KPIs.
    closed_month = closed_period[:4] + "-" + closed_period[4:6]
    current_month = current_period[:4] + "-" + current_period[4:6]
    daily_rows = await trino_execute(
        f"""
        SELECT substr(chargeperiodstart, 1, 10) AS dia,
               SUM(billedcost) AS custo,
               SUM(CASE WHEN skumeter = 'B95909' THEN consumedquantity ELSE 0 END) AS gpu_hours,
               SUM(CASE WHEN skumeter = 'B108085' THEN consumedquantity ELSE 0 END) AS ai_unit_hours,
               COUNT(*) AS linhas
        FROM {FOCUS_TABLE}
        WHERE {scope_sql}
          AND substr(chargeperiodstart, 1, 7) IN ('{closed_month}', '{current_month}')
        GROUP BY 1
        ORDER BY 1
        """,
        timeout_s=120,
        cancel_event=cancel_event,
    )

    daily: list[dict[str, Any]] = []
    closed_daily: list[float] = []
    current_daily: list[float] = []
    for r in daily_rows or []:
        dia = _s(r.get("dia"))
        custo = _money(r.get("custo"))
        gpu_hours = _money(r.get("gpu_hours"))
        ai_unit_hours = _money(r.get("ai_unit_hours"))
        daily.append(
            {
                "date": dia,
                "cost": custo,
                "gpu_hours": round(gpu_hours, 2),
                "gpu_units": round(gpu_hours / 24, 2) if gpu_hours else 0.0,
                "ai_unit_hours": round(ai_unit_hours, 2),
                "ai_units": round(ai_unit_hours / 24, 2) if ai_unit_hours else 0.0,
            }
        )
        if dia.startswith(closed_period[:4] + "-" + closed_period[4:6]):
            closed_daily.append(custo)
        if dia.startswith(current_period[:4] + "-" + current_period[4:6]):
            current_daily.append(custo)

    # 5. Current partial month and run-rate projection
    # Partial = billing period total (same source as the monthly chart), so the
    # KPI and the chart can never diverge. Daily grain is used only for the
    # run-rate / projection and the daily chart.
    current_partial = period_total.get(current_period, 0.0)
    run_rate_7d = 0.0
    if current_daily:
        run_rate_7d = sum(current_daily[-7:]) / min(len(current_daily[-7:]), 7)
    elif closed_daily:
        run_rate_7d = sum(closed_daily[-7:]) / min(len(closed_daily[-7:]), 7)

    current_days = calendar.monthrange(int(current_period[:4]), int(current_period[4:6]))[1]
    projection = run_rate_7d * current_days if run_rate_7d else 0.0

    # 6. Forecast from dimon (for comparison only — never use as KPI)
    forecast_current = 0.0
    try:
        forecast_rows = await trino_execute(
            f"""
            SELECT SUM(realizado) AS forecast_value
            FROM {DIMON_TABLE}
            WHERE _billing_period = '{current_period}'
              AND lower(tipo) = 'forecast'
            """,
            timeout_s=30,
            cancel_event=cancel_event,
        )
        if forecast_rows:
            forecast_current = _money(forecast_rows[0].get("forecast_value"))
    except Exception:
        forecast_current = 0.0

    # 7. Subaccounts for closed period
    sub_rows = await trino_execute(
        f"""
        SELECT subaccountname,
               SUM(billedcost) AS cost
        FROM {FOCUS_TABLE}
        WHERE {scope_sql}
          AND _billing_period = '{closed_period}'
        GROUP BY 1
        ORDER BY 2 DESC
        """,
        timeout_s=60,
        cancel_event=cancel_event,
    )
    subaccounts = [
        {"name": _s(r.get("subaccountname")) or "Não informado", "cost": _money(r.get("cost"))}
        for r in sub_rows or []
    ]

    # 8. Insights / anomalies
    insights: list[dict[str, Any]] = []
    if closed_daily and run_rate_7d:
        # detect step-downs / step-ups in the last 14 days of the closed period
        window = [c for c in closed_daily if c > 0]
        if len(window) >= 7:
            first_week = sum(window[:7]) / 7
            last_week = sum(window[-7:]) / 7
            if first_week > 0:
                change = (last_week - first_week) / first_week
                if change < -0.25:
                    insights.append(
                        _add_insight(
                            "step_down",
                            round(change * 100, 1),
                            f"Run-rate médio caiu {abs(round(change * 100, 1))}% na última semana de {_label(closed_period)} vs. primeira semana.",
                        )
                    )
                elif change > 0.25:
                    insights.append(
                        _add_insight(
                            "step_up",
                            round(change * 100, 1),
                            f"Run-rate médio subiu {round(change * 100, 1)}% na última semana.",
                        )
                    )

    closed_total = period_total.get(closed_period, 0.0)
    prev_total = period_total.get(prev_period, 0.0)
    mom = ((closed_total - prev_total) / prev_total * 100) if prev_total else None

    # Bucket breakdown for closed period
    buckets = {k: by_period[closed_period].get(k, 0.0) for k in ("Generative AI", "Hospedagem GPU", "Dados e armazenamento", "Infraestrutura")}

    # Monthly chart series (realizado for every collected period)
    monthly = []
    for p in all_periods:
        vals = {k: by_period[p].get(k, 0.0) for k in ("Generative AI", "Hospedagem GPU", "Dados e armazenamento", "Infraestrutura")}
        monthly.append(
            {
                "period": p,
                "label": _label(p),
                "buckets": vals,
                "total": period_total.get(p, 0.0),
                "is_partial": p == current_period,
            }
        )

    return {
        "available": True,
        "empty": False,
        "reference": {
            "closed_period": closed_period,
            "closed_label": _label(closed_period),
            "current_period": current_period,
            "current_label": _label(current_period),
        },
        "executive": {
            "total_closed": round(closed_total, 2),
            "total_prev_closed": round(prev_total, 2),
            "mom_delta_pct": round(mom, 2) if mom is not None else None,
            "current_partial": round(current_partial, 2),
            "current_forecast": round(forecast_current, 2),
            "run_rate_daily": round(run_rate_7d, 2),
            "monthly_projection": round(projection, 2),
        },
        "buckets": {
            k: {
                "cost": round(v, 2),
                "share_pct": round(v / closed_total * 100, 2) if closed_total else 0.0,
                "prev": round(by_period.get(prev_period, {}).get(k, 0.0), 2),
            }
            for k, v in buckets.items()
        },
        "capacity": {
            "gpu_hours": round(gpu_hours, 2),
            "gpu_average": round(gpu_hours / hours_in_closed, 2) if hours_in_closed else 0.0,
            "ai_unit_hours": round(ai_unit_hours, 2),
            "ai_unit_average": round(ai_unit_hours / hours_in_closed, 2) if hours_in_closed else 0.0,
            "ocpu_hours": round(ocpu_hours, 2),
            "gb_hours": round(gb_hours, 2),
            "daily": daily,
        },
        "skus": [
            {
                **s,
                "cost": round(s["cost"], 2),
                "quantity": round(s["quantity"], 2),
                "share_pct": round(s["cost"] / closed_total * 100, 2) if closed_total else 0.0,
            }
            for s in skus
        ],
        "subaccounts": [{**s, "cost": round(s["cost"], 2)} for s in subaccounts],
        "monthly": monthly,
        "insights": insights,
    }


def _prev_period(period: str) -> str:
    y, m = int(period[:4]), int(period[4:6])
    m -= 1
    if m == 0:
        y -= 1
        m = 12
    return f"{y}{m:02d}"


async def _operational(
    wisper_compartment_ids: list[str],
    cancel_event: asyncio.Event | None,
) -> dict[str, Any]:
    """Return OCI operational metrics and inventory filtered to the Wisper scope."""

    compartment_filter = ""
    if wisper_compartment_ids:
        quoted = ", ".join(f"'{c}'" for c in wisper_compartment_ids)
        compartment_filter = f"AND compartment_id IN ({quoted})"

    # Metrics — last 90 days (or whatever is available)
    metrics_rows = []
    try:
        metrics_rows = await trino_execute(
            f"""
            SELECT metric_name,
                   SUM(metric_value) AS total_value,
                   AVG(metric_value) AS average_value,
                   APPROX_PERCENTILE(metric_value, 0.5) AS p50_value,
                   COUNT(*) AS points
            FROM {OP_METRICS_TABLE}
            WHERE record_type = 'metric'
              AND from_iso8601_timestamp(timestamp) >= current_timestamp - INTERVAL '90' DAY
              {compartment_filter}
            GROUP BY 1
            """,
            timeout_s=60,
            cancel_event=cancel_event,
        )
    except Exception:
        metrics_rows = []

    by_name = {str(row.get("metric_name")): row for row in metrics_rows or []}
    invocations = _money(by_name.get("TotalInvocationCount", {}).get("total_value"))
    errors = _money(by_name.get("ClientErrorCount", {}).get("total_value")) + _money(
        by_name.get("ServerErrorCount", {}).get("total_value")
    )

    # Window coverage
    window = {"min": None, "max": None, "days": None}
    try:
        window_rows = await trino_execute(
            f"""
            SELECT MIN(timestamp) AS min_ts,
                   MAX(timestamp) AS max_ts,
                   COUNT(*) AS points
            FROM {OP_METRICS_TABLE}
            WHERE record_type = 'metric' {compartment_filter}
            """,
            timeout_s=30,
            cancel_event=cancel_event,
        )
        if window_rows:
            min_ts = _s(window_rows[0].get("min_ts"))
            max_ts = _s(window_rows[0].get("max_ts"))
            points = int(window_rows[0].get("points") or 0)
            days = None
            if min_ts and max_ts:
                try:
                    days = (
                        datetime.fromisoformat(max_ts.replace("Z", "+00:00"))
                        - datetime.fromisoformat(min_ts.replace("Z", "+00:00"))
                    ).days + 1
                except Exception:
                    pass
            window = {"min": min_ts or None, "max": max_ts or None, "days": days, "points": points}
    except Exception:
        pass

    # Daily operational series
    daily_ops: list[dict[str, Any]] = []
    try:
        daily_rows = await trino_execute(
            f"""
            SELECT substr(CAST(timestamp AS VARCHAR), 1, 10) AS dia,
                   SUM(CASE WHEN metric_name = 'InputTokenCount' THEN metric_value ELSE 0 END) AS input_tokens,
                   SUM(CASE WHEN metric_name = 'OutputTokenCount' THEN metric_value ELSE 0 END) AS output_tokens,
                   SUM(CASE WHEN metric_name = 'TotalInvocationCount' THEN metric_value ELSE 0 END) AS invocations,
                   SUM(CASE WHEN metric_name = 'ClientErrorCount' THEN metric_value ELSE 0 END) AS client_errors,
                   SUM(CASE WHEN metric_name = 'ServerErrorCount' THEN metric_value ELSE 0 END) AS server_errors,
                   AVG(CASE WHEN metric_name = 'InvocationLatency' THEN metric_value END) AS latency_avg
            FROM {OP_METRICS_TABLE}
            WHERE record_type = 'metric'
              AND from_iso8601_timestamp(timestamp) >= current_timestamp - INTERVAL '90' DAY
              {compartment_filter}
            GROUP BY 1
            ORDER BY 1
            """,
            timeout_s=60,
            cancel_event=cancel_event,
        )
        for r in daily_rows or []:
            daily_ops.append(
                {
                    "date": _s(r.get("dia")),
                    "input_tokens": round(_money(r.get("input_tokens")), 2),
                    "output_tokens": round(_money(r.get("output_tokens")), 2),
                    "invocations": round(_money(r.get("invocations")), 2),
                    "errors": round(_money(r.get("client_errors")) + _money(r.get("server_errors")), 2),
                    "latency_avg_ms": round(_money(r.get("latency_avg")), 2),
                }
            )
    except Exception:
        pass

    # Inventory — latest snapshot only
    inventory: list[dict[str, Any]] = []
    try:
        inv_rows = await trino_execute(
            f"""
            SELECT resource_type,
                   resource_name,
                   region,
                   model_id,
                   unit_shape,
                   unit_count,
                   lifecycle_state,
                   compartment_id,
                   compartment_name,
                   collected_at
            FROM {OP_METRICS_TABLE}
            WHERE record_type = 'inventory'
              {compartment_filter}
              AND collected_at = (
                  SELECT MAX(collected_at)
                  FROM {OP_METRICS_TABLE}
                  WHERE record_type = 'inventory'
                    {compartment_filter}
              )
            ORDER BY resource_type, resource_name
            """,
            timeout_s=60,
            cancel_event=cancel_event,
        )
        for r in inv_rows or []:
            inventory.append(
                {
                    "resource_type": _s(r.get("resource_type")),
                    "resource_name": _s(r.get("resource_name")),
                    "region": _s(r.get("region")),
                    "model_id": _s(r.get("model_id")),
                    "unit_shape": _s(r.get("unit_shape")),
                    "unit_count": _money(r.get("unit_count")),
                    "lifecycle_state": _s(r.get("lifecycle_state")),
                    "compartment_id": _s(r.get("compartment_id")),
                    "compartment_name": _s(r.get("compartment_name")),
                    "collected_at": _s(r.get("collected_at")),
                }
            )
    except Exception:
        # Fallback: read without collected_at filter if the column is missing (legacy schema)
        try:
            inv_rows = await trino_execute(
                f"""
                SELECT resource_type,
                       resource_name,
                       region,
                       model_id,
                       unit_shape,
                       unit_count,
                       lifecycle_state,
                       compartment_id
                FROM {OP_METRICS_TABLE}
                WHERE record_type = 'inventory'
                  {compartment_filter}
                ORDER BY resource_type, resource_name
                """,
                timeout_s=60,
                cancel_event=cancel_event,
            )
            for r in inv_rows or []:
                inventory.append(
                    {
                        "resource_type": _s(r.get("resource_type")),
                        "resource_name": _s(r.get("resource_name")),
                        "region": _s(r.get("region")),
                        "model_id": _s(r.get("model_id")),
                        "unit_shape": _s(r.get("unit_shape")),
                        "unit_count": _money(r.get("unit_count")),
                        "lifecycle_state": _s(r.get("lifecycle_state")),
                        "compartment_id": _s(r.get("compartment_id")),
                        "compartment_name": "",
                        "collected_at": None,
                    }
                )
        except Exception:
            pass

    # Aggregate inventory for the executive view
    ai_clusters: list[dict[str, Any]] = []
    endpoints: list[dict[str, Any]] = []
    compute_by_shape: dict[str, dict[str, Any]] = {}
    gpu_units = 0
    for r in inventory:
        rt = r["resource_type"]
        if rt == "dedicated_ai_cluster":
            ai_clusters.append(r)
            gpu_units += r["unit_count"] if r["unit_count"] else 0
        elif rt == "endpoint":
            endpoints.append(r)
        elif rt == "compute_instance":
            shape = r["unit_shape"] or "Não informado"
            if shape not in compute_by_shape:
                compute_by_shape[shape] = {"shape": shape, "count": 0, "states": defaultdict(int)}
            compute_by_shape[shape]["count"] += 1
            compute_by_shape[shape]["states"][r["lifecycle_state"] or "UNKNOWN"] += 1

    # GPU utilization can come from two sources:
    #  1. oci_computeagent (Bare Metal GPU hosts only — OCI does not publish it for VM.GPU)
    #  2. custom_gpu namespace (in-guest agent publishing nvidia-smi, see scripts/oci-gpu-agent)
    gpu_util_value = None
    gpu_util_source = None
    if "GpuUtilization" in by_name:
        gpu_util_value = _money(by_name["GpuUtilization"].get("average_value"))
        gpu_util_source = "oci_computeagent"
    elif "gpu_utilization_percent" in by_name:
        gpu_util_value = _money(by_name["gpu_utilization_percent"].get("average_value"))
        gpu_util_source = "custom_gpu"

    return {
        "available": len(metrics_rows) > 0 or len(inventory) > 0,
        "window": window,
        "daily": daily_ops,
        "kpis": {
            "input_tokens": round(_money(by_name.get("InputTokenCount", {}).get("total_value")), 2),
            "output_tokens": round(_money(by_name.get("OutputTokenCount", {}).get("total_value")), 2),
            "invocations": round(invocations, 2),
            # Latency can be dominated by a few long-running batches; prefer the
            # median (p50) over the mean for the executive KPI.
            "latency": round(_money(by_name.get("InvocationLatency", {}).get("p50_value")), 2)
            if "InvocationLatency" in by_name and by_name["InvocationLatency"].get("p50_value") is not None
            else round(_money(by_name.get("InvocationLatency", {}).get("average_value")), 2),
            "errors": round(errors, 2),
            "success_rate": round((invocations - errors) / invocations * 100, 2) if invocations else None,
            "gpu_utilization": round(gpu_util_value, 2) if gpu_util_value is not None else None,
            "gpu_utilization_source": gpu_util_source,
            "gpu_memory_utilization": round(_money(by_name.get("gpu_memory_utilization_percent", {}).get("average_value")), 2)
            if "gpu_memory_utilization_percent" in by_name
            else None,
            "cpu_utilization": round(_money(by_name.get("CpuUtilization", {}).get("average_value")), 2)
            if "CpuUtilization" in by_name
            else None,
            "memory_utilization": round(_money(by_name.get("MemoryUtilization", {}).get("average_value")), 2)
            if "MemoryUtilization" in by_name
            else None,
        },
        "inventory": {
            "items": inventory,
            "ai_clusters": ai_clusters,
            "endpoints": endpoints,
            "compute_by_shape": [
                {"shape": s, "count": v["count"], "states": dict(v["states"])}
                for s, v in sorted(compute_by_shape.items())
            ],
            "totals": {
                "gpu_units_dedicated": round(gpu_units, 0),
                "endpoints": len(endpoints),
                "compute_instances": sum(v["count"] for v in compute_by_shape.values()),
            },
        },
    }


async def _wisper_compartment_ids(
    scope_sql: str,
    cancel_event: asyncio.Event | None,
) -> list[str]:
    """Discover Wisper compartment IDs from billing + GenAI inventory."""
    ids: set[str] = set()
    try:
        rows = await trino_execute(
            f"""
            SELECT DISTINCT subaccountid AS compartment_id
            FROM {FOCUS_TABLE}
            WHERE {scope_sql}
            """,
            timeout_s=60,
            cancel_event=cancel_event,
        )
        for r in rows or []:
            cid = _s(r.get("compartment_id"))
            if cid:
                ids.add(cid)
    except Exception:
        pass

    try:
        rows = await trino_execute(
            f"""
            SELECT DISTINCT compartment_id
            FROM {OP_METRICS_TABLE}
            WHERE record_type = 'inventory'
              AND resource_type IN ('dedicated_ai_cluster', 'endpoint')
              AND (
                   lower(resource_name) LIKE '%whisper%'
                OR lower(resource_name) LIKE '%llm%'
                OR lower(resource_name) LIKE '%stt%'
              )
            """,
            timeout_s=60,
            cancel_event=cancel_event,
        )
        for r in rows or []:
            cid = _s(r.get("compartment_id"))
            if cid:
                ids.add(cid)
    except Exception:
        pass

    return sorted(ids)


async def load(cancel_event: asyncio.Event | None = None) -> dict[str, Any]:
    current_period = _now_period()
    scope_sql = f"regexp_like(lower(COALESCE(subaccountname, '')), '{WISPER_SUBACCOUNT_RE}')"

    financials = await _financials(scope_sql, current_period, cancel_event)
    if financials.get("empty"):
        return {"available": True, "empty": True, "monthly": []}

    # Discover compartment IDs for operational/inventory filtering
    compartment_ids = await _wisper_compartment_ids(scope_sql, cancel_event)

    operational = await _operational(compartment_ids, cancel_event)

    # Unit economics: cost of AI hosting over tokens in the telemetry window
    unit_economics = _compute_unit_economics(financials, operational, current_period)

    # Data quality checks
    data_quality = _data_quality(financials, operational)

    return {
        "available": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reference": financials["reference"],
        "executive": financials["executive"],
        "buckets": financials["buckets"],
        "capacity": financials["capacity"],
        "monthly": financials["monthly"],
        "skus": financials["skus"],
        "subaccounts": financials["subaccounts"],
        "insights": financials["insights"],
        "operational": operational,
        "unit_economics": unit_economics,
        "data_quality": data_quality,
        "notes": [
            "B95909 = GPU Hours; B97384 = OCPU de suporte; B97385 = GB Hours de suporte; B108085 = AI unit-hours.",
            "O custo do mês em curso é parcial; a projeção usa a média móvel dos últimos 7 dias com telemetria financeira.",
        ],
    }


def _compute_unit_economics(
    financials: dict[str, Any],
    operational: dict[str, Any],
    current_period: str,
) -> dict[str, Any]:
    """Custo por MM de tokens — apenas Generative AI e últimos 30 dias.

    Tokens só podem ser mensurados pela telemetria do serviço Generative AI
    (InputTokenCount/OutputTokenCount), então o numerador de custo é o bucket
    "Generative AI" do mês fechado (AI unit-hours), nunca o custo total do
    projeto (GPU compute, storage etc. não geram tokens).
    """
    ref = financials["reference"]
    cost_period = ref["closed_period"]
    genai_cost = _money((financials.get("buckets") or {}).get("Generative AI", {}).get("cost"))
    days_in_closed = calendar.monthrange(int(cost_period[:4]), int(cost_period[4:6]))[1]

    # Tokens dos ÚLTIMOS 30 DIAS de telemetria (série diária já agregada)
    daily_ops = operational.get("daily") or []
    window_days = min(30, len(daily_ops))
    last_days = daily_ops[-window_days:] if window_days > 0 else []
    tokens_in = sum(_money(d.get("input_tokens")) for d in last_days)
    tokens_out = sum(_money(d.get("output_tokens")) for d in last_days)

    result: dict[str, Any] = {
        "period": cost_period,
        "period_label": _label(cost_period),
        "basis": "Generative AI",
        "genai_cost": round(genai_cost, 2),
        "input_tokens": round(tokens_in, 2),
        "output_tokens": round(tokens_out, 2),
        "total_tokens": round(tokens_in + tokens_out, 2),
        "telemetry_window_days": window_days,
        "cost_per_mm_input_tokens": None,
        "cost_per_mm_output_tokens": None,
        "cost_per_mm_total_tokens": None,
        "note": "Telemetria insuficiente para calcular custo por token.",
    }

    if window_days >= 7 and (tokens_in or tokens_out) and genai_cost > 0:
        genai_cost_per_day = genai_cost / days_in_closed if days_in_closed else 0.0
        tokens_per_day_total = (tokens_in + tokens_out) / window_days
        if tokens_per_day_total > 0:
            result["cost_per_mm_total_tokens"] = round(
                genai_cost_per_day / (tokens_per_day_total / 1e6), 2
            )
        tokens_per_day_in = tokens_in / window_days
        tokens_per_day_out = tokens_out / window_days
        if tokens_per_day_in > 0:
            result["cost_per_mm_input_tokens"] = round(genai_cost_per_day / (tokens_per_day_in / 1e6), 2)
        if tokens_per_day_out > 0:
            result["cost_per_mm_output_tokens"] = round(genai_cost_per_day / (tokens_per_day_out / 1e6), 2)
        result["note"] = (
            f"Baseado apenas em Generative AI (tokens mensuráveis): R$ {genai_cost_per_day:,.2f}/dia "
            f"(mês fechado {_label(cost_period)}) ÷ consumo dos últimos {window_days} dias de telemetria."
        )

    return result


def _data_quality(financials: dict[str, Any], operational: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    exec_info = financials["executive"]

    checks.append(
        {
            "check": "kpis_usam_mes_fechado",
            "ok": True,
            "detail": f"Mês fechado de referência: {_label(financials['reference']['closed_period'])}",
        }
    )

    forecast = exec_info.get("current_forecast") or 0
    partial = exec_info.get("current_partial") or 0
    checks.append(
        {
            "check": "forecast_mes_aberto_coerente",
            "ok": forecast == 0 or forecast >= partial,
            "detail": f"Forecast {forecast:,.2f} vs. realizado parcial {partial:,.2f}",
        }
    )

    op_available = operational.get("available", False)
    max_ts = operational.get("window", {}).get("max")
    days = operational.get("window", {}).get("days")
    if not op_available:
        checks.append(
            {
                "check": "oci_operational_fresca",
                "ok": False,
                "detail": "Telemetria operacional não disponível.",
            }
        )
    else:
        stale = False
        if max_ts:
            try:
                max_dt = datetime.fromisoformat(max_ts.replace("Z", "+00:00"))
                stale = (datetime.now(timezone.utc) - max_dt).total_seconds() > 24 * 3600
            except Exception:
                pass
        checks.append(
            {
                "check": "oci_operational_fresca",
                "ok": not stale,
                "detail": f"Última métrica: {max_ts or 'n/a'} ({days} dia(s) de dados)",
            }
        )

    # GPU compute shapes depend on the collector being able to inventory the
    # billing compartments (whisper/llm). Without IAM it returns 404 and the
    # SKU shapes stay empty for B95909/B97384/B97385.
    gpu_sku = next((s for s in financials.get("skus", []) if s.get("sku") == "B95909"), None)
    has_gpu_shapes = bool(gpu_sku and gpu_sku.get("shapes"))
    checks.append(
        {
            "check": "shapes_gpu_compute",
            "ok": has_gpu_shapes,
            "detail": (
                "Shapes de GPU mapeados via inventário OCI."
                if has_gpu_shapes
                else "Sem acesso IAM aos compartments whisper/llm (404) — shapes de GPU compute indisponíveis; conceder compute:listInstances ao principal OCI."
            ),
        }
    )

    return checks
