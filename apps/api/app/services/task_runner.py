"""
ETL Task Runner — executes extraction/transformation in background threads
so the asyncio event loop stays free for API requests.

All heavy I/O (Azure blob download) and CPU (pandas) work runs inside a
ThreadPoolExecutor using synchronous DB sessions.
"""

import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import select, or_, update as sa_update
from sqlalchemy.orm import Session

from app.database import async_session, SyncSession
from app.models.source_connection import SourceConnection
from app.models.etl_task import EtlTask
from app.models.ingested_blob import IngestedBlob
from app.models.rules import EtlMapping, BusinessRule, ClassificationRule
from app.models.destination import DestinationTable, DestinationColumn, SourceDestinationMapping
from app.services.azure_extractor import AzureExtractor, derive_billing_period
from app.services.gcp_bigquery_extractor import GcpBigQueryExtractor
from app.services.oci_extractor import OciExtractor, derive_billing_period_oci
from app.services.mongodb_extractor import MongoAtlasExtractor
from app.services.github_extractor import GithubBillingExtractor
from app.services.github_license_extractor import GithubLicenseExtractor
from app.services.github_token_extractor import GithubTokenExtractor
from app.services.notebooklm_license_extractor import NotebookLMLicenseExtractor
from app.services.notebooklm_token_extractor import NotebookLMTokenExtractor
from app.services.claude_license_extractor import ClaudeLicenseExtractor
from app.services.claude_token_extractor import ClaudeTokenExtractor
from app.services.expression_engine import (
    evaluate_expression,
    evaluate_conditional,
    build_scope_mask,
)
from app.services import iceberg_service as iceberg
from app.services.classification_field_values import enrich_from_dataframe

logger = logging.getLogger(__name__)

# ── thread-safe shared state ──────────────────────────────────────────
_inmemory_tasks: dict[int, dict] = {}
_cancel_flags: set[int] = set()

# ── thread pool for ETL work (max 2 concurrent jobs) ──────────────────
_etl_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="etl-worker")


def _persist_download_progress(task_id: int, **values) -> None:
    """Persist worker progress so it is visible from the separate API process."""
    try:
        s = SyncSession()
        s.execute(sa_update(EtlTask).where(EtlTask.id == task_id).values(**values))
        s.commit()
        s.close()
    except Exception as exc:
        logger.debug("Could not persist download progress for task %s: %s", task_id, exc)


def _persist_download_delta(task_id: int, delta: int, **values) -> None:
    """Atomically add downloaded bytes while updating the current blob fields."""
    try:
        s = SyncSession()
        values["downloaded_bytes_total"] = EtlTask.downloaded_bytes_total + max(0, delta)
        s.execute(sa_update(EtlTask).where(EtlTask.id == task_id).values(**values))
        s.commit()
        s.close()
    except Exception as exc:
        logger.debug("Could not persist download delta for task %s: %s", task_id, exc)


def _persist_download_blob(task_id: int, blob_path: str, status: str, downloaded: int, size: int) -> None:
    """Update one item in the persisted per-blob download breakdown."""
    try:
        s = SyncSession()
        row = s.execute(select(EtlTask).where(EtlTask.id == task_id).with_for_update()).scalar_one()
        blobs = list(row.download_blobs or [])
        item = next((b for b in blobs if b.get("blob_path") == blob_path), None)
        if item is None:
            item = {"blob_path": blob_path, "size": size, "downloaded": 0, "status": status}
            blobs.append(item)
        item.update({"size": size or item.get("size", 0), "downloaded": max(0, downloaded), "status": status})
        row.download_blobs = blobs
        s.commit()
        s.close()
    except Exception as exc:
        logger.debug("Could not persist per-blob progress for task %s: %s", task_id, exc)


# ═══════════════════════════════════════════════════════════════════════
#  Synchronous blob processor (runs in thread)
# ═══════════════════════════════════════════════════════════════════════

def _process_blob_sync(
    session: Session,
    extractor: AzureExtractor,
    blob_path: str,
    blob_prefix: str,
    last_modified: datetime,
    content_length: Optional[int],
    etag: Optional[str],
    task_id: Optional[int] = None,
) -> tuple[int, int, int, list[str]]:
    """Synchronous version of AzureExtractor._process_blob — runs inside a thread."""
    # Skip non-CSV files (manifest.json, etc.)
    if not blob_path.lower().endswith('.csv'):
        logger.info("Skipping non-CSV blob: %s", blob_path)
        return 0, 0, 0, [f"Skipped non-CSV: {blob_path}"]
    errors: list[str] = []
    conn = extractor.connection
    blob_service = extractor._get_blob_service_client()
    container_name = conn.config_json["container_name"]
    blob_client = blob_service.get_blob_client(container=container_name, blob=blob_path)

    # ── download + parse CSV ──
    try:
        _persist_download_progress(
            task_id,
            current_blob=blob_path,
            current_blob_size=content_length or 0,
            download_bytes=0,
            phase=f"Download: {blob_path.rsplit('/', 1)[-1]}",
        )
        _persist_download_blob(task_id, blob_path, "DOWNLOADING", 0, content_length or 0)
        import io
        stream = blob_client.download_blob(max_concurrency=1, timeout=300)
        data = bytearray()
        blob_size = content_length or stream.size or 0
        last_report = 0
        last_persisted = 0
        chunk_size = 4 * 1024 * 1024  # 4 MB chunks
        for chunk in stream.chunks():
            data.extend(chunk)
            dl_bytes = len(data)
            # Update in-memory status every ~5 MB or on completion
            if task_id and (dl_bytes - last_report >= 5_000_000 or dl_bytes >= (blob_size or dl_bytes)):
                last_report = dl_bytes
                delta = dl_bytes - last_persisted
                _persist_download_delta(task_id, delta, download_bytes=dl_bytes, current_blob=blob_path,
                                        phase=f"Download: {blob_path.rsplit('/', 1)[-1]}")
                _persist_download_blob(task_id, blob_path, "DOWNLOADING", dl_bytes, blob_size)
                last_persisted = dl_bytes
                st = _inmemory_tasks.get(task_id)
                if st:
                    st["download_bytes"] = dl_bytes
        raw_bytes = bytes(data)
        _persist_download_blob(task_id, blob_path, "PARSING", len(raw_bytes), blob_size)
        _persist_download_progress(task_id, phase=f"Leitura CSV: {blob_path.rsplit('/', 1)[-1]}")
        # Use the same encoding/delimiter detection as the async extractor.
        # Bronze must contain this exact pre-transformation dataframe.
        df = extractor._parse_csv_bytes(raw_bytes, blob_path)
        raw_df_bronze = df.copy()
    except Exception as e:
        errors.append(f"Failed to read blob {blob_path}: {e}")
        return 0, 0, 0, errors
    finally:
        if task_id:
            # Download completion is distinct from import completion. The
            # latter is updated after Bronze/Silver/Gold succeeds below.
            _persist_download_blob(task_id, blob_path, "DOWNLOADED", content_length or 0, content_length or 0)
            _persist_download_progress(task_id, download_bytes=0, current_blob_size=0)

    billing_period = derive_billing_period(blob_path, df)
    _persist_download_progress(task_id, phase="Bronze: gravando origem original")
    # ── upsert file metadata only (PostgreSQL never stores cost rows) ──
    existing = session.execute(
        select(IngestedBlob).where(
            IngestedBlob.connection_id == conn.id,
            IngestedBlob.blob_path == blob_path,
        )
    ).scalar_one_or_none()

    if existing:
        session.delete(existing)
        session.flush()

    ingested = IngestedBlob(
        connection_id=conn.id,
        blob_path=blob_path,
        blob_prefix=blob_prefix,
        billing_period=billing_period,
        last_modified=last_modified,
        etag=etag or "",
        content_length=content_length or 0,
        row_count=len(df),
        status="INGESTED",
    )
    session.add(ingested)
    session.flush()

    # ── Iceberg Bronze ──
    if not raw_df_bronze.empty and billing_period:
        _persist_download_blob(task_id, blob_path, "BRONZE", content_length or 0, content_length or 0)
        try:
            iceberg.write_bronze(conn.code, raw_df_bronze, billing_period, blob_path)
        except Exception as exc:
            logger.error("Bronze write failed (%s): %s", blob_path, exc)

            session.delete(ingested)
            session.flush()
            return len(df), 0, 0, [f"Bronze write failed: {exc}"]

    # Extraction/Load ends at Bronze. Mapping, Silver, rules and classifications
    # belong exclusively to the later destination reprocess task. Returning here
    # also prevents an existing Silver table from breaking a valid source load.
    _persist_download_progress(task_id, phase="Carga Bronze concluída; origem sem transformação")
    _persist_download_blob(task_id, blob_path, "BRONZE_IMPORTED", content_length or 0, content_length or 0)
    return len(df), 0, 0, []

    # ── map source columns to destination schema (Silver) ──
    targets = _build_destination_targets(
        session, df, conn.id, conn.code, billing_period or "", blob_path
    )
    if not targets:
        message = f"Carga Bronze concluída para a origem {conn.code}; etapa de mapping/Silver não executada."
        _persist_download_progress(task_id, phase=message)
        # This is a valid source-only ingestion. Bronze is complete; Silver is
        # intentionally deferred until the user creates the relationship.
        return len(df), 0, 0, []

    _persist_download_progress(task_id, phase="Silver/Gold: normalizando e aplicando mapping")
    _persist_download_blob(task_id, blob_path, "SILVER_GOLD", content_length or 0, content_length or 0)

    # Rules/classifications run only after mapping, on destination column names.
    normalized_count = 0
    destination_count = 0
    for destination_id, physical_name, target, column_types in targets:
        rules = session.execute(
            select(BusinessRule).where(
                BusinessRule.destination_table_id == destination_id,
                BusinessRule.active == True,
            ).order_by(BusinessRule.priority)
        ).scalars().all()
        class_rules = session.execute(
            select(ClassificationRule).where(
                ClassificationRule.destination_table_id == destination_id,
                ClassificationRule.active == True,
            ).order_by(ClassificationRule.priority)
        ).scalars().all()
        for rule in rules:
            _apply_business_rule_sync(target, rule, billing_period or "")
        _apply_classifications_sync(target, class_rules, session)
        normalized_count += len(target)
        iceberg.write_silver_destination(
            conn.code, physical_name, target, billing_period or "", column_types
        )
        destination_count += iceberg.write_destination(
            physical_name, target, partition_col="_billing_period",
            column_types=column_types,
        )
        try:
            enrich_from_dataframe(session, destination_id, conn.code, target)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Field-value enrichment failed for %s: %s", physical_name, exc)

    _persist_download_blob(task_id, blob_path, "IMPORTED", content_length or 0, content_length or 0)
    return len(df), normalized_count, destination_count, errors


