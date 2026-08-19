"""
Iceberg Service — Data Lakehouse layer atop MinIO + Nessie + PyIceberg.

Layers:
  Bronze  (raw)       → bronze.raw_{connection_code}
  Silver  (mapped)    → silver.{source}_{destination_model}
  Gold    (analytics) → gold.{destination_table}

Fallback: if Nessie/MinIO are unavailable, uses SQL catalog (PostgreSQL) +
local filesystem warehouse (./data/iceberg/).
"""

from __future__ import annotations

import logging
import os
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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Catalog configuration
# ---------------------------------------------------------------------------

# Primary: Nessie + MinIO S3 (Docker)
_NESSIE_CONFIG = {
    "type": "nessie",
    "uri": os.getenv("NESSIE_URI", "http://localhost:19120/api/v1"),
    "warehouse": os.getenv("ICEBERG_WAREHOUSE", "s3://warehouse/"),
    "s3.endpoint": os.getenv("S3_ENDPOINT", "http://localhost:9000"),
    "s3.access-key-id": os.getenv("AWS_ACCESS_KEY_ID", "minioadmin"),
    "s3.secret-access-key": os.getenv("AWS_SECRET_ACCESS_KEY", "minioadmin"),
    "s3.path-style-access": "true",
    "s3.region": "us-east-1",
}

# Fallback: SQL catalog (PostgreSQL) + local filesystem (no Docker needed)
_LOCAL_WAREHOUSE = Path(os.getenv("ICEBERG_LOCAL_WAREHOUSE", os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "data", "iceberg",
)))
_LOCAL_WAREHOUSE.mkdir(parents=True, exist_ok=True)

_catalog: Optional[Catalog] = None
_catalog_type: str = ""
_initialized_namespaces: set[str] = set()


def _try_nessie() -> Optional[Catalog]:
    """Attempt to connect to Nessie + MinIO. Returns None on failure."""
    try:
        cat = load_catalog("nessie", **_NESSIE_CONFIG)
        # Quick connectivity test
        cat.list_namespaces()
        return cat
    except Exception as e:
        logger.info("Nessie/MinIO unavailable (%s) — falling back to local SQL catalog", e)
        return None


def _create_sql_catalog() -> Catalog:
    """Create a SQL catalog using PostgreSQL + local filesystem warehouse."""
    from app.config import settings
    warehouse_uri = f"file://{_LOCAL_WAREHOUSE.resolve()}"
    logger.info("Using SQL catalog (PostgreSQL) with warehouse at %s", warehouse_uri)
    return load_catalog(
        "sql",
        uri=settings.database_url_sync,           # PostgreSQL for catalog metadata
        warehouse=warehouse_uri,                   # Local filesystem for data files
        **{"s3.endpoint": ""},                     # disable S3 config
    )


def _get_catalog() -> Catalog:
    """Lazy-load the Iceberg catalog.

    Local Trino reads the PostgreSQL JDBC catalog + filesystem warehouse.
    Prefer that unless ICEBERG_CATALOG_TYPE=nessie, otherwise writes disappear
    from Trino while a stale SQL table (like raw_oci_prod) stays unreadable.
    """
    global _catalog, _catalog_type
    if _catalog is not None:
        return _catalog

    requested = os.getenv("ICEBERG_CATALOG_TYPE", "sql").lower()
    if requested == "nessie":
        _catalog = _try_nessie()
        if _catalog is None:
            _catalog = _create_sql_catalog()
            _catalog_type = "sql"
        else:
            _catalog_type = "nessie"
            logger.info("Connected to Nessie catalog")
        return _catalog

    _catalog = _create_sql_catalog()
    _catalog_type = "sql"
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
    folded = pd.DataFrame(index=df.index)
    for names in groups.values():
        keep = names[0]
        series = df[keep]
        for extra in names[1:]:
            extra_series = df[extra]
            blank = series.isna() | (series.astype(str).str.strip() == "") | (series.astype(str) == "nan")
            series = series.where(~blank, extra_series)
        folded[keep] = series
    return folded


