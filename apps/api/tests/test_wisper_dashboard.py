"""Testes do dashboard executivo Wisper/OCI."""

from app.services.wisper_dashboard import (
    BUCKET_GPU,
    BUCKETS,
    _bucket,
    _compute_unit_economics,
    _is_gpu_shape,
    _merge_forecast_monthly,
    _next_period,
    _periods_until_year_end,
    _pick_tokens_30d,
    _tokens_from_daily_window,
    _window_from_rows,
)


def test_bucket_generative_ai():
    assert _bucket("GENERATIVE_AI", "AI UNITS", "B108085") == "Generative AI"


def test_bucket_gpu_hosting():
    assert _bucket("COMPUTE", "GPU HOURS", "B95909") == BUCKET_GPU
    assert _bucket("COMPUTE", "OCPU", "B97384") == BUCKET_GPU
    assert _bucket("COMPUTE", "MEMORY", "B97385") == BUCKET_GPU


def test_bucket_storage_and_infra():
    assert _bucket("OBJECTSTORE", "STANDARD", "X") == "Dados e armazenamento"
    assert _bucket("NETWORK", "EGRESS", "X") == "Infraestrutura"


def test_is_gpu_shape():
    assert _is_gpu_shape("VM.GPU.A10.1")
    assert not _is_gpu_shape("VM.Standard.E4.Flex")


def test_next_period_and_year_end():
    assert _next_period("202608") == "202609"
    assert _next_period("202612") == "202701"
    assert _periods_until_year_end("202609") == ["202609", "202610", "202611", "202612"]


def test_merge_forecast_monthly_adds_future_months():
    monthly = [
        {
            "period": "202607",
            "label": "jul/2026",
            "buckets": {bucket: 100.0 for bucket in BUCKETS},
            "total": 400.0,
            "is_partial": False,
            "is_forecast_only": False,
        },
        {
            "period": "202608",
            "label": "ago/2026",
            "buckets": {bucket: 50.0 for bucket in BUCKETS},
            "total": 200.0,
            "is_partial": True,
            "is_forecast_only": False,
        },
    ]
    forecast_by_period = {
        "202608": {"total": 300.0, "buckets": {"Generative AI": 120.0, BUCKET_GPU: 180.0}},
        "202609": {"total": 250.0, "buckets": {"Generative AI": 100.0, BUCKET_GPU: 150.0}},
    }
    merged = _merge_forecast_monthly(monthly, forecast_by_period, "202608")
    by_period = {item["period"]: item for item in merged}
    assert by_period["202608"]["forecast_total"] == 300.0
    assert by_period["202608"]["forecast_remaining"] == 100.0
    assert by_period["202609"]["is_forecast_only"] is True
    assert by_period["202609"]["forecast_total"] == 250.0


def test_hosting_dedicated_is_generative_ai_plus_gpu_bucket():
    buckets = {
        "Generative AI": {"cost": 100_000.0},
        BUCKET_GPU: {"cost": 200_000.0},
        "Dados e armazenamento": {"cost": 10_000.0},
        "Infraestrutura": {"cost": 5_000.0},
    }
    genai = buckets["Generative AI"]["cost"]
    gpu = buckets[BUCKET_GPU]["cost"]
    hosting_total = genai + gpu
    assert hosting_total == 300_000.0
    assert hosting_total / 315_000.0 > 0.95


