"""Deterministic financial-contract settlement.

The calculator is deliberately independent from SQLAlchemy, pandas and the
provider names.  It settles one contract/cloud/budget-id pool at a time and
returns auditable values that can be persisted by the orchestration layer.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable


CENT = Decimal("0.01")


def money(value: Decimal | int | float | str | None) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class SettlementResult:
    commitment: Decimal
    consumption: Decimal
    opening_credit: Decimal
    credit_generated: Decimal
    gross_overage: Decimal
    credit_used: Decimal
    uncovered_overage: Decimal
    realized: Decimal
    closing_credit: Decimal


def settle_pool(
    commitment: Decimal | int | float | str,
    consumption: Decimal | int | float | str,
    opening_credit: Decimal | int | float | str = 0,
) -> SettlementResult:
    """Settle one monthly pool using cent-accurate arithmetic."""
    budget = money(commitment)
    consumed = money(consumption)
    opening = money(opening_credit)
    if budget < 0:
        raise ValueError("commitment cannot be negative")
    if opening < 0:
        raise ValueError("opening credit cannot be negative")

    delta = budget - consumed
    if delta > 0:
        generated = delta
        overage = money(0)
        used = money(0)
        uncovered = money(0)
        # No consumption means no project-level realized amount.  The budget
        # delta remains in the contractual ledger as credit only.
        realized = money(0) if consumed == 0 else budget
        closing = opening + generated
    else:
        generated = money(0)
        overage = -delta
        used = min(opening, overage)
        uncovered = overage - used
        realized = budget + uncovered
        closing = opening - used

    return SettlementResult(
        commitment=budget,
        consumption=consumed,
        opening_credit=opening,
        credit_generated=generated,
        gross_overage=money(overage),
        credit_used=money(used),
        uncovered_overage=money(uncovered),
        realized=money(realized),
        closing_credit=money(closing),
    )


def allocate_pool(realized_pool: Decimal | int | float | str, consumptions: Iterable[Decimal | int | float | str]) -> list[Decimal]:
    """Allocate a pool over positive consumption values and close in cents.

    Negative/zero rows receive zero.  Any cent remainder is assigned to the
    largest positive row, with the first occurrence breaking ties.
    """
    values = [money(value) for value in consumptions]
    total_positive = sum((max(value, Decimal(0)) for value in values), Decimal(0))
    pool = money(realized_pool)
    if total_positive <= 0:
        return [money(0) for _ in values]

    allocations = [
        money(pool * max(value, Decimal(0)) / total_positive)
        for value in values
    ]
    remainder = pool - sum(allocations, Decimal(0))
    if remainder:
        largest = max(
            (index for index, value in enumerate(values) if value > 0),
            key=lambda index: values[index],
        )
        allocations[largest] = money(allocations[largest] + remainder)
    return allocations
