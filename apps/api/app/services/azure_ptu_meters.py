"""Versioned Azure OpenAI PTU FOCUS meter allowlist and FinOps formulas."""

from __future__ import annotations

from typing import Any, Iterable

# Exact meter names observed for Azure OpenAI provisioned capacity.
# Do not match generic "Provisioned" (disks, storage, SQL).
AZURE_OPENAI_PTU_METER_ALLOWLIST_VERSION = "2026-08-24"
AZURE_OPENAI_PTU_METERS: frozenset[str] = frozenset({
    "Provisioned Managed Global Unit",
    "Provisioned Managed Unit",
    "Provisioned Throughput Unit",
    "Azure OpenAI Provisioned Throughput",
    "PTU",
    "Global Provisioned Managed",
    "Regional Provisioned Managed",
})

_BLOCK_SUBSTRINGS = ("storage", "disk", "premium ssd", "standard ssd", "managed disk", "blob", "files")
_ALLOW_SUBSTRINGS = (
    "provisioned managed",
    "provisioned throughput",
    "global provisioned",
    "regional provisioned",
    "openai ptu",
)


def focus_date_sql_expr(
    chargeperiodstart: str = '"chargeperiodstart"',
    billingperiodstart: str = '"billingperiodstart"',
) -> str:
    """Parse FOCUS period columns stored as MM/DD/YYYY (chargeperiodstart is always populated)."""
    charge = f"TRY_CAST(date_parse(CAST({chargeperiodstart} AS VARCHAR), '%m/%d/%Y') AS DATE)"
    billing = f"TRY_CAST(date_parse(CAST({billingperiodstart} AS VARCHAR), '%m/%d/%Y') AS DATE)"
    return f"COALESCE({charge}, {billing})"


def focus_date_predicate_sql(
    date_from: str | None = None,
    date_to: str | None = None,
    *,
    chargeperiodstart: str = '"chargeperiodstart"',
    billingperiodstart: str = '"billingperiodstart"',
) -> str:
    """Bounded date filter for dest_billing_cloud (never compare ISO strings to MM/DD/YYYY)."""
    date_expr = focus_date_sql_expr(chargeperiodstart, billingperiodstart)
    clauses: list[str] = []
    if date_from:
        clauses.append(f"{date_expr} >= DATE '{str(date_from)[:10]}'")
    if date_to:
        clauses.append(f"{date_expr} <= DATE '{str(date_to)[:10]}'")
    return " AND ".join(clauses) if clauses else "1=1"


def focus_period_bucket_sql(
    grain: str = "month",
    *,
    chargeperiodstart: str = '"chargeperiodstart"',
    billingperiodstart: str = '"billingperiodstart"',
) -> str:
    """Bucket FOCUS rows by parsed charge/billing period start."""
    date_expr = focus_date_sql_expr(chargeperiodstart, billingperiodstart)
    if grain == "day":
        return f"date_format({date_expr}, '%Y-%m-%d')"
    if grain == "hour":
        return f"date_format({date_expr}, '%Y-%m-%d %H')"
    return f"date_format({date_expr}, '%Y-%m')"


def focus_ptu_meter_sql_expr(
    skumeter: str = '"skumeter"',
    skuid: str = '"skuid"',
    servicename: str = '"servicename"',
    chargedescription: str = '"chargedescription"',
    skupricedetails: str = '"skupricedetails"',
) -> str:
    """SQL expression (0/1) approximating is_azure_openai_ptu_meter for aggregations."""
    haystack = _focus_haystack_sql(skumeter, skuid, servicename, chargedescription, skupricedetails)
    blocked = " OR ".join(f"{haystack} LIKE '%{token}%'" for token in _BLOCK_SUBSTRINGS)
    allowed = " OR ".join(f"{haystack} LIKE '%{token}%'" for token in _ALLOW_SUBSTRINGS)
    openai_like = (
        f"({haystack} LIKE '%openai%' OR {haystack} LIKE '%cognitive services%' "
        f"OR {haystack} LIKE '%cognitiveservices%' OR {haystack} LIKE '%azure openai%')"
    )
    return (
        f"CASE WHEN {openai_like} AND NOT ({blocked}) AND ({allowed}) THEN 1 ELSE 0 END"
    )


