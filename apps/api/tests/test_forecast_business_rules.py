from datetime import date
from types import SimpleNamespace

import pandas as pd

from app.services.forecast_service import (
    _apply_forecast_business_rules,
    _build_series_context,
    _cloud_effective_cutoff,
    _replicated_month_value,
    _series_key,
    _source_exclusion_filter,
)


def _rule(**kwargs):
    defaults = {
        "name": "test-rule",
        "identification_column": "",
        "identification_values": [],
        "identification_clauses": [],
        "charge_type": "Purchase",
        "behavior": "RECURRING_MONTHLY",
        "reference_column": None,
        "reference_values": [],
        "value_column": "realizado",
        "start_period": "202001",
        "end_period": None,
        "growth_rate": 0.0,
        "replacement_value": None,
        "cancel_on_refund": False,
        "scope": {},
        "priority": 10,
        "active": True,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _marketplace_context():
    index = pd.to_datetime(["2026-06-15"])
    daily = pd.Series([8_561_121.0], index=index)
    return _build_series_context(daily, date(2026, 8, 18))


def test_last_closed_month_uses_last_nonzero_closed_month():
    ctx = _marketplace_context()
    assert ctx["last_closed_month_total"] == 8_561_121.0


def test_last_closed_month_is_zero_when_no_prior_nonzero_month():
    index = pd.date_range("2026-08-01", periods=10, freq="D")
    daily = pd.Series([100.0] * len(index), index=index)
    ctx = _build_series_context(daily, date(2026, 8, 18))
    assert ctx["last_closed_month_total"] == 0.0


def test_recurring_monthly_uses_last_nonzero_closed_month():
    index = pd.date_range("2026-07-01", periods=31, freq="D")
    daily = pd.Series([100.0] * len(index), index=index)
    ctx = _build_series_context(daily, date(2026, 7, 31))
    assert ctx["last_closed_month_total"] == 3100.0
    key_cols = ["cloud_origem", "servicename"]
    key_values = {"cloud_origem": "oci-prod", "servicename": "COMPUTE"}
    output = pd.DataFrame(
        [
            {**key_values, "forecast_month": "202608", "tipo": "forecast", "forecast_value": 999.0},
            {**key_values, "forecast_month": "202609", "tipo": "forecast", "forecast_value": 999.0},
        ]
    )
    rule = _rule(
        identification_column="servicename",
        identification_values=["COMPUTE"],
        scope={"cloud_origem": "oci-prod"},
        behavior="RECURRING_MONTHLY",
    )
    applied = _apply_forecast_business_rules(
        output,
        [rule],
        {_series_key(key_values, key_cols): ctx},
        key_cols,
        date(2026, 8, 18),
    )
    assert rule.name in applied
    assert output.loc[output["forecast_month"] == "202608", "forecast_value"].iloc[0] == 3100.0
    assert output.loc[output["forecast_month"] == "202609", "forecast_value"].iloc[0] == 3100.0


def test_cloud_effective_cutoff_uses_per_origin_anchor():
    cloud_cutoffs = {
        "azure-marketplace": date(2026, 7, 10),
        "gcp-ti": date(2026, 8, 16),
    }
    assert _cloud_effective_cutoff(
        cloud_cutoffs,
        "cloud_origem",
        {"cloud_origem": "azure-marketplace"},
        date(2026, 8, 16),
    ) == date(2026, 7, 10)
    assert _cloud_effective_cutoff(
        cloud_cutoffs,
        "cloud_origem",
        {"cloud_origem": "gcp-ti"},
        date(2026, 8, 16),
    ) == date(2026, 8, 16)


def test_july_only_marketplace_forecasts_august_when_global_cutoff_is_later():
    index = pd.to_datetime(["2026-07-10"])
    daily = pd.Series([1_417_849.0], index=index)
    ctx = _build_series_context(daily, date(2026, 7, 10))
    assert ctx["last_closed_month_total"] == 1_417_849.0
    assert ctx["current_key"] == "202607"

    key_cols = ["cloud_origem", "marketplaceproductname", "marketplacebillingmodel"]
    key_values = {
        "cloud_origem": "azure-marketplace",
        "marketplaceproductname": "Teradata Vantage (SaaS)",
        "marketplacebillingmodel": "RECURRING",
    }
    output = pd.DataFrame([
        {**key_values, "forecast_month": "202607", "tipo": "realizado", "forecast_value": 1_417_849.0},
        {**key_values, "forecast_month": "202608", "tipo": "forecast", "forecast_value": 0.0},
    ])
    rule = _rule(
        name="Marketplace RECURRING mensal",
        identification_clauses=[{"column": "marketplacebillingmodel", "values": ["RECURRING"]}],
        behavior="RECURRING_MONTHLY",
    )
    applied = _apply_forecast_business_rules(
        output,
        [rule],
        {_series_key(key_values, key_cols): ctx},
        key_cols,
        date(2026, 8, 18),
    )
    assert "Marketplace RECURRING mensal" in applied
    assert output.loc[output["forecast_month"] == "202608", "forecast_value"].iloc[0] == 1_417_849.0


def test_marketplace_oneshot_rule_zeros_forecast():
    ctx = _marketplace_context()
    key_cols = ["marketplacebillingmodel"]
    key_values = {"marketplacebillingmodel": "ONESHOT"}
    inflated_math_forecast = 128_000_000.0
    output = pd.DataFrame(
        [
            {**key_values, "forecast_month": "202608", "tipo": "forecast", "forecast_value": inflated_math_forecast},
            {**key_values, "forecast_month": "202609", "tipo": "forecast", "forecast_value": inflated_math_forecast},
        ]
    )
    rule = _rule(
        name="Marketplace ONESHOT zera forecast",
        identification_clauses=[{"column": "marketplacebillingmodel", "values": ["ONESHOT"]}],
        behavior="REPLACE_VALUE",
        replacement_value=0.0,
    )
    applied = _apply_forecast_business_rules(
        output,
        [rule],
        {_series_key(key_values, key_cols): ctx},
        key_cols,
        date(2026, 8, 18),
    )
    assert "Marketplace ONESHOT zera forecast" in applied
    assert output["forecast_value"].tolist() == [0.0, 0.0]


def test_identification_clauses_use_or_semantics():
    output = pd.DataFrame(
        [
            {"marketplacebillingmodel": "ONESHOT", "forecast_month": "202608", "tipo": "forecast", "forecast_value": 100.0},
            {"marketplacebillingmodel": "RECURRING", "forecast_month": "202608", "tipo": "forecast", "forecast_value": 200.0},
            {"marketplacebillingmodel": "OTHER", "forecast_month": "202608", "tipo": "forecast", "forecast_value": 300.0},
        ]
    )
    rule = _rule(
        name="Marketplace billing OR",
        identification_clauses=[
            {"column": "marketplacebillingmodel", "values": ["ONESHOT"]},
            {"column": "marketplacebillingmodel", "values": ["RECURRING"]},
        ],
        behavior="REPLACE_VALUE",
        replacement_value=0.0,
    )
    applied = _apply_forecast_business_rules(
        output,
        [rule],
        {},
        ["marketplacebillingmodel"],
        date(2026, 8, 18),
    )
    assert "Marketplace billing OR" in applied
    assert output["forecast_value"].tolist() == [0.0, 0.0, 300.0]


def test_usage_series_without_purchase_rule_keeps_math_forecast():
    key_cols = ["cloud_origem", "servicename"]
    key_values = {"cloud_origem": "oci-prod", "servicename": "COMPUTE"}
    index = pd.date_range("2026-07-01", periods=30, freq="D")
    daily = pd.Series([100_000.0] * len(index), index=index)
    series_context = {_series_key(key_values, key_cols): _build_series_context(daily, date(2026, 8, 18))}
    math_value = 2_500_000.0
    output = pd.DataFrame(
        [{**key_values, "forecast_month": "202608", "tipo": "forecast", "forecast_value": math_value}]
    )
    marketplace_rule = _rule(
        name="Marketplace ONESHOT zera forecast",
        identification_clauses=[{"column": "marketplacebillingmodel", "values": ["ONESHOT"]}],
        behavior="REPLACE_VALUE",
        replacement_value=0.0,
    )
    applied = _apply_forecast_business_rules(
        output,
        [marketplace_rule],
        series_context,
        key_cols,
        date(2026, 8, 18),
    )
    assert "Marketplace ONESHOT zera forecast (não aplicada: campo fora das dimensões)" in applied
    assert output["forecast_value"].iloc[0] == math_value


def test_repeat_next_month_only_first_future_month():
    current = "202608"
    assert _replicated_month_value(
        behavior="REPEAT_NEXT_MONTH",
        forecast_month="202609",
        current_month_key=current,
        last_closed_month_total=8_561_121.0,
        current_month_realized=0.0,
        growth_rate=0.0,
    ) == 8_561_121.0
    assert (
        _replicated_month_value(
            behavior="REPEAT_NEXT_MONTH",
            forecast_month="202610",
            current_month_key=current,
            last_closed_month_total=8_561_121.0,
            current_month_realized=0.0,
            growth_rate=0.0,
        )
        is None
    )


def test_recurring_monthly_keeps_restante_for_daily_usage_open_month():
    """Uso diário (não lumpy — Azure/GCP): o mês aberto mantém o restante
    (baseline × dias_restantes) em vez de zerar — estimativa = realizado
    parcial + forecast do restante."""
    current = "202608"
    assert (
        _replicated_month_value(
            behavior="RECURRING_MONTHLY",
            forecast_month=current,
            current_month_key=current,
            last_closed_month_total=5_000_000.0,
            current_month_realized=2_500_000.0,
            growth_rate=0.0,
            lumpy=False,
        )
        is None
    )
    # Futuros continuam replicando o último mês fechado.
    assert _replicated_month_value(
        behavior="RECURRING_MONTHLY",
        forecast_month="202609",
        current_month_key=current,
        last_closed_month_total=5_000_000.0,
        current_month_realized=2_500_000.0,
        growth_rate=0.0,
        lumpy=False,
    ) == 5_000_000.0


def test_recurring_monthly_zeroes_open_month_for_lumpy():
    """Série lumpy (fatura mensal já lançada): mês aberto = 0 para não
    duplicar o valor do mês fechado sobre o uso em andamento."""
    current = "202608"
    assert _replicated_month_value(
        behavior="RECURRING_MONTHLY",
        forecast_month=current,
        current_month_key=current,
        last_closed_month_total=1_417_849.0,
        current_month_realized=1_417_849.0,
        growth_rate=0.0,
        lumpy=True,
    ) == 0.0


def test_rule_without_identification_or_scope_is_not_applied_globally():
    output = pd.DataFrame(
        [
            {"cloud_origem": "oci-prod", "servicename": "COMPUTE", "forecast_month": "202608", "tipo": "forecast", "forecast_value": 100.0},
            {"cloud_origem": "gcp-ttech", "servicename": "COMPUTE", "forecast_month": "202608", "tipo": "forecast", "forecast_value": 200.0},
        ]
    )
    rule = _rule(name="Refund global", behavior="CANCEL_ON_REFUND", cancel_on_refund=True)
    applied = _apply_forecast_business_rules(
        output,
        [rule],
        {},
        ["cloud_origem", "servicename"],
        date(2026, 8, 18),
    )
    assert applied == ["Refund global (não aplicada: sem identificação/escopo)"]
    assert output["forecast_value"].tolist() == [100.0, 200.0]


def test_source_exclusion_prefix_uses_gold_field_without_forecast_dimension():
    rule = _rule(
        name="Excluir consumo horario PTU Aura Brain",
        identification_column="skumeter",
        identification_values=["Provisioned Managed"],
        behavior="SOURCE_EXCLUDE_PREFIX",
        scope={"departamento_nivel_5": "Aura Brain", "servicename": "Microsoft.CognitiveServices"},
        start_period="202512",
    )
    sql_filter, applied = _source_exclusion_filter(
        [rule],
        {"columns": [
            {"name": "skumeter"}, {"name": "departamento_nivel_5"},
            {"name": "servicename"}, {"name": "_billing_period"},
        ]},
        "_billing_period",
    )
    assert applied == [rule.name]
    assert 'CAST("skumeter" AS VARCHAR)' in sql_filter
    assert "Provisioned Managed" in sql_filter
    assert 'CAST("departamento_nivel_5" AS VARCHAR) = \'Aura Brain\'' in sql_filter
    assert 'CAST("_billing_period" AS VARCHAR) >= \'202512\'' in sql_filter


def test_source_exclusion_rule_is_not_reapplied_after_aggregation():
    output = pd.DataFrame([{"forecast_month": "202609", "tipo": "forecast", "forecast_value": 123.0}])
    rule = _rule(
        name="Excluir origem", identification_column="skumeter",
        identification_values=["Provisioned Managed"], behavior="SOURCE_EXCLUDE_PREFIX",
    )
    applied = _apply_forecast_business_rules(output, [rule], {}, [], date(2026, 8, 29))
    assert applied == []
    assert output["forecast_value"].tolist() == [123.0]


def test_source_mean_30d_rule_builds_a_source_filter_without_dimension():
    rule = _rule(
        name="GitHub overage média de 30 dias",
        identification_column="skumeter",
        identification_values=["AI Credit GAC"],
        behavior="SOURCE_MEAN_30D_PREFIX",
        scope={"servicename": "GitHub", "servicesubcategory": "GitHub Copilot"},
        start_period="202606",
    )
    sql_filter, applied = _source_exclusion_filter(
        [rule],
        {"columns": [
            {"name": "skumeter"}, {"name": "servicename"},
            {"name": "servicesubcategory"}, {"name": "_billing_period"},
        ]},
        "_billing_period",
        behaviors={"SOURCE_MEAN_30D_PREFIX"},
    )
    assert applied == [rule.name]
    assert "AI Credit GAC" in sql_filter
    assert 'CAST("servicesubcategory" AS VARCHAR) = \'GitHub Copilot\'' in sql_filter


def test_source_mean_30d_rule_is_not_reapplied_after_aggregation():
    output = pd.DataFrame([{
        "cloud_origem": "azure-ti", "forecast_month": "202609",
        "tipo": "forecast", "forecast_value": 123.0,
    }])
    rule = _rule(
        identification_column="skumeter",
        identification_values=["AI Credit GAC"],
        behavior="SOURCE_MEAN_30D_PREFIX",
    )
    applied = _apply_forecast_business_rules(output, [rule], {}, [], date(2026, 8, 30))
    assert applied == []
    assert output["forecast_value"].tolist() == [123.0]
