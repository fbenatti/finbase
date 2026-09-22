from app.routers.forecast import _daily_gold_actual_context
from app.services import lakehouse_layers as layers


def test_daily_drilldown_uses_original_detail_date_not_monthly_mart():
    source, date_expr, measure, type_column, type_filter = _daily_gold_actual_context({
        "destination_physical_name": "dest_modelo_focus",
        "date_column": "chargeperiodstart",
        "period_column": "_billing_period",
        "measure_column": "realizado",
        "type_column": "tipo",
    })

    assert source == f"iceberg.{layers.detail_layer()}.dest_modelo_focus"
    assert '"chargeperiodstart"' in date_expr
    assert measure == "realizado"
    assert type_column == "tipo"
    assert '"tipo"' in type_filter
