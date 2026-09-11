from types import SimpleNamespace

import pandas as pd

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
