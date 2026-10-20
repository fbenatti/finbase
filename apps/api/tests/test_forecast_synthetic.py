import pandas as pd
import pytest

from app.services.forecast_synthetic import apply_linked_offsets


def test_central_ri_return_follows_sum_of_consumer_forecasts_and_keeps_real_credit():
    columns = ["project", "chargecategory", "is_synthetic"]
    history = pd.DataFrame([
        dict(project="A", chargecategory="Usage", is_synthetic=False, value=100),
        dict(project="B", chargecategory="Usage", is_synthetic=False, value=300),
    ])
    output = pd.DataFrame([
        dict(project="A", chargecategory="Usage", is_synthetic=False, forecast_month="202610", forecast_value=120, selected_method="MEAN_7D"),
        dict(project="B", chargecategory="Usage", is_synthetic=False, forecast_month="202610", forecast_value=200, selected_method="MEAN_28D"),
        dict(project="RI Returns", chargecategory="Return", is_synthetic=True, forecast_month="202610", forecast_value=-400, selected_method="MEAN_14D"),
        dict(project="RI Returns", chargecategory="Return", is_synthetic=False, forecast_month="202610", forecast_value=-10, selected_method="MEAN_14D"),
    ])
    links = [dict(source_0=project, source_1="Usage", source_2=False,
                  target_0="RI Returns", target_1="Return", target_2=True, offset_value=value)
             for project, value in [("A", -100), ("B", -300)]]
    assert apply_linked_offsets(output, history, links, columns) == 1
    assert output.loc[2, "forecast_value"] == -320
    assert output.loc[3, "forecast_value"] == -10
    assert output.forecast_value.sum() == -10


def test_partial_clone_keeps_uncompensated_usage_and_routes_multiple_projects():
    columns = ["project", "is_synthetic"]
    history = pd.DataFrame([dict(project="A", is_synthetic=False, value=100)])
    output = pd.DataFrame([
        dict(project="A", is_synthetic=False, forecast_month="202610", forecast_value=200, selected_method="MEAN_7D"),
        dict(project="R1", is_synthetic=True, forecast_month="202610", forecast_value=-999, selected_method="MEAN_7D"),
        dict(project="R2", is_synthetic=True, forecast_month="202610", forecast_value=-999, selected_method="MEAN_7D"),
    ])
    links = [dict(source_0="A", source_1=False, target_0=target, target_1=True, offset_value=value)
             for target, value in [("R1", -20), ("R2", -30)]]
    apply_linked_offsets(output, history, links, columns)
    assert output.forecast_value.tolist() == [200, -40, -60]


def test_excluded_source_cannot_leave_a_projected_synthetic_credit():
    columns = ["project", "is_synthetic"]
    history = pd.DataFrame([dict(project="A", is_synthetic=False, value=0)])
    output = pd.DataFrame([
        dict(project="A", is_synthetic=False, forecast_month="202610", forecast_value=0, selected_method="MEAN_7D"),
        dict(project="R", is_synthetic=True, forecast_month="202610", forecast_value=-100, selected_method="MEAN_7D"),
    ])
    apply_linked_offsets(output, history, [dict(source_0="A", source_1=False, target_0="R", target_1=True, offset_value=-100)], columns)
    assert output.forecast_value.tolist() == [0, 0]
