"""OCI adapter of the commitment engine: daily billed quantity from the Silver billing.

OCI has no compute reservation: its commitment is Universal Credits, a spend
pool drawn over the term, so the engine needs the steady consumption, not an
hourly invoice. It reads the daily FOCUS billing (``dest_billing_cloud``) of
the connection and turns each SKU's billed quantity (``pricingquantity``, e.g.
OCPU hours, GB hours) into an average per hour of the day — quantity ÷ 24 is
the units kept running — priced at the SKU's effective unit price in the
window (``effectivecost ÷ pricingquantity``). No list price is invented.

Cost basis: the effective cost is what the tenancy pays today, contract
discounts included. OCI offers in the catalog are therefore a discount over
the current billed rate, not over list price.

``compute_hourly_rows`` still keeps compute per hour when cost reports bring
it (``oci_compute_hourly``), but the engine no longer depends on it.
"""
from __future__ import annotations

from collections import defaultdict
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
    lines, per_sku = [], defaultdict(lambda: {"quantity": 0.0, "cost_hour": 0.0})
    for row in rows:
        price = prices.get((row["sku"], row["unit"]))
        quantity = float(row["quantity"] or 0.0)
        # A line billed without quantity (flat fee) keeps its cost: there is no unit to price.
        day_cost = quantity * price if price is not None else float(row["cost"] or 0.0)
        cost_hour = day_cost / 24
        lines.append(UsageLine(hour_utc=str(row["usage_day"]), ondemand_cost=cost_hour, region=row["region"],
                               family=row["sku"]))
        item = per_sku[(row["service"], row["sku"], row["sku_meter"], row["unit"])]
        item["quantity"] += quantity
        item["cost_hour"] += cost_hour
    days = {line.hour_utc for line in lines}
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
            "note": "Base calculada da quantidade faturada por dia (÷ 24 = unidades médias em uso) × preço "
                    "unitário efetivo de cada SKU na janela; não depende de fatura horária.",
            "days": len(days),
            "skus": len(per_sku),
            "top_skus": [{
                "service": service, "sku": sku, "sku_meter": meter, "unit": unit,
                "quantity_per_day": round(values["quantity"] / len(days), 4),
                # "OCPU Hours" / "GB Hours": quantity per day ÷ 24 is the units running on average.
                "units_running": round(values["quantity"] / len(days) / 24, 4) if "hour" in str(unit).lower() else None,
                "unit_price": round(prices[(sku, unit)], 6) if (sku, unit) in prices else None,
                "cost_hour": round(values["cost_hour"] / len(days), 4),
            } for (service, sku, meter, unit), values in top],
        },
    )


register_commitment_provider(CommitmentProvider(provider="oci", read_usage=read_usage, connector_types=("oci",)))
