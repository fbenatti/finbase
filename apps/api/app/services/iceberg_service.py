"""
Iceberg Service — Data Lakehouse layer atop PostgreSQL SQL catalog + PyIceberg.

Layers:
  Bronze  (raw)       → bronze.raw_{connection_code}
  Silver  (mapped)    → silver.{source}_{destination_model}
  Gold    (analytics) → gold.{destination_table}

Metadata is stored in PostgreSQL and Parquet/metadata files are stored in the
shared local filesystem warehouse (`apps/data/iceberg`).
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import pyarrow as pa
from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.schema import Schema
from pyiceberg.types import (
    NestedField,
    StringType,
    FloatType,
    DoubleType,
    IntegerType,
    LongType,
    TimestampType,
)
from pyiceberg.partitioning import PartitionSpec, PartitionField
from pyiceberg.transforms import IdentityTransform

from app.services import lakehouse_layers as layers

logger = logging.getLogger(__name__)
_STAGING_TABLE_RE = re.compile(r"^(?P<base>[a-z0-9_]+)_stg_(?P<timestamp>\d{16,20})$")
_STAGING_TABLE_RE = re.compile(r"^(?P<base>[a-z0-9_]+)_stg_(?P<timestamp>\d{16,20})$")

# ---------------------------------------------------------------------------
# Catalog configuration
# ---------------------------------------------------------------------------

# SQL catalog (PostgreSQL) + local filesystem warehouse. Containers explicitly
# set ICEBERG_LOCAL_WAREHOUSE=/data/iceberg; local development keeps using the
# repository's apps/data/iceberg directory. Catalog URIs stay portable as
# file:///data/iceberg/...; FileIO remaps them onto this physical directory.
_LOCAL_DEFAULT = Path("/data/iceberg") if Path("/.dockerenv").exists() else Path(__file__).resolve().parents[3] / "data" / "iceberg"
_LOCAL_WAREHOUSE = Path(os.getenv("ICEBERG_LOCAL_WAREHOUSE", str(_LOCAL_DEFAULT))).expanduser().resolve()
_LOCAL_WAREHOUSE.mkdir(parents=True, exist_ok=True)

_catalog: Optional[Catalog] = None
_initialized_namespaces: set[str] = set()
_legacy_path_patch_installed = False


def _normalize_pyiceberg_location(location: str) -> str:
    """Map portable /data/iceberg and legacy host URIs to the physical warehouse."""
    from app.services.iceberg_catalog_migration import physical_warehouse_path

    if not location:
        return location
    physical = physical_warehouse_path(location, _LOCAL_WAREHOUSE)
    return str(physical) if physical is not None else location


def _install_legacy_warehouse_path_patch() -> None:
    """Remap warehouse paths in every PyIceberg local-file open.

    Scans call ``pyiceberg.io.pyarrow._parse_location`` then
    ``LocalFileSystem.open_input_file(path)``. Patching ``PyArrowFile`` is not
    enough because that class is not on the scan path.
    """
    global _legacy_path_patch_installed
    if _legacy_path_patch_installed:
        return

    import pyiceberg.io as iceberg_io
    import pyiceberg.io.pyarrow as iceberg_pyarrow
    from pyiceberg.io.pyarrow import PyArrowFileIO

    def _wrap_parse(original):
        def patched(location: str, *args, **kwargs):
            return original(_normalize_pyiceberg_location(location), *args, **kwargs)
        return patched

    if hasattr(iceberg_io, "_parse_location"):
        iceberg_io._parse_location = _wrap_parse(iceberg_io._parse_location)
    if hasattr(iceberg_pyarrow, "_parse_location"):
        iceberg_pyarrow._parse_location = _wrap_parse(iceberg_pyarrow._parse_location)
    PyArrowFileIO.parse_location = staticmethod(_wrap_parse(PyArrowFileIO.parse_location))
    _legacy_path_patch_installed = True


# Scan/FileIO remap must be active before the first Bronze read, including in
# spawned worker processes that never went through FastAPI lifespan.
_install_legacy_warehouse_path_patch()


def _create_sql_catalog() -> Catalog:
    """Create a SQL catalog using PostgreSQL + local filesystem warehouse."""
    from app.config import settings
    from app.services.iceberg_catalog_migration import CANONICAL_WAREHOUSE_URI

    _install_legacy_warehouse_path_patch()
    logger.info(
        "Using SQL catalog (PostgreSQL) with logical warehouse %s (physical %s)",
        CANONICAL_WAREHOUSE_URI,
        _LOCAL_WAREHOUSE,
    )
    return load_catalog(
        "sql",
        uri=settings.database_url_sync,           # PostgreSQL for catalog metadata
        warehouse=CANONICAL_WAREHOUSE_URI,        # Portable URI; FileIO remaps to disk
    )


def _get_catalog() -> Catalog:
    """Lazy-load the PostgreSQL SQL catalog and local filesystem warehouse."""
    global _catalog
    if _catalog is not None:
        return _catalog

    _catalog = _create_sql_catalog()
    return _catalog


def _ensure_namespace(namespace: str) -> None:
    """Create an Iceberg namespace if it does not already exist."""
    if namespace in _initialized_namespaces:
        return
    cat = _get_catalog()
    try:
        cat.create_namespace(namespace)
    except Exception:
        pass  # already exists or not supported (SQL catalog)
    _initialized_namespaces.add(namespace)


def ensure_namespace(namespace: str) -> None:
    """Public wrapper: create an Iceberg namespace if it does not exist."""
    _ensure_namespace(namespace)


def _table_id(namespace: str, table: str) -> tuple:
    """Build a table identifier tuple e.g. ('gold', 'dest_modelo_focus')."""
    return (namespace, table.replace("-", "_").replace(".", "_"))


def _table_short_name(table_ref) -> str:
    """Normalize catalog list_tables / load_table identifiers to bare table name."""
    if isinstance(table_ref, tuple) and len(table_ref) > 1:
        return str(table_ref[1])
    if isinstance(table_ref, str):
        return table_ref.split(".")[-1]
    return str(table_ref)


def _namespace_has_table(cat: Catalog, namespace: str, short_name: str) -> bool:
    try:
        return any(_table_short_name(t) == short_name for t in cat.list_tables(namespace))
    except Exception:
        return False


def _is_staging_location(table: Any) -> bool:
    try:
        return "_stg_" in str(table.location())
    except Exception:
        return False


def _fold_case_insensitive_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse columns that differ only by case.

    Trino Iceberg treats identifiers as case-insensitive. OCI cost CSVs can
    ship both ``tags/environment`` and ``tags/Environment``, which makes
    ``SELECT *`` fail with GENERIC_INTERNAL_ERROR / duplicate map keys.
    """
    groups: dict[str, list[Any]] = {}
    for column in df.columns:
        groups.setdefault(str(column).lower(), []).append(column)
    if all(len(names) == 1 for names in groups.values()):
        return df
    # Build all columns at once. Assigning them one by one fragments the
    # DataFrame for wide CSVs (OCI commonly has hundreds of tag columns),
    # increasing both CPU time and peak memory before Arrow conversion.
    folded_columns: dict[Any, pd.Series] = {}
    for names in groups.values():
        keep = names[0]
        series = df[keep]
        for extra in names[1:]:
            extra_series = df[extra]
            blank = series.isna() | (series.astype(str).str.strip() == "") | (series.astype(str) == "nan")
            series = series.where(~blank, extra_series)
        folded_columns[keep] = series
    return pd.DataFrame(folded_columns, index=df.index)


def _schema_has_case_collisions(schema: Any) -> bool:
    names = [str(field.name) for field in schema.fields]
    return len(names) != len({name.lower() for name in names})


def _align_columns_to_existing_schema(df: pd.DataFrame, schema: Any) -> pd.DataFrame:
    """Use the stored spelling for incoming columns matched case-insensitively.

    Iceberg stores names case-sensitively while Trino does not.  This prevents
    a later source file that spells an existing tag differently (for example,
    ``tags/Project`` after ``tags/project``) from evolving a second column.
    """
    stored_by_folded_name = {str(field.name).lower(): field.name for field in schema.fields}
    rename = {
        column: stored_by_folded_name.get(str(column).lower(), column)
        for column in df.columns
    }
    if all(source == target for source, target in rename.items()):
        return df
    aligned = df.rename(columns=rename)
    return _fold_case_insensitive_columns(aligned)


def _bronze_table_id(connection_code: str) -> tuple[str, str]:
    safe = connection_code.replace("-", "_").replace(".", "_")
    return ("bronze", f"raw_{safe}")


def list_bronze_slices(connection_code: str, billing_period: str) -> list[dict[str, Any]]:
    """Enumerate physical Bronze provenance, independently of the SQL manifest.

    The manifest can be stale after an interrupted import. Reprocessing must
    still see every blob physically present in Iceberg and use its actual row
    count as the immutable input contract.
    """
    try:
        from pyiceberg.expressions import EqualTo
        table = _get_catalog().load_table(_bronze_table_id(connection_code))
        scan = table.scan(
            row_filter=EqualTo("_billing_period", str(billing_period)),
            selected_fields=("_blob_path", "_billing_period"),
        )
        frame = scan.to_pandas()
        if frame.empty:
            return []
        return [
            {"blob_path": str(path), "billing_period": str(billing_period), "row_count": int(n)}
            for path, n in frame.groupby("_blob_path", dropna=False).size().items()
            if path is not None and str(path) not in {"", "nan"}
        ]
    except Exception:
        logger.exception("Failed to enumerate physical Bronze slices for %s/%s", connection_code, billing_period)
        return []


def _df_to_arrow(
    df: pd.DataFrame,
    column_types: Optional[dict[str, str]] = None,
    target_schema: Optional[Any] = None,
) -> pa.Table:
    """
    Convert a pandas DataFrame to a PyArrow Table, safely coercing columns to
    expected types.

    Args:
        df: Input pandas DataFrame.
        column_types: Optional dict mapping column_name → data_type
                      (e.g. {'cost_usd': 'DOUBLE', 'resource_name': 'TEXT'}).
                      When provided, columns are coerced to match before conversion.
                      When omitted, all object columns are forced to string to avoid
                      PyArrow inference failures on mixed-type columns.
        target_schema: Optional Iceberg schema to map PARQUET:field_id by column name.

    Returns:
        PyArrow Table with all columns typed consistently and mapped field IDs.
    """
    import pyarrow as pa_lib
    import numpy as np

    df = df.copy()

    # ── Phase 1: Coerce columns to expected types ──
    # Map SQL/DB data_type names to canonical categories
    _STRING_TYPES = {"text", "varchar", "string", "character varying", "str", "char", "timestamptz", "timestamp", "jsonb", "json"}
    _INT_TYPES = {"integer", "int", "int4", "int8", "bigint", "smallint", "long", "int64", "int32"}
    _FLOAT_TYPES = {"double", "float", "double precision", "real", "numeric", "decimal", "float8", "float64", "float32"}
    _BOOL_TYPES = {"boolean", "bool"}

    for col in df.columns:
        target = (column_types.get(col, "") if column_types else "").lower().strip()
        current_dtype = df[col].dtype

        if target in _STRING_TYPES:
            # Force to string, replacing NaN/None sentinels with None
            df[col] = df[col].astype(str).replace(
                {"nan": None, "None": None, "NaT": None, "<NA>": None, "": None}
            )
            # Replace pd.NA and numpy NaN that survived the string conversion
            mask = df[col].isin(["nan", "None", "NaT", "<NA>"])
            df.loc[mask, col] = None

        elif target in _INT_TYPES:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

        elif target in _FLOAT_TYPES:
            # A batch containing only whole values is inferred by Arrow as
            # int64.  Gold uses a stable DOUBLE contract, so force float64
            # even when the values happen to be integers in this one blob.
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")

        elif target in _BOOL_TYPES:
            def _as_bool(value):
                if value is None or (isinstance(value, float) and value != value):
                    return pd.NA
                if isinstance(value, bool):
                    return value
                text = str(value).strip().lower()
                if text in {"true", "1", "yes", "sim"}:
                    return True
                if text in {"false", "0", "no", "não", "nao", "nan", "none", "<na>", ""}:
                    return False if text in {"false", "0", "no", "não", "nao"} else pd.NA
                return pd.NA

            df[col] = df[col].map(_as_bool).astype("boolean")

        elif not target:
            # No type hint — force object columns to string (safe default)
            if current_dtype == object or str(current_dtype) == "object":
                df[col] = df[col].astype(str).replace(
                    {"nan": None, "None": None, "NaT": None, "<NA>": None, "": None}
                )
                mask = df[col].isin(["nan", "None", "NaT", "<NA>"])
                df.loc[mask, col] = None

    # ── Phase 2: Replace NaN/NaT with None ──
    df = df.where(pd.notna(df), None)

    # ── Phase 2b: Re-aplicar coerção de tipo após .where() ──
    # O .where(..., None) acima converte silenciosamente Int64 (nullable bigint)
    # para float64/object. Isso causa "Cannot change column type: long -> double"
    # no Iceberg. Re-aplicamos a coerção para INT e BOOL explicitamente.
    if column_types:
        for col in df.columns:
            target = column_types.get(col, "").lower().strip()
            if target in _INT_TYPES:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
            elif target in _BOOL_TYPES:
                df[col] = df[col].astype("boolean")

    # ── Phase 3: Convert to PyArrow ──
    arrow_table = pa_lib.Table.from_pandas(df, preserve_index=False)

    # All-null columns infer as Arrow null. Keep the declared Gold contract
    # (boolean stays boolean); only fall back to string when no type was given.
    def _declared_arrow_type(name: str):
        target = (column_types.get(name, "") if column_types else "").lower().strip()
        if target in _BOOL_TYPES:
            return pa_lib.bool_()
        if target in _INT_TYPES:
            return pa_lib.int64()
        if target in _FLOAT_TYPES:
            return pa_lib.float64()
        return pa_lib.string()

    schema_changed = False
    new_fields = []
    for i in range(len(arrow_table.schema)):
        field = arrow_table.schema.field(i)
        if pa_lib.types.is_null(field.type):
            new_fields.append(pa_lib.field(field.name, _declared_arrow_type(field.name), nullable=True))
            schema_changed = True
        else:
            new_fields.append(field)

    if schema_changed:
        new_schema = pa_lib.schema(new_fields)
        cols = [arrow_table.column(j).cast(new_schema.field(j).type) for j in range(len(new_fields))]
        arrow_table = pa_lib.table(dict(zip([f.name for f in new_fields], cols)))

    # Existing Iceberg tables are the authoritative type contract.  Pandas
    # inference is intentionally ignored for columns already present in that
    # contract: nullable integers, booleans, dates and all-null batches must
    # retain the target type on every write path (reprocess, reclassification,
    # forecast, shared cost, and manual loads).
    if target_schema is not None:
        target_arrow_schema = target_schema.as_arrow()
        target_fields = {field.name: field for field in target_arrow_schema}
        source_columns = {
            field.name: arrow_table.column(idx)
            for idx, field in enumerate(arrow_table.schema)
        }
        aligned_columns = []
        aligned_fields = []
        # Iceberg field IDs are positional when ``pyarrow_to_schema`` builds a
        # new staging table.  Merely casting by name is therefore insufficient:
        # a DataFrame containing the same columns in another order creates a
        # staging schema whose IDs point to different names and the very first
        # append fails with ``Mismatch in fields``.  Rebuild the Arrow table in
        # the canonical Iceberg order and materialize absent optional columns
        # as typed NULLs.  Truly new columns remain after the canonical
        # contract so callers that explicitly evolve a schema can still see
        # them instead of losing data silently.
        for target_field in target_arrow_schema:
            column = source_columns.get(target_field.name)
            if column is None:
                if not target_field.nullable:
                    raise ValueError(
                        f"Coluna obrigatória {target_field.name!r} ausente para o schema Iceberg"
                    )
                column = pa_lib.nulls(len(arrow_table), type=target_field.type)
            elif column.type != target_field.type:
                try:
                    column = column.cast(target_field.type, safe=True)
                except (pa_lib.ArrowInvalid, pa_lib.ArrowNotImplementedError, pa_lib.ArrowTypeError) as exc:
                    raise ValueError(
                        f"Coluna {target_field.name!r} incompatível com o schema Iceberg: "
                        f"{column.type} -> {target_field.type}: {exc}"
                    ) from exc
            aligned_columns.append(column)
            aligned_fields.append(
                pa_lib.field(
                    target_field.name,
                    target_field.type,
                    nullable=target_field.nullable,
                    metadata=target_field.metadata,
                )
            )

        for source_field in arrow_table.schema:
            if source_field.name in target_fields:
                continue
            aligned_columns.append(source_columns[source_field.name])
            aligned_fields.append(source_field)
        arrow_table = pa_lib.Table.from_arrays(aligned_columns, schema=pa_lib.schema(aligned_fields))

    # Ensure every field has a deterministic PARQUET:field_id metadata mapped by name
    final_fields = []
    for idx in range(len(arrow_table.schema)):
        f = arrow_table.schema.field(idx)
        fid = None
        if target_schema is not None:
            try:
                iceberg_field = target_schema.find_field(f.name)
                if iceberg_field:
                    fid = str(iceberg_field.field_id)
            except Exception:
                pass
        if not fid:
            fid = str(idx + 1)
        meta = dict(f.metadata or {})
        meta[b"PARQUET:field_id"] = fid.encode()
        final_fields.append(pa_lib.field(f.name, f.type, nullable=f.nullable, metadata=meta))
    new_schema = pa_lib.schema(final_fields)
    arrow_table = arrow_table.cast(new_schema)

    return arrow_table


