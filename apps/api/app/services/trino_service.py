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
# Fases da query (fila, análise, planejamento, execução) lidas de GET /v1/query/{id}
# depois de cada statement. É uma chamada HTTP a mais por query, então fica ligada
# por padrão só em desenvolvimento, onde serve para medir o que o cluster gasta
# antes de ler dado.
TRINO_QUERY_PHASES = os.getenv(
    "TRINO_QUERY_PHASES", "true" if os.getenv("ENV", "").lower() == "development" else "false"
).lower() == "true"
_SQL_LOG_CHARS = 160

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
    session_properties: dict[str, str] | None = None,
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
            return await _execute_once(sql, catalog, timeout_s, cancel_event, session_properties)
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


def sql_kind(sql: str) -> str:
    """Primeira palavra do statement (SELECT, WITH, DESCRIBE, ALTER...), para agrupar métricas."""
    for token in sql.strip().split(None, 2):
        word = token.strip("(").upper()
        if word:
            return word
    return "VAZIO"


def sql_for_log(sql: str, limit: int = _SQL_LOG_CHARS) -> str:
    """SQL em uma linha e truncado, para o log por query não virar um despejo."""
    flat = " ".join(sql.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


async def _query_phases(client: httpx.AsyncClient, query_id: str | None) -> dict[str, Any]:
    """Tempos por fase em GET /v1/query/{id}. Falha vira dicionário vazio: é diagnóstico."""
    if not query_id or not TRINO_QUERY_PHASES:
        return {}
    try:
        resp = await client.get(
            urljoin(TRINO_URL, f"/v1/query/{query_id}"), headers={"X-Trino-User": TRINO_USER}, timeout=3.0,
        )
        if resp.status_code != 200:
            return {}
        stats = resp.json().get("queryStats") or {}
    except Exception:  # noqa: BLE001 — diagnóstico nunca derruba a query
        return {}
    return {
        key: stats.get(field)
        for key, field in (
            ("queued", "queuedTime"), ("analysis", "analysisTime"),
            ("planning", "planningTime"), ("execution", "executionTime"),
        )
        if stats.get(field) is not None
    }


def _record_query(kind: str, elapsed_ms: float, *, error: bool) -> None:
    try:
        from app.services.finops_cache import record_request_metric

        record_request_metric("trino", elapsed_ms, error=error)
        record_request_metric(f"trino:{kind}", elapsed_ms, error=error)
    except Exception:  # noqa: BLE001
        logger.debug("Falha ao registrar métrica da query Trino", exc_info=True)


async def _execute_once(
    sql: str,
    catalog: str | None = None,
    timeout_s: float = 60.0,
    cancel_event: asyncio.Event | None = None,
    session_properties: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    effective_catalog = catalog or TRINO_CATALOG
    effective_sql = qualify_sql_catalog(sql, effective_catalog)
    headers = {
        "X-Trino-User": TRINO_USER,
        "X-Trino-Catalog": effective_catalog,
        "X-Trino-Schema": "bronze",
    }
    if session_properties:
        # Propriedades de sessão (ex.: retenção mínima de snapshots) vão no
        # cabeçalho do protocolo, como `SET SESSION` faria numa conexão.
        headers["X-Trino-Session"] = ",".join(f"{key}={value}" for key, value in session_properties.items())
    kind = sql_kind(effective_sql)
    started = time.monotonic()
    query_id: str | None = None
    outcome = "erro"

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
        try:
            query_id, rows_out = await _execute_protocol(
                client, effective_sql, headers, timeout_s, cancel_event,
            )
            outcome = "ok"
            return rows_out
        except TrinoCancelled:
            outcome = "cancelada"
            raise
        finally:
            elapsed_ms = (time.monotonic() - started) * 1000
            _record_query(kind, elapsed_ms, error=outcome == "erro")
            phases = await _query_phases(client, query_id) if outcome == "ok" else {}
            phase_text = (" " + " ".join(f"{k}={v}" for k, v in phases.items())) if phases else ""
            logger.info(
                "Trino %s %s em %.0f ms [%s]%s: %s",
                kind, outcome, elapsed_ms, query_id or "sem id", phase_text, sql_for_log(effective_sql),
            )


async def _execute_protocol(
    client: httpx.AsyncClient,
    effective_sql: str,
    headers: dict[str, str],
    timeout_s: float,
    cancel_event: asyncio.Event | None,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Protocolo do statement: POST, seguir nextUri até acabar. Devolve (queryId, linhas)."""
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
    query_id = data.get("id")
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

    return query_id, result


def execute_sync(
    sql: str,
    catalog: str | None = None,
    timeout_s: float = 60.0,
    session_properties: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Synchronous wrapper for execute (convenience)."""
    import asyncio

    return asyncio.run(execute(sql, catalog, timeout_s, session_properties=session_properties))


def _raise_trino_error(resp: httpx.Response) -> None:
    """Extract error info from a Trino response and raise."""
    try:
        body = resp.json()
        msg = body.get("error", {}).get("message", resp.text)
    except Exception:
        msg = resp.text
    raise TrinoError(f"Trino error {resp.status_code}: {msg}")