def _build_destination_targets(
    session: Session,
    df: pd.DataFrame,
    connection_id: int,
    connection_code: str,
    billing_period: str,
    blob_path: str,
) -> list[tuple[int, str, pd.DataFrame, dict[str, str]]]:
    """Build Silver frames from source mappings, retaining provenance."""
    mappings = session.execute(
        select(SourceDestinationMapping).where(
            SourceDestinationMapping.source_connection_id == connection_id,
            SourceDestinationMapping.active == True,
        ).order_by(SourceDestinationMapping.priority)
    ).scalars().all()
    grouped: dict[int, list[SourceDestinationMapping]] = {}
    for mapping in mappings:
        grouped.setdefault(mapping.destination_table_id, []).append(mapping)

    result = []
    for destination_id, dest_mappings in grouped.items():
        dest = session.get(DestinationTable, destination_id)
        if not dest:
            continue
        columns = session.execute(
            select(DestinationColumn.column_name, DestinationColumn.data_type)
            .where(DestinationColumn.destination_table_id == destination_id)
            .order_by(DestinationColumn.position)
        ).all()
        names = {name for name, _ in columns}
        types = {name: (dtype or "TEXT") for name, dtype in columns}
        target_data = {}
        for mapping in dest_mappings:
            if mapping.source_column in df.columns:
                target_data[mapping.destination_column] = df[mapping.source_column].values
        for column in df.columns:
            if column in names and column not in target_data:
                target_data[column] = df[column].values
        if not target_data:
            continue
        target = pd.DataFrame(target_data, index=df.index)
        target["source_connection_id"] = connection_id
        target["source_connection_code"] = connection_code
        target["source_provider"] = connection_code.split("-")[0]
        target["source_blob_path"] = blob_path
        target["source_billing_period"] = billing_period
        target["_billing_period"] = billing_period
        types["source_connection_id"] = "INTEGER"
        for name in ("source_connection_code", "source_provider", "source_blob_path", "source_billing_period", "_billing_period"):
            types[name] = "TEXT"
        result.append((destination_id, dest.physical_name, target, types))
    return result


def _apply_mapping_sync(df: pd.DataFrame, mapping: EtlMapping) -> None:
    """Synchronous column mapping."""
    if mapping.transform_type == "RENAME":
        if mapping.source_column in df.columns and mapping.target_column:
            df.rename(columns={mapping.source_column: mapping.target_column}, inplace=True)
    elif mapping.transform_type == "COPY":
        if mapping.source_column in df.columns and mapping.target_column and mapping.target_column not in df.columns:
            df[mapping.target_column] = df[mapping.source_column]
    elif mapping.transform_type == "FORMULA":
        if mapping.target_column:
            try:
                df[mapping.target_column] = df.eval(mapping.source_column, engine="python")
            except Exception:
                pass


def _resolve_scope_filter(
    by_lower: dict[str, str],
    scope_filter: dict | None,
) -> dict | None:
    if not scope_filter or not scope_filter.get("field"):
        return None
    field = str(scope_filter.get("field", ""))
    resolved = by_lower.get(field.lower(), field)
    return {
        **scope_filter,
        "field": resolved,
    }


def _apply_business_rule_sync(df: pd.DataFrame, rule: BusinessRule, billing_period: str) -> None:
    """Synchronous business-rule application."""
    if rule.valid_from and billing_period < rule.valid_from:
        return
    if rule.valid_until and billing_period > rule.valid_until:
        return

    # Rules are configured with logical/source names, while the physical Gold
    # schema may use a different casing. Resolve referenced columns without
    # changing the persisted schema.
    by_lower = {str(column).lower(): column for column in df.columns}
    target_column = by_lower.get(str(rule.target_column or "").lower(), rule.target_column)
    cfg = rule.config_json or {}
    scope_mask = build_scope_mask(
        df,
        _resolve_scope_filter(by_lower, cfg.get("scope_filter")),
    )

    if rule.rule_type == "EXPRESSION" and rule.expression:
        expression = str(rule.expression)
        for logical, physical in sorted(by_lower.items(), key=lambda item: len(item[0]), reverse=True):
            import re
            expression = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(logical)}(?![A-Za-z0-9_])", str(physical), expression, flags=re.IGNORECASE)
        evaluate_expression(df, expression, target_column, scope_mask=scope_mask)
    elif rule.rule_type == "CONDITIONAL" and rule.conditions:
        conditions = []
        for original in rule.conditions:
            condition = dict(original)
            field = condition.get("field", "")
            condition["field"] = by_lower.get(str(field).lower(), field)
            condition["op"] = condition.get("op", condition.get("operator", "=="))
            if condition["op"] == "=":
                condition["op"] = "=="
            condition["then_field"] = by_lower.get(str(condition.get("then_field", target_column)).lower(), target_column)
            then_value = condition.get("then_value")
            if isinstance(then_value, str):
                for logical, physical in sorted(by_lower.items(), key=lambda item: len(item[0]), reverse=True):
                    import re
                    then_value = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(logical)}(?![A-Za-z0-9_])", str(physical), then_value, flags=re.IGNORECASE)
                condition["then_value"] = then_value
            conditions.append(condition)
        evaluate_conditional(df, conditions, scope_mask=scope_mask)
    elif rule.rule_type in ("PTAX_BRL", "TAX_BASE", "TAX_MARKETPLACE", "MARKUP"):
        src = cfg.get("source_column", "")
        rate = float(cfg.get("rate", 1))
        if src in df.columns and rule.target_column:
            # "First match wins" — don't overwrite already-filled values
            if rule.target_column in df.columns:
                unfilled = df[rule.target_column].isna() | (df[rule.target_column].astype(str).str.strip() == "")
                unfilled = unfilled & scope_mask
                result = pd.to_numeric(df[src], errors="coerce").fillna(0) * rate
                df.loc[unfilled, rule.target_column] = result[unfilled]
            else:
                result = pd.to_numeric(df[src], errors="coerce").fillna(0) * rate
                df[target_column] = result.where(scope_mask, np.nan)


