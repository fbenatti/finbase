from types import SimpleNamespace

import pytest
from app.services import lakehouse_layers as layers

from app.services.dimon_materialization import (
    _desligue_validation_dates,
    _merged_dimensions,
    dimon_table_name,
    evaluate_daily_quality,
    evaluate_vm_lifecycle,
    is_aks_managed_resource,
    normalize_code,
    preview_source,
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
async def test_preview_source_reads_the_detail_layer(monkeypatch):
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
    # A fonte do mart é o detalhe (Silver), resolvido em um único ponto.
    assert f"iceberg.{layers.detail_layer()}.dest_modelo_focus" in calls[-1]


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


def test_desligue_validation_dates_last_14_days_of_month():
    start, end = _desligue_validation_dates("202608")
    assert start == "2026-08-18"
    assert end == "2026-08-31"


def test_evaluate_daily_quality_counts_material_days():
    rows = [
        {"day": "2026-08-20", "hours": 0.1},
        {"day": "2026-08-21", "hours": 4.5},
        {"day": "2026-08-22", "hours": 8},
        {"day": "2026-08-23", "hours": 3.9},
    ]
    quality = evaluate_daily_quality(
        rows,
        validation_start="2026-08-18",
        validation_end="2026-08-31",
        total_hours=16.5,
        baseline_hours=744,
    )
    assert quality["active_days"] == 2
    assert quality["window_hours"] == 16.5
    assert quality["window_uptime_pct"] == round(16.5 / 336, 4)
    assert quality["monthly_uptime_pct"] == round(16.5 / 744, 4)


def test_is_aks_managed_resource_detects_mc_aks():
    rid = "/subscriptions/x/resourceGroups/mc_aks_cluster/providers/Microsoft.Compute/virtualMachines/aks-default-6qvtj"
    assert is_aks_managed_resource(rid) is True
    assert is_aks_managed_resource("/subscriptions/x/resourceGroups/rg-dev/providers/Microsoft.Compute/virtualMachines/vm1") is False


def test_desligue_list_filter_sql_excludes_ephemeral_and_24x7():
    from app.services.dimon_materialization import desligue_list_filter_sql

    sql = desligue_list_filter_sql()
    assert "CICLO_CURTO" in sql
    assert "LIGADA_24x7" in sql
    assert "prismacloud-scan" in sql


def test_evaluate_vm_lifecycle_recently_born():
    born, died, lifecycle = evaluate_vm_lifecycle(
        "2026-08-20", "2026-08-31", "2026-08-02", "2026-08-31"
    )
    assert born is True
    assert died is False
    assert lifecycle == "recently_born"
