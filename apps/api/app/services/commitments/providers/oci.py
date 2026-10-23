"""OCI adapter of the commitment engine: daily billed quantity from the Silver billing.

OCI has no compute reservation: its commitment is Universal Credits, a spend
pool drawn over the term, so the engine needs the steady consumption, not an
hourly invoice. It reads the daily FOCUS billing (``dest_billing_cloud``) of
the connection and reads each SKU's billed quantity (``pricingquantity``) in
its unit of measure, as OCI bills it per day:

- ``… Hours`` / ``… Per Hour`` (OCPU, ECPU, GB of memory, load balancer,
  Mbps): unit-hours of the day — ÷ 24 is the units kept running (a 4-OCPU
  instance on all day bills 96 OCPU hours);
- ``… Months`` / ``… per Month`` (block volume GB, performance units,
  desktops): the month pro-rated to the day — × days of the month is the
  units provisioned (14 000 GB in October bill 451.61 GB months a day);
- anything else (requests, API calls, datapoints): consumption counted per day.

The day's cost is that quantity at the SKU's effective unit price in the
window (``effectivecost ÷ pricingquantity``), so prices come from the billing
and no list price is invented.

Cost basis: the effective cost is what the tenancy pays today, contract
discounts included. OCI offers in the catalog are therefore a discount over
the current billed rate, not over list price.

``compute_hourly_rows`` still keeps compute per hour when cost reports bring
it (``oci_compute_hourly``), but the engine no longer depends on it.
"""
from __future__ import annotations

from collections import defaultdict
import calendar
from datetime import datetime, timedelta
import re
from typing import Any

import pandas as pd

from ..contracts import UsageLine
from ..engine import DAY
from ..runner import CommitmentProvider, UsageSnapshot, register_commitment_provider

BILLING_TABLE = "dest_billing_cloud"
COMPUTE_SERVICE = "compute"
_HOUR = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2})")
_SAFE = re.compile(r"^[A-Za-z0-9._:\-]+$")

# Unit of measure → how a day's quantity becomes average units in use.
HOURLY_UNIT = "hour"
MONTHLY_UNIT = "month"
COUNTED_UNIT = "count"
_UNIT_TREATMENT = {
    HOURLY_UNIT: "unidade-hora por dia ÷ 24 = unidades médias em uso",
    MONTHLY_UNIT: "unidade-mês rateada por dia × dias do mês = unidades provisionadas",
    COUNTED_UNIT: "consumo contado por dia (sem unidade contínua em uso)",
}


def unit_kind(unit: str | None) -> str:
    text = str(unit or "").lower()
    if re.search(r"\bhours?\b", text):
        return HOURLY_UNIT
    if re.search(r"\bmonths?\b", text):
        return MONTHLY_UNIT
    return COUNTED_UNIT


def units_in_use(quantity: float, kind: str, day: str) -> float | None:
    """Average units in use that day for a quantity billed in the unit's measure."""
    if kind == HOURLY_UNIT:
        return quantity / 24
    if kind == MONTHLY_UNIT:
        year, month = int(day[:4]), int(day[5:7])
        return quantity * calendar.monthrange(year, month)[1]
    return None

_COLUMNS = {
    "lineItem/intervalUsageStart": "usage_start",
    "product/service": "service",
    "product/resourceId": "resource_id",
    "product/region": "region",
    "cost/productSku": "sku",
    "product/description": "sku_description",
    "cost/myCost": "billed_cost",
    "usage/billedQuantity": "billed_quantity",
    "cost/currencyCode": "currency",
}


def _hour(value: Any) -> str | None:
    match = _HOUR.match(str(value or "").strip())
    return f"{match.group(1)}T{match.group(2)}" if match else None


def compute_hourly_rows(raw: pd.DataFrame, *, connection_id: int, billing_period: str) -> pd.DataFrame:
    """Compute lines of a raw cost report summed per hour, resource and SKU."""
    from app.services.iceberg_service import _fold_case_insensitive_columns

    if raw is None or raw.empty:
        return pd.DataFrame()
    frame = _fold_case_insensitive_columns(raw)
    lower = {str(column).lower(): column for column in frame.columns}
    present = {lower[source.lower()]: target for source, target in _COLUMNS.items() if source.lower() in lower}
    if not {"usage_start", "service", "billed_cost"} <= set(present.values()):
        return pd.DataFrame()
    frame = frame[list(present)].rename(columns=present)
    for target in _COLUMNS.values():
        if target not in frame.columns:
            frame[target] = None
    frame = frame[frame["service"].astype(str).str.strip().str.lower() == COMPUTE_SERVICE].copy()
    frame["usage_hour"] = frame["usage_start"].map(_hour)
    frame = frame[frame["usage_hour"].notna()]
    if frame.empty:
        return pd.DataFrame()
    frame["billed_cost"] = pd.to_numeric(frame["billed_cost"], errors="coerce").fillna(0.0)
    frame["billed_quantity"] = pd.to_numeric(frame["billed_quantity"], errors="coerce").fillna(0.0)
    keys = ["usage_hour", "resource_id", "region", "sku", "sku_description", "currency"]
    frame[keys] = frame[keys].astype("string").fillna("")
    grouped = frame.groupby(keys, as_index=False, dropna=False)[["billed_cost", "billed_quantity"]].sum()
    grouped["source_connection_id"] = int(connection_id)
    grouped["_billing_period"] = str(billing_period)
    return grouped.replace({"": None})


def _literal(value: str) -> str:
    if not _SAFE.match(value):
        raise ValueError(f"valor inválido para filtro da Silver: {value!r}")
    return f"'{value}'"


