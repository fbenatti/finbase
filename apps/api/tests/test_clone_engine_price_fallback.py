from types import SimpleNamespace

import pandas as pd

from app.services.clone_engine import CloneEngine, assign_source_record_ids


def _rule():
    return SimpleNamespace(
        id=10,
        version=1,
        priority=1,
        active=True,
        valid_from=None,
        valid_until=None,
        provider="azure",
        conditions=[
            {"field": "ChargeCategory", "op": "=", "value": "Usage"},
            {"field": "CommitmentDiscountType", "op": "=", "value": "Reservation"},
        ],
        roles={
            "meter_id": "source_meter_id",
            "unit_price": "source_unit_price",
            "pricing_model": "source_pricing_model",
            "on_demand_value": "OnDemand",
            "quantity": "ConsumedQuantity",
            "exchange_rate": "source_exchange_rate",
            "billed_cost": "BilledCost",
            "effective_cost": "EffectiveCost",
        },
        original_changes={},
        clone_changes={"ChargeCategory": {"operation": "SET", "value": "Return"}},
    )


def _reservation_row():
    return {
        "ChargeCategory": "Usage",
        "CommitmentDiscountType": "Reservation",
        "source_meter_id": "meter-1",
        "source_unit_price": 0.0,
        "source_pricing_model": "Reservation",
        "ConsumedQuantity": 2.0,
        "source_exchange_rate": 1.0,
        "BilledCost": 0.0,
        "EffectiveCost": 0.0,
        "cloud_origem": "azure-ti",
        "source_record_id": "record-1",
    }


def test_usage_before_ondemand_batch_uses_month_wide_reference():
    row = _reservation_row() | {"CommitmentDiscountType": "SavingsPlan", "source_pricing_model": "SavingsPlan"}
    rule = _rule()
    rule.conditions[1]["value"] = "SavingsPlan"
    key = CloneEngine._reference_key(row, "source_meter_id")
    engine = CloneEngine([rule], 1, period_prices={"202602": {key: 8.0}})

    result, count = engine.apply(pd.DataFrame([row]), "202602")

    assert count == 1
    assert result.loc[~result.is_synthetic, "BilledCost"].iloc[0] == 16.0
    assert result.loc[result.is_synthetic, "BilledCost"].iloc[0] == -16.0
    assert result.loc[result.is_synthetic, "ondemand_reference_period"].iloc[0] == "202602"


def test_future_month_reference_does_not_value_earlier_usage():
    row = _reservation_row()
    key = CloneEngine._reference_key(row, "source_meter_id")
    engine = CloneEngine([_rule()], 1, period_prices={"202603": {key: 8.0}})

    result, count = engine.apply(pd.DataFrame([row]), "202602")

    assert count == 0
    assert result.BilledCost.iloc[0] == 0
    assert len(engine.pendencies) == 1


def test_azure_ti_uses_shared_azure_reference_when_month_has_no_ondemand_row():
    engine = CloneEngine([_rule()], 1, reference_prices={("azure", "meter-1"): 10.0})

    result, count = engine.apply(pd.DataFrame([_reservation_row()]), "202609")

    assert count == 1
    clone = result.loc[result["is_synthetic"]].iloc[0]
    assert clone["BilledCost"] == -20.0
    assert clone["ondemand_reference_price"] == 10.0
    assert clone["ondemand_reference_period"] == "reference_catalog"
    assert clone["ChargeCategory"] == "Return"


def test_observed_same_month_ondemand_price_wins_over_catalog_fallback():
    on_demand = _reservation_row() | {
        "ChargeCategory": "Usage",
        "CommitmentDiscountType": "None",
        "source_unit_price": 8.0,
        "source_pricing_model": "OnDemand",
        "BilledCost": 16.0,
        "EffectiveCost": 16.0,
    }
    engine = CloneEngine([_rule()], 1, reference_prices={("azure", "meter-1"): 10.0})

    result, count = engine.apply(
        pd.DataFrame([on_demand, _reservation_row()]), "202609"
    )

    assert count == 1
    clone = result.loc[result["is_synthetic"]].iloc[0]
    assert clone["BilledCost"] == -16.0
    assert clone["ondemand_reference_price"] == 8.0
    assert clone["ondemand_reference_period"] == "202609"


