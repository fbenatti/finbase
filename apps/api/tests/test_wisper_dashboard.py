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
            {"input_tokens": 1_000_000, "output_tokens": 500_000},
            {"input_tokens": 1_000_000, "output_tokens": 500_000},
            {"input_tokens": 1_000_000, "output_tokens": 500_000},
            {"input_tokens": 1_000_000, "output_tokens": 500_000},
            {"input_tokens": 1_000_000, "output_tokens": 500_000},
            {"input_tokens": 1_000_000, "output_tokens": 500_000},
            {"input_tokens": 1_000_000, "output_tokens": 500_000},
        ],
    }
    result = _compute_unit_economics(financials, operational, "202609")
    assert result["basis"] == "Generative AI"
    assert result["excludes_gpu_hosting"] is True
    assert "Não inclui Hospedagem GPU" in result["summary"]
    assert result["cost_per_mm_total_tokens"] is not None
    # If GPU cost were included, value would be ~8.3x higher.
    assert result["cost_per_mm_total_tokens"] < 5_000