# ---------------------------------------------------------------------------
# Bronze layer — raw ingested data, one table per connection code
# ---------------------------------------------------------------------------
def write_bronze(
    connection_code: str,
    df: pd.DataFrame,
    billing_period: str,
    blob_path: str = "",
) -> int:
    """
    Write a raw DataFrame to the Bronze layer.

    Table:  bronze.raw_{connection_code}
    Partitioned by: billing_period (string)
    Returns: number of rows appended.
    """
    if df.empty:
        return 0

    # Ingestion is idempotent per source blob.  Re-running the same extraction
    # must replace its previous snapshot instead of appending a second copy.
    # Callers that stream a blob in chunks use a distinct merged frame before
    # invoking this function; the delete therefore happens exactly once per
    # logical blob.
    if blob_path:
        delete_bronze_slice(connection_code, blob_path=blob_path, billing_period=billing_period)

    table_id = _bronze_table_id(connection_code)
    table_name = f"{table_id[0]}.{table_id[1]}"
    _ensure_namespace("bronze")

    df = _fold_case_insensitive_columns(df.copy())
    df["_billing_period"] = billing_period
    df["_blob_path"] = blob_path
    df["_ingested_at"] = pd.Timestamp.utcnow().isoformat()

    # Bronze is the immutable source snapshot. Source CSVs can infer the same
    # column as numeric in one blob and text in another (often because of
    # blanks/identifiers), so store source values as nullable text to keep one
    # stable raw schema without changing their textual content.
    bronze_types = {
        col: "TEXT" for col in df.columns
        if col not in {"_billing_period", "_blob_path", "_ingested_at"}
    }
    arrow_table = _df_to_arrow(df, column_types=bronze_types)
    cat = _get_catalog()
    rows = len(df)

    from pyiceberg.io.pyarrow import pyarrow_to_schema
    iceberg_schema = pyarrow_to_schema(arrow_table.schema)
    billing_field_id = iceberg_schema.highest_field_id + 1
    partition_spec = PartitionSpec(
        PartitionField(
            source_id=iceberg_schema.find_field("_billing_period").field_id,
            field_id=billing_field_id,
            transform=IdentityTransform(),
            name="_billing_period",
        )
    )

    tbl = None
    try:
        tbl = cat.load_table(table_id)
    except Exception:
        pass

    if tbl is not None and _schema_has_case_collisions(tbl.schema()):
        logger.warning("Dropping %s: Iceberg schema has case-colliding columns that Trino cannot read", table_name)
        cat.drop_table(table_id)
        tbl = None

    if tbl is not None:
        try:
            df = _align_columns_to_existing_schema(df, tbl.schema())
            bronze_types = {
                col: "TEXT" for col in df.columns
                if col not in {"_billing_period", "_blob_path", "_ingested_at"}
            }
            arrow_table = _df_to_arrow(df, column_types=bronze_types)
            iceberg_schema = pyarrow_to_schema(arrow_table.schema)
            # Explicitly evolve before conversion. PyArrow/Iceberg may silently
            # project away newly introduced provenance and financial columns
            # when the existing table schema is a strict subset of the frame.
            existing_names = {field.name for field in tbl.schema().fields}
            incoming_names = {field.name for field in iceberg_schema.fields}
            if not incoming_names.issubset(existing_names):
                with tbl.update_schema() as schema_update:
                    schema_update.union_by_name(iceberg_schema)
                tbl = cat.load_table(table_id)
            arrow_table = _df_to_arrow(df, column_types=bronze_types, target_schema=tbl.schema())
            tbl.append(arrow_table)
            logger.info("Appended %d rows to %s", rows, table_name)
            return rows
        except Exception as exc:
            # Source exports can add columns over time. Evolve the existing
            # table instead of attempting create_table and hitting "already exists".
            try:
                with tbl.update_schema() as schema_update:
                    schema_update.union_by_name(iceberg_schema)
                tbl = cat.load_table(table_id)
                arrow_table = _df_to_arrow(df, column_types=bronze_types, target_schema=tbl.schema())
                tbl.append(arrow_table)
                logger.info("Appended %d rows to %s after schema evolution", rows, table_name)
                return rows
            except Exception as append_exc:
                logger.error("Bronze append failed for %s: %s", table_name, append_exc)
                raise append_exc from exc

    try:
        tbl = cat.create_table(table_id, schema=iceberg_schema, partition_spec=partition_spec)
    except Exception as create_exc:
        # Parallel workers can race on the first month for a connection.
        if _namespace_has_table(cat, table_id[0], table_id[1]) or "already exists" in str(create_exc).lower():
            tbl = cat.load_table(table_id)
            try:
                tbl.append(arrow_table)
            except Exception as append_exc:
                with tbl.update_schema() as schema_update:
                    schema_update.union_by_name(iceberg_schema)
                tbl = cat.load_table(table_id)
                tbl.append(arrow_table)
            logger.info("Appended %d rows to existing %s after create race", rows, table_name)
            return rows
        raise

    tbl.append(arrow_table)
    logger.info("Created %s with %d rows (partitioned by billing_period)", table_name, rows)
    return rows


def reset_bronze(connection_code: str) -> bool:
    """Drop the raw Bronze table for an explicit source reimport.

    This is intentionally separate from normal ingestion: append remains the
    safe default, while a user-requested source rebuild gets a clean snapshot.
    Also removes leftover warehouse files so Trino cannot keep serving a
    cached/broken schema after the catalog row is gone.
    """
    import shutil

    table_id = _bronze_table_id(connection_code)
    table_name = f"{table_id[0]}.{table_id[1]}"
    cat = _get_catalog()
    dropped = False
    try:
        cat.drop_table(table_id)
        logger.warning("Reset Bronze table %s", table_name)
        dropped = True
    except Exception as exc:
        logger.info("Bronze table %s was not in the catalog: %s", table_name, exc)
    short = table_id[1]
    for namespace_pattern in ("bronze", "bronze.db"):
        candidate = _LOCAL_WAREHOUSE / namespace_pattern / short
        if candidate.is_dir():
            shutil.rmtree(candidate, ignore_errors=True)
            logger.warning("Deleted leftover Bronze warehouse dir %s", candidate)
            dropped = True
    return dropped


def _combine_filters(*parts: Any) -> Any:
    from pyiceberg.expressions import And

    exprs = [part for part in parts if part is not None]
    if not exprs:
        raise ValueError("Iceberg row filter requires at least one predicate")
    row_filter = exprs[0]
    for part in exprs[1:]:
        row_filter = And(row_filter, part)
    return row_filter


def _bronze_slice_filter(*, blob_path: str | None = None, billing_period: str | None = None):
    from pyiceberg.expressions import EqualTo

    return _combine_filters(
        EqualTo("_blob_path", blob_path) if blob_path else None,
        EqualTo("_billing_period", str(billing_period)) if billing_period else None,
    )


def delete_bronze_slice(
    connection_code: str,
    *,
    blob_path: str | None = None,
    billing_period: str | None = None,
) -> None:
    """Delete prior Bronze rows for one blob/month so re-ingest stays idempotent."""
    if not blob_path and not billing_period:
        return
    table_id = _bronze_table_id(connection_code)

    try:
        table = _get_catalog().load_table(table_id)
    except Exception:
        return

    row_filter = _bronze_slice_filter(blob_path=blob_path, billing_period=billing_period)
    table.delete(row_filter)
    logger.info(
        "Deleted Bronze slice from %s.%s (blob_path=%s, billing_period=%s)",
        table_id[0], table_id[1], blob_path, billing_period,
    )


def replace_bronze_slice(
    connection_code: str,
    df: pd.DataFrame,
    billing_period: str,
    blob_path: str,
) -> int:
    """Atomically replace one raw source file/month in Bronze.

    Downloaders must never delete a prior valid source snapshot and append the
    replacement in separate commits: an append failure between those steps
    turns a transient connector problem into permanent data loss.  Existing
    Bronze tables use one Iceberg transaction for the exact provenance slice.
    """
    if df.empty:
        return 0
    table_id = _bronze_table_id(connection_code)
    try:
        table = _get_catalog().load_table(table_id)
    except Exception:
        # First import has no earlier slice to preserve.
        return write_bronze(connection_code, df, billing_period, blob_path)

    # This table must be repaired once without dropping its data.  Renaming
    # the conflicting legacy fields preserves their values and makes the
    # table readable by Trino again.
    if _schema_has_case_collisions(table.schema()):
        raise RuntimeError(
            f"Bronze schema {table_id[0]}.{table_id[1]} has case-colliding columns; "
            "repair the schema before replacing a slice"
        )

    frame = _fold_case_insensitive_columns(df.copy())
    frame = _align_columns_to_existing_schema(frame, table.schema())
    frame["_billing_period"] = str(billing_period)
    frame["_blob_path"] = blob_path
    frame["_ingested_at"] = pd.Timestamp.utcnow().isoformat()
    bronze_types = {
        col: "TEXT" for col in frame.columns
        if col not in {"_billing_period", "_blob_path", "_ingested_at"}
    }
    arrow_table = _df_to_arrow(frame, column_types=bronze_types)
    from pyiceberg.io.pyarrow import pyarrow_to_schema
    incoming_schema = pyarrow_to_schema(arrow_table.schema)
    existing_names = {field.name for field in table.schema().fields}
    incoming_names = {field.name for field in incoming_schema.fields}
    if not incoming_names.issubset(existing_names):
        with table.update_schema() as schema_update:
            schema_update.union_by_name(incoming_schema)
        table = _get_catalog().load_table(table_id)
    slice_filter = _bronze_slice_filter(blob_path=blob_path, billing_period=str(billing_period))
    from app.services.write_lock import exclusive_write_lock

    with exclusive_write_lock():
        last_exc: Exception | None = None
        for attempt in range(5):
            table = _get_catalog().load_table(table_id)
            arrow_table = _df_to_arrow(frame, column_types=bronze_types, target_schema=table.schema())
            try:
                tx = table.transaction()
                tx.delete(slice_filter)
                tx.append(arrow_table)
                tx.commit_transaction()
                logger.info(
                    "Replaced Bronze slice %s/%s (%d rows)",
                    connection_code,
                    billing_period,
                    len(frame),
                )
                return len(frame)
            except Exception as exc:
                last_exc = exc
                if attempt < 4 and "branch main has changed" in str(exc):
                    logger.warning(
                        "Bronze slice commit conflict %s/%s attempt %s/5, retrying",
                        connection_code,
                        billing_period,
                        attempt + 1,
                    )
                    continue
                raise
        if last_exc:
            raise last_exc
    return len(frame)


