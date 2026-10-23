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


def test_tenancy_collection_queries_once_from_the_root_with_subtree(monkeypatch):
    extractor = _extractor(monkeypatch, {
        "discovery": "tenancy", "resolution": "5m", "metric_names": ["CpuUtilization"], "statistics": ["mean", "max"],
    })
    asked = []

    class Monitoring:
        def summarize_metrics_data(self, compartment_id, details, compartment_id_in_subtree=False):
            asked.append((compartment_id, compartment_id_in_subtree, details.query))
            return SimpleNamespace(headers={}, data=[])

    monkeypatch.setattr(extractor, "_new_client", lambda: Monitoring())
    now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
    start, end, _, _ = extractor.windows(now)[-1]
    progress = []
    extractor.fetch_window(blob_path_for("5m", start, end), now=now, progress=lambda done, total: progress.append((done, total)))
    # One query per metric/statistic from the tenancy root covering every
    # subcompartment (2,162 per-compartment calls became 94 on 23/10/2026).
    assert sorted(asked) == [
        (extractor.oci_config()["tenancy"], True, "CpuUtilization[5m].max()"),
        (extractor.oci_config()["tenancy"], True, "CpuUtilization[5m].mean()"),
    ]
    assert progress[-1] == (2, 2)


def test_single_compartment_discovery_is_unchanged(monkeypatch):
    extractor = _extractor(monkeypatch, {"discovery": "compartment", "compartment_id": "comp-a1"})
    assert extractor.compartment_ids() == ["comp-a1"]
    assert extractor.resource_ids == ["i-2"]


def test_asset_units_run_one_asset_after_another(monkeypatch):
    extractor = _extractor(monkeypatch, {"discovery": "tenancy", "resolution": "5m", "collection_unit": "asset"})
    now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(extractor, "windows", lambda now=None: OciMonitoringExtractor.windows(extractor, datetime(2026, 10, 23, 12, tzinfo=timezone.utc)))
    paths = [item.blob_path for item in extractor._build_scan_items({}, set())]
    assets = [path.rsplit("/", 1)[1] for path in paths]
    windows = len(extractor.windows(now))
    # i-1's windows first, then i-2's: each VM is complete before the next.
    assert assets == ["i-1"] * windows + ["i-2"] * windows
    assert all(item.billing_period in ("202609", "202610") for item in extractor._build_scan_items({}, set()))


def test_asset_unit_filters_each_namespace_by_its_own_resources(monkeypatch):
    extractor = _extractor(monkeypatch, {
        "discovery": "tenancy", "resolution": "5m", "collection_unit": "asset",
        "metrics": ["oci_computeagent/CpuUtilization", "oci_vcn/VnicConntrackUtilPercent", "oci_blockstore/VolumeThrottledIOs"],
    })
    monkeypatch.setattr(extractor, "asset_resources", lambda asset: (
        {"compute.instance": [asset], "network.vnic": ["vnic-1"], "storage.volume": ["vol-1", "boot-1"]}, []))
    asked = []

    class Monitoring:
        def summarize_metrics_data(self, compartment_id, details, compartment_id_in_subtree=False):
            asked.append((compartment_id, compartment_id_in_subtree, details.namespace, details.query))
            return SimpleNamespace(headers={}, data=[])

    monkeypatch.setattr(extractor, "_new_client", lambda: Monitoring())
    now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
    start, end, _, _ = extractor.windows(now)[-1]
    extractor.fetch_window(blob_path_for("5m", start, end, "i-1"), now=now)
    filters = {(namespace, query.split("{")[1].split("}")[0]) for _root, _subtree, namespace, query in asked}
    assert ("oci_computeagent", 'resourceId = "i-1"') in filters
    assert ("oci_vcn", 'resourceId = "vnic-1"') in filters
    assert {("oci_blockstore", 'resourceId = "vol-1"'), ("oci_blockstore", 'resourceId = "boot-1"')} <= filters
    assert {(root, subtree) for root, subtree, *_ in asked} == {("ocid1.tenancy.root", True)}
