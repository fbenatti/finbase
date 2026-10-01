from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.contract_settlement import (
    allocate_pool,
    assign_contract_budget_ids,
    organizational_budget_commitments,
    organizational_budget_owners,
    period_within_contract,
    settle_pool,
    settle_rows,
)


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


def test_uncovered_overage_does_not_increase_realized():
    result = settle_pool("1000", "1300", "100")
    assert result.credit_used == Decimal("100.00")
    assert result.uncovered_overage == Decimal("200.00")
    assert result.realized == Decimal("1000.00")
    assert result.closing_credit == Decimal("0.00")


def test_two_months_generate_then_consume_credit_without_inflating_realized():
    january = settle_pool("1000", "900")
    february = settle_pool("1000", "1100", january.closing_credit)

    assert january.realized == Decimal("1000.00")
    assert january.credit_generated == Decimal("100.00")
    assert january.commitment - january.consumption == january.credit_generated
    assert february.opening_credit == Decimal("100.00")
    assert february.realized == Decimal("1000.00")
    assert february.gross_overage == Decimal("100.00")
    assert february.credit_used == Decimal("100.00")
    assert february.uncovered_overage == Decimal("0.00")
    assert february.closing_credit == Decimal("0.00")


def test_signed_adjustment_uses_positive_credit_and_negative_overage():
    under = settle_pool("892532.94", "1953.59")
    over = settle_pool("1000", "1300")

    assert under.credit_generated - under.gross_overage == Decimal("890579.35")
    assert over.credit_generated - over.gross_overage == Decimal("-300.00")
    assert over.realized == over.commitment


def test_credit_generated_later_offsets_carried_overage():
    january = settle_pool("1000", "1300")
    february = settle_pool("1000", "800", opening_overage=january.closing_overage)
    march = settle_pool("1000", "900", opening_overage=february.closing_overage)

    assert january.closing_overage == Decimal("300.00")
    assert february.credit_generated == Decimal("200.00")
    assert february.credit_used == Decimal("200.00")
    assert february.closing_overage == Decimal("100.00")
    assert march.credit_generated == Decimal("100.00")
    assert march.closing_overage == Decimal("0.00")


def test_overage_generated_later_uses_carried_credit():
    january = settle_pool("1000", "900")
    february = settle_pool("1000", "1300", opening_credit=january.closing_credit)

    assert january.closing_credit == Decimal("100.00")
    assert february.gross_overage == Decimal("300.00")
    assert february.credit_used == Decimal("100.00")
    assert february.closing_overage == Decimal("200.00")


def test_overage_is_recorded_as_paid_and_balance_zero_at_contract_end():
    result = settle_pool("1000", "1000", opening_overage="250", contract_end=True)

    assert result.uncovered_overage == Decimal("250.00")
    assert result.settled_overage == Decimal("250.00")
    assert result.closing_overage == Decimal("0.00")


def test_provider_realized_cloud_formulas_are_exact():
    billed = Decimal("1000")
    oci_realized_cloud = billed * Decimal("0.47")
    ibm_realized_cloud = (
        billed * Decimal("0.79036")
        + billed * Decimal("0.10") * Decimal("0.29505")
    )

    assert oci_realized_cloud == Decimal("470.00")
    assert ibm_realized_cloud == Decimal("819.86500")


def test_contract_validity_excludes_pre_contract_billing_months():
    assert not period_within_contract("202512", "202601", "202712")
    assert period_within_contract("202601", "202601", "202712")
    assert period_within_contract("202712", "202601", "202712")
    assert not period_within_contract("202801", "202601", "202712")


def test_allocation_ignores_project_budget_and_uses_consumption_pool():
    assert allocate_pool("1000", ["500", "400"]) == [Decimal("555.56"), Decimal("444.44")]


def test_allocation_closes_rounding_remainder_on_largest_row():
    allocations = allocate_pool("100", ["1", "1", "1"])
    assert sum(allocations, Decimal(0)) == Decimal("100.00")
    assert allocations == [Decimal("33.34"), Decimal("33.33"), Decimal("33.33")]


def test_zero_consumption_has_no_line_allocation():
    assert allocate_pool("1000", [0, 0]) == [Decimal("0.00"), Decimal("0.00")]


def test_zero_consumption_creates_credit_without_realized():
    result = settle_pool("1000", "0")
    assert result.realized == Decimal("0.00")
    assert result.credit_generated == Decimal("1000.00")
    assert result.closing_credit == Decimal("1000.00")


def test_negative_commitment_is_rejected():
    with pytest.raises(ValueError, match="commitment"):
        settle_pool(-1, 0)


def test_required_budget_pool_scenario_a100_b900_consumption_a500_b400():
    """The individual project limits must not participate in settlement."""
    budget_by_project = {"A": Decimal("100"), "B": Decimal("900")}
    consumption_by_project = {"A": Decimal("500"), "B": Decimal("400")}

    pool = settle_pool(
        sum(budget_by_project.values()),
        sum(consumption_by_project.values()),
    )
    allocations = allocate_pool(pool.realized, list(consumption_by_project.values()))

    assert pool.commitment == Decimal("1000.00")
    assert pool.consumption == Decimal("900.00")
    assert pool.credit_generated == Decimal("100.00")
    assert pool.gross_overage == Decimal("0.00")
    assert pool.realized == Decimal("1000.00")
    assert allocations == [Decimal("555.56"), Decimal("444.44")]
    assert sum(allocations, Decimal("0")) == pool.realized