def _is_silver_destination_table(physical_name: str, namespace: str = "silver") -> bool:
    if namespace != layers.detail_layer() and namespace != "silver":
        return False
    safe_name = physical_name.replace("-", "_").replace(".", "_")
    return safe_name.startswith("dest_")


def delete_destination_slice(
    physical_name: str,
    *,
    billing_period: str | None = None,
    snapshot_date: str | None = None,
    connection_id: int,
    namespace: str = "silver",
    partition_spec_json: dict | None = None,
) -> None:
    """Remove one replace-slice from a Silver destination table."""
    from app.services.destination_slice import (
        build_slice_row_filter,
        resolve_destination_slice_config_for_fields,
    )

    safe_name = physical_name.replace("-", "_").replace(".", "_")
    tid = _table_id(namespace, safe_name)
    try:
        table = _get_catalog().load_table(tid)
    except Exception:
        return
    config = resolve_destination_slice_config_for_fields(
        physical_name,
        partition_spec_json,
        {field.name for field in table.schema().fields},
    )
    row_filter = build_slice_row_filter(
        config,
        connection_id=connection_id,
        billing_period=billing_period,
        snapshot_date=snapshot_date,
    )
    table.delete(row_filter)
    logger.info(
        "Deleted Silver slice %s.%s grain=%s connection_id=%s billing_period=%s snapshot_date=%s",
        namespace,
        safe_name,
        config.grain,
        connection_id,
        billing_period,
        snapshot_date,
    )


def _bronze_slice_filters(blob_path: str | None, billing_period: str | None) -> list[Any]:
    """Return the immutable, exact Bronze provenance filter.

    Falling back from ``blob_path + billing_period`` to blob path alone lets a
    missing month silently consume another month's data. A historical table
    must be repaired at ingestion, never guessed during reprocessing.
    """
    if not blob_path and not billing_period:
        return [None]
    return [_bronze_slice_filter(blob_path=blob_path, billing_period=billing_period)]


def read_bronze(connection_code: str, blob_path: str | None = None,
                billing_period: str | None = None,
                max_rows: int | None = None) -> pd.DataFrame:
    """Read a filtered immutable Bronze snapshot for reprocessing.

    Filtering must happen in Iceberg, before conversion to pandas.  Reading
    the whole Bronze table and filtering afterwards makes each blob batch
    deserialize millions of unrelated rows and appears to freeze the worker.
    """
    table_id = _bronze_table_id(connection_code)
    table = _get_catalog().load_table(table_id)
    filters = _bronze_slice_filters(blob_path, billing_period)
    for idx, row_filter in enumerate(filters):
        scan_kwargs: dict[str, Any] = {}
        if row_filter is not None:
            scan_kwargs["row_filter"] = row_filter
        if max_rows is not None and max_rows >= 0:
            scan_kwargs["limit"] = max_rows
        df = table.scan(**scan_kwargs).to_pandas().reset_index(drop=True)
        if not df.empty or idx == len(filters) - 1:
            return df
    return pd.DataFrame()


def read_bronze_batches(connection_code: str, blob_path: str | None = None,
                        billing_period: str | None = None,
                        max_rows: int | None = None,
                        batch_size: int = 25_000):
    """Yield Bronze batches without materializing the complete blob in RAM."""
    table_id = _bronze_table_id(connection_code)
    table = _get_catalog().load_table(table_id)
    for row_filter in _bronze_slice_filters(blob_path, billing_period):
        scan_kwargs: dict[str, Any] = {}
        if row_filter is not None:
            scan_kwargs["row_filter"] = row_filter
        if max_rows is not None and max_rows >= 0:
            scan_kwargs["limit"] = max_rows
        reader = table.scan(**scan_kwargs).to_arrow_batch_reader()
        emitted = 0
        found = False
        for arrow_batch in reader:
            offset = 0
            while offset < arrow_batch.num_rows:
                if max_rows is not None and max_rows >= 0:
                    remaining = max_rows - emitted
                    if remaining <= 0:
                        return
                    take = min(batch_size, remaining)
                else:
                    take = batch_size
                piece = arrow_batch.slice(offset, min(take, arrow_batch.num_rows - offset))
                if piece.num_rows:
                    found = True
                    yield piece.to_pandas().reset_index(drop=True)
                    emitted += piece.num_rows
                offset += piece.num_rows
        if found:
            return


def audit_bronze_blob(
    connection_code: str,
    *,
    blob_path: str,
    billing_period: str,
    expected_rows: int | None,
    batch_size: int = 50_000,
    allow_consolidated_row_count: bool = False,
    allow_legitimate_empty: bool = False,
) -> dict[str, Any]:
    """Fully read and reconcile one immutable Bronze blob before Gold work.

    This deliberately streams every batch: manifests alone cannot prove that a
    data file belongs exclusively to one ``_blob_path`` value.
    """
    result: dict[str, Any] = {
        "connection_code": connection_code,
        "blob_path": blob_path,
        "billing_period": str(billing_period),
        "expected_rows": expected_rows,
        "actual_rows": 0,
        "status": "FAILED",
        "error": "",
    }
    if expected_rows is None:
        result["error"] = "row_count da ingestão é desconhecido"
        return result
    try:
        for frame in read_bronze_batches(
            connection_code,
            blob_path=blob_path,
            billing_period=billing_period,
            batch_size=batch_size,
        ):
            if "_blob_path" not in frame or "_billing_period" not in frame:
                result["error"] = "Bronze sem colunas de proveniência"
                return result
            if not (frame["_blob_path"].astype(str) == str(blob_path)).all():
                result["error"] = "Bronze retornou path diferente do manifesto"
                return result
            if not (frame["_billing_period"].astype(str) == str(billing_period)).all():
                result["error"] = "Bronze retornou período diferente do manifesto"
                return result
            result["actual_rows"] += len(frame)
    except Exception as exc:
        result["error"] = f"Bronze ilegível: {exc}"
        return result
    if result["actual_rows"] == 0:
        # A manifest with zero expected rows represents an empty source
        # snapshot. It must not block a full reconstruction merely because no
        # physical parquet rows were written for that snapshot.
        if int(expected_rows or 0) == 0:
            result["status"] = "PASSED"
            result["source_empty"] = True
            result["message"] = "Fonte consultada com sucesso; 0 registros"
            return result
        result["error"] = "nenhuma linha física encontrada para o arquivo"
        return result
    if allow_consolidated_row_count:
        # OCI stores a consolidated daily dataset. Its source inventory can
        # legitimately report the pre-aggregation row count, so equality of
        # row numbers is not an integrity invariant for that connector.
        result["status"] = "PASSED"
        result["row_count_reconciled_by"] = "consolidated_source"
        return result
    if result["actual_rows"] != int(expected_rows):
        result["error"] = (
            f"contagem divergente: esperado {expected_rows}, encontrado {result['actual_rows']}"
        )
        return result
    result["status"] = "PASSED"
    return result


# ---------------------------------------------------------------------------
# Silver layer — processed / normalized data
# ---------------------------------------------------------------------------
def write_silver(
    connection_code: str,
    df: pd.DataFrame,
    billing_period: str,
    column_types: Optional[dict[str, str]] = None,
    table_name_override: Optional[str] = None,
) -> int:
    """
    Write a processed DataFrame to the Silver layer.

    The legacy connection-only Silver name is intentionally disallowed.
    Callers must provide an explicit source/destination table name.
    Partitioned by: billing_period
    Returns: number of rows appended.
    """
    if df.empty:
        return 0

    if not table_name_override:
        raise ValueError(
            "Legacy connection-only Silver tables are disabled; "
            "provide an explicit source/destination table name"
        )
    table_name = table_name_override
    _ensure_namespace("silver")

    df = df.copy()
    df["_billing_period"] = billing_period
    df["_processed_at"] = pd.Timestamp.utcnow().isoformat()

    cat = _get_catalog()
    target_schema = None
    try:
        target_schema = cat.load_table(table_name).schema()
    except Exception:
        # First publication has no canonical Iceberg schema yet; declared
        # destination types remain the authority for table creation.
        pass
    arrow_table = _df_to_arrow(
        df,
        column_types=column_types,
        target_schema=target_schema,
    )
    rows = len(df)

    try:
        tbl = cat.load_table(table_name)
        tbl.append(arrow_table)
        logger.info("Appended %d rows to %s", rows, table_name)
        return rows
    except Exception as append_exc:
        # Se a tabela existe, o problema é o append (tipos/partição), não a
        # ausência dela. Tentar criar de novo só produziria um
        # TableAlreadyExistsError que esconde a causa real.
        from pyiceberg.exceptions import TableAlreadyExistsError as _Exists

        try:
            cat.load_table(table_name)
            existe = True
        except Exception:  # noqa: BLE001
            existe = False
        if existe:
            logger.error(
                "Falha ao anexar em %s (tabela existe): %s: %s",
                table_name, type(append_exc).__name__, append_exc,
            )
            raise

    from pyiceberg.io.pyarrow import pyarrow_to_schema
    iceberg_schema = pyarrow_to_schema(arrow_table.schema)
    billing_field_id = iceberg_schema.highest_field_id + 1
    partition_spec = PartitionSpec(
        PartitionField(
            source_id=iceberg_schema.find_field("_billing_period").field_id,
            field_id=billing_field_id,
            transform=IdentityTransform(),
            name="_billing_period",
        )
    )

    tbl = cat.create_table(
        table_name,
        schema=iceberg_schema,
        partition_spec=partition_spec,
    )
    tbl.append(arrow_table)
    logger.info("Created %s with %d rows", table_name, rows)
    return rows


def write_silver_destination(
    source_code: str,
    destination_physical_name: str,
    df: pd.DataFrame,
    billing_period: str,
    column_types: Optional[dict[str, str]] = None,
) -> int:
    """Write mapped, source-specific Silver data for a destination model."""
    if _is_silver_destination_table(destination_physical_name):
        raise RuntimeError(
            "write_silver_destination não grava mais silver.dest_*; "
            "use publish_silver_dataframe_slice no destino canônico."
        )
    safe_source = source_code.replace("-", "_").replace(".", "_")
    safe_dest = destination_physical_name.replace("-", "_").replace(".", "_")
    return write_silver(
        f"{safe_source}_{safe_dest}", df, billing_period,
        column_types=column_types,
        table_name_override=f"silver.{safe_source}_{safe_dest}",
    )


# ---------------------------------------------------------------------------
# Gold layer — fact_cost (analytics-ready star schema)
# ---------------------------------------------------------------------------
FACT_SCHEMA = Schema(
    NestedField(1, "connection_code", StringType(), required=False),
    NestedField(2, "connection_name", StringType(), required=False),
    NestedField(3, "billing_period", StringType(), required=False),
    NestedField(4, "cost_type", StringType(), required=False),
    NestedField(5, "subscription_id", StringType(), required=False),
    NestedField(6, "subscription_name", StringType(), required=False),
    NestedField(7, "resource_group", StringType(), required=False),
    NestedField(8, "service_name", StringType(), required=False),
    NestedField(9, "service_category", StringType(), required=False),
    NestedField(10, "meter_category", StringType(), required=False),
    NestedField(11, "meter_name", StringType(), required=False),
    NestedField(12, "meter_subcategory", StringType(), required=False),
    NestedField(13, "resource_type", StringType(), required=False),
    NestedField(14, "resource_name", StringType(), required=False),
    NestedField(15, "resource_id", StringType(), required=False),
    NestedField(16, "region", StringType(), required=False),
    NestedField(17, "currency", StringType(), required=False),
    NestedField(18, "unit_of_measure", StringType(), required=False),
    NestedField(19, "quantity", DoubleType(), required=False),
    NestedField(20, "effective_price", DoubleType(), required=False),
    NestedField(21, "cost_usd", DoubleType(), required=False),
    NestedField(22, "cost_brl", DoubleType(), required=False),
    NestedField(23, "tax_base", DoubleType(), required=False),
    NestedField(24, "tax_marketplace", DoubleType(), required=False),
    NestedField(25, "final_cost_brl", DoubleType(), required=False),
    NestedField(27, "modelo_ai", StringType(), required=False),
    NestedField(28, "cloud_name", StringType(), required=False),
    NestedField(29, "centro_custo", StringType(), required=False),
    NestedField(30, "contrato", StringType(), required=False),
    NestedField(31, "pricing_model", StringType(), required=False),
    NestedField(32, "frequency", StringType(), required=False),
    NestedField(33, "charge_type", StringType(), required=False),
    NestedField(34, "data_consumo", StringType(), required=False),
    NestedField(36, "responsavel", StringType(), required=False),
    NestedField(37, "origem", StringType(), required=False),
    NestedField(38, "id_orcamento", StringType(), required=False),
)


def write_gold(df: pd.DataFrame) -> int:
    """Write a fact DataFrame to the Gold layer. (legacy — kept for backward compat)"""
    return write_destination("fact_cost", df, partition_col="billing_period")


