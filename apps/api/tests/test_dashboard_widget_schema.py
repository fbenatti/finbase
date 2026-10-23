"""Dashboard widgets render saved reports with the visualization they were saved with."""

import pytest
from pydantic import ValidationError

from app.routers.analysis import DashboardWidgetCreate


def test_widget_accepts_table_and_chart_visualizations():
    for chart_type in ("table", "bar", "line", "pie", "kpi"):
        widget = DashboardWidgetCreate(dashboard_id=1, pivot_view_id=2, title="Custo por mês", chart_type=chart_type)
        assert widget.chart_type == chart_type


def test_widget_rejects_unknown_visualization():
    with pytest.raises(ValidationError):
        DashboardWidgetCreate(dashboard_id=1, title="Custo", chart_type="radar")
