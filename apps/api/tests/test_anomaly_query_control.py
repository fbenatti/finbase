import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

from app.services.anomaly_v2 import query_control, runner
from app.services.trino_service import TrinoCancelled


def test_query_has_one_attempt_bounded_timeout_and_cancellation(monkeypatch):
    async def scenario():
        event = asyncio.Event()
        token = query_control.cancel_event.set(event)
        execute = AsyncMock(return_value=[])
        monkeypatch.setattr(query_control, "execute_trino", execute)
        try:
            await query_control.execute("SELECT test", timeout_s=1200)
            assert execute.await_args.kwargs == {"timeout_s": 120, "max_retries": 1, "cancel_event": event}
        finally:
            query_control.cancel_event.reset(token)

    asyncio.run(scenario())


def test_worker_cancellation_reaches_inflight_query(monkeypatch):
    async def scenario():
        async def detection(*args, **kwargs):
            event = query_control.cancel_event.get()
            assert event is not None
            await event.wait()
            raise TrinoCancelled("cancelled")

        @asynccontextmanager
        async def sessions():
            yield object()

        monkeypatch.setattr(runner, "_execute_detection_v2", detection)
        monkeypatch.setattr(runner, "async_session", sessions)
        finish = AsyncMock(return_value={"status": "CANCELLED"})
        monkeypatch.setattr(runner, "_finish_cancelled", finish)
        result = await asyncio.wait_for(runner.execute_detection_v2(11, run_id=9, should_abort=lambda: True), timeout=2)
        assert result == {"status": "CANCELLED"}
        assert finish.await_count == 1
        assert query_control.cancel_event.get() is None

    asyncio.run(scenario())