# ---------------------------------------------------------------------------
# Destination tables — dynamic Iceberg tables in the gold namespace
# ---------------------------------------------------------------------------
def read_destination(physical_name: str, namespace: str = "silver") -> pd.DataFrame:
    """Read all data from an existing Iceberg destination table.

    Loads the gold.{physical_name} table and returns its contents as a pandas
    DataFrame. Returns an empty DataFrame if the table does not exist.
    """
    safe_name = physical_name.replace("-", "_").replace(".", "_")
    tid = _table_id(namespace, safe_name)
    cat = _get_catalog()

    # Check if table exists first — avoids blowing up the caller
    try:
        existing = cat.list_tables(tid[0])
        existing_names = [t[1] if isinstance(t, tuple) and len(t) > 1 else t for t in existing]
        if safe_name not in existing_names:
            logger.info(f"Table gold.{safe_name} does not exist — returning empty DataFrame")
            return pd.DataFrame()
    except Exception:
        logger.info(f"Unable to list tables in namespace {tid[0]} — returning empty DataFrame")
        return pd.DataFrame()

    # Table exists — load and return its contents
    try:
        table = cat.load_table(tid)
        return table.scan().to_pandas()
    except Exception as e:
        logger.warning(f"Failed to read gold.{safe_name}: {e} — returning empty DataFrame")
        return pd.DataFrame()


def read_destination_batches(physical_name: str, excluded_periods: set[str] | None = None,
                             excluded_connection_ids: set[int] | None = None,
                             batch_size: int = 25_000, namespace: str = "silver"):
    """Yield destination rows in bounded batches, optionally pruning partitions."""
    safe_name = physical_name.replace("-", "_").replace(".", "_")
    tid = _table_id(namespace, safe_name)
    cat = _get_catalog()
    try:
        table = cat.load_table(tid)
    except Exception:
        return
    from pyiceberg.expressions import AlwaysTrue, And, Not, In
    row_filter = AlwaysTrue()
    # Preserve every row except the exact intersection selected for
    # reprocessing: NOT(period IN selected AND connection IN selected).
    # Using NOT(period) AND NOT(connection) incorrectly deletes all other
    # periods belonging to a selected connection.
    if excluded_periods and excluded_connection_ids:
        selected_rows = And(
            In("_billing_period", tuple(str(x) for x in excluded_periods)),
            In("source_connection_id", tuple(excluded_connection_ids)),
        )
        row_filter = And(row_filter, Not(selected_rows))
    for arrow_batch in table.scan(row_filter=row_filter).to_arrow_batch_reader():
        if arrow_batch.num_rows:
            batch_df = arrow_batch.to_pandas().reset_index(drop=True)
            # Parquet escritos anteriormente armazenaram clone_rule_id/clone_rule_version
            # como float64 (double). Ao reescrever na staged table (schema=long), o Iceberg
            # rejeita com "Cannot change column type: long -> double".
            # Forçamos Int64 (nullable bigint) aqui para evitar esse conflito.
            _FORCE_INT64 = {
                "clone_rule_id",
                "clone_rule_version",
                "source_connection_id",
                "financial_contract_id",
            }
            for _col in _FORCE_INT64:
                if _col in batch_df.columns:
                    batch_df[_col] = pd.to_numeric(batch_df[_col], errors="coerce").astype("Int64")
            yield batch_df


def read_destination_slice_batches(
    physical_name: str,
    *,
    billing_period: str | None = None,
    snapshot_date: str | None = None,
    connection_id: int,
    batch_size: int = 50_000,
    namespace: str = "silver",
    partition_spec_json: dict | None = None,
):
    """Yield one exact Silver replace-slice in bounded pandas batches."""
    from app.services.destination_slice import (
        build_slice_row_filter,
        resolve_destination_slice_config_for_fields,
    )

    safe_name = physical_name.replace("-", "_").replace(".", "_")
    try:
        table = _get_catalog().load_table(_table_id(namespace, safe_name))
    except Exception:
        return
    config = resolve_destination_slice_config_for_fields(
        physical_name,
        partition_spec_json,
        {field.name for field in table.schema().fields},
    )
    row_filter = build_slice_row_filter(
        config,
        connection_id=connection_id,
        billing_period=billing_period,
        snapshot_date=snapshot_date,
    )
    for arrow_batch in table.scan(row_filter=row_filter).to_arrow_batch_reader():
        if arrow_batch.num_rows:
            # PyArrow chooses its own record-batch size, which can be much
            # larger than what the classification engine can process without
            # monopolising a local workstation. Slice before converting to
            # pandas so every rule evaluation and staged write is bounded.
            for offset in range(0, arrow_batch.num_rows, batch_size):
                batch_df = arrow_batch.slice(offset, batch_size).to_pandas().reset_index(drop=True)
                # Legacy Gold files may expose nullable BIGINT columns as
                # float64 through pandas. The staged Iceberg schema is long;
                # normalise before append to avoid long -> double failures.
                for column in (
                    "clone_rule_id",
                    "clone_rule_version",
                    "source_connection_id",
                    "financial_contract_id",
                ):
                    if column in batch_df.columns:
                        batch_df[column] = pd.to_numeric(batch_df[column], errors="coerce").astype("Int64")
                yield batch_df


def estimate_destination_slice_rows(
    physical_name: str,
    *,
    billing_period: str | None = None,
    snapshot_date: str | None = None,
    connection_id: int,
    namespace: str = "silver",
    partition_spec_json: dict | None = None,
) -> int:
    """Return the manifest-level row count for one Silver replace-slice."""
    from app.services.destination_slice import (
        build_slice_row_filter,
        resolve_destination_slice_config_for_fields,
    )

    safe_name = physical_name.replace("-", "_").replace(".", "_")
    try:
        table = _get_catalog().load_table(_table_id(namespace, safe_name))
    except Exception:
        return 0
    config = resolve_destination_slice_config_for_fields(
        physical_name,
        partition_spec_json,
        {field.name for field in table.schema().fields},
    )
    row_filter = build_slice_row_filter(
        config,
        connection_id=connection_id,
        billing_period=billing_period,
        snapshot_date=snapshot_date,
    )
    return sum(int(task.file.record_count or 0) for task in table.scan(row_filter=row_filter).plan_files())


def write_destination_staged(
    physical_name: str,
    df: pd.DataFrame,
    *,
    partition_col: str = "_billing_period",
    column_types: Optional[dict[str, str]] = None,
    namespace: str = "silver",
    add_tipo: bool = True,
    partition_spec_json: dict | None = None,
    slice_config=None,
) -> str:
    """Write a destination DataFrame to a temporary Iceberg table (staged).

    Writes to gold.{physical_name}_stg_{timestamp} — does NOT touch the
    original table. Caller must call swap_staged_table() to activate.

    Args:
        column_types: Optional dict column_name → data_type for type coercion.
                      Example: {'cost_usd': 'DOUBLE', 'resource_name': 'TEXT'}.

    Returns: temp table safe_name (for later swap).
    """
    import time
    _ensure_namespace(namespace)
    safe_name = physical_name.replace("-", "_").replace(".", "_")
    # Consecutive source/month publications can start within the same second.
    # A second-resolution name could collide with a table just renamed from a
    # prior staging location and delete its metadata during cleanup.
    ts = time.time_ns()
    temp_safe_name = f"{safe_name}_stg_{ts}"
    tid = _table_id(namespace, temp_safe_name)

    if df.empty:
        return temp_safe_name

    df = df.copy()
    # Every row written to a destination Gold is historical realized data.
    # Forecast projections are published separately in *_planning.
    if add_tipo and "tipo" not in df.columns:
        df["tipo"] = "realizado"
    from app.services.destination_slice import (
        build_iceberg_partition_spec,
        primary_partition_column,
        resolve_destination_slice_config,
        sanitize_partition_source_values,
    )

    config = slice_config or resolve_destination_slice_config(
        physical_name, partition_spec_json, column_names=set(df.columns),
    )
    df = sanitize_partition_source_values(df, config)
    if partition_col == "_billing_period" and config.grain == "snapshot_date":
        partition_col = primary_partition_column(config)
    if partition_col not in df.columns:
        df[partition_col] = "unknown"

    cat = _get_catalog()
    target_schema = None
    try:
        target_schema = cat.load_table(_table_id(namespace, safe_name)).schema()
    except Exception:
        # No canonical destination yet: create the first staged schema from
        # explicit destination types plus the incoming frame.
        pass
    arrow_table = _df_to_arrow(
        df,
        column_types=column_types,
        target_schema=target_schema,
    )

    from pyiceberg.io.pyarrow import pyarrow_to_schema
    iceberg_schema = pyarrow_to_schema(arrow_table.schema)

    partition_spec = build_iceberg_partition_spec(iceberg_schema, config, pyarrow_safe=True)
    if not partition_spec.fields:
        partition_field_idx = arrow_table.schema.get_field_index(partition_col)
        if partition_field_idx < 0:
            tbl = cat.create_table(tid, schema=iceberg_schema)
            tbl.append(arrow_table)
            return temp_safe_name

        part_iceberg_field = iceberg_schema.find_field(partition_col)
        if part_iceberg_field is None:
            tbl = cat.create_table(tid, schema=iceberg_schema)
            tbl.append(arrow_table)
            return temp_safe_name

        source_id = part_iceberg_field.field_id
        new_field_id = iceberg_schema.highest_field_id + 1000
        partition_spec = PartitionSpec(
            PartitionField(
                source_id=source_id,
                field_id=new_field_id,
                transform=IdentityTransform(),
                name=partition_col,
            )
        )

    # Always create fresh temp table (delete old temp if exists)
    try:
        _force_delete_table(tid, temp_safe_name)
    except Exception:
        pass

    tbl = cat.create_table(tid, schema=iceberg_schema, partition_spec=partition_spec)
    tbl.append(arrow_table)
    logger.info("Staged %d rows to gold.%s", len(df), temp_safe_name)
    return temp_safe_name


def _align_staged_batch_to_target(batch, target) -> pa.Table:
    """Align column order and Iceberg field IDs before appending staged data."""
    target_column_names = [field.name for field in target.schema().fields]
    staged_table = pa.Table.from_batches([batch]).select(target_column_names)
    return pa.Table.from_arrays(
        [staged_table.column(name) for name in target_column_names],
        schema=target.schema().as_arrow(),
    )


_PUBLISH_APPEND_MAX_ROWS = int(os.getenv("OPENFINOPS_PUBLISH_APPEND_MAX_ROWS", "2000000"))


def _append_staged_batches(tx, batches, target, max_rows: int = _PUBLISH_APPEND_MAX_ROWS) -> int:
    """Append staged batches in as few ``tx.append`` calls as possible.

    Every ``tx.append`` is a separate Iceberg snapshot with its own manifest,
    and each new snapshot rewrites a manifest list referencing all previous
    manifests.  Appending batch by batch made publish time grow quadratically
    with table history.
    """
    appends = 0
    pending: list[pa.Table] = []
    pending_rows = 0

    def flush() -> None:
        nonlocal appends, pending, pending_rows
        if pending:
            tx.append(pa.concat_tables(pending))
            appends += 1
        pending = []
        pending_rows = 0

    for batch in batches:
        if not batch.num_rows:
            continue
        pending.append(_align_staged_batch_to_target(batch, target))
        pending_rows += batch.num_rows
        if pending_rows >= max_rows:
            flush()
    flush()
    return appends


def _merge_staged_into_canonical_table(
    physical_name: str,
    temp_safe_name: str,
    *,
    namespace: str,
    delete_filter: Any,
    known_row_count: int | None = None,
) -> int:
    """Atomic DELETE (filter) + append from staging into the canonical table."""
    safe_name = physical_name.replace("-", "_").replace(".", "_")
    main_tid = _table_id(namespace, safe_name)
    temp_tid = _table_id(namespace, temp_safe_name)
    cat = _get_catalog()

    staged = cat.load_table(temp_tid)
    try:
        target = cat.load_table(main_tid)
    except Exception as exc:
        from pyiceberg.exceptions import NoSuchTableError
        if not isinstance(exc, (NoSuchTableError, FileNotFoundError)):
            raise
        if _namespace_has_table(cat, namespace, safe_name):
            cat.drop_table(main_tid)
        target = cat.create_table(main_tid, schema=staged.schema(), partition_spec=staged.spec())

    if _is_staging_location(target):
        cat.drop_table(main_tid)
        target = cat.create_table(main_tid, schema=staged.schema(), partition_spec=staged.spec())

    _replace_incompatible_columns(target, staged)
    target = cat.load_table(main_tid)
    with target.update_schema() as schema_update:
        schema_update.union_by_name(staged.schema())
    target = cat.load_table(main_tid)

    tx = target.transaction()
    tx.delete(delete_filter)
    _append_staged_batches(tx, staged.scan().to_arrow_batch_reader(), target)
    tx.commit_transaction()
    _force_delete_table(temp_tid, temp_safe_name)

    if known_row_count is not None:
        logger.info(
            "Published staged %s into %s.%s (%d rows supplied by writer)",
            temp_safe_name,
            namespace,
            safe_name,
            known_row_count,
        )
        return int(known_row_count)

    try:
        tbl = cat.load_table(main_tid)
        rows = len(tbl.scan().to_arrow())
        logger.info("Published staged %s into %s.%s (%d rows)", temp_safe_name, namespace, safe_name, rows)
        return rows
    except Exception as e:
        logger.warning("Load after merge failed (table may still be accessible): %s", e)
        return -1


