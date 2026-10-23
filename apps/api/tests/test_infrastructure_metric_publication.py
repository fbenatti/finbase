import json

import pytest

from app.services.infrastructure_metrics.publication import merge_metric_rows


def _row(**changes):
    row = {
        "_source_connection_id": 4, "_silver_owner": "oci_infrastructure_metrics",
        "cloud_provider": "oci", "cloud_partition": None,
        "provider_account_id": "tenancy-a", "sub_account_id": None,
        "region_id": "sa-saopaulo-1", "resource_id": "vm-a",
        "metric_namespace": "oci_computeagent", "source_metric_name": "CpuUtilization",
        "metric_scope": "resource", "dimensions_hash": "dims-a",
        "dimension_schema_version": 1, "aggregation_interval_seconds": 60,
        "resolution_seconds": 60, "origin_kind": "NATIVE",
        "bucket_start_utc": "2026-10-23T10:00:00",
        "value": 40.0, "value_average": 40.0, "native_p95": 67.0,
        "statistic_lineage_json": '{"mean":{"request_id":"r1"},"p95":{"request_id":"r2"}}',
        "statistics_status_json": '{"requested":["mean","p95"],"returned":["mean","p95"]}',
    }
    row.update(changes)
    return row


def test_partial_recollection_updates_mean_without_erasing_prior_p95():
    old = _row()
    incoming = _row(
        value=42.0, value_average=42.0, native_p95=None,
        statistic_lineage_json='{"mean":{"request_id":"r3"}}',
        statistics_status_json='{"requested":["mean","p95"],"returned":["mean"],"errors":[{"code":"InvalidParameter"}]}',
    )
    merged, = merge_metric_rows([old], [incoming])
    assert merged["value_average"] == 42.0
    assert merged["native_p95"] == 67.0
    lineage = json.loads(merged["statistic_lineage_json"])
    assert lineage["mean"]["request_id"] == "r3"
    assert lineage["p95"]["request_id"] == "r2"
    status = json.loads(merged["statistics_status_json"])
    assert status["returned"] == ["mean", "p95"]
    assert status["errors"] == [{"code": "InvalidParameter"}]


def test_new_rows_append_to_preserved_existing_day_and_duplicate_old_key_fails():
    old = _row()
    new = _row(resource_id="vm-b", value=1.0, value_average=1.0,
               dimensions_hash="dims-b")
    result = merge_metric_rows([old], [new])
    assert {row["resource_id"] for row in result} == {"vm-a", "vm-b"}
    with pytest.raises(ValueError, match="duplicate metric series buckets"):
        merge_metric_rows([old, old], [new])


def test_conflicting_incoming_duplicate_bucket_is_rejected():
    with pytest.raises(ValueError, match="conflicting duplicate bucket values"):
        merge_metric_rows([], [_row(value_average=10), _row(value_average=11)])


def test_non_conflicting_partial_incoming_duplicate_bucket_combines_statistics():
    mean = _row(native_p95=None)
    p95 = _row(value=None, value_average=None, native_p95=75.0)
    merged, = merge_metric_rows([], [mean, p95])
    assert merged["value_average"] == 40.0
    assert merged["native_p95"] == 75.0


def test_real_normalizer_rows_merge_without_optional_identity_keys():
    from datetime import datetime, timedelta, timezone

    from app.services.infrastructure_metrics.catalog import OCI_METRICS
    from app.services.infrastructure_metrics.contracts import (
        MetricPoint, MetricScope, QueryPlan, QueryResult,
    )
    from app.services.infrastructure_metrics.normalization import query_results_to_silver_rows

    start = datetime(2026, 10, 23, tzinfo=timezone.utc)
    scope = MetricScope("oci", 4, "synthetic-account", "synthetic-region",
                        ("synthetic-vm",), enabled=True)
    plan = QueryPlan(OCI_METRICS[0], scope, start, start + timedelta(minutes=1),
                     60, ("mean",), request_id="synthetic-request")
    result = QueryResult(plan, 200, points=(MetricPoint(
        start, {"resourceId": "synthetic-vm"}, {"mean": 10.0}),))
    rows = query_results_to_silver_rows(
        (result,), source_owner="oci_infrastructure_metrics", collection_run_id="test-run")

    assert len(rows) == 1
    assert "cloud_partition" not in rows[0]
    assert len(merge_metric_rows([], rows)) == 1


def test_initializes_empty_multicloud_metric_series_table(monkeypatch):
    from app.services import iceberg_service
    from app.services.infrastructure_metrics.publication import ensure_metric_series_table
    from app.services.silver_models import METRIC_SERIES

    created = {}
    namespaces = []

    class FakeCatalog:
        def create_table(self, table_id, *, schema, partition_spec):
            created.update(table_id=table_id, schema=schema, partition_spec=partition_spec)

    monkeypatch.setattr(iceberg_service, "table_exists", lambda _name, namespace: False)
    monkeypatch.setattr(iceberg_service, "ensure_namespace", namespaces.append)
    monkeypatch.setattr(iceberg_service, "get_catalog", lambda: FakeCatalog())

    assert ensure_metric_series_table() is True
    assert namespaces == ["silver"]
    assert created["table_id"] == ("silver", "resource_metric_series")
    assert {field.name for field in created["schema"].fields} == {field.name for field in METRIC_SERIES.fields}
    assert {field.name for field in created["partition_spec"].fields} == {"metric_day", "_source_connection_id"}
