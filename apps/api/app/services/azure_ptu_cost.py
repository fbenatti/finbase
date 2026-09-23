"""Enriquecimento financeiro do Azure PTU Ops a partir da Gold (FOCUS).

Lê ``iceberg.{layers.detail_layer()}.dest_modelo_focus`` (realizado) e
``iceberg.{layers.detail_layer()}.dest_modelo_focus_planning`` (forecast) e devolve o bloco
financeiro do dashboard com a separação correta de capacidade PTU:

* **Reserva** — ``Microsoft.Capacity`` (chargecategory Purchase/Refund/Return),
  ``consumedquantity = 100`` PTUs/mês; a compra mensal da reserva.
* **On-demand** — meter ``Provisioned Throughput Units`` com
  ``consumedunit = '1/Month'`` e ``commitmentdiscounttype = 'OnDemand'``,
  ``consumedquantity = 100`` PTUs/mês; a PTU on-demand paga por mês.
  Quando o mês ainda não recebeu a compra na Gold, o valor é estimado pelo
  último realizado da compra (o forecast do planejamento espelha o consumo
  horário e não expõe a compra — não é usado).

O consumo diário/horário da capacidade (meter ``Provisioned Managed*``,
``1/Hour``) não é retornado: ele já está incluído no custo da compra mensal.

Os projetos são classificados no ``departamento_nivel_5``. O Vivo Mais
publica a reserva PTU como ``Vivo+`` na Gold (subconta de faturamento);
o dashboard normaliza para o rótulo ``Vivo Mais``. Aura Brain usa o
mesmo nome na Gold.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.services.trino_service import execute as trino_execute
from app.services import lakehouse_layers as layers

logger = logging.getLogger(__name__)

FOCUS_TABLE = "dest_modelo_focus"
PLANNING_TABLE = "dest_modelo_focus_planning"
DETAIL_NAMESPACE = f"iceberg.{layers.detail_layer()}"
PROJECTS = ("Vivo Mais", "Aura Brain")

# Chaves de projeto usadas pelo dashboard (filtro de projeto) -> rótulo exibido.
PROJECT_KEY_TO_GOLD = {
    "vivo-mais": "Vivo Mais",
    "aura-brain": "Aura Brain",
}

# Nomes publicados na Gold FOCUS por rótulo do dashboard (Vivo Mais -> Vivo+).
GOLD_PROJECT_NAMES: dict[str, tuple[str, ...]] = {
    "Vivo Mais": ("Vivo Mais", "Vivo+"),
    "Aura Brain": ("Aura Brain",),
}

_PROJECT_SQL = (
    "CASE WHEN COALESCE(NULLIF(departamento_nivel_5, ''), departamento) = 'Vivo+' "
    "THEN 'Vivo Mais' "
    "ELSE COALESCE(NULLIF(departamento_nivel_5, ''), departamento) END"
)
_PLANNING_PROJECT_SQL = (
    "CASE WHEN COALESCE(NULLIF(departamento_nivel_5, ''), departamento_nivel_5) = 'Vivo+' "
    "THEN 'Vivo Mais' "
    "ELSE COALESCE(NULLIF(departamento_nivel_5, ''), departamento_nivel_5) END"
)

# Classificação dos meters PTU na Gold FOCUS.
RESERVATION_SERVICE = "Microsoft.Capacity"           # compra da reserva
RESERVATION_CHARGES = ("Purchase", "Refund", "Return")
ONDEMAND_METER = "Provisioned Throughput Units"      # PTU on-demand mensal
ONDEMAND_UNIT = "1/Month"

# Quantos meses de histórico realizado a série mensal do dashboard mostra.
HISTORY_MONTHS = 8

_MONTH_LABELS = {
    1: "jan", 2: "fev", 3: "mar", 4: "abr", 5: "mai", 6: "jun",
    7: "jul", 8: "ago", 9: "set", 10: "out", 11: "nov", 12: "dez",
}


def _period_label(period: str) -> str:
    """'202608' -> 'ago/2026'."""
    try:
        year = int(period[:4])
        month = int(period[4:6])
        return f"{_MONTH_LABELS.get(month, str(month).zfill(2))}/{year}"
    except (ValueError, TypeError):
        return str(period or "—")


def _shift_period(period: str, offset: int) -> str:
    """Desloca um período YYYYMM por ``offset`` meses (pode ser negativo)."""
    try:
        year, month = int(period[:4]), int(period[4:6])
    except (ValueError, TypeError):
        return period
    total = year * 12 + (month - 1) + offset
    year = total // 12
    month = total % 12 + 1
    return f"{year:04d}{month:02d}"


def _periods_in_window(date_from: str, date_to: str) -> list[str]:
    """Períodos YYYYMM que intersectam [date_from, date_to] (YYYY-MM-DD)."""
    try:
        start = datetime.fromisoformat(date_from + "T00:00:00+00:00")
        end = datetime.fromisoformat(date_to + "T23:59:59+00:00")
    except (ValueError, TypeError):
        return []
    if end < start:
        start, end = end, start
    first = f"{start.year:04d}{start.month:02d}"
    last = f"{end.year:04d}{end.month:02d}"
    out: list[str] = []
    period = first
    while period <= last and len(out) < 24:
        out.append(period)
        period = _shift_period(period, 1)
    return out


def _resolve_projects(project_keys: list[str] | None) -> list[str]:
    """Mapeia as chaves do dashboard (vivo-mais/aura-brain) para o rótulo exibido."""
    resolved: list[str] = []
    for key in (project_keys or []):
        name = PROJECT_KEY_TO_GOLD.get(str(key or ""), str(key or ""))
        if name in PROJECTS and name not in resolved:
            resolved.append(name)
    return resolved or list(PROJECTS)


def _gold_project_names(project_labels: list[str]) -> list[str]:
    """Expande rótulos do dashboard para os valores literais na Gold."""
    names: list[str] = []
    for label in project_labels:
        for gold_name in GOLD_PROJECT_NAMES.get(label, (label,)):
            if gold_name not in names:
                names.append(gold_name)
    return names


async def _load_realized_rows(
    periods: list[str],
    project_keys: list[str] | None,
    cancel_event: asyncio.Event | None,
) -> list[dict[str, Any]]:
    """Agregação única sobre a Gold (realizado) por (período, projeto)."""
    selected_projects = _resolve_projects(project_keys)
    gold_projects = _gold_project_names(selected_projects)
    project_list = ", ".join(f"'{p}'" for p in gold_projects)
    period_list = ", ".join(f"'{p}'" for p in periods)
    reservation_charges = ", ".join(f"'{c}'" for c in RESERVATION_CHARGES)
    sql = f"""
        SELECT _billing_period AS periodo,
               {_PROJECT_SQL} AS projeto,
               MAX(departamento_nivel_4) AS departamento_nivel_4,
               SUM(COALESCE(TRY_CAST(realizado AS DOUBLE), 0))     AS realizado,
               SUM(COALESCE(TRY_CAST(billedcost AS DOUBLE), 0))    AS billedcost,
               SUM(CASE WHEN servicename = '{RESERVATION_SERVICE}'
                         AND chargecategory = 'Purchase'
                        THEN COALESCE(TRY_CAST(consumedquantity AS DOUBLE), 0)
                        ELSE 0 END) AS reservation_units_purchased,
               SUM(CASE WHEN servicename = '{RESERVATION_SERVICE}'
                        THEN COALESCE(TRY_CAST(realizado AS DOUBLE), 0)
                        ELSE 0 END) AS reservation_cost,
               SUM(CASE WHEN skumeter = '{ONDEMAND_METER}'
                         AND consumedunit = '{ONDEMAND_UNIT}'
                         AND commitmentdiscounttype = 'OnDemand'
                        THEN COALESCE(TRY_CAST(consumedquantity AS DOUBLE), 0)
                        ELSE 0 END) AS ondemand_units,
               SUM(CASE WHEN skumeter = '{ONDEMAND_METER}'
                         AND consumedunit = '{ONDEMAND_UNIT}'
                         AND commitmentdiscounttype = 'OnDemand'
                        THEN COALESCE(TRY_CAST(realizado AS DOUBLE), 0)
                        ELSE 0 END) AS ondemand_cost,
               COUNT(*) FILTER (WHERE skumeter = '{ONDEMAND_METER}'
                                 AND consumedunit = '{ONDEMAND_UNIT}'
                                 AND commitmentdiscounttype = 'OnDemand') AS ondemand_rows
        FROM {DETAIL_NAMESPACE}.{FOCUS_TABLE}
        WHERE _billing_period IN ({period_list})
          AND COALESCE(NULLIF(departamento_nivel_5, ''), departamento) IN ({project_list})
        GROUP BY 1, 2
        ORDER BY 1, 2
    """
    return await trino_execute(sql, timeout_s=90, cancel_event=cancel_event)


async def _load_forecast_rows(
    periods: list[str],
    project_keys: list[str] | None,
    cancel_event: asyncio.Event | None,
) -> list[dict[str, Any]]:
    """Agregação do forecast (planejamento) por (período, projeto).

    O planejamento não tem skumeter; a PTU on-demand no realizado aparece em
    Microsoft.CognitiveServices / Azure OpenAI (Usage) e a reserva em
    Microsoft.Capacity (Purchase). Expoe também esses dois valores previstos
    para completar o mês corrente quando o realizado ainda não chegou.
    """
    selected_projects = _resolve_projects(project_keys)
    gold_projects = _gold_project_names(selected_projects)
    project_list = ", ".join(f"'{p}'" for p in gold_projects)
    period_list = ", ".join(f"'{p}'" for p in periods)
    sql = f"""
        SELECT period AS periodo,
               {_PLANNING_PROJECT_SQL} AS projeto,
               MAX(departamento_nivel_4) AS departamento_nivel_4,
               SUM(COALESCE(TRY_CAST(realizado AS DOUBLE), 0)) AS realizado
        FROM {DETAIL_NAMESPACE}.{PLANNING_TABLE}
        WHERE period IN ({period_list})
          AND COALESCE(NULLIF(departamento_nivel_5, ''), departamento_nivel_5) IN ({project_list})
        GROUP BY 1, 2
        ORDER BY 1, 2
    """
    return await trino_execute(sql, timeout_s=90, cancel_event=cancel_event)


def _monthly_rows(
    realized: list[dict[str, Any]],
    forecast: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Junta realizado + forecast em uma linha mensal por projeto."""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in realized:
        period = str(row.get("periodo") or "")
        project = str(row.get("projeto") or "—")
        bucket = grouped.setdefault((period, project), {
            "period": period,
            "label": _period_label(period),
            "project": project,
            "department": row.get("departamento_nivel_4"),
            "reservation_units": 0.0,
            "reservation_cost": 0.0,
            "ondemand_units": None,
            "ondemand_cost": None,
            "ondemand_rows": 0,
            "total_realized": 0.0,
            "billedcost": 0.0,
            "forecast": None,
        })
        bucket["total_realized"] = float(bucket["total_realized"]) + float(row.get("realizado") or 0.0)
        bucket["billedcost"] = float(bucket["billedcost"]) + float(row.get("billedcost") or 0.0)
        bucket["reservation_units"] = float(bucket["reservation_units"]) + float(row.get("reservation_units_purchased") or 0.0)
        bucket["reservation_cost"] = float(bucket["reservation_cost"]) + float(row.get("reservation_cost") or 0.0)
        bucket["ondemand_rows"] = int(bucket["ondemand_rows"]) + int(row.get("ondemand_rows") or 0)
        if int(row.get("ondemand_rows") or 0) > 0:
            # Só expõe on-demand quando a linha mensal existe (evita "R$ 0"
            # quando a Gold ainda não recebeu o lançamento do mês).
            bucket["ondemand_units"] = float(row.get("ondemand_units") or 0.0)
            bucket["ondemand_cost"] = float(row.get("ondemand_cost") or 0.0)
    for row in forecast:
        period = str(row.get("periodo") or "")
        project = str(row.get("projeto") or "—")
        bucket = grouped.setdefault((period, project), {
            "period": period,
            "label": _period_label(period),
            "project": project,
            "department": row.get("departamento_nivel_4"),
            "reservation_units": 0.0,
            "reservation_cost": 0.0,
            "ondemand_units": None,
            "ondemand_cost": None,
            "ondemand_rows": 0,
            "total_realized": 0.0,
            "billedcost": 0.0,
            "forecast": None,
        })
        bucket["forecast"] = float(row.get("realizado") or 0.0)
    return [grouped[key] for key in sorted(grouped)]


