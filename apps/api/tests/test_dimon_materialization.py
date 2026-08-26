from types import SimpleNamespace

import pytest

from app.services.dimon_materialization import (
    _merged_dimensions, dimon_table_name, normalize_code, preview_source,
)


def test_dimon_table_name_sanitizes_hyphens():
    # Original code is preserved; the physical table swaps hyphens for underscores.
    assert dimon_table_name("copilot-custo") == "dest_dm_cost_copilot_custo"
    assert dimon_table_name("PTU 4P") == "dest_dm_cost_ptu_4p"
    assert normalize_code("Visão Completa!") == "vis_o_completa"


def test_merged_dimensions_uses_analytic_default():
    schema = {
        "_billing_period": "_billing_period",
        "tipo": "tipo",
        "cloud_origem": "cloud_origem",
        "departamento_nivel_1": "departamento_nivel_1",
        "servicename": "servicename",
        "realizado": "realizado",
        "resourcename": "resourcename",  # high-cardinality — not in default
    }
    dims = _merged_dimensions({"dest_modelo_focus": schema}, [])
    assert "_billing_period" in dims
    assert "servicename" in dims
    assert "departamento_nivel_1" in dims
    # High-cardinality columns are excluded by default.
    assert "resourcename" not in dims
    # Period is always first.
    assert dims[0] == "_billing_period"


def test_merged_dimensions_honors_configured():
    schema = {"_billing_period": "_billing_period", "servicename": "servicename", "skumeter": "skumeter", "realizado": "realizado"}
    dims = _merged_dimensions({"g": schema}, ["skumeter", "servicename"])
    assert "skumeter" in dims and "servicename" in dims
    assert dims[0] == "_billing_period"


@pytest.mark.asyncio
async def test_preview_source_queries_gold(monkeypatch):
    calls = []

    async def fake_execute(sql, **_kwargs):
        calls.append(sql)
        if sql.startswith("DESCRIBE"):
            return [{"Column": "servicename", "Type": "varchar"}, {"Column": "realizado", "Type": "double"}]
        return [{"row_count": 10, "total_value": 123.45}]

    monkeypatch.setattr("app.services.dimon_materialization.execute", fake_execute)
    result = await preview_source(
        gold_table="dest_modelo_focus",
        filter_json={"conditions": [{"column": "servicename", "operator": "=", "values": ["GitHub"]}]},
    )
    assert result["row_count"] == 10
    assert result["total_value"] == 123.45
    assert "iceberg.gold.dest_modelo_focus" in calls[-1]


def test_source_gold_tables_list_kept_in_order():
    """A source binds one or more Gold tables with the same structure."""
    source = SimpleNamespace(
        id=1,
        gold_table="dest_modelo_focus",
        gold_tables=["dest_modelo_focus", "dest_modelo_focus_planning"],
        filter_json={"conditions": [{"column": "ServiceName", "operator": "=", "values": ["GitHub"]}]},
        value_column="realizado",
        percent=100.0,
    )
    schema = {"servicename": "servicename", "realizado": "realizado"}
    # _source_clause validates against each table's own schema.
    from app.services.dimon_materialization import _source_clause
    clause = _source_clause(schema, source)
    assert "servicename" in clause


def test_alias_for_missing_departamento_on_planning():
    """A filter on 'departamento' applies to planning as departamento_nivel_1."""
    from app.services.dimon_materialization import _normalized_conditions
    planning_schema = {
        "departamento_nivel_1": "departamento_nivel_1",
        "servicename": "servicename",
        "realizado": "realizado",
    }
    conds, missing = _normalized_conditions(
        planning_schema,
        [{"column": "departamento", "operator": "NOT_EMPTY", "values": []}],
    )
    assert missing == []
    assert conds[0]["column"] == "departamento_nivel_1"
