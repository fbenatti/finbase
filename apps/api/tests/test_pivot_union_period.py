"""Union pivot must align Gold `_billing_period` with planning `period`."""

from app.routers.analysis import (
    PivotFilter,
    PivotMeasure,
    PivotUnionRequest,
    _apply_pivot_axis_limits,
    _cell_filters_for_side,
    _destination_id_for_origin,
    _filters_for_side,
    _measure_column_for_side,
    _origin_from_cell_filters,
    _period_on_axes,
    _schema_cache_key,
    _schema_period_column,
)


def test_schema_period_column_prefers_billing_period():
    assert _schema_period_column({"_billing_period": "varchar", "tipo": "varchar"}) == "_billing_period"
    assert _schema_period_column({"period": "varchar"}) == "period"


def test_period_on_axes_detects_billing_column():
    assert _period_on_axes(["organograma"], ["_billing_period"])
    assert _period_on_axes(["period"], [])
    assert not _period_on_axes(["organograma"], ["cloud_origem"])


def test_filters_for_side_remap_billing_period_to_planning_period():
    filters = [PivotFilter(column="_billing_period", values=["202608"], operator="IN")]
    applied = _filters_for_side(filters, {"period": "varchar", "tipo": "varchar", "realizado": "double"})
    assert len(applied) == 1
    assert applied[0].column == "period"
    assert applied[0].values == ["202608"]


def test_filters_for_side_keep_gold_billing_period():
    filters = [PivotFilter(column="_billing_period", values=["202607"], operator="IN")]
    applied = _filters_for_side(filters, {"_billing_period": "varchar", "EffectiveCost": "double"})
    assert applied[0].column == "_billing_period"


def test_filters_for_side_skip_gold_only_dimensions_on_planning():
    filters = [PivotFilter(column="ServiceName", values=["VPC"], operator="IN")]
    applied = _filters_for_side(filters, {"period": "varchar", "tipo": "varchar"})
    assert applied == []


def test_measure_falls_back_to_planning_realizado():
    assert _measure_column_for_side({"realizado": "double", "period": "varchar"}, "EffectiveCost") == "realizado"
    assert _measure_column_for_side({"EffectiveCost": "double"}, "EffectiveCost") == "EffectiveCost"
    assert _measure_column_for_side({"value": "double"}, "BilledCost") == "value"


def test_chronological_axis_keys_sorts_yyyymm_ascending():
    from app.routers.analysis import _chronological_axis_keys
    keys = [("202608",), ("202504",), ("202701",), ("202601",)]
    assert _chronological_axis_keys(keys, ["_billing_period"]) == [
        ("202504",), ("202601",), ("202608",), ("202701",),
    ]
    from app.routers.analysis import _dimension_order_by
    assert _dimension_order_by(2, 1) == " ORDER BY d0, d1, d2"
    inner = 'SELECT "origem" AS d0, "_billing_period" AS d1, SUM(1) AS m0 FROM t GROUP BY 1, 2'
    sql = _apply_pivot_axis_limits(inner, ["origem"], ["_billing_period"], 1, 500, 60, " ORDER BY d0, d1")
    assert "row_keys" in sql
    assert "period_keys" in sql
    assert "JOIN period_keys ON agg.d1 IS NOT DISTINCT FROM period_keys.d1" in sql
    assert "LIMIT 30000" in sql.split("period_keys")[-1] or sql.strip().endswith("LIMIT 30000")


def test_union_request_does_not_put_origem_on_axes():
    """origem is optional: the client must request it as a row or column."""
    req = PivotUnionRequest(
        destination_table_ids=[1, -1],
        rows=["organograma"],
        columns=["_billing_period"],
        measures=[PivotMeasure(column="EffectiveCost", aggregation="SUM")],
    )
    assert "origem" not in req.rows
    assert "origem" not in req.columns


def test_origin_is_stripped_from_cell_filters():
    remaining, origin = _origin_from_cell_filters({
        "origem": "realizado",
        "organograma": "TI",
        "_billing_period": "202607",
    })
    assert origin == "realizado"
    assert remaining == {"organograma": "TI", "_billing_period": "202607"}


def test_origin_maps_to_planning_destination_id():
    assert _destination_id_for_origin(3, "forecast") == -3
    assert _destination_id_for_origin(-3, "realizado") == 3
    assert _destination_id_for_origin(3, None) == 3


def test_cell_filters_remap_period_and_drop_unknown_columns():
    mapped = _cell_filters_for_side(
        {"_billing_period": "202607", "ServiceName": "VPC"},
        {"period": "varchar", "tipo": "varchar"},
    )
    assert mapped == {"period": "202607"}


def test_schema_cache_key_is_namespace_qualified():
    assert _schema_cache_key("gold", "dest_modelo_focus") == "gold.dest_modelo_focus"
    assert _schema_cache_key("gold", "dest_dm_cost") == "gold.dest_dm_cost"


def test_axis_limit_keeps_large_negative_refunds_in_unfiltered_pivot():
    inner = 'SELECT "organograma_nivel_6" AS d0, SUM("realizado") AS m0 FROM t GROUP BY 1'

    sql = _apply_pivot_axis_limits(inner, ["organograma_nivel_6"], [], 1, 500, 60, " ORDER BY d0")

    assert "ORDER BY SUM(ABS(COALESCE(m0, 0))) DESC" in sql