def _schema_has_case_collisions(schema: Any) -> bool:
    names = [str(field.name) for field in schema.fields]
    return len(names) != len({name.lower() for name in names})


def _bronze_table_id(connection_code: str) -> tuple[str, str]:
    safe = connection_code.replace("-", "_").replace(".", "_")
    return ("bronze", f"raw_{safe}")


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
            if df[col].dtype == bool:
                pass
            elif df[col].dtype == object:
                # Map common boolean string representations
                bool_map = {
                    "true": True, "false": False, "1": True, "0": False,
                    "yes": True, "no": False, "sim": True, "não": False,
                    True: True, False: False, 1: True, 0: False,
                }
                df[col] = df[col].map(bool_map)
                # Unmapped values become None
                non_bool = ~df[col].isin([True, False])
                df.loc[non_bool, col] = None

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

    # ── Phase 3: Convert to PyArrow ──
    arrow_table = pa_lib.Table.from_pandas(df, preserve_index=False)

    # Fix columns that ended up as null type (all None) — convert to string
    schema_changed = False
    new_fields = []
    for i in range(len(arrow_table.schema)):
        field = arrow_table.schema.field(i)
        if pa_lib.types.is_null(field.type):
            new_fields.append(pa_lib.field(field.name, pa_lib.string(), nullable=True))
            schema_changed = True
        else:
            new_fields.append(field)

    if schema_changed:
        new_schema = pa_lib.schema(new_fields)
        cols = [arrow_table.column(j).cast(new_schema.field(j).type) for j in range(len(new_fields))]
        arrow_table = pa_lib.table(dict(zip([f.name for f in new_fields], cols)))

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
    from pyiceberg.expressions import AlwaysTrue, And, EqualTo

    try:
        table = _get_catalog().load_table(table_id)
    except Exception:
        return

    row_filter = AlwaysTrue()
    if blob_path:
        row_filter = And(row_filter, EqualTo("_blob_path", blob_path))
    if billing_period:
        row_filter = And(row_filter, EqualTo("_billing_period", str(billing_period)))
    table.delete(row_filter)
    logger.info(
        "Deleted Bronze slice from %s.%s (blob_path=%s, billing_period=%s)",
        table_id[0], table_id[1], blob_path, billing_period,
    )


def read_bronze(connection_code: str, blob_path: str | None = None,
                billing_period: str | None = None,
                max_rows: int | None = None) -> pd.DataFrame:
    """Read a filtered immutable Bronze snapshot for reprocessing.

    Filtering must happen in Iceberg, before conversion to pandas.  Reading
    the whole Bronze table and filtering afterwards makes each blob batch
    deserialize millions of unrelated rows and appears to freeze the worker.
    """
    table_id = _bronze_table_id(connection_code)
    table_name = f"{table_id[0]}.{table_id[1]}"
    table = _get_catalog().load_table(table_id)
    from pyiceberg.expressions import AlwaysTrue, And, EqualTo

    row_filter = AlwaysTrue()
    if blob_path:
        row_filter = And(row_filter, EqualTo("_blob_path", blob_path))
    if billing_period:
        row_filter = And(row_filter, EqualTo("_billing_period", str(billing_period)))

    scan_kwargs = {"row_filter": row_filter}
    if max_rows is not None and max_rows >= 0:
        scan_kwargs["limit"] = max_rows
    return table.scan(**scan_kwargs).to_pandas().reset_index(drop=True)


