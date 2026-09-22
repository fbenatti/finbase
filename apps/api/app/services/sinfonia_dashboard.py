"""Executive cost view for the Sinfonia portfolio.

The Dimon table already contains the governed allocation; this module only
normalizes its presentation names and selects the authoritative monthly value.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Any

from app.services.trino_service import execute as trino_execute
from app.services.dimon_materialization import DIMON_NAMESPACE

DIMON_TABLE = f"iceberg.{DIMON_NAMESPACE}.dest_dm_cost_projeto_sinfonia"

_PROJECTS = {
    "sinfonia - watson": ("IBM", "Sinfonia IBM", "Watson"),
    "sinfonia - vivocog": ("IBM", "Sinfonia IBM", "VivoCog"),
    "fábrica de agente": ("Azure", "Fábrica de Agentes", None),
    "fabrica de agente": ("Azure", "Fábrica de Agentes", None),
}
_FACTORY_CHILDREN = {
    "fábrica de agente - comercial": "Comercial",
    "fabrica de agente - comercial": "Comercial",
    "fábrica de agente - financeiro": "Financeiro",
    "fabrica de agente - financeiro": "Financeiro",
    "fábrica de agente - suporte tecnico": "Suporte Técnico",
    "fabrica de agente - suporte técnico": "Suporte Técnico",
}
_FACTORY_PROJECTS = ("Comercial", "Financeiro", "Suporte Técnico")


def _text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def normalize_row(row: dict[str, Any]) -> dict[str, str] | None:
    """Map a Dimon row to the portfolio hierarchy, excluding Sinfonia IA."""
    level5 = _text(row.get("organograma_nivel_5"))
    level6 = _text(row.get("organograma_nivel_6"))
    key5 = level5.lower()
    mapping = _PROJECTS.get(key5)
    if not mapping:
        return None
    cloud, group, project = mapping
    if group == "Fábrica de Agentes":
        project = _FACTORY_CHILDREN.get(level6.lower())
        if not project:
            return None
    return {"cloud": cloud, "group": group, "project": project}


def ia_family_for(row: dict[str, Any], cloud: str) -> str | None:
    haystack = " ".join(_text(row.get(k)).lower() for k in ("servicename", "servicesubcategory", "skumeter"))
    if cloud == "IBM":
        if "watsonx orchestrate" in haystack:
            return "watsonx Orchestrate"
        if "watsonx.ai" in haystack or "watsonx ai" in haystack:
            return "watsonx.ai Runtime"
        return None
    rules = (
        ("document intelligence", "Azure Document Intelligence"),
        ("azure speech", "Azure Speech"),
        ("azure openai", "Azure OpenAI"),
        ("openai", "Azure OpenAI"),
        ("cognitive", "Azure AI / Cognitive Services"),
        ("foundry", "Azure AI Foundry"),
    )
    return next((family for term, family in rules if term in haystack), None)


def _label(period: str) -> str:
    months = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
    return f"{months[int(period[4:6]) - 1]}/{period[:4]}" if len(period) == 6 else period


def build_dashboard(rows: list[dict[str, Any]], *, open_period: str | None = None) -> dict[str, Any]:
    """Aggregate query rows into an API-safe executive payload.

    Past months use direct cost (realizado + rateio); the current/open month
    prefers forecast whenever present, as agreed for this portfolio.
    """
    open_period = open_period or datetime.now().strftime("%Y%m")
    buckets: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    details: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        entity = normalize_row(row)
        if not entity:
            continue
        period, tipo = _text(row.get("period")), _text(row.get("tipo")).lower()
        if not period or tipo not in ("realizado", "rateio", "forecast"):
            continue
        project = entity["project"] or ""
        amount = float(row.get("realized") or 0)
        buckets[period][project][tipo] += amount
        details[period].append({**row, **entity, "amount": amount})

    periods = sorted(buckets)
    monthly: list[dict[str, Any]] = []
    for period in periods:
        project_values: dict[str, float] = {}
        forecast_total = 0.0
        for project, types in buckets[period].items():
            direct = types["realizado"] + types["rateio"]
            forecast = types["forecast"]
            # Forecast takes precedence in the current/future open horizon;
            # completed historical months retain their governed direct cost.
            project_values[project] = forecast if period >= open_period and forecast else direct
            forecast_total += forecast
        ibm = project_values.get("Watson", 0.0) + project_values.get("VivoCog", 0.0)
        factory = sum(project_values.get(name, 0.0) for name in _FACTORY_PROJECTS)
        monthly.append({"period": period, "label": _label(period), "ibm": ibm, "factory": factory,
                        "total": ibm + factory, "forecast_total": forecast_total,
                        "source": "forecast" if period >= open_period and forecast_total else "realizado"})

    current = next((item for item in reversed(monthly) if item["period"] <= open_period), monthly[-1] if monthly else None)
    if not current:
        return {"available": True, "empty": True, "table": DIMON_TABLE, "monthly": []}
    current_period = current["period"]
    current_values = {name: buckets[current_period][name]["forecast"] if current_period >= open_period and buckets[current_period][name]["forecast"] else buckets[current_period][name]["realizado"] + buckets[current_period][name]["rateio"] for name in buckets[current_period]}
    cloud_costs = [{"cloud": "IBM", "cost": current["ibm"]}, {"cloud": "Azure", "cost": current["factory"]}]
    projects = [{"project": name, "group": "Sinfonia IBM" if name in ("Watson", "VivoCog") else "Fábrica de Agentes", "cloud": "IBM" if name in ("Watson", "VivoCog") else "Azure", "cost": current_values.get(name, 0.0)} for name in ("Watson", "VivoCog", *_FACTORY_PROJECTS)]
    families: dict[str, float] = defaultdict(float)
    services: dict[tuple[str, str], float] = defaultdict(float)
    for row in details[current_period]:
        # Use only the same source chosen for the current executive number.
        target_tipo = "forecast" if current_period >= open_period and buckets[current_period][row["project"]]["forecast"] else None
        if target_tipo and _text(row.get("tipo")).lower() != target_tipo:
            continue
        if not target_tipo and _text(row.get("tipo")).lower() not in ("realizado", "rateio"):
            continue
        family = ia_family_for(row, row["cloud"])
        if family:
            families[family] += row["amount"]
        service = _text(row.get("servicename")) or "Não informado"
        services[(row["cloud"], service)] += row["amount"]
    return {
        "available": True, "table": DIMON_TABLE, "reference": {"period": current_period, "label": current["label"], "source": current["source"]},
        "kpis": {"total": current["total"], "ibm": current["ibm"], "factory": current["factory"], "forecast": current["forecast_total"]},
        "monthly": monthly, "projects": projects, "clouds": cloud_costs,
        "ai_families": [{"family": name, "cost": value} for name, value in sorted(families.items(), key=lambda x: x[1], reverse=True)],
        "services": [{"cloud": cloud, "service": service, "cost": value} for (cloud, service), value in sorted(services.items(), key=lambda x: x[1], reverse=True)[:12]],
        "notes": ["Rateios compõem o custo do projeto.", "No mês aberto, o forecast é a referência executiva e não é somado ao realizado."],
    }


async def load_sinfonia_dashboard(*, cancel_event: asyncio.Event | None = None) -> dict[str, Any]:
    rows = await trino_execute(f"""
        SELECT _billing_period AS period, tipo, cloud_origem,
               departamento AS organograma_nivel_5, CAST(NULL AS VARCHAR) AS organograma_nivel_6,
               servicename, servicesubcategory, skumeter, SUM(COALESCE(TRY_CAST(realizado AS DOUBLE), 0)) AS realized
        FROM {DIMON_TABLE}
        GROUP BY 1,2,3,4,5,6,7,8
    """, timeout_s=120, cancel_event=cancel_event)
    return build_dashboard(rows or [])
