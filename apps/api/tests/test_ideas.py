"""Unit tests for Ideas state machine and metrics."""
from datetime import datetime, timezone, timedelta

from app.models.idea import IdeaStatusInterval
from app.services.idea_service import (
    compute_stage_metrics, dwell_by_status, initial_status, validate_transition,
)


def _iv(status: str, entered: datetime, exited: datetime | None = None, duration: int | None = None):
    return IdeaStatusInterval(
        id=1, idea_id=1, status=status, from_status=None,
        entered_at=entered, exited_at=exited, duration_seconds=duration,
    )


def test_bug_critico_starts_in_analise():
    assert initial_status("BUG_CRITICO") == "EM_ANALISE"


def test_feature_starts_nova():
    assert initial_status("FEATURE") == "NOVA"


def test_invalid_transition_raises():
    try:
        validate_transition("NOVA", "EM_PRODUCAO")
        assert False, "should raise"
    except ValueError:
        pass


def test_dwell_and_stage_metrics():
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    intervals = [
        _iv("NOVA", created, created + timedelta(days=2), 2 * 86400),
        _iv("EM_ANALISE", created + timedelta(days=2), created + timedelta(days=7), 5 * 86400),
        _iv("BACKLOG", created + timedelta(days=7), created + timedelta(days=12), 5 * 86400),
        _iv("EM_IMPLEMENTACAO", created + timedelta(days=12), created + timedelta(days=20), 8 * 86400),
        _iv("CONCLUIDA_ANALISE", created + timedelta(days=20), created + timedelta(days=22), 2 * 86400),
        _iv("EM_PRODUCAO", created + timedelta(days=22), None, None),
    ]
    now = created + timedelta(days=25)
    m = compute_stage_metrics(intervals, created, now)
    assert m["time_to_start_days"] == 12.0
    assert m["time_in_impl_days"] == 10.0  # 8 + 2
    assert m["time_to_prod_days"] == 22.0
    dwell = dwell_by_status(intervals, now)
    assert dwell["NOVA"] == 2.0
    assert dwell["EM_IMPLEMENTACAO"] == 8.0
