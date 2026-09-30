"""Deterministic financial-contract settlement.

The calculator is deliberately independent from SQLAlchemy, pandas and the
provider names.  It settles one contract/cloud/budget-id pool at a time and
returns auditable values that can be persisted by the orchestration layer.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable


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


def organizational_budget_commitments(
    pool_keys: Iterable[tuple[str, str, str]],
    departments: dict[int, Any],
    entries: Iterable[tuple[int | None, Decimal | int | float | str]],
) -> dict[tuple[str, str, str], Decimal]:
    """Resolve monthly commitments from the organizational budget ledger.

    Budget imports are additive ledger entries and normally do not repeat the
    budget ID/cloud on ``planning_version``.  Those dimensions belong to the
    organization: a child inherits the closest ``id_orcamento`` from its
    ancestors and the cloud is the third node of the canonical hierarchy
    (Consumo Cloud > Chargeback/Showback > provider).

    The commitment of one pool is therefore the sum of every monthly planning
    entry whose department resolves to the same cloud + inherited budget ID.
    This deliberately includes the parent's own entry and all matching child
    entries; individual project limits never become independent pools.
    """
    keys = list(pool_keys)
    requested_scopes = {(cloud.strip().lower(), budget_id.strip()) for _, cloud, budget_id in keys}
    totals = {scope: Decimal("0") for scope in requested_scopes}
    scope_cache: dict[int, tuple[str, str] | None] = {}

    def department_scope(department_id: int) -> tuple[str, str] | None:
        if department_id in scope_cache:
            return scope_cache[department_id]
        chain: list[Any] = []
        current = departments.get(department_id)
        seen: set[int] = set()
        while current is not None and int(current.id) not in seen:
            seen.add(int(current.id))
            chain.append(current)
            current = departments.get(current.parent_id) if current.parent_id else None
        chain.reverse()
        budget_id = next(
            (str(item.id_orcamento).strip() for item in reversed(chain) if getattr(item, "id_orcamento", None)),
            "",
        )
        cloud = str(chain[2].name).strip().lower() if len(chain) >= 3 else ""
        resolved = (cloud, budget_id) if cloud and budget_id else None
        scope_cache[department_id] = resolved
        return resolved

    for department_id, value in entries:
        if department_id is None:
            continue
        scope = department_scope(int(department_id))
        if scope in totals:
            totals[scope] += Decimal(str(value or 0))

    return {
        key: money(totals.get((key[1].strip().lower(), key[2].strip()), Decimal("0")))
        for key in keys
    }


def settle_rows(rows: list[dict], *, commitment_by_pool: dict[tuple[str, str, str], Decimal], opening_credit_by_pool: dict[tuple[str, str, str], Decimal] | None = None) -> list[dict]:
    """Apply one settlement result to already transformed rows.

    This pure helper is the seam used by Silver/Gold orchestration: rows are
    grouped by contract, cloud, budget ID and period, then the pool result is
    allocated back without provider-specific branches.
    """
    opening_credit_by_pool = opening_credit_by_pool or {}
    groups: dict[tuple[str, str, str], list[int]] = {}
    for index, row in enumerate(rows):
        if str(row.get("financial_effect") or "").upper() != "BUDGET_COMMITMENT":
            continue
        key = (str(row.get("financial_contract_id")), str(row.get("cloud_origem") or "").lower(), str(row.get("id_orcamento") or ""))
        groups.setdefault(key, []).append(index)
    output = [dict(row) for row in rows]
    for key, indexes in groups.items():
        commitment = commitment_by_pool.get(key, Decimal("0"))
        consumption = [money(output[i].get("realizado_cloud", output[i].get("consumo_realizado"))) for i in indexes]
        result = settle_pool(commitment, sum(consumption, Decimal("0")), opening_credit_by_pool.get(key, Decimal("0")))
        allocations = allocate_pool(result.realized, consumption)
        for index, value in zip(indexes, allocations):
            output[index]["realizado"] = float(value)
            cloud_value = money(output[index].get("realizado_cloud", output[index].get("consumo_realizado")))
            output[index]["consumo_realizado"] = float(cloud_value)
            output[index]["ajuste_contratual"] = float(value - cloud_value)
    return output
