"""Bound anomaly scans and propagate worker cancellation into Trino HTTP calls."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Any

from app.services.trino_service import execute as execute_trino

cancel_event: ContextVar[asyncio.Event | None] = ContextVar("anomaly_cancel_event", default=None)
MAX_QUERY_SECONDS = 120


async def execute(sql: str, *, timeout_s: float = 60, **kwargs: Any) -> list[dict[str, Any]]:
    # Do not resubmit an expensive scan twelve times after a coordinator crash.
    kwargs["max_retries"] = 1
    kwargs.setdefault("cancel_event", cancel_event.get())
    return await execute_trino(sql, timeout_s=min(timeout_s, MAX_QUERY_SECONDS), **kwargs)
