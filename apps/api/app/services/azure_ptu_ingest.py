"""Shared usage-event ingestion for HTTP and Event Hub paths."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.azure_ptu import AzurePtuSubproject, AzurePtuUsageEvent

logger = logging.getLogger(__name__)

USAGE_EVENT_FIELDS = frozenset({
    "event_id", "occurred_at", "telemetry_client_id", "project_key", "subproject_key",
    "environment", "azure_account_resource_id", "deployment_name", "model_name",
    "model_version", "api_operation", "stream", "status_code", "is_spillover",
    "request_count", "input_tokens", "cached_input_tokens", "output_tokens",
    "reasoning_tokens", "total_tokens", "latency_ms",
})


@dataclass
class IngestResult:
    accepted: int = 0
    quarantined: int = 0
    duplicates: int = 0
    received: int = 0
    iceberg: dict[str, int] = field(default_factory=dict)


def _normalize_event(raw: dict[str, Any]) -> dict[str, Any]:
    payload = {k: raw[k] for k in USAGE_EVENT_FIELDS if k in raw}
    if "event_id" not in payload or "telemetry_client_id" not in payload:
        raise ValueError("event_id and telemetry_client_id are required")
    occurred = payload.get("occurred_at")
    if isinstance(occurred, str):
        payload["occurred_at"] = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
    elif not isinstance(occurred, datetime):
        payload["occurred_at"] = datetime.now(timezone.utc)
    for token_field in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens", "request_count"):
        payload[token_field] = int(payload.get(token_field) or 0)
    payload["total_tokens"] = payload.get("total_tokens") or payload["input_tokens"] + payload["output_tokens"]
    payload["is_spillover"] = bool(payload.get("is_spillover"))
    if payload.get("status_code") is not None:
        payload["status_code"] = int(payload["status_code"])
    return payload


def _ingest_normalized(
    session: Session,
    normalized: list[dict[str, Any]],
    *,
    received: int,
    existing: set[str],
    by_client: dict[str, AzurePtuSubproject],
) -> IngestResult:
    result = IngestResult(received=received)
    accepted_rows: list[dict[str, Any]] = []

    for event in normalized:
        if event["event_id"] in existing:
            result.duplicates += 1
            continue
        sub = by_client.get(event["telemetry_client_id"])
        reason = None
        status = "accepted"
        if not sub:
            status, reason = "quarantine", "UNKNOWN_TELEMETRY_CLIENT"
        elif event.get("project_key") and event["project_key"] != sub.project_key:
            status, reason = "quarantine", "PROJECT_CLIENT_MISMATCH"
        elif event.get("subproject_key") and event["subproject_key"] != sub.subproject_key:
            status, reason = "quarantine", "SUBPROJECT_CLIENT_MISMATCH"

        row = AzurePtuUsageEvent(
            **event,
            project_key=(sub.project_key if sub else event.get("project_key")),
            subproject_key=(sub.subproject_key if sub else event.get("subproject_key")),
            ingestion_status=status,
            quarantine_reason=reason,
        )
        session.add(row)
        if status == "accepted":
            result.accepted += 1
            accepted_rows.append({**event, "project_key": row.project_key, "subproject_key": row.subproject_key})
        else:
            result.quarantined += 1
        existing.add(event["event_id"])

    session.flush()
    result.iceberg = persist_usage_to_iceberg(accepted_rows)
    return result


def ingest_usage_event_dicts(session: Session, events: list[dict[str, Any]]) -> IngestResult:
    """Validate, quarantine, dedupe and persist usage events (Postgres + Iceberg)."""
    if not events:
        return IngestResult()

    normalized: list[dict[str, Any]] = []
    for raw in events:
        try:
            normalized.append(_normalize_event(raw))
        except (TypeError, ValueError) as exc:
            logger.info("Skipping malformed PTU usage event: %s", exc)

    if not normalized:
        return IngestResult(received=len(events))

    ids = [e["event_id"] for e in normalized]
    existing = set(session.execute(
        select(AzurePtuUsageEvent.event_id).where(AzurePtuUsageEvent.event_id.in_(ids))
    ).scalars().all())
    clients = session.execute(
        select(AzurePtuSubproject).where(
            AzurePtuSubproject.telemetry_client_id.in_([e["telemetry_client_id"] for e in normalized]),
            AzurePtuSubproject.active.is_(True),
        )
    ).scalars().all()
    by_client = {x.telemetry_client_id: x for x in clients}
    return _ingest_normalized(session, normalized, received=len(events), existing=existing, by_client=by_client)


async def ingest_usage_events_async(session: Any, events: list[dict[str, Any]]) -> IngestResult:
    """Async variant for FastAPI routes."""
    if not events:
        return IngestResult()

    normalized: list[dict[str, Any]] = []
    for raw in events:
        try:
            normalized.append(_normalize_event(raw))
        except (TypeError, ValueError) as exc:
            logger.info("Skipping malformed PTU usage event: %s", exc)

    if not normalized:
        return IngestResult(received=len(events))

    ids = [e["event_id"] for e in normalized]
    existing = set((await session.execute(
        select(AzurePtuUsageEvent.event_id).where(AzurePtuUsageEvent.event_id.in_(ids))
    )).scalars().all())
    clients = (await session.execute(
        select(AzurePtuSubproject).where(
            AzurePtuSubproject.telemetry_client_id.in_([e["telemetry_client_id"] for e in normalized]),
            AzurePtuSubproject.active.is_(True),
        )
    )).scalars().all()
    by_client = {x.telemetry_client_id: x for x in clients}
    return _ingest_normalized(session, normalized, received=len(events), existing=existing, by_client=by_client)


def persist_usage_to_iceberg(events: list[dict[str, Any]]) -> dict[str, int]:
    """Write bronze usage events and gold hourly subproject rollups."""
    counts = {"bronze_events": 0, "gold_subproject": 0}
    if not events:
        return counts
    try:
        import pandas as pd
        from app.services import iceberg_service as iceberg

        now = datetime.now(timezone.utc).isoformat()
        bronze_rows = []
        rollup: dict[tuple[str, str, str, str], dict[str, Any]] = {}

        for event in events:
            occurred = event.get("occurred_at")
            if isinstance(occurred, datetime):
                hour = occurred.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")
                period = occurred.astimezone(timezone.utc).strftime("%Y%m")
            else:
                hour = str(occurred or "")[:13] + ":00:00Z"
                period = hour[:7].replace("-", "")
            bronze_rows.append({
                **{k: event.get(k) for k in USAGE_EVENT_FIELDS},
                "ingestion_status": "accepted",
                "metric_hour": hour,
                "received_at": now,
                "_billing_period": period,
            })
            if not event.get("subproject_key"):
                continue
            key = (
                str(event.get("project_key") or ""),
                str(event.get("subproject_key") or ""),
                str(event.get("deployment_name") or ""),
                hour,
            )
            bucket = rollup.setdefault(key, {
                "project_key": event.get("project_key"),
                "subproject_key": event.get("subproject_key"),
                "deployment_name": event.get("deployment_name"),
                "model_name": event.get("model_name"),
                "metric_hour": hour,
                "request_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "spillover_request_count": 0,
                "_billing_period": period,
            })
            bucket["request_count"] += int(event.get("request_count") or 0)
            bucket["input_tokens"] += int(event.get("input_tokens") or 0)
            bucket["output_tokens"] += int(event.get("output_tokens") or 0)
            bucket["total_tokens"] += int(event.get("total_tokens") or 0)
            if event.get("is_spillover"):
                bucket["spillover_request_count"] += int(event.get("request_count") or 0)

        if bronze_rows:
            counts["bronze_events"] = iceberg.write_azure_ptu_usage_events(pd.DataFrame(bronze_rows))
        if rollup:
            counts["gold_subproject"] = iceberg.write_azure_ptu_subproject_usage(pd.DataFrame(list(rollup.values())))
    except Exception as exc:
        logger.warning("Azure PTU usage Iceberg persist skipped: %s", exc)
    return counts
