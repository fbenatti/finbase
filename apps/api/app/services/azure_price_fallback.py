"""Resolve only the Azure reference prices missing from observed and curated data."""
from __future__ import annotations

import re
from typing import Any, Callable

from app.services.price_units import price_unit_factor


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip().casefold()


def load_azure_observed_prices(
    execute: Callable[..., list[dict[str, Any]]],
    *,
    connection_code: str,
    billing_period: str,
    blob_paths: list[str],
) -> dict[tuple[str, ...], float]:
    """Read month-wide On-Demand references from the selected immutable input.

    Usage can precede its only On-Demand observation by several batches or
    blobs. Loading distinct keys first makes valuation independent of that
    order, without loading the entire billing export into memory. Conflicting
    observations are left to the existing batch resolver, rather than guessed.
    """
    if not re.fullmatch(r"azure-[a-zA-Z0-9_-]+", connection_code):
        raise ValueError(f"Conexão Azure inválida para referência: {connection_code}")
    if not blob_paths:
        return {}
    raw_table = "raw_" + connection_code.replace("-", "_")
    period_sql = str(billing_period).replace("'", "''")
    paths_sql = ",".join("'" + path.replace("'", "''") + "'" for path in sorted(set(blob_paths)))
    rows = execute(f"""
        SELECT DISTINCT meterid, billingaccountid, pricingcurrency, unitofmeasure,
               productid, meterregion, try_cast(unitprice AS DOUBLE) AS unit_price
        FROM iceberg.bronze.{raw_table}
        WHERE _billing_period = '{period_sql}'
          AND _blob_path IN ({paths_sql})
          AND lower(trim(chargetype)) = 'usage'
          AND lower(trim(pricingmodel)) = 'ondemand'
          AND try_cast(unitprice AS DOUBLE) > 0
    """, timeout_s=90)
    candidates: dict[tuple[str, ...], set[float]] = {}
    for row in rows:
        meter = _text(row.get("meterid"))
        if not meter:
            continue
        key = (connection_code.casefold(), meter, *(
            _text(row.get(field)) for field in (
                "billingaccountid", "pricingcurrency", "unitofmeasure",
                "productid", "meterregion",
            )
        ))
        candidates.setdefault(key, set()).add(float(row["unit_price"]))
    return {key: next(iter(prices)) for key, prices in candidates.items() if len(prices) == 1}


