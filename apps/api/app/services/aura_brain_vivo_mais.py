"""Dashboard executivo de custo dos projetos Aura Brain e Vivo Mais.

Lê as Dimons pré-agregadas de Aura Brain e Vivo Mais e decompõe o custo de
cada projeto em
classes executivas:

* **ptu** — a **compra mensal** de capacidade PTU: a reserva
  (``chargecategory`` Purchase/Refund/Return sobre ``Azure AI Foundry
  Provisioned Throughput Reservation`` / ``Microsoft.Capacity``) e a PTU
  on-demand mensal (meter ``Provisioned Throughput Units``, 1/Month). No mês
  de referência vale **uma única fonte**: a compra realizada se existir;
  senão o forecast (``tipo='forecast'``) do mesmo mês. Nunca os dois.
* **tokens_ondemand** — meters PAYG de tokens (``skumeter`` com "Tokens" ou
  "consumption-units": gpt 4o, gpt 4.1 mini, embedding-ada, Claude Foundry CCU).
* **infra** — todo o restante (VMs, Cosmos DB, discos, rede, Service Bus,
  Log Analytics...), detalhado por ``servicesubcategory``.

O **consumo diário/horário** de capacidade (meters ``Provisioned Managed*``,
1/Hour — uso da PTU coberto pela compra, anulado por Returns na Gold) **não é
retornado**: ele já está no custo da compra; devolvê-lo duplicaria o valor.

Fato de negócio da aba Vivo Mais: todo o custo do Vivo Mais vive nas subcontas
do Aura Brain (CORE VP ... AURA BRAIN - PRO/DEV) — o projeto consome a
capacidade/infra do Aura Brain e não recebe rateio de infra porque a
infraestrutura não é específica do Vivo Mais.
"""

from __future__ import annotations

import asyncio
from datetime import date
import logging
import time
from typing import Any

from app.services.trino_service import execute as trino_execute
from app.services.dimon_materialization import DIMON_NAMESPACE
from app.services.azure_ptu_dimon import (
    DIMON_TABLE as PTU_DIMON_TABLE,
    load_project_token_totals,
    load_project_tokens_last_30d,
)

logger = logging.getLogger(__name__)

DIMON_NAMESPACE = f"iceberg.{__import__('app.services.dimon_materialization', fromlist=['DIMON_NAMESPACE']).DIMON_NAMESPACE}"
PROJECT_LABELS = {"aura-brain": "Aura Brain", "vivo-mais": "Vivo Mais"}
PROJECTS = ("Aura Brain", "Vivo Mais")
PROJECT_SOURCES = {
    "aura-brain": {
        "table": "dest_dm_cost_custo_aura_brain_vivo_mais",
        "source_project": "Aura Brain",
    },
    "vivo-mais": {
        "table": "dest_dm_cost_custo_vivo_mais",
        "source_project": "Vivo+",
    },
}

# Meters da compra mensal de PTU on-demand (1/Month) — lowercased.
_PTU_PURCHASE_METERS = (
    "provisioned throughput unit",
    "provisioned throughput units",
    "azure openai provisioned throughput",
)
_PTU_PURCHASE_METERS_IN = ", ".join(f"'{m}'" for m in _PTU_PURCHASE_METERS)

# Serviços relacionados a IA na fatura Azure (por servicesubcategory) que
# merecem card próprio — saem de infra e de tokens on-demand. A ordem importa:
# regras mais específicas primeiro (a primeira correspondência vence).
_IA_FAMILY_RULES: tuple[tuple[str, str], ...] = (
    ("azure document intelligence", "Azure Document Intelligence"),
    ("azure openai gpt5", "Azure OpenAI GPT-5"),
    ("azure openai media", "Azure OpenAI Media"),
    ("azure openai reasoning", "Azure OpenAI Reasoning"),
    ("azure deepseek", "Azure Deepseek"),
    ("azure openai oss models", "Azure OpenAI OSS"),
    ("azure speech", "Azure Speech"),
    ("claude", "Claude (Anthropic)"),
    ("anthropic", "Claude (Anthropic)"),
    ("mistral", "Outros modelos de IA"),
    ("cohere", "Outros modelos de IA"),
)


def _ia_family_sql() -> str:
    """CASE SQL servicesubcategory -> família canônica de IA (ou NULL)."""
    clauses = " ".join(
        f"WHEN lower(COALESCE(CAST(servicesubcategory AS VARCHAR), '')) LIKE '%{key}%' THEN '{family}'"
        for key, family in _IA_FAMILY_RULES
    )
    return f"CASE {clauses} ELSE NULL END"

