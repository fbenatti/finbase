from types import SimpleNamespace

import pytest
from app.services import lakehouse_layers as layers

from app.services.gold_materialization import (
    _merged_dimensions,
    rule_target_table,
    normalize_code,
    preview_source,
)


def test_gold_table_name_sanitizes_hyphens():
    # Original code is preserved; the physical table swaps hyphens for underscores.
    assert rule_target_table("copilot-custo") == "dest_dm_cost_copilot_custo"
    assert rule_target_table("PTU Producao") == "dest_dm_cost_ptu_producao"
    assert normalize_code("Visão Completa!") == "vis_o_completa"


def test_merged_dimensions_uses_analytic_default():
    schema = {
        "_billing_period": "_billing_period",
        "tipo": "tipo",
        "cloud_origem": "cloud_origem",
        "hierarquia_1": "hierarquia_1",
        "servicename": "servicename",
        "realizado": "realizado",
        "resourcename": "resourcename",  # high-cardinality — not in default
    }
    dims = _merged_dimensions({"dest_billing_cloud": schema}, [])
    assert "_billing_period" in dims
    assert "servicename" in dims
    assert "hierarquia_1" in dims
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
async def test_preview_source_reads_the_detail_layer(monkeypatch):
    calls = []

    async def fake_execute(sql, **_kwargs):
        calls.append(sql)
        if sql.startswith("DESCRIBE"):
            return [{"Column": "servicename", "Type": "varchar"}, {"Column": "realizado", "Type": "double"}]
        return [{"row_count": 10, "total_value": 123.45}]

    monkeypatch.setattr("app.services.gold_materialization.execute", fake_execute)
    result = await preview_source(
        gold_table="dest_billing_cloud",
        filter_json={"conditions": [{"column": "servicename", "operator": "=", "values": ["GitHub"]}]},
    )
    assert result["row_count"] == 10
    assert result["total_value"] == 123.45
    # A fonte do cubo é o detalhe (Silver), resolvido em um único ponto.
    assert f"iceberg.{layers.detail_layer()}.dest_billing_cloud" in calls[-1]


def test_source_gold_tables_list_kept_in_order():
    """A source binds one or more Gold tables with the same structure."""
    source = SimpleNamespace(
        id=1,
        gold_table="dest_billing_cloud",
        gold_tables=["dest_billing_cloud", "dest_billing_cloud_planning"],
        filter_json={"conditions": [{"column": "ServiceName", "operator": "=", "values": ["GitHub"]}]},
        value_column="realizado",
        percent=100.0,
    )
    schema = {"servicename": "servicename", "realizado": "realizado"}
    # _source_clause validates against each table's own schema.
    from app.services.gold_materialization import _source_clause
    clause = _source_clause(schema, source)
    assert "servicename" in clause