def _apply_classifications_sync(df: pd.DataFrame, class_rules: list[ClassificationRule], session: Session | None = None,
                               dept_map: dict | None = None,
                               hierarchy_cache: dict | None = None) -> None:
    """Synchronous classification application. First-match-wins per row.

    Pre-loads departments into dicts (zero DB queries during loop), then applies
    vectorized boolean masks to the DataFrame.

    When dept_map and hierarchy_cache are provided, no DB queries are made.
    When session is provided and caches are None, loads from DB (backward compat).

    For each rule that matches, populates:
    - target_field (e.g., 'departamento') with match_value
    - department hierarchy fields: centro_custo, ambiente, custom_fields
    """
    from app.models.rules import Department
    from app.services.classification_engine import (
        _canon,
        evaluate_rule_conditions,
        normalize_rule_conditions,
    )

    # ── Load department caches from DB only if not pre-loaded ──
    if dept_map is None and hierarchy_cache is None:
        dept_ids_needed = {r.department_id for r in class_rules if r.department_id}

        dept_map = {}

        if dept_ids_needed and session:
            # Classification materializes the complete organizational path.
            # A cache containing only leaves and direct parents silently
            # truncates deeper trees, so keep the full (small) department tree
            # available while walking from a rule leaf to the root.
            depts = session.execute(select(Department)).scalars().all()
            dept_map = {d.id: d for d in depts}

    # Ensure dept_map is always initialized
    if dept_map is None:
        dept_map = {}

    # ── Pre-build department hierarchy chains (cached in dict) ──
    if hierarchy_cache is None:
        hierarchy_cache = {}

    def get_hierarchy(dept_id: int):
        if dept_id in hierarchy_cache:
            return hierarchy_cache[dept_id]
        dept = dept_map.get(dept_id)
        if not dept:
            hierarchy_cache[dept_id] = (None, None, [])
            return (None, None, [])
        # Walk up to root
        visited = [dept]
        current_id = dept.parent_id
        depth = 0
        while current_id and depth < 7:
            parent = dept_map.get(current_id)
            if parent:
                visited.append(parent)
                current_id = parent.parent_id
            else:
                break
            depth += 1
        root = visited[-1] if visited else dept
        result = (dept, root, visited)  # (leaf, root, chain from leaf→root)
        hierarchy_cache[dept_id] = result
        return result

    # ── Ensure ALL hierarchy columns exist BEFORE rule application ──
    for col in ['departamento', 'centro_custo', 'ambiente',
                'custom_fields', 'subscription_rg',
                'departamento_nivel_1', 'departamento_nivel_2',
                'departamento_nivel_3', 'departamento_nivel_4',
                'departamento_nivel_5', 'departamento_nivel_6',
                'departamento_nivel_7']:
        if col not in df.columns:
            df[col] = None

    # ── Pre-compute _canon columns for all unique fields referenced by rules ──
    canon_cache: dict[str, pd.Series] = {}
    unique_fields = set()
    for rule in class_rules:
        for cond in normalize_rule_conditions(rule):
            fld = cond.get("field", "")
            if fld and fld in df.columns:
                unique_fields.add(fld)
    for fld in unique_fields:
        canon_cache[fld] = df[fld].fillna("").astype(str).map(_canon)

    # ── Apply rules ──
    for rule in class_rules:
        # A classification must allocate to an organizational object.  Legacy
        # unlinked rules are deliberately ignored rather than emitting a
        # misleading free-text project with no department hierarchy.
        if not rule.active or not rule.department_id:
            continue
        mask = evaluate_rule_conditions(df, rule, canon_cache=canon_cache)
        if mask is None or not mask.any():
            continue

        target = rule.target_field
        if target not in df.columns:
            df[target] = None
        # First-match-wins: only fill rows where target is still empty
        unfilled = df[target].isna() | (df[target].astype(str).str.strip() == "")
        effective_mask = mask & unfilled
        if not effective_mask.any():
            continue

        df.loc[effective_mask, target] = rule.match_value

        # Resolve department hierarchy from pre-built cache (zero DB queries!)
        dept_id = rule.department_id

        if dept_id:
            leaf, root, chain = get_hierarchy(dept_id)
            if leaf:
                # Walk chain from root→leaf to find first non-null value for each field
                # (root fields serve as fallback when leaf fields are null)
                merged_custom_fields: dict = {}
                centro_custo_val: str | None = None
                ambiente_val: str | None = None
                for d in reversed(chain):  # root → ... → leaf
                    if d.custom_fields and isinstance(d.custom_fields, dict):
                        merged_custom_fields.update(d.custom_fields)
                    if centro_custo_val is None and d.centro_custo:
                        centro_custo_val = d.centro_custo
                    if ambiente_val is None and d.ambiente:
                        ambiente_val = d.ambiente

                for col_name, col_val in [
                    ('departamento', leaf.name),
                    ('centro_custo', centro_custo_val),
                    ('ambiente', ambiente_val),
                ]:
                    if col_val is not None:
                        if col_name not in df.columns:
                            df[col_name] = None
                        df.loc[effective_mask, col_name] = str(col_val)
                # Materialize the individual hierarchy levels (root → leaf) as
                # stable columns so roll-ups (e.g. "departamento pai") become a
                # first-class dimension. nivel_1 = root (diretoria).
                root_to_leaf = list(reversed(chain))
                for i, d in enumerate(root_to_leaf, start=1):
                    col_name = f"departamento_nivel_{i}"
                    if col_name not in df.columns:
                        df[col_name] = None
                    df.loc[effective_mask, col_name] = d.name or ""
                # custom_fields as JSON string (merged from whole chain)
                if merged_custom_fields:
                    if 'custom_fields' not in df.columns:
                        df['custom_fields'] = None
                    import json
                    df.loc[effective_mask, 'custom_fields'] = json.dumps(merged_custom_fields, default=str, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════
#  Destination table writer
# ═══════════════════════════════════════════════════════════════════════

def _write_to_destination_tables_sync(
    session: Session,
    df: pd.DataFrame,
    connection_id: int,
    connection_code: str,
) -> int:
    """Write the processed DataFrame rows to all mapped destination tables (Iceberg).

    Uses SourceDestinationMapping to map source columns → destination columns,
    then writes into Iceberg gold.{physical_name} tables.
    Returns total rows written across all destination tables.
    """
    if df.empty:
        return 0

    # Load all active source→destination mappings for this connection
    mappings = session.execute(
        select(SourceDestinationMapping).where(
            SourceDestinationMapping.source_connection_id == connection_id,
            SourceDestinationMapping.active == True,
        ).order_by(SourceDestinationMapping.priority)
    ).scalars().all()

    if not mappings:
        logger.info("No SourceDestinationMapping for connection %d — skipping destination write", connection_id)
        return 0

    # Group mappings by destination table
    by_table: dict[int, list[SourceDestinationMapping]] = {}
    for m in mappings:
        by_table.setdefault(m.destination_table_id, []).append(m)

    total_written = 0

    for dest_table_id, dest_mappings in by_table.items():
        dest_info = session.get(DestinationTable, dest_table_id)
        if not dest_info:
            continue

        phys_name = dest_info.physical_name

        # Get destination columns and their types (from metadata)
        dest_cols_result = session.execute(
            select(DestinationColumn.column_name, DestinationColumn.data_type).where(
                DestinationColumn.destination_table_id == dest_table_id,
            ).order_by(DestinationColumn.position)
        ).all()
        dest_cols = {r[0] for r in dest_cols_result}
        dest_col_types: dict[str, str] = {r[0]: (r[1] or "TEXT") for r in dest_cols_result}
        # Add internal columns
        dest_col_types["cloud_origem"] = "TEXT"
        dest_col_types["source_connection_id"] = "INTEGER"
        dest_col_types["_billing_period"] = "TEXT"

        # Build target DataFrame
        target_data: dict[str, pd.Series] = {}
        for m in dest_mappings:
            if m.source_column in df.columns:
                target_data[m.destination_column] = df[m.source_column].values

        for col in df.columns:
            if col in dest_cols and col not in target_data:
                target_data[col] = df[col].values

        if not target_data:
            logger.warning("No columns mapped for dest table %s (id=%d)", phys_name, dest_table_id)
            continue

        target = pd.DataFrame(target_data)

        # Add metadata columns
        target["cloud_origem"] = connection_code
        target["source_connection_id"] = connection_id

        # Get billing_period from DataFrame if available for partitioning
        billing_period = "unknown"
        if "_billing_period" in df.columns:
            billing_period = str(df["_billing_period"].iloc[0]) if not df.empty else "unknown"
        elif "billing_period" in df.columns:
            billing_period = str(df["billing_period"].iloc[0]) if not df.empty else "unknown"
        elif "BillingPeriodStart" in df.columns:
            bp = df["BillingPeriodStart"].dropna()
            if not bp.empty:
                billing_period = str(bp.iloc[0])[:7]  # YYYY-MM

        # Filter to only columns that are destination columns (or internal)
        internal_cols = {"cloud_origem", "source_connection_id"}
        valid_cols = [c for c in target.columns if c in dest_cols or c in internal_cols]
        target = target[valid_cols]

        if target.empty:
            continue

        # Convert NaN → None
        target = target.where(pd.notna(target), None)

        # Write to Iceberg — pass column types so schema matches DestinationColumn
        rows_written = iceberg.write_destination(
            phys_name,
            target,
            partition_col="_billing_period",
            column_types=dest_col_types,
        )
        if not rows_written:
            target["_billing_period"] = billing_period
            rows_written = iceberg.write_destination(
                phys_name, target,
                partition_col="_billing_period",
                column_types=dest_col_types,
            )

        total_written += rows_written
        logger.info("Wrote %d rows to Iceberg %s (connection %s)", rows_written, phys_name, connection_code)
        if rows_written:
            try:
                enrich_from_dataframe(session, dest_table_id, connection_code, target)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Field-value enrichment failed for table %s: %s", phys_name, exc)

    return total_written


def _process_gcp_month_sync(
    session: Session,
    extractor: GcpBigQueryExtractor,
    blob_path: str,
    blob_prefix: str,
    last_modified: datetime,
    content_length: Optional[int],
    task_id: Optional[int] = None,
) -> tuple[int, int, int, list[str]]:
    """Ingest one BigQuery invoice month into Bronze."""
    errors: list[str] = []
    conn = extractor.connection
    try:
        table_name, invoice_month = extractor.parse_blob_path(blob_path)
    except ValueError as exc:
        return 0, 0, 0, [str(exc)]

    _persist_download_progress(
        task_id,
        current_blob=blob_path,
        current_blob_size=content_length or 0,
        download_bytes=0,
        phase=f"BigQuery: {table_name} {invoice_month}",
    )
    _persist_download_blob(task_id, blob_path, "QUERYING", 0, content_length or 0)

    existing = session.execute(
        select(IngestedBlob).where(
            IngestedBlob.connection_id == conn.id,
            IngestedBlob.blob_path == blob_path,
        )
    ).scalar_one_or_none()
    if existing:
        session.delete(existing)
        session.flush()

    try:
        iceberg.delete_bronze_slice(conn.code, blob_path=blob_path, billing_period=invoice_month)
        rows_written, ingest_errors = extractor.ingest_month(table_name, invoice_month, blob_path)
        errors.extend(ingest_errors)
    except Exception as exc:
        errors.append(f"GCP ingest failed for {blob_path}: {exc}")
        return 0, 0, 0, errors

    ingested = IngestedBlob(
        connection_id=conn.id,
        blob_path=blob_path,
        blob_prefix=blob_prefix,
        billing_period=invoice_month,
        last_modified=last_modified,
        etag="",
        content_length=rows_written,
        row_count=rows_written,
        status="INGESTED",
    )
    session.add(ingested)
    session.flush()
    _persist_download_blob(task_id, blob_path, "BRONZE_IMPORTED", rows_written, rows_written)
    _persist_download_progress(task_id, phase="Carga Bronze concluída; origem sem transformação")
    return rows_written, 0, 0, errors


def _process_oci_blob_sync(
    session: Session,
    extractor: OciExtractor,
    blob_path: str,
    blob_prefix: str,
    last_modified: datetime,
    content_length: Optional[int],
    task_id: Optional[int] = None,
) -> tuple[int, int, int, list[str]]:
    """Download, parse e carga consolidada do mês OCI em Bronze Iceberg."""
    errors: list[str] = []
    conn = extractor.connection
    tenancy_ocid = conn.config_json.get("tenancy_ocid") or conn.config_json.get("tenancy")
    namespace = conn.config_json.get("namespace", "bling")
    client = extractor._get_object_storage_client()

    # Identificar se é um período agregado (ex: reports/cost-csv#period=202608)
    if "#period=" in blob_path:
        billing_period = blob_path.split("#period=")[-1][:6]
        prefix = blob_path.split("#period=")[0]
    else:
        billing_period = derive_billing_period_oci(blob_path, last_modified)
        prefix = blob_prefix

    try:
        if "#period=" in blob_path:
            # Obter todos os arquivos do mês
            objects_in_period = extractor.get_objects_for_period(billing_period, prefix)
            if not objects_in_period:
                # Se não filtrou por data, tentar listar com prefixo
                objects_in_period = [obj for obj in extractor._list_all_raw_objects(prefix)
                                     if derive_billing_period_oci(obj.name, obj.time_created) == billing_period]
        else:
            # Arquivo individual
            objects_in_period = [type("Obj", (), {"name": blob_path, "size": content_length or 0, "time_created": last_modified})()]

        total_files = len(objects_in_period)
        total_bytes_expected = sum(getattr(obj, "size", 0) or 0 for obj in objects_in_period)

        _persist_download_progress(
            task_id,
            current_blob=blob_path,
            current_blob_size=total_bytes_expected,
            download_bytes=0,
            phase=f"Download OCI ({billing_period}): 0/{total_files} arquivos",
        )
        _persist_download_blob(task_id, blob_path, "DOWNLOADING", 0, total_bytes_expected)

        dfs: list[pd.DataFrame] = []
        cumulative_bytes = 0

        for idx, obj in enumerate(objects_in_period, 1):
            if _check_cancelled(task_id):
                return 0, 0, 0, ["Cancelled"]

            obj_name = obj.name
            response = client.get_object(
                namespace_name=namespace,
                bucket_name=tenancy_ocid,
                object_name=obj_name,
            )
            raw_bytes = response.data.content
            dl_bytes = len(raw_bytes)
            cumulative_bytes += dl_bytes

            _persist_download_delta(
                task_id, dl_bytes, download_bytes=cumulative_bytes,
                current_blob=blob_path,
                phase=f"Download OCI ({billing_period}): {idx}/{total_files} arquivos ({dl_bytes // 1024} KB)"
            )

            df_part = extractor._parse_csv_bytes(raw_bytes, obj_name)
            if not df_part.empty:
                # Agregação parcial imediata por arquivo para economizar memória (reduz de 15k para ~500 linhas)
                df_part_agg = OciExtractor.aggregate_df_daily(df_part)
                dfs.append(df_part_agg)

        if not dfs:
            logger.warning("Nenhum dado encontrado para o período OCI %s", billing_period)
            return 0, 0, 0, [f"Nenhum registro para {billing_period}"]

        _persist_download_blob(task_id, blob_path, "PARSING", cumulative_bytes, total_bytes_expected or cumulative_bytes)
        _persist_download_progress(task_id, phase=f"Consolidando {len(dfs)} arquivos de {billing_period}...")

        full_df = pd.concat(dfs, ignore_index=True)
        # Agregação diária final do mês
        full_df = OciExtractor.aggregate_df_daily(full_df)
        raw_df_bronze = full_df.copy()
    except Exception as e:
        logger.error("Falha ao processar período OCI %s: %s", blob_path, e)
        errors.append(f"Failed to process OCI period {blob_path}: {e}")
        return 0, 0, 0, errors
    finally:
        if task_id:
            _persist_download_blob(task_id, blob_path, "DOWNLOADED", content_length or 0, content_length or 0)
            _persist_download_progress(task_id, download_bytes=0, current_blob_size=0)

    _persist_download_progress(task_id, phase=f"Bronze: gravando consolidado OCI ({len(full_df)} linhas diárias)")

    existing = session.execute(
        select(IngestedBlob).where(
            IngestedBlob.connection_id == conn.id,
            IngestedBlob.blob_path == blob_path,
        )
    ).scalar_one_or_none()

    if existing:
        session.delete(existing)
        session.flush()

    ingested = IngestedBlob(
        connection_id=conn.id,
        blob_path=blob_path,
        blob_prefix=blob_prefix,
        billing_period=billing_period,
        last_modified=last_modified or datetime.now(timezone.utc),
        etag=f"{len(objects_in_period)}_{cumulative_bytes}",
        content_length=cumulative_bytes,
        row_count=len(full_df),
        status="INGESTED",
    )
    session.add(ingested)
    session.flush()

    # Iceberg Bronze
    if not raw_df_bronze.empty and billing_period:
        _persist_download_blob(task_id, blob_path, "BRONZE", cumulative_bytes, cumulative_bytes)
        try:
            iceberg.write_bronze(conn.code, raw_df_bronze, billing_period, blob_path)
        except Exception as exc:
            logger.error("Bronze write failed for OCI (%s): %s", blob_path, exc)
            session.delete(ingested)
            session.flush()
            return len(full_df), 0, 0, [f"Bronze write failed: {exc}"]

    _persist_download_progress(task_id, phase=f"Carga Bronze OCI ({billing_period}) concluída com {len(full_df)} linhas")
    _persist_download_blob(task_id, blob_path, "BRONZE_IMPORTED", cumulative_bytes, cumulative_bytes)
    return len(full_df), 0, 0, []


def _process_oci_blob_sync_worker(
    task_id: int,
    item,
    extractor: OciExtractor,
) -> tuple[int, int, int, list[str]]:
    from app.database import SyncSession as WorkerSync

    if _check_cancelled(task_id):
        return 0, 0, 0, ["Cancelled"]

    _persist_download_progress(task_id, active_downloads=EtlTask.active_downloads + 1)
    if task_id in _inmemory_tasks:
        _inmemory_tasks[task_id]["active_downloads"] = _inmemory_tasks[task_id].get("active_downloads", 0) + 1
    session = WorkerSync()
    try:
        result = _process_oci_blob_sync(
            session,
            extractor,
            item.blob_path,
            item.blob_prefix,
            item.last_modified,
            item.content_length,
            task_id,
        )
        session.commit()
        return result
    finally:
        session.close()
        try:
            s = WorkerSync()
            s.execute(sa_update(EtlTask).where(EtlTask.id == task_id).values(
                active_downloads=EtlTask.active_downloads - 1,
                download_bytes=0,
                current_blob_size=0,
            ))
            s.commit()
            s.close()
            if task_id in _inmemory_tasks:
                _inmemory_tasks[task_id]["active_downloads"] = max(0, _inmemory_tasks[task_id].get("active_downloads", 1) - 1)
        except Exception:
            pass


def _process_mongodb_invoice_sync(
    session: Session,
    extractor: MongoAtlasExtractor,
    blob_path: str,
    blob_prefix: str,
    last_modified,
    content_length,
    task_id: int | None = None,
) -> tuple[int, int, int, list[str]]:
    """Busca os line items de uma invoice MongoDB Atlas e grava em Bronze Iceberg."""
    errors: list[str] = []
    conn = extractor.connection
    invoice_id = blob_path

    try:
        _persist_download_progress(
            task_id,
            current_blob=blob_path,
            current_blob_size=content_length or 0,
            download_bytes=0,
            phase=f"Download MongoDB Atlas: invoice {invoice_id}",
        )
        _persist_download_blob(task_id, blob_path, "DOWNLOADING", 0, content_length or 0)

        df, billing_period, _invoice_fields = extractor.fetch_invoice(invoice_id)
        raw_df_bronze = df.copy()
        total_cents = content_length or 0

        if df.empty:
            logger.info("Invoice MongoDB Atlas %s sem line items (fatura zerada)", invoice_id)

        _persist_download_blob(task_id, blob_path, "PARSING", total_cents, total_cents)
    except Exception as e:
        logger.error("Falha ao processar invoice MongoDB Atlas %s: %s", invoice_id, e)
        errors.append(f"Failed to process MongoDB Atlas invoice {invoice_id}: {e}")
        return 0, 0, 0, errors
    finally:
        if task_id:
            _persist_download_blob(task_id, blob_path, "DOWNLOADED", content_length or 0, content_length or 0)
            _persist_download_progress(task_id, download_bytes=0, current_blob_size=0)

    if not billing_period:
        return 0, 0, 0, [f"Could not derive billing_period for invoice {invoice_id}"]

    _persist_download_progress(task_id, phase=f"Bronze: gravando invoice MongoDB Atlas ({len(df)} line items)")

    existing = session.execute(
        select(IngestedBlob).where(
            IngestedBlob.connection_id == conn.id,
            IngestedBlob.blob_path == blob_path,
        )
    ).scalar_one_or_none()

    if existing:
        session.delete(existing)
        session.flush()

    ingested = IngestedBlob(
        connection_id=conn.id,
        blob_path=blob_path,
        blob_prefix=blob_prefix,
        billing_period=billing_period,
        last_modified=last_modified or datetime.now(timezone.utc),
        etag="",
        content_length=content_length or 0,
        row_count=len(df),
        status="INGESTED",
    )
    session.add(ingested)
    session.flush()

    # Iceberg Bronze
    if not raw_df_bronze.empty:
        _persist_download_blob(task_id, blob_path, "BRONZE", total_cents, total_cents)
        try:
            iceberg.write_bronze(conn.code, raw_df_bronze, billing_period, blob_path)
        except Exception as exc:
            logger.error("Bronze write failed for MongoDB Atlas (%s): %s", blob_path, exc)
            session.delete(ingested)
            session.flush()
            return len(df), 0, 0, [f"Bronze write failed: {exc}"]

    _persist_download_progress(task_id, phase=f"Carga Bronze MongoDB Atlas ({billing_period}) concluída com {len(df)} linhas")
    _persist_download_blob(task_id, blob_path, "BRONZE_IMPORTED", total_cents, total_cents)
    return len(df), 0, 0, []


def _process_github_report_sync(
    session: Session,
    extractor: GithubBillingExtractor,
    blob_path: str,
    blob_prefix: str,
    last_modified,
    content_length,
    task_id: int | None = None,
) -> tuple[int, int, int, list[str]]:
    """Busca o CSV detalhado de billing do GitHub e grava em Bronze Iceberg."""
    errors: list[str] = []
    conn = extractor.connection
    period = blob_path # blob_path carries the billing period (YYYYMM) here

    try:
        _persist_download_progress(
            task_id,
            current_blob=blob_path,
            current_blob_size=content_length or 0,
            download_bytes=0,
            phase=f"Download GitHub Billing: {period}",
        )
        _persist_download_blob(task_id, blob_path, "DOWNLOADING", 0, content_length or 0)

        df, report_id = extractor._fetch_report_for_period(period)
        raw_df_bronze = df.copy()
        
        # O csv pode não ter o tamanho em content_length
        total_size = len(df) * 100 
        _persist_download_blob(task_id, blob_path, "PARSING", total_size, total_size)
    except Exception as e:
        logger.error("Falha ao processar GitHub Billing %s: %s", period, e)
        errors.append(f"Failed to process GitHub Billing {period}: {e}")
        return 0, 0, 0, errors
    finally:
        if task_id:
            _persist_download_blob(task_id, blob_path, "DOWNLOADED", content_length or 0, content_length or 0)
            _persist_download_progress(task_id, download_bytes=0, current_blob_size=0)

    billing_period = period
    _persist_download_progress(task_id, phase=f"Bronze: gravando relatório GitHub ({len(df)} linhas)")

    existing = session.execute(
        select(IngestedBlob).where(
            IngestedBlob.connection_id == conn.id,
            IngestedBlob.blob_path == blob_path,
        )
    ).scalar_one_or_none()

    if existing:
        session.delete(existing)
        session.flush()

    from datetime import datetime, timezone
    ingested = IngestedBlob(
        connection_id=conn.id,
        blob_path=blob_path,
        blob_prefix=blob_prefix,
        billing_period=billing_period,
        last_modified=last_modified or datetime.now(timezone.utc),
        etag="",
        content_length=content_length or 0,
        row_count=len(df),
        status="INGESTED",
    )
    session.add(ingested)
    session.flush()

    if not raw_df_bronze.empty:
        _persist_download_blob(task_id, blob_path, "BRONZE", total_size, total_size)
        try:
            iceberg.write_bronze(conn.code, raw_df_bronze, billing_period, blob_path)
        except Exception as exc:
            logger.error("Bronze write failed for GitHub Billing (%s): %s", blob_path, exc)
            session.delete(ingested)
            session.flush()
            return len(df), 0, 0, [f"Bronze write failed: {exc}"]

    _persist_download_progress(task_id, phase=f"Carga Bronze GitHub Billing ({billing_period}) concluída com {len(df)} linhas")
    _persist_download_blob(task_id, blob_path, "BRONZE_IMPORTED", total_size, total_size)
    return len(df), 0, 0, []


# ── GitHub License ────────────────────────────────────────────────────────────

def _process_github_license_sync(
    session: Session,
    extractor: GithubLicenseExtractor,
    blob_path: str,
    blob_prefix: str,
    last_modified,
    content_length,
    task_id: int | None = None,
) -> tuple[int, int, int, list[str]]:
    """Coleta usuários licenciados do GitHub Enterprise e grava em Bronze Iceberg."""
    errors: list[str] = []
    conn = extractor.connection

    try:
        _persist_download_progress(
            task_id,
            current_blob=blob_path,
            current_blob_size=0,
            download_bytes=0,
            phase="Download GitHub License: buscando usuários licenciados...",
        )
        _persist_download_blob(task_id, blob_path, "DOWNLOADING", 0, 0)

        df = extractor._fetch_licenses()
        total_size = len(df) * 100
        _persist_download_blob(task_id, blob_path, "PARSING", total_size, total_size)
    except Exception as e:
        logger.error("[GithubLicense] Falha ao coletar licenças: %s", e)
        errors.append(f"Failed to process GitHub License: {e}")
        return 0, 0, 0, errors
    finally:
        _persist_download_blob(task_id, blob_path, "DOWNLOADED", 0, 0)
        _persist_download_progress(task_id, download_bytes=0, current_blob_size=0)

    _persist_download_progress(task_id, phase=f"Bronze: gravando licenças GitHub ({len(df)} usuários)")

    existing = session.execute(
        select(IngestedBlob).where(
            IngestedBlob.connection_id == conn.id,
            IngestedBlob.blob_path == blob_path,
        )
    ).scalar_one_or_none()
    if existing:
        session.delete(existing)
        session.flush()

    from datetime import datetime, timezone
    ingested = IngestedBlob(
        connection_id=conn.id,
        blob_path=blob_path,
        blob_prefix=blob_prefix,
        billing_period=None,
        last_modified=last_modified or datetime.now(timezone.utc),
        etag="",
        content_length=0,
        row_count=len(df),
        status="INGESTED",
    )
    session.add(ingested)
    session.flush()

    if not df.empty:
        _persist_download_blob(task_id, blob_path, "BRONZE", total_size, total_size)
        try:
            iceberg.write_bronze(conn.code, df, None, blob_path)
        except Exception as exc:
            logger.error("[GithubLicense] Bronze write failed: %s", exc)
            session.delete(ingested)
            session.flush()
            return len(df), 0, 0, [f"Bronze write failed: {exc}"]

    _persist_download_progress(task_id, phase=f"Carga Bronze GitHub License concluída com {len(df)} usuários")
    _persist_download_blob(task_id, blob_path, "BRONZE_IMPORTED", total_size, total_size)
    return len(df), 0, 0, []


def _process_github_license_sync_worker(
    task_id: int,
    item,
    extractor: GithubLicenseExtractor,
) -> tuple[int, int, int, list[str]]:
    from app.database import SyncSession as WorkerSync
    if _check_cancelled(task_id):
        return 0, 0, 0, ["Cancelled"]
    _persist_download_progress(task_id, active_downloads=EtlTask.active_downloads + 1)
    if task_id in _inmemory_tasks:
        _inmemory_tasks[task_id]["active_downloads"] = _inmemory_tasks[task_id].get("active_downloads", 0) + 1
    session = WorkerSync()
    try:
        result = _process_github_license_sync(
            session, extractor, item.blob_path, item.blob_prefix,
            item.last_modified, item.content_length, task_id,
        )
        session.commit()
        return result
    finally:
        session.close()
        try:
            s = WorkerSync()
            s.execute(sa_update(EtlTask).where(EtlTask.id == task_id).values(
                active_downloads=EtlTask.active_downloads - 1, download_bytes=0, current_blob_size=0,
            ))
            s.commit()
            s.close()
            if task_id in _inmemory_tasks:
                _inmemory_tasks[task_id]["active_downloads"] = max(0, _inmemory_tasks[task_id].get("active_downloads", 1) - 1)
        except Exception:
            pass


# ── GitHub Token (Copilot Seats) ──────────────────────────────────────────────

def _process_github_token_sync(
    session: Session,
    extractor: GithubTokenExtractor,
    blob_path: str,
    blob_prefix: str,
    last_modified,
    content_length,
    task_id: int | None = None,
) -> tuple[int, int, int, list[str]]:
    """Gera/baixa relatório detalhado de consumo de tokens do GitHub e grava em Bronze Iceberg."""
    billing_period = blob_path
    errors: list[str] = []
    conn = extractor.connection

    try:
        _persist_download_progress(
            task_id,
            current_blob=blob_path,
            current_blob_size=0,
            download_bytes=0,
            phase=f"Download GitHub Token ({billing_period}): solicitando relatório de consumo de tokens...",
        )
        _persist_download_blob(task_id, blob_path, "DOWNLOADING", 0, 0)

        df, report_id = extractor._fetch_report_for_period(billing_period)
        total_size = len(df) * 150
        _persist_download_blob(task_id, blob_path, "PARSING", total_size, total_size)
    except Exception as e:
        logger.error("[GithubToken] Falha ao processar período %s: %s", billing_period, e)
        errors.append(f"Failed to process GitHub Token {billing_period}: {e}")
        return 0, 0, 0, errors
    finally:
        _persist_download_blob(task_id, blob_path, "DOWNLOADED", 0, 0)
        _persist_download_progress(task_id, download_bytes=0, current_blob_size=0)

    _persist_download_progress(task_id, phase=f"Bronze: gravando consumo de tokens GitHub ({billing_period}) com {len(df)} linhas")

    existing = session.execute(
        select(IngestedBlob).where(
            IngestedBlob.connection_id == conn.id,
            IngestedBlob.blob_path == blob_path,
        )
    ).scalar_one_or_none()
    if existing:
        session.delete(existing)
        session.flush()

    from datetime import datetime, timezone
    ingested = IngestedBlob(
        connection_id=conn.id,
        blob_path=blob_path,
        blob_prefix=blob_prefix,
        billing_period=billing_period,
        last_modified=last_modified or datetime.now(timezone.utc),
        etag=report_id or "",
        content_length=total_size,
        row_count=len(df),
        status="INGESTED",
    )
    session.add(ingested)
    session.flush()

    if not df.empty:
        _persist_download_blob(task_id, blob_path, "BRONZE", total_size, total_size)
        try:
            iceberg.write_bronze(conn.code, df, billing_period, blob_path)
        except Exception as exc:
            logger.error("Bronze write failed for GitHub Token (%s): %s", blob_path, exc)
            session.delete(ingested)
            session.flush()
            return len(df), 0, 0, [f"Bronze write failed: {exc}"]

    _persist_download_progress(task_id, phase=f"Carga Bronze GitHub Token ({billing_period}) concluída com {len(df)} linhas")
    _persist_download_blob(task_id, blob_path, "BRONZE_IMPORTED", total_size, total_size)
    return len(df), 0, 0, []



def _process_github_token_sync_worker(
    task_id: int,
    item,
    extractor: GithubTokenExtractor,
) -> tuple[int, int, int, list[str]]:
    from app.database import SyncSession as WorkerSync
    if _check_cancelled(task_id):
        return 0, 0, 0, ["Cancelled"]
    _persist_download_progress(task_id, active_downloads=EtlTask.active_downloads + 1)
    if task_id in _inmemory_tasks:
        _inmemory_tasks[task_id]["active_downloads"] = _inmemory_tasks[task_id].get("active_downloads", 0) + 1
    session = WorkerSync()
    try:
        result = _process_github_token_sync(
            session, extractor, item.blob_path, item.blob_prefix,
            item.last_modified, item.content_length, task_id,
        )
        session.commit()
        return result
    finally:
        session.close()
        try:
            s = WorkerSync()
            s.execute(sa_update(EtlTask).where(EtlTask.id == task_id).values(
                active_downloads=EtlTask.active_downloads - 1, download_bytes=0, current_blob_size=0,
            ))
            s.commit()
            s.close()
            if task_id in _inmemory_tasks:
                _inmemory_tasks[task_id]["active_downloads"] = max(0, _inmemory_tasks[task_id].get("active_downloads", 1) - 1)
        except Exception:
            pass


# ── NotebookLM Licenças ────────────────────────────────────────────────────────

def _process_notebooklm_license_sync(
    session: Session,
    extractor: NotebookLMLicenseExtractor,
    blob_path: str,
    blob_prefix: str,
    last_modified,
    content_length,
    task_id: int | None = None,
) -> tuple[int, int, int, list[str]]:
    """Coleta lista completa de usuários licenciados do NotebookLM e grava em Bronze Iceberg."""
    errors: list[str] = []
    conn = extractor.connection

    try:
        _persist_download_progress(
            task_id,
            current_blob=blob_path,
            current_blob_size=0,
            download_bytes=0,
            phase="Download NotebookLM Licenças: coletando colaboradores...",
        )
        _persist_download_blob(task_id, blob_path, "DOWNLOADING", 0, 0)

        df = extractor._fetch_licenses()
        total_size = len(df) * 120
        _persist_download_blob(task_id, blob_path, "PARSING", total_size, total_size)
    except Exception as e:
        logger.error("[NotebookLMLicense] Falha ao coletar dados: %s", e)
        errors.append(f"Failed to process NotebookLM License: {e}")
        return 0, 0, 0, errors
    finally:
        _persist_download_blob(task_id, blob_path, "DOWNLOADED", 0, 0)
        _persist_download_progress(task_id, download_bytes=0, current_blob_size=0)

    _persist_download_progress(task_id, phase=f"Bronze: gravando licenças NotebookLM ({len(df)} registros)")

    existing = session.execute(
        select(IngestedBlob).where(
            IngestedBlob.connection_id == conn.id,
            IngestedBlob.blob_path == blob_path,
        )
    ).scalar_one_or_none()
    if existing:
        session.delete(existing)
        session.flush()

    from datetime import datetime, timezone
    ingested = IngestedBlob(
        connection_id=conn.id,
        blob_path=blob_path,
        blob_prefix=blob_prefix,
        billing_period=datetime.now(timezone.utc).strftime("%Y%m"),
        last_modified=last_modified or datetime.now(timezone.utc),
        etag="",
        content_length=total_size,
        row_count=len(df),
        status="INGESTED",
    )
    session.add(ingested)
    session.flush()

    if not df.empty:
        _persist_download_blob(task_id, blob_path, "BRONZE", total_size, total_size)
        try:
            iceberg.write_bronze(conn.code, df, None, blob_path)
        except Exception as exc:
            logger.error("[NotebookLMLicense] Bronze write failed: %s", exc)
            session.delete(ingested)
            session.flush()
            return len(df), 0, 0, [f"Bronze write failed: {exc}"]

    _persist_download_progress(task_id, phase=f"Carga Bronze NotebookLM Licenças concluída com {len(df)} usuários")
    _persist_download_blob(task_id, blob_path, "BRONZE_IMPORTED", total_size, total_size)
    return len(df), 0, 0, []


def _process_notebooklm_license_sync_worker(
    task_id: int,
    item,
    extractor: NotebookLMLicenseExtractor,
) -> tuple[int, int, int, list[str]]:
    from app.database import SyncSession as WorkerSync
    if _check_cancelled(task_id):
        return 0, 0, 0, ["Cancelled"]
    _persist_download_progress(task_id, active_downloads=EtlTask.active_downloads + 1)
    if task_id in _inmemory_tasks:
        _inmemory_tasks[task_id]["active_downloads"] = _inmemory_tasks[task_id].get("active_downloads", 0) + 1
    session = WorkerSync()
    try:
        result = _process_notebooklm_license_sync(
            session, extractor, item.blob_path, item.blob_prefix,
            item.last_modified, item.content_length, task_id,
        )
        session.commit()
        return result
    finally:
        session.close()
        try:
            s = WorkerSync()
            s.execute(sa_update(EtlTask).where(EtlTask.id == task_id).values(
                active_downloads=EtlTask.active_downloads - 1, download_bytes=0, current_blob_size=0,
            ))
            s.commit()
            s.close()
            if task_id in _inmemory_tasks:
                _inmemory_tasks[task_id]["active_downloads"] = max(0, _inmemory_tasks[task_id].get("active_downloads", 1) - 1)
        except Exception:
            pass


# ── NotebookLM Tokens (Consumo de Tokens por Mês) ─────────────────────────────

def _process_notebooklm_token_sync(
    session: Session,
    extractor: NotebookLMTokenExtractor,
    blob_path: str,
    blob_prefix: str,
    last_modified,
    content_length,
    task_id: int | None = None,
) -> tuple[int, int, int, list[str]]:
    """Gera relatório de consumo de tokens NotebookLM para o período e grava em Bronze Iceberg."""
    errors: list[str] = []
    conn = extractor.connection
    period = blob_path

    try:
        _persist_download_progress(
            task_id,
            current_blob=blob_path,
            current_blob_size=0,
            download_bytes=0,
            phase=f"Download NotebookLM Tokens: gerando telemetria {period}...",
        )
        _persist_download_blob(task_id, blob_path, "DOWNLOADING", 0, 0)

        df = extractor._fetch_tokens_for_period(period)
        total_size = len(df) * 150
        _persist_download_blob(task_id, blob_path, "PARSING", total_size, total_size)
    except Exception as e:
        logger.error("[NotebookLMToken] Falha ao coletar dados para período %s: %s", period, e)
        errors.append(f"Failed to process NotebookLM Token for {period}: {e}")
        return 0, 0, 0, errors
    finally:
        _persist_download_blob(task_id, blob_path, "DOWNLOADED", 0, 0)
        _persist_download_progress(task_id, download_bytes=0, current_blob_size=0)

    _persist_download_progress(task_id, phase=f"Bronze: gravando tokens NotebookLM {period} ({len(df)} linhas)")

    existing = session.execute(
        select(IngestedBlob).where(
            IngestedBlob.connection_id == conn.id,
            IngestedBlob.blob_path == blob_path,
        )
    ).scalar_one_or_none()
    if existing:
        session.delete(existing)
        session.flush()

    from datetime import datetime, timezone
    ingested = IngestedBlob(
        connection_id=conn.id,
        blob_path=blob_path,
        blob_prefix=blob_prefix,
        billing_period=period,
        last_modified=last_modified or datetime.now(timezone.utc),
        etag="",
        content_length=total_size,
        row_count=len(df),
        status="INGESTED",
    )
    session.add(ingested)
    session.flush()

    if not df.empty:
        _persist_download_blob(task_id, blob_path, "BRONZE", total_size, total_size)
        try:
            iceberg.write_bronze(conn.code, df, None, blob_path)
        except Exception as exc:
            logger.error("[NotebookLMToken] Bronze write failed: %s", exc)
            session.delete(ingested)
            session.flush()
            return len(df), 0, 0, [f"Bronze write failed: {exc}"]

    _persist_download_progress(task_id, phase=f"Carga Bronze NotebookLM Tokens {period} concluída com {len(df)} registros")
    _persist_download_blob(task_id, blob_path, "BRONZE_IMPORTED", total_size, total_size)
    return len(df), 0, 0, []


def _process_notebooklm_token_sync_worker(
    task_id: int,
    item,
    extractor: NotebookLMTokenExtractor,
) -> tuple[int, int, int, list[str]]:
    from app.database import SyncSession as WorkerSync
    if _check_cancelled(task_id):
        return 0, 0, 0, ["Cancelled"]
    _persist_download_progress(task_id, active_downloads=EtlTask.active_downloads + 1)
    if task_id in _inmemory_tasks:
        _inmemory_tasks[task_id]["active_downloads"] = _inmemory_tasks[task_id].get("active_downloads", 0) + 1
    session = WorkerSync()
    try:
        result = _process_notebooklm_token_sync(
            session, extractor, item.blob_path, item.blob_prefix,
            item.last_modified, item.content_length, task_id,
        )
        session.commit()
        return result
    finally:
        session.close()
        try:
            s = WorkerSync()
            s.execute(sa_update(EtlTask).where(EtlTask.id == task_id).values(
                active_downloads=EtlTask.active_downloads - 1, download_bytes=0, current_blob_size=0,
            ))
            s.commit()
            s.close()
            if task_id in _inmemory_tasks:
                _inmemory_tasks[task_id]["active_downloads"] = max(0, _inmemory_tasks[task_id].get("active_downloads", 1) - 1)
        except Exception:
            pass


# ── Claude Licenças (Anthropic) ────────────────────────────────────────────────

def _process_claude_license_sync(
    session: Session,
    extractor: ClaudeLicenseExtractor,
    blob_path: str,
    blob_prefix: str,
    last_modified,
    content_length,
    task_id: int | None = None,
) -> tuple[int, int, int, list[str]]:
    """Coleta lista de colaboradores licenciados do Claude Enterprise e grava em Bronze Iceberg."""
    errors: list[str] = []
    conn = extractor.connection

    try:
        _persist_download_progress(
            task_id,
            current_blob=blob_path,
            current_blob_size=0,
            download_bytes=0,
            phase="Download Claude Licenças: consultando colaboradores...",
        )
        _persist_download_blob(task_id, blob_path, "DOWNLOADING", 0, 0)

        df = extractor._fetch_licenses()
        total_size = len(df) * 120
        _persist_download_blob(task_id, blob_path, "PARSING", total_size, total_size)
    except Exception as e:
        logger.error("[ClaudeLicense] Falha ao coletar dados: %s", e)
        errors.append(f"Failed to process Claude License: {e}")
        return 0, 0, 0, errors
    finally:
        _persist_download_blob(task_id, blob_path, "DOWNLOADED", 0, 0)
        _persist_download_progress(task_id, download_bytes=0, current_blob_size=0)

    _persist_download_progress(task_id, phase=f"Bronze: gravando licenças Claude Enterprise ({len(df)} registros)")

    existing = session.execute(
        select(IngestedBlob).where(
            IngestedBlob.connection_id == conn.id,
            IngestedBlob.blob_path == blob_path,
        )
    ).scalar_one_or_none()
    if existing:
        session.delete(existing)
        session.flush()

    from datetime import datetime, timezone
    ingested = IngestedBlob(
        connection_id=conn.id,
        blob_path=blob_path,
        blob_prefix=blob_prefix,
        billing_period=datetime.now(timezone.utc).strftime("%Y%m"),
        last_modified=last_modified or datetime.now(timezone.utc),
        etag="",
        content_length=total_size,
        row_count=len(df),
        status="INGESTED",
    )
    session.add(ingested)
    session.flush()

    if not df.empty:
        _persist_download_blob(task_id, blob_path, "BRONZE", total_size, total_size)
        try:
            iceberg.write_bronze(conn.code, df, None, blob_path)
        except Exception as exc:
            logger.error("[ClaudeLicense] Bronze write failed: %s", exc)
            session.delete(ingested)
            session.flush()
            return len(df), 0, 0, [f"Bronze write failed: {exc}"]

    _persist_download_progress(task_id, phase=f"Carga Bronze Claude Licenças concluída com {len(df)} colaboradores")
    _persist_download_blob(task_id, blob_path, "BRONZE_IMPORTED", total_size, total_size)
    return len(df), 0, 0, []


def _process_claude_license_sync_worker(
    task_id: int,
    item,
    extractor: ClaudeLicenseExtractor,
) -> tuple[int, int, int, list[str]]:
    from app.database import SyncSession as WorkerSync
    if _check_cancelled(task_id):
        return 0, 0, 0, ["Cancelled"]
    _persist_download_progress(task_id, active_downloads=EtlTask.active_downloads + 1)
    if task_id in _inmemory_tasks:
        _inmemory_tasks[task_id]["active_downloads"] = _inmemory_tasks[task_id].get("active_downloads", 0) + 1
    session = WorkerSync()
    try:
        result = _process_claude_license_sync(
            session, extractor, item.blob_path, item.blob_prefix,
            item.last_modified, item.content_length, task_id,
        )
        session.commit()
        return result
    finally:
        session.close()
        try:
            s = WorkerSync()
            s.execute(sa_update(EtlTask).where(EtlTask.id == task_id).values(
                active_downloads=EtlTask.active_downloads - 1, download_bytes=0, current_blob_size=0,
            ))
            s.commit()
            s.close()
            if task_id in _inmemory_tasks:
                _inmemory_tasks[task_id]["active_downloads"] = max(0, _inmemory_tasks[task_id].get("active_downloads", 1) - 1)
        except Exception:
            pass


# ── Claude Tokens (Consumo de Tokens por Mês) ──────────────────────────────────

def _process_claude_token_sync(
    session: Session,
    extractor: ClaudeTokenExtractor,
    blob_path: str,
    blob_prefix: str,
    last_modified,
    content_length,
    task_id: int | None = None,
) -> tuple[int, int, int, list[str]]:
    """Gera relatório de telemetria de tokens Claude e grava em Bronze Iceberg."""
    errors: list[str] = []
    conn = extractor.connection
    period = blob_path

    try:
        _persist_download_progress(
            task_id,
            current_blob=blob_path,
            current_blob_size=0,
            download_bytes=0,
            phase=f"Download Claude Tokens: gerando telemetria {period}...",
        )
        _persist_download_blob(task_id, blob_path, "DOWNLOADING", 0, 0)

        df = extractor._fetch_tokens_for_period(period)
        total_size = len(df) * 150
        _persist_download_blob(task_id, blob_path, "PARSING", total_size, total_size)
    except Exception as e:
        logger.error("[ClaudeToken] Falha ao coletar dados para período %s: %s", period, e)
        errors.append(f"Failed to process Claude Token for {period}: {e}")
        return 0, 0, 0, errors
    finally:
        _persist_download_blob(task_id, blob_path, "DOWNLOADED", 0, 0)
        _persist_download_progress(task_id, download_bytes=0, current_blob_size=0)

    _persist_download_progress(task_id, phase=f"Bronze: gravando tokens Claude {period} ({len(df)} linhas)")

    existing = session.execute(
        select(IngestedBlob).where(
            IngestedBlob.connection_id == conn.id,
            IngestedBlob.blob_path == blob_path,
        )
    ).scalar_one_or_none()
    if existing:
        session.delete(existing)
        session.flush()

    from datetime import datetime, timezone
    ingested = IngestedBlob(
        connection_id=conn.id,
        blob_path=blob_path,
        blob_prefix=blob_prefix,
        billing_period=period,
        last_modified=last_modified or datetime.now(timezone.utc),
        etag="",
        content_length=total_size,
        row_count=len(df),
        status="INGESTED",
    )
    session.add(ingested)
    session.flush()

    if not df.empty:
        _persist_download_blob(task_id, blob_path, "BRONZE", total_size, total_size)
        try:
            iceberg.write_bronze(conn.code, df, None, blob_path)
        except Exception as exc:
            logger.error("[ClaudeToken] Bronze write failed: %s", exc)
            session.delete(ingested)
            session.flush()
            return len(df), 0, 0, [f"Bronze write failed: {exc}"]

    _persist_download_progress(task_id, phase=f"Carga Bronze Claude Tokens {period} concluída com {len(df)} registros")
    _persist_download_blob(task_id, blob_path, "BRONZE_IMPORTED", total_size, total_size)
    return len(df), 0, 0, []


def _process_claude_token_sync_worker(
    task_id: int,
    item,
    extractor: ClaudeTokenExtractor,
) -> tuple[int, int, int, list[str]]:
    from app.database import SyncSession as WorkerSync
    if _check_cancelled(task_id):
        return 0, 0, 0, ["Cancelled"]
    _persist_download_progress(task_id, active_downloads=EtlTask.active_downloads + 1)
    if task_id in _inmemory_tasks:
        _inmemory_tasks[task_id]["active_downloads"] = _inmemory_tasks[task_id].get("active_downloads", 0) + 1
    session = WorkerSync()
    try:
        result = _process_claude_token_sync(
            session, extractor, item.blob_path, item.blob_prefix,
            item.last_modified, item.content_length, task_id,
        )
        session.commit()
        return result
    finally:
        session.close()
        try:
            s = WorkerSync()
            s.execute(sa_update(EtlTask).where(EtlTask.id == task_id).values(
                active_downloads=EtlTask.active_downloads - 1, download_bytes=0, current_blob_size=0,
            ))
            s.commit()
            s.close()
            if task_id in _inmemory_tasks:
                _inmemory_tasks[task_id]["active_downloads"] = max(0, _inmemory_tasks[task_id].get("active_downloads", 1) - 1)
        except Exception:
            pass


def _process_mongodb_invoice_sync_worker(
    task_id: int,
    item,
    extractor: MongoAtlasExtractor,
) -> tuple[int, int, int, list[str]]:
    from app.database import SyncSession as WorkerSync

    if _check_cancelled(task_id):
        return 0, 0, 0, ["Cancelled"]

    _persist_download_progress(task_id, active_downloads=EtlTask.active_downloads + 1)
    if task_id in _inmemory_tasks:
        _inmemory_tasks[task_id]["active_downloads"] = _inmemory_tasks[task_id].get("active_downloads", 0) + 1
    session = WorkerSync()
    try:
        result = _process_mongodb_invoice_sync(
            session,
            extractor,
            item.blob_path,
            item.blob_prefix,
            item.last_modified,
            item.content_length,
            task_id,
        )
        session.commit()
        return result
    finally:
        session.close()
        try:
            s = WorkerSync()
            s.execute(sa_update(EtlTask).where(EtlTask.id == task_id).values(
                active_downloads=EtlTask.active_downloads - 1,
                download_bytes=0,
                current_blob_size=0,
            ))
            s.commit()
            s.close()
            if task_id in _inmemory_tasks:
                _inmemory_tasks[task_id]["active_downloads"] = max(0, _inmemory_tasks[task_id].get("active_downloads", 1) - 1)
        except Exception:
            pass


def _process_github_report_sync_worker(
    task_id: int,
    item,
    extractor: GithubBillingExtractor,
) -> tuple[int, int, int, list[str]]:
    from app.database import SyncSession as WorkerSync

    if _check_cancelled(task_id):
        return 0, 0, 0, ["Cancelled"]

    _persist_download_progress(task_id, active_downloads=EtlTask.active_downloads + 1)
    if task_id in _inmemory_tasks:
        _inmemory_tasks[task_id]["active_downloads"] = _inmemory_tasks[task_id].get("active_downloads", 0) + 1
    session = WorkerSync()
    try:
        result = _process_github_report_sync(
            session,
            extractor,
            item.blob_path,
            item.blob_prefix,
            item.last_modified,
            item.content_length,
            task_id,
        )
        session.commit()
        return result
    finally:
        session.close()
        try:
            s = WorkerSync()
            s.execute(sa_update(EtlTask).where(EtlTask.id == task_id).values(
                active_downloads=EtlTask.active_downloads - 1,
                download_bytes=0,
                current_blob_size=0,
            ))
            s.commit()
            s.close()
            if task_id in _inmemory_tasks:
                _inmemory_tasks[task_id]["active_downloads"] = max(0, _inmemory_tasks[task_id].get("active_downloads", 1) - 1)
        except Exception:
            pass


def _process_gcp_month_sync_worker(
    task_id: int,
    item,
    extractor: GcpBigQueryExtractor,
) -> tuple[int, int, int, list[str]]:
    from app.database import SyncSession as WorkerSync

    if _check_cancelled(task_id):
        return 0, 0, 0, ["Cancelled"]

    _persist_download_progress(task_id, active_downloads=EtlTask.active_downloads + 1)
    if task_id in _inmemory_tasks:
        _inmemory_tasks[task_id]["active_downloads"] = _inmemory_tasks[task_id].get("active_downloads", 0) + 1
    session = WorkerSync()
    try:
        result = _process_gcp_month_sync(
            session,
            extractor,
            item.blob_path,
            item.blob_prefix,
            item.last_modified,
            item.content_length,
            task_id,
        )
        session.commit()
        return result
    finally:
        session.close()
        try:
            s = WorkerSync()
            s.execute(sa_update(EtlTask).where(EtlTask.id == task_id).values(
                active_downloads=EtlTask.active_downloads - 1,
                download_bytes=0,
                current_blob_size=0,
            ))
            s.commit()
            s.close()
            if task_id in _inmemory_tasks:
                _inmemory_tasks[task_id]["active_downloads"] = max(0, _inmemory_tasks[task_id].get("active_downloads", 1) - 1)
        except Exception:
            pass


def _process_blob_sync_worker(
    task_id: int,
    item,
    extractor: AzureExtractor,
) -> tuple[int, int, int, list[str]]:
    """Wrapper for _process_blob_sync that creates its own DB session.

    SQLAlchemy sessions are NOT thread-safe, so each worker thread
    must create its own SyncSession.
    """
    from app.database import SyncSession as WorkerSync

    if _check_cancelled(task_id):
        return 0, 0, 0, ["Cancelled"]

    _persist_download_progress(task_id, active_downloads=EtlTask.active_downloads + 1)
    if task_id in _inmemory_tasks:
        _inmemory_tasks[task_id]["active_downloads"] = _inmemory_tasks[task_id].get("active_downloads", 0) + 1
    session = WorkerSync()
    try:
        result = _process_blob_sync(
            session, extractor,
            item.blob_path, item.blob_prefix,
            item.last_modified, item.content_length, None,
            task_id,
        )
        session.commit()
        return result
    finally:
        session.close()
        try:
            s = WorkerSync()
            s.execute(sa_update(EtlTask).where(EtlTask.id == task_id).values(
                active_downloads=EtlTask.active_downloads - 1,
                download_bytes=0,
                current_blob_size=0,
            ))
            s.commit()
            s.close()
            if task_id in _inmemory_tasks:
                _inmemory_tasks[task_id]["active_downloads"] = max(0, _inmemory_tasks[task_id].get("active_downloads", 1) - 1)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════
#  Main ETL thread function
# ═══════════════════════════════════════════════════════════════════════

def _check_cancelled(task_id: int) -> bool:
    """Check if task has been cancelled — cross-process safe via DB + in-memory flag."""
    if task_id in _cancel_flags:
        return True
    try:
        from app.database import SyncSession
        from app.models.etl_task import EtlTask
        db = SyncSession()
        row = db.get(EtlTask, task_id)
        db.close()
        if row and row.status == "CANCELLED":
            _cancel_flags.add(task_id)
            return True
    except Exception:
        pass
    return False


def _save_progress_sync(task_id: int, result) -> None:
    """Persist task progress to DB."""
    try:
        s = SyncSession()
        t = s.get(EtlTask, task_id)
        if t:
            t.rows_raw = result.rows_raw
            t.rows_normalized = result.rows_normalized
            t.rows_destination = result.rows_destination
            t.processed_blobs = result.processed_blobs
            t.current_blob = result.current_blob
            s.commit()
        s.close()
    except Exception:
        pass


def _run_etl_sync(
    task_id: int,
    connection_id: int,
    selected_blobs: Optional[list[str]],
    force_periods: Optional[list[str]],
) -> None:
    """Run the full ETL pipeline synchronously inside a worker thread."""
    status = _inmemory_tasks.get(task_id)
    if not status:
        return

    session = SyncSession()
    try:
        result = session.get(EtlTask, task_id)
        if not result:
            return

        conn = session.get(SourceConnection, connection_id)
        if not conn or conn.connector_type not in ("azure_blob", "gcp_bigquery", "oci", "mongodb_atlas", "github_license", "github_token", "notebooklm_license", "notebooklm_token", "claude_license", "claude_token"):
            result.status = "FAILED"
            result.errors = ["Invalid connection"]
            result.finished_at = datetime.now(timezone.utc)
            session.commit()
            status["status"] = "FAILED"
            return

        if conn.connector_type == "azure_blob":
            extractor = AzureExtractor(conn)
            scan_items = extractor._scan_sync(session)
            worker_fn = _process_blob_sync_worker
        elif conn.connector_type == "oci":
            extractor = OciExtractor(conn)
            scan_items = extractor._scan_sync(session)
            worker_fn = _process_oci_blob_sync_worker
        elif conn.connector_type == "mongodb_atlas":
            extractor = MongoAtlasExtractor(conn)
            scan_items = extractor._scan_sync(session)
            worker_fn = _process_mongodb_invoice_sync_worker
        elif conn.connector_type == "github_license":
            extractor = GithubLicenseExtractor(conn)
            scan_items = extractor._scan_sync(session)
            worker_fn = _process_github_license_sync_worker
        elif conn.connector_type == "github_token":
            extractor = GithubTokenExtractor(conn)
            scan_items = extractor._scan_sync(session)
            worker_fn = _process_github_token_sync_worker
        elif conn.connector_type == "notebooklm_license":
            extractor = NotebookLMLicenseExtractor(conn)
            scan_items = extractor._scan_sync(session)
            worker_fn = _process_notebooklm_license_sync_worker
        elif conn.connector_type == "notebooklm_token":
            extractor = NotebookLMTokenExtractor(conn)
            scan_items = extractor._scan_sync(session)
            worker_fn = _process_notebooklm_token_sync_worker
        elif conn.connector_type == "claude_license":
            extractor = ClaudeLicenseExtractor(conn)
            scan_items = extractor._scan_sync(session)
            worker_fn = _process_claude_license_sync_worker
        elif conn.connector_type == "claude_token":
            extractor = ClaudeTokenExtractor(conn)
            scan_items = extractor._scan_sync(session)
            worker_fn = _process_claude_token_sync_worker
        else:
            extractor = GcpBigQueryExtractor(conn)
            scan_result = extractor._scan_sync(session)
            scan_items = scan_result.items
            worker_fn = _process_gcp_month_sync_worker

        # Scan (uses sync SDK — already non-blocking in thread)

        if selected_blobs:
            selected_set = set(selected_blobs)
            to_process = [item for item in scan_items if item.blob_path in selected_set]
        elif force_periods:
            force_set = set(force_periods)
            to_process = [
                item for item in scan_items 
                if (item.billing_period in force_set or any(p in (item.billing_period or "") for p in force_set))
            ]
        else:
            to_process = [item for item in scan_items if item.status in ("NEW", "CHANGED")]

        result.total_blobs = len(to_process)
        result.processed_blobs = 0
        status["total_blobs"] = len(to_process)
        status["processed_blobs"] = 0
        total_dl = sum(
            (getattr(item, "content_length", 0) or getattr(item, "row_count", 0) or 0)
            for item in to_process
        )
        status["total_download_bytes"] = total_dl
        status["downloaded_bytes_total"] = 0
        status["active_downloads"] = 0
        status["download_bytes"] = 0
        status["current_blob_size"] = 0
        result.total_download_bytes = total_dl
        result.downloaded_bytes_total = 0
        result.active_downloads = 0
        result.download_bytes = 0
        result.current_blob_size = 0
        result.download_blobs = [
            {
                "blob_path": item.blob_path,
                "size": (item.content_length or getattr(item, "row_count", 0) or 0),
                "downloaded": 0,
                "status": "QUEUED",
            }
            for item in to_process
        ]
        status["download_blobs"] = result.download_blobs
        session.commit()

        progress_lock = Lock()
        total = len(to_process)
        completed = 0

        # Para OCI, GCP BigQuery, MongoDB, GitHub, NotebookLM e Claude, usar 1 worker por vez para evitar
        # concorrência de commit OCC no Apache Iceberg e manter uso de memória mínimo.
        max_workers = 1 if conn.connector_type in ("oci", "gcp_bigquery", "mongodb_atlas", "github_license", "github_token", "notebooklm_license", "notebooklm_token", "claude_license", "claude_token") else 2



        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="blob-dl") as dl_executor:
            futures = {}
            for i, item in enumerate(to_process):
                if _check_cancelled(task_id):
                    break
                future = dl_executor.submit(
                    worker_fn,
                    task_id, item, extractor,
                )
                futures[future] = i

            for future in as_completed(futures):
                if _check_cancelled(task_id):
                    for f in futures:
                        f.cancel()
                    result.status = "CANCELLED"
                    result.finished_at = datetime.now(timezone.utc)
                    status["status"] = "CANCELLED"
                    session.commit()
                    return

                idx = futures[future]
                item = to_process[idx]
                try:
                    raw, norm, dest, errs = future.result(timeout=900)  # 15min per blob
                except Exception as blob_err:
                    error_msg = f"{item.blob_path}: {blob_err}"
                    logger.error(error_msg)
                    with progress_lock:
                        completed += 1
                        result.errors.append(error_msg)
                        status.setdefault("errors", []).append(error_msg)
                        result.processed_blobs = completed
                        status["processed_blobs"] = completed
                        result.current_blob = item.blob_path
                        status["current_blob"] = item.blob_path
                        _save_progress_sync(task_id, result)
                    continue
                else:
                    with progress_lock:
                        completed += 1
                        result.rows_raw += raw
                        result.rows_normalized += norm
                        result.rows_destination = (result.rows_destination or 0) + dest
                        result.processed_blobs = completed
                        result.current_blob = item.blob_path
                        status["processed_blobs"] = completed
                        status["rows_raw"] = result.rows_raw
                        status["rows_normalized"] = result.rows_normalized
                        status["rows_destination"] = status.get("rows_destination", 0) + dest
                        status["current_blob"] = item.blob_path
                        status["downloaded_bytes_total"] += (
                            item.content_length or getattr(item, "row_count", 0) or 0
                        )
                        for e in errs:
                            result.errors.append(e)
                            status.setdefault("errors", []).append(e)
                        # Persist progress to DB inside the lock for safety
                        s = SyncSession()
                        t = s.get(EtlTask, task_id)
                        if t:
                            t.rows_raw = result.rows_raw
                            t.rows_normalized = result.rows_normalized
                            t.rows_destination = result.rows_destination
                            t.processed_blobs = result.processed_blobs
                            t.current_blob = result.current_blob
                            t.errors = list(result.errors or [])
                            s.commit()
                        s.close()

        result.status = "COMPLETED" if not result.errors else "COMPLETED_WITH_ERRORS"
        result.finished_at = datetime.now(timezone.utc)
        result.current_blob = None
        status["status"] = result.status
        status["active_downloads"] = 0
        status["download_bytes"] = 0
        status["current_blob_size"] = 0
        _persist_download_progress(
            task_id,
            active_downloads=0,
            download_bytes=0,
            current_blob_size=0,
            current_blob=None,
            phase=("Concluído com erros: " + result.errors[0][:160]) if result.errors else "Importação concluída",
        )
        # Persist the final error list and phase. Previously these values only
        # lived in the worker process and disappeared from the API response.
        final_session = SyncSession()
        final_row = final_session.get(EtlTask, task_id)
        if final_row:
            unique_errors = list(dict.fromkeys(result.errors or []))
            final_row.errors = unique_errors
            final_row.phase = ("Concluído com erros: " + unique_errors[0][:160]) if unique_errors else (
                "Carga Bronze concluída; origem carregada sem transformação"
                if result.rows_raw and not result.rows_normalized else "Importação concluída"
            )
            final_row.status = result.status
            final_row.finished_at = result.finished_at
            final_session.commit()
        final_session.close()
        session.commit()

    except Exception as e:
        status["status"] = "FAILED"
        status.setdefault("errors", []).append(str(e))
        try:
            # Never leave a task permanently RUNNING when the worker thread
            # fails outside the per-blob future (Iceberg, DB commit, etc.).
            failed = SyncSession()
            row = failed.get(EtlTask, task_id)
            if row and row.status not in ("CANCELLED", "CANCELLING"):
                row.status = "FAILED"
                row.phase = "Falha: " + str(e)[:180]
                row.errors = list(row.errors or []) + [str(e)]
                row.finished_at = datetime.now(timezone.utc)
                failed.commit()
            failed.close()
            session.rollback()
        except Exception:
            pass
    finally:
        _cancel_flags.discard(task_id)
        session.close()

