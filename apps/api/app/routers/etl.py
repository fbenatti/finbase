from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text, delete as sa_delete, or_
from app.database import get_db
from app.models.source_connection import SourceConnection
from app.models.ingested_blob import IngestedBlob
from app.models.skipped_blob import SkippedBlob
from app.schemas.schemas import (
    BlobScanResult, IngestedBlobOut, EtlRunRequest,
    SkippedBlobOut, SkipBlobRequest, ReprocessRequest,
    ScheduledTaskCreate, ScheduledTaskUpdate, ScheduledTaskOut,
)
from app.services.azure_extractor import AzureExtractor
from app.services.task_runner import get_task_status as _get_task_status, cancel_task, get_running_tasks
import httpx
from httpx import DigestAuth
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from croniter import croniter

router = APIRouter(prefix="/etl", tags=["etl"])


@router.post("/connections/{connection_id}/scan", response_model=BlobScanResult)
async def scan_blobs(connection_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SourceConnection).where(SourceConnection.id == connection_id)
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    if conn.connector_type != "azure_blob":
        raise HTTPException(status_code=400, detail="Scan only supported for azure_blob connections")

    extractor = AzureExtractor(conn)
    return await extractor.scan(db)


@router.get("/blobs/", response_model=list[IngestedBlobOut])
async def list_ingested_blobs(
    connection_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(IngestedBlob).order_by(IngestedBlob.ingested_at.desc())
    if connection_id:
        stmt = stmt.where(IngestedBlob.connection_id == connection_id)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.delete("/blobs/skipped", status_code=204)
async def clear_skipped_blobs(
    connection_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Clear all ACK/ignored markers for one connection."""
    result = await db.execute(
        select(SkippedBlob).where(SkippedBlob.connection_id == connection_id)
    )
    for skipped in result.scalars().all():
        await db.delete(skipped)
    await db.commit()


@router.delete("/blobs/{blob_id}", status_code=204)
async def delete_ingested_blob(blob_id: int, db: AsyncSession = Depends(get_db)):
    """Remove ingestion metadata; financial rows live in Iceberg/Parquet."""
    result = await db.execute(select(IngestedBlob).where(IngestedBlob.id == blob_id))
    blob = result.scalar_one_or_none()
    if not blob:
        # The UI can retry a delete after the metadata was already removed.
        # Treat it as success so deletion remains idempotent.
        return

    await db.execute(text("DELETE FROM ingested_blob WHERE id = :blob_id"), {"blob_id": blob_id})
    await db.commit()


@router.delete("/blobs/", status_code=204)
async def delete_all_ingested_blobs(
    connection_id: int = Query(..., description="Connection ID to clear all data for"),
    billing_period: str | None = Query(None, description="Optional: only clear a specific period"),
    db: AsyncSession = Depends(get_db),
):
    """Remove ingestion metadata (optionally filtered by period)."""
    where = "WHERE ib.connection_id = :connection_id"
    params: dict = {"connection_id": connection_id}
    if billing_period:
        where += " AND ib.billing_period = :billing_period"
        params["billing_period"] = billing_period

    # Delete the ingested blobs
    await db.execute(text(f"""
        DELETE FROM ingested_blob {where}
    """), params)

    await db.commit()


@router.post("/run")
async def run_etl(request: EtlRunRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SourceConnection).where(SourceConnection.id == request.connection_id)
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    if conn.connector_type != "azure_blob":
        return {
            "task_id": 0,
            "status": "NOT_IMPLEMENTED",
            "message": f"ETL para {conn.connector_type} sera implementado na Fase 5. Azure Blob ja disponivel."
        }

    from app.models.etl_task import EtlTask
    from sqlalchemy import insert

    new_id = await db.execute(
        insert(EtlTask).values(
            task_type="extraction",
            status="QUEUED",
            connection_id=request.connection_id,
            destination_table_id=None,
            connection_ids=[request.connection_id],
            billing_periods=request.force_periods or [],
            selected_blobs=request.selected_blobs or [],
            total_blobs=0,
            processed_blobs=0,
            rows_raw=0,
            rows_normalized=0,
            rows_destination=0,
            errors=[],
        ).returning(EtlTask.id)
    )
    task_id = new_id.scalar_one()
    await db.commit()

    # Register in in-memory dict so the frontend can see it immediately
    from app.services.task_runner import _inmemory_tasks
    _inmemory_tasks[task_id] = {
        "id": task_id,
        "connection_id": request.connection_id,
        "status": "QUEUED",
        "task_type": "extraction",
        "total_blobs": 0,
        "processed_blobs": 0,
        "rows_raw": 0,
        "rows_normalized": 0,
        "rows_destination": 0,
        "current_blob": None,
        "current_blob_size": 0,
        "download_bytes": 0,
        "downloaded_bytes_total": 0,
        "total_download_bytes": 0,
        "active_downloads": 0,
        "errors": [],
    }

    return {"task_id": task_id, "status": "QUEUED", "message": "Extraction queued — worker will pick it up"}


@router.post("/cancel/{task_id}")
async def cancel_etl(task_id: int):
    if cancel_task(task_id):
        return {"status": "CANCELLING"}
    raise HTTPException(status_code=404, detail="Task not found or already finished")


@router.post("/connections/{connection_id}/reset-source")
async def reset_source_for_reimport(connection_id: int, db: AsyncSession = Depends(get_db)):
    """Clear source metadata and Bronze so current CSVs can be reimported cleanly."""
    from app.services import iceberg_service as iceberg
    from app.models.etl_task import EtlTask
    conn = await db.get(SourceConnection, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    active = await db.execute(select(EtlTask.id).where(EtlTask.connection_id == connection_id, EtlTask.status.in_(["QUEUED", "RUNNING"])))
    if active.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Existe uma tarefa ativa para esta fonte")
    await db.execute(sa_delete(IngestedBlob).where(IngestedBlob.connection_id == connection_id))
    await db.commit()
    bronze_reset = iceberg.reset_bronze(conn.code)
    return {"status": "READY", "connection_id": connection_id, "bronze_reset": bronze_reset, "message": "Metadados e Bronze removidos. Execute a importação dos CSVs atuais."}


@router.get("/status/{task_id}")
async def get_etl_status(task_id: int):
    status = _get_task_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="Task not found")
    return status


@router.get("/tasks/")
async def list_running_tasks():
    """List all currently running (non-terminal) ETL tasks."""
    return get_running_tasks()


# --- Skipped Blobs ---

@router.get("/blobs/skipped", response_model=list[SkippedBlobOut])
async def list_skipped_blobs(
    connection_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """List all blobs marked as skipped for a connection."""
    result = await db.execute(
        select(SkippedBlob)
        .where(SkippedBlob.connection_id == connection_id)
        .order_by(SkippedBlob.skipped_at.desc())
    )
    return result.scalars().all()


@router.post("/blobs/skip", response_model=SkippedBlobOut, status_code=201)
async def skip_blob(
    body: SkipBlobRequest,
    db: AsyncSession = Depends(get_db),
):
    """Mark a blob as skipped so it is never imported."""
    # Check if already skipped
    result = await db.execute(
        select(SkippedBlob).where(
            SkippedBlob.connection_id == body.connection_id,
            SkippedBlob.blob_path == body.blob_path,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    skipped = SkippedBlob(
        connection_id=body.connection_id,
        blob_path=body.blob_path,
        reason=body.reason,
    )
    db.add(skipped)
    await db.commit()
    await db.refresh(skipped)
    return skipped


@router.delete("/blobs/skip/{skip_id}", status_code=204)
async def unskip_blob(skip_id: int, db: AsyncSession = Depends(get_db)):
    """Remove a blob from the skip list so it can be imported again."""
    result = await db.execute(
        select(SkippedBlob).where(SkippedBlob.id == skip_id)
    )
    skipped = result.scalar_one_or_none()
    if not skipped:
        raise HTTPException(status_code=404, detail="Skipped blob not found")
    await db.delete(skipped)
    await db.commit()


@router.post("/connections/{connection_id}/test")
async def test_connection(connection_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SourceConnection).where(SourceConnection.id == connection_id)
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    config = conn.config_json

    if conn.connector_type == "azure_blob":
        try:
            extractor = AzureExtractor(conn)
            client = extractor._get_blob_service_client()
            container = config.get("container_name", "")
            cc = client.get_container_client(container)
            blobs = cc.list_blobs()
            next(iter(blobs))
            return {
                "status": "OK",
                "message": f"Connected to {config.get('storage_account','?')}/{container}",
            }
        except Exception as e:
            return {"status": "FAILED", "message": str(e)[:500]}

    if conn.connector_type == "gcp_bigquery":
        return {
            "status": "PENDING",
            "message": f"GCP BigQuery ({config.get('project_id','?')}.{config.get('dataset_id','?')}) sera implementado na Fase 5. Estrutura de conexao OK.",
        }

    if conn.connector_type == "mongodb_atlas":
        public_key = config.get("public_key", "")
        private_key = config.get("private_key", "")
        base_url = config.get("base_url", "https://cloud.mongodb.com/api/atlas/v1.0")

        if not public_key or not private_key:
            return {
                "status": "PENDING",
                "message": f"MongoDB Atlas ({config.get('contract_type','?')}) — chaves de API nao configuradas. Adicione public_key e private_key na conexao para testar.",
            }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    base_url.rstrip("/"),
                    auth=DigestAuth(username=public_key, password=private_key),
                )
                if resp.status_code == 200:
                    return {
                        "status": "OK",
                        "message": f"MongoDB Atlas ({config.get('contract_type','?')}) — API respondendo OK",
                    }
                detail = ""
                try:
                    detail = resp.json().get("detail", "")
                except Exception:
                    pass
                return {
                    "status": "FAILED",
                    "message": f"MongoDB Atlas retornou HTTP {resp.status_code}: {detail}"[:500],
                }
        except Exception as e:
            return {"status": "FAILED", "message": str(e)[:500]}

    return {
        "status": "NOT_IMPLEMENTED",
        "message": f"Test not implemented for {conn.connector_type}",
    }


# ═══════════════════════════════════════════════════════════════════════
# Destination table reprocessing (standalone, async with progress)
# ═══════════════════════════════════════════════════════════════════════

_dest_reprocess_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dest-reprocess")
_dest_task_counter = 0
# NOTE: reprocess tasks are registered into task_runner._inmemory_tasks for global visibility



def _write_dest_chunk(
    records: list[dict],
    conn_mappings: list,
    conn,
    dest_col_names: set[str],
    phys_name: str,
    total_written: int,
) -> int:
    """Build a DataFrame from records, map columns, and write to Iceberg gold.{phys_name}."""
    import pandas as pd
    from sqlalchemy import text
    from app.services import iceberg_service as iceberg

    if not records:
        return total_written

    df = pd.DataFrame(records)

    target_data: dict[str, pd.Series] = {}
    for m in conn_mappings:
        if m.source_column in df.columns:
            target_data[m.destination_column] = df[m.source_column].values

    for col in df.columns:
        if col in dest_col_names and col not in target_data and not col.startswith("_"):
            target_data[col] = df[col].values

    if not target_data:
        return total_written

    target = pd.DataFrame(target_data)
    target["cloud_origem"] = conn.code
    target["source_connection_id"] = conn.id if hasattr(conn, 'id') else conn.connection_id

    internal_cols = {"cloud_origem", "source_connection_id"}
    valid_cols = [c for c in target.columns if c in dest_col_names or c in internal_cols]
    target = target[valid_cols]

    if target.empty:
        return total_written

    target = target.where(pd.notna(target), None)

    # Extract billing period from records for partitioning
    bp_set = set()
    for r in records:
        bp = r.get("_billing_period", r.get("billing_period", ""))
        if bp:
            bp_set.add(str(bp))
    billing_period = sorted(bp_set)[0] if bp_set else "unknown"

    target["_billing_period"] = billing_period

    rows = iceberg.write_destination(phys_name, target, partition_col="_billing_period")
    return total_written + rows


def _process_blob_for_reprocess(
    blob_id: int,
    connection_id: int,
    connection_code: str,
    billing_period: str,
    brules: list,       # detached BusinessRule objects
    crules: list,       # detached ClassificationRule objects
    cmapps: list,       # detached SourceDestinationMapping objects
    dest_col_names: set[str],
    dept_cache: dict,
    proj_cache: dict,
    hierarchy_cache: dict,
    output_cols: set[str],
    task_id: int,
    source_frame: "pd.DataFrame | None" = None,
) -> "pd.DataFrame | None":
    """Process a single blob in a thread — reads from DB, applies rules/mappings/classifications.

    Each thread gets its own SQLAlchemy SyncSession (session-per-thread pattern)
    because SQLAlchemy Session is not thread-safe.

    Returns the processed DataFrame (with cloud_origem, source_connection_id,
    _billing_period columns) or None if the blob had no data or was cancelled.
    """
    import pandas as pd
    from app.database import SyncSession
    from app.services import iceberg_service as iceberg
    from app.services.task_runner import (
        _apply_business_rule_sync, _apply_classifications_sync,
        _check_cancelled, _inmemory_tasks,
    )

    session = SyncSession()
    try:
        # Reprocess must start from immutable Bronze, never from the removed
        # legacy SQL normalized_cost_line table.
        source = source_frame if source_frame is not None else iceberg.read_bronze(connection_code, billing_period=billing_period)
        if source_frame is not None:
            source = source[source["_billing_period"].astype(str) == str(billing_period)].copy()
        # The caller identifies the blob by metadata id; resolve its path once.
        from app.models.ingested_blob import IngestedBlob
        meta = session.get(IngestedBlob, blob_id)
        if meta is not None and source_frame is not None:
            if "_blob_path" in source.columns:
                source = source[source["_blob_path"] == meta.blob_path].copy()
            source = source.iloc[:meta.row_count or 0]
        elif meta is not None:
            # IngestedBlob.row_count is the canonical input contract. Bronze
            # may contain historical append duplicates; never let those rows
            # inflate a reprocess beyond the validated CSV count.
            source = iceberg.read_bronze(
                connection_code,
                blob_path=meta.blob_path,
                billing_period=billing_period,
                max_rows=meta.row_count or 0,
            )
            if len(source) != (meta.row_count or 0):
                raise RuntimeError(
                    f"Contagem Bronze divergente para {meta.blob_path}: "
                    f"esperado {meta.row_count}, encontrado {len(source)}"
                )

        chunks: list[pd.DataFrame] = []
        rows_in_blob = 0
        for start in range(0, len(source), 50000):
            chunk = source.iloc[start:start + 50000].copy()
            if _check_cancelled(task_id):
                return None

            df_chunk = chunk.drop(columns=[c for c in ["_billing_period", "_blob_path", "_ingested_at"] if c in chunk.columns])

            # Phase 1: Map source columns → destination columns (Silver)
            target = pd.DataFrame()
            for m in cmapps:
                if m.source_column in df_chunk.columns:
                    target[m.destination_column] = df_chunk[m.source_column]

            # Preserve provenance and only destination-named fields.
            target["cloud_origem"] = connection_code
            target["source_connection_id"] = connection_id
            target["_billing_period"] = str(billing_period)

            # Phase 2: Rules operate on the mapped destination contract.
            for r in brules:
                _apply_business_rule_sync(target, r, str(billing_period))

            # Phase 3: Classifications operate on the mapped destination.
            if crules:
                _apply_classifications_sync(
                    target, crules, None,
                    dept_map=dept_cache,
                    proj_map=proj_cache,
                    hierarchy_cache=hierarchy_cache,
                )

            if target.empty:
                continue

            chunks.append(target)
            rows_in_blob += len(target)
            # Persist heartbeat/progress while a large blob is being processed.
            # Previously the UI stayed on the same blob until the whole future
            # completed, which looked like a deadlock for 300k+ row files.
            progress_text = (
                f"blob {billing_period}: {rows_in_blob:,} linhas processadas"
            )
            status = _inmemory_tasks.get(task_id)
            if status is not None:
                status["current_blob"] = progress_text
                status["phase"] = progress_text
            session.execute(text("""
                UPDATE etl_task
                   SET current_blob = :phase, phase = :phase
                 WHERE id = :task_id AND status = 'RUNNING'
            """), {"phase": progress_text, "task_id": task_id})
            session.commit()

        if not chunks:
            return None

        return pd.concat(chunks, ignore_index=True)
    finally:
        # Libera o Bronze filtrado e os chunks usados para compor este blob;
        # o DataFrame retornado continua vivo somente até ser escrito no
        # staging pelo coordenador.
        try:
            session.close()
        except Exception:
            pass
        try:
            import gc
            for _name in ("source", "chunks", "chunk", "df_chunk", "target"):
                if _name in locals():
                    locals()[_name] = None
            gc.collect()
        except Exception:
            pass

def _reprocess_destination_sync(
    task_id: int,
    destination_table_id: int,
    connection_ids: list[int],
    billing_periods: list[str],
    replace_existing: bool = False,
) -> dict:
    """Read normalized data blob-by-blob via raw JSON → pandas → Iceberg. Granular progress."""
    import pandas as pd, json
    from app.database import SyncSession
    from app.models.source_connection import SourceConnection
    from app.models.ingested_blob import IngestedBlob
    from app.models.destination import DestinationTable, DestinationColumn, SourceDestinationMapping
    from app.models.rules import BusinessRule, ClassificationRule, Department, Project
    from app.models.etl_task import EtlTask
    from sqlalchemy import select, text, func
    from app.services import iceberg_service as iceberg
    from app.services.task_runner import _inmemory_tasks, _check_cancelled

    status = _inmemory_tasks.get(task_id, {})
    status["status"] = "RUNNING"
    
    def _sync_db():
        """Write current status to EtlTask DB row so it survives API reloads."""
        try:
            db2 = SyncSession()
            row = db2.get(EtlTask, task_id)
            if row:
                row.status = status.get("status", row.status)
                row.total_blobs = status.get("total_blobs", row.total_blobs)
                row.processed_blobs = status.get("processed_blobs", row.processed_blobs)
                row.rows_destination = status.get("rows_destination", row.rows_destination)
                row.current_blob = status.get("current_blob", row.current_blob)
                row.phase = status.get("current_blob", row.phase)
                row.result_json = {"table": status.get("table", ""), "rows_written": status.get("rows_destination", 0)}
                if status.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
                    row.finished_at = func.now()
                db2.commit()
            db2.close()
        except Exception:
            pass

    _sync_db()
    session = SyncSession()
    try:
        dest_info = session.get(DestinationTable, destination_table_id)
        if not dest_info:
            status["status"] = "FAILED"; status["error"] = "Tabela destino nao encontrada"
            return {"success": False, "error": "Tabela destino nao encontrada", "rows_written": 0}
        phys_name = dest_info.physical_name; status["table"] = dest_info.name

        all_mappings = []
        for cid in connection_ids:
            all_mappings.extend(session.execute(select(SourceDestinationMapping).where(
                SourceDestinationMapping.source_connection_id == cid,
                SourceDestinationMapping.destination_table_id == destination_table_id,
                SourceDestinationMapping.active == True,
            )).scalars().all())
        if not all_mappings:
            status["status"] = "FAILED"; status["error"] = "Nenhum mapeamento"
            return {"success": False, "error": "Nenhum mapeamento", "rows_written": 0}

        dest_col_meta = session.execute(
            select(DestinationColumn.column_name, DestinationColumn.data_type)
            .where(DestinationColumn.destination_table_id == destination_table_id)
            .order_by(DestinationColumn.position)
        ).all()
        dest_col_names = {r[0] for r in dest_col_meta}
        dest_col_types: dict[str, str] = {r[0]: (r[1] or "TEXT") for r in dest_col_meta}

        # Gather all blob IDs upfront for progress tracking
        all_blobs: list[tuple[int, int, str]] = []  # (blob_id, connection_id, billing_period)
        for cid in connection_ids:
            q = select(IngestedBlob.id, IngestedBlob.billing_period).where(IngestedBlob.connection_id == cid)
            if billing_periods:
                q = q.where(IngestedBlob.billing_period.in_(billing_periods))
            q = q.order_by(IngestedBlob.billing_period)
            for row in session.execute(q).all():
                all_blobs.append((row[0], cid, row[1]))

        total_blobs = len(all_blobs)
        expected_input_rows = sum(
            (session.get(IngestedBlob, blob_id).row_count or 0)
            for blob_id, _, _ in all_blobs
            if session.get(IngestedBlob, blob_id) is not None
        )
        status["total_blobs"] = total_blobs
        status["processed_blobs"] = 0
        status["rows_destination"] = 0
        _sync_db()

        # ── Pre-load connections, rules, and hierarchy for ALL connections BEFORE the thread pool ──
        # ORM objects are loaded in the main session and become detached after session.close().
        # Detached objects retain their loaded attributes (expire_on_commit=False), so
        # worker threads can safely read them without any DB interaction.
        conn_map: dict[int, SourceConnection] = {}
        conn_rules: dict[int, tuple] = {}  # cid → (brules, crules, dept_cache, proj_cache, hierarchy_cache)
        unique_cids = set(cid for _, cid, _ in all_blobs)
        output_cols = {'projeto', 'departamento', 'diretoria', 'centro_custo',
                       'ambiente', 'custom_fields', 'subscription_rg', 'projeto_nome',
                       'CostBRL', 'ContractedCost', 'ListCost', 'TaxBRL',
                       'EffectiveCost', 'Tax_Base', 'Tax_Marketplace',
                       'VL_CUSTO_FINAL_BRL'}
        internal_cols_all = {"cloud_origem", "source_connection_id"}

        for cid in unique_cids:
            conn = session.get(SourceConnection, cid)
            if not conn:
                continue
            conn_map[cid] = conn

            # Load business rules (this connection + global rules)
            _brules = session.execute(
                select(BusinessRule).where(
                    or_(BusinessRule.connection_id == cid, BusinessRule.connection_id.is_(None)),
                    BusinessRule.active == True,
                ).order_by(BusinessRule.priority)
            ).scalars().all()

            # Load classification rules (this connection + global rules)
            _crules = session.execute(
                select(ClassificationRule).where(
                    or_(ClassificationRule.connection_id == cid, ClassificationRule.connection_id.is_(None)),
                    ClassificationRule.active == True,
                ).order_by(ClassificationRule.priority)
            ).scalars().all()

            # Business rules may create arbitrary destination columns (for
            # example ``realizado``). Keep those columns in the final Gold
            # projection instead of dropping them through the legacy fixed
            # output_cols allow-list below.
            for rule in _brules:
                if rule.target_column:
                    output_cols.add(rule.target_column)
                for condition in (rule.conditions or []):
                    if isinstance(condition, dict) and condition.get("then_field"):
                        output_cols.add(condition["then_field"])
            for rule in _crules:
                if rule.target_field:
                    output_cols.add(rule.target_field)

            # Pre-load department/project hierarchy
            _dept_cache: dict = {}
            _proj_cache: dict = {}
            _hierarchy_cache: dict = {}
            if _crules:
                dept_ids_needed = {r.department_id for r in _crules if r.department_id}
                proj_ids_needed = {r.project_id for r in _crules if r.project_id}

                if dept_ids_needed:
                    depts = session.execute(
                        select(Department).where(Department.id.in_(dept_ids_needed))
                    ).scalars().all()
                    _dept_cache = {d.id: d for d in depts}
                    # Also load parent departments (walk up the tree)
                    parent_ids = set()
                    for d in depts:
                        cur = d.parent_id
                        depth = 0
                        while cur and depth < 5:
                            parent_ids.add(cur)
                            if cur in _dept_cache:
                                cur = _dept_cache[cur].parent_id
                            else:
                                break
                            depth += 1
                    if parent_ids:
                        missing = parent_ids - set(_dept_cache.keys())
                        if missing:
                            parents = session.execute(
                                select(Department).where(Department.id.in_(missing))
                            ).scalars().all()
                            for p in parents:
                                _dept_cache[p.id] = p

                if proj_ids_needed:
                    projs = session.execute(
                        select(Project).where(Project.id.in_(proj_ids_needed))
                    ).scalars().all()
                    _proj_cache = {p.id: p for p in projs}

                # Pre-build hierarchy cache: dept_id → (leaf_dept, root_dept, chain_from_leaf_to_root)
                for did in dept_ids_needed:
                    dept = _dept_cache.get(did)
                    if not dept:
                        _hierarchy_cache[did] = (None, None, [])
                        continue
                    visited = [dept]
                    cur = dept.parent_id
                    depth = 0
                    while cur and depth < 5:
                        parent = _dept_cache.get(cur)
                        if parent:
                            visited.append(parent)
                            cur = parent.parent_id
                        else:
                            break
                        depth += 1
                    _hierarchy_cache[did] = (dept, visited[-1], visited)

            conn_rules[cid] = (_brules, _crules, _dept_cache, _proj_cache, _hierarchy_cache)

        # Close main session NOW — all ORM objects are detached but attributes remain accessible
        session.close()

        status["phase"] = "Processando batches"
        status["current_blob"] = "Aguardando primeiro batch"
        _sync_db()

        # Processa no máximo três blobs em memória. O código anterior guardava
        # os 28 DataFrames e depois fazia um pd.concat de toda a massa, o que
        # duplicava milhões de linhas em RAM e travava antes da escrita final.
        ordered_dest_cols = [r[0] for r in dest_col_meta]
        def _prepare_for_stage(frame: pd.DataFrame) -> pd.DataFrame:
            keep_cols = [c for c in ordered_dest_cols if c in frame.columns]
            keep_cols.extend(c for c in output_cols if c in frame.columns and c not in keep_cols)
            keep_cols.extend(c for c in internal_cols_all if c in frame.columns and c not in keep_cols)
            if "_billing_period" in frame.columns and "_billing_period" not in keep_cols:
                keep_cols.append("_billing_period")
            return frame[keep_cols].where(pd.notna(frame[keep_cols]), None)

        temp_name: str | None = None
        staged_rows = 0

        def _stage_frame(frame: pd.DataFrame) -> None:
            """Append one completed blob to the temporary Iceberg table."""
            nonlocal temp_name, staged_rows
            prepared = _prepare_for_stage(frame)
            for start in range(0, len(prepared), 100_000):
                chunk = prepared.iloc[start:start + 100_000]
                if temp_name is None:
                    temp_name = iceberg.write_destination_staged(
                        phys_name, chunk, partition_col="_billing_period", column_types=dest_col_types,
                    )
                else:
                    iceberg.append_to_staged(
                        temp_name, chunk, partition_col="_billing_period", column_types=dest_col_types,
                    )
                staged_rows += len(chunk)
            del prepared

        # For partial reprocesses, preserve unaffected rows in the same
        # temporary table. Full rebuilds never read the old Gold table.
        if billing_periods and connection_ids and not replace_existing:
            try:
                existing_df = iceberg.read_destination(phys_name)
                keep_mask = ~(
                    existing_df["_billing_period"].isin(billing_periods)
                    & existing_df["source_connection_id"].isin(connection_ids)
                )
                existing_df = existing_df[keep_mask]
                if not existing_df.empty:
                    _stage_frame(existing_df)
                del existing_df
            except Exception as merge_err:
                if "NoSuchTableException" not in type(merge_err).__name__ and "does not exist" not in str(merge_err).lower():
                    raise RuntimeError(f"Falha ao preservar dados existentes: {merge_err}") from merge_err

        # ── Phase 1: process in parallel; stage each finished blob immediately ──
        total_rows_processed = 0
        errors: list[str] = []
        progress_lock = Lock()
        processed = 0

        # Configurable: increase for more I/O parallelism (each thread = 1 DB connection)
        max_workers = 3

        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="reprocess-blob") as executor:
            future_map: dict = {}
            for blob_id, cid, bperiod in all_blobs:
                if cid not in conn_rules:
                    with progress_lock:
                        processed += 1
                        status["processed_blobs"] = processed
                        _sync_db()
                    continue

                brules, crules, dept_cache, proj_cache, hierarchy_cache = conn_rules[cid]
                cmapps = [m for m in all_mappings if m.source_connection_id == cid]
                if not cmapps:
                    with progress_lock:
                        processed += 1
                        status["processed_blobs"] = processed
                        _sync_db()
                    continue

                conn = conn_map[cid]
                future = executor.submit(
                    _process_blob_for_reprocess,
                    blob_id, cid, conn.code, bperiod,
                    brules, crules, cmapps, dest_col_names,
                    dept_cache, proj_cache, hierarchy_cache,
                    output_cols, task_id,
                    None,
                )
                future_map[future] = (blob_id, cid, bperiod)

            for future in as_completed(future_map):
                blob_id, cid, bperiod = future_map[future]

                if _check_cancelled(task_id):
                    status["status"] = "CANCELLED"
                    # Cancel any futures that haven't started yet
                    for f in future_map:
                        f.cancel()
                    return {"success": False, "error": "Cancelado", "rows_written": 0}

                try:
                    df_result = future.result()

                    with progress_lock:
                        processed += 1
                        if df_result is not None and not df_result.empty:
                            total_rows_processed += len(df_result)
                            status["current_blob"] = (
                                f"gravando staging: blob {processed}/{total_blobs} "
                                f"({total_rows_processed} linhas processadas)"
                            )
                            _sync_db()
                            _stage_frame(df_result)
                            del df_result
                        status["processed_blobs"] = processed
                        status["rows_destination"] = total_rows_processed
                        status["current_blob"] = f"blob {processed}/{total_blobs}: {bperiod} ({total_rows_processed} linhas acumuladas)"
                        _sync_db()
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    with progress_lock:
                        processed += 1
                        error_msg = f"Blob {blob_id} ({bperiod}): {e}"
                        errors.append(error_msg)
                        status["processed_blobs"] = processed
                        status["current_blob"] = error_msg
                        _sync_db()

        if errors:
            status.setdefault("errors", []).extend(errors)
        if total_rows_processed == 0:
            if temp_name:
                iceberg.cleanup_staged_table(temp_name)
            if errors:
                status["status"] = "FAILED"
                status["error"] = errors[0]
                status["current_blob"] = f"Falha: {errors[0]}"
                _sync_db()
                return {"success": False, "error": errors[0], "rows_written": 0}
            status["status"] = "COMPLETED"
            status["current_blob"] = "Nenhum dado processado"
            return {"success": True, "rows_written": 0, "table": dest_info.name}

        if total_rows_processed != expected_input_rows:
            msg = (
                f"Contagem de entrada/saída divergente: entrada {expected_input_rows:,}, "
                f"saída {total_rows_processed:,}. Reprocessamento abortado; destino anterior preservado."
            )
            status["status"] = "FAILED"
            status["error"] = msg
            status["current_blob"] = msg
            status.setdefault("errors", []).append(msg)
            _sync_db()
            if temp_name:
                iceberg.cleanup_staged_table(temp_name)
            return {"success": False, "error": msg, "rows_written": 0}

        try:
            status["current_blob"] = f"substituindo tabela MODELO FOCUS (swap atomico)..."
            _sync_db()

            total_written = iceberg.swap_staged_table(phys_name, temp_name)
        except Exception as stage_err:
            status["current_blob"] = f"Falha na escrita Iceberg ({stage_err}), revertendo..."
            _sync_db()
            if temp_name:
                iceberg.cleanup_staged_table(temp_name)
            raise stage_err

        status["status"] = "COMPLETED"
        status["rows_destination"] = total_written
        status["current_blob"] = f"Concluido: {total_written} linhas, {len(all_blobs)} blobs processados"
        _sync_db()
        return {"success": True, "rows_written": total_written, "table": dest_info.name}

    except Exception as e:
        import traceback
        status["status"] = "FAILED"; status["error"] = str(e); status["current_blob"] = f"Erro: {e}"
        traceback.print_exc()
        _sync_db()
        return {"success": False, "error": str(e), "rows_written": 0}
    finally:
        # Keep the previous Gold table intact whenever staging did not reach
        # the atomic swap, and release worker-held pandas objects promptly.
        try:
            if locals().get("temp_name") and status.get("status") != "COMPLETED":
                iceberg.cleanup_staged_table(temp_name)
        except Exception:
            pass
        try:
            session.close()
        except Exception:
            pass
        try:
            import gc
            gc.collect()
        except Exception:
            pass


@router.post("/reprocess")
async def reprocess_destination(body: ReprocessRequest, db: AsyncSession = Depends(get_db)):
    """Create a reprocess task in QUEUED status. A separate worker process picks it up."""
    from app.models.etl_task import EtlTask
    from sqlalchemy import insert

    active = await db.execute(
        select(EtlTask.id).where(
            EtlTask.task_type == "reprocess",
            EtlTask.destination_table_id == body.destination_table_id,
            EtlTask.status.in_(["QUEUED", "RUNNING"]),
        ).limit(1)
    )
    active_id = active.scalar_one_or_none()
    if active_id is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Já existe um reprocessamento ativo para esta tabela (task {active_id})",
        )

    result = await db.execute(
        insert(EtlTask).values(
            task_type="reprocess",
            status="QUEUED",
            destination_table_id=body.destination_table_id,
            connection_ids=body.connection_ids,
            billing_periods=body.billing_periods,
            result_json={"replace_existing": body.replace_existing},
            total_blobs=0,
            processed_blobs=0,
            rows_raw=0,
            rows_normalized=0,
            rows_destination=0,
            errors=[],
        ).returning(EtlTask.id)
    )
    task_id = result.scalar_one()
    await db.commit()

    return {"task_id": task_id, "message": "Reprocess queued — worker will pick it up"}


@router.get("/reprocess-status/{task_id}")
async def get_reprocess_status(task_id: int):
    """Get the status of a reprocess task (in-memory, with DB fallback)."""
    from app.services.task_runner import _inmemory_tasks
    status = _inmemory_tasks.get(task_id)
    if not status:
        # Fallback to DB (task may have survived an API reload)
        try:
            from app.database import SyncSession
            from app.models.etl_task import EtlTask
            db = SyncSession()
            row = db.get(EtlTask, task_id)
            db.close()
            if row:
                status = {
                    "id": row.id,
                    "status": row.status,
                    "current_blob": row.current_blob or row.phase or "",
                    "total_blobs": row.total_blobs,
                    "processed_blobs": row.processed_blobs,
                    "rows_destination": row.rows_destination,
                    "table": (row.result_json or {}).get("table", ""),
                    "connection_ids": row.connection_ids or [],
                    "error": "",
                }
        except Exception:
            pass
    if not status:
        raise HTTPException(status_code=404, detail="Task not found")
    tb = status.get("total_blobs", 0)
    cb = status.get("processed_blobs", 0)
    progress = int((cb / tb) * 100) if tb > 0 else 0
    return {
        "id": task_id,
        "status": status.get("status", "UNKNOWN"),
        "phase": status.get("current_blob", status.get("phase", "")),
        "total_batches": tb,
        "completed_batches": cb,
        "progress": progress,
        "rows_written": status.get("rows_destination", 0),
        "table": status.get("table", ""),
        "connection_ids": status.get("connection_ids", []),
        "error": status.get("error", ""),
    }


@router.post("/reprocess/cancel/{task_id}")
async def cancel_reprocess(task_id: int):
    """Cancel a running/queued reprocess task — cross-process via DB + in-memory flag."""
    from app.services.task_runner import _inmemory_tasks, _cancel_flags
    from app.services.task_runner import cancel_task as _cancel_task

    _cancel_flags.add(task_id)
    status = _inmemory_tasks.get(task_id)
    if status:
        status["status"] = "CANCELLED"

    # Also write to DB so the worker process sees the cancellation
    try:
        from app.database import SyncSession
        from app.models.etl_task import EtlTask
        from sqlalchemy import func as sa_func
        db = SyncSession()
        row = db.get(EtlTask, task_id)
        if row and row.status in ("QUEUED", "RUNNING"):
            row.status = "CANCELLED"
            row.phase = "Cancelado pelo usuario"
            row.finished_at = sa_func.now()
            db.commit()
            db.close()
            return {"message": "Cancel signal sent", "task_id": task_id}
        db.close()
    except Exception:
        pass

    # If no in-memory entry and no queued/running DB row, task doesn't exist
    if not status:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"message": "Cancel signal sent", "task_id": task_id}


@router.post("/connections/{connection_id}/process-destination")
async def process_destination_tables(connection_id: int):
    """Legacy: reprocess all dest tables for a single connection (all periods)."""
    from app.database import SyncSession as SS
    from app.models.destination import SourceDestinationMapping
    from sqlalchemy import select

    # Discover which destination tables this connection maps to
    session = SS()
    try:
        mapped_tables = session.execute(
            select(SourceDestinationMapping.destination_table_id)
            .where(
                SourceDestinationMapping.source_connection_id == connection_id,
                SourceDestinationMapping.active == True,
            )
            .distinct()
        ).scalars().all()

        total = 0
        last_error = None
        loop = asyncio.get_running_loop()
        for dt_id in mapped_tables:
            # Use a negative task_id for internal calls (no progress tracking)
            internal_task_id = -dt_id
            from app.services.task_runner import _inmemory_tasks as _gt
            _gt[internal_task_id] = {"status": "RUNNING", "total_blobs": 0, "processed_blobs": 0, "rows_destination": 0}
            res = await loop.run_in_executor(
                _dest_reprocess_executor,
                _reprocess_destination_sync,
                internal_task_id,
                dt_id,
                [connection_id],
                [],
            )
            if res["success"]:
                total += res["rows_written"]
            else:
                last_error = res.get("error", "Unknown")
        session.close()
        return {"success": True, "rows_written": total, "error": last_error}
    except Exception as e:
        session.close()
        return {"success": False, "error": str(e), "rows_written": 0}


@router.get("/periods")
async def list_available_periods(
    connection_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """List distinct billing periods available for a connection (from ingested blobs)."""
    rows = (await db.execute(
        text(
            "SELECT DISTINCT billing_period, COUNT(*) as cnt "
            "FROM ingested_blob WHERE connection_id = :cid AND billing_period IS NOT NULL AND billing_period != '' "
            "GROUP BY billing_period ORDER BY billing_period DESC"
        ),
        {"cid": connection_id},
    )).fetchall()
    return [
        {"period": r[0], "blob_count": r[1]}
        for r in rows
    ]


@router.get("/connections-for-dest/{destination_table_id}")
async def list_connections_for_destination(
    destination_table_id: int,
    db: AsyncSession = Depends(get_db),
):
    """List connections that have SourceDestinationMappings for this destination table."""
    rows = (await db.execute(
        text(
            "SELECT DISTINCT sc.id, sc.code, sc.name, COUNT(sdm.id) AS mapping_count "
            "FROM source_destination_mapping sdm "
            "JOIN source_connection sc ON sc.id = sdm.source_connection_id "
            "WHERE sdm.destination_table_id = :did AND sdm.active = true "
            "GROUP BY sc.id, sc.code, sc.name "
            "ORDER BY sc.code"
        ),
        {"did": destination_table_id},
    )).fetchall()
    return [
        {
            "id": r[0],
            "code": r[1],
            "name": r[2],
            "mapping_count": r[3],
        }
        for r in rows
    ]


# ═══════════════════════════════════════════════════════════════════════
# Scheduled Tasks (cron-like ETL automation)
# ═══════════════════════════════════════════════════════════════════════

from app.models.scheduled_task import ScheduledTask
from app.services import scheduler_service


@router.get("/schedules", response_model=list[ScheduledTaskOut])
async def list_schedules(db: AsyncSession = Depends(get_db)):
    """List all scheduled ETL tasks."""
    result = await db.execute(
        select(ScheduledTask).order_by(ScheduledTask.created_at.desc())
    )
    return result.scalars().all()


@router.post("/schedules", response_model=ScheduledTaskOut, status_code=201)
async def create_schedule(body: ScheduledTaskCreate, db: AsyncSession = Depends(get_db)):
    """Create a new scheduled ETL task."""
    # Validate cron expression
    try:
        croniter(body.cron_expression)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid cron expression: {body.cron_expression}")

    sched = ScheduledTask(
        name=body.name,
        connection_id=body.connection_id,
        cron_expression=body.cron_expression,
        timezone=body.timezone,
        destination_reprocess=body.destination_reprocess,
        active=body.active,
        config_json=body.config_json,
    )
    db.add(sched)
    await db.commit()
    await db.refresh(sched)

    # Register in the scheduler if active
    if sched.active:
        scheduler_service.add_job(sched)

    return sched


@router.put("/schedules/{schedule_id}", response_model=ScheduledTaskOut)
async def update_schedule(schedule_id: int, body: ScheduledTaskUpdate, db: AsyncSession = Depends(get_db)):
    """Update a scheduled ETL task."""
    result = await db.execute(
        select(ScheduledTask).where(ScheduledTask.id == schedule_id)
    )
    sched = result.scalar_one_or_none()
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(sched, key, value)

    await db.commit()
    await db.refresh(sched)

    # Re-register or remove from scheduler
    scheduler_service.remove_job(schedule_id)
    if sched.active:
        scheduler_service.add_job(sched)

    return sched


@router.delete("/schedules/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a scheduled ETL task."""
    result = await db.execute(
        select(ScheduledTask).where(ScheduledTask.id == schedule_id)
    )
    sched = result.scalar_one_or_none()
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found")

    scheduler_service.remove_job(schedule_id)
    await db.delete(sched)
    await db.commit()


@router.post("/schedules/{schedule_id}/run")
async def run_schedule_now(schedule_id: int, db: AsyncSession = Depends(get_db)):
    """Manually trigger a scheduled ETL task right now."""
    result = await db.execute(
        select(ScheduledTask).where(ScheduledTask.id == schedule_id)
    )
    sched = result.scalar_one_or_none()
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found")

    task_id = await scheduler_service.run_etl_and_reprocess(sched.connection_id)
    return {"task_id": task_id, "connection_id": sched.connection_id, "schedule_name": sched.name}