# Classificação executiva por linha da Dimon. Ordem importa:
# 1) compra/reserva de capacidade (Purchase/Refund/Return da reserva Foundry /
#    Microsoft.Capacity) — pode vir com meter "Provisioned Managed*";
# 2) consumo diário/horário (Provisioned Managed*, 1/Hour) -> EXCLUÍDO;
# 3) compra mensal on-demand de PTU (Provisioned Throughput Units, 1/Month);
# 4) serviços de IA (Document Intelligence, Claude, GPT-5, Speech...) -> card próprio;
# 5) meters PAYG de tokens;
# 6) resto -> infra.
_COST_CLASS_SQL = f"""
CASE
  WHEN chargecategory IN ('Purchase', 'Refund', 'Return')
    AND (lower(COALESCE(CAST(servicesubcategory AS VARCHAR), '')) LIKE '%provisioned throughput reservation%'
         OR lower(COALESCE(CAST(servicename AS VARCHAR), '')) LIKE '%capacity%')
    THEN 'ptu'
  WHEN lower(COALESCE(CAST(skumeter AS VARCHAR), '')) LIKE 'provisioned managed%'
    THEN '__excluded__'
  WHEN lower(COALESCE(CAST(skumeter AS VARCHAR), '')) IN ({_PTU_PURCHASE_METERS_IN})
    THEN 'ptu'
  WHEN {_ia_family_sql()} IS NOT NULL
    THEN 'ia_service'
  WHEN lower(COALESCE(CAST(skumeter AS VARCHAR), '')) LIKE '%tokens%'
    OR lower(COALESCE(CAST(skumeter AS VARCHAR), '')) LIKE '%consumption-units%'
    THEN 'tokens_ondemand'
  ELSE 'infra'
END
"""

COST_CLASSES = ("ptu", "tokens_ondemand", "ia_service", "infra")
COST_CLASS_LABELS = {
    "ptu": "PTUs (compra mensal)",
    "tokens_ondemand": "Tokens on-demand",
    "ia_service": "Serviços de IA",
    "infra": "Infraestrutura",
}

# Cache curto: a Dimon é alimentada em lote (batch), então 60s é seguro e
# evita repetir as queries Trino a cada redraw/refiltro do React.
_COST_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_COST_CACHE_TTL_S = 60.0

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


def _last_closed_period(today: date | None = None) -> str:
    """Retorna o mês anterior ao atual no formato YYYYMM."""
    reference = today or date.today()
    if reference.month == 1:
        return f"{reference.year - 1}12"
    return f"{reference.year}{reference.month - 1:02d}"


def _empty_classes() -> dict[str, float]:
    return {cls: 0.0 for cls in COST_CLASSES}


def ia_family_for(row: dict[str, Any]) -> str | None:
    """Família canônica de um serviço de IA (ou None se não for serviço de IA)."""
    sub = str(row.get("servicesubcategory") or "").strip().lower()
    if not sub:
        return None
    for key, family in _IA_FAMILY_RULES:
        if key in sub:
            return family
    return None


def classify_dimon_row(row: dict[str, Any]) -> str:
    """Classifica uma linha da Dimon (realizada ou forecast) em classe executiva.

    Retorna ``__excluded__`` para o consumo diário/horário (Provisioned
    Managed*, 1/Hour), que não deve aparecer no dashboard, e ``ia_service``
    para serviços de IA com card próprio. Exposto para testes; o SQL de
    produção usa a mesma lógica em ``_COST_CLASS_SQL``.
    """
    skumeter = str(row.get("skumeter") or "").strip().lower()
    chargecategory = str(row.get("chargecategory") or "").strip()
    servicesubcategory = str(row.get("servicesubcategory") or "").strip().lower()
    servicename = str(row.get("servicename") or "").strip().lower()

    if chargecategory in ("Purchase", "Refund", "Return") and (
        "provisioned throughput reservation" in servicesubcategory
        or "capacity" in servicename
    ):
        return "ptu"
    if skumeter.startswith("provisioned managed"):
        return "__excluded__"
    if skumeter in _PTU_PURCHASE_METERS:
        return "ptu"
    if ia_family_for(row) is not None:
        return "ia_service"
    if "tokens" in skumeter or "consumption-units" in skumeter:
        return "tokens_ondemand"
    return "infra"