def test_credits_are_isolated_by_budget_pool_and_cloud():
    pool_a_oci = settle_pool("1000", "900")
    pool_b_oci = settle_pool("1000", "1100", opening_credit="0")
    pool_a_ibm = settle_pool("1000", "1100", opening_credit="0")

    # The 100 credit from OCI/A is never supplied to OCI/B or IBM/A.
    assert pool_a_oci.closing_credit == Decimal("100.00")
    assert pool_b_oci.credit_used == Decimal("0.00")
    assert pool_b_oci.uncovered_overage == Decimal("100.00")
    assert pool_a_ibm.credit_used == Decimal("0.00")
    assert pool_a_ibm.uncovered_overage == Decimal("100.00")


def test_settle_rows_writes_realized_and_contractual_adjustment():
    rows = [
        {"financial_effect": "BUDGET_COMMITMENT", "financial_contract_id": 1, "cloud_origem": "ibm", "id_orcamento": "EXTERNO-IBM", "consumo_realizado": 500},
        {"financial_effect": "BUDGET_COMMITMENT", "financial_contract_id": 1, "cloud_origem": "ibm", "id_orcamento": "EXTERNO-IBM", "consumo_realizado": 400},
        {"financial_effect": "DIRECT_REALIZED", "consumo_realizado": 25, "realizado": 25},
    ]
    result = settle_rows(rows, commitment_by_pool={("1", "ibm", "EXTERNO-IBM"): Decimal("1000")})
    assert [result[0]["realizado"], result[1]["realizado"]] == [555.56, 444.44]
    assert result[0]["ajuste_contratual"] == 55.56
    assert round(sum(row["ajuste_contratual"] for row in result[:2]), 2) == 100.0
    assert result[2]["realizado"] == 25


def test_pool_budget_is_scaled_by_cloud_consumption_weight():
    rows = [
        {"financial_effect": "BUDGET_COMMITMENT", "financial_contract_id": 1, "cloud_origem": "ibm", "id_orcamento": "EXTERNO-IBM", "realizado_cloud": 600000},
        {"financial_effect": "BUDGET_COMMITMENT", "financial_contract_id": 1, "cloud_origem": "ibm", "id_orcamento": "EXTERNO-IBM", "realizado_cloud": 400000},
    ]
    result = settle_rows(rows, commitment_by_pool={("1", "ibm", "EXTERNO-IBM"): Decimal("2000000")})
    assert result[0]["realizado"] == 1200000.0
    assert result[1]["realizado"] == 800000.0


def test_commitment_comes_from_parent_budget_id_and_includes_parent_and_children():
    departments = {
        1: SimpleNamespace(id=1, parent_id=None, name="Consumo Cloud", id_orcamento=None),
        2: SimpleNamespace(id=2, parent_id=1, name="Chargeback", id_orcamento=None),
        3: SimpleNamespace(id=3, parent_id=2, name="IBM", id_orcamento="EXTERNO-IBM"),
        4: SimpleNamespace(id=4, parent_id=3, name="Projeto A", id_orcamento=None),
        5: SimpleNamespace(id=5, parent_id=3, name="Projeto B", id_orcamento=None),
        6: SimpleNamespace(id=6, parent_id=2, name="Azure", id_orcamento="EXTERNO-AZURE"),
        7: SimpleNamespace(id=7, parent_id=6, name="Projeto A", id_orcamento=None),
    }
    pool = ("1", "ibm", "EXTERNO-IBM")
    result = organizational_budget_commitments(
        [pool],
        departments,
        [
            (3, "50"),   # valor próprio do pai IBM
            (4, "100"),  # filho herda EXTERNO-IBM
            (5, "850"),  # outro filho no mesmo pool
            (7, "999"),  # mesmo nome, mas outra cloud/ID
        ],
    )

    assert result == {pool: Decimal("1000.00")}


def test_missing_budget_id_uses_single_scope_configured_on_contract():
    rows = [{
        "financial_effect": "BUDGET_COMMITMENT",
        "financial_contract_id": 1.0,
        "cloud_origem": "IBM",
        "id_orcamento": None,
    }]

    result = assign_contract_budget_ids(rows, {("1", "ibm"): {"EXTERNO-IBM"}})

    assert result[0]["id_orcamento"] == "EXTERNO-IBM"


def test_missing_budget_id_rejects_ambiguous_contract_scope():
    rows = [{
        "financial_effect": "BUDGET_COMMITMENT",
        "financial_contract_id": 1,
        "cloud_origem": "ibm",
        "id_orcamento": "",
    }]

    with pytest.raises(ValueError, match="expected one configured scope, found 2"):
        assign_contract_budget_ids(
            rows,
            {("1", "ibm"): {"EXTERNO-IBM", "OUTRO-IBM"}},
        )


def test_budget_owner_is_resolved_by_explicit_id_and_cloud_path():
    departments = {
        1: SimpleNamespace(id=1, parent_id=None, name="Consumo Cloud", id_orcamento=None),
        2: SimpleNamespace(id=2, parent_id=1, name="Chargeback", id_orcamento=None),
        3: SimpleNamespace(id=3, parent_id=2, name="IBM", id_orcamento="EXTERNO-IBM"),
        4: SimpleNamespace(id=4, parent_id=3, name="Projeto", id_orcamento=None),
        5: SimpleNamespace(id=5, parent_id=2, name="Azure", id_orcamento="EXTERNO-AZURE"),
    }

    assert organizational_budget_owners(
        departments,
        [("ibm", "EXTERNO-IBM"), ("azure", "EXTERNO-AZURE")],
    ) == {
        ("ibm", "EXTERNO-IBM"): 3,
        ("azure", "EXTERNO-AZURE"): 5,
    }