def focus_ptu_meter_where_sql(
    skumeter: str = '"skumeter"',
    skuid: str = '"skuid"',
    servicename: str = '"servicename"',
    chargedescription: str = '"chargedescription"',
    skupricedetails: str = '"skupricedetails"',
) -> str:
    return f"({focus_ptu_meter_sql_expr(skumeter, skuid, servicename, chargedescription, skupricedetails)} = 1)"


def is_azure_openai_ptu_meter(
    *,
    sku_meter: str | None = None,
    sku_name: str | None = None,
    service_name: str | None = None,
    meter_name: str | None = None,
    charge_description: str | None = None,
) -> bool:
    parts = [sku_meter, sku_name, service_name, meter_name, charge_description]
    haystack = " ".join(str(p or "") for p in parts).lower()
    if any(blocked in haystack for blocked in _BLOCK_SUBSTRINGS):
        return False
    exact = {str(p).strip() for p in parts if p}
    if exact & AZURE_OPENAI_PTU_METERS:
        if "openai" in haystack or "cognitive" in haystack or "ai services" in haystack or "ptu" in haystack:
            return True
        return "openai" in haystack or any(m.lower() in haystack for m in AZURE_OPENAI_PTU_METERS)
    openai_like = "openai" in haystack or "cognitive services" in haystack or "azure openai" in haystack
    if not openai_like:
        return False
    return any(token in haystack for token in _ALLOW_SUBSTRINGS)


def focus_azure_ai_sql_expr(
    skumeter: str = '"skumeter"',
    skuid: str = '"skuid"',
    servicename: str = '"servicename"',
    chargedescription: str = '"chargedescription"',
    skupricedetails: str = '"skupricedetails"',
) -> str:
    """Azure AI family: OpenAI, Foundry, Cognitive, ML, token PAYG (excludes generic VM/storage)."""
    haystack = _focus_haystack_sql(skumeter, skuid, servicename, chargedescription, skupricedetails)
    tokens = (
        "openai", "azure openai", "foundry", "azure ai services", "azure ai",
        "cognitive services", "cognitive", "machine learning", "azure ml",
        "document intelligence", "form recognizer", "content safety",
        "embeddings", "gpt-4", "gpt-5", "gpt-3",
    )
    match = " OR ".join(f"{haystack} LIKE '%{t}%'" for t in tokens)
    blocked = " OR ".join(
        f"{haystack} LIKE '%{t}%'" for t in ("virtual machine", "bandwidth", "load balancer")
    )
    return f"CASE WHEN ({match}) AND NOT ({blocked}) THEN 1 ELSE 0 END"


def focus_ia_ondemand_sql_expr(
    skumeter: str = '"skumeter"',
    skuid: str = '"skuid"',
    servicename: str = '"servicename"',
    chargedescription: str = '"chargedescription"',
    skupricedetails: str = '"skupricedetails"',
) -> str:
    """Non-PTU Azure AI: Foundry, PAYG tokens, spillover, other AI services."""
    ptu = focus_ptu_meter_sql_expr(skumeter, skuid, servicename, chargedescription, skupricedetails)
    ai = focus_azure_ai_sql_expr(skumeter, skuid, servicename, chargedescription, skupricedetails)
    return f"CASE WHEN {ai} = 1 AND {ptu} = 0 THEN 1 ELSE 0 END"