# ═══════════════════════════════════════════════════════════════════════
#  Add sync scan method to AzureExtractor dynamically
# ═══════════════════════════════════════════════════════════════════════

import re

DATE_RANGE_RE = re.compile(r"(\d{8})-(\d{8})")


def _scan_sync_extractor(self: AzureExtractor, session: Session) -> list:
    """Synchronous scan — runs in thread, uses sync DB + Azure SDK."""
    from app.schemas.schemas import BlobScanItem
    from app.models.skipped_blob import SkippedBlob

    blob_service = self._get_blob_service_client()
    container_name = self.connection.config_json["container_name"]
    container_client = blob_service.get_container_client(container_name)

    # Load skipped blob paths for this connection
    skipped_result = session.execute(
        select(SkippedBlob.blob_path).where(
            SkippedBlob.connection_id == self.connection.id,
        )
    )
    skipped_paths = {row[0] for row in skipped_result}

    items: list[BlobScanItem] = []
    for prefix in self.blob_prefixes:
        for blob in container_client.list_blobs(name_starts_with=prefix):
            blob_path = blob.name
            last_modified = blob.last_modified
            size = getattr(blob, "size", None)

            # Mark skipped blobs as SKIPPED instead of filtering
            if blob_path in skipped_paths:
                status = "SKIPPED"
                billing_period = derive_billing_period(blob_path, None)
                items.append(BlobScanItem(
                    blob_path=blob_path, blob_prefix=prefix,
                    last_modified=last_modified, content_length=size,
                    status=status, billing_period=billing_period,
                ))
                continue

            existing = session.execute(
                select(IngestedBlob).where(
                    IngestedBlob.connection_id == self.connection.id,
                    IngestedBlob.blob_path == blob_path,
                )
            ).scalar_one_or_none()

            if existing is None:
                status = "NEW"
            elif existing.last_modified and last_modified and existing.last_modified >= last_modified:
                status = "UNCHANGED"
            else:
                status = "CHANGED"

            billing_period = derive_billing_period(blob_path, None)

            items.append(BlobScanItem(
                blob_path=blob_path,
                blob_prefix=prefix,
                last_modified=last_modified,
                content_length=size,
                status=status,
                billing_period=billing_period,
            ))

    return items


