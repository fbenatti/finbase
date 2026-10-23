from datetime import datetime, timezone

import pytest

from app.services.infrastructure_metrics.catalog import OCI_METRICS, get_metric
from app.services.infrastructure_metrics.contracts import (
    MetricScope, QueryPlan,
)
from app.services.infrastructure_metrics.identity import dimensions_hash, series_id
from app.services.silver_models import METRIC_SERIES


def _series(**overrides):
    values = {
        "provider": "oci", "connection_id": 7, "account_id": "tenancy-a",
        "region": "sa-saopaulo-1", "resource_type": "instance",
        "resource_id": "ocid-instance-a", "metric": "cpu.utilization",
        "dimensions": {"resourceId": "ocid-instance-a"},
    }
    values.update(overrides)
    return series_id(**values)


def test_series_identity_includes_provider_connection_region_resource_and_labels():
    base = _series()
    assert base != _series(dimensions={"resourceId": "ocid-instance-a", "nicId": "nic-a"})
    assert base != _series(resource_id="ocid-instance-b")
    assert base != _series(connection_id=8)
    assert base != _series(region="us-ashburn-1")
    assert base != _series(provider="aws")
    assert base != _series(namespace="oci_blockstore", metric_scope="volume")
    assert base != _series(source_owner="other_source")
    assert base != _series(cloud_partition="oc2")
    assert base != _series(sub_account_id="compartment-b")


def test_dimension_hash_is_independent_of_mapping_insertion_order():
    assert dimensions_hash({"resourceId": "i-1", "nicId": "n-1"}) == dimensions_hash(
        {"nicId": "n-1", "resourceId": "i-1"}
    )


def test_scope_requires_explicit_small_allowlist():
    with pytest.raises(ValueError, match="cannot be empty"):
        MetricScope("oci", 7, "tenancy-a", "sa-saopaulo-1", ()).validate()
    with pytest.raises(ValueError, match="exceeds max_resources"):
        MetricScope("oci", 7, "tenancy-a", "sa-saopaulo-1", ("a", "b", "c")).validate()


def test_query_plan_requires_supported_resolution_and_aware_window():
    descriptor = get_metric("oci", "oci_computeagent", "CpuUtilization")
    scope = MetricScope("oci", 7, "tenancy-a", "sa-saopaulo-1", ("i-1",))
    plan = QueryPlan(
        descriptor, scope, datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 1, tzinfo=timezone.utc), 60, ("mean",),
    )
    plan.validate()
    with pytest.raises(ValueError, match="not supported"):
        QueryPlan(
            descriptor, scope, plan.start_utc, plan.end_utc, 120, ("mean",),
        ).validate()


def test_catalog_covers_the_eight_pilot_metrics_and_native_percentiles():
    assert len(OCI_METRICS) == 8
    assert all(metric.native_percentiles == (90, 95, 99) for metric in OCI_METRICS)
    assert {metric.canonical_unit for metric in OCI_METRICS} >= {
        "percent", "operations", "bytes",
    }
    assert all(metric.kind.value == "COUNTER" for metric in OCI_METRICS[2:])
    with pytest.raises(LookupError):
        get_metric("oci", "oci_computeagent", "UnknownMetric")


def test_silver_metric_model_preserves_resolution_percentiles_and_lineage():
    names = set(METRIC_SERIES.column_names())
    assert {
        "series_id", "dimensions_hash", "bucket_end_utc", "resolution_seconds",
        "source_sample_period_seconds", "native_p90", "native_p95", "native_p99",
        "statistic_lineage_json", "collection_run_id", "metric_day",
    } <= names
    assert METRIC_SERIES.field("sample_count").type == "double"