def test_source_record_ids_are_unique_across_batches_and_blobs():
    first = assign_source_record_ids(
        pd.DataFrame({"value": [1, 2]}),
        connection_id=2,
        blob_id=3982,
        row_offset=0,
    )
    second = assign_source_record_ids(
        pd.DataFrame({"value": [3, 4]}),
        connection_id=2,
        blob_id=3982,
        row_offset=2,
    )
    another_blob = assign_source_record_ids(
        pd.DataFrame({"value": [5]}),
        connection_id=2,
        blob_id=3983,
        row_offset=0,
    )

    ids = [*first.source_record_id, *second.source_record_id, *another_blob.source_record_id]
    assert len(ids) == len(set(ids)) == 5


def test_synthetic_return_keeps_the_exact_source_row_id():
    row = _reservation_row() | {"source_record_id": "2:3982:173"}
    engine = CloneEngine([_rule()], 1, reference_prices={("azure", "meter-1"): 10.0})

    result, count = engine.apply(pd.DataFrame([row]), "202609")

    assert count == 1
    original = result.loc[~result["is_synthetic"]].iloc[0]
    clone = result.loc[result["is_synthetic"]].iloc[0]
    assert clone["source_record_id"] == original["source_record_id"]
    assert clone["clone_source_record_id"] == original["source_record_id"]
    assert clone["BilledCost"] == -original["BilledCost"]
    assert clone["EffectiveCost"] == -original["EffectiveCost"]


def test_contract_price_fallback_requires_shared_account_and_matching_unit_dimensions():
    row = _reservation_row() | {
        "source_billing_account_id": "account-1",
        "source_pricing_currency": "USD",
        "source_unit_of_measure": "10K",
        "source_product_id": "product-1",
        "source_region": "region-1",
    }
    key = ("azure-ti", "meter-1", "account-1", "usd", "10k", "product-1", "region-1")
    engine = CloneEngine([_rule()], 1, reference_prices={key: 3.25})

    result, count = engine.apply(pd.DataFrame([row]), "202609")

    assert count == 1
    assert result.loc[result["is_synthetic"], "BilledCost"].iloc[0] == -6.5


def test_contract_price_fallback_does_not_match_different_account_or_unit():
    row = _reservation_row() | {
        "source_billing_account_id": "account-2",
        "source_pricing_currency": "USD",
        "source_unit_of_measure": "Hour",
        "source_product_id": "product-1",
        "source_region": "region-1",
    }
    key = ("azure-ti", "meter-1", "account-1", "usd", "10k", "product-1", "region-1")
    engine = CloneEngine([_rule()], 1, reference_prices={key: 3.25})

    result, count = engine.apply(pd.DataFrame([row]), "202609")

    assert count == 0
    assert engine.pendencies[0]["meter_id"] == "meter-1"


def test_bronze_price_sheet_candidate_must_have_equivalent_uom():
    row = _reservation_row() | {
        "source_billing_account_id": "account-1",
        "source_pricing_currency": "USD",
        "source_unit_of_measure": "10K",
        "source_product_id": "product-1",
        "source_region": "region-1",
    }
    key = ("azure-ti", "meter-1", "account-1", "usd", "10k", "product-1", "region-1")

    exact = CloneEngine([_rule()], 1, reference_prices={
        key: {"unit_price": 3.25, "unit_of_measure": "10K"},
    })
    result, count = exact.apply(pd.DataFrame([row]), "202609")
    assert count == 1
    assert result.loc[result["is_synthetic"], "BilledCost"].iloc[0] == -6.5

    incompatible = CloneEngine([_rule()], 1, reference_prices={
        key: {"unit_price": 3.25, "unit_of_measure": "Hour"},
    })
    _, count = incompatible.apply(pd.DataFrame([row]), "202609")
    assert count == 0


def test_curated_reference_list_precedes_price_sheet_fallback():
    row = _reservation_row() | {
        "source_billing_account_id": "account-1",
        "source_pricing_currency": "USD",
        "source_unit_of_measure": "10K",
        "source_product_id": "product-1",
        "source_region": "region-1",
    }
    key = ("azure-ti", "meter-1", "account-1", "usd", "10k", "product-1", "region-1")
    engine = CloneEngine(
        [_rule()], 1,
        reference_prices={("azure", "meter-1"): {
            "unit_price": 2.5, "unit_of_measure": "10K", "currency": "USD",
        }},
        price_sheet_prices={key: {"unit_price": 3.25, "unit_of_measure": "10K"}},
    )

    result, count = engine.apply(pd.DataFrame([row]), "202609")

    clone = result.loc[result["is_synthetic"]].iloc[0]
    assert count == 1
    assert clone["BilledCost"] == -5.0
    assert clone["ondemand_reference_period"] == "reference_catalog"