async def _query_cost_rows(
    project_key: str,
    cancel_event: asyncio.Event | None,
) -> list[dict[str, Any]]:
    """Todas as linhas da Dimon agregadas por (período, projeto, tipo,
    chargecategory, classe, servicesubcategory, is_ondemand_ptu). O consumo
    diário/horário sai do resultado na própria query (WHERE <> '__excluded__').
    """
    source = PROJECT_SOURCES[project_key]
    table_sql = f"{DIMON_NAMESPACE}.{source['table']}"
    project = PROJECT_LABELS[project_key]
    source_project = source["source_project"].lower().replace("'", "''")
    sql = f"""
        SELECT _billing_period AS period,
               '{project}' AS project,
               tipo,
               chargecategory,
               {_COST_CLASS_SQL} AS cost_class,
               COALESCE(CAST(servicesubcategory AS VARCHAR), '—') AS servicesubcategory,
               {_ia_family_sql()} AS ia_family,
               CASE WHEN lower(COALESCE(CAST(skumeter AS VARCHAR), '')) IN ({_PTU_PURCHASE_METERS_IN})
                    THEN 1 ELSE 0 END AS is_ondemand_ptu,
               COALESCE(SUM(TRY_CAST(realizado AS DOUBLE)), 0) AS realized
        FROM {table_sql}
        WHERE lower(CAST(organograma_nivel_5 AS VARCHAR)) = '{source_project}'
          AND {_COST_CLASS_SQL} <> '__excluded__'
        GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
    """
    return await trino_execute(sql, timeout_s=120, cancel_event=cancel_event)


async def _query_subaccounts(
    project_key: str,
    period: str,
    cancel_event: asyncio.Event | None,
) -> list[dict[str, Any]]:
    """Distribuição do mês por subconta (sustenta a narrativa do rateio)."""
    source = PROJECT_SOURCES[project_key]
    table_sql = f"{DIMON_NAMESPACE}.{source['table']}"
    source_project = source["source_project"].lower().replace("'", "''")
    rows = await trino_execute(
        f"""
        SELECT subaccountname,
               COALESCE(SUM(TRY_CAST(realizado AS DOUBLE)), 0) AS realized
        FROM {table_sql}
        WHERE lower(CAST(organograma_nivel_5 AS VARCHAR)) = '{source_project}'
          AND _billing_period = '{period}'
          AND tipo = 'realizado'
          AND {_COST_CLASS_SQL} <> '__excluded__'
        GROUP BY 1
        ORDER BY 2 DESC
        """,
        timeout_s=60,
        cancel_event=cancel_event,
    )
    return rows or []


def _ondemand_ptu_estimate(
    rows: list[dict[str, Any]],
    period: str,
    project: str,
) -> tuple[float, str | None]:
    """Valor da compra on-demand de PTU para o mês de referência.

    Regra: OnDemand = só o valor da compra mensal (Provisioned Throughput
    Units, 1/Month). Quando o mês ainda não recebeu a compra na Gold, estima
    pelo **último realizado da compra** — o planejamento do Aura Brain espelha
    o consumo horário em ``Azure OpenAI`` e não expõe a compra, então não é
    usado (o "resto" — consumo diário — deve ser ignorado).

    Retorna ``(valor, nota_da_estimativa)``.
    """
    last_value = 0.0
    last_period: str | None = None
    for row in rows:
        if str(row.get("project") or "") != project:
            continue
        if str(row.get("tipo") or "") != "realizado":
            continue
        if int(row.get("is_ondemand_ptu") or 0) != 1:
            continue
        row_period = str(row.get("period") or "")
        if not row_period or row_period >= period:
            continue
        if last_period is None or row_period > last_period:
            last_period = row_period
            last_value = float(row.get("realized") or 0)
    note = f"último realizado ({_period_label(last_period)})" if last_period else None
    return last_value, note


