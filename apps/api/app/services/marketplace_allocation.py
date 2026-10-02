"""Proportional Cloud-cost allocation for Marketplace line items.

The Marketplace source supplies the consumption mix in USD.  The Cloud source
supplies the authoritative realized pool in BRL.  This module deliberately
does not perform currency conversion or add columns: callers provide the
already-normalized USD measure and the BRL pool, and the result is written to
the existing ``realizado_cloud``/``realizado`` fields.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable


CENT = Decimal("0.01")


def _decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def allocate_marketplace_pool(
    marketplace_usd: Iterable[Any],
    cloud_realized_brl: Any,
) -> list[Decimal]:
    """Allocate a Cloud BRL pool proportionally to Marketplace USD rows.

    Rounding is performed in cents and any remainder is assigned to the
    largest Marketplace row, guaranteeing that the line allocation closes
    exactly to the Cloud pool.
    """
    values = [_decimal(value) for value in marketplace_usd]
    total = sum(values, Decimal("0"))
    pool = _decimal(cloud_realized_brl).quantize(CENT, rounding=ROUND_HALF_UP)
    if not values or total <= 0:
        return [Decimal("0.00") for _ in values]

    allocations = [
        (pool * value / total).quantize(CENT, rounding=ROUND_HALF_UP)
        for value in values
    ]
    remainder = pool - sum(allocations, Decimal("0"))
    largest = max(range(len(values)), key=lambda index: values[index])
    allocations[largest] += remainder
    return allocations


def marketplace_allocation_rule_matches(
    rule_config: dict[str, Any] | None,
    *,
    connection_code: str,
    cloud_product_name: str,
    cloud_sku: str | None = None,
) -> bool:
    """Return whether a financial rule identifies the Cloud allocation pool."""
    config = rule_config or {}
    if str(config.get("allocation_method", "")).upper() != "MARKETPLACE_USD_SHARE":
        return False
    if str(config.get("marketplace_connection_code", "")).lower() != connection_code.lower():
        return False
    if str(config.get("cloud_product_name", "")) != cloud_product_name:
        return False
    configured_sku = config.get("cloud_sku")
    return not configured_sku or str(configured_sku) == str(cloud_sku or "")
