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
from app.services import lakehouse_layers as layers


# Source of truth for Wisper financials: FOCUS gold filtered by SubAccountName
FOCUS_TABLE = f"iceberg.{layers.detail_layer()}.dest_modelo_focus"
# Operational/inventory source
OP_METRICS_TABLE = f"iceberg.{layers.detail_layer()}.dest_oci_operational_metrics"
# Legacy dimon table (kept only for current-month forecast comparison)
DIMON_TABLE = f"iceberg.{layers.detail_layer()}.dest_dm_cost_custo_wisper"

WISPER_SUBACCOUNT_RE = "whisper|llm|poc-stt"

BUCKET_GPU = "Hospedagem GPU (compute + suporte)"
BUCKETS = ("Generative AI", BUCKET_GPU, "Dados e armazenamento", "Infraestrutura")

SKU_EXECUTIVE_NAMES = {
    "B95909": "GPU dedicada (GPU-h)",
    "B108085": "Generative AI (AI unit-h)",
    "B97384": "Suporte compute (OCPU-h)",
    "B97385": "Suporte compute (GB-h)",
}


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
        return BUCKET_GPU
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


async def _trino(
    sql: str,
    *,
    timeout_s: int,
    cancel_event: asyncio.Event | None,
) -> list[dict[str, Any]]:
    """Run a Trino statement; return [] on failure so callers can gather safely."""
    try:
        return await trino_execute(sql, timeout_s=timeout_s, cancel_event=cancel_event) or []
    except Exception:
        return []


def _is_gpu_shape(shape: str) -> bool:
    haystack = shape.upper()
    return any(token in haystack for token in ("GPU", "A10", "A100", "H100", "L40", "BM."))


def _next_period(period: str) -> str:
    y, m = int(period[:4]), int(period[4:6])
    m += 1
    if m > 12:
        y += 1
        m = 1
    return f"{y}{m:02d}"


def _periods_until_year_end(anchor: str) -> list[str]:
    year = anchor[:4]
    end = f"{year}12"
    periods: list[str] = []
    current = anchor
    while current <= end:
        periods.append(current)
        if current == end:
            break
        current = _next_period(current)
    return periods


def _merge_forecast_monthly(
    monthly: list[dict[str, Any]],
    forecast_by_period: dict[str, dict[str, Any]],
    current_period: str,
) -> list[dict[str, Any]]:
    """Attach forecast totals and future months from Dimon/planning."""
    by_period = {item["period"]: dict(item) for item in monthly}
    for period, forecast in forecast_by_period.items():
        entry = by_period.setdefault(
            period,
            {
                "period": period,
                "label": _label(period),
                "buckets": {bucket: 0.0 for bucket in BUCKETS},
                "total": 0.0,
                "is_partial": period == current_period,
                "is_forecast_only": period > current_period,
            },
        )
        entry["forecast_total"] = round(_money(forecast.get("total")), 2)
        entry["forecast_buckets"] = {
            bucket: round(_money(forecast.get("buckets", {}).get(bucket)), 2)
            for bucket in BUCKETS
        }
        entry["is_forecast_only"] = period > current_period
        if period > current_period:
            entry["total"] = entry["forecast_total"]
            entry["buckets"] = dict(entry["forecast_buckets"])
    for period, entry in by_period.items():
        forecast_total = _money(forecast_by_period.get(period, {}).get("total"))
        entry["forecast_total"] = round(forecast_total, 2) if forecast_total else None
        if period == current_period and forecast_total:
            partial = _money(entry.get("total"))
            entry["forecast_remaining"] = round(max(forecast_total - partial, 0.0), 2)
    return [by_period[p] for p in sorted(by_period)]