def _project_totals(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Somas por projeto dentro de um conjunto de linhas mensais."""
    totals: dict[str, dict[str, float]] = {}
    for row in rows:
        project = row["project"]
        bucket = totals.setdefault(project, {
            "reservation_units": 0.0, "reservation_cost": 0.0,
            "ondemand_units": 0.0, "ondemand_cost": 0.0,
            "total_realized": 0.0,
            "billedcost": 0.0, "forecast": 0.0,
        })
        for field in ("reservation_units", "reservation_cost", "total_realized", "billedcost"):
            bucket[field] += float(row.get(field) or 0.0)
        bucket["ondemand_units"] += float(row.get("ondemand_units") or 0.0)
        bucket["ondemand_cost"] += float(row.get("ondemand_cost") or 0.0)
        bucket["forecast"] += float(row.get("forecast") or 0.0)
    return totals


def _carry_forward_reservation_purchase(monthly: list[dict[str, Any]]) -> None:
    """Mantém visível a última compra positiva da reserva nos meses seguintes.

    Microsoft.Capacity não necessariamente publica uma nova linha Purchase em
    todo mês. Ausência da linha não significa que as 100 PTUs do Vivo Mais
    desapareceram; o dashboard deve mostrá-las como referência da última
    compra, sem classificá-las como novo realizado do mês.
    """
    for row in monthly:
        if float(row.get("reservation_units") or 0) > 0:
            continue
        previous = next(
            (
                candidate for candidate in reversed(monthly)
                if candidate["project"] == row["project"]
                and candidate["period"] < row["period"]
                and not candidate.get("reservation_referenced")
                and float(candidate.get("reservation_units") or 0) > 0
                and float(candidate.get("reservation_cost") or 0) > 0
            ),
            None,
        )
        if previous is None:
            continue
        row["reservation_units"] = previous.get("reservation_units")
        row["reservation_cost"] = previous.get("reservation_cost")
        row["reservation_referenced"] = True
        row["reservation_reference_note"] = f"última compra {previous.get('label')}"


async def load_gold_ptu_costs(
    *,
    date_from: str,
    date_to: str,
    project_keys: list[str] | None = None,
    cancel_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    """Bloco financeiro do Azure PTU Ops. Nunca levanta exceção para o caller."""
    try:
        return await _load_gold_ptu_costs(
            date_from=date_from, date_to=date_to,
            project_keys=project_keys, cancel_event=cancel_event,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Azure PTU cost enrichment failed: %s", exc, exc_info=exc)
        return {"available": False, "error": str(exc)[:400], "table": FOCUS_TABLE}


async def _load_gold_ptu_costs(
    *,
    date_from: str,
    date_to: str,
    project_keys: list[str] | None,
    cancel_event: asyncio.Event | None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    current_period = now.strftime("%Y%m")

    window_periods = _periods_in_window(date_from, date_to)
    history_periods = [
        _shift_period(current_period, i - (HISTORY_MONTHS - 1))
        for i in range(HISTORY_MONTHS)
    ]
    all_periods = sorted(set(history_periods) | set(window_periods))

    realized, forecast = await asyncio.gather(
        _load_realized_rows(all_periods, project_keys, cancel_event),
        _load_forecast_rows(all_periods, project_keys, cancel_event),
        return_exceptions=True,
    )
    if isinstance(realized, Exception):
        raise realized
    if isinstance(forecast, Exception):
        logger.warning("Azure PTU forecast (planning) load failed: %s", forecast)
        forecast = []

    monthly = _monthly_rows(realized, forecast)
    if not monthly:
        return {
            "available": True,
            "empty": True,
            "table": FOCUS_TABLE,
            "current_period": current_period,
            "window_label": _window_label(window_periods),
            "monthly": [],
        }

    realized_periods = sorted({row["period"] for row in monthly})
    candidates = [p for p in window_periods if p in realized_periods] or realized_periods
    current = candidates[-1] if candidates else current_period

    # A reserva do Vivo Mais continua contratada mesmo quando Microsoft.Capacity
    # não publica uma nova linha Purchase no mês do filtro.
    _carry_forward_reservation_purchase(monthly)

    # On-demand é uma compra fixa de 100 PTUs/mês (ex.: Aura Brain). Quando o
    # mês corrente ainda não recebeu a linha na Gold (ex.: ago/2026), estima
    # pelo ÚLTIMO REALIZADO da compra — "OnDemand = só o valor da compra; o
    # resto (consumo diário/horário) é ignorado". O forecast do planejamento
    # não é usado: no Aura Brain ele espelha o consumo horário ('Azure OpenAI'
    # Usage), não a compra. Vale SOMENTE para projetos com histórico real de
    # on-demand — nunca fabrica unidades para projetos cobertos pela reserva.
    for row in monthly:
        if int(row.get("ondemand_rows") or 0) == 0 and row["project"] in PROJECTS:
            previous = next(
                (r for r in reversed(monthly)
                 if r["project"] == row["project"] and r["period"] < row["period"]
                 and int(r.get("ondemand_rows") or 0) > 0),
                None,
            )
            if previous is None:
                # Sem histórico de on-demand contratado: mantém 0/None — a
                # capacidade desse projeto é coberta pela reserva (Microsoft.Capacity).
                continue
            row["ondemand_units"] = previous.get("ondemand_units")
            row["ondemand_cost"] = previous.get("ondemand_cost")
            row["ondemand_estimated"] = True
            row["ondemand_estimate_note"] = f"previsto — último realizado {previous.get('label')}"

    current_rows = [row for row in monthly if row["period"] == current]
    by_project: dict[str, dict[str, Any]] = {}
    for row in current_rows:
        by_project[row["project"]] = {
            "reservation_units": row["reservation_units"],
            "reservation_cost": row["reservation_cost"],
            "reservation_referenced": row.get("reservation_referenced", False),
            "reservation_reference_note": row.get("reservation_reference_note"),
            "ondemand_units": row["ondemand_units"],
            "ondemand_cost": row["ondemand_cost"],
            "ondemand_estimated": row.get("ondemand_estimated", False),
            "ondemand_estimate_note": row.get("ondemand_estimate_note"),
            "total_realized": row["total_realized"],
            "billedcost": row["billedcost"],
            "forecast": row["forecast"],
            "department": row["department"],
        }
    project_totals = _project_totals(current_rows)
    totals = {field: sum(bucket[field] for bucket in project_totals.values()) for field in
              ("reservation_units", "reservation_cost", "total_realized", "billedcost")}
    totals["forecast"] = sum(bucket["forecast"] for bucket in project_totals.values())
    totals["ondemand_units"] = sum(bucket["ondemand_units"] for bucket in project_totals.values())
    totals["ondemand_cost"] = sum(bucket["ondemand_cost"] for bucket in project_totals.values())
    totals["ondemand_estimated"] = any(row.get("ondemand_estimated") for row in current_rows)
    totals["reservation_referenced"] = any(row.get("reservation_referenced") for row in current_rows)

    # Notas de qualidade do dado (ex.: on-demand do mês corrente estimado).
    notes: list[str] = []
    estimated_rows = [row for row in current_rows if row.get("ondemand_estimated")]
    if estimated_rows:
        for row in estimated_rows:
            notes.append(
                f"On-demand PTU de {row['project']} ({_period_label(current)}) ainda não lançado na Gold — "
                f"usado {row.get('ondemand_estimate_note', 'previsto')} "
                f"(R$ {float(row.get('ondemand_cost') or 0):,.0f})."
            )
    referenced_reservations = [row for row in current_rows if row.get("reservation_referenced")]
    for row in referenced_reservations:
        notes.append(
            f"Reserva PTU de {row['project']} ({_period_label(current)}): exibida pela "
            f"{row.get('reservation_reference_note', 'última compra disponível')} "
            f"({float(row.get('reservation_units') or 0):,.0f} PTUs · "
            f"R$ {float(row.get('reservation_cost') or 0):,.0f}); não é uma nova compra do mês."
        )
    if any(float(row.get("reservation_cost") or 0) > 0 and float(row.get("reservation_units") or 0) != 100
           for row in current_rows):
        notes.append("Quantidade da reserva diferente de 100 PTUs no mês de referência (ajuste/refund de capacidade).")

    return {
        "available": True,
        "empty": False,
        "table": FOCUS_TABLE,
        "current_period": current,
        "current_label": _period_label(current),
        "window_label": _window_label(window_periods),
        "history_periods": history_periods,
        "projects": sorted({row["project"] for row in monthly}),
        "notes": notes,
        "current": {
            "totals": totals,
            "projects": by_project,
        },
        "monthly": monthly,
    }


def _window_label(periods: list[str]) -> str:
    if not periods:
        return "—"
    if len(periods) == 1:
        return _period_label(periods[0])
    return f"{_period_label(periods[0])} – {_period_label(periods[-1])}"
