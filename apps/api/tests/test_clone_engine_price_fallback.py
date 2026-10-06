from types import SimpleNamespace

import pandas as pd

from app.services.clone_engine import CloneEngine


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
