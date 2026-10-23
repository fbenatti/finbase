import random
from datetime import datetime, timedelta, timezone

from app.services.infrastructure_metrics.opportunities import (
    DEFAULT_POLICY, READY, RIGHTSIZE_DOWN, analyze_resource, summarize_series,
)
from app.services.infrastructure_metrics.peak_patterns import (
    SCHEDULE_OR_CAP_JOB, conditional_rightsizing, detect_recurring_peaks,
)
from app.services.infrastructure_metrics.providers.oci_rightsizing import OCI_FLEX_RULES, OciFlexPriceBook
from app.services.infrastructure_metrics.sizing import CapacitySnapshot, FlexSizingCatalog

# 2026-09-23 00:00 in São Paulo (UTC-3, no DST).
START = datetime(2026, 9, 23, 3, tzinfo=timezone.utc)
DAYS = 30
STEP = timedelta(minutes=5)
VM = CapacitySnapshot(
    provider="oci", resource_id="r1", resource_name="queue", sku="VM.Optimized3.Flex",
    family="VM.Optimized3.Flex", vcpus=10.0, memory_gb=32.0, attributes={"ocpu": 5.0},
)
CATALOG = FlexSizingCatalog(OCI_FLEX_RULES)
PRICES = OciFlexPriceBook(ocpu_hour=0.28, memory_gb_hour=0.0079, currency="BRL")


def _series(level=lambda local, rng: None, *, base=20.0, seed=7):
    """30 days of 5-minute buckets; ``level`` returns (avg, max) for a peak bucket or None."""
    rng = random.Random(seed)
    rows = []
    for index in range(DAYS * 288):
        bucket = START + index * STEP
        local = bucket - timedelta(hours=3)
        avg = base + rng.uniform(-3, 3)
        maximum = avg + rng.uniform(2, 8)
        peak = level(local, rng)
        if peak:
            avg, maximum = peak
        rows.append({"bucket_start_utc": bucket.strftime("%Y-%m-%dT%H:%M:%S"),
                     "value_average": avg, "value_maximum": maximum})
    return rows


def _daily_job(local, rng):
    """Cron at 06:05 and 13:05, 5 to 10 minutes long, skipped on one day."""
    if local.day == 1 and local.month == 10:
        return None
    minutes = local.hour * 60 + local.minute
    if minutes in (6 * 60 + 5, 13 * 60 + 5) or (minutes == 13 * 60 + 10 and local.day % 3 == 0):
        return 60.0, 97.0
    return None


def _detect(rows):
    return detect_recurring_peaks(rows, resolution_seconds=300, policy=DEFAULT_POLICY)


def test_daily_peaks_at_fixed_hours_are_detected_with_slots_and_share():
    result = _detect(_series(_daily_job))
    assert result["detected"]
    windows = result["windows"]
    assert [(w["start"], w["end"]) for w in windows] == [("06:05", "06:10"), ("13:05", "13:10")]
    assert all(w["weekdays"] is None for w in windows)
    assert windows[0]["days_with_peak"] == 29 and windows[0]["days_observed"] == 30
    assert windows[0]["typical_duration_minutes"] == 5.0
    assert result["label"] == "06:05–06:10 e 13:05–13:10, em 29 de 30 dias"
    # ~68 job buckets at 60% against 8.6k buckets at ~20%.
    assert 0.01 < result["cpu_time_share"] < 0.04


def test_random_peaks_are_not_a_pattern():
    # Unscheduled peaks hit some hour on most days and some weekday 4 weeks in
    # a row by chance; the binomial test against the background rejects them.
    for per_day in (2, 6, 12):
        for seed in range(10):
            def random_peaks(local, rng):
                return (60.0, 97.0) if rng.random() < per_day / 288 else None

            result = _detect(_series(random_peaks, seed=seed))
            assert not result["detected"], (per_day, seed, result.get("label"))
            assert result["peak_time_share"] > 0
            assert "não se repetem" in result["reason"]


def test_constant_load_is_not_a_pattern():
    high = _detect(_series(base=88.0))
    assert not high["detected"] and "carga contínua" in high["reason"]
    low = _detect(_series(base=20.0))
    assert not low["detected"] and low["peak_time_share"] == 0


def test_weekly_job_is_reported_with_its_weekday():
    def monday_backup(local, rng):
        if local.weekday() == 0 and local.hour == 2 and local.minute < 20:
            return 70.0, 99.0
        return None

    result = _detect(_series(monday_backup))
    assert result["detected"]
    (window,) = result["windows"]
    assert window["weekdays"] == [0] and window["start"] == "02:00" and window["end"] == "02:20"
    assert "às segunda" in result["label"]


def _analyze(rows):
    end = START + timedelta(days=DAYS)
    cpu = summarize_series(rows, window_start=START, window_end=end, resolution_seconds=300)
    memory = summarize_series(_series(base=40.0), window_start=START, window_end=end, resolution_seconds=300)
    result = analyze_resource(resource_id="r1", capacity=VM, cpu=cpu, memory=memory,
                              catalog=CATALOG, price_book=PRICES)
    insight = conditional_rightsizing(
        result, capacity=VM, cpu_rows=rows, memory=memory, catalog=CATALOG, price_book=PRICES,
        policy=DEFAULT_POLICY, window_start=START, window_end=end, resolution_seconds=300,
    )
    return result, insight


def test_recurring_peaks_unlock_a_conditional_smaller_size():
    result, insight = _analyze(_series(_daily_job))
    # The peaks keep the CPU at 10 vCPU; memory alone shrinks.
    assert result["action_type"] == RIGHTSIZE_DOWN and result["to_config"]["vcpus"] == 10.0
    conditional = insight["conditional"]
    assert conditional["action_type"] == SCHEDULE_OR_CAP_JOB
    assert conditional["readiness_status"] == READY and conditional["confidence"] == "MEDIUM"
    assert conditional["to_config"]["vcpus"] < 10.0
    assert conditional["to_config"]["memory_gb"] == result["to_config"]["memory_gb"]
    # Saving on top of the main recommendation, priced with the same book.
    expected = 730 * (5 - conditional["to_config"]["ocpu"]) * 0.28
    assert abs(conditional["incremental_monthly_saving"] - round(expected, 2)) < 0.02
    assert conditional["monthly_saving"] > result["monthly_saving"]
    assert 1 <= conditional["suggested_cap_vcpus"] < conditional["to_config"]["vcpus"]
    assert any("CPUQuota" in action for action in conditional["suggested_actions"])
    assert "condicionada" in " ".join(conditional["rationale"])


def test_no_conditional_when_the_size_does_not_depend_on_the_peaks():
    def tiny_job(local, rng):
        return (24.0, 45.0) if local.hour == 6 and local.minute == 5 else None

    _, insight = _analyze(_series(tiny_job))
    assert insight["conditional"] is None
