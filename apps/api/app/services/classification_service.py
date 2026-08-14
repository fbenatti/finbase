"""Materialized, immutable classification builds and publication metadata."""
from __future__ import annotations
import asyncio, hashlib, json, logging
from datetime import datetime, timezone
from sqlalchemy import select, func
from app.database import SyncSession
from app.models.classification import (FinancialBaseVersion, ClassificationRulesetSnapshot,
    ClassificationVersion, ClassificationPublication)
from app.models.destination import DestinationTable, DestinationColumn
from app.models.rules import ClassificationRule, Department, Project
from app.services import iceberg_service as iceberg
from app.services.task_runner import _apply_classifications_sync

logger = logging.getLogger(__name__)

def _build_classification_sql(version, base, session) -> tuple[int, int, list[str]]:
    """Execute classification in Trino; no detailed financial rows enter pandas."""
    from app.services.trino_service import execute
    from app.services.classification_sql import build_ctas
    snap = session.get(ClassificationRulesetSnapshot, version.ruleset_snapshot_id)
    rules = list(snap.rules_json or [])
    async def run():
        await execute("CREATE SCHEMA IF NOT EXISTS iceberg.classified", timeout_s=120)
        described = await execute(f"DESCRIBE iceberg.financial_base.{base.physical_name}", timeout_s=120)
        columns = [str(row.get("Column") or row.get("column_name") or row.get("column")) for row in described]
        sql = build_ctas(base.physical_name, version.physical_table, columns, rules,
                         (snap.organization_json or {}).get("departments", []), version.id, base.iceberg_snapshot_id)
        await execute(sql, timeout_s=float(__import__('os').getenv("CLASSIFICATION_SQL_TIMEOUT_S", "1800")))
        metrics = await execute(
            f"SELECT count(*) AS rows, count_if(\"departamento\" IS NOT NULL AND trim(CAST(\"departamento\" AS varchar)) <> '') AS classified FROM iceberg.classified.{version.physical_table}",
            timeout_s=300)
        return columns, metrics[0] if metrics else {"rows": 0, "classified": 0}
    columns, metrics = asyncio.run(run())
    return int(metrics.get("rows") or 0), int(metrics.get("classified") or 0), columns

def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()

def _rules_snapshot(session, destination_id):
    rules = session.execute(select(ClassificationRule).where(
        ((ClassificationRule.destination_table_id == destination_id) | (ClassificationRule.destination_table_id.is_(None))),
        ClassificationRule.active == True).order_by(ClassificationRule.priority, ClassificationRule.id)).scalars().all()
    rule_json = [{c.name: getattr(r, c.name) for c in r.__table__.columns if c.name not in {"created_at", "updated_at"}} for r in rules]
    dept_ids = {r.department_id for r in rules if r.department_id}
    proj_ids = {r.project_id for r in rules if r.project_id}
    depts = session.execute(select(Department)).scalars().all()
    projs = session.execute(select(Project)).scalars().all()
    org = {"departments": [{c.name: getattr(d, c.name) for c in d.__table__.columns if c.name not in {"created_at", "updated_at"}} for d in depts],
           "projects": [{c.name: getattr(p, c.name) for c in p.__table__.columns if c.name not in {"created_at", "updated_at"}} for p in projs if p.id in proj_ids]}
    payload = {"rules": rule_json, "organization": org}
    existing = session.execute(select(ClassificationRulesetSnapshot).where(ClassificationRulesetSnapshot.ruleset_hash == _hash(payload))).scalar_one_or_none()
    if existing:
        return existing, rules
    snap = ClassificationRulesetSnapshot(destination_table_id=destination_id, rules_json=rule_json, organization_json=org, ruleset_hash=_hash(payload), classifier_version="classification_engine.v1")
    session.add(snap); session.flush()
    return snap, rules

