from decimal import Decimal

import pytest

from app.services.contract_settlement import allocate_pool, settle_pool


def test_credit_generated_when_pool_is_under_consumed():
    result = settle_pool("1000", "900")
    assert result.realized == Decimal("1000.00")
    assert result.credit_generated == Decimal("100.00")
    assert result.closing_credit == Decimal("100.00")


def test_existing_credit_covers_overage():
    result = settle_pool("1000", "1100", "150")
    assert result.gross_overage == Decimal("100.00")
    assert result.credit_used == Decimal("100.00")
    assert result.uncovered_overage == Decimal("0.00")
    assert result.realized == Decimal("1000.00")
    assert result.closing_credit == Decimal("50.00")


def test_uncovered_overage_increases_realized():
    result = settle_pool("1000", "1300", "100")
    assert result.credit_used == Decimal("100.00")
    assert result.uncovered_overage == Decimal("200.00")
    assert result.realized == Decimal("1200.00")
    assert result.closing_credit == Decimal("0.00")


def test_allocation_ignores_project_budget_and_uses_consumption_pool():
    assert allocate_pool("1000", ["500", "400"]) == [Decimal("555.56"), Decimal("444.44")]


def test_allocation_closes_rounding_remainder_on_largest_row():
    allocations = allocate_pool("100", ["1", "1", "1"])
    assert sum(allocations, Decimal(0)) == Decimal("100.00")
    assert allocations == [Decimal("33.34"), Decimal("33.33"), Decimal("33.33")]


def test_zero_consumption_has_no_line_allocation():
    assert allocate_pool("1000", [0, 0]) == [Decimal("0.00"), Decimal("0.00")]


def test_negative_commitment_is_rejected():
    with pytest.raises(ValueError, match="commitment"):
        settle_pool(-1, 0)
