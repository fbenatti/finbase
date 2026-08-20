"""Gold FROM helper for dashboards.

Query-time D-n hiding of recent realizado is disabled: it full-scanned Gold,
stalled Trino, and blocked the stack. Forecast still overwrites overlapping days.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class RealizedCutoffPolicy:
    cutoff_days: int
    date_column: str
    period_column: str | None
    type_column: str | None
    interval_rules: list[dict[str, Any]]
    global_interval: bool


def realized_cutoff_source(
    physical_name: str,
    schema: dict[str, str],
    policy: RealizedCutoffPolicy | None,
) -> str:
    del schema, policy
    return f"iceberg.gold.{physical_name}"


async def fetch_realized_cutoff_policy(
    db: AsyncSession,
    destination_table_id: int,
) -> RealizedCutoffPolicy | None:
    del db, destination_table_id
    return None