async def _forecast_by_period(
    current_period: str,
    cancel_event: asyncio.Event | None,
) -> dict[str, dict[str, Any]]:
    """Forecast monthly totals from Dimon until December of the current year."""
    end_period = f"{current_period[:4]}12"
    try:
        rows = await trino_execute(
            f"""
            SELECT _billing_period AS period,
                   servicename,
                   servicesubcategory,
                   skumeter,
                   SUM(COALESCE(TRY_CAST(realizado AS DOUBLE), 0)) AS amount
            FROM {DIMON_TABLE}
            WHERE lower(tipo) = 'forecast'
              AND _billing_period >= '{current_period}'
              AND _billing_period <= '{end_period}'
            GROUP BY 1, 2, 3, 4
            """,
            timeout_s=60,
            cancel_event=cancel_event,
        )
    except Exception:
        return {}

    by_period: dict[str, dict[str, Any]] = defaultdict(lambda: {"total": 0.0, "buckets": defaultdict(float)})
    for row in rows or []:
        period = _s(row.get("period"))
        if not period:
            continue
        bucket = _bucket(
            _s(row.get("servicename")),
            _s(row.get("servicesubcategory")),
            _s(row.get("skumeter")),
        )
        amount = _money(row.get("amount"))
        by_period[period]["total"] += amount
        by_period[period]["buckets"][bucket] += amount
    return {
        period: {
            "total": values["total"],
            "buckets": dict(values["buckets"]),
        }
        for period, values in by_period.items()
    }


