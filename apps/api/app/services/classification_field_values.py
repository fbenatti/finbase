"""Classification field-value reference — distinct values per destination table / cloud / field.

The Gold tables stay the source of truth. This Postgres reference is a small
derived index rebuilt at the end of each destination reprocess (from the
in-memory DataFrame, no extra scan) and optionally backfilled from Trino.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import async_session
from app.services import trino_service
from app.services.cloud_scope import connection_cloud_scope

logger = logging.getLogger(__name__)

MAX_VALUES_PER_FIELD = 500
_SKIP_COLUMNS = {"_billing_period", "source_connection_id", "cloud_origem", "id", "created_at", "updated_at"}


def _norm_cloud(cloud: str | None) -> str:
    value = str(cloud or "").strip().lower()
    if not value:
        return ""
    scope = connection_cloud_scope(value)
    return scope or value.split("-", 1)[0]


def _safe_column(name: str) -> str:
    """Return an identifier safe to embed in SQL: raw when simple, quoted otherwise."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return name
    return '"' + name.replace('"', '""') + '"'


def enrich_from_dataframe(session: Session, destination_table_id: int, cloud: str, df) -> int:
    """Upsert distinct values found in an in-memory gold slice (called after each gold write)."""
    if df is None or df.empty:
        return 0
    cloud_norm = _norm_cloud(cloud)
    if not cloud_norm:
        return 0
    rows: list[dict] = []
    for column in df.columns:
        name = str(column)
        if name.lower() in _SKIP_COLUMNS:
            continue
        series = df[column]
        if getattr(series.dtype, "kind", "O") in "bifc":
            continue
        values = series.dropna().astype(str).str.strip()
        values = values[(values != "") & (values != "None") & (values != "nan")]
        if values.empty:
            continue
        for value, count in values.value_counts().head(MAX_VALUES_PER_FIELD).items():
            rows.append({
                "destination_table_id": destination_table_id,
                "cloud": cloud_norm,
                "field": name[:200],
                "value": str(value)[:500],
                "count": int(count),
            })
    if not rows:
        return 0
    _upsert_rows_sync(session, rows)
    return len(rows)


def _upsert_rows_sync(session: Session, rows: Iterable[dict]) -> None:
    statement = text(
        """
        INSERT INTO classification_field_value (destination_table_id, cloud, field, value, count, updated_at)
        VALUES (:destination_table_id, :cloud, :field, :value, :count, now())
        ON CONFLICT (destination_table_id, cloud, field, value)
        DO UPDATE SET count = classification_field_value.count + EXCLUDED.count, updated_at = now()
        """
    )
    batch: list[dict] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= 2000:
            session.execute(statement, batch)
            batch.clear()
    if batch:
        session.execute(statement, batch)
    session.commit()


async def _rule_fields(session, destination_table_id: int) -> list[str]:
    """Fields actually referenced by classification rules for this table."""
    rows = (
        await session.execute(
            text(
                "SELECT source_field, conditions FROM classification_rule "
                "WHERE destination_table_id = :id"
            ),
            {"id": destination_table_id},
        )
    ).all()
    fields: set[str] = set()
    for source_field, conditions in rows:
        if source_field:
            fields.add(str(source_field))
        if isinstance(conditions, list):
            for cond in conditions:
                if isinstance(cond, dict) and cond.get("field"):
                    fields.add(str(cond["field"]))
    return sorted(fields)


async def refresh_destination_values(
    destination_table_id: int | None = None,
    fields: list[str] | None = None,
) -> dict:
    """Backfill the reference from Gold via Trino (manual refresh, targeted by fields)."""
    refreshed: list[int] = []
    async with async_session() as session:
        if destination_table_id is not None:
            tables = (
                await session.execute(
                    text("SELECT id, physical_name FROM destination_table WHERE id = :id"),
                    {"id": destination_table_id},
                )
            ).all()
        else:
            tables = (
                await session.execute(
                    text("SELECT id, physical_name FROM destination_table ORDER BY id")
                )
            ).all()
        for table_id, physical_name in tables:
            try:
                await _refresh_table(session, int(table_id), str(physical_name), fields)
                refreshed.append(int(table_id))
            except Exception as exc:  # noqa: BLE001
                logger.exception("Field-value refresh failed for table %s: %s", physical_name, exc)
    return {"status": "done", "refreshed": refreshed}


async def _refresh_table(
    session,
    destination_table_id: int,
    physical_name: str,
    fields: list[str] | None = None,
) -> None:
    safe = physical_name.replace("-", "_").replace(".", "_")
    quoted = '"' + safe.replace('"', '""') + '"'
    table_ref = f"iceberg.gold.{quoted}"

    scan_fields = [f.strip() for f in fields if f and f.strip()] if fields else await _rule_fields(session, destination_table_id)
    if not scan_fields:
        logger.info("No classification fields to refresh for table %s", physical_name)
        return

    async def scan_one(column: str) -> list[dict]:
        col_name = _safe_column(str(column))
        sql = (
            f"SELECT cloud_origem AS cloud, {col_name} AS value, COUNT(*) AS c "
            f"FROM {table_ref} "
            f"WHERE {col_name} IS NOT NULL AND TRIM({col_name}) <> '' "
            f"GROUP BY 1, 2 ORDER BY 3 DESC LIMIT {MAX_VALUES_PER_FIELD}"
        )
        try:
            result = await trino_service.execute(sql, timeout_s=180.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Field-value scan failed for %s.%s: %s", physical_name, column, exc)
            return []
        return [
            {
                "destination_table_id": destination_table_id,
                "cloud": _norm_cloud(row.get("cloud")),
                "field": str(column)[:200],
                "value": str(row.get("value") or "")[:500],
                "count": int(row.get("c") or 0),
            }
            for row in result
        ]

    semaphore = asyncio.Semaphore(2)
    async def limited(column: str):
        async with semaphore:
            try:
                return await scan_one(column)
            finally:
                await asyncio.sleep(0.3)  # gentle with Trino

    scanned = await asyncio.gather(*(limited(c) for c in scan_fields[:40]))
    new_rows = [row for rows in scanned for row in rows]
    new_rows = [r for r in new_rows if r["cloud"] and r["value"]]
    await session.execute(
        text("DELETE FROM classification_field_value WHERE destination_table_id = :id"),
        {"id": destination_table_id},
    )
    if new_rows:
        statement = text(
            """
            INSERT INTO classification_field_value (destination_table_id, cloud, field, value, count, updated_at)
            VALUES (:destination_table_id, :cloud, :field, :value, :count, now())
            ON CONFLICT (destination_table_id, cloud, field, value)
            DO UPDATE SET count = classification_field_value.count + EXCLUDED.count, updated_at = now()
            """
        )
        for row in new_rows:
            await session.execute(statement, row)
    await session.commit()
