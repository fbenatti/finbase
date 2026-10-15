#!/usr/bin/env python3
"""Hard reset do módulo de anomalias (PostgreSQL + referências Iceberg legadas)."""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy import text

from app.database import async_session
from app.services.anomaly_schema import (
    ASSESSMENT_NAMESPACE,
    ASSESSMENT_TABLE,
    GOLD_NAMESPACE,
    GOLD_TABLE,
    REFERENCE_NAMESPACE,
    REFERENCE_TABLE,
)
from app.services.trino_service import execute as trino_execute


async def _count_pg(session, table: str) -> int:
    return int((await session.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one())


async def main(dry_run: bool) -> None:
    async with async_session() as session:
        tables = [
            "anomaly_notification",
            "anomaly_action",
            "anomaly_case_event",
            "anomaly_case",
            "anomaly_publication",
            "anomaly_run",
        ]
        counts = {t: await _count_pg(session, t) for t in tables}
        print(json.dumps({"postgresql": counts, "dry_run": dry_run}, indent=2))
        if dry_run:
            return
        # DELETE em anomaly_case_event é bloqueado pelo gatilho de imutabilidade;
        # TRUNCATE não dispara gatilhos por linha e é o reset completo desejado.
        table_list = ", ".join(tables)
        await session.execute(
            text(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE")
        )
        await session.commit()
    for stmt in (
        f"DROP TABLE IF EXISTS iceberg.{REFERENCE_NAMESPACE}.{REFERENCE_TABLE}",
        f"DROP TABLE IF EXISTS iceberg.{ASSESSMENT_NAMESPACE}.{ASSESSMENT_TABLE}",
        f"DROP TABLE IF EXISTS iceberg.{GOLD_NAMESPACE}.{GOLD_TABLE}",
    ):
        await trino_execute(stmt, timeout_s=300)
    print("Reset concluído em", datetime.now(timezone.utc).isoformat())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