# Monkey-patch the sync scan onto AzureExtractor instances
AzureExtractor._scan_sync = _scan_sync_extractor


# ═══════════════════════════════════════════════════════════════════════
#  Public API (unchanged signatures)
# ═══════════════════════════════════════════════════════════════════════

async def start_etl_task(
    connection_id: int,
    selected_blobs: list[str] | None = None,
    force_periods: list[str] | None = None,
) -> int:
    """Create an ETL task and launch it in a background thread."""
    async with async_session() as db:
        task = EtlTask(connection_id=connection_id, status="RUNNING")
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = task.id

    _inmemory_tasks[task_id] = {
        "id": task_id,
        "connection_id": connection_id,
        "status": "RUNNING",
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
        "errors": [],
    }

    # Run ETL in a thread so the asyncio event loop stays free
    loop = asyncio.get_running_loop()
    loop.run_in_executor(
        _etl_executor,
        _run_etl_sync,
        task_id,
        connection_id,
        selected_blobs,
        force_periods,
    )
    return task_id


def cancel_task(task_id: int) -> bool:
    """Cancel a running/queued ETL task. Uses DB as the shared signal between API and worker processes."""
    # 1. Set the in-process cancel flag (works when task runs in same process)
    _cancel_flags.add(task_id)

    # 2. Write CANCELLED to DB so the worker process sees it
    try:
        from app.database import SyncSession
        from app.models.etl_task import EtlTask
        db = SyncSession()
        row = db.get(EtlTask, task_id)
        if row and row.status in ("QUEUED", "RUNNING"):
            row.status = "CANCELLED"
            row.phase = "Cancelado pelo usuario"
            row.finished_at = datetime.now(timezone.utc)
            db.commit()
            cancelled = True
        else:
            cancelled = False
        db.close()
    except Exception:
        cancelled = False

    # 3. Update in-memory entry if present
    status = _inmemory_tasks.get(task_id)
    if status:
        status["status"] = "CANCELLED"

    return cancelled


