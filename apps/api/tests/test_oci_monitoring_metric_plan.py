from types import SimpleNamespace

from app.services.infrastructure_metrics.catalog import ALL_OCI_METRICS, OCI_RESOURCE_TYPE_BY_NAMESPACE
from app.services.oci_monitoring_extractor import DEFAULT_COLLECTION_PLAN, DEFAULT_STATISTICS, OciMonitoringExtractor


def _extractor(**config):
    return OciMonitoringExtractor(SimpleNamespace(id=7, config_json={"compartment_id": "c", "discovery": "tenancy", **config}))


def test_default_plan_covers_agent_hypervisor_health_vnic_and_volumes():
    namespaces = {namespace for namespace, _name, _stats in _extractor().collection_plan()}
    assert namespaces == set(OCI_RESOURCE_TYPE_BY_NAMESPACE)
    plan = {(namespace, name): stats for namespace, name, stats in DEFAULT_COLLECTION_PLAN}
    assert plan[("oci_vmi_resource_utilization", "CpuUtilization")] == DEFAULT_STATISTICS
    assert plan[("oci_compute_instance_health", "InstanceAccessibilityStatus")] == ("max",)


def test_legacy_metric_names_keep_their_namespace():
    plan = _extractor(namespace="oci_computeagent", metric_names=["CpuUtilization"], statistics=["mean"]).collection_plan()
    assert plan == [("oci_computeagent", "CpuUtilization", ("mean",))]


def test_explicit_metrics_use_the_default_statistics_of_each_metric():
    plan = _extractor(metrics=["oci_vcn/VnicConntrackUtilPercent"]).collection_plan()
    assert plan == [("oci_vcn", "VnicConntrackUtilPercent", ("mean", "max"))]


def test_every_collected_metric_has_one_canonical_name():
    catalog = {f"{m.namespace}/{m.name}": m.canonical_metric for m in ALL_OCI_METRICS}
    for namespace, name, _stats in DEFAULT_COLLECTION_PLAN:
        assert f"{namespace}/{name}" in catalog
    # Same native name in two namespaces must not collide.
    assert catalog["oci_computeagent/CpuUtilization"] != catalog["oci_vmi_resource_utilization/CpuUtilization"]
    assert len(set(catalog.values())) == len(catalog)
