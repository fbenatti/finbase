import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import destination, etl_task, forecast, identity, source_connection  # noqa: F401
from app.models.anomaly import AnomalyCase, AnomalyCaseEvent, AnomalyConfig, AnomalyRun
from app.services.anomaly_v2 import runner
from app.services.anomaly_v2.baseline import expected_for_day
from app.services.anomaly_v2.detectors import continuous_episode, run_detectors_for_scope
from app.services.anomaly_v2.gates import valid_days_by_cloud
from app.services.anomaly_v2.return_engine import evaluate_return
from app.services.anomaly_v2.scoring import score_signal


def history(days=60, spike=50):
    start = date(2026, 8, 20)
    return [{"scope_id": "scope", "day": (start + timedelta(days=index)).isoformat(),
             "cost_cloud": 5000.0 if index == spike else 100.0,
             "cloud_origem": "cloud", "subaccountname": "sub", "servicename": "service",
             "chargecategory": "usage", "commitmentdiscounttype": "ondemand"}
            for index in range(days)]


def test_baseline_excludes_event_and_future_and_is_order_independent():
    rows = history()
    target = date.fromisoformat(rows[50]["day"])
    past = [row for row in rows if row["day"] < target.isoformat()]
    expected = expected_for_day(past, target=target)
    assert expected_for_day(rows, target=target) == expected
    assert expected_for_day(list(reversed(rows)), target=target) == expected


def test_missing_or_partial_days_cannot_confirm_return():
    rows = history(days=54)
    event = date.fromisoformat(rows[50]["day"])
    gapped = rows[:52] + rows[53:]
    end = date.fromisoformat(rows[-1]["day"])
    assert continuous_episode(gapped, as_of=end, materiality=50, min_pct=.2)["deviation_status"] == "EM_DESVIO"
    assert evaluate_return(daily=gapped, scope_id="scope", episode_start=event,
                           as_of=end, treated=False)["deviation_status"] == "EM_DESVIO"
    partial = [dict(row, cost_cloud=1.0) if row["day"] > event.isoformat() else row for row in rows]
    valid = valid_days_by_cloud(partial)["cloud"]
    assert evaluate_return(daily=partial, scope_id="scope", episode_start=event, as_of=end,
                           treated=False, valid_days=valid)["deviation_status"] == "EM_DESVIO"


def test_continued_increase_is_not_absorbed_into_episode_reference():
    rows = history(days=80)
    for index in range(50, 80):
        rows[index]["cost_cloud"] = 5000
    episode = continuous_episode(rows, as_of=date.fromisoformat(rows[-1]["day"]), materiality=50, min_pct=.2)
    assert episode["deviation_status"] == "EM_DESVIO"
    assert episode["expected"] == 100


def test_one_off_and_monthly_impacts_are_not_multiplied_by_thirty():
    spike = score_signal({"detector": "SPIKE", "delta": 60000, "persistence_days": 1}, materiality=100)
    assert spike["projected_month_impact"] == 0
    assert spike["severity"] == "CRITICAL"
    monthly = score_signal({"detector": "MONTHLY_ACCUMULATED", "delta": 1200}, materiality=100)
    assert monthly["projected_month_impact"] == 1200


def test_expected_commitments_do_not_fall_through_to_daily_spike():
    rows = [{"scope_id": "scope", "day": "2026-09-09", "cost_cloud": 1000},
            {"scope_id": "scope", "day": "2026-10-09", "cost_cloud": 1000}]
    ledger = [{"scope_id": "scope", "charges": [{"day": row["day"], "amount": 1000} for row in rows]}]
    signals = run_detectors_for_scope(scope_id="scope", daily=rows,
        profile={"cost_nature": "COMMITMENT"}, as_of=date(2026, 10, 18),
        materiality=100, min_pct=.2, investigate_score=55, ledger_entries=ledger)
    assert not signals


def test_uncollected_months_do_not_reduce_monthly_expectation():
    rows = [{"scope_id": "s", "day": "2026-09-05", "cost_cloud": 1000},
            {"scope_id": "s", "day": "2026-10-05", "cost_cloud": 1000}]
    assert not run_detectors_for_scope(scope_id="s", daily=rows,
        profile={"cost_nature": "INTERMITTENT"}, as_of=date(2026, 10, 18), materiality=100,
        min_pct=.2, investigate_score=55, ledger_entries=[])


