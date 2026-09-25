"""90-day retention for daily Azure metric partitions only.

Snapshot expiration is deliberately separate: row deletion does not reclaim old
files until a later, independently reviewed Iceberg maintenance operation.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.trino_service import execute

SILVER_TABLES = {
    "monitor_vm_metrics": "resource_metric_series",
    "container_insights": "container_metric_series",
}


def metric_retention_cutoff(today_utc=None) -> str:
    """First of the last 90 complete UTC days, YYYYMMDD."""
    day = today_utc or datetime.now(timezone.utc).date()
    return (day - timedelta(days=90)).strftime("%Y%m%d")


def _run(sql: str) -> list[dict[str, Any]]:
    return asyncio.run(execute(sql, timeout_s=300, max_retries=3))


def _safe_name(name: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_]+", name):
        raise ValueError("Unsafe Iceberg table name")
    return name


def preview_metric_retention(session, *, today_utc=None) -> dict[str, Any]:
    """Read-only preview grouped by table/day, connection and row count."""
    from sqlalchemy import select
    from app.models.azure_connection_source import AzureConnectionSource
    from app.models.source_connection import SourceConnection
    from app.services.iceberg_service import list_tables
    cutoff = metric_retention_cutoff(today_utc)
    selections = session.execute(select(AzureConnectionSource).where(
        AzureConnectionSource.enabled.is_(True),
        AzureConnectionSource.source_key.in_(tuple(SILVER_TABLES)),
    )).scalars().all()
    tables: list[dict[str, Any]] = []
    for selection in selections:
        connection = session.get(SourceConnection, selection.connection_id)
        if not connection or connection.cloud_scope != "azure":
            continue
        from app.services.azure_source_registry import source_code
        bronze = _safe_name("raw_" + source_code(connection.code, selection.source_key).replace("-", "_").replace(".", "_"))
        silver = SILVER_TABLES[selection.source_key]
        bronze_counts = []
        if bronze in set(list_tables("bronze")):
            bronze_counts = _run(
                f"SELECT _billing_period AS day, count(*) AS records FROM iceberg.bronze.{bronze} "
                f"WHERE regexp_like(_billing_period, '^[0-9]{{8}}$') AND _billing_period < '{cutoff}' "
                "GROUP BY 1 ORDER BY 1"
            )
        silver_counts = _run(
            f"SELECT _billing_period AS day, count(*) AS records FROM iceberg.silver.{silver} "
            f"WHERE _source_connection_id = {int(connection.id)} AND _silver_owner = '{selection.source_key}' "
            f"AND regexp_like(_billing_period, '^[0-9]{{8}}$') AND _billing_period < '{cutoff}' "
            "GROUP BY 1 ORDER BY 1"
        )
        if bronze in set(list_tables("bronze")):
            tables.append({"layer": "bronze", "table": bronze, "connection_id": connection.id,
                           "source_key": selection.source_key, "days": bronze_counts})
        tables.append({"layer": "silver", "table": silver, "connection_id": connection.id,
                       "source_key": selection.source_key, "days": silver_counts})
    distinct_tables = sorted({(item["layer"], item["table"]) for item in tables})
    return {"cutoff_day": cutoff, "window_days": 90, "tables": tables,
            "snapshot_maintenance": {
                "executed": False,
                "plan": [
                    f"ALTER TABLE iceberg.{layer}.{table} EXECUTE expire_snapshots(retention_threshold => '7d')"
                    for layer, table in distinct_tables
                ] + [
                    f"ALTER TABLE iceberg.{layer}.{table} EXECUTE remove_orphan_files(retention_threshold => '7d')"
                    for layer, table in distinct_tables
                ],
                "note": "Rodar em manutenção separada, após respeitar a janela mínima do catálogo e backups; exclusão de linhas não libera arquivos imediatamente.",
            }}


def apply_metric_retention(session, *, today_utc=None) -> dict[str, Any]:
    """Preview, delete only scoped daily rows under the warehouse writer lock."""
    from sqlalchemy import delete, update
    from app.models.ingested_blob import IngestedBlob
    from app.models.metric_daily_collection import MetricDailyCollection
    from app.services.write_lock import exclusive_write_lock

    preview = preview_metric_retention(session, today_utc=today_utc)
    cutoff = preview["cutoff_day"]
    with exclusive_write_lock():
        # First apply Iceberg row deletes. Metadata is changed only after every
        # targeted predicate is confirmed empty.
        for item in preview["tables"]:
            table = _safe_name(item["table"])
            if item["layer"] == "bronze":
                predicate = (f"regexp_like(_billing_period, '^[0-9]{{8}}$') "
                             f"AND _billing_period < '{cutoff}'")
                check_scope = ""
            else:
                predicate = (f"_source_connection_id = {int(item['connection_id'])} "
                             f"AND _silver_owner = '{item['source_key']}' "
                             f"AND regexp_like(_billing_period, '^[0-9]{{8}}$') "
                             f"AND _billing_period < '{cutoff}'")
                check_scope = f"AND _source_connection_id = {int(item['connection_id'])} AND _silver_owner = '{item['source_key']}'"
            _run(f"DELETE FROM iceberg.{item['layer']}.{table} WHERE {predicate}")
            remaining = _run(
                f"SELECT count(*) AS n FROM iceberg.{item['layer']}.{table} "
                f"WHERE regexp_like(_billing_period, '^[0-9]{{8}}$') AND _billing_period < '{cutoff}' {check_scope}"
            )
            if int(remaining[0]["n"] or 0):
                raise RuntimeError(f"Retention verification failed for {item['layer']}.{table}")
        # Source-scoped ingestion metadata and persistent daily state follow the
        # confirmed Iceberg operations. Monthly YYYYMM rows cannot match YYYYMMDD.
        for connection_id, source_key in sorted({(item["connection_id"], item["source_key"]) for item in preview["tables"]}):
            prefix = "monitor" if source_key == "monitor_vm_metrics" else "azure_container_insights"
            session.execute(delete(IngestedBlob).where(
                IngestedBlob.connection_id == connection_id,
                IngestedBlob.blob_prefix == prefix,
                IngestedBlob.billing_period < cutoff,
                IngestedBlob.billing_period.op("~")("^[0-9]{8}$"),
            ))
            session.execute(update(MetricDailyCollection).where(
                MetricDailyCollection.connection_id == connection_id,
                MetricDailyCollection.source_key == source_key,
                MetricDailyCollection.day_utc < cutoff,
            ).values(status="EXPIRED", reason=f"Removido da cópia local pela retenção de 90 dias (corte {cutoff})."))
        session.commit()
    return {**preview, "applied": True}
