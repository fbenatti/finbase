"""Daily, aggregate-only saving measurements over Gold tables."""
from datetime import date, datetime, timedelta, timezone
import re
from sqlalchemy import select
from app.models.saving import SavingAction, SavingMeasurement
from app.models.destination import DestinationTable
from app.services.trino_service import execute_sync

SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
OPS = {"IN", "NOT_IN", "CONTAINS", "NOT_CONTAINS"}

def _ident(value: str) -> str:
    if not SAFE.match(value): raise ValueError(f"Coluna inválida: {value}")
    return f'"{value}"'

def _literal(value): return "'" + str(value).replace("'", "''") + "'"

def _where(action):
    clauses = []
    for item in action.filters or []:
        col, op, values = _ident(str(item.get("column", ""))), item.get("operator", "IN"), item.get("values", [])
        if op not in OPS or not values: raise ValueError("Filtro de saving inválido")
        vals = ", ".join(_literal(v) for v in values)
        if op == "IN": clauses.append(f"CAST({col} AS VARCHAR) IN ({vals})")
        elif op == "NOT_IN": clauses.append(f"CAST({col} AS VARCHAR) NOT IN ({vals})")
        elif op == "CONTAINS": clauses.append("(" + " OR ".join("LOWER(CAST(%s AS VARCHAR)) LIKE LOWER(%s)" % (col, _literal("%" + str(v) + "%")) for v in values) + ")")
        else: clauses.append("(" + " AND ".join("LOWER(CAST(%s AS VARCHAR)) NOT LIKE LOWER(%s)" % (col, _literal("%" + str(v) + "%")) for v in values) + ")")
    return " AND ".join(clauses) if clauses else "1=1"

def refresh_actions(session, today: date | None = None):
    today = today or date.today(); actions = session.execute(select(SavingAction).where(SavingAction.status.in_(["EM_FILA", "EM_ANALISE"]))).scalars().all()
    for action in actions:
        try:
            start = date.fromisoformat(action.start_date); end = start + timedelta(days=action.duration_days - 1)
            if today < start: action.status = "EM_FILA"; continue
            action.status = "EM_ANALISE"
            dest = session.get(DestinationTable, action.destination_table_id)
            if not dest: raise ValueError("Tabela GOLD não encontrada")
            first = start
            existing = {m.measured_date for m in session.execute(select(SavingMeasurement).where(SavingMeasurement.action_id == action.id)).scalars()}
            day = first
            while day <= min(today, end):
                key = day.isoformat()
                if key not in existing:
                    sql = f"SELECT COALESCE(SUM(TRY_CAST({_ident(action.cost_column)} AS DOUBLE)), 0) AS cost FROM iceberg.gold.{dest.physical_name.replace('-', '_').replace('.', '_')} WHERE CAST({_ident(action.date_column)} AS DATE) = DATE '{key}' AND {_where(action)}"
                    cost = float((execute_sync(sql, timeout_s=90) or [{"cost": 0}])[0].get("cost") or 0)
                    baseline = float(action.baseline_monthly) / 30
                    projected = float(action.projected_monthly) / 30
                    session.add(SavingMeasurement(action_id=action.id, measured_date=key, actual_cost=cost, baseline_value=baseline, projected_saving=projected, realized_saving=baseline-cost))
                day += timedelta(days=1)
            action.last_measured_at = datetime.now(timezone.utc)
            if today >= end:
                rows = session.execute(select(SavingMeasurement).where(SavingMeasurement.action_id == action.id)).scalars().all()
                action.final_realized = sum(r.realized_saving for r in rows); action.final_projected = sum(r.projected_saving for r in rows)
                action.outcome = "SAVING" if action.final_realized > 0.005 else "CRESCIMENTO_DE_CUSTO" if action.final_realized < -0.005 else "NEUTRO"
                action.status = "FINALIZADA"; action.completed_at = datetime.now(timezone.utc)
        except Exception as exc:
            action.status = "ERRO"; action.error_message = str(exc)
    session.commit()