def test_unit_economics_uses_only_generative_ai_cost():
    financials = {
        "reference": {"closed_period": "202608"},
        "buckets": {
            "Generative AI": {"cost": 30_000.0},
            BUCKET_GPU: {"cost": 200_000.0},
        },
    }
    operational = {
        "daily": [
            {"date": "2026-08-01", "input_tokens": 1_000_000, "output_tokens": 500_000},
            {"date": "2026-08-02", "input_tokens": 1_000_000, "output_tokens": 500_000},
            {"date": "2026-08-03", "input_tokens": 1_000_000, "output_tokens": 500_000},
            {"date": "2026-08-04", "input_tokens": 1_000_000, "output_tokens": 500_000},
            {"date": "2026-08-05", "input_tokens": 1_000_000, "output_tokens": 500_000},
            {"date": "2026-08-06", "input_tokens": 1_000_000, "output_tokens": 500_000},
            {"date": "2026-08-07", "input_tokens": 1_000_000, "output_tokens": 500_000},
        ],
    }
    result = _compute_unit_economics(financials, operational, "202609")
    assert result["basis"] == "Generative AI"
    assert result["excludes_gpu_hosting"] is True
    assert "Não inclui Hospedagem GPU" in result["summary"]
    assert result["cost_per_mm_total_tokens"] is not None
    # If GPU cost were included, value would be ~8.3x higher.
    assert result["cost_per_mm_total_tokens"] < 5_000


def test_tokens_from_daily_window_uses_calendar_days_and_skips_empty_dates():
    daily = [
        {"date": "2026-08-01", "input_tokens": 0, "output_tokens": 0, "invocations": 10},
        {"date": "2026-08-02", "input_tokens": 1_000, "output_tokens": 500, "invocations": 5},
        {"date": "2026-08-03", "input_tokens": 2_000, "output_tokens": 1_000, "invocations": 6},
    ]
    result = _tokens_from_daily_window(daily, 30)
    assert result["total_tokens"] == 4_500
    assert result["date_from"] == "2026-08-01"
    assert result["date_to"] == "2026-08-03"

    empty = _tokens_from_daily_window(
        [{"date": "2026-08-01", "input_tokens": 0, "output_tokens": 0, "invocations": 99}],
        30,
    )
    assert empty["total_tokens"] == 0
    assert empty["date_from"] is None
    assert empty["date_to"] is None


def test_tokens_from_daily_window_counts_days_with_tokens():
    daily = [
        {"date": "2026-08-01", "input_tokens": 0, "output_tokens": 0},
        {"date": "2026-08-02", "input_tokens": 1_000, "output_tokens": 0},
        {"date": "2026-08-03", "input_tokens": 0, "output_tokens": 500},
    ]
    result = _tokens_from_daily_window(daily, 30)
    assert result["total_tokens"] == 1_500
    assert result["window_days"] == 2


def test_pick_tokens_30d_prefers_daily_series_over_extra_sum():
    daily = [
        {"date": "2026-08-01", "input_tokens": 100, "output_tokens": 50},
        {"date": "2026-08-02", "input_tokens": 100, "output_tokens": 50},
    ]
    extra = {
        "input_tokens": 9_999,
        "output_tokens": 9_999,
        "total_tokens": 19_998,
        "window_days": 30,
        "date_from": "2026-07-01",
        "date_to": "2026-07-30",
    }
    picked = _pick_tokens_30d(daily, extra_sum=extra)
    assert picked["total_tokens"] == 300
    assert picked["date_from"] == "2026-08-01"


def test_pick_tokens_30d_falls_back_to_extra_sum_then_unfiltered_daily():
    extra = {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
        "window_days": 7,
        "date_from": "2026-08-01",
        "date_to": "2026-08-07",
    }
    assert _pick_tokens_30d([], extra_sum=extra)["total_tokens"] == 15

    unfiltered = [{"date": "2026-08-10", "input_tokens": 1_000, "output_tokens": 0}]
    picked = _pick_tokens_30d([], extra_sum=None, unfiltered_daily=unfiltered)
    assert picked["total_tokens"] == 1_000


def test_window_from_rows_computes_inclusive_days():
    window = _window_from_rows(
        [{"min_ts": "2026-08-01T00:00:00+00:00", "max_ts": "2026-08-03T00:00:00+00:00", "points": 12}]
    )
    assert window["days"] == 3
    assert window["points"] == 12
    assert _window_from_rows([])["min"] is None
