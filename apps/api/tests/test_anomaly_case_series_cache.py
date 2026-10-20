"""Regression tests for investigation read reuse, isolation and retries."""
import asyncio
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.anomaly_v2 import case_series as series


@pytest.fixture(autouse=True)
def clean_cache():
    series._SERIES_CACHE.clear()
    series._SERIES_PENDING.clear()
    yield
    series._SERIES_CACHE.clear()
    series._SERIES_PENDING.clear()


def inputs():
    return (
        SimpleNamespace(series_id="scope", event_date="2026-10-09", case_version=1,
                        series_dimensions={"cloud_origem": "azure-ti"}),
        SimpleNamespace(id=1, version=1, destination_physical_name="focus"),
    )


def test_concurrent_opens_share_read_and_results_are_isolated(monkeypatch):
    async def scenario():
        case, config = inputs()
        entered, release = asyncio.Event(), asyncio.Event()

        async def load(*args, **kwargs):
            entered.set()
            await release.wait()
            return {"items": [{"observed_cloud": 42}], "_detail_failed": False}

        reader = AsyncMock(side_effect=load)
        monkeypatch.setattr(series, "_read_v2_case_series", reader)
        first = asyncio.create_task(series.read_v2_case_series(case, config, as_of=date(2026, 10, 18)))
        await entered.wait()
        second = asyncio.create_task(series.read_v2_case_series(case, config, as_of=date(2026, 10, 18)))
        release.set()
        a, b = await asyncio.gather(first, second)
        a["items"][0]["observed_cloud"] = -1
        cached = await series.read_v2_case_series(case, config, as_of=date(2026, 10, 18))
        assert b == cached == {"items": [{"observed_cloud": 42}]}
        assert reader.await_count == 1
        assert not series._SERIES_PENDING

    asyncio.run(scenario())


def test_expiry_scope_config_and_case_changes_trigger_new_reads(monkeypatch):
    async def scenario():
        case, config = inputs()
        clock = [100.0]
        monkeypatch.setattr(series.time, "monotonic", lambda: clock[0])
        reader = AsyncMock(return_value={"items": []})
        monkeypatch.setattr(series, "_read_v2_case_series", reader)

        async def read():
            return await series.read_v2_case_series(case, config, as_of=date(2026, 10, 18))

        await read()
        await read()
        assert reader.await_count == 1
        clock[0] += series._SERIES_TTL_S
        await read()
        case.series_dimensions = {"cloud_origem": "aws"}
        await read()
        config.version += 1
        await read()
        case.case_version += 1
        await read()
        config.destination_physical_name = "other_source"
        await read()
        await series.read_v2_case_series(case, config, as_of=date(2026, 10, 19))
        assert reader.await_count == 7

    asyncio.run(scenario())


def test_errors_and_failed_optional_details_are_retried(monkeypatch):
    async def scenario():
        case, config = inputs()
        reader = AsyncMock(side_effect=[RuntimeError("Trino unavailable"),
                                       {"items": [], "_detail_failed": True},
                                       {"items": [], "detail_items": [{"observed_cloud": 42}]}])
        monkeypatch.setattr(series, "_read_v2_case_series", reader)
        with pytest.raises(RuntimeError):
            await series.read_v2_case_series(case, config, as_of=date(2026, 10, 18))
        partial = await series.read_v2_case_series(case, config, as_of=date(2026, 10, 18))
        assert partial == {"items": []}
        assert not series._SERIES_CACHE
        complete = await series.read_v2_case_series(case, config, as_of=date(2026, 10, 18))
        assert complete["detail_items"]
        assert reader.await_count == 3
        assert not series._SERIES_PENDING

    asyncio.run(scenario())


def test_disconnecting_one_caller_does_not_cancel_shared_read(monkeypatch):
    async def scenario():
        case, config = inputs()
        entered, release = asyncio.Event(), asyncio.Event()

        async def load(*args, **kwargs):
            entered.set()
            await release.wait()
            return {"items": []}

        reader = AsyncMock(side_effect=load)
        monkeypatch.setattr(series, "_read_v2_case_series", reader)
        first = asyncio.create_task(series.read_v2_case_series(case, config, as_of=date(2026, 10, 18)))
        await entered.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert await series.read_v2_case_series(case, config, as_of=date(2026, 10, 18)) == {"items": []}
        assert reader.await_count == 1

    asyncio.run(scenario())


def test_cache_size_is_bounded(monkeypatch):
    async def scenario():
        case, config = inputs()
        monkeypatch.setattr(series, "_SERIES_CACHE_MAX", 2)
        monkeypatch.setattr(series, "_read_v2_case_series", AsyncMock(return_value={"items": []}))
        for index in range(4):
            case.series_id = str(index)
            await series.read_v2_case_series(case, config, as_of=date(2026, 10, 18))
        assert len(series._SERIES_CACHE) == 2

    asyncio.run(scenario())


def test_chart_baseline_is_independent_of_trino_result_order():
    start = date(2026, 9, 1)
    end = start + timedelta(days=44)
    rows = [{"day": (start + timedelta(days=index)).isoformat(),
             "cost_cloud": 100 + index * 3 + (index % 7) * 20}
            for index in range(45)]
    by_day = {row["day"]: row["cost_cloud"] for row in rows}
    expected = series._chart_items(by_day, rows, start=start, end=end, as_of=end)
    reversed_rows = list(reversed(rows))
    actual = series._chart_items(dict(reversed(list(by_day.items()))), reversed_rows,
                                 start=start, end=end, as_of=end)
    assert actual == expected
