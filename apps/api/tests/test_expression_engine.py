import pandas as pd
import pytest

from app.services.expression_engine import build_scope_mask, evaluate_conditional, evaluate_expression


def test_conditional_not_empty_applies_then_expression():
    df = pd.DataFrame({
        "MarketplaceProductName": ["Teradata", None, "", "nan"],
        "BilledCost": [100.0, 200.0, 300.0, 400.0],
        "realizado": [None, None, None, None],
    })
    evaluate_conditional(df, [{
        "field": "MarketplaceProductName",
        "op": "not_empty",
        "logic": "AND",
        "then_field": "realizado",
        "then_value": "BilledCost * 1.1383",
    }])
    assert df.loc[0, "realizado"] == pytest.approx(113.83)
    assert pd.isna(df.loc[1, "realizado"])
    assert pd.isna(df.loc[2, "realizado"])
    assert pd.isna(df.loc[3, "realizado"])


def test_build_scope_mask_filters_rows():
    df = pd.DataFrame({
        "cloud_origem": ["azure-dev", "gcp-producao", "azure-dev"],
        "BilledCost": [10.0, 20.0, 30.0],
    })
    mask = build_scope_mask(df, {"field": "cloud_origem", "op": "contains", "value": "azure"})
    assert mask.tolist() == [True, False, True]


def test_evaluate_expression_respects_scope_mask():
    df = pd.DataFrame({
        "cloud_origem": ["azure-dev", "gcp-producao"],
        "BilledCost": [100.0, 200.0],
        "realizado": [None, None],
    })
    scope_mask = build_scope_mask(df, {"field": "cloud_origem", "op": "contains", "value": "azure"})
    evaluate_expression(df, "BilledCost * 2", "realizado", scope_mask=scope_mask)
    assert df.loc[0, "realizado"] == pytest.approx(200.0)
    assert pd.isna(df.loc[1, "realizado"])


def test_conditional_empty_skips_rows():
    df = pd.DataFrame({
        "MarketplaceProductName": [None, "SaaS"],
        "BilledCost": [50.0, 150.0],
        "realizado": [None, None],
    })
    evaluate_conditional(df, [{
        "field": "MarketplaceProductName",
        "op": "empty",
        "logic": "AND",
        "then_field": "realizado",
        "then_value": "99",
    }])
    assert df.loc[0, "realizado"] == 99.0
    assert pd.isna(df.loc[1, "realizado"])


def test_gcp_credit_realizado_rule_priority_order():
    """Credit lines first (BilledCost + CreditAmount), then default BilledCost."""
    df = pd.DataFrame({
        "Credits": [None, "CREDIT", "CREDIT"],
        "BilledCost": [100.0, 10.0, 1.5],
        "CreditAmount": [None, -4.0, -0.5],
        "realizado": [None, None, None],
    })
    credit_scope = build_scope_mask(df, {"field": "Credits", "op": "=", "value": "CREDIT"})
    evaluate_expression(df, "BilledCost + CreditAmount", "realizado", scope_mask=credit_scope)
    evaluate_expression(df, "BilledCost", "realizado")
    assert df.loc[0, "realizado"] == pytest.approx(100.0)
    assert df.loc[1, "realizado"] == pytest.approx(6.0)
    assert df.loc[2, "realizado"] == pytest.approx(1.0)
    assert df["realizado"].sum() == pytest.approx(107.0)


def test_azure_marketplace_rule_wins_before_generic_azure_tax():
    """A Marketplace row must retain 13.83%, not be overwritten by 18.61%."""
    df = pd.DataFrame({
        "MarketplaceProductName": ["Teradata", None],
        "BilledCost": [100.0, 100.0],
        "realizado": [None, None],
    })
    evaluate_conditional(df, [{
        "field": "MarketplaceProductName",
        "op": "not_empty",
        "then_field": "realizado",
        "then_value": "BilledCost * 1.1383",
    }])
    evaluate_expression(df, "BilledCost * 1.186065", "realizado")

    assert df.loc[0, "realizado"] == pytest.approx(113.83)
    assert df.loc[1, "realizado"] == pytest.approx(118.6065)
