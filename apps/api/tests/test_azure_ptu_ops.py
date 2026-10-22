"""Unit tests for Azure PTU Ops aggregation and recommendations."""

from __future__ import annotations

from app.services.azure_ptu_ops import (
    build_ops_recommendations,
    comparable_periods,
    count_high_util_hours,
    metric_field_availability,
    peak_utilization_label,
    request_weighted_avg,
    weighted_utilization,
)
from app.services.azure_ptu_service import (
    _build_load_gold_metrics_sql,
    _dedupe_metric_points,
    _utilization_values,
)


def _metric_row(**kwargs):
    base = {
        "metric_hour": "2026-08-01T10:00:00Z",
        "deployment_name": "gpt-ptu",
        "provisioned_ptus": 35,
        "request_count": 100,
    }
    base.update(kwargs)
    return base


def test_dedupe_metric_points_sums_totals_per_timestamp():
    points = [
        {"metric": "ProcessedPromptTokens", "timeStamp": "2026-08-01T10:05:00Z", "total": 10, "dimensions": {"StatusCode": "200"}},
        {"metric": "ProcessedPromptTokens", "timeStamp": "2026-08-01T10:05:00Z", "total": 15, "dimensions": {"StatusCode": "429"}},
        {"metric": "ProcessedPromptTokens", "timeStamp": "2026-08-01T10:05:00Z", "total": 15, "dimensions": {"StatusCode": "429"}},
    ]
    merged = _dedupe_metric_points(points)
    assert len(merged) == 1
    assert merged[0]["total"] == 25


def test_utilization_invalid_values_flagged_and_excluded():
    points = [
        {"average": 85.0},
        {"average": 120.0},
    ]
    valid, raw, anomaly = _utilization_values(points, "average")
    assert anomaly is True
    assert valid == [85.0]
    assert raw == [85.0, 120.0]


def test_weighted_utilization_by_ptus():
    metrics = [
        _metric_row(utilization_avg=20, provisioned_ptus=10),
        _metric_row(utilization_avg=80, provisioned_ptus=30, metric_hour="2026-08-01T11:00:00Z"),
    ]
    util = weighted_utilization(metrics)
    assert util is not None
    assert round(util, 1) == 65.0


def test_peak_utilization_uses_observed_peak_with_few_samples():
    metrics = [{"utilization_max": 70}, {"utilization_max": 92}]
    peak, label = peak_utilization_label(metrics)
    assert peak == 92
    assert label == "pico horário observado"


def test_request_weighted_latency_average():
    metrics = [
        _metric_row(time_to_response_avg_ms=100, request_count=10),
        _metric_row(time_to_response_avg_ms=300, request_count=90, metric_hour="2026-08-01T11:00:00Z"),
    ]
    avg = request_weighted_avg(metrics, "time_to_response_avg_ms")
    assert avg == 280.0


def test_metric_field_availability_coverage():
    metrics = [
        _metric_row(utilization_avg=50, input_tokens_sum=100),
        _metric_row(utilization_avg=None, input_tokens_sum=None, metric_hour="2026-08-01T11:00:00Z"),
    ]
    meta = metric_field_availability(metrics)
    assert meta["utilization_avg"]["sample_count"] == 1
    assert meta["utilization_avg"]["coverage_pct"] == 50.0
    assert meta["normalized_ttft_avg_ms"]["available"] is False


def test_count_high_util_hours():
    metrics = [
        _metric_row(utilization_max=78),
        _metric_row(utilization_max=82, metric_hour="2026-08-01T11:00:00Z"),
        _metric_row(utilization_max=96, metric_hour="2026-08-01T12:00:00Z"),
    ]
    assert count_high_util_hours(metrics, 80) == 2
    assert count_high_util_hours(metrics, 95) == 1


def test_comparable_periods_requires_similar_length():
    current = [_metric_row(metric_hour=f"2026-08-0{i}T10:00:00Z") for i in range(1, 8)]
    previous = [_metric_row(metric_hour=f"2026-07-2{i}T10:00:00Z") for i in range(0, 7)]
    assert comparable_periods(7 * 24, previous) is True
    assert comparable_periods(7 * 24, []) is False