def _monthly_series(
    rows: list[dict[str, Any]],
    periods: list[str],
    project: str,
) -> list[dict[str, Any]]:
    """Série mensal com classes do realizado (incl. serviços de IA por família)
    e total do forecast (de um projeto)."""
    realized_by_period: dict[str, dict[str, Any]] = {}
    forecast_by_period: dict[str, float] = {}
    for row in rows:
        if str(row.get("project") or "") != project:
            continue
        period = str(row.get("period") or "")
        if not period:
            continue
        tipo = str(row.get("tipo") or "")
        amount = float(row.get("realized") or 0)
        if tipo == "forecast":
            # Forecast bruto: as linhas Return/Refund do espelho espelham o
            # consumo horário (que já é excluído do realizado e não é custo
            # incremental) — abatê-las deixaria os meses futuros próximos de
            # zero e o gráfico "vazio".
            if str(row.get("chargecategory") or "").upper() in ("RETURN", "REFUND"):
                continue
            forecast_by_period[period] = forecast_by_period.get(period, 0.0) + amount
            continue
        # A Dimon específica do Vivo Mais também publica ``tipo='rateio'``
        # para meses futuros. Rateio não é custo realizado e não pode definir
        # o mês de referência nem ser somado aos KPIs de realizado.
        if tipo != "realizado":
            continue
        bucket = realized_by_period.setdefault(period, {"classes": _empty_classes(), "ia_services": {}})
        klass = str(row.get("cost_class") or "infra")
        if klass == "__excluded__":
            continue
        bucket["classes"][klass] = bucket["classes"].get(klass, 0.0) + amount
        if klass == "ia_service":
            family = str(row.get("ia_family") or "Serviços de IA")
            bucket["ia_services"][family] = bucket["ia_services"].get(family, 0.0) + amount

    series: list[dict[str, Any]] = []
    for period in periods:
        bucket = realized_by_period.get(period)
        if bucket is None:
            series.append({
                "period": period,
                "label": _period_label(period),
                "realizado": None,
                "forecast": forecast_by_period.get(period),
            })
            continue
        classes = bucket["classes"]
        total = sum(classes.values())
        series.append({
            "period": period,
            "label": _period_label(period),
            "realizado": {
                "total": total,
                **{cls: classes.get(cls, 0.0) for cls in COST_CLASSES},
                "ia_services": dict(bucket["ia_services"]),
            },
            "forecast": forecast_by_period.get(period),
        })
    return series


def _mom_pct(current_total: float | None, previous_total: float | None) -> float | None:
    if current_total is None or not previous_total:
        return None
    return ((current_total - previous_total) / previous_total) * 100


def _infra_by_subcategory(
    rows: list[dict[str, Any]],
    current_period: str,
    project: str,
) -> list[dict[str, Any]]:
    """Ranking de infra por servicesubcategory no mês corrente (realizado)."""
    buckets: dict[str, float] = {}
    for row in rows:
        if str(row.get("project") or "") != project:
            continue
        if str(row.get("period") or "") != current_period:
            continue
        if str(row.get("tipo") or "") != "realizado":
            continue
        if str(row.get("cost_class") or "") != "infra":
            continue
        sub = str(row.get("servicesubcategory") or "—")
        buckets[sub] = buckets.get(sub, 0.0) + float(row.get("realized") or 0)
    total = sum(v for v in buckets.values() if v > 0)
    return [
        {
            "servicesubcategory": name,
            "realized": amount,
            "pct": (amount / total * 100) if total else None,
        }
        for name, amount in sorted(buckets.items(), key=lambda item: item[1], reverse=True)
        if amount > 0
    ]


def _ia_services_ranking(
    rows: list[dict[str, Any]],
    current_period: str,
    project: str,
) -> list[dict[str, Any]]:
    """Ranking das famílias de serviços de IA no mês corrente (realizado)."""
    buckets: dict[str, float] = {}
    for row in rows:
        if str(row.get("project") or "") != project:
            continue
        if str(row.get("period") or "") != current_period:
            continue
        if str(row.get("tipo") or "") != "realizado":
            continue
        if str(row.get("cost_class") or "") != "ia_service":
            continue
        family = str(row.get("ia_family") or "Serviços de IA")
        buckets[family] = buckets.get(family, 0.0) + float(row.get("realized") or 0)
    total = sum(v for v in buckets.values() if v > 0)
    return [
        {
            "family": name,
            "realized": amount,
            "pct": (amount / total * 100) if total else None,
        }
        for name, amount in sorted(buckets.items(), key=lambda item: item[1], reverse=True)
        if amount > 0
    ]


