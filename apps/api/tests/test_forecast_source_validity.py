from datetime import date
from types import SimpleNamespace

import pandas as pd

from app.services.forecast_service import _apply_source_validity_rules, _series_key


def _rule(active=True):
    return SimpleNamespace(
        id=1,
        name="Fonte Azure-Producao vencida",
        priority=1,
        active=active,
        behavior="SOURCE_VALIDITY",
        behavior_options={"max_age_days": 30},
        identification_column="",
        identification_values=[],
        identification_clauses=[],
        scope={"cloud_origem": "azure-producao"},
        start_period="202001",
        end_period=None,
    )


def _case(last_seen: date, *, evaluated_at: date = date(2026, 10, 2), active=True):
    key_cols = ["cloud_origem", "servicename"]
    keys = {"cloud_origem": "azure-producao", "servicename": "PTU"}
    output = pd.DataFrame([
        {**keys, "forecast_month": "202610", "tipo": "forecast", "forecast_value": 120.0, "realizado": 120.0, "realizado_cloud": 120.0},
        {**keys, "forecast_month": "202611", "tipo": "forecast", "forecast_value": 120.0, "realizado": 120.0, "realizado_cloud": 120.0},
        {**keys, "forecast_month": "202612", "tipo": "realizado", "forecast_value": 20.0, "realizado": 20.0, "realizado_cloud": 20.0},
    ])
    context = {"last_observed_date": last_seen}
    summary = _apply_source_validity_rules(
        output,
        [_rule(active)],
        {_series_key(keys, key_cols): context},
        key_cols,
        evaluated_at,
    )
    return output, summary


def test_source_validity_suspends_after_configured_age_and_only_forecast_rows():
    output, summary = _case(date(2026, 9, 1))
    assert output["forecast_value"].tolist() == [0.0, 0.0, 20.0]
    assert output["realizado"].tolist() == [0.0, 0.0, 20.0]
    assert output["realizado_cloud"].tolist() == [0.0, 0.0, 20.0]
    assert "validade 30 dias" in summary[0]
    assert "idade avaliada 31 dias" in summary[0]


def test_source_validity_keeps_data_at_boundary_and_simulation_clock():
    output, summary = _case(date(2026, 9, 2))
    assert output["forecast_value"].tolist() == [120.0, 120.0, 20.0]
    assert "válida" in summary[0]


def test_source_validity_uses_simulated_date_not_wall_clock():
    output, _ = _case(date(2026, 7, 3), evaluated_at=date(2026, 8, 2))
    assert output["forecast_value"].tolist() == [120.0, 120.0, 20.0]


def test_disabled_source_validity_rule_has_no_effect():
    output, summary = _case(date(2026, 7, 2), active=False)
    assert output["forecast_value"].tolist() == [120.0, 120.0, 20.0]
    assert summary == []