def test_recommendation_capacity_underused_low_util_with_valid_peaks():
    metrics = [_metric_row(utilization_avg=15, utilization_max=45, metric_hour=f"2026-08-01T{i:02d}:00:00Z") for i in range(10, 18)]
    deployments = [{
        "deployment_name": "gpt-ptu",
        "utilization_avg": 15,
        "utilization_max": 45,
        "rate_limited_count": 0,
    }]
    recs = build_ops_recommendations(metrics, deployments, [], period={"start": "a", "end": "b"})
    codes = {r["code"] for r in recs}
    assert "CAPACITY_UNDERUSED" in codes


def test_recommendation_throttling_on_429():
    metrics = [_metric_row(rate_limited_count=12, request_count=100)]
    deployments = [{"deployment_name": "gpt-ptu", "rate_limited_count": 12, "utilization_avg": 60, "utilization_max": 70}]
    recs = build_ops_recommendations(metrics, deployments, [])
    assert any(r["code"] == "THROTTLING" for r in recs)


def test_recommendation_data_quality_on_invalid_util():
    metrics = [_metric_row(utilization_quality_anomaly=True, utilization_avg=85, utilization_max=110)]
    deployments = [{"deployment_name": "gpt-ptu", "utilization_quality_anomaly": True, "utilization_avg": 85, "utilization_max": 110}]
    recs = build_ops_recommendations(metrics, deployments, [])
    assert any(r["code"] == "DATA_QUALITY" for r in recs)


def test_recommendation_peak_risk():
    metrics = [_metric_row(utilization_max=97)]
    deployments = [{"deployment_name": "gpt-ptu", "utilization_max": 97, "utilization_avg": 70}]
    recs = build_ops_recommendations(metrics, deployments, [])
    assert any(r["code"] == "PEAK_RISK" for r in recs)


def test_recommendation_load_imbalance():
    imbalance = [{
        "project_key": "projeto-alfa",
        "severity": "high",
        "message": "spread 40 p.p.",
    }]
    recs = build_ops_recommendations([], [{"deployment_name": "a", "project_key": "projeto-alfa"}], imbalance)
    assert any(r["code"] == "LOAD_IMBALANCE" for r in recs)


def test_partial_gold_null_fields_do_not_break_weighted_util():
    metrics = [
        _metric_row(utilization_avg=50, provisioned_ptus=35),
        _metric_row(utilization_avg=None, provisioned_ptus=None, metric_hour="2026-08-01T11:00:00Z"),
    ]
    assert weighted_utilization(metrics) == 50.0


def test_load_gold_metrics_sql_omits_missing_optional_columns():
    legacy_columns = {
        "connection_id", "metric_hour", "account_resource_id", "deployment_name",
        "model_name", "model_version", "region", "sku_name", "provisioned_ptus",
        "project_hint", "input_tokens_sum", "output_tokens_sum", "total_tokens_sum",
        "utilization_avg", "utilization_max", "utilization_p95", "request_peak_per_minute",
        "request_count", "success_count", "client_error_count", "server_error_count",
        "rate_limited_count", "spillover_request_count", "time_to_response_avg_ms",
        "time_to_response_max_ms", "time_to_last_byte_avg_ms", "normalized_ttft_avg_ms",
        "normalized_ttft_max_ms", "normalized_tbt_avg_ms", "normalized_tbt_max_ms",
        "tokens_per_second_avg", "tokens_per_second_max",
    }
    sql = _build_load_gold_metrics_sql(
        bucket="CAST(metric_hour AS VARCHAR)",
        hour_filter="CAST(metric_hour AS VARCHAR) >= '2026-08-01T00:00:00Z'",
        conn_filter="",
        project_filter="",
        columns=legacy_columns,
    )
    assert "CAST(NULL AS DOUBLE) AS streaming_request_count" in sql
    assert "CAST(NULL AS DOUBLE) AS context_cache_match_rate_avg" in sql
    assert "SUM(TRY_CAST(streaming_request_count AS DOUBLE))" not in sql