def read_bronze_batches(connection_code: str, blob_path: str | None = None,
                        billing_period: str | None = None,
                        max_rows: int | None = None,
                        batch_size: int = 25_000):
    """Yield Bronze batches without materializing the complete blob in RAM."""
    table_id = _bronze_table_id(connection_code)
    table_name = f"{table_id[0]}.{table_id[1]}"
    table = _get_catalog().load_table(table_id)
    from pyiceberg.expressions import AlwaysTrue, And, EqualTo

    row_filter = AlwaysTrue()
    if blob_path:
        row_filter = And(row_filter, EqualTo("_blob_path", blob_path))
    if billing_period:
        row_filter = And(row_filter, EqualTo("_billing_period", str(billing_period)))

    scan = table.scan(row_filter=row_filter, limit=max_rows if max_rows is not None and max_rows >= 0 else None)
    reader = scan.to_arrow_batch_reader()
    emitted = 0
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
                yield piece.to_pandas().reset_index(drop=True)
                emitted += piece.num_rows
            offset += piece.num_rows


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

    arrow_table = _df_to_arrow(df, column_types=column_types)
    cat = _get_catalog()
    rows = len(df)

    try:
        tbl = cat.load_table(table_name)
        tbl.append(arrow_table)
        logger.info("Appended %d rows to %s", rows, table_name)
        return rows
    except Exception:
        pass

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
)


def write_gold(df: pd.DataFrame) -> int:
    """Write a fact DataFrame to the Gold layer. (legacy — kept for backward compat)"""
    return write_destination("fact_cost", df, partition_col="billing_period")


# ---------------------------------------------------------------------------
# Destination tables — dynamic Iceberg tables in the gold namespace
# ---------------------------------------------------------------------------
def read_destination(physical_name: str, namespace: str = "gold") -> pd.DataFrame:
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


def read_destination_batches(physical_name: str, excluded_periods: set[str] | None = None,
                             excluded_connection_ids: set[int] | None = None,
                             batch_size: int = 25_000, namespace: str = "gold"):
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
            yield arrow_batch.to_pandas().reset_index(drop=True)

    try:
        table = cat.load_table(tid)
        return table.scan().to_pandas()
    except Exception as e:
        logger.warning(f"Failed to read gold.{safe_name}: {e} — returning empty DataFrame")
        return pd.DataFrame()


