#!/usr/bin/env python3
"""Inline Dimon materialization with the centro_custo dimension.

Runs the same pipeline the worker executes for dimon_reprocess tasks, but in
this process (so module reload is not required). Creates its own EtlTask row
and marks it COMPLETED/FAILED at the end.
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import SyncSession  # noqa: E402
from app.models.etl_task import EtlTask  # noqa: E402
# Pré-importa todos os modelos para popular o metadata do SQLAlchemy
# (mesmo procedimento do worker.py) antes de usar SyncSession.
from app.models.destination import DestinationTable, DestinationColumn, SourceDestinationMapping  # noqa: E402
from app.models.source_connection import SourceConnection  # noqa: E402
from app.models.ingested_blob import IngestedBlob  # noqa: E402
from app.models.rules import BusinessRule, ClassificationRule, Department  # noqa: E402
from app.models.skipped_blob import SkippedBlob  # noqa: E402
from app.models.fact_cost import FactCost  # noqa: E402
from app.models.period_status import PeriodStatus  # noqa: E402
from app.models.forecast import ForecastConfig, ForecastAdjustment, ForecastBusinessRule, ForecastRun  # noqa: E402
from app.models.dimon_rule import DimonRule, DimonRuleSource  # noqa: E402
from app.services.dimon_materialization import materialize_dimons  # noqa: E402

s = SyncSession()
task = EtlTask(task_type="dimon_reprocess", status="RUNNING", errors=[])
task.phase = "Inline: materializando Dimons com centro_custo"
task.started_at = datetime.now(timezone.utc)
s.add(task)
s.flush()
task_id = task.id
s.commit()
print(f"task_id={task_id}", flush=True)


def progress(done, total, name):
    print(f"  [{done}/{total}] {name}", flush=True)
    cur = s.get(EtlTask, task_id)
    if cur:
        cur.processed_blobs = done
        cur.total_blobs = total
        cur.phase = f"Materializando {done}/{total}: {name}"
        s.commit()


try:
    result = asyncio.run(materialize_dimons(s, run_id=task_id, progress=progress))
    failed = list(result.get("failed") or [])
    rows = int(result.get("rows") or 0)
    cur = s.get(EtlTask, task_id)
    if cur:
        cur.status = "COMPLETED" if not failed or rows else "FAILED"
        cur.phase = f"Dimon publicada: {rows} linhas" + (f" · {len(failed)} regra(s) com erro" if failed else "")
        cur.rows_destination = rows
        cur.processed_blobs = int(result.get("rules") or 0)
        cur.errors = [f.get("error", "") for f in failed] or None
        cur.finished_at = datetime.now(timezone.utc)
        cur.result_json = {**(cur.result_json or {}), **result}
        s.commit()
    print("RESULTADO:", json.dumps(result, ensure_ascii=False, default=str)[:3000], flush=True)
except Exception as exc:
    print("ERRO_GERAL:", str(exc), flush=True)
    cur = s.get(EtlTask, task_id)
    if cur:
        cur.status = "FAILED"
        cur.phase = f"Erro inline: {str(exc)[:160]}"
        cur.errors = [str(exc)]
        cur.finished_at = datetime.now(timezone.utc)
        s.commit()
    raise
finally:
    s.close()