def swap_staged_table(physical_name: str, temp_safe_name: str, namespace: str = layers.GOLD,
                      known_row_count: int | None = None) -> int:
    """Replace a final table from staging without promoting its staging path.

    SQLCatalog ``rename_table`` only changes the catalog identity; it does not
    move the physical table directory.  Promoting a ``*_stg_*`` table therefore
    leaves a live table in a disposable directory.  Publish into the canonical
    table instead and remove the staging table only after the data commit.
    """
    from pyiceberg.expressions import AlwaysTrue

    return _merge_staged_into_canonical_table(
        physical_name,
        temp_safe_name,
        namespace=namespace,
        delete_filter=AlwaysTrue(),
        known_row_count=known_row_count,
    )


def replace_staged_connection(
    physical_name: str,
    temp_safe_name: str,
    *,
    connection_id: int,
    namespace: str = layers.SILVER,
    known_row_count: int | None = None,
) -> int:
    """Replace one connection's rows while retaining the other source connections."""
    from pyiceberg.expressions import EqualTo

    return _merge_staged_into_canonical_table(
        physical_name,
        temp_safe_name,
        namespace=namespace,
        delete_filter=EqualTo("source_connection_id", int(connection_id)),
        known_row_count=known_row_count,
    )


def staging_column_single_value(
    temp_safe_name: str,
    column: str,
    *,
    namespace: str = layers.GOLD,
) -> str:
    """Return the single distinct value of *column* in a staging table."""
    tid = _table_id(namespace, temp_safe_name.replace("-", "_").replace(".", "_"))
    staged = _get_catalog().load_table(tid)
    if column not in {field.name for field in staged.schema().fields}:
        raise ValueError(f"Coluna {column} ausente no staging {temp_safe_name}")
    arrow = staged.scan().select(column).to_arrow()
    if arrow.num_rows == 0:
        raise ValueError(f"Staging {temp_safe_name} vazio")
    series = arrow[column].to_pandas().dropna().astype(str).unique()
    if len(series) != 1:
        raise ValueError(
            f"Staging {temp_safe_name} deve ter um único valor em {column}, encontrados: {series[:5]}"
        )
    return str(series[0])


def purge_gold_cube_snapshots(physical_name: str, *, namespace: str = layers.GOLD) -> bool:
    """Expire Iceberg snapshots anteriores ao corrente em um cubo gold (libera disco)."""
    if namespace != layers.GOLD:
        return False
    safe_name = physical_name.replace("-", "_").replace(".", "_")
    try:
        table = _get_catalog().load_table(_table_id(namespace, safe_name))
    except Exception as exc:
        logger.warning("Purge de snapshots ignorado para %s.%s: %s", namespace, safe_name, exc)
        return False
    if table.current_snapshot() is None or _is_staging_location(table):
        return False
    try:
        from datetime import datetime, timezone
        from pyiceberg.table.maintenance import MaintenanceTable

        MaintenanceTable(table).expire_snapshots().older_than(
            datetime.now(timezone.utc)
        ).commit()
    except Exception as exc:
        logger.warning("expire_snapshots PyIceberg falhou em %s.%s: %s", namespace, safe_name, exc)
    return _trino_remove_orphan_files(namespace, safe_name)


def _trino_remove_orphan_files(namespace: str, safe_name: str, retention: str = "1s") -> bool:
    from urllib.parse import urljoin
    import httpx
    from app.services.trino_service import TRINO_URL, TRINO_USER

    session_prop = (
        f"iceberg.remove_orphan_files_min_retention={retention}"
    )
    headers = {
        "X-Trino-User": TRINO_USER,
        "X-Trino-Catalog": "iceberg",
        "X-Trino-Schema": namespace,
        "X-Trino-Session": session_prop,
    }
    sql = (
        f"ALTER TABLE iceberg.{namespace}.{safe_name} "
        f"EXECUTE remove_orphan_files(retention_threshold => '{retention}')"
    )
    submit_url = urljoin(TRINO_URL, "/v1/statement")
    try:
        with httpx.Client(timeout=300.0) as client:
            resp = client.post(submit_url, content=sql, headers=headers)
            if resp.status_code != 200:
                logger.warning("Trino remove_orphan_files HTTP %d: %s", resp.status_code, resp.text[:200])
                return False
            data = resp.json()
            next_uri = data.get("nextUri")
            while next_uri:
                resp = client.get(next_uri, headers=headers)
                data = resp.json()
                if "error" in data:
                    logger.warning(
                        "Trino remove_orphan_files: %s",
                        data["error"].get("message"),
                    )
                    return False
                if data.get("stats", {}).get("state") in ("FINISHED", "FAILED", "CANCELED"):
                    break
                next_uri = data.get("nextUri")
        logger.info("remove_orphan_files concluído para iceberg.%s.%s", namespace, safe_name)
        return True
    except Exception as exc:
        logger.warning("remove_orphan_files falhou para %s.%s: %s", namespace, safe_name, exc)
        return False


def replace_staged_cube(
    physical_name: str,
    temp_safe_name: str,
    *,
    namespace: str = layers.GOLD,
    known_row_count: int | None = None,
    purge_snapshots: bool = True,
) -> int:
    """Replace completo de cubo Gold (mesma transação da Silver) + purge de snapshots."""
    if namespace != layers.GOLD:
        raise ValueError("replace_staged_cube só se aplica ao namespace gold")
    from pyiceberg.expressions import AlwaysTrue

    rows = _merge_staged_into_canonical_table(
        physical_name,
        temp_safe_name,
        namespace=namespace,
        delete_filter=AlwaysTrue(),
        known_row_count=known_row_count,
    )
    if purge_snapshots:
        purge_gold_cube_snapshots(physical_name, namespace=namespace)
    return rows


def replace_staged_cube_snapshot_day(
    physical_name: str,
    temp_safe_name: str,
    *,
    snapshot_col: str,
    snapshot_value: str,
    namespace: str = layers.GOLD,
    known_row_count: int | None = None,
) -> int:
    """Replace de um dia em cubo Gold com snapshot_mode (demais dias preservados)."""
    if namespace != layers.GOLD:
        raise ValueError("replace_staged_cube_snapshot_day só se aplica ao namespace gold")
    from pyiceberg.expressions import EqualTo

    return _merge_staged_into_canonical_table(
        physical_name,
        temp_safe_name,
        namespace=namespace,
        delete_filter=EqualTo(snapshot_col, str(snapshot_value)),
        known_row_count=known_row_count,
    )


def cleanup_staged_table(temp_safe_name: str, namespace: str = "silver") -> None:
    """Remove a staged temp table (rollback on failure)."""
    try:
        tid = _table_id(namespace, temp_safe_name)
        _force_delete_table(tid, temp_safe_name)
        logger.info("Cleaned up staged table %s.%s", namespace, temp_safe_name)
    except Exception as e:
        logger.warning("Cleanup staged table failed: %s", e)


def _iceberg_type_key(field_type: Any) -> str:
    return type(field_type).__name__.replace("Type", "").lower()


def _replace_incompatible_columns(target, staged) -> None:
    """Drop Gold columns whose Iceberg type cannot evolve to the staged type.

    GitHub-first publishes store unused BOOLEAN dest columns as string (all-null
    object). A later Claude/NotebookLM slice writes real booleans and Iceberg
    rejects ``string -> boolean``. Deleting the old field and adding it with the
    staged type is safe: prior values were empty sentinels, not typed facts.
    """
    target_by_name = {field.name: field for field in target.schema().fields}
    replacements: list[tuple[str, Any]] = []
    for staged_field in staged.schema().fields:
        existing = target_by_name.get(staged_field.name)
        if existing is None:
            continue
        old_key = _iceberg_type_key(existing.field_type)
        new_key = _iceberg_type_key(staged_field.field_type)
        if old_key == new_key:
            continue
        allowed = {
            ("string", "boolean"),
            ("string", "long"),
            ("string", "integer"),
            ("string", "double"),
            ("string", "timestamp"),
        }
        if (old_key, new_key) in allowed:
            replacements.append((staged_field.name, staged_field.field_type))
    for name, field_type in replacements:
        logger.warning("Replacing Gold column %s type to match staged schema (%s)", name, field_type)
        with target.update_schema() as schema_update:
            schema_update.delete_column(name)
        with target.update_schema() as schema_update:
            schema_update.add_column(name, field_type)


def publish_staged_partition(
    physical_name: str,
    temp_safe_name: str,
    *,
    billing_period: str | None = None,
    snapshot_date: str | None = None,
    connection_id: int,
    namespace: str = "silver",
    partition_spec_json: dict | None = None,
    expected_row_count: int | None = None,
) -> int:
    """Atomically replace one Silver slice with rows from staging.

    Replace keys follow destination_slice (month+connection or day+connection).
    Iceberg partition extras affect pruning only, not the delete filter.
    """
    from app.services.destination_slice import (
        build_slice_row_filter,
        resolve_destination_slice_config_for_fields,
        validate_staged_table_slice,
    )

    safe_name = physical_name.replace("-", "_").replace(".", "_")
    cat = _get_catalog()
    target_id = _table_id(namespace, safe_name)
    staging_id = _table_id(namespace, temp_safe_name)
    staged = cat.load_table(staging_id)
    staged_tasks = list(staged.scan().plan_files())
    row_count = sum(task.file.record_count for task in staged_tasks)
    slice_config = resolve_destination_slice_config_for_fields(
        physical_name,
        partition_spec_json,
        {field.name for field in staged.schema().fields},
    )
    validate_staged_table_slice(
        staged,
        slice_config,
        connection_id=connection_id,
        billing_period=billing_period,
        snapshot_date=snapshot_date,
        expected_row_count=expected_row_count if expected_row_count is not None else row_count,
    )

    try:
        target = cat.load_table(target_id)
    except Exception as exc:
        from pyiceberg.exceptions import NoSuchTableError
        # A catalog entry can survive after its metadata.json was removed
        # (for example, by an interrupted staging cleanup or by a warehouse
        # path mismatch between API/worker and Trino).  Treat this as a broken
        # target, not as a reason to fail the Bronze -> Gold publication.  The
        # staged table is already the validated source of truth for this
        # connection/month; recreate only the canonical target entry below.
        exc_text = str(exc).lower()
        missing_metadata = (
            "metadata not found" in exc_text
            or "metadata location" in exc_text
            or "no such file or directory" in exc_text
        )
        missing_table = isinstance(exc, (NoSuchTableError, FileNotFoundError)) or missing_metadata
        if not missing_table:
            raise
        # Bootstrap a final canonical table.  Do not rename the staging table:
        # SQLCatalog preserves its ``*_stg_*`` filesystem location on rename.
        if _namespace_has_table(cat, namespace, safe_name):
            cat.drop_table(target_id)
        target = cat.create_table(target_id, schema=staged.schema(), partition_spec=staged.spec())

    if _is_staging_location(target):
        # A one-slice write must never drop other slices merely to repair a
        # legacy table location. The full recovery path uses swap_staged_table
        # after staging the verified complete table.
        raise RuntimeError(
            f"Destino {namespace}.{safe_name} ainda aponta para staging; "
            "execute a recuperação completa antes de publicar uma fatia"
        )

    # A partial reprocess publishes one connection/month at a time.  The
    # staging table can legitimately contain newly materialized organizational
    # dimensions (for example organograma_nivel_6 and organograma_nivel_7) while the existing
    # Gold table still has the prior schema.  Evolve the target by name before
    # appending Arrow batches; otherwise PyArrow rejects the new fields and the
    # atomic publication fails after all processing has completed.
    _replace_incompatible_columns(target, staged)
    target = cat.load_table(target_id)
    with target.update_schema() as schema_update:
        schema_update.union_by_name(staged.schema())
    target = cat.load_table(target_id)
    target_column_names = [field.name for field in target.schema().fields]
    target_arrow_fields = {field.name: field for field in target.schema().as_arrow()}

    overwrite_filter = build_slice_row_filter(
        slice_config,
        connection_id=connection_id,
        billing_period=billing_period,
        snapshot_date=snapshot_date,
    )
    batches = [batch for batch in staged.scan().to_arrow_batch_reader() if batch.num_rows]
    last_exc: Exception | None = None
    for attempt in range(5):
        target = cat.load_table(target_id)
        try:
            tx = target.transaction()
            tx.delete(overwrite_filter)
            # ``add_files`` only accepts external Parquet without Iceberg field IDs.
            # Staging is produced by PyIceberg and therefore contains those IDs; write
            # the staged rows natively into this transaction instead.
            _append_staged_batches(tx, batches, target)
            tx.commit_transaction()
            break
        except Exception as exc:
            last_exc = exc
            if attempt < 4 and "branch main has changed" in str(exc):
                logger.warning(
                    "Silver slice commit conflict %s.%s attempt %s/5, retrying",
                    namespace,
                    safe_name,
                    attempt + 1,
                )
                continue
            raise
    # The native write above created new target files, so staging can be safely
    # removed with all of its now-unreferenced files.
    _force_delete_table(staging_id, temp_safe_name)
    slice_label = snapshot_date if slice_config.grain == "snapshot_date" else billing_period
    logger.info(
        "Published %d rows for %s/%s into %s.%s",
        row_count,
        connection_id,
        slice_label,
        namespace,
        safe_name,
    )
    return int(row_count)