def write_destination_staged(
    physical_name: str,
    df: pd.DataFrame,
    *,
    partition_col: str = "_billing_period",
    column_types: Optional[dict[str, str]] = None,
    namespace: str = "gold",
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
    if "tipo" not in df.columns:
        df["tipo"] = "realizado"
    if partition_col not in df.columns:
        df[partition_col] = "unknown"

    arrow_table = _df_to_arrow(df, column_types=column_types)
    cat = _get_catalog()

    from pyiceberg.io.pyarrow import pyarrow_to_schema
    iceberg_schema = pyarrow_to_schema(arrow_table.schema)

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


def swap_staged_table(physical_name: str, temp_safe_name: str, namespace: str = "gold",
                      known_row_count: int | None = None) -> int:
    """Atomically swap staged temp table → main table.

    1. Drop original gold.{physical_name} table via catalog (if exists)
    2. Rename temp table → main table via catalog.rename_table (atomically handles
       metadata + file paths correctly, unlike manual SQL/fs manipulation)
    3. Load and count rows

    Returns: number of rows in the new table, or -1 if counting fails.
    """
    safe_name = physical_name.replace("-", "_").replace(".", "_")
    main_tid = _table_id(namespace, safe_name)
    temp_tid = _table_id(namespace, temp_safe_name)
    cat = _get_catalog()

    # A temporary compatibility view can remain after a pipeline migration.
    # It is safe to remove only here, after the replacement staging table is
    # complete and validated by the caller.
    if namespace == "gold":
        try:
            from app.services.trino_service import execute_sync
            execute_sync(f'DROP VIEW IF EXISTS iceberg.gold."{safe_name}"', timeout_s=30)
        except Exception as exc:
            logger.info("Gold object is not a Trino view (or Trino unavailable): %s", exc)

    # 1. Drop original main table via catalog
    _force_delete_table(main_tid, safe_name)

    # 2. Rename temp → main (PyIceberg handles metadata + file paths correctly)
    try:
        cat.rename_table(temp_tid, main_tid)
        logger.info("Renamed gold.%s → gold.%s via catalog", temp_safe_name, safe_name)
    except Exception as e:
        logger.error("Catalog rename_table failed: %s", e)
        raise

    # 3. Never re-read a multi-million-row table just to obtain a number we
    # already know from the writer.  The former scan().to_arrow() was an
    # unnecessary full materialization after every successful publication.
    if known_row_count is not None:
        logger.info("Swapped %s → %s.%s (%d rows supplied by writer)", temp_safe_name, namespace, safe_name, known_row_count)
        return int(known_row_count)

    # Backward-compatible fallback for callers that genuinely do not know it.
    try:
        tbl = cat.load_table(main_tid)
        rows = len(tbl.scan().to_arrow())
        logger.info("Swapped %s → gold.%s (%d rows)", temp_safe_name, safe_name, rows)
        return rows
    except Exception as e:
        logger.warning("Load after swap failed (table may still be accessible): %s", e)
        return -1


def cleanup_staged_table(temp_safe_name: str, namespace: str = "gold") -> None:
    """Remove a staged temp table (rollback on failure)."""
    try:
        tid = _table_id(namespace, temp_safe_name)
        _force_delete_table(tid, temp_safe_name)
        logger.info("Cleaned up staged table gold.%s", temp_safe_name)
    except Exception as e:
        logger.warning("Cleanup staged table failed: %s", e)


def publish_staged_partition(
    physical_name: str,
    temp_safe_name: str,
    *,
    billing_period: str,
    connection_id: int,
    namespace: str = "gold",
) -> int:
    """Atomically replace one source/month slice with files from staging.

    The staging table is built and validated before this function is called.
    Its Parquet files are adopted in the same Iceberg transaction that removes
    the prior ``(_billing_period, source_connection_id)`` slice, so readers
    see either the prior month/source or the new one, never a partial batch.
    """
    from pyiceberg.expressions import And, EqualTo

    safe_name = physical_name.replace("-", "_").replace(".", "_")
    cat = _get_catalog()
    target_id = _table_id(namespace, safe_name)
    staging_id = _table_id(namespace, temp_safe_name)
    staged = cat.load_table(staging_id)
    staged_tasks = list(staged.scan().plan_files())
    row_count = sum(task.file.record_count for task in staged_tasks)

    try:
        target = cat.load_table(target_id)
    except Exception as exc:
        from pyiceberg.exceptions import NoSuchTableError
        if not isinstance(exc, NoSuchTableError):
            raise
        # First published slice: keeping the staging table itself is the
        # cheapest atomic publication and preserves its partition spec.
        cat.rename_table(staging_id, target_id)
        return int(row_count)

    # A partial reprocess publishes one connection/month at a time.  The
    # staging table can legitimately contain newly materialized organizational
    # dimensions (for example departamento_nivel_6 and _7) while the existing
    # Gold table still has the prior schema.  Evolve the target by name before
    # appending Arrow batches; otherwise PyArrow rejects the new fields and the
    # atomic publication fails after all processing has completed.
    with target.update_schema() as schema_update:
        schema_update.union_by_name(staged.schema())
    target = cat.load_table(target_id)

    overwrite_filter = And(
        EqualTo("_billing_period", str(billing_period)),
        EqualTo("source_connection_id", int(connection_id)),
    )
    tx = target.transaction()
    tx.delete(overwrite_filter)
    # ``add_files`` only accepts external Parquet without Iceberg field IDs.
    # Staging is produced by PyIceberg and therefore contains those IDs; write
    # each bounded Arrow batch natively into this transaction instead.  This
    # keeps publication atomic without materializing a whole month in memory.
    for batch in staged.scan().to_arrow_batch_reader():
        if batch.num_rows:
            import pyarrow as pa
            tx.append(pa.Table.from_batches([batch]))
    tx.commit_transaction()
    # The native write above created new target files, so staging can be safely
    # removed with all of its now-unreferenced files.
    _force_delete_table(staging_id, temp_safe_name)
    logger.info("Published %d rows for %s/%s into %s.%s", row_count, connection_id, billing_period, namespace, safe_name)
    return int(row_count)


def append_to_staged(
    temp_safe_name: str,
    df: pd.DataFrame,
    *,
    partition_col: str = "_billing_period",
    column_types: Optional[dict[str, str]] = None,
    namespace: str = "gold",
) -> None:
    """Append data to an existing staged temp table."""
    if df.empty:
        return

    df = df.copy()
    if partition_col not in df.columns:
        df[partition_col] = "unknown"

    arrow_table = _df_to_arrow(df, column_types=column_types)
    cat = _get_catalog()
    tid = _table_id(namespace, temp_safe_name)

    try:
        tbl = cat.load_table(tid)
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
) -> int:
    """Write a destination DataFrame to an Iceberg table (append-or-create).

    For incremental (per-chunk) ETL writes. Uses tbl.append() when the table
    exists and creates a fresh table when it doesn't.

    For full reprocess with drop-and-replace, use write_destination_staged +
    swap_staged_table instead.

    Args:
        column_types: Optional dict column_name → data_type for type coercion.
    """
    if df.empty:
        return 0

    _ensure_namespace("gold")
    safe_name = physical_name.replace("-", "_").replace(".", "_")
    tid = _table_id("gold", safe_name)

    df = df.copy()
    if partition_col not in df.columns:
        df[partition_col] = "unknown"

    arrow_table = _df_to_arrow(df, column_types=column_types)
    cat = _get_catalog()
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
    # 1. Try catalog-level drop first (handles files + metadata atomically)
    try:
        cat = _get_catalog()
        cat.drop_table(tid)
        logger.info("Dropped table %s via catalog", tid)
        return
    except Exception:
        pass  # Table may not exist in catalog, fall through to manual cleanup

    # 2. Clean SQL catalog metadata
    try:
        from app.database import SyncSession
        from sqlalchemy import text
        pg = SyncSession()
        pg.execute(text("DELETE FROM iceberg_tables WHERE table_name = :name"), {"name": safe_name})
        pg.commit()
        pg.close()
    except Exception:
        pass

    # 3. Clean local warehouse files (Parquet, Avro, metadata)
    #    PyIceberg SQL catalog uses {namespace}.db/{table_name}/ directory layout.
    #    Match EXACT directory name to avoid deleting staged tables
    #    (e.g. "dest_modelo_focus" only, NOT "dest_modelo_focus_stg_123").
    warehouse = _LOCAL_WAREHOUSE
    for namespace_pattern in ["gold", "gold.db"]:
        base = str(warehouse / namespace_pattern)
        candidate = os.path.join(base, safe_name)
        if os.path.isdir(candidate):
            shutil.rmtree(candidate, ignore_errors=True)
            logger.info("Deleted warehouse dir: %s", candidate)


