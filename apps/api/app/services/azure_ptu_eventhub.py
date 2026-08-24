"""Event Hub consumer with Postgres partition checkpoints."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.database import SyncSession
from app.models.azure_ptu import AzurePtuCheckpoint
from app.models.source_connection import SourceConnection
from app.services.azure_ptu_ingest import ingest_usage_event_dicts
from app.services.azure_ptu_service import FAILURE_ALERT_THRESHOLD, record_checkpoint_result, resolve_config

logger = logging.getLogger(__name__)

DEFAULT_CONSUMER_GROUP = "openfinops-azure-ptu"


def _hub_config(config: dict[str, Any]) -> dict[str, Any]:
    hub = config.get("eventhub") if isinstance(config.get("eventhub"), dict) else {}
    return hub or {}


def _connection_string(config: dict[str, Any]) -> str | None:
    if config.get("eventhub_connection_string"):
        return str(config["eventhub_connection_string"])
    hub = _hub_config(config)
    return hub.get("connection_string") or hub.get("eventhub_connection_string")


def _build_consumer(config: dict[str, Any]):
    try:
        from azure.eventhub import EventHubConsumerClient
    except ImportError as exc:
        raise RuntimeError("azure-eventhub package is required for Event Hub ingestion") from exc

    hub = _hub_config(config)
    namespace = hub.get("namespace")
    eventhub_name = hub.get("eventhub_name")
    consumer_group = hub.get("consumer_group") or DEFAULT_CONSUMER_GROUP
    conn_str = _connection_string(config)

    if conn_str:
        return EventHubConsumerClient.from_connection_string(
            conn_str=conn_str,
            consumer_group=consumer_group,
            eventhub_name=eventhub_name,
        ), eventhub_name

    if not namespace or not eventhub_name:
        raise ValueError("Event Hub namespace and eventhub_name are required")

    from azure.eventhub import EventHubConsumerClient
    from app.services.azure_ptu_service import _credential

    fqdn = namespace if str(namespace).endswith(".servicebus.windows.net") else f"{namespace}.servicebus.windows.net"
    return EventHubConsumerClient(
        fully_qualified_namespace=fqdn,
        eventhub_name=eventhub_name,
        consumer_group=consumer_group,
        credential=_credential(resolve_config(config)),
    ), eventhub_name


def _get_checkpoint(session, connection_id: int, partition_id: str) -> AzurePtuCheckpoint:
    row = session.execute(
        select(AzurePtuCheckpoint).where(
            AzurePtuCheckpoint.connection_id == connection_id,
            AzurePtuCheckpoint.partition_id == partition_id,
        )
    ).scalar_one_or_none()
    if row:
        return row
    row = AzurePtuCheckpoint(connection_id=connection_id, partition_id=partition_id)
    session.add(row)
    session.flush()
    return row


def _parse_events(body: bytes | str) -> list[dict[str, Any]]:
    text = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else str(body)
    payload = json.loads(text)
    if isinstance(payload, dict) and "events" in payload:
        events = payload["events"]
    elif isinstance(payload, list):
        events = payload
    elif isinstance(payload, dict):
        events = [payload]
    else:
        return []
    return [e for e in events if isinstance(e, dict)]


def consume_connection_eventhub(connection_id: int, *, max_wait: float = 5.0, max_batch: int = 100) -> dict[str, Any]:
    """Receive one batch per partition and advance Postgres checkpoints."""
    session = SyncSession()
    summary = {"connection_id": connection_id, "partitions": 0, "events": 0, "accepted": 0, "quarantined": 0, "duplicates": 0, "status": "SKIPPED"}
    try:
        conn = session.get(SourceConnection, connection_id)
        if not conn or conn.connector_type != "azure_openai_ptu":
            summary["status"] = "SKIPPED"
            summary["message"] = "Not an azure_openai_ptu connection"
            return summary

        cfg = resolve_config(dict(conn.config_json or {}))
        hub = _hub_config(cfg)
        if not hub.get("namespace") and not _connection_string(cfg):
            summary["message"] = "Event Hub not configured"
            return summary
        if not hub.get("eventhub_name") and not _connection_string(cfg):
            summary["message"] = "Event Hub name missing"
            return summary

        client, _ = _build_consumer(cfg)
        try:
            partition_ids = client.get_partition_ids()
            summary["partitions"] = len(partition_ids)

            for partition_id in partition_ids:
                checkpoint = _get_checkpoint(session, connection_id, str(partition_id))
                start_position = None
                if checkpoint.offset:
                    from azure.eventhub import EventPosition
                    start_position = EventPosition.from_offset(checkpoint.offset, checkpoint.sequence_number)

                batch_events: list[dict[str, Any]] = []
                last_offset = checkpoint.offset
                last_seq = checkpoint.sequence_number

                def on_event(partition_context, event):
                    nonlocal last_offset, last_seq
                    if event is None:
                        return
                    last_offset = event.offset
                    last_seq = event.sequence_number
                    try:
                        batch_events.extend(_parse_events(event.body_as_str()))
                    except json.JSONDecodeError:
                        logger.info("Skipping non-JSON Event Hub payload on partition %s", partition_id)

                try:
                    client.receive(
                        on_event=on_event,
                        partition_id=partition_id,
                        starting_position=start_position,
                        max_wait_time=max_wait,
                    )
                except Exception as exc:
                    record_checkpoint_result(
                        session, connection_id, str(partition_id),
                        success=False, message=str(exc)[:500],
                    )
                    session.commit()
                    refreshed = _get_checkpoint(session, connection_id, str(partition_id))
                    if refreshed.consecutive_failures >= FAILURE_ALERT_THRESHOLD:
                        summary["alert"] = True
                    continue

                if batch_events:
                    ingest = ingest_usage_event_dicts(session, batch_events[:max_batch])
                    summary["events"] += ingest.received
                    summary["accepted"] += ingest.accepted
                    summary["quarantined"] += ingest.quarantined
                    summary["duplicates"] += ingest.duplicates

                record_checkpoint_result(
                    session, connection_id, str(partition_id),
                    success=True,
                    offset=last_offset,
                    sequence_number=last_seq,
                    message=f"events={len(batch_events)}",
                )
                session.commit()
        finally:
            client.close()

        summary["status"] = "OK"
        return summary
    except Exception as exc:
        session.rollback()
        logger.warning("Event Hub consume failed for connection %s: %s", connection_id, exc)
        record_checkpoint_result(session, connection_id, "eventhub", success=False, message=str(exc)[:500])
        session.commit()
        summary["status"] = "FAILED"
        summary["message"] = str(exc)[:500]
        return summary
    finally:
        session.close()


def consume_all_ptu_eventhubs() -> list[dict[str, Any]]:
    session = SyncSession()
    try:
        rows = session.execute(
            select(SourceConnection.id).where(SourceConnection.connector_type == "azure_openai_ptu")
        ).scalars().all()
    finally:
        session.close()
    return [consume_connection_eventhub(conn_id) for conn_id in rows]