def publish_silver_dataframe_slice(
    physical_name: str,
    df: pd.DataFrame,
    *,
    connection_id: int,
    billing_period: str | None = None,
    snapshot_date: str | None = None,
    column_types: Optional[dict[str, str]] = None,
    partition_spec_json: dict | None = None,
    namespace: str = "silver",
) -> int:
    """Stage and atomically publish one Silver destination slice."""
    from app.services.destination_slice import (
        primary_partition_column,
        resolve_destination_slice_config,
        sanitize_partition_source_values,
        validate_staging_slice,
    )

    if df.empty:
        return 0
    config = resolve_destination_slice_config(
        physical_name, partition_spec_json, column_names=set(df.columns),
    )
    df = sanitize_partition_source_values(df, config)
    validate_staging_slice(
        df,
        config,
        connection_id=connection_id,
        billing_period=billing_period,
        snapshot_date=snapshot_date,
    )
    temp_name = write_destination_staged(
        physical_name,
        df,
        partition_col=primary_partition_column(config),
        column_types=column_types,
        namespace=namespace,
        slice_config=config,
    )
    return publish_staged_partition(
        physical_name,
        temp_name,
        billing_period=billing_period,
        snapshot_date=snapshot_date,
        connection_id=connection_id,
        namespace=namespace,
        partition_spec_json=partition_spec_json,
        expected_row_count=len(df),
    )


def publish_staged_by_slices(physical_name: str, temp_safe_name: str, namespace: str = layers.GOLD) -> int:
    """Publish a staged table slice-by-slice into the Gold destination table.

    Groups rows by (_billing_period, source_connection_id) and calls
    publish_staged_partition for each slice, ensuring that other connections'
    data in the Gold destination table is NEVER overwritten or dropped.
    """
    raise RuntimeError(
        "Publicação multi-fatia foi desativada: enfileire uma tarefa por origem+mês "
        "e chame publish_staged_partition."
    )


def append_to_staged(
    temp_safe_name: str,
    df: pd.DataFrame,
    *,
    partition_col: str = "_billing_period",
    column_types: Optional[dict[str, str]] = None,
    namespace: str = "silver",
) -> None:
    """Append data to an existing staged temp table."""
    if df.empty:
        return

    df = df.copy()
    if partition_col not in df.columns:
        df[partition_col] = "unknown"

    cat = _get_catalog()
    tid = _table_id(namespace, temp_safe_name)

    try:
        tbl = cat.load_table(tid)
        arrow_table = _df_to_arrow(
            df,
            column_types=column_types,
            target_schema=tbl.schema(),
        )
        tbl.append(arrow_table)
        logger.info("Appended %d rows to staged gold.%s", len(df), temp_safe_name)
    except Exception as e:
        logger.error("Append to staged table failed: %s", e)
        raise


def write_destination(
    physical_name: str,
    df: pd.DataFrame,
    *,
    partition_col: str = "_billing_period",
    column_types: Optional[dict[str, str]] = None,
    namespace: str = "silver",
    allow_append: bool = False,
) -> int:
    """Write a destination DataFrame to an Iceberg table (append-or-create).

    Silver ``dest_*`` tables reject append; use publish_silver_dataframe_slice.
    """
    if _is_silver_destination_table(physical_name, namespace=namespace) and not allow_append:
        raise RuntimeError(
            f"Append direto em {namespace}.{physical_name} está desabilitado; "
            "use write_destination_staged + publish_staged_partition "
            "(ou publish_silver_dataframe_slice)."
        )
    if df.empty:
        return 0

    _ensure_namespace(namespace)
    safe_name = physical_name.replace("-", "_").replace(".", "_")
    tid = _table_id(namespace, safe_name)

    df = df.copy()
    if partition_col not in df.columns:
        df[partition_col] = "unknown"

    cat = _get_catalog()
    target_schema = None
    try:
        target_schema = cat.load_table(tid).schema()
    except Exception:
        pass
    arrow_table = _df_to_arrow(
        df,
        column_types=column_types,
        target_schema=target_schema,
    )
    rows = len(df)

    from pyiceberg.io.pyarrow import pyarrow_to_schema
    iceberg_schema = pyarrow_to_schema(arrow_table.schema)

    partition_field_idx = arrow_table.schema.get_field_index(partition_col)

    if partition_field_idx < 0:
        logger.warning("Partition column %s not in schema — appending without partition", partition_col)
        try:
            tbl = cat.load_table(tid)
            tbl.append(arrow_table)
            return rows
        except Exception:
            pass
        tbl = cat.create_table(tid, schema=iceberg_schema)
        tbl.append(arrow_table)
        return rows

    part_iceberg_field = iceberg_schema.find_field(partition_col)
    if part_iceberg_field is None:
        logger.warning("Partition column %s not in Iceberg schema", partition_col)
        tbl = cat.create_table(tid, schema=iceberg_schema)
        tbl.append(arrow_table)
        return rows

    source_id = part_iceberg_field.field_id
    new_field_id = iceberg_schema.highest_field_id + 1000
    partition_spec = PartitionSpec(
        PartitionField(
            source_id=source_id,
            field_id=new_field_id,
            transform=IdentityTransform(),
            name=partition_col,
        )
    )

    try:
        tbl = cat.load_table(tid)
        tbl.append(arrow_table)
        logger.info("Appended %d rows to gold.%s", rows, safe_name)
        return rows
    except Exception as exc:
        # Check if the table exists — if it does, the append failure is likely
        # a schema mismatch. NEVER force-recreate here because that would drop
        # data from previous blobs. Re-raise so the caller can handle it.
        try:
            tables_in_ns = cat.list_tables(tid[0])
            table_names = [t[0] if isinstance(t, tuple) else t for t in tables_in_ns]
            if safe_name in table_names:
                logger.error("Gold append failed for %s (table exists, NOT recreating): %s", safe_name, exc)
                raise
        except Exception:
            # If list_tables itself fails, assume the table might exist and don't risk force-recreate
            logger.error("Gold append failed for %s, cannot verify table existence: %s — re-raising", safe_name, exc)
            raise

    # Table truly doesn't exist — create it fresh
    tbl = cat.create_table(tid, schema=iceberg_schema, partition_spec=partition_spec)
    tbl.append(arrow_table)
    logger.info("Created gold.%s with %d rows (partitioned by %s)", safe_name, rows, partition_col)
    return rows


def _force_delete_table(tid: tuple, safe_name: str) -> None:
    """Force-delete Iceberg table: SQL metadata + local Parquet/metadata files."""
    import shutil, os
    from urllib.parse import urlparse
    target_locations: list[str] = []
    ns = tid[0] if isinstance(tid, tuple) and len(tid) > 0 else layers.GOLD

    # 1. Obter caminho físico real antes do drop no catálogo
    cat = _get_catalog()
    try:
        tbl = cat.load_table(tid)
        loc = tbl.location()
        if loc:
            target_locations.append(loc.replace("file://", ""))
    except Exception:
        pass

    # SQLCatalog can leave a staging table and its canonical table pointing at
    # the same physical location after legacy renames. Drop the requested
    # catalog entry, but never remove files still referenced by another table.
    protected_locations: set[str] = set()
    try:
        from app.database import SyncSession
        from sqlalchemy import text
        pg = SyncSession()
        try:
            refs = pg.execute(text(
                "SELECT table_namespace, table_name, metadata_location FROM iceberg_tables"
            )).all()
            for ref_namespace, ref_name, metadata_location in refs:
                if (ref_namespace, ref_name) == (ns, safe_name) or not metadata_location:
                    continue
                metadata_path = urlparse(str(metadata_location)).path
                table_location = os.path.realpath(_normalize_pyiceberg_location(
                    str(Path(metadata_path).parent.parent)
                ))
                protected_locations.add(table_location)
        finally:
            pg.close()
    except Exception as exc:
        # Without a complete reference check, catalog removal is still safe;
        # physical deletion is skipped below.
        logger.warning("Could not verify shared Iceberg locations before deleting %s: %s", tid, exc)
        protected_locations.add("__catalog_reference_check_failed__")

    # 2. Dropar tabela via catálogo (remove registro do Postgres no SqlCatalog)
    try:
        cat.drop_table(tid)
        logger.info("Dropped table %s via catalog", tid)
    except Exception:
        pass  # Table may not exist in catalog, fall through to manual cleanup

    # 3. Clean SQL catalog metadata
    try:
        from app.database import SyncSession
        from sqlalchemy import text
        pg = SyncSession()
        pg.execute(text(
            "DELETE FROM iceberg_tables WHERE table_namespace = :namespace AND table_name = :name"
        ), {"namespace": ns, "name": safe_name})
        pg.commit()
        pg.close()
    except Exception:
        pass

    # 4. Remove only the resolved physical location for this exact table.
    # Canonical /data paths have a different host spelling in local dev.
    for loc_path in target_locations:
        physical = _normalize_pyiceberg_location(loc_path)
        if os.path.realpath(physical) in protected_locations:
            logger.warning("Preserving shared Iceberg location while deleting %s: %s", tid, physical)
            continue
        if os.path.isdir(physical):
            shutil.rmtree(physical, ignore_errors=True)
            logger.info("Deleted warehouse dir via location: %s", physical)

    # 5. Clean local warehouse files (fallback por convenção de pasta)
    warehouse = _LOCAL_WAREHOUSE
    for namespace_pattern in [ns, f"{ns}.db"]:
        base = str(warehouse / namespace_pattern)
        candidate = os.path.join(base, safe_name)
        if os.path.isdir(candidate) and os.path.realpath(candidate) not in protected_locations:
            shutil.rmtree(candidate, ignore_errors=True)
            logger.info("Deleted warehouse dir: %s", candidate)


def cleanup_orphan_staging(max_age_hours: int = 2) -> dict[str, int]:
    """Garbage collector periódico para remover pastas temporárias órfãs (*_stg_*).

    Inspeciona o warehouse local, protege todas as tabelas ativas no catálogo SQL
    e exclui diretórios com padrão *_stg_* que tenham mais de max_age_hours de inatividade
    e não correspondam a nenhuma tabela registrada.
    """
    import os, shutil, time
    from pathlib import Path
    from app.database import SyncSession
    from app.models.destination import DestinationTable
    from app.models.etl_task import EtlTask
    from sqlalchemy import func, select

    cat = _get_catalog()
    active_locations: set[str] = set()
    catalog_errors: list[str] = []
    warehouse_path = Path(_LOCAL_WAREHOUSE).resolve()
    now = time.time()
    min_age_seconds = max_age_hours * 3600
    dropped_tables = 0

    if not warehouse_path.exists():
        return {"deleted_dirs": 0, "deleted_bytes": 0, "dropped_tables": 0}

    known_namespaces = list(layers.ACTIVE_LAYERS) + ["financial_base"]

    def _has_active_writer(namespace: str, base_name: str) -> bool:
        session = SyncSession()
        try:
            if namespace == layers.GOLD:
                return session.execute(
                    select(EtlTask.id)
                    .join(DestinationTable, DestinationTable.id == EtlTask.destination_table_id)
                    .where(
                        EtlTask.task_type.in_(["reprocess", "reclassification"]),
                        EtlTask.status.in_(["QUEUED", "RUNNING"]),
                        func.replace(
                            func.replace(DestinationTable.physical_name, "-", "_"), ".", "_"
                        ) == base_name,
                    )
                    .limit(1)
                ).scalar_one_or_none() is not None
            return session.execute(
                select(EtlTask.id).where(
                    EtlTask.task_type.in_(["reprocess", "reclassification", "gold_reprocess", "shared_cost", "forecast", "forecast_accuracy", "ai_cost_relationship_reprocess"]),
                    EtlTask.status.in_(["QUEUED", "RUNNING"]),
                ).limit(1)
            ).scalar_one_or_none() is not None
        finally:
            session.close()

    for ns in known_namespaces:
        try:
            for t in cat.list_tables(ns):
                table_name = t[1] if isinstance(t, tuple) else t
                match = _STAGING_TABLE_RE.fullmatch(str(table_name))
                if match:
                    created_at = int(match.group("timestamp")) / 1_000_000_000
                    if now - created_at >= min_age_seconds and not _has_active_writer(ns, match.group("base")):
                        _force_delete_table((ns, table_name), str(table_name))
                        dropped_tables += 1
                        continue
                try:
                    tbl = cat.load_table((ns, table_name))
                    loc = _normalize_pyiceberg_location(tbl.location())
                    active_locations.add(os.path.realpath(loc))
                except Exception as exc:
                    if match and now - int(match.group("timestamp")) / 1_000_000_000 >= min_age_seconds and not _has_active_writer(ns, match.group("base")):
                        _force_delete_table((ns, table_name), str(table_name))
                        dropped_tables += 1
                        continue
                    catalog_errors.append(f"{ns}.{table_name}: {exc}")
        except Exception as exc:
            # A failed catalog read is not evidence that its directory is
            # orphaned. Stop the collector rather than risking live data.
            catalog_errors.append(f"{ns}: {exc}")

    if catalog_errors:
        logger.error("Staging cleanup skipped because catalog verification failed: %s", catalog_errors)
        return {"deleted_dirs": 0, "deleted_bytes": 0, "dropped_tables": dropped_tables}

    deleted_dirs = 0
    deleted_bytes = 0

    # A failed publication leaves a staging table registered in the SQL
    # catalog. Such tables are not filesystem orphans, so remove their catalog
    # entries explicitly after the safety window unless their writer is active.
    for namespace in known_namespaces:
        for table_ref in cat.list_tables(namespace):
            table_name = table_ref[1] if isinstance(table_ref, tuple) else table_ref
            match = _STAGING_TABLE_RE.fullmatch(str(table_name))
            if not match:
                continue
            created_at = int(match.group("timestamp")) / 1_000_000_000
            if now - created_at < min_age_seconds or _has_active_writer(namespace, match.group("base")):
                continue
            _force_delete_table((namespace, table_name), str(table_name))
            dropped_tables += 1

    for db_dir in warehouse_path.glob("*"):
        if not db_dir.is_dir():
            continue
        for tbl_dir in db_dir.glob("*"):
            if not tbl_dir.is_dir() or "_stg_" not in tbl_dir.name:
                continue

            real_path = os.path.realpath(str(tbl_dir))
            if real_path in active_locations:
                continue

            try:
                mtime = tbl_dir.stat().st_mtime
                if (now - mtime) < min_age_seconds:
                    continue  # Muito recente, pode pertencer a um job em execução

                dir_size = 0
                for f in tbl_dir.rglob("*"):
                    if f.is_file():
                        dir_size += f.stat().st_size

                shutil.rmtree(tbl_dir, ignore_errors=True)
                deleted_dirs += 1
                deleted_bytes += dir_size
                logger.info("Garbage Collector Iceberg removeu pasta órfã: %s (%d bytes)", tbl_dir.name, dir_size)
            except Exception as e:
                logger.warning("Falha ao remover pasta órfã %s: %s", tbl_dir, e)

    if deleted_dirs > 0:
        logger.info(
            "Garbage Collector Iceberg finalizado: %d pastas removidas, %.2f MB liberados",
            deleted_dirs, deleted_bytes / (1024 * 1024)
        )
    return {
        "deleted_dirs": deleted_dirs,
        "deleted_bytes": deleted_bytes,
        "dropped_tables": dropped_tables,
    }