def get_catalog() -> Catalog:
    """Expose the catalog for external use (e.g., health checks)."""
    return _get_catalog()


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


def write_forecast_planning(physical_name: str, df: pd.DataFrame, value_column: str | None = None) -> str:
    """Publish the compact monthly planning table in Gold.

    This is intentionally separate from the detailed Gold fact table. It only
    contains configured series dimensions, month, type and forecast measures.
    ``value_column`` names the forecast measure (e.g. the configured measure
    column) in the published table; when empty it falls back to ``value``.
    """
    if df.empty:
        return f"{physical_name}_planning"
    _ensure_namespace("gold")
    safe_name = physical_name.replace("-", "_").replace(".", "_") + "_planning"
    keep = [c for c in df.columns if c not in {"selected_method", "fallback_level", "history_points", "backtest_wape"}]
    planning = df[keep].copy()
    measure_name = value_column or "value"
    planning = planning.rename(columns={"forecast_month": "period", "forecast_value": measure_name})
    arrow_table = _df_to_arrow(planning, column_types={measure_name: "DOUBLE"})
    tid = ("gold", safe_name)
    cat = _get_catalog()
    try:
        cat.drop_table(tid)
    except Exception:
        pass
    from pyiceberg.io.pyarrow import pyarrow_to_schema
    table = cat.create_table(tid, schema=pyarrow_to_schema(arrow_table.schema))
    table.append(arrow_table)
    logger.info("Published %d planning rows to gold.%s", len(planning), safe_name)
    return safe_name
