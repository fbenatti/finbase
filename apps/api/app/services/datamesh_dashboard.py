"""Executive Datamesh view over its governed Dimon cost table."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Any

from app.services.trino_service import execute as trino_execute
from app.services.gold_materialization import MART_NAMESPACE

MART_TABLE = f"iceberg.{MART_NAMESPACE}.dest_dm_cost_projeto_datamesh"
BUCKETS = ("Databricks", "Dados associados", "Infraestrutura")
_DATA_TERMS = (
    "storage", "database", "sql", "synapse", "data factory", "datafactory",
    "event hub", "eventhub", "event streams", "analytics", "stream analytics",
    "cosmos", "postgres", "mysql", "redis", "elasticsearch", "data lake",
)


def _text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def classify_cost(row: dict[str, Any]) -> str:
    """Classify Azure services into Databricks, data/storage dependencies, or infra."""
    haystack = " ".join(_text(row.get(k)).lower() for k in ("servicename", "servicesubcategory", "skumeter"))
    if "databricks" in haystack:
        return "Databricks"
    if any(term in haystack for term in _DATA_TERMS):
        return "Dados associados"
    return "Infraestrutura"


def _label(period: str) -> str:
    months = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
    return f"{months[int(period[4:6]) - 1]}/{period[:4]}" if len(period) == 6 else period


def build_dashboard(rows: list[dict[str, Any]], *, open_period: str | None = None) -> dict[str, Any]:
    open_period = open_period or datetime.now().strftime("%Y%m")
    values: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    scoped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in rows:
        period, tipo = _text(raw.get("period")), _text(raw.get("tipo")).lower()
        if not period or tipo not in ("realizado", "rateio", "forecast"):
            continue
        row = dict(raw)
        row["bucket"] = classify_cost(row)
        row["amount"] = float(row.get("realized") or 0)
        values[period][row["bucket"]][tipo] += row["amount"]
        scoped[period].append(row)
    if not values:
        return {"available": True, "empty": True, "table": MART_TABLE, "monthly": []}

    monthly = []
    for period in sorted(values):
        buckets = {}
        forecast = 0.0
        for bucket in BUCKETS:
            source = values[period][bucket]
            if period >= open_period and source["forecast"]:
                # O motor grava em forecast_value apenas os dias restantes do mês
                # (baseline × remaining_days). Para a estimativa executiva total do
                # mês aberto, somamos realizado parcial + rateio + forecast restante.
                buckets[bucket] = source["realizado"] + source["rateio"] + source["forecast"]
            else:
                buckets[bucket] = source["realizado"] + source["rateio"]
            forecast += source["forecast"]
        monthly.append({"period": period, "label": _label(period), "buckets": buckets, "total": sum(buckets.values()), "forecast_total": forecast, "source": "forecast" if period >= open_period and forecast else "realizado"})
    current = next((m for m in reversed(monthly) if m["period"] <= open_period), monthly[-1])
    period = current["period"]
    chosen_type = "forecast" if period >= open_period and current["forecast_total"] else None
    project_costs: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    cuts: dict[str, dict[str, float]] = {"centro_custo": defaultdict(float), "subaccountname": defaultdict(float), "regionname": defaultdict(float)}
    services: dict[tuple[str, str], float] = defaultdict(float)
    # Keep project attribution honest: it uses the newest period with level 6
    # data, which can be the August realized load while the executive headline
    # already uses the August forecast.
    project_periods = [key for key in scoped if key <= period and any(_text(row.get("organograma_nivel_6")) for row in scoped[key])]
    project_period = max(project_periods) if project_periods else None
    project_type = "forecast" if project_period and project_period >= open_period and any(_text(row.get("tipo")).lower() == "forecast" and _text(row.get("organograma_nivel_6")) for row in scoped[project_period]) else None
    for row in scoped[period]:
        tipo = _text(row.get("tipo")).lower()
        if (chosen_type and tipo != chosen_type) or (not chosen_type and tipo not in ("realizado", "rateio")):
            continue
        for dimension, bucket in cuts.items():
            value = _text(row.get(dimension)) or "Não informado"
            bucket[value] += row["amount"]
        services[(row["bucket"], _text(row.get("servicename")) or "Não informado")] += row["amount"]
    if project_period:
        for row in scoped[project_period]:
            tipo = _text(row.get("tipo")).lower()
            if (project_type and tipo != project_type) or (not project_type and tipo not in ("realizado", "rateio")):
                continue
            project = _text(row.get("organograma_nivel_6")) or "Não informado"
            project_costs[project][row["bucket"]] += row["amount"]
    projects = []
    for name, composition in sorted(project_costs.items(), key=lambda item: sum(item[1].values()), reverse=True):
        total = sum(composition.values())
        dominant = max(BUCKETS, key=lambda bucket: composition.get(bucket, 0)) if total else "—"
        projects.append({"project": name, "cost": total, "dominant_bucket": dominant, "buckets": {bucket: composition.get(bucket, 0) for bucket in BUCKETS}})
    return {
        "available": True, "table": MART_TABLE,
        "reference": {"period": period, "label": current["label"], "source": current["source"]},
        "kpis": {"total": current["total"], "databricks": current["buckets"]["Databricks"], "data": current["buckets"]["Dados associados"], "infra": current["buckets"]["Infraestrutura"], "forecast": current["forecast_total"]},
        "monthly": monthly, "projects": projects,
        "coverage": {"project_periods": project_periods, "reference_has_projects": project_period == period, "project_reference": project_period},
        "cuts": {key: [{"name": name, "cost": cost} for name, cost in sorted(bucket.items(), key=lambda item: item[1], reverse=True)[:10]] for key, bucket in cuts.items()},
        "services": [{"bucket": bucket, "service": service, "cost": cost} for (bucket, service), cost in sorted(services.items(), key=lambda item: item[1], reverse=True)[:15]],
        "notes": ["Rateios compõem o custo do Datamesh.", "No mês aberto, o forecast é a referência executiva e não é somado ao realizado."],
    }


async def load_datamesh_dashboard(*, cancel_event: asyncio.Event | None = None) -> dict[str, Any]:
    rows = await trino_execute(f"""
        SELECT _billing_period AS period, tipo, cloud_origem,
               departamento AS organograma_nivel_6,
               centro_custo, subaccountname, regionname, servicename, servicesubcategory, skumeter,
               SUM(COALESCE(TRY_CAST(realizado AS DOUBLE), 0)) AS realized
        FROM {MART_TABLE}
        GROUP BY 1,2,3,4,5,6,7,8,9,10
    """, timeout_s=120, cancel_event=cancel_event)
    return build_dashboard(rows or [])