def expire_gold_snapshots(table_name: str = "dest_modelo_focus", retention_days: int = 7) -> bool:
    """Maintain historical snapshots only after proving the active table is safe."""
    from urllib.parse import urljoin
    import httpx
    from app.services.trino_service import TRINO_URL, TRINO_USER

    # Never run maintenance against a broken/staging-backed table. Snapshot
    # age is not data age: the active snapshot remains authoritative forever.
    try:
        table = _get_catalog().load_table((layers.detail_layer(), table_name))
        if table.current_snapshot() is None or "_stg_" in str(table.location()):
            logger.error("Snapshot maintenance skipped for unsafe table gold.%s", table_name)
            return False
    except Exception as exc:
        logger.error("Snapshot maintenance skipped; Gold metadata is unavailable: %s", exc)
        return False
    try:
        from app.database import SyncSession
        from sqlalchemy import text
        session = SyncSession()
        active_writer = session.execute(text(
            "SELECT EXISTS (SELECT 1 FROM etl_task WHERE status = 'RUNNING' "
            "AND task_type IN ('reprocess','reclassification','gold_reprocess','shared_cost','forecast','forecast_accuracy','ai_cost_relationship_reprocess'))"
        )).scalar()
        session.close()
        if active_writer:
            logger.info("Snapshot maintenance postponed: analytic writer is active")
            return False
    except Exception as exc:
        logger.error("Snapshot maintenance skipped; writer state is unknown: %s", exc)
        return False
    retention_str = f"{max(7, int(retention_days))}d"
    session_prop = f"iceberg.expire_snapshots_min_retention={retention_str},iceberg.remove_orphan_files_min_retention={retention_str}"
    headers = {
        "X-Trino-User": TRINO_USER,
        "X-Trino-Catalog": "iceberg",
        "X-Trino-Schema": layers.detail_layer(),
        "X-Trino-Session": session_prop,
    }
    submit_url = urljoin(TRINO_URL, "/v1/statement")
    statements = [
        f"ALTER TABLE iceberg.{layers.detail_layer()}.{table_name} EXECUTE expire_snapshots(retention_threshold => '{retention_str}')",
        f"ALTER TABLE iceberg.gold.{table_name} EXECUTE remove_orphan_files(retention_threshold => '{retention_str}')",
    ]

    try:
        with httpx.Client(timeout=300.0) as client:
            for sql in statements:
                resp = client.post(submit_url, content=sql, headers=headers)
                if resp.status_code != 200:
                    logger.warning("Trino %s HTTP %d: %s", sql[:40], resp.status_code, resp.text)
                    continue
                data = resp.json()
                next_uri = data.get("nextUri")
                while next_uri:
                    resp = client.get(next_uri, headers=headers)
                    data = resp.json()
                    if "error" in data:
                        logger.warning("Trino erro em %s: %s", sql[:40], data["error"].get("message"))
                        break
                    state = data.get("stats", {}).get("state")
                    if state in ("FINISHED", "FAILED", "CANCELED"):
                        break
                    next_uri = data.get("nextUri")
        logger.info("Manutenção de snapshots concluída para iceberg.gold.%s (retenção: %s)", table_name, retention_str)
        return True
    except Exception as exc:
        logger.warning("Falha na manutenção de snapshots para iceberg.gold.%s: %s", table_name, exc)
        return False


def get_catalog() -> Catalog:
    """Expose the catalog for external use (e.g., health checks)."""
    return _get_catalog()


def table_exists(physical_name: str, namespace: str = layers.GOLD) -> bool:
    """Return whether the Iceberg table is registered in the catalog."""
    safe_name = physical_name.replace("-", "_").replace(".", "_")
    try:
        _get_catalog().load_table(_table_id(namespace, safe_name))
        return True
    except Exception:
        return False


def rename_legacy_organograma_columns(namespace: str) -> dict[str, list[tuple[str, str]]]:
    """Rename legacy departamento_* columns to organograma_* on existing Iceberg tables."""
    cat = _get_catalog()
    _ensure_namespace(namespace)
    renamed: dict[str, list[tuple[str, str]]] = {}
    for short_name in list_tables(namespace):
        table_id = (namespace, short_name)
        try:
            tbl = cat.load_table(table_id)
        except Exception:
            continue
        field_names = {field.name for field in tbl.schema().fields}
        pairs: list[tuple[str, str]] = []
        for level in range(1, 8):
            old_name = f"departamento_nivel_{level}"
            new_name = f"organograma_nivel_{level}"
            if old_name in field_names and new_name not in field_names:
                pairs.append((old_name, new_name))
        if "departamento" in field_names and "organograma" not in field_names:
            pairs.append(("departamento", "organograma"))
        if not pairs:
            continue
        with tbl.update_schema() as schema_update:
            for old_name, new_name in pairs:
                schema_update.rename_column(old_name, new_name)
        renamed[short_name] = pairs
        logger.info(
            "Renamed %d hierarchy column(s) on %s.%s",
            len(pairs),
            namespace,
            short_name,
        )
    return renamed


def prune_stale_catalog_entries(namespace: str) -> int:
    """Drop catalog metadata for tables whose warehouse/metadata no longer loads.

    After a filesystem-only reset, ``iceberg_tables`` can still list silver/gold
    names while PyIceberg cannot open them — Analysis would show ghosts and
    partition metadata would fail with 503.
    """
    cat = _get_catalog()
    try:
        catalog_tables = list(cat.list_tables(namespace))
    except Exception:
        return 0
    removed = 0
    for tup in catalog_tables:
        table_name = tup[1] if isinstance(tup, tuple) and len(tup) > 1 else tup
        safe_name = str(table_name)
        try:
            cat.load_table((namespace, safe_name))
        except Exception as exc:
            logger.info(
                "Pruning stale Iceberg catalog entry %s.%s: %s",
                namespace,
                safe_name,
                exc,
            )
            _force_delete_table((namespace, safe_name), safe_name)
            removed += 1
    return removed


def remove_catalog_table(namespace: str, table_name: str) -> None:
    """Remove one table from the SQL catalog (metadata only when files are gone)."""
    _force_delete_table((namespace, str(table_name)), str(table_name))


def list_tables(namespace: str) -> list[str]:
    """List all Iceberg tables in a namespace."""
    _ensure_namespace(namespace)
    try:
        # PyIceberg returns (namespace, table) identifiers. Older local
        # catalogs may return plain names, so support both forms.
        return [tup[1] if isinstance(tup, tuple) and len(tup) > 1 else tup
                for tup in _get_catalog().list_tables(namespace)]
    except Exception:
        return []


def write_forecast_result(table_name: str, df: pd.DataFrame) -> int:
    """Replace a forecast result table in the dedicated forecast namespace."""
    if df.empty:
        return 0
    namespace = "forecast"
    _ensure_namespace(namespace)
    safe_name = table_name.replace("-", "_").replace(".", "_")
    tid = (namespace, safe_name)
    arrow_table = _df_to_arrow(df, column_types={"forecast_value": "DOUBLE"})
    cat = _get_catalog()
    try:
        cat.drop_table(tid)
    except Exception:
        pass
    from pyiceberg.io.pyarrow import pyarrow_to_schema
    schema = pyarrow_to_schema(arrow_table.schema)
    table = cat.create_table(tid, schema=schema)
    table.append(arrow_table)
    logger.info("Published %d forecast rows to %s.%s", len(df), namespace, safe_name)
    return len(df)


def planning_table_name(physical_name: str) -> str:
    return physical_name.replace("-", "_").replace(".", "_") + "_planning"


def _publish_planning_dataframe(safe_name: str, df: pd.DataFrame, value_column: str) -> str:
    # Planejamento é detalhe (série mensal por dimensão), não cubo: vive na
    # camada de detalhe. A resolução vem de lakehouse_layers para que o escritor
    # e o merge de rateio nunca divirjam (ver merge_planning_rateio).
    namespace = layers.detail_layer()
    _ensure_namespace(namespace)
    arrow_table = _df_to_arrow(df, column_types={value_column: "DOUBLE"})
    tid = (namespace, safe_name)
    cat = _get_catalog()
    try:
        cat.drop_table(tid)
    except Exception:
        pass
    from pyiceberg.io.pyarrow import pyarrow_to_schema
    table = cat.create_table(tid, schema=pyarrow_to_schema(arrow_table.schema))
    table.append(arrow_table)
    logger.info("Published %d planning rows to %s.%s", len(df), namespace, safe_name)
    return safe_name


def write_forecast_planning(physical_name: str, df: pd.DataFrame, value_column: str | None = None) -> str:
    """Publish the compact monthly planning table in Gold.

    This is intentionally separate from the detailed Gold fact table. It only
    contains configured series dimensions, month, type and forecast measures.
    ``value_column`` names the forecast measure (e.g. the configured measure
    column) in the published table; when empty it falls back to ``value``.
    Forecast publish replaces the table; shared-cost rateio is rewritten afterwards.
    """
    safe_name = planning_table_name(physical_name)
    if df.empty:
        return safe_name
    keep = [c for c in df.columns if c not in {"selected_method", "fallback_level", "history_points", "backtest_wape"}]
    planning = df[keep].copy()
    measure_name = value_column or "value"
    planning = planning.rename(columns={"forecast_month": "period", "forecast_value": measure_name})
    return _publish_planning_dataframe(safe_name, planning, measure_name)


def merge_planning_rateio(
    planning_name: str,
    rateio_df: pd.DataFrame,
    value_column: str = "realizado",
    *,
    preserve_types: tuple[str, ...] = ("realizado", "forecast"),
) -> int:
    """Substitui as linhas ``tipo='rateio'`` preservando realizado e forecast.

    A leitura da tabela existente usa o **mesmo** resolvedor de camada da
    publicação (``lakehouse_layers.detail_layer``). Antes disso, a publicação
    gravava em ``silver`` e esta função lia de ``gold``: a leitura voltava vazia
    e cada execução de rateio republicava a tabela apenas com ``tipo='rateio'``,
    descartando silenciosamente realizado e forecast.

    Se a tabela existente não puder ser lida, o merge **não** destrói o que não
    conseguiu ler: levanta erro em vez de publicar um subconjunto.
    """
    safe_name = planning_name.replace("-", "_").replace(".", "_")
    namespace = layers.detail_layer()
    cat = _get_catalog()
    tid = (namespace, safe_name)

    read_error: Exception | None = None
    existing = pd.DataFrame()
    try:
        table = cat.load_table(tid)
        existing = table.scan().to_pandas()
    except Exception as exc:  # noqa: BLE001 - distinguimos "não existe" de "falhou"
        read_error = exc

    if rateio_df is None:
        rateio_df = pd.DataFrame()

    if read_error is not None and not rateio_df.empty:
        # Tabela ausente é aceitável apenas quando não há linhas prévias a
        # preservar; qualquer outra falha de leitura aborta o merge.
        missing = "NoSuchTable" in type(read_error).__name__ or "does not exist" in str(read_error).lower()
        if not missing:
            raise RuntimeError(
                f"Falha ao ler {namespace}.{safe_name} antes do merge de rateio; "
                f"recusando publicar para não perder realizado/forecast: {read_error}"
            )
        logger.info(
            "Planning %s.%s ainda não existe; publicando somente as linhas de rateio",
            namespace, safe_name,
        )

    if existing.empty:
        keep = existing
    elif "tipo" in existing.columns:
        keep = existing[existing["tipo"].astype(str).str.lower() != "rateio"].copy()
    else:
        keep = existing.copy()

    if keep.empty and rateio_df.empty:
        return 0

    # Sem rateio novo e sem rateio anterior não há nada a fazer: republicar
    # reescreveria a tabela inteira e poderia perder linhas se qualquer etapa
    # da escrita falhasse.
    if rateio_df.empty and _type_counts(existing).get("rateio", 0) == 0:
        return 0

    frames = [frame for frame in (keep, rateio_df) if frame is not None and not frame.empty]
    combined = pd.concat(frames, ignore_index=True, sort=False)

    # Guarda de perda de dado: um tipo preservado que existia antes não pode
    # desaparecer do resultado por causa do merge.
    before = _type_counts(existing)
    after = _type_counts(combined)
    for tipo in preserve_types:
        if before.get(tipo, 0) > 0 and after.get(tipo, 0) == 0:
            raise RuntimeError(
                f"Merge de rateio descartaria tipo='{tipo}' de {namespace}.{safe_name} "
                f"(antes={before.get(tipo, 0)} linhas, depois=0); publicação abortada"
            )

    _publish_planning_dataframe(safe_name, combined, value_column)
    logger.info(
        "Merged rateio into %s.%s: before=%s after=%s",
        namespace, safe_name, before, after,
    )
    return 0 if rateio_df.empty else int(len(rateio_df))


