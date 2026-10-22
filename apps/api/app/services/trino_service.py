"""
Trino Service — query Iceberg tables via Trino REST API.

Uses the Trino HTTP statement endpoint directly (no heavy client needed).
Returns results as list[dict] for JSON serialization.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import asyncio
import contextlib
from typing import Any
from urllib.parse import urljoin

import httpx

logger = logging.getLogger(__name__)

TRINO_URL = os.getenv("TRINO_URL", "http://localhost:8080")
TRINO_USER = os.getenv("TRINO_USER", "finops")
TRINO_CATALOG = os.getenv("TRINO_CATALOG", "iceberg")
TRINO_TRANSPORT_RETRIES = max(1, int(os.getenv("TRINO_TRANSPORT_RETRIES", "12")))
TRINO_STARTUP_RECOVERY_RETRIES = max(1, int(os.getenv("TRINO_STARTUP_RECOVERY_RETRIES", "16")))
TRINO_READY_WAIT_S = max(5.0, float(os.getenv("TRINO_READY_WAIT_S", "120")))

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
    "429",
    "starting",
    # HTTP 200 with a non-JSON body — the coordinator is mid-restart and
    # answers the statement endpoint with an HTML/empty page. Transient.
    "resposta inválida do coordinator",
    "invalid response from coordinator",
)


class TrinoError(Exception):
    """Raised when a Trino statement fails."""


class TrinoCancelled(TrinoError):
    """Raised when the caller aborts a running Trino statement."""


def qualify_sql_catalog(sql: str, catalog: str | None = None) -> str:
    """Route fully qualified Iceberg SQL through the configured Trino catalog."""
    effective_catalog = catalog or TRINO_CATALOG
    if not re.fullmatch(r"[A-Za-z0-9_]+", effective_catalog):
        raise ValueError("Invalid Trino catalog name")
    if effective_catalog == "iceberg":
        return sql
    return re.sub(r"(?<![A-Za-z0-9_])iceberg(?=\.)", effective_catalog, sql)


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
    cancel_event: asyncio.Event | None = None,
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
    attempt = 0
    while attempt < attempts:
        try:
            if wait_for_coordinator:
                # Give the coordinator a full, constant readiness window on
                # every retry instead of shrinking it — a restart can take
                # several minutes, and a shrinking budget wastes retries.
                await wait_until_ready(timeout_s=TRINO_READY_WAIT_S)
            return await _execute_once(sql, catalog, timeout_s, cancel_event)
        except TrinoCancelled:
            raise
        except Exception as exc:
            if not _is_retryable(exc):
                raise
            last_error = exc
            wait_for_coordinator = True
            # Worker processes can take over a minute to finish Trino startup.
            # Keep the normal retry budget for transport failures, but allow a
            # longer bounded window for this explicit transient response.
            if "still initializing" in str(exc).lower():
                attempts = max(attempts, TRINO_STARTUP_RECOVERY_RETRIES)
            if attempt + 1 >= attempts:
                break
            delay = min(10.0, 1.0 * (2 ** attempt))
            logger.warning("Trino query retry %s/%s after %s: %s", attempt + 1, attempts, type(exc).__name__, exc)
            await asyncio.sleep(delay)
            attempt += 1
    detail = str(last_error).strip() if last_error else ""
    if not detail:
        detail = type(last_error).__name__ if last_error else "TransportError"
    raise TrinoError(
        f"Trino indisponível após {attempts} tentativas: {detail}"
    ) from last_error


async def _cancel_trino(client: httpx.AsyncClient, uri: str | None, headers: dict[str, str]) -> None:
    if not uri:
        return
    try:
        await client.delete(uri, headers=headers)
    except Exception:
        logger.debug("Failed to cancel Trino query at %s", uri, exc_info=True)


async def _request_with_cancel(
    client: httpx.AsyncClient,
    method: str,
    uri: str,
    headers: dict[str, str],
    cancel_event: asyncio.Event | None,
    cancel_uri: str | None,
    *,
    content: str | None = None,
) -> httpx.Response:
    """Run an httpx request but abort promptly when cancel_event is set."""
    if cancel_event and cancel_event.is_set():
        await _cancel_trino(client, cancel_uri or uri, headers)
        raise TrinoCancelled("Trino query cancelled")

    request_coro = client.post(uri, headers=headers, content=content) if method == "POST" else client.get(uri, headers=headers)
    fetch = asyncio.create_task(request_coro)
    cancel_wait = asyncio.create_task(cancel_event.wait()) if cancel_event else None
    try:
        if cancel_wait:
            done, _ = await asyncio.wait({fetch, cancel_wait}, return_when=asyncio.FIRST_COMPLETED)
            if cancel_wait in done and cancel_event and cancel_event.is_set():
                fetch.cancel()
                with contextlib.suppress(asyncio.CancelledError, httpx.HTTPError):
                    await fetch
                await _cancel_trino(client, cancel_uri or uri, headers)
                raise TrinoCancelled("Trino query cancelled")
        return await fetch
    finally:
        if cancel_wait and not cancel_wait.done():
            cancel_wait.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cancel_wait


async def _execute_once(
    sql: str,
    catalog: str | None = None,
    timeout_s: float = 60.0,
    cancel_event: asyncio.Event | None = None,
) -> list[dict[str, Any]]:
    effective_catalog = catalog or TRINO_CATALOG
    effective_sql = qualify_sql_catalog(sql, effective_catalog)
    headers = {
        "X-Trino-User": TRINO_USER,
        "X-Trino-Catalog": effective_catalog,
        "X-Trino-Schema": "bronze",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
        if cancel_event and cancel_event.is_set():
            raise TrinoCancelled("Trino query cancelled")
        # 1 — Submit statement
        submit_url = urljoin(TRINO_URL, "/v1/statement")
        resp = await _request_with_cancel(
            client, "POST", submit_url, headers, cancel_event, submit_url, content=effective_sql,
        )
        if resp.status_code in (429, 502, 503) and "QUEUED" not in resp.text:
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
        cancel_uri = data.get("partialCancelUri") or next_uri
        columns = [c["name"] for c in data.get("columns", [])]
        rows: list[list[Any]] = data.get("data", []) or []

        # 2 — Follow nextUri until finished
        deadline = time.monotonic() + timeout_s
        while next_uri:
            if cancel_event and cancel_event.is_set():
                await _cancel_trino(client, cancel_uri or next_uri, headers)
                raise TrinoCancelled("Trino query cancelled")
            if time.monotonic() > deadline:
                await _cancel_trino(client, cancel_uri or next_uri, headers)
                raise TrinoError("Trino query timed out")

            resp = await _request_with_cancel(
                client, "GET", next_uri, headers, cancel_event, cancel_uri or next_uri,
            )
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
            cancel_uri = page.get("partialCancelUri") or next_uri or cancel_uri
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