def _billing_periods(start: datetime, end: datetime) -> list[str]:
    periods, cursor = [], start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cursor < end:
        periods.append(cursor.strftime("%Y%m"))
        cursor = (cursor + timedelta(days=32)).replace(day=1)
    return periods


def read_usage(connection: Any, start: datetime, end: datetime, *, execute=None) -> UsageSnapshot:
    """Daily billed quantity of the connection in [start, end), priced at the effective unit price."""
    from app.services import lakehouse_layers as layers

    if execute is None:
        from app.services.trino_service import execute_sync as execute
    periods = ", ".join(_literal(period) for period in _billing_periods(start, end))
    rows = execute(
        f"SELECT substr(chargeperiodstart, 1, 10) AS usage_day, regionname AS region, servicename AS service, "
        f"skuid AS sku, skumeter AS sku_meter, pricingunit AS unit, billingcurrency AS currency, "
        f"SUM(CAST(pricingquantity AS DOUBLE)) AS quantity, SUM(CAST(effectivecost AS DOUBLE)) AS cost "
        f"FROM {layers.detail_layer()}.{BILLING_TABLE} "
        f"WHERE source_connection_id = {int(connection.id)} AND _billing_period IN ({periods}) "
        f"AND chargeperiodstart >= {_literal(start.strftime('%Y-%m-%d'))} "
        f"AND chargeperiodstart < {_literal(end.strftime('%Y-%m-%d'))} "
        f"AND COALESCE(chargecategory, 'Usage') = 'Usage' "
        f"GROUP BY 1, 2, 3, 4, 5, 6, 7",
        timeout_s=180,
    )
    if not rows:
        raise LookupError(
            "Não há fatura da OCI nesta janela: processe os meses recentes desta conexão para o motor ler a quantidade diária."
        )
    return _build(connection, rows)


def _unit_prices(rows: list[dict]) -> dict[tuple, float]:
    """Effective price per unit of each SKU over the window (billed cost ÷ billed quantity)."""
    totals: dict[tuple, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for row in rows:
        quantity = float(row["quantity"] or 0.0)
        if quantity > 0:
            total = totals[(row["sku"], row["unit"])]
            total[0] += float(row["cost"] or 0.0)
            total[1] += quantity
    return {sku: cost / quantity for sku, (cost, quantity) in totals.items()}


def _build(connection: Any, rows: list[dict]) -> UsageSnapshot:
    prices = _unit_prices(rows)
    lines = []
    per_sku = defaultdict(lambda: {"quantity": 0.0, "units": 0.0, "cost_hour": 0.0})
    per_unit = defaultdict(lambda: {"skus": set(), "cost_hour": 0.0})
    for row in rows:
        price = prices.get((row["sku"], row["unit"]))
        quantity = float(row["quantity"] or 0.0)
        day = str(row["usage_day"])
        # A line billed without quantity (flat fee) keeps its cost: there is no unit to price.
        day_cost = quantity * price if price is not None else float(row["cost"] or 0.0)
        cost_hour = day_cost / 24
        lines.append(UsageLine(hour_utc=day, ondemand_cost=cost_hour, region=row["region"], family=row["sku"]))
        kind = unit_kind(row["unit"])
        item = per_sku[(row["service"], row["sku"], row["sku_meter"], row["unit"])]
        item["quantity"] += quantity
        item["units"] += units_in_use(quantity, kind, day) or 0.0
        item["cost_hour"] += cost_hour
        unit = per_unit[str(row["unit"] or "(sem unidade)")]
        unit["skus"].add(row["sku"])
        unit["cost_hour"] += cost_hour
    days = {line.hour_utc for line in lines}
    count = len(days)
    currencies = {row["currency"] for row in rows if row["currency"]}
    config = getattr(connection, "config_json", None) or {}
    top = sorted(per_sku.items(), key=lambda item: item[1]["cost_hour"], reverse=True)[:15]
    return UsageSnapshot(
        scope=str(config.get("tenancy_ocid") or config.get("tenancy") or connection.code),
        currency=next(iter(currencies)) if len(currencies) == 1 else None,
        # Every day the tenancy runs anything has billing lines, so a day without them is not loaded.
        observed_hours=days,
        lines=lines,
        grain=DAY,
        basis={
            "source": f"{BILLING_TABLE} (quantidade faturada por dia)",
            "note": "Base calculada da quantidade faturada por dia na unidade de medida de cada SKU (horas ÷ 24, "
                    "meses × dias do mês) × preço unitário efetivo da janela; não depende de fatura horária.",
            "days": count,
            "skus": len(per_sku),
            "units": [{
                "unit": unit, "treatment": _UNIT_TREATMENT[unit_kind(unit)], "skus": len(values["skus"]),
                "cost_hour": round(values["cost_hour"] / count, 4),
            } for unit, values in sorted(per_unit.items(), key=lambda item: -item[1]["cost_hour"])],
            "top_skus": [{
                "service": service, "sku": sku, "sku_meter": meter, "unit": unit,
                "treatment": _UNIT_TREATMENT[unit_kind(unit)],
                "quantity_per_day": round(values["quantity"] / count, 4),
                "units_in_use": round(values["units"] / count, 4) if unit_kind(unit) != COUNTED_UNIT else None,
                "unit_price": round(prices[(sku, unit)], 6) if (sku, unit) in prices else None,
                "cost_hour": round(values["cost_hour"] / count, 4),
            } for (service, sku, meter, unit), values in top],
        },
    )


register_commitment_provider(CommitmentProvider(provider="oci", read_usage=read_usage, connector_types=("oci",)))