def get_task_status(task_id: int) -> dict | None:
    """Return task status with DB fallback for worker-executed tasks.

    Always syncs from DB for QUEUED tasks (so the API process sees when the
    worker transitions them to RUNNING), and falls back to DB for tasks not
    found in the in-memory dict.
    """
    status = _inmemory_tasks.get(task_id)

    # Always refresh persisted fields: extraction runs in a separate worker process.
    if status:
        try:
            from app.database import SyncSession
            from app.models.etl_task import EtlTask
            db = SyncSession()
            row = db.get(EtlTask, task_id)
            db.close()
            if row:
                status["status"] = row.status
                status["total_blobs"] = row.total_blobs
                status["processed_blobs"] = row.processed_blobs
                status["rows_raw"] = row.rows_raw
                status["rows_normalized"] = row.rows_normalized
                status["rows_destination"] = row.rows_destination
                status["current_blob"] = row.current_blob
                status["total_download_bytes"] = row.total_download_bytes
                status["downloaded_bytes_total"] = row.downloaded_bytes_total
                status["current_blob_size"] = row.current_blob_size
                status["download_bytes"] = row.download_bytes
                status["active_downloads"] = row.active_downloads
                status["download_blobs"] = row.download_blobs or []
                status["errors"] = row.errors or []
        except Exception:
            pass
        return status

    if status:
        return status

    # Fallback to DB (task may be executed by the worker in a different process)
    try:
        from app.database import SyncSession
        from app.models.etl_task import EtlTask
        db = SyncSession()
        row = db.get(EtlTask, task_id)
        db.close()
        if row:
            return {
                "id": row.id,
                "connection_id": row.connection_id,
                "status": row.status,
                "task_type": row.task_type,
                "total_blobs": row.total_blobs,
                "processed_blobs": row.processed_blobs,
                "rows_raw": row.rows_raw,
                "rows_normalized": row.rows_normalized,
                "rows_destination": row.rows_destination,
                "current_blob": row.current_blob,
                "current_blob_size": 0,
                "download_bytes": 0,
                "downloaded_bytes_total": 0,
                "total_download_bytes": 0,
                "downloaded_bytes_total": row.downloaded_bytes_total,
                "total_download_bytes": row.total_download_bytes,
                "active_downloads": row.active_downloads,
                "download_blobs": row.download_blobs or [],
                "phase": row.phase or "",
                "errors": row.errors or [],
            }
    except Exception:
        pass
    return None