def focus_category_sql_expr(
    skumeter: str = '"skumeter"',
    skuid: str = '"skuid"',
    servicename: str = '"servicename"',
    chargedescription: str = '"chargedescription"',
    skupricedetails: str = '"skupricedetails"',
) -> str:
    """Classify FOCUS row: ptu | ia_ondemand | vm | storage | network | other_infra."""
    haystack = _focus_haystack_sql(skumeter, skuid, servicename, chargedescription, skupricedetails)
    ptu = focus_ptu_meter_sql_expr(skumeter, skuid, servicename, chargedescription, skupricedetails)
    ondemand = focus_ia_ondemand_sql_expr(skumeter, skuid, servicename, chargedescription, skupricedetails)
    vm = (
        f"{haystack} LIKE '%virtual machine%' OR {haystack} LIKE '%virtual machines%' "
        f"OR {haystack} LIKE '%vmss%' OR {haystack} LIKE '%aks%' OR {haystack} LIKE '%container service%'"
    )
    storage = (
        f"{haystack} LIKE '%storage%' OR {haystack} LIKE '%blob%' OR {haystack} LIKE '%managed disk%' "
        f"OR {haystack} LIKE '%premium ssd%' OR {haystack} LIKE '%standard ssd%' OR {haystack} LIKE '%files%'"
    )
    network = (
        f"{haystack} LIKE '%bandwidth%' OR {haystack} LIKE '%virtual network%' "
        f"OR {haystack} LIKE '%load balancer%' OR {haystack} LIKE '%application gateway%' "
        f"OR {haystack} LIKE '%nat gateway%' OR {haystack} LIKE '%public ip%' OR {haystack} LIKE '%vpn%'"
    )
    return (
        f"CASE WHEN {ptu} = 1 THEN 'ptu' "
        f"WHEN {ondemand} = 1 THEN 'ia_ondemand' "
        f"WHEN {vm} THEN 'vm' "
        f"WHEN {storage} THEN 'storage' "
        f"WHEN {network} THEN 'network' "
        f"ELSE 'other_infra' END"
    )


def _focus_haystack_sql(
    skumeter: str = '"skumeter"',
    skuid: str = '"skuid"',
    servicename: str = '"servicename"',
    chargedescription: str = '"chargedescription"',
    skupricedetails: str = '"skupricedetails"',
) -> str:
    meter_l = f"lower(COALESCE(CAST({skumeter} AS VARCHAR), ''))"
    sku_l = f"lower(COALESCE(CAST({skuid} AS VARCHAR), ''))"
    detail_l = f"lower(COALESCE(CAST({skupricedetails} AS VARCHAR), ''))"
    svc_l = f"lower(COALESCE(CAST({servicename} AS VARCHAR), ''))"
    desc_l = f"lower(COALESCE(CAST({chargedescription} AS VARCHAR), ''))"
    return f"concat({meter_l}, ' ', {sku_l}, ' ', {detail_l}, ' ', {svc_l}, ' ', {desc_l})"


def meter_deployment_type(sku_meter: str | None) -> str | None:
    text = str(sku_meter or "").lower()
    if "global" in text:
        return "global"
    if "data zone" in text or "data_zone" in text:
        return "data_zone"
    if "regional" in text or "provisioned managed unit" == text:
        return "regional"
    return None


def coverage_pct(application_tokens: float, monitor_tokens: float) -> float | None:
    if not monitor_tokens:
        return None
    return 100.0 * float(application_tokens) / float(monitor_tokens)


def coverage_band(pct: float | None) -> str:
    if pct is None:
        return "unknown"
    if 95 <= pct <= 105:
        return "healthy"
    if 80 <= pct < 95:
        return "partial"
    if pct < 80:
        return "critical"
    return "duplication"


def unused_capacity_pct(utilization_avg: float | None) -> float:
    if utilization_avg is None:
        return 0.0
    return max(0.0, 1.0 - float(utilization_avg) / 100.0)


def unused_capacity_cost(hourly_allocated_cost: float, utilization_avg: float | None) -> float:
    return float(hourly_allocated_cost) * unused_capacity_pct(utilization_avg)


def cost_per_million_tokens(allocated_cost: float, total_tokens: float) -> float | None:
    if not total_tokens:
        return None
    return float(allocated_cost) / float(total_tokens) * 1_000_000


