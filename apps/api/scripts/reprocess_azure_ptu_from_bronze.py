"""Rebuild hourly PTU Gold from the latest copy of each Bronze metric hour.

Run with PYTHONPATH=apps/api and the normal catalog environment configured.
The Azure Monitor collector appends audit copies to Bronze, so summing Bronze
directly would multiply token counts on every reread.
"""
from __future__ import annotations

import asyncio
import sys

import pandas as pd
from sqlalchemy import select

from app.database import SyncSession
from app.models.source_connection import SourceConnection
from app.services import iceberg_service
from app.services.azure_ptu_gold import refresh_azure_ptu_gold
from app.services.trino_service import execute


async def rebuild(connection_code: str) -> dict[str, object]:
    prefix = "raw_" + connection_code.replace("-", "_") + "_ptu"
    tables = {row["Table"] for row in await execute("SHOW TABLES FROM iceberg.bronze")}
    names = [name for name in (prefix, prefix + "_metrics") if name in tables]
    if not names:
        raise RuntimeError(f"Nenhuma Bronze PTU para {connection_code}")
    rows = []
    bronze_rows = 0
    for name in names:
        rows.extend(await execute(
            f"""
            SELECT * FROM (
              SELECT *, ROW_NUMBER() OVER (
                PARTITION BY connection_id, account_resource_id, deployment_name,
                             model_name, model_version, region, sku_name, metric_hour
                ORDER BY collected_at DESC, _billing_period DESC
              ) AS _row_number
              FROM iceberg.bronze.{name}
            ) WHERE _row_number = 1
            """,
            timeout_s=180,
        ))
        bronze_rows += int((await execute(f"SELECT count(*) AS n FROM iceberg.bronze.{name}"))[0]["n"])
    if not rows:
        raise RuntimeError(f"Bronze PTU de {connection_code} vazia")
    frame = pd.DataFrame(rows).drop(
        columns=["_row_number", "window_start", "raw_dimensions", "_blob_path", "_ingested_at"],
        errors="ignore",
    )
    frame["connection_id"] = pd.to_numeric(frame["connection_id"])
    with SyncSession() as session:
        current_id = session.execute(
            select(SourceConnection.id).where(SourceConnection.code == connection_code)
        ).scalar_one()
    legacy_ids = sorted(set(frame["connection_id"].dropna().astype(int)) - {current_id})
    # The legacy PTU connection was removed when sources moved under the Azure
    # environment connection. Its numeric ID must not create a second copy.
    identity = ["account_resource_id", "deployment_name", "metric_hour"]
    frame = frame.sort_values("collected_at").drop_duplicates(identity, keep="last")
    frame["connection_id"] = current_id
    frame["_billing_period"] = frame["metric_hour"].astype(str).str[:7].str.replace("-", "", regex=False)
    written = iceberg_service.upsert_azure_ptu_hourly(frame)
    if legacy_ids:
        from pyiceberg.expressions import EqualTo

        table = iceberg_service._get_catalog().load_table(("gold", "dest_azure_ptu_metrics"))
        for legacy_id in legacy_ids:
            table.delete(EqualTo("connection_id", legacy_id))
            table = iceberg_service._get_catalog().load_table(("gold", "dest_azure_ptu_metrics"))
    daily = await refresh_azure_ptu_gold(days=400)
    return {
        "bronze_rows": bronze_rows,
        "distinct_hours": len(frame),
        "hourly_written": written,
        "daily": daily,
        "latest_hour": str(frame["metric_hour"].max()),
        "legacy_connection_ids_removed": legacy_ids,
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: reprocess_azure_ptu_from_bronze.py CONNECTION_CODE")
    print(asyncio.run(rebuild(sys.argv[1])))