def load_azure_price_fallbacks(
    execute: Callable[..., list[dict[str, Any]]],
    *,
    connection_code: str,
    billing_period: str,
    active_snapshot_id: str | None,
    meter_batch_size: int = 250,
) -> tuple[dict[tuple[str, ...], Any], dict[tuple[str, ...], Any], dict[str, int]]:
    """Load the small reference list first, then query Price Sheet for true gaps.

    The source-period scan only returns distinct reservation/savings-plan keys
    without an observed On-Demand row. Meter IDs already covered by the curated
    list are removed before the Price Sheet is queried. Full Price Sheet data
    stays in Bronze; this helper returns only the small needed fallback set.
    """
    if not re.fullmatch(r"azure-[a-zA-Z0-9_-]+", connection_code):
        raise ValueError(f"Conexão Azure inválida para referência: {connection_code}")
    raw_table = "raw_" + connection_code.replace("-", "_")
    period_sql = str(billing_period).replace("'", "''")
    try:
        curated_rows = execute("""
            SELECT meter_id, reference_price_usd, unit_of_measure, currency
            FROM iceberg.bronze.raw_azure_price_reference
            WHERE coalesce(needs_review, false) = false
              AND try_cast(reference_price_usd AS DOUBLE) > 0
        """, timeout_s=30)
    except Exception as exc:
        message = str(exc).casefold()
        missing_cache = (
            "raw_azure_price_reference" in message
            and any(marker in message for marker in ("does not exist", "not found", "no such table"))
        )
        if not missing_cache:
            raise
        curated_rows = []
    curated: dict[tuple[str, ...], Any] = {}
    curated_by_meter: dict[str, dict[str, Any]] = {}
    for row in curated_rows:
        meter = _text(row.get("meter_id"))
        if not meter:
            continue
        item = {
            "unit_price": float(row["reference_price_usd"]),
            "unit_of_measure": row.get("unit_of_measure"),
            "currency": row.get("currency"),
        }
        curated[("azure", meter)] = item
        curated_by_meter[meter] = item

    counts = {"curated_prices": len(curated), "missing_reference_keys": 0, "price_sheet_candidates": 0}
    if not active_snapshot_id:
        return curated, {}, counts

    snapshot_sql = active_snapshot_id.replace("'", "''")
    missing = execute(f"""
        WITH observed AS (
            SELECT DISTINCT coalesce(lower(trim(meterid)), '') AS meter,
                   coalesce(lower(trim(billingaccountid)), '') AS account,
                   coalesce(lower(trim(pricingcurrency)), '') AS currency,
                   coalesce(lower(trim(unitofmeasure)), '') AS unit,
                   coalesce(lower(trim(productid)), '') AS product,
                   coalesce(lower(trim(meterregion)), '') AS region
            FROM iceberg.bronze.{raw_table}
            WHERE _billing_period = '{period_sql}'
              AND lower(trim(chargetype)) = 'usage'
              AND lower(trim(pricingmodel)) = 'ondemand'
        ), required AS (
            SELECT DISTINCT coalesce(lower(trim(meterid)), '') AS meter,
                   coalesce(lower(trim(billingaccountid)), '') AS account,
                   coalesce(lower(trim(pricingcurrency)), '') AS currency,
                   coalesce(lower(trim(unitofmeasure)), '') AS unit,
                   coalesce(lower(trim(productid)), '') AS product,
                   coalesce(lower(trim(meterregion)), '') AS region
            FROM iceberg.bronze.{raw_table}
            WHERE _billing_period = '{period_sql}'
              AND lower(trim(chargetype)) = 'usage'
              AND lower(trim(pricingmodel)) IN ('reservation', 'savingsplan')
        )
        SELECT r.meter, r.account, r.currency, r.unit, r.product, r.region
        FROM required r
        LEFT JOIN observed o
          ON r.meter=o.meter AND r.account=o.account AND r.currency=o.currency
         AND r.unit=o.unit AND r.product=o.product AND r.region=o.region
        WHERE o.meter IS NULL AND r.meter <> '' AND r.account <> ''
    """, timeout_s=90)

    # A curated price resolves a key only when its currency and unit agree.
    unresolved = []
    for row in missing:
        meter = _text(row.get("meter"))
        curated_price = curated_by_meter.get(meter)
        if curated_price:
            currency_matches = (
                not curated_price.get("currency")
                or _text(curated_price.get("currency")) == _text(row.get("currency"))
            )
            unit_matches = (
                bool(curated_price.get("unit_of_measure"))
                and price_unit_factor(curated_price["unit_of_measure"], row.get("unit")) is not None
            )
            if currency_matches and unit_matches:
                continue
        unresolved.append({
            "meter": meter, "account": _text(row.get("account")),
            "currency": _text(row.get("currency")), "unit": _text(row.get("unit")),
            "product": _text(row.get("product")), "region": _text(row.get("region")),
        })
    counts["missing_reference_keys"] = len(unresolved)
    if not unresolved:
        return curated, {}, counts

    meters = sorted({row["meter"] for row in unresolved})
    unresolved_by_meter: dict[str, list[dict[str, str]]] = {}
    for key_row in unresolved:
        unresolved_by_meter.setdefault(key_row["meter"], []).append(key_row)
    candidates: dict[tuple[str, ...], Any] = {}
    for start in range(0, len(meters), max(1, meter_batch_size)):
        batch = meters[start:start + max(1, meter_batch_size)]
        meter_sql = ",".join("'" + meter.replace("'", "''") + "'" for meter in batch)
        rows = execute(f"""
            SELECT DISTINCT meterid, billingaccountid, currency, unitofmeasure,
                   productid, meterregion, try_cast(unitprice AS DOUBLE) AS unit_price
            FROM iceberg.bronze.raw_azure_pricesheet
            WHERE snapshot_id = '{snapshot_sql}'
              AND lower(pricetype) = 'consumption'
              AND try_cast(unitprice AS DOUBLE) > 0
              AND lower(trim(meterid)) IN ({meter_sql})
        """, timeout_s=90)
        for row in rows:
            meter = _text(row.get("meterid"))
            account = _text(row.get("billingaccountid"))
            currency = _text(row.get("currency"))
            product = _text(row.get("productid"))
            region = _text(row.get("meterregion"))
            reference_unit = row.get("unitofmeasure")
            try:
                unit_price = float(row.get("unit_price"))
            except (TypeError, ValueError):
                continue
            for key_row in unresolved_by_meter.get(meter, ()):
                if account != key_row["account"] or currency != key_row["currency"]:
                    continue
                # Azure billing exports and Price Sheet can expose different
                # product identifiers for the same meter. Meter ID is the
                # lookup key; account, currency, region, and compatible UOM
                # remain the safety constraints. Ambiguous prices are rejected
                # below rather than guessed.
                if key_row["region"] and region and region != key_row["region"]:
                    continue
                if price_unit_factor(reference_unit, key_row["unit"]) is None:
                    continue
                key = (
                    connection_code.casefold(), meter, account, currency,
                    key_row["unit"], key_row["product"], key_row["region"],
                )
                candidate = {"unit_price": unit_price, "unit_of_measure": reference_unit}
                previous = candidates.get(key)
                if previous and (
                    previous["unit_price"] != unit_price
                    or _text(previous.get("unit_of_measure")) != _text(reference_unit)
                ):
                    candidates[key] = {"ambiguous": True}
                else:
                    candidates[key] = candidate
    counts["price_sheet_candidates"] = sum(not item.get("ambiguous", False) for item in candidates.values())
    return curated, candidates, counts
