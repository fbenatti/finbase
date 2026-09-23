#!/usr/bin/env python3
"""Repair old scheduled Gold tasks created before partition publishing.

Usage (from ``apps/api``)::

    PYTHONPATH=. python scripts/repair_queued_partition_reprocesses.py
    PYTHONPATH=. python scripts/repair_queued_partition_reprocesses.py --apply

The default is a dry run.  Only queued scheduled-pipeline tasks scoped to one
connection and one billing period are changed; broad/manual reprocesses retain
their existing full-merge semantics.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Keep this registration list aligned with worker.py.  SQLAlchemy must know all
# referenced tables before a flush in deployments where imports are otherwise
# lazy.
from app.database import SyncSession  # noqa: E402
from app.models.etl_task import EtlTask  # noqa: E402
from app.models.destination import DestinationTable, DestinationColumn, SourceDestinationMapping  # noqa: F401,E402
from app.models.source_connection import SourceConnection  # noqa: F401,E402
from app.models.ingested_blob import IngestedBlob  # noqa: F401,E402
from app.models.rules import BusinessRule, ClassificationRule, Department  # noqa: F401,E402
from app.models.skipped_blob import SkippedBlob  # noqa: F401,E402
from app.models.fact_cost import FactCost  # noqa: F401,E402
from app.models.period_status import PeriodStatus  # noqa: F401,E402
from app.models.forecast import ForecastConfig, ForecastAdjustment, ForecastBusinessRule, ForecastRun  # noqa: F401,E402
from app.models.azure_ptu import AzurePtuProjectMapping, AzurePtuSubproject  # noqa: F401,E402
from sqlalchemy import select  # noqa: E402


def is_eligible(task: EtlTask) -> bool:
    payload = task.result_json if isinstance(task.result_json, dict) else {}
    return (
        task.status == "QUEUED"
        and task.task_type == "reprocess"
        and payload.get("scheduled_pipeline") is True
        and payload.get("publish_by_partition") is not True
        and len(task.connection_ids or []) == 1
        and len(task.billing_periods or []) == 1
        and task.destination_table_id is not None
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="persist the eligible updates")
    args = parser.parse_args()

    session = SyncSession()
    try:
        queued = session.execute(select(EtlTask).where(
            EtlTask.status == "QUEUED", EtlTask.task_type == "reprocess",
        )).scalars().all()
        eligible = [task for task in queued if is_eligible(task)]
        for task in eligible:
            print(
                f"task={task.id} destination={task.destination_table_id} "
                f"connection={task.connection_ids[0]} period={task.billing_periods[0]}"
            )
            if args.apply:
                task.result_json = {**dict(task.result_json or {}), "publish_by_partition": True}
        if args.apply:
            session.commit()
            print(f"Updated {len(eligible)} queued task(s).")
        else:
            print(f"Dry run: {len(eligible)} queued task(s) eligible. Re-run with --apply to update.")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