async def _financials(
    scope_sql: str,
    current_period: str,
    cancel_event: asyncio.Event | None,
) -> dict[str, Any]:
    """Return monthly series, buckets, SKUs, subaccounts and daily series."""

    # 1. Monthly series (realizado).  The Wisper Dimon is the canonical,
    # pre-aggregated slice and avoids rescanning the 35M-row FOCUS table for
    # the first query on every dashboard load.  FOCUS remains used below only
    # for the detailed daily/SKU drill-downs that require its finer grain.
    monthly_rows = await trino_execute(
        f"""
        SELECT _billing_period AS period,
               SUM(COALESCE(TRY_CAST(realizado AS DOUBLE), 0)) AS total,
               servicename,
               servicesubcategory,
               skumeter,
               SUM(COALESCE(TRY_CAST(realizado AS DOUBLE), 0)) AS bucket_cost
        FROM {DIMON_TABLE}
        WHERE lower(COALESCE(tipo, '')) = 'realizado'
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

    # 3. Closed-period detail: SKU MoM, shapes, daily grain, forecast, subaccounts
    prev_period = _prev_period(closed_period)
    closed_month = closed_period[:4] + "-" + closed_period[4:6]
    current_month = current_period[:4] + "-" + current_period[4:6]

    sku_rows, shape_rows, fleet_rows, daily_rows, sub_rows, forecast_by_period = await asyncio.gather(
        _trino(
            f"""
            SELECT _billing_period AS period,
                   servicename,
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
              AND _billing_period IN ('{closed_period}', '{prev_period}')
            GROUP BY 1, 2, 3, 4, 5
            """,
            timeout_s=120,
            cancel_event=cancel_event,
        ),
        _trino(
            f"""
            -- OCI inventory is already loaded below as fleet_rows.  A join
            -- back to FOCUS by resource ID added a full additional financial
            -- scan only to decorate SKU labels; keep the payload contract and
            -- use the inventory fleet fallback instead.
            SELECT CAST(NULL AS VARCHAR) AS sku,
                   CAST(NULL AS VARCHAR) AS shape,
                   CAST(0 AS BIGINT) AS recursos
            WHERE FALSE
            """,
            timeout_s=120,
            cancel_event=cancel_event,
        ),
        _trino(
            f"""
            SELECT unit_shape, SUM(unit_count) AS units
            FROM {OP_METRICS_TABLE}
            WHERE record_type = 'inventory'
              AND resource_type = 'dedicated_ai_cluster'
            GROUP BY 1
            """,
            timeout_s=30,
            cancel_event=cancel_event,
        ),
        _trino(
            f"""
            SELECT substr(chargeperiodstart, 1, 10) AS dia,
                   servicename,
                   servicesubcategory,
                   skumeter,
                   SUM(billedcost) AS custo,
                   SUM(CASE WHEN skumeter = 'B95909' THEN consumedquantity ELSE 0 END) AS gpu_hours,
                   SUM(CASE WHEN skumeter = 'B108085' THEN consumedquantity ELSE 0 END) AS ai_unit_hours,
                   COUNT(*) AS linhas
            FROM {FOCUS_TABLE}
            WHERE {scope_sql}
              -- _billing_period is the Iceberg partition.  Filtering with
              -- substr(chargeperiodstart) forced a scan of every FOCUS month.
              AND _billing_period IN ('{closed_period}', '{current_period}')
            GROUP BY 1, 2, 3, 4
            ORDER BY 1
            """,
            timeout_s=120,
            cancel_event=cancel_event,
        ),
        _trino(
            f"""
            SELECT subaccountname,
                   SUM(COALESCE(TRY_CAST(realizado AS DOUBLE), 0)) AS cost
            FROM {DIMON_TABLE}
            WHERE lower(COALESCE(tipo, '')) = 'realizado'
              AND _billing_period = '{closed_period}'
            GROUP BY 1
            ORDER BY 2 DESC
            """,
            timeout_s=60,
            cancel_event=cancel_event,
        ),
        _forecast_by_period(current_period, cancel_event),
    )

    prev_sku_cost: dict[str, float] = defaultdict(float)
    closed_sku_rows: list[dict[str, Any]] = []
    for r in sku_rows:
        period = _s(r.get("period"))
        sku = _s(r.get("skumeter"))
        cost = _money(r.get("cost"))
        if period == prev_period:
            prev_sku_cost[sku] += cost
        elif period == closed_period:
            closed_sku_rows.append(r)
    sku_rows = closed_sku_rows

    shapes_by_sku: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for r in shape_rows:
        sku = _s(r.get("sku"))
        shape = _s(r.get("shape"))
        recursos = int(r.get("recursos") or 0)
        if sku and shape:
            shapes_by_sku[sku].append((shape, recursos))

    # Fallback: fleet shapes of Dedicated AI Clusters when billed resources are
    # not inventoried (billing compartments return 404 without IAM read).
    fleet_shapes = [
        (_s(r.get("unit_shape")), int(r.get("units") or 0))
        for r in fleet_rows
        if _s(r.get("unit_shape"))
    ]

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
                "executive_name": SKU_EXECUTIVE_NAMES.get(sku, _s(r.get("servicesubcategory")) or _s(r.get("servicename"))),
                "bucket": _bucket(
                    _s(r.get("servicename")),
                    _s(r.get("servicesubcategory")),
                    sku,
                ),
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

    # The executive component list is a cost ranking, not an arbitrary
    # database grouping order.  Keep the API ordered so every consumer gets
    # the same highest-to-lowest view.
    skus.sort(key=lambda item: item["cost"], reverse=True)

    hours_in_closed = 24 * calendar.monthrange(int(closed_period[:4]), int(closed_period[4:6]))[1]

    daily_by_date: dict[str, dict[str, Any]] = {}
    for r in daily_rows:
        dia = _s(r.get("dia"))
        if not dia:
            continue
        bucket = _bucket(
            _s(r.get("servicename")),
            _s(r.get("servicesubcategory")),
            _s(r.get("skumeter")),
        )
        custo = _money(r.get("custo"))
        day_gpu_hours = _money(r.get("gpu_hours"))
        day_ai_unit_hours = _money(r.get("ai_unit_hours"))
        entry = daily_by_date.setdefault(
            dia,
            {
                "date": dia,
                "cost": 0.0,
                "cost_hosting": 0.0,
                "buckets": {bucket_name: 0.0 for bucket_name in BUCKETS},
                "gpu_hours": 0.0,
                "ai_unit_hours": 0.0,
                "is_partial": dia.startswith(current_period[:4] + "-" + current_period[4:6]),
            },
        )
        entry["cost"] += custo
        entry["buckets"][bucket] += custo
        if bucket in (BUCKET_GPU, "Generative AI"):
            entry["cost_hosting"] += custo
        entry["gpu_hours"] += day_gpu_hours
        entry["ai_unit_hours"] += day_ai_unit_hours

    daily = [
        {
            **entry,
            "cost": round(entry["cost"], 2),
            "cost_hosting": round(entry["cost_hosting"], 2),
            "buckets": {bucket: round(value, 2) for bucket, value in entry["buckets"].items()},
            "gpu_hours": round(entry["gpu_hours"], 2),
            "ai_unit_hours": round(entry["ai_unit_hours"], 2),
        }
        for entry in sorted(daily_by_date.values(), key=lambda item: item["date"])
    ]
    daily_closed = [item for item in daily if not item["is_partial"]]
    closed_daily = [item["cost"] for item in daily_closed]
    current_daily = [item["cost"] for item in daily if item["is_partial"]]

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

    forecast_current = _money((forecast_by_period or {}).get(current_period, {}).get("total"))
    subaccounts_raw = [
        {"name": _s(r.get("subaccountname")) or "Não informado", "cost": _money(r.get("cost"))}
        for r in sub_rows
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
    buckets = {k: by_period[closed_period].get(k, 0.0) for k in BUCKETS}

    # Monthly chart series (realizado for every collected period)
    monthly = []
    for p in all_periods:
        vals = {k: by_period[p].get(k, 0.0) for k in BUCKETS}
        monthly.append(
            {
                "period": p,
                "label": _label(p),
                "buckets": vals,
                "total": period_total.get(p, 0.0),
                "is_partial": p == current_period,
                "is_forecast_only": False,
            }
        )

    monthly = _merge_forecast_monthly(monthly, forecast_by_period or {}, current_period)

    genai_cost = buckets.get("Generative AI", 0.0)
    gpu_cost = buckets.get(BUCKET_GPU, 0.0)
    hosting_total = genai_cost + gpu_cost

    subaccounts = [
        {
            **item,
            "share_pct": round(item["cost"] / closed_total * 100, 2) if closed_total else 0.0,
        }
        for item in subaccounts_raw
    ]

    return {
        "available": True,
        "empty": False,
        "reference": {
            "closed_period": closed_period,
            "closed_label": _label(closed_period),
            "current_period": current_period,
            "current_label": _label(current_period),
            "financial_source": "Dimon Wisper (FOCUS consolidada)",
            "forecast_source": "Dimon/planning",
        },
        "executive": {
            "total_closed": round(closed_total, 2),
            "total_prev_closed": round(prev_total, 2),
            "mom_delta_pct": round(mom, 2) if mom is not None else None,
            "current_partial": round(current_partial, 2),
            "current_forecast": round(forecast_current, 2),
            "forecast_remaining": round(max(forecast_current - current_partial, 0.0), 2) if forecast_current else None,
            "run_rate_daily": round(run_rate_7d, 2),
            "monthly_projection": round(projection, 2),
            "hosting_dedicated": {
                "total": round(hosting_total, 2),
                "share_pct": round(hosting_total / closed_total * 100, 2) if closed_total else 0.0,
                "generative_ai": {
                    "cost": round(genai_cost, 2),
                    "share_pct": round(genai_cost / closed_total * 100, 2) if closed_total else 0.0,
                    "bucket": "Generative AI",
                    "source": "FOCUS · serviço GENERATIVE_AI",
                },
                "gpu_hosting": {
                    "cost": round(gpu_cost, 2),
                    "share_pct": round(gpu_cost / closed_total * 100, 2) if closed_total else 0.0,
                    "bucket": BUCKET_GPU,
                    "source": "FOCUS · GPU-h, OCPU-h e GB-h de suporte",
                },
            },
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
            "daily_closed": daily_closed,
            "reconciliation": {
                "billed_gpu_hours": round(gpu_hours, 2),
                "billed_ai_unit_hours": round(ai_unit_hours, 2),
                "note": (
                    "Capacidade faturada vem da FOCUS (horas cobradas). "
                    "O inventário OCI mostra recursos provisionados no snapshot mais recente."
                ),
            },
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


def _metric_date_predicate(days: int) -> str:
    """Robust rolling window on metric timestamps (varchar or ISO in Iceberg)."""
    return (
        f"TRY_CAST(substr(CAST(timestamp AS VARCHAR), 1, 10) AS DATE) "
        f">= CURRENT_DATE - INTERVAL '{int(days)}' DAY"
    )


def _tokens_from_daily_window(daily_ops: list[dict[str, Any]], window_days: int = 30) -> dict[str, Any]:
    """Aggregate token totals for the last N calendar days in the daily series."""
    empty = {
        "input_tokens": 0.0,
        "output_tokens": 0.0,
        "total_tokens": 0.0,
        "window_days": 0,
        "date_from": None,
        "date_to": None,
    }
    if not daily_ops:
        return empty

    dated = [day for day in daily_ops if day.get("date")]
    if not dated:
        return empty

    max_date_str = max(str(day["date"]) for day in dated)
    try:
        max_date = datetime.strptime(max_date_str[:10], "%Y-%m-%d").date()
        from datetime import timedelta

        cutoff = (max_date - timedelta(days=window_days - 1)).isoformat()
        window = [day for day in dated if str(day["date"])[:10] >= cutoff]
    except Exception:
        window = dated[-window_days:]

    input_tokens = sum(_money(day.get("input_tokens")) for day in window)
    output_tokens = sum(_money(day.get("output_tokens")) for day in window)
    total_tokens = input_tokens + output_tokens
    if total_tokens <= 0:
        return empty

    days_with_tokens = sum(
        1
        for day in window
        if _money(day.get("input_tokens")) or _money(day.get("output_tokens"))
    )
    return {
        "input_tokens": round(input_tokens, 2),
        "output_tokens": round(output_tokens, 2),
        "total_tokens": round(total_tokens, 2),
        "window_days": days_with_tokens or len(window) or window_days,
        "date_from": window[0].get("date"),
        "date_to": window[-1].get("date"),
    }


def _empty_tokens_30d() -> dict[str, Any]:
    return {
        "input_tokens": 0.0,
        "output_tokens": 0.0,
        "total_tokens": 0.0,
        "window_days": 0,
        "date_from": None,
        "date_to": None,
    }


def _pick_tokens_30d(
    daily_ops: list[dict[str, Any]],
    extra_sum: dict[str, Any] | None = None,
    unfiltered_daily: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Prefer already-loaded daily series; optional SUM / unfiltered daily as fallback."""
    from_daily = _tokens_from_daily_window(daily_ops, 30)
    if from_daily["total_tokens"] > 0:
        return from_daily
    if extra_sum and _money(extra_sum.get("total_tokens")) > 0:
        return extra_sum
    if unfiltered_daily:
        alt = _tokens_from_daily_window(unfiltered_daily, 30)
        if alt["total_tokens"] > 0:
            return alt
    return from_daily if from_daily["total_tokens"] > 0 else _empty_tokens_30d()



async def _tokens_last_30d(
    extra_filter: str,
    cancel_event: asyncio.Event | None,
) -> dict[str, Any]:
    """Single 30-day token SUM (GenAI metrics). extra_filter is a WHERE fragment or ''."""
    rows = await _trino(
        f"""
        SELECT
          SUM(CASE WHEN metric_name = 'InputTokenCount' THEN metric_value ELSE 0 END) AS input_tokens,
          SUM(CASE WHEN metric_name = 'OutputTokenCount' THEN metric_value ELSE 0 END) AS output_tokens,
          MIN(substr(CAST(timestamp AS VARCHAR), 1, 10)) AS date_from,
          MAX(substr(CAST(timestamp AS VARCHAR), 1, 10)) AS date_to,
          COUNT(DISTINCT substr(CAST(timestamp AS VARCHAR), 1, 10)) AS days_with_tokens
        FROM {OP_METRICS_TABLE}
        WHERE record_type = 'metric'
          AND metric_name IN ('InputTokenCount', 'OutputTokenCount')
          AND {_metric_date_predicate(30)}
          {extra_filter}
        """,
        timeout_s=60,
        cancel_event=cancel_event,
    )
    if not rows:
        return _empty_tokens_30d()
    row = rows[0]
    input_tokens = _money(row.get("input_tokens"))
    output_tokens = _money(row.get("output_tokens"))
    total_tokens = input_tokens + output_tokens
    if total_tokens <= 0:
        return _empty_tokens_30d()
    return {
        "input_tokens": round(input_tokens, 2),
        "output_tokens": round(output_tokens, 2),
        "total_tokens": round(total_tokens, 2),
        "window_days": int(row.get("days_with_tokens") or 0) or 30,
        "date_from": _s(row.get("date_from")) or None,
        "date_to": _s(row.get("date_to")) or None,
    }



async def _load_daily_ops(
    compartment_filter: str,
    cancel_event: asyncio.Event | None,
    *,
    days: int = 90,
) -> list[dict[str, Any]]:
    daily_ops: list[dict[str, Any]] = []
    daily_rows = await _trino(
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
          AND {_metric_date_predicate(days)}
          {compartment_filter}
        GROUP BY 1
        ORDER BY 1
        """,
        timeout_s=60,
        cancel_event=cancel_event,
    )
    for r in daily_rows:
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
    return daily_ops


async def _resolve_tokens_30d(
    *,
    compartment_filter: str,
    daily_ops: list[dict[str, Any]],
    invocations: float,
    cancel_event: asyncio.Event | None,
) -> dict[str, Any]:
    """30-day tokens from daily series; at most one extra Trino round-trip."""
    from_daily = _tokens_from_daily_window(daily_ops, 30)
    if from_daily["total_tokens"] > 0:
        return from_daily

    extra_sum = await _tokens_last_30d(compartment_filter, cancel_event)
    picked = _pick_tokens_30d(daily_ops, extra_sum=extra_sum)
    if picked["total_tokens"] > 0:
        return picked

    if invocations > 0 and compartment_filter:
        unfiltered_daily = await _load_daily_ops("", cancel_event)
        return _pick_tokens_30d(daily_ops, extra_sum=extra_sum, unfiltered_daily=unfiltered_daily)
    return _empty_tokens_30d()


def _window_from_rows(window_rows: list[dict[str, Any]]) -> dict[str, Any]:
    window: dict[str, Any] = {"min": None, "max": None, "days": None}
    if not window_rows:
        return window
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
    return {"min": min_ts or None, "max": max_ts or None, "days": days, "points": points}


async def _load_inventory(
    compartment_filter: str,
    cancel_event: asyncio.Event | None,
) -> list[dict[str, Any]]:
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
        return inventory
    except Exception:
        pass

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
    return inventory


async def _operational(
    wisper_compartment_ids: list[str],
    cancel_event: asyncio.Event | None,
) -> dict[str, Any]:
    """Return OCI operational metrics and inventory filtered to the Wisper scope."""

    compartment_filter = ""
    if wisper_compartment_ids:
        quoted = ", ".join(f"'{c}'" for c in wisper_compartment_ids)
        compartment_filter = f"AND compartment_id IN ({quoted})"

    metrics_rows, window_rows, daily_ops, inventory = await asyncio.gather(
        _trino(
            f"""
            SELECT metric_name,
                   SUM(metric_value) AS total_value,
                   AVG(metric_value) AS average_value,
                   APPROX_PERCENTILE(metric_value, 0.5) AS p50_value,
                   COUNT(*) AS points
            FROM {OP_METRICS_TABLE}
            WHERE record_type = 'metric'
              AND {_metric_date_predicate(90)}
              {compartment_filter}
            GROUP BY 1
            """,
            timeout_s=60,
            cancel_event=cancel_event,
        ),
        _trino(
            f"""
            SELECT MIN(timestamp) AS min_ts,
                   MAX(timestamp) AS max_ts,
                   COUNT(*) AS points
            FROM {OP_METRICS_TABLE}
            WHERE record_type = 'metric' {compartment_filter}
            """,
            timeout_s=30,
            cancel_event=cancel_event,
        ),
        _load_daily_ops(compartment_filter, cancel_event),
        _load_inventory(compartment_filter, cancel_event),
    )

    by_name = {str(row.get("metric_name")): row for row in metrics_rows}
    invocations = _money(by_name.get("TotalInvocationCount", {}).get("total_value"))
    errors = _money(by_name.get("ClientErrorCount", {}).get("total_value")) + _money(
        by_name.get("ServerErrorCount", {}).get("total_value")
    )
    window = _window_from_rows(window_rows)

    # Aggregate inventory for the executive view
    ai_clusters: list[dict[str, Any]] = []
    endpoints: list[dict[str, Any]] = []
    compute_by_shape: dict[str, dict[str, Any]] = {}
    gpu_units = 0
    inventory_collected_at: str | None = None
    by_compartment: dict[str, dict[str, Any]] = {}
    endpoints_by_model: dict[str, dict[str, Any]] = {}
    for r in inventory:
        if r.get("collected_at") and not inventory_collected_at:
            inventory_collected_at = r["collected_at"]
        compartment = r.get("compartment_name") or r.get("compartment_id") or "Não informado"
        comp_entry = by_compartment.setdefault(
            compartment,
            {
                "compartment": compartment,
                "ai_clusters": 0,
                "endpoints": 0,
                "compute_instances": 0,
                "gpu_units": 0.0,
            },
        )
        rt = r["resource_type"]
        if rt == "dedicated_ai_cluster":
            ai_clusters.append({**r, "financial_bucket": "Generative AI", "source": "OCI GenAI API"})
            gpu_units += r["unit_count"] if r["unit_count"] else 0
            comp_entry["ai_clusters"] += 1
            comp_entry["gpu_units"] += _money(r.get("unit_count"))
        elif rt == "endpoint":
            endpoints.append({**r, "financial_bucket": "Generative AI", "source": "OCI GenAI API"})
            comp_entry["endpoints"] += 1
            model = r.get("model_id") or "Modelo não informado"
            model_entry = endpoints_by_model.setdefault(
                model,
                {"model": model, "count": 0, "states": defaultdict(int)},
            )
            model_entry["count"] += 1
            model_entry["states"][r.get("lifecycle_state") or "UNKNOWN"] += 1
        elif rt == "compute_instance":
            shape = r["unit_shape"] or "Não informado"
            role = "gpu_compute" if _is_gpu_shape(shape) else "support_infra"
            if shape not in compute_by_shape:
                compute_by_shape[shape] = {
                    "shape": shape,
                    "count": 0,
                    "states": defaultdict(int),
                    "role": role,
                    "financial_bucket": BUCKET_GPU if role == "gpu_compute" else "Infraestrutura",
                    "source": "OCI Compute API",
                }
            compute_by_shape[shape]["count"] += 1
            compute_by_shape[shape]["states"][r["lifecycle_state"] or "UNKNOWN"] += 1
            comp_entry["compute_instances"] += 1

    tokens_30d = await _resolve_tokens_30d(
        compartment_filter=compartment_filter,
        daily_ops=daily_ops,
        invocations=invocations,
        cancel_event=cancel_event,
    )

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
        "tokens_30d": tokens_30d,
        "kpis": {
            "input_tokens": tokens_30d["input_tokens"],
            "output_tokens": tokens_30d["output_tokens"],
            "total_tokens": tokens_30d["total_tokens"],
            "input_tokens_90d": round(_money(by_name.get("InputTokenCount", {}).get("total_value")), 2),
            "output_tokens_90d": round(_money(by_name.get("OutputTokenCount", {}).get("total_value")), 2),
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
            "endpoints_by_model": [
                {"model": model, "count": values["count"], "states": dict(values["states"])}
                for model, values in sorted(endpoints_by_model.items(), key=lambda item: -item[1]["count"])
            ],
            "compute_by_shape": [
                {"shape": s, "count": v["count"], "states": dict(v["states"]), "role": v["role"], "financial_bucket": v["financial_bucket"], "source": v["source"]}
                for s, v in sorted(compute_by_shape.items())
            ],
            "compute_gpu": [
                item for item in sorted(compute_by_shape.values(), key=lambda item: -item["count"]) if item["role"] == "gpu_compute"
            ],
            "compute_support": [
                item for item in sorted(compute_by_shape.values(), key=lambda item: -item["count"]) if item["role"] == "support_infra"
            ],
            "by_compartment": sorted(by_compartment.values(), key=lambda item: -(item["ai_clusters"] + item["endpoints"] + item["compute_instances"])),
            "collected_at": inventory_collected_at,
            "totals": {
                "gpu_units_dedicated": round(gpu_units, 0),
                "endpoints": len(endpoints),
                "compute_instances": sum(v["count"] for v in compute_by_shape.values()),
            },
        },
    }


async def _wisper_compartment_ids(
    scope_sql: str,
    current_period: str,
    cancel_event: asyncio.Event | None,
) -> list[str]:
    """Discover active Wisper compartment IDs from recent billing + inventory.

    Compartment IDs are absent from the cost Dimon.  Limit the fallback FOCUS
    lookup to the current and immediately preceding partition; the old query
    scanned every historical FOCUS partition just to obtain stable IDs.
    """
    recent_period = _prev_period(current_period)
    focus_rows = await _trino(
        f"""
        SELECT DISTINCT subaccountid AS compartment_id
        FROM {FOCUS_TABLE}
        WHERE {scope_sql}
          AND _billing_period IN ('{recent_period}', '{current_period}')
        """,
        timeout_s=60,
        cancel_event=cancel_event,
    )
    focus_ids = [
        "'" + _s(row.get("compartment_id")).replace("'", "''") + "'"
        for row in focus_rows
        if _s(row.get("compartment_id"))
    ]
    inventory_scope_rows, inventory_name_rows = await asyncio.gather(
        _trino(
            f"""
            SELECT DISTINCT compartment_id
            FROM {OP_METRICS_TABLE}
            WHERE record_type = 'inventory'
              AND resource_type IN ('dedicated_ai_cluster', 'endpoint')
              AND compartment_id IN ({', '.join(focus_ids) or "''"})
            """,
            timeout_s=60,
            cancel_event=cancel_event,
        ),
        _trino(
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
        ),
    )
    ids: set[str] = set()
    for rows in (focus_rows, inventory_scope_rows, inventory_name_rows):
        for r in rows:
            cid = _s(r.get("compartment_id"))
            if cid:
                ids.add(cid)
    return sorted(ids)


async def load(cancel_event: asyncio.Event | None = None) -> dict[str, Any]:
    current_period = _now_period()
    scope_sql = f"regexp_like(lower(COALESCE(subaccountname, '')), '{WISPER_SUBACCOUNT_RE}')"

    financials, compartment_ids = await asyncio.gather(
        _financials(scope_sql, current_period, cancel_event),
        _wisper_compartment_ids(scope_sql, current_period, cancel_event),
    )
    if financials.get("empty"):
        return {"available": True, "empty": True, "monthly": []}

    operational = await _operational(compartment_ids, cancel_event)

    # Unit economics: cost of AI hosting over tokens in the telemetry window
    unit_economics = _compute_unit_economics(financials, operational, current_period)

    # Data quality checks
    data_quality = _data_quality(financials, operational)

    reference = {
        **financials["reference"],
        "inventory_snapshot_at": operational.get("inventory", {}).get("collected_at"),
        "telemetry_window_days": operational.get("window", {}).get("days"),
        "telemetry_window_from": operational.get("window", {}).get("min"),
        "telemetry_window_to": operational.get("window", {}).get("max"),
    }

    return {
        "available": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reference": reference,
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
            "Hospedagem dedicada de IA = Generative AI + Hospedagem GPU (compute + suporte).",
            "O custo do mês em curso é parcial; a projeção por run-rate usa a média móvel dos últimos 7 dias com telemetria financeira.",
            "Realizado: FOCUS · Forecast: Dimon/planning.",
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

    # Tokens dos últimos 30 dias (consulta dedicada; evita dias com outras métricas e zero tokens).
    tokens_30d = operational.get("tokens_30d") or {}
    if _money(tokens_30d.get("total_tokens")) <= 0:
        tokens_30d = _tokens_from_daily_window(operational.get("daily") or [], 30)
    tokens_in = _money(tokens_30d.get("input_tokens"))
    tokens_out = _money(tokens_30d.get("output_tokens"))
    window_days = int(tokens_30d.get("window_days") or 0)

    result: dict[str, Any] = {
        "period": cost_period,
        "period_label": _label(cost_period),
        "basis": "Generative AI",
        "is_estimate": True,
        "excludes_gpu_hosting": True,
        "genai_cost": round(genai_cost, 2),
        "input_tokens": round(tokens_in, 2),
        "output_tokens": round(tokens_out, 2),
        "total_tokens": round(tokens_in + tokens_out, 2),
        "telemetry_window_days": window_days or 30,
        "token_date_from": tokens_30d.get("date_from"),
        "token_date_to": tokens_30d.get("date_to"),
        "cost_per_mm_input_tokens": None,
        "cost_per_mm_output_tokens": None,
        "cost_per_mm_total_tokens": None,
        "summary": (
            "Considera apenas o custo de Generative AI. Não inclui Hospedagem GPU: "
            "não há telemetria de consumo de tokens processado nas GPUs."
        ),
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
            f"Custo Generative AI de {_label(cost_period)} (R$ {genai_cost:,.0f} no mês fechado) "
            f"÷ tokens dos últimos {window_days} dias de telemetria. "
            "Não inclui Hospedagem GPU."
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
