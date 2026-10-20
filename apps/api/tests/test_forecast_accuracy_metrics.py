import pytest

from app.routers.forecast import _accuracy_metrics


@pytest.mark.parametrize("forecast,actual,variance,pct,wape", [
    (100, 120, 20, .2, 1 / 6),
    (-100, -80, 20, .2, .25),
    (-100, -120, -20, -.2, 1 / 6),
    (-100, 100, 200, 2, 2),
])
def test_signed_costs_keep_delta_direction_and_nonnegative_error(forecast, actual, variance, pct, wape):
    metrics = _accuracy_metrics(forecast, actual)
    assert metrics["variance"] == variance
    assert metrics["variance_pct"] == pytest.approx(pct)
    assert metrics["wape"] == pytest.approx(wape)
    assert metrics["accuracy"] == pytest.approx(1 - wape)
    assert metrics["bias"] == pytest.approx(-variance / abs(actual))


def test_zero_bases_remain_undefined_instead_of_dividing_by_zero():
    assert _accuracy_metrics(0, -100)["variance_pct"] is None
    metrics = _accuracy_metrics(-100, 0)
    assert metrics["wape"] is None
    assert metrics["accuracy"] is None
    assert metrics["bias"] is None