def build_classification(version_id: int) -> dict:
    session = SyncSession()
    version = session.get(ClassificationVersion, version_id)
    if not version: raise RuntimeError("classification version not found")
    base = session.get(FinancialBaseVersion, version.financial_base_version_id)
    dest = session.get(DestinationTable, version.destination_table_id)
    version.status = "RUNNING"; session.commit()
    try:
        if __import__('os').getenv("CLASSIFICATION_SQL_ENABLED", "true").lower() == "true":
            total, classified, public_columns = _build_classification_sql(version, base, session)
            if total != int(base.row_count or 0):
                raise RuntimeError(f"SQL classification row count mismatch: base={base.row_count}, classified={total}")
            version.row_count = total; version.classified_rows = classified
            version.unclassified_rows = total - classified
            version.status = "READY"; version.metrics_json = {"rows": total, "classified_rows": classified,
                "engine": "trino_sql", "public_columns": public_columns}
            session.commit()
            publish_classification(version.id, session=session)
            return {"success": True, "version_id": version.id, "rows": total, "engine": "trino_sql"}
        rules = session.execute(select(ClassificationRule).where(
            ((ClassificationRule.destination_table_id == dest.id) | (ClassificationRule.destination_table_id.is_(None))), ClassificationRule.active == True
        ).order_by(ClassificationRule.priority, ClassificationRule.id)).scalars().all()
        dept_map = {d.id:d for d in session.execute(select(Department)).scalars().all()}
        proj_map = {p.id:p for p in session.execute(select(Project)).scalars().all()}
        hierarchy = {}
        types = {c.column_name: c.data_type for c in session.execute(select(DestinationColumn).where(DestinationColumn.destination_table_id == dest.id)).scalars().all()}
        temp = None; total = classified = 0; public_columns = []
        # Scan the base in bounded Arrow batches. Classification is still
        # materialized, but never needs the full financial history in RAM.
        for frame in iceberg.read_destination_batches(base.physical_name, batch_size=100_000, namespace="financial_base"):
            _apply_classifications_sync(frame, rules, None, dept_map=dept_map, proj_map=proj_map, hierarchy_cache=hierarchy)
            frame["_classification_version_id"] = version.id
            frame["_financial_base_snapshot_id"] = base.iceberg_snapshot_id
            if not public_columns:
                public_columns = [c for c in frame.columns if c not in {"_classification_version_id", "_financial_base_snapshot_id"}]
            total += len(frame)
            classified += int(frame.get("departamento", frame.iloc[:, 0]).notna().sum()) if len(frame) else 0
            if temp is None:
                temp = iceberg.write_destination_staged(version.physical_table, frame, partition_col="_billing_period", column_types=types, namespace="classified")
            else:
                iceberg.append_to_staged(temp, frame, partition_col="_billing_period", column_types=types, namespace="classified")
        if temp is None:
            raise RuntimeError("financial base is empty; classification was not published")
        rows = iceberg.swap_staged_table(version.physical_table, temp, namespace="classified")
        version.row_count = rows if rows >= 0 else total
        version.classified_rows = classified
        version.unclassified_rows = max(0, version.row_count - version.classified_rows)
        version.status = "READY"; version.metrics_json = {"rows": version.row_count,
            "classified_rows": version.classified_rows,
            "public_columns": public_columns}
        session.commit()
        publish_classification(version.id, session=session)
        return {"success": True, "version_id": version.id, "rows": version.row_count}
    except Exception as exc:
        version.status = "FAILED"; version.error = str(exc); session.commit(); raise
    finally: session.close()

def publish_classification(version_id: int, session=None) -> dict:
    own = session is None
    session = session or SyncSession()
    version = session.get(ClassificationVersion, version_id)
    dest = session.get(DestinationTable, version.destination_table_id)
    previous = session.execute(select(ClassificationVersion).where(ClassificationVersion.destination_table_id == dest.id, ClassificationVersion.status == "ACTIVE")).scalar_one_or_none()
    view = f"iceberg.gold.{dest.physical_name.replace('-', '_').replace('.', '_')}"
    target = f"iceberg.classified.{version.physical_table}"
    public_columns = (version.metrics_json or {}).get("public_columns") or []
    projection = ", ".join('"' + c.replace('"', '""') + '"' for c in public_columns) or "*"
    definition = f"CREATE OR REPLACE VIEW {view} AS SELECT {projection} FROM {target}"
    legacy_name = f"{dest.physical_name}_legacy_baseline"
    renamed_legacy = False
    try:
        # First publication turns the old physical Gold into a retained
        # baseline, freeing its public name for the stable view.
        if previous is None:
            cat = iceberg.get_catalog()
            old_tid = ("gold", dest.physical_name.replace("-", "_").replace(".", "_"))
            legacy_tid = ("gold", legacy_name.replace("-", "_").replace(".", "_"))
            try:
                cat.load_table(old_tid)
                cat.rename_table(old_tid, legacy_tid)
                renamed_legacy = True
            except Exception:
                pass
        from app.services.trino_service import execute
        asyncio.run(execute(definition))
    except Exception as exc:
        if renamed_legacy:
            try: iceberg.get_catalog().rename_table(("gold", legacy_name), ("gold", dest.physical_name))
            except Exception: logger.exception("Could not restore legacy Gold after failed view publication")
        raise RuntimeError(f"Stable Gold view publication failed: {exc}") from exc
    if previous: previous.status = "SUPERSEDED"
    version.status = "ACTIVE"; version.published_at = datetime.now(timezone.utc)
    session.add(ClassificationPublication(destination_table_id=dest.id, previous_version_id=previous.id if previous else None, version_id=version.id, view_name=view, new_definition=definition))
    session.commit()
    if own: session.close()
    return {"version_id": version.id, "view": view, "target": target}

def create_version(destination_id: int, mode="FULL", task_id=None) -> int:
    session = SyncSession(); dest = session.get(DestinationTable, destination_id)
    base = session.execute(select(FinancialBaseVersion).where(FinancialBaseVersion.destination_table_id == destination_id, FinancialBaseVersion.status == "ACTIVE").order_by(FinancialBaseVersion.id.desc())).scalars().first()
    if not base: raise RuntimeError("active financial base version not found")
    snap, _ = _rules_snapshot(session, destination_id)
    last = session.execute(select(func.max(ClassificationVersion.version)).where(ClassificationVersion.destination_table_id == destination_id)).scalar() or 0
    row = ClassificationVersion(destination_table_id=destination_id, version=last+1, financial_base_version_id=base.id, ruleset_snapshot_id=snap.id, physical_table=f"{dest.physical_name}__cv_{last+1}", mode=mode, source_task_id=task_id)
    session.add(row); session.commit(); ident=row.id; session.close(); return ident
