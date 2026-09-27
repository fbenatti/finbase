from types import SimpleNamespace

import pandas as pd
import pytest

from app.services import oci_operational_monitoring as monitoring
from app.services.oci_operational_monitoring import configured_compartments, gold_frame_from_bronze


def test_configured_compartments_accepts_list_and_csv():
    assert configured_compartments(SimpleNamespace(config_json={"generative_ai_compartment_ids": [" a ", "b"]})) == ["a", "b"]
    assert configured_compartments(SimpleNamespace(config_json={"generative_ai_compartment_ids": "a, b"})) == ["a", "b"]
    assert configured_compartments(SimpleNamespace(config_json={})) == []


def test_gold_frame_from_bronze_preserves_historical_metrics_and_latest_inventory():
    bronze = pd.DataFrame([
        {"record_type": "metric", "timestamp": "2026-06-02T00:00:00+00:00", "resource_id": "endpoint-1", "metric_name": "InputTokenCount", "metric_value": "10"},
        {"record_type": "metric", "timestamp": "2026-09-10T00:00:00+00:00", "resource_id": "endpoint-1", "metric_name": "InputTokenCount", "metric_value": "20"},
        {"record_type": "inventory", "timestamp": None, "resource_id": "endpoint-1", "metric_name": None, "collected_at": "2026-06-02T00:00:00+00:00", "resource_name": "old"},
        {"record_type": "inventory", "timestamp": None, "resource_id": "endpoint-1", "metric_name": None, "collected_at": "2026-09-10T00:00:00+00:00", "resource_name": "new"},
        {"record_type": "inventory", "timestamp": None, "resource_id": "endpoint-1", "metric_name": None, "collected_at": None, "resource_name": "legacy-without-date"},
    ])

    result = gold_frame_from_bronze(
        bronze, connection=SimpleNamespace(id=9, code="oci-prod"), period="202609"
    )

    metrics = result[result["record_type"] == "metric"]
    inventory = result[result["record_type"] == "inventory"]
    assert len(metrics) == 2
    assert set(metrics["_billing_period"]) == {"202606", "202609"}
    assert len(inventory) == 1
    assert inventory.iloc[0]["resource_name"] == "new"


def test_reprocess_replaces_only_the_oci_connection(monkeypatch):
    calls = {}
    bronze = pd.DataFrame([{
        "record_type": "metric", "timestamp": "2026-09-27T10:00:00+00:00",
        "resource_id": "endpoint-1", "metric_name": "InputTokenCount",
        "metric_value": "100", "collected_at": "2026-09-27T10:00:00+00:00",
    }])
    monkeypatch.setattr(monitoring.iceberg, "read_bronze", lambda source: bronze)
    monkeypatch.setattr(
        monitoring.iceberg, "write_destination_staged",
        lambda *args, **kwargs: calls.setdefault("staged", (args, kwargs)) and "staging_table",
    )

    def replace(*args, **kwargs):
        calls["replace"] = (args, kwargs)
        return kwargs["known_row_count"]

    monkeypatch.setattr(monitoring.iceberg, "replace_staged_connection", replace)
    monkeypatch.setattr(
        "app.services.gold_governance.record_frame_publication_blocking",
        lambda **kwargs: calls.setdefault("manifest", kwargs),
    )
    result = monitoring.reprocess_silver_from_bronze(SimpleNamespace(id=9, code="oci-prod"))

    assert result["silver_rows"] == 1
    assert result["token_rows"] == 1
    assert calls["staged"][1]["namespace"] == "silver"
    assert calls["replace"][1]["connection_id"] == 9
    assert calls["manifest"]["namespace"] == "silver"


def test_reprocess_preserves_silver_if_bronze_is_empty(monkeypatch):
    monkeypatch.setattr(monitoring.iceberg, "read_bronze", lambda source: pd.DataFrame())
    with pytest.raises(RuntimeError, match="Silver preservada"):
        monitoring.reprocess_silver_from_bronze(SimpleNamespace(id=9, code="oci-prod"))


def test_connection_swap_deletes_only_its_own_rows(monkeypatch):
    captured = {}

    def merge(*args, **kwargs):
        captured.update(kwargs)
        return 3

    monkeypatch.setattr(monitoring.iceberg, "_merge_staged_into_canonical_table", merge)
    result = monitoring.iceberg.replace_staged_connection(
        monitoring.TABLE, "staging_table", connection_id=9, known_row_count=3
    )

    assert result == 3
    assert captured["namespace"] == "silver"
    assert "source_connection_id" in str(captured["delete_filter"])
    assert "9" in str(captured["delete_filter"])
