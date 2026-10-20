import pandas as pd
import pytest
from pyiceberg.catalog.sql import SqlCatalog

from app.services import iceberg_service as iceberg


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    cat = SqlCatalog("anomaly-test", uri="sqlite:///:memory:", warehouse=str(tmp_path))
    cat.create_namespace("silver")
    monkeypatch.setattr(iceberg, "_get_catalog", lambda: cat)
    monkeypatch.setattr(iceberg, "_ensure_namespace", lambda namespace: None)
    return cat


def test_publication_replaces_rows_and_empty_result_clears_previous(catalog):
    types = {"run_id": "bigint", "observed": "double", "forecast_cloud": "double"}
    first = pd.DataFrame([{"run_id": 1, "observed": 123.5, "forecast_cloud": None}])
    a = iceberg.publish_dataframe_atomic("silver", "anomaly_signal", first, column_types=types)
    second = pd.DataFrame([{"run_id": 2, "observed": 456.0, "forecast_cloud": None}])
    b = iceberg.publish_dataframe_atomic("silver", "anomaly_signal", second, column_types=types)
    table = catalog.load_table(("silver", "anomaly_signal"))
    assert table.scan().to_arrow().to_pylist() == second.to_dict("records")
    assert a["snapshot_id"] != b["snapshot_id"]
    assert table.schema().find_field("forecast_cloud").field_type.__class__.__name__ == "DoubleType"
    iceberg.publish_dataframe_atomic("silver", "anomaly_signal", second.iloc[:0], column_types=types)
    assert catalog.load_table(("silver", "anomaly_signal")).scan().to_arrow().num_rows == 0


def test_invalid_result_preserves_previous_snapshot(catalog):
    original = pd.DataFrame([{"run_id": 1, "observed": 123.5}])
    a = iceberg.publish_dataframe_atomic("silver", "anomaly_signal", original)
    with pytest.raises(Exception):
        iceberg.publish_dataframe_atomic("silver", "anomaly_signal", pd.DataFrame([{"wrong_column": "bad"}]))
    table = catalog.load_table(("silver", "anomaly_signal"))
    assert table.current_snapshot().snapshot_id == a["snapshot_id"]
    assert table.scan().to_arrow().to_pylist() == original.to_dict("records")
