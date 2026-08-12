"""
Trino Service — query Iceberg tables via Trino REST API.

Uses the Trino HTTP statement endpoint directly (no heavy client needed).
Returns results as list[dict] for JSON serialization.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any
from urllib.parse import urljoin

import httpx

logger = logging.getLogger(__name__)

TRINO_URL = os.getenv("TRINO_URL", "http://localhost:8080")
TRINO_USER = os.getenv("TRINO_USER", "finops")
TRINO_CATALOG = os.getenv("TRINO_CATALOG", "iceberg")


class TrinoError(Exception):
    """Raised when a Trino statement fails."""


async def execute(
    sql: str,
    catalog: str | None = None,
    timeout_s: float = 60.0,
) -> list[dict[str, Any]]:
    """
    Execute a SQL statement against Trino and return rows as list of dicts.

    Handles the Trino protocol: POST → get nextUri → fetch until done.
    """
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
        if resp.status_code == 503 and "QUEUED" in resp.text:
            # Trino queues the query — follow nextUri
            pass
        elif resp.status_code != 200:
            _raise_trino_error(resp)

        data = resp.json()
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
            if resp.status_code != 200:
                _raise_trino_error(resp)

            page = resp.json()
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
