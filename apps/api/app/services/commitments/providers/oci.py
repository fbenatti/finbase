"""OCI adapter of the commitment engine: hourly compute from the cost reports.

The cost reports carry one line per hour (``lineItem/intervalUsageStart``),
but the cost Bronze is folded to days to save volume. Before that fold, the
compute lines are kept at hour grain in ``oci_compute_hourly`` (Silver), the
only source the engine reads for OCI.

Cost basis: ``cost/myCost`` is what the tenancy pays today, contract discounts
included. OCI offers in the catalog are therefore a discount over the current
billed rate, not over list price. The Usage API mode only returns daily data,
so its connections have no hourly series and stay ``INSUFFICIENT_HOURLY_DATA``.
"""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any

import pandas as pd

from ..contracts import UsageLine
from ..runner import CommitmentProvider, UsageSnapshot, register_commitment_provider

HOURLY_TABLE = "oci_compute_hourly"
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


def read_usage(connection: Any, start: datetime, end: datetime, *, execute=None) -> UsageSnapshot:
    """Hourly compute usage of the connection in [start, end)."""
    from app.services import lakehouse_layers as layers

    if (getattr(connection, "config_json", None) or {}).get("extraction_mode") == "usage_api":
        raise LookupError(
            "Esta conexão OCI lê a Usage API, que hoje é ingerida por dia. O motor precisa da fatura por hora: "
            "use os relatórios de custo (CSV) ou habilite a leitura horária da Usage API."
        )
    if execute is None:
        from app.services.trino_service import execute_sync as execute
    try:
        rows = execute(
            f"SELECT usage_hour, resource_id, region, sku, currency, SUM(CAST(billed_cost AS DOUBLE)) AS cost "
            f"FROM {layers.detail_layer()}.{HOURLY_TABLE} "
            f"WHERE source_connection_id = {int(connection.id)} "
            f"AND usage_hour >= {_literal(start.strftime('%Y-%m-%dT%H'))} "
            f"AND usage_hour < {_literal(end.strftime('%Y-%m-%dT%H'))} "
            f"GROUP BY usage_hour, resource_id, region, sku, currency",
            timeout_s=180,
        )
    except Exception as exc:
        if "does not exist" in str(exc).lower():
            raise LookupError(
                "Ainda não há fatura horária da OCI: reprocesse os meses desta conexão "
                "(relatórios de custo CSV) para preencher o compute por hora."
            ) from exc
        raise
    return _build(connection, rows)


def _build(connection: Any, rows: list[dict]) -> UsageSnapshot:
    lines = [
        UsageLine(hour_utc=str(row["usage_hour"]), ondemand_cost=float(row["cost"] or 0.0), region=row["region"],
                  family=row["sku"], resource_id=row["resource_id"])
        for row in rows
    ]
    currencies = {row["currency"] for row in rows if row["currency"]}
    config = getattr(connection, "config_json", None) or {}
    return UsageSnapshot(
        scope=str(config.get("tenancy_ocid") or config.get("tenancy") or connection.code),
        currency=next(iter(currencies)) if len(currencies) == 1 else None,
        # Compute bills every hour a tenancy runs anything, so an hour without
        # compute lines is treated as not loaded rather than as zero usage.
        observed_hours={line.hour_utc[:13] for line in lines},
        lines=lines,
    )


register_commitment_provider(CommitmentProvider(provider="oci", read_usage=read_usage, connector_types=("oci",)))