def _type_counts(frame: pd.DataFrame) -> dict[str, int]:
    """Contagem de linhas por ``tipo`` (case-insensitive); vazio sem a coluna."""
    if frame is None or frame.empty or "tipo" not in frame.columns:
        return {}
    counts = frame["tipo"].astype(str).str.lower().value_counts()
    return {str(key): int(value) for key, value in counts.items()}


def _append_or_create_table(namespace: str, table_name: str, df: pd.DataFrame, partition_col: str) -> int:
    if df is None or df.empty:
        return 0
    _ensure_namespace(namespace)
    tid = _table_id(namespace, table_name)
    frame = df.copy()
    if partition_col not in frame.columns:
        frame[partition_col] = "unknown"
    arrow_table = _df_to_arrow(frame)
    cat = _get_catalog()
    try:
        tbl = cat.load_table(tid)
        tbl.append(arrow_table)
    except Exception:
        from pyiceberg.io.pyarrow import pyarrow_to_schema
        iceberg_schema = pyarrow_to_schema(arrow_table.schema)
        partition_field_idx = arrow_table.schema.get_field_index(partition_col)
        spec = PartitionSpec()
        if partition_field_idx >= 0:
            spec = PartitionSpec(
                PartitionField(
                    source_id=iceberg_schema.find_field(partition_col).field_id,
                    field_id=iceberg_schema.highest_field_id + 1,
                    transform=IdentityTransform(),
                    name=partition_col,
                )
            )
        cat.create_table(tid, schema=iceberg_schema, partition_spec=spec)
        cat.load_table(tid).append(arrow_table)
    return len(frame)


def upsert_azure_ptu_hourly(df: pd.DataFrame) -> int:
    """Replace gold.dest_azure_ptu_metrics rows for the same connection+hour keys."""
    if df is None or df.empty:
        return 0
    from pyiceberg.expressions import And, EqualTo, GreaterThanOrEqual, LessThanOrEqual

    _ensure_namespace(layers.GOLD)
    tid = _table_id(layers.GOLD, "dest_azure_ptu_metrics")
    frame = df.copy()
    if "_billing_period" not in frame.columns:
        frame["_billing_period"] = frame["metric_hour"].astype(str).str[:7].str.replace("-", "")
    numeric_types = {
        name: "DOUBLE" for name in (
            "provisioned_ptus", "input_tokens_sum", "output_tokens_sum", "total_tokens_sum",
            "active_tokens_sum", "active_tokens_max", "active_tokens_p95", "utilization_avg",
            "utilization_max", "utilization_p95", "request_count", "request_peak_per_minute",
            "success_count", "client_error_count", "server_error_count", "rate_limited_count",
            "spillover_request_count", "time_to_response_avg_ms", "time_to_response_max_ms",
            "time_to_response_p95_ms", "time_to_last_byte_avg_ms", "normalized_ttft_avg_ms",
            "normalized_ttft_max_ms", "normalized_ttft_p95_ms", "normalized_tbt_avg_ms",
            "normalized_tbt_max_ms", "normalized_tbt_p95_ms",             "tokens_per_second_avg", "tokens_per_second_max", "completeness_pct",
            "context_cache_match_rate_avg", "availability_rate_avg",
            "utilization_avg_raw", "utilization_max_raw",
            "streaming_request_count", "non_streaming_request_count",
        )
    }
    numeric_types.update({
        "connection_id": "BIGINT", "source_points": "BIGINT", "expected_points": "BIGINT",
        "utilization_quality_anomaly": "BOOLEAN",
    })
    cat = _get_catalog()
    try:
        tbl = cat.load_table(tid)
    except Exception:
        return _append_or_create_table("gold", "dest_azure_ptu_metrics", frame, "_billing_period")
    from pyiceberg.io.pyarrow import pyarrow_to_schema
    incoming = _df_to_arrow(frame, column_types=numeric_types)
    incoming_schema = pyarrow_to_schema(incoming.schema)
    existing_names = {field.name for field in tbl.schema().fields}
    incoming_names = {field.name for field in incoming_schema.fields}
    if not incoming_names.issubset(existing_names):
        with tbl.update_schema() as schema_update:
            for field in incoming_schema.fields:
                if field.name not in existing_names:
                    schema_update.add_column(field.name, field.field_type)
        tbl = cat.load_table(tid)
    target_types: dict[str, str] = {}
    for field in tbl.schema().fields:
        field_type = str(field.field_type).lower()
        if "double" in field_type or "float" in field_type or "decimal" in field_type:
            target_types[field.name] = "DOUBLE"
        elif "long" in field_type or "int" in field_type:
            target_types[field.name] = "BIGINT"
        elif "bool" in field_type:
            target_types[field.name] = "BOOLEAN"
        else:
            target_types[field.name] = "TEXT"
    periods = sorted({str(x) for x in frame["_billing_period"].dropna().unique()})
    for period in periods:
        subset = frame[frame["_billing_period"].astype(str) == period]
        if subset.empty:
            continue
        period_conn_ids = sorted({int(x) for x in subset["connection_id"].dropna().unique()})
        tx = tbl.transaction()
        for conn_id in period_conn_ids:
            conn_subset = subset[subset["connection_id"] == conn_id]
            if conn_subset.empty:
                continue
            min_hour = str(conn_subset["metric_hour"].min())
            max_hour = str(conn_subset["metric_hour"].max())
            tx.delete(And(
                EqualTo("_billing_period", period),
                EqualTo("connection_id", conn_id),
                GreaterThanOrEqual("metric_hour", min_hour),
                LessThanOrEqual("metric_hour", max_hour),
            ))
        tx.append(_df_to_arrow(subset, column_types=target_types, target_schema=tbl.schema()))
        tx.commit_transaction()
    return len(frame)


def write_azure_ptu_bronze_metrics(df: pd.DataFrame, connection_code: str | None = None) -> int:
    """Append audit rows; evolve schema and map field IDs when the table already exists."""
    if df is None or df.empty:
        return 0
    from pyiceberg.io.pyarrow import pyarrow_to_schema

    _ensure_namespace("bronze")
    # Nome padrão do Bronze: a fonte carrega a conexão, senão uma conexão nova
    # sobrescreveria o dado da anterior na mesma tabela.
    nome_tabela = (
        f"raw_{connection_code.replace('-', '_')}_ptu_metrics"
        if connection_code
        else "raw_azure_ptu_metrics"
    )
    tid = _table_id("bronze", nome_tabela)
    frame = df.copy()
    if "_billing_period" not in frame.columns:
        frame["_billing_period"] = frame["metric_hour"].astype(str).str[:7].str.replace("-", "")
    numeric_types = {
        name: "DOUBLE" for name in (
            "provisioned_ptus", "input_tokens_sum", "output_tokens_sum", "total_tokens_sum",
            "active_tokens_sum", "active_tokens_max", "active_tokens_p95", "utilization_avg",
            "utilization_max", "utilization_p95", "request_count", "request_peak_per_minute",
            "time_to_response_avg_ms", "time_to_response_max_ms", "time_to_response_p95_ms",
            "time_to_last_byte_avg_ms", "normalized_ttft_avg_ms", "normalized_ttft_max_ms",
            "normalized_ttft_p95_ms", "normalized_tbt_avg_ms", "normalized_tbt_max_ms",
            "normalized_tbt_p95_ms", "completeness_pct",
        )
    }
    numeric_types.update({
        "connection_id": "BIGINT",
        "source_points": "BIGINT",
        "expected_points": "BIGINT",
        "success_count": "TEXT",
        "client_error_count": "TEXT",
        "server_error_count": "TEXT",
        "rate_limited_count": "TEXT",
        "spillover_request_count": "TEXT",
        "tokens_per_second_avg": "TEXT",
        "tokens_per_second_max": "TEXT",
        "window_start": "TEXT",
        "raw_dimensions": "TEXT",
        "collected_at": "TEXT",
    })
    cat = _get_catalog()
    try:
        tbl = cat.load_table(tid)
    except Exception:
        return _append_or_create_table("bronze", nome_tabela, frame, "_billing_period")
    incoming = _df_to_arrow(frame, column_types=numeric_types)
    incoming_schema = pyarrow_to_schema(incoming.schema)
    existing_names = {field.name for field in tbl.schema().fields}
    incoming_names = {field.name for field in incoming_schema.fields}
    if not incoming_names.issubset(existing_names):
        with tbl.update_schema() as schema_update:
            for field in incoming_schema.fields:
                if field.name not in existing_names:
                    schema_update.add_column(field.name, field.field_type)
        tbl = cat.load_table(tid)
    target_types: dict[str, str] = {}
    for field in tbl.schema().fields:
        field_type = str(field.field_type).lower()
        if "double" in field_type or "float" in field_type or "decimal" in field_type:
            target_types[field.name] = "DOUBLE"
        elif "long" in field_type or "int" in field_type:
            target_types[field.name] = "BIGINT"
        elif "bool" in field_type:
            target_types[field.name] = "BOOLEAN"
        else:
            target_types[field.name] = "TEXT"
    tbl.append(_df_to_arrow(frame, column_types=target_types, target_schema=tbl.schema()))
    return len(frame)


def write_azure_ptu_capacity(df: pd.DataFrame) -> int:
    tid = _table_id("gold", "dest_azure_ptu_capacity")
    cat = _get_catalog()
    try:
        table = cat.load_table(tid)
        old_identity_partition = any(
            field.name == "deployment_resource_id" for field in table.spec().fields
        )
        if old_identity_partition:
            if table.current_snapshot() is None:
                # The former layout never wrote a data file: its identity
                # partition path exceeded macOS' file-name limit.
                cat.drop_table(tid)
            else:
                logger.warning(
                    "Skipping Azure PTU capacity append: existing table uses the unsafe deployment_resource_id partition"
                )
                return 0
    except Exception:
        pass
    return _append_or_create_table("gold", "dest_azure_ptu_capacity", df, "_billing_period")


def write_azure_ptu_usage_events(df: pd.DataFrame) -> int:
    return _append_or_create_table("bronze", "raw_azure_ptu_usage_events", df, "_billing_period")


def write_azure_ptu_subproject_usage(df: pd.DataFrame) -> int:
    return _append_or_create_table("gold", "dest_azure_ptu_subproject_usage", df, "_billing_period")


def write_bronze_catalog(
    table_name: str,
    df: pd.DataFrame,
    collection_period: str,
    *,
    column_types: Optional[dict[str, str]] = None,
) -> int:
    """Append benefit catalog/utilization rows to a dedicated Bronze table.

    Reservations/Savings Plans são coletados via ARM e gravados no Bronze sem
    normalização FOCUS (por ora). A tabela é particionada por
    ``_billing_period`` (mês de coleta, ex. 202602) e evolui o schema por nome
    quando novas colunas chegam — mesmo contrato do write_bronze.

    O catálogo é um snapshot completo do estado dos benefícios: para a mesma
    conexão + mês de coleta, a fatia anterior é substituída (delete + append)
    para que cada coleta reflita o estado atual e não acumule duplicatas.
    """
    if df is None or df.empty:
        return 0
    from pyiceberg.expressions import And, EqualTo
    from pyiceberg.io.pyarrow import pyarrow_to_schema

    _ensure_namespace("bronze")
    tid = _table_id("bronze", table_name)
    frame = df.copy()
    if "_billing_period" not in frame.columns:
        frame["_billing_period"] = str(collection_period)
    if "_ingested_at" not in frame.columns:
        frame["_ingested_at"] = pd.Timestamp.utcnow().isoformat()

    cat = _get_catalog()
    try:
        tbl = cat.load_table(tid)
    except Exception:
        return _append_or_create_table("bronze", table_name, frame, "_billing_period")

    # Remove snapshot anterior da mesma conexão + mês (idempotência de catálogo).
    try:
        conn_ids = sorted({int(x) for x in frame["connection_id"].dropna().unique()})
        for conn_id in conn_ids:
            tbl.delete(And(
                EqualTo("_billing_period", str(collection_period)),
                EqualTo("connection_id", conn_id),
            ))
    except Exception as exc:
        logger.warning("Bronze %s delete-slice falhou (seguindo com append): %s", table_name, exc)
        tbl = cat.load_table(tid)

    incoming = _df_to_arrow(frame, column_types=column_types)
    incoming_schema = pyarrow_to_schema(incoming.schema)
    existing_names = {field.name for field in tbl.schema().fields}
    incoming_names = {field.name for field in incoming_schema.fields}
    if not incoming_names.issubset(existing_names):
        with tbl.update_schema() as schema_update:
            for field in incoming_schema.fields:
                if field.name not in existing_names:
                    schema_update.add_column(field.name, field.field_type)
        tbl = cat.load_table(tid)

    target_types: dict[str, str] = {}
    for field in tbl.schema().fields:
        field_type = str(field.field_type).lower()
        if "double" in field_type or "float" in field_type or "decimal" in field_type:
            target_types[field.name] = "DOUBLE"
        elif "long" in field_type or "int" in field_type:
            target_types[field.name] = "BIGINT"
        elif "bool" in field_type:
            target_types[field.name] = "BOOLEAN"
        else:
            target_types[field.name] = "TEXT"

    periods = sorted({str(x) for x in frame["_billing_period"].dropna().unique()})
    for period in periods:
        subset = frame[frame["_billing_period"].astype(str) == period]
        if subset.empty:
            continue
        tbl.append(_df_to_arrow(subset, column_types=target_types, target_schema=tbl.schema()))
    logger.info("Bronze %s: appended %d rows (collection %s)", table_name, len(frame), collection_period)
    return len(frame)
