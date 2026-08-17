"""
Trino Service — query Iceberg tables via Trino REST API.

Uses the Trino HTTP statement endpoint directly (no heavy client needed).
Returns results as list[dict] for JSON serialization.
"""

from __future__ import annotations

import json
import logging
import os
import time
import asyncio
from typing import Any
from urllib.parse import urljoin

import httpx

logger = logging.getLogger(__name__)

TRINO_URL = os.getenv("TRINO_URL", "http://localhost:8080")
TRINO_USER = os.getenv("TRINO_USER", "finops")
TRINO_CATALOG = os.getenv("TRINO_CATALOG", "iceberg")
TRINO_TRANSPORT_RETRIES = max(1, int(os.getenv("TRINO_TRANSPORT_RETRIES", "8")))
TRINO_READY_WAIT_S = max(5.0, float(os.getenv("TRINO_READY_WAIT_S", "60")))

_RETRYABLE_MARKERS = (
    "still initializing",
    "expecting value",
    "connection reset",
    "readerror",
    "remoteprotocolerror",
    "server disconnected",
    "connection aborted",
    "broken pipe",
    "timed out",
    "timeout",
    "503",
    "502",
    "starting",
)


class TrinoError(Exception):
    """Raised when a Trino statement fails."""


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TransportError, json.JSONDecodeError)):
        return True
    if isinstance(exc, TrinoError):
        message = str(exc).lower()
        return any(marker in message for marker in _RETRYABLE_MARKERS)
    return False


async def wait_until_ready(timeout_s: float | None = None) -> None:
    """Block until the coordinator reports ACTIVE (or raise after timeout)."""
    deadline = time.monotonic() + (timeout_s if timeout_s is not None else TRINO_READY_WAIT_S)
    last_error: Exception | None = None
    info_url = urljoin(TRINO_URL, "/v1/info")
    while time.monotonic() < deadline:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
                resp = await client.get(info_url)
            if resp.status_code == 200:
                payload = resp.json()
                if str(payload.get("state", "")).upper() == "ACTIVE" and not payload.get("starting"):
                    return
                last_error = TrinoError(f"Trino state={payload.get('state')} starting={payload.get('starting')}")
            else:
                last_error = TrinoError(f"Trino info HTTP {resp.status_code}")
        except Exception as exc:
            last_error = exc
        await asyncio.sleep(1.0)
    detail = str(last_error).strip() if last_error else "sem resposta"
    raise TrinoError(f"Trino não ficou ACTIVE após {int(timeout_s or TRINO_READY_WAIT_S)}s: {detail}") from last_error


async def execute(
    sql: str,
    catalog: str | None = None,
    timeout_s: float = 60.0,
    max_retries: int | None = None,
) -> list[dict[str, Any]]:
    """
    Execute a SQL statement against Trino and return rows as list of dicts.

    Handles the Trino protocol: POST → get nextUri → fetch until done.
    """
    # The coordinator can close keep-alive connections after Iceberg writes or
    # restart under load (forecast aggregation right after reprocess). Retry
    # transport failures with backoff instead of failing a long-running job.
    last_error: Exception | None = None
    wait_for_coordinator = False
    attempts = TRINO_TRANSPORT_RETRIES if max_retries is None else max(1, max_retries)
    for attempt in range(attempts):
        try:
            if wait_for_coordinator:
                remaining = max(5.0, TRINO_READY_WAIT_S - 5.0 * attempt)
                await wait_until_ready(timeout_s=remaining)
            return await _execute_once(sql, catalog, timeout_s)
        except Exception as exc:
            if not _is_retryable(exc):
                raise
            last_error = exc
            wait_for_coordinator = True
            if attempt + 1 >= attempts:
                break
            delay = min(10.0, 1.0 * (2 ** attempt))
            logger.warning("Trino query retry %s/%s after %s: %s", attempt + 1, TRINO_TRANSPORT_RETRIES, type(exc).__name__, exc)
            await asyncio.sleep(delay)
    detail = str(last_error).strip() if last_error else ""
    if not detail:
        detail = type(last_error).__name__ if last_error else "TransportError"
    raise TrinoError(
        f"Trino indisponível após {attempts} tentativas: {detail}"
    ) from last_error


async def _execute_once(
    sql: str,
    catalog: str | None = None,
    timeout_s: float = 60.0,
) -> list[dict[str, Any]]:
    effective_catalog = catalog or TRINO_CATALOG
    headers = {
        "X-Trino-User": TRINO_USER,
        "X-Trino-Catalog": effective_catalog,
        "X-Trino-Schema": "bronze",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
        # 1 — Submit statement
        submit_url = urljoin(TRINO_URL, "/v1/statement")
        resp = await client.post(submit_url, headers=headers, content=sql)
        if resp.status_code in (502, 503) and "QUEUED" not in resp.text:
            raise TrinoError(f"Trino error {resp.status_code}: coordinator ocupado ou reiniciando")
        if resp.status_code == 503 and "QUEUED" in resp.text:
            # Trino queues the query — follow nextUri
            pass
        elif resp.status_code != 200:
            _raise_trino_error(resp)

        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise TrinoError(f"Trino error {resp.status_code}: resposta inválida do coordinator") from exc
        next_uri = data.get("nextUri")
        columns = [c["name"] for c in data.get("columns", [])]
        rows: list[list[Any]] = data.get("data", []) or []

        # 2 — Follow nextUri until finished
        deadline = time.monotonic() + timeout_s
        while next_uri:
            if time.monotonic() > deadline:
                # Cancel the query
                await client.delete(next_uri, headers=headers)
                raise TrinoError("Trino query timed out")

            resp = await client.get(next_uri, headers=headers)
            if resp.status_code in (502, 503, 429):
                raise TrinoError(f"Trino error {resp.status_code}: coordinator ocupado ou reiniciando")
            if resp.status_code != 200:
                _raise_trino_error(resp)

            try:
                page = resp.json()
            except json.JSONDecodeError as exc:
                raise TrinoError(f"Trino error {resp.status_code}: resposta inválida do coordinator") from exc
            if page.get("error"):
                _raise_trino_error(resp)
            next_uri = page.get("nextUri")
            # Trino may return columns only after a queued query starts
            # running. Preserve the schema from the first page that provides
            # it instead of assuming it is present in the initial response.
            if page.get("columns"):
                columns = [c["name"] for c in page["columns"]]
            if page.get("data"):
                rows.extend(page["data"])

        # 3 — Build list of dicts
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(dict(zip(columns, row)))

        return result


def execute_sync(
    sql: str,
    catalog: str | None = None,
    timeout_s: float = 60.0,
) -> list[dict[str, Any]]:
    """Synchronous wrapper for execute (convenience)."""
    import asyncio

    return asyncio.run(execute(sql, catalog, timeout_s))


def _raise_trino_error(resp: httpx.Response) -> None:
    """Extract error info from a Trino response and raise."""
    try:
        body = resp.json()
        msg = body.get("error", {}).get("message", resp.text)
    except Exception:
        msg = resp.text
    raise TrinoError(f"Trino error {resp.status_code}: {msg}")
