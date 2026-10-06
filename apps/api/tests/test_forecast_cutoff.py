from datetime import date

from app.services.forecast_service import _realized_cutoff


def test_realized_cutoff_keeps_stale_observed_data_instead_of_double_subtracting_d2():
    assert _realized_cutoff(
        date(2026, 10, 1), as_of=date(2026, 10, 6), excluded_days=2,
    ) == date(2026, 10, 1)


def test_realized_cutoff_applies_d2_when_source_is_current():
    assert _realized_cutoff(
        date(2026, 10, 6), as_of=date(2026, 10, 6), excluded_days=2,
    ) == date(2026, 10, 4)


def test_realized_cutoff_never_includes_future_observations():
    assert _realized_cutoff(
        date(2026, 10, 9), as_of=date(2026, 10, 6), excluded_days=2,
    ) == date(2026, 10, 4)
