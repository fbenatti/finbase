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