async def load_project_cost_dashboard(
    *,
    project_key: str,
    period_range: str = "current_year",
    cancel_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    """Payload executivo de custo de um projeto. Nunca levanta para o caller de rota."""
    project = PROJECT_LABELS.get(str(project_key or ""))
    if not project:
        raise ValueError(f"project_key inválida: {project_key}")
    if period_range not in ("current_year", "all"):
        raise ValueError(f"period_range inválido: {period_range}")

    cache_key = f"{project_key}|{period_range}"
    cached = _COST_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < _COST_CACHE_TTL_S:
        return cached[1]

    source_table = PROJECT_SOURCES[project_key]["table"]
    rows = await _query_cost_rows(project_key, cancel_event)
    periods = sorted({str(r.get("period") or "") for r in rows if r.get("period")})
    if not periods:
        payload = {"available": False, "error": "Dimon sem períodos disponíveis.", "table": source_table}
        _COST_CACHE[cache_key] = (time.monotonic(), payload)
        return payload

    series_all = _monthly_series(rows, periods, project)
    try:
        # A janela de tokens é independente dos períodos existentes na Dimon
        # financeira. Isso permite sinalizar e estimar lacunas de cobertura.
        tokens_by_period = await load_project_token_totals(project_key, None, cancel_event)
    except Exception as exc:
        logger.warning("Tokens da Dimon PTU falharam para %s: %s", project_key, exc)
        tokens_by_period = {}
    try:
        tokens_last_30d = await load_project_tokens_last_30d(project_key, cancel_event)
    except Exception as exc:
        logger.warning("Tokens 30d da Dimon PTU falharam para %s: %s", project_key, exc)
        tokens_last_30d = {}
    for month in series_all:
        token_row = tokens_by_period.get(str(month.get("period") or ""), {})
        total_tokens = float(token_row.get("total_tokens") or 0)
        month["tokens"] = {
            "input_tokens": float(token_row.get("input_tokens") or 0),
            "output_tokens": float(token_row.get("output_tokens") or 0),
            "total_tokens": total_tokens,
        }
        realized_total = (month.get("realizado") or {}).get("total")
        month["cost_per_million_tokens"] = (
            float(realized_total) * 1_000_000 / total_tokens
            if realized_total is not None and total_tokens > 0 else None
        )
    anchor_idx = next(
        (i for i in range(len(series_all) - 1, -1, -1)
         if series_all[i].get("realizado") and series_all[i]["realizado"].get("total") is not None),
        None,
    )
    if anchor_idx is None:
        payload = {
            "available": True,
            "empty": True,
            "table": source_table,
            "project_key": project_key,
            "project_label": project,
            "available_periods": periods,
            "monthly": [],
            "current": None,
            "notes": ["Nenhum mês com custo realizado na Dimon."],
        }
        _COST_CACHE[cache_key] = (time.monotonic(), payload)
        return payload

    # O realizado de referência é o mês fechado anterior ao mês corrente.
    # Se a carga já contém o mês atual parcial, ele é ignorado; se o último
    # realizado disponível já é o mês anterior, ele não deve ser recuado de novo.
    closed_period = _last_closed_period()
    closed_realized_indices = [
        i for i, month in enumerate(series_all)
        if month["period"] <= closed_period
        and month.get("realizado")
        and month["realizado"].get("total") is not None
    ]
    current_idx = closed_realized_indices[-1] if closed_realized_indices else anchor_idx
    anchor = series_all[current_idx]
    if period_range == "current_year":
        anchor_year = anchor["period"][:4]
        series = [m for m in series_all if f"{anchor_year}01" <= m["period"] <= f"{anchor_year}12"]
    else:
        series = series_all
    current = anchor
    previous_realized_indices = [
        i for i in range(current_idx)
        if series_all[i].get("realizado")
        and series_all[i]["realizado"].get("total") is not None
    ]
    prev = series_all[previous_realized_indices[-1]] if previous_realized_indices else None
    current_period = current["period"]
    # Todos os detalhes usam a mesma referência do KPI.
    detail_period = current_period
    detail_label = _period_label(detail_period)

    subaccounts_task = _query_subaccounts(project_key, detail_period, cancel_event)
    subaccounts = await subaccounts_task
    if isinstance(subaccounts, Exception):
        logger.warning("Subcontas Dimon falharam: %s", subaccounts)
        subaccounts = []

    # PTU do mês de referência: compra realizada se existir; senão estimada
    # pelo último realizado da compra on-demand ("OnDemand = só o valor da
    # compra; o resto — consumo diário — é ignorado").
    realized = current.get("realizado") or {}
    realized_ptu = float(realized.get("ptu") or 0)
    ptu_estimate_note: str | None = None
    if realized_ptu != 0:
        ptu_source = "realizado"
        ptu_value = realized_ptu
    else:
        estimate, note = _ondemand_ptu_estimate(rows, current_period, project)
        if estimate > 0:
            ptu_source = "estimado"
            ptu_value = estimate
            ptu_estimate_note = note
        else:
            ptu_source = "realizado"
            ptu_value = 0.0

    classes = {cls: float(realized.get(cls) or 0) for cls in COST_CLASSES}
    classes["ptu"] = ptu_value
    blended_total = sum(classes.values()) if realized.get("total") is not None else None
    forecast_total = None

    notes: list[str] = []
    if ptu_source == "estimado":
        notes.append(
            f"A compra mensal de PTU de {project} em {current['label']} ainda não foi lançada na Gold — "
            f"estimado pelo {ptu_estimate_note}: R$ {ptu_value:,.0f} (OnDemand = só o valor da compra; "
            "o consumo diário/horário é ignorado). Quando a compra entrar no realizado, ela substitui a estimativa."
        )
    if any(str(r.get("tipo") or "") == "forecast" for r in rows):
        notes.append(
            "Forecast (planejamento) sem granularidade por meter — exibido como total bruto "
            "(as devoluções do espelho, que espelham o consumo horário já excluído do realizado, não são abatidas)."
        )
    if ptu_source == "realizado" and any(
        str(r.get("period") or "") == current_period
        and str(r.get("project") or "") == project
        and str(r.get("tipo") or "") == "realizado"
        and str(r.get("chargecategory") or "") in ("Return", "Refund")
        for r in rows
    ):
        notes.append(
            f"O mês {current['label']} tem devoluções (Returns/Refunds) abatidas do custo bruto de capacidade."
        )

    current_payload = {
        "period": current_period,
        "label": current["label"],
        "realizado": {
            "total": blended_total,
            "realizado_total": blended_total,
            "forecast_total": forecast_total,
            "classes": classes,
            "pct": {
                cls: (classes[cls] / blended_total * 100) if blended_total else None
                for cls in COST_CLASSES
            },
        },
        "ptu_source": ptu_source,
        "ptu_estimate_note": ptu_estimate_note,
        "forecast_total": forecast_total,
        "mom_pct": _mom_pct(
            blended_total,
            prev["realizado"]["total"] if prev and prev.get("realizado") else None,
        ),
        "prev_period_label": prev["label"] if prev else None,
        "infra_by_subcategory": _infra_by_subcategory(rows, detail_period, project),
        "ia_services_by_subcategory": _ia_services_ranking(rows, detail_period, project),
        "detail_period": detail_period,
        "detail_label": detail_label,
        "subaccounts": [
            {"subaccountname": str(s.get("subaccountname") or "—"), "realized": float(s.get("realized") or 0)}
            for s in subaccounts
        ],
    }
    current_tokens = tokens_last_30d
    current_total_tokens = float(current_tokens.get("total_tokens") or 0)
    current_payload["token_efficiency"] = {
        "input_tokens": float(current_tokens.get("input_tokens") or 0),
        "output_tokens": float(current_tokens.get("output_tokens") or 0),
        "total_tokens": current_total_tokens,
        "million_tokens": current_total_tokens / 1_000_000,
        "project_cost": blended_total,
        "cost_per_million_tokens": (
            blended_total * 1_000_000 / current_total_tokens
            if blended_total is not None and current_total_tokens > 0 else None
        ),
        "cost_period": current_period,
        "token_window": "last_30_days",
        "token_date_from": current_tokens.get("date_from"),
        "token_date_to": current_tokens.get("date_to"),
        "estimated": False,
        "note": (
            f"Custo mensal de {_period_label(current_period)} dividido pelo consumo móvel dos últimos 30 dias."
        ),
        "source_table": PTU_DIMON_TABLE,
    }

    payload = {
        "available": True,
        "table": source_table,
        "project_key": project_key,
        "project_label": project,
        "available_periods": periods,
        "period_range": period_range,
        "monthly": series,
        "current": current_payload,
        "notes": notes,
    }
    _COST_CACHE[cache_key] = (time.monotonic(), payload)
    if len(_COST_CACHE) > 32:
        oldest = sorted(_COST_CACHE.items(), key=lambda item: item[1][0])[:8]
        for key, _ in oldest:
            _COST_CACHE.pop(key, None)
    return payload