def test_annual_impact_is_twelve_times_monthly_total():
    from app.routers.anomalies import impact_summary

    rows = [SimpleNamespace(deviation_status=status, projected_month_impact=value, impact_to_date=10)
            for status, value in [("AUTOCORRIGIDO", 100), ("CORRIGIDO_COM_ACAO", 200),
                                  ("EM_DESVIO", 50), ("ACEITO", 70)]]
    result_rows = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))
    result = asyncio.run(impact_summary(db=SimpleNamespace(execute=AsyncMock(return_value=result_rows)), user=None))
    assert result["projected_saving_monthly"] == 300
    assert result["projected_saving_annual"] == 3600
    assert result["open_exposure_monthly"] == 120
    assert result["open_exposure_annual"] == 1440


class AsyncAdapter:
    def __init__(self, db):
        self.db = db

    async def get(self, *args):
        return self.db.get(*args)

    async def execute(self, *args, **kwargs):
        return self.db.execute(*args, **kwargs)

    async def flush(self):
        self.db.flush()

    async def commit(self):
        self.db.commit()

    def add(self, row):
        self.db.add(row)


@pytest.mark.parametrize("recovered", [True, False])
def test_runner_publishes_and_persists_cases_idempotently(monkeypatch, recovered):
    engine = create_engine("sqlite://")
    names = ("anomaly_config", "anomaly_run", "anomaly_case", "anomaly_case_event", "anomaly_action", "anomaly_notification")
    Base.metadata.create_all(engine, tables=[Base.metadata.tables[name] for name in names])
    with Session(engine, expire_on_commit=False) as db:
        config = AnomalyConfig(name="test", destination_physical_name="focus", date_column="chargeperiodstart", enabled=True)
        db.add(config)
        db.commit()
        rows = history(days=60, spike=50 if recovered else 59)
        cutoff = date.fromisoformat(rows[-1]["day"])

        @asynccontextmanager
        async def sessions():
            yield AsyncAdapter(db)

        monkeypatch.setattr(runner, "async_session", sessions)
        monkeypatch.setattr(runner, "_source_snapshot_id", AsyncMock(return_value="snapshot-1"))
        monkeypatch.setattr(runner, "load_daily_history", AsyncMock(return_value=rows))
        monkeypatch.setattr(runner, "build_daily_aggregate", AsyncMock(return_value={"row_count": len(rows)}))
        monkeypatch.setattr(runner, "refresh_commitment_ledger", AsyncMock(return_value={"entries": []}))
        monkeypatch.setattr(runner, "_ensure_tables", AsyncMock())
        writer = AsyncMock(return_value=1)
        monkeypatch.setattr(runner, "_publish_signals", writer)
        detected_at = datetime.combine(cutoff + timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc)

        async def scenario():
            for index in range(2):
                run = AnomalyRun(anomaly_config_id=config.id, trigger_type="MANUAL", status="QUEUED")
                db.add(run)
                db.commit()
                result = await runner.execute_detection_v2(config.id, run_id=run.id, detected_at=detected_at)
                assert result["status"] == "COMPLETED"
                assert result["cases_created"] == (1 if index == 0 else 0)
                assert run.status == "COMPLETED"
            cases = db.execute(select(AnomalyCase)).scalars().all()
            assert len(cases) == 1
            assert cases[0].state == ("CLOSED" if recovered else "NEW")
            assert cases[0].projected_month_impact == 0
            assert db.execute(select(AnomalyCaseEvent)).scalars().all()
            assert writer.await_count == 2
            # Scheduled retries remain idempotent, including after another skip.
            for _ in range(2):
                run = AnomalyRun(anomaly_config_id=config.id, trigger_type="SCHEDULED", status="QUEUED")
                db.add(run)
                db.commit()
                result = await runner.execute_detection_v2(config.id, run_id=run.id, detected_at=detected_at)
                assert result["status"] == "SKIPPED_NO_NEW_DATA"
                assert run.config_snapshot["v2_summary"]["model_version"] == runner.MODEL_VERSION
            assert writer.await_count == 2

        asyncio.run(scenario())
