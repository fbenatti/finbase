from datetime import datetime, timezone
from types import SimpleNamespace

import oci

from app.services.oci_monitoring_extractor import OciMonitoringExtractor, blob_path_for

TREE = {"ocid1.tenancy.root": ["comp-a", "comp-b"], "comp-a": ["comp-a1"], "comp-b": [], "comp-a1": []}
INSTANCES = {
    "ocid1.tenancy.root": [],
    "comp-a": [("i-1", "RUNNING")],
    "comp-a1": [("i-2", "RUNNING"), ("i-gone", "TERMINATED")],
    "comp-b": [],
}


def _extractor(monkeypatch, config):
    connection = SimpleNamespace(id=7, code="oci-monitoring", config_json=config)
    extractor = OciMonitoringExtractor(connection, credentials_connection=connection)
    monkeypatch.setattr(extractor, "oci_config", lambda: {"tenancy": "ocid1.tenancy.root", "region": "sa-saopaulo-1"})

    class Identity:
        def __init__(self, *args, **kwargs):
            pass

        def list_compartments(self, parent, **kwargs):
            return [SimpleNamespace(id=child) for child in TREE[parent]]

    class Compute:
        def __init__(self, *args, **kwargs):
            pass

        def list_instances(self, compartment_id):
            return [SimpleNamespace(id=rid, lifecycle_state=state) for rid, state in INSTANCES[compartment_id]]

    monkeypatch.setattr(oci.identity, "IdentityClient", Identity)
    monkeypatch.setattr(oci.core, "ComputeClient", Compute)
    monkeypatch.setattr(oci.pagination, "list_call_get_all_results",
                        lambda call, *args, **kwargs: SimpleNamespace(data=call(*args, **kwargs)))
    return extractor


def test_tenancy_discovery_walks_every_compartment(monkeypatch):
    extractor = _extractor(monkeypatch, {"discovery": "tenancy", "resolution": "5m"})
    assert sorted(extractor.compartment_ids()) == ["comp-a", "comp-a1", "comp-b", "ocid1.tenancy.root"]
    assert sorted(extractor.resource_ids) == ["i-1", "i-2"]
    extractor._validate()  # no compartment_id needed


def test_tenancy_collection_queries_each_compartment_with_instances(monkeypatch):
    extractor = _extractor(monkeypatch, {
        "discovery": "tenancy", "resolution": "5m", "metric_names": ["CpuUtilization"], "statistics": ["mean"],
    })
    asked = []

    class Monitoring:
        def summarize_metrics_data(self, compartment_id, details):
            asked.append((compartment_id, details.query))
            return SimpleNamespace(headers={}, data=[])

    extractor._client = Monitoring()
    now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
    start, end, _, _ = extractor.windows(now)[-1]
    extractor.fetch_window(blob_path_for("5m", start, end), now=now)
    # One query per compartment that holds instances, without a resource filter.
    assert asked == [("comp-a", "CpuUtilization[5m].mean()"), ("comp-a1", "CpuUtilization[5m].mean()")]


def test_single_compartment_discovery_is_unchanged(monkeypatch):
    extractor = _extractor(monkeypatch, {"discovery": "compartment", "compartment_id": "comp-a1"})
    assert extractor.compartment_ids() == ["comp-a1"]
    assert extractor.resource_ids == ["i-2"]