def get_running_tasks() -> list[dict]:
    """Return all non-terminal tasks from in-memory dict + DB (RUNNING and QUEUED).

    For tasks executed by the worker (in a different process), progress is read
    from the EtlTask DB row. For tasks still in the API process memory
    (legacy or freshly-queued), progress comes from the in-memory dict.
    """
    terminal = {"COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED"}
    tasks = []

    # Also fetch RUNNING and QUEUED tasks from DB for all task types
    # (QUEUED tasks are created by the API and picked up by the worker;
    #  RUNNING tasks show tasks the worker is actively processing;
    #  this DB fallback also catches tasks that survived an API reload.)
    try:
        from app.database import SyncSession
        from app.models.etl_task import EtlTask
        from sqlalchemy import select
        db = SyncSession()
        db_tasks = db.execute(
            select(EtlTask).where(
                EtlTask.status.in_(["RUNNING", "QUEUED"]),
            )
        ).scalars().all()
        # PostgreSQL is authoritative across the API and worker processes.
        # Retain only genuinely non-persisted legacy tasks from this process;
        # otherwise a terminal DB task can remain RUNNING forever in the API's
        # stale in-memory dictionary.
        tasks.extend(
            t for t in _inmemory_tasks.values()
            if t.get("id") is None and t.get("status", "") not in terminal
        )
        existing_ids = {t.get("id") for t in tasks}
        for dt in db_tasks:
            entry = {
                "id": dt.id,
                "type": dt.task_type,
                "status": dt.status,
                "task_type": dt.task_type,
                "connection_id": dt.connection_id,
                "destination_table_id": dt.destination_table_id,
                "connection_ids": dt.connection_ids or [],
                "total_blobs": dt.total_blobs,
                "processed_blobs": dt.processed_blobs,
                "rows_raw": dt.rows_raw,
                "rows_normalized": dt.rows_normalized,
                "rows_destination": dt.rows_destination,
                "current_blob": dt.current_blob,
                "current_blob_size": dt.current_blob_size,
                "download_bytes": dt.download_bytes,
                "downloaded_bytes_total": dt.downloaded_bytes_total,
                "total_download_bytes": dt.total_download_bytes,
                "active_downloads": dt.active_downloads,
                "download_blobs": dt.download_blobs or [],
                "phase": dt.phase,
                "table": (dt.result_json or {}).get("table", ""),
                "error": "",
            }
            if dt.id not in existing_ids:
                tasks.append(entry)
            else:
                # Override stale in-memory entry with fresh DB data
                for i, t in enumerate(tasks):
                    if t.get("id") == dt.id:
                        tasks[i] = entry
                        break
        db.close()
    except Exception:
        pass

    return tasks