def pricing_mode_label(purchase_mode: str | None, sku_name: str | None = None) -> str:
    """Map ARM/FOCUS purchase to the customer checklist: hourly vs reservation."""
    mode = str(purchase_mode or "").lower()
    sku = str(sku_name or "").lower().replace(" ", "")
    if mode == "reservation" or "globalprovisioned" in sku:
        return "Reserva (mensal/anual)"
    if mode == "ondemand" or "provisionedmanaged" in sku:
        return "Horário (OnDemand)"
    if mode == "payg":
        return "PAYG / spillover"
    return "Não classificado"


def observed_tpm(token_sum: float | None, hours: float) -> float | None:
    """Tokens-per-minute observed over the window (checklist Input/Output TPM)."""
    if not hours or token_sum is None:
        return None
    minutes = float(hours) * 60.0
    if minutes <= 0:
        return None
    return float(token_sum) / minutes


def attach_origin_cost_per_million(
    *,
    origin_cost: float,
    total_tokens: float,
    input_tokens: float = 0.0,
    output_tokens: float = 0.0,
) -> dict[str, Any]:
    return {
        "origin_cost": origin_cost,
        "total_tokens": total_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_per_million_tokens": cost_per_million_tokens(origin_cost, total_tokens),
        "cost_per_million_input": cost_per_million_tokens(origin_cost, input_tokens) if input_tokens else None,
        "cost_per_million_output": cost_per_million_tokens(origin_cost, output_tokens) if output_tokens else None,
    }


def split_project_cost_by_ptu_hours(
    project_cost: float,
    deployment_ptu_hours: dict[str, float],
) -> dict[str, float]:
    total = sum(max(0.0, v) for v in deployment_ptu_hours.values())
    if total <= 0:
        return {k: 0.0 for k in deployment_ptu_hours}
    return {k: project_cost * (max(0.0, v) / total) for k, v in deployment_ptu_hours.items()}


def split_deployment_cost_by_tokens(
    deployment_cost: float,
    subproject_tokens: dict[str, float],
    monitor_tokens: float,
) -> dict[str, Any]:
    attributed = sum(max(0.0, v) for v in subproject_tokens.values())
    coverage = coverage_pct(attributed, monitor_tokens)
    attributable = deployment_cost * (attributed / monitor_tokens) if monitor_tokens else 0.0
    unattributed = max(0.0, deployment_cost - attributable)
    shares = {}
    for key, tokens in subproject_tokens.items():
        share = (tokens / attributed) if attributed else 0.0
        shares[key] = {
            "tokens": tokens,
            "allocated_cost": attributable * share,
            "ptu_equivalent_estimate": None,
        }
    return {
        "coverage_pct": coverage,
        "coverage_band": coverage_band(coverage),
        "attributable_cost": attributable,
        "unattributed_cost": unattributed,
        "subprojects": shares,
    }


def scd2_apply_capacity_change(
    existing: list[dict[str, Any]],
    *,
    deployment_id: str,
    ptus: float,
    observed_at: str,
    extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Close the open SCD2 row when PTU capacity changes and append a new validity."""
    rows = [dict(r) for r in existing]
    open_rows = [r for r in rows if r.get("deployment_resource_id") == deployment_id and not r.get("valid_until")]
    current = open_rows[-1] if open_rows else None
    if current and float(current.get("provisioned_ptus") or 0) == float(ptus):
        current["last_seen_at"] = observed_at
        return rows
    if current:
        current["valid_until"] = observed_at
        current["last_seen_at"] = observed_at
    payload = {
        "deployment_resource_id": deployment_id,
        "provisioned_ptus": float(ptus),
        "valid_from": observed_at,
        "valid_until": None,
        "first_seen_at": observed_at,
        "last_seen_at": observed_at,
        **(extra or {}),
    }
    rows.append(payload)
    return rows


def p95(values: Iterable[float]) -> float | None:
    series = [float(v) for v in values]
    if not series:
        return None
    series.sort()
    idx = min(len(series) - 1, max(0, int(round(0.95 * (len(series) - 1)))))
    return series[idx]
