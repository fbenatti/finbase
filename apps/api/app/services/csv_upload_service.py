"""Manual CSV upload service for Bronze ingestion.

Allows users to upload a local CSV file, preview its columns, adjust inferred
data types, and load it into the Bronze layer as a monthly snapshot. The
resulting table follows the same Bronze contract as Azure/GCP extractions:
``bronze.raw_{connection_code}``, partitioned by ``_billing_period``.
"""

from __future__ import annotations

import io
import logging
import re
import warnings
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.models.ingested_blob import IngestedBlob
from app.models.source_connection import SourceConnection
from app.services import iceberg_service as iceberg
from app.services.azure_extractor import AzureExtractor

logger = logging.getLogger(__name__)

# Ordered by likelihood for Brazilian/Portuguese exports.
_CSV_CANDIDATES = [
    (";", ",", "."),   # semicolon sep, comma decimal, dot thousands
    (",", ".", ","),   # standard US
    (",", ".", None),   # standard US without thousands
    (";", ".", None),   # semicolon without thousands
    ("\t", ".", None),  # TSV
]

_ALLOWED_TYPES = {"TEXT", "INTEGER", "DOUBLE", "BOOLEAN", "DATE"}


def _try_read_csv(
    raw_bytes: bytes,
    enc: str,
    sep: str,
    dec: str,
    thou: str | None,
    skiprows: list[int] | int | None = None,
) -> pd.DataFrame | None:
    try:
        kwargs: dict[str, Any] = {
            "encoding": enc,
            "sep": sep,
            "low_memory": False,
            "on_bad_lines": "skip",
        }
        if dec:
            kwargs["decimal"] = dec
        if thou:
            kwargs["thousands"] = thou
        if skiprows is not None:
            kwargs["skiprows"] = skiprows
        return pd.read_csv(io.BytesIO(raw_bytes), **kwargs)
    except (UnicodeDecodeError, UnicodeError, pd.errors.ParserError, ValueError):
        return None


def _find_header_row(raw_bytes: bytes, enc: str, sep: str) -> int | None:
    """Find the first data row that looks like a FOCUS/CSV header.

    Some providers (e.g. IBM Cloud) prefix the real data with metadata rows.
    We scan the first lines and return the index of the first line whose number
    of fields matches the maximum field count seen in the file.
    """
    try:
        text = raw_bytes.decode(enc)
    except (UnicodeDecodeError, UnicodeError):
        return None

    lines = text.splitlines()
    if not lines:
        return None

    counts = []
    quoted = False
    for line in lines:
        # Very fast CSV field count respecting quoted commas.
        n_fields = 1
        for ch in line:
            if ch == '"':
                quoted = not quoted
            elif ch == sep and not quoted:
                n_fields += 1
        counts.append(n_fields)

    if not counts:
        return None

    max_fields = max(counts)
    # Header must be within the first 10 rows and have the max number of fields.
    for idx, count in enumerate(counts[:10]):
        if count == max_fields and max_fields >= 2:
            return idx
    return None


def parse_csv_bytes(raw_bytes: bytes) -> pd.DataFrame:
    """Parse raw CSV bytes with robust encoding/separator/header detection."""
    encodings = ["utf-8-sig", "utf-8", "utf-16", "latin-1"]

    best_df: pd.DataFrame | None = None
    best_cols = 0
    best_skiprows: list[int] | int | None = None

    for enc in encodings:
        for sep, dec, thou in _CSV_CANDIDATES:
            # Try without skipping rows first.
            df = _try_read_csv(raw_bytes, enc, sep, dec, thou)
            if df is not None and len(df.columns) > best_cols:
                best_df = df
                best_cols = len(df.columns)
                best_skiprows = None

            # Try skipping leading metadata rows (common for IBM FOCUS exports).
            header_idx = _find_header_row(raw_bytes, enc, sep)
            if header_idx is not None and header_idx > 0:
                skip = list(range(header_idx))
                df = _try_read_csv(raw_bytes, enc, sep, dec, thou, skiprows=skip)
                if df is not None and len(df.columns) > best_cols:
                    best_df = df
                    best_cols = len(df.columns)
                    best_skiprows = skip

    if best_df is None:
        raise ValueError("Failed to decode CSV with any known encoding/separator")

    df = best_df

    if len(df.columns) < 2:
        raise ValueError("CSV was parsed as a single column; delimiter detection failed")

    if df.columns.duplicated().any():
        duplicates = df.columns[df.columns.duplicated()].tolist()
        raise ValueError(f"CSV contains duplicate columns: {duplicates}")

    # Strip BOM from column names if present.
    df.columns = [str(c).lstrip("\ufeff") for c in df.columns]

    # Drop fully empty rows that sometimes appear after metadata sections.
    df = df.dropna(how="all").reset_index(drop=True)

    return df


def _is_integer_series(series: pd.Series) -> bool:
    try:
        converted = pd.to_numeric(series, errors="coerce")
        # Allow NaN, but non-NaN values must be whole numbers within int64 range.
        finite = converted.dropna()
        if len(finite) == 0:
            return False
        return bool((finite == finite.astype("int64")).all() and finite.abs().max() <= 9_223_372_036_854_775_807)
    except Exception:
        return False


def _is_date_series(series: pd.Series) -> bool:
    if series.dtype.kind == "M":
        return True
    try:
        # If the majority of values parse as numbers, prefer numeric types over
        # date. This avoids mis-classifying numeric columns such as Cost/Quantity
        # as dates just because they can be coerced.
        numeric = pd.to_numeric(series, errors="coerce")
        if numeric.notna().sum() / max(len(series), 1) >= 0.5:
            return False

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            converted = pd.to_datetime(series, errors="coerce", dayfirst=True)
        non_null = converted.notna().sum()
        total = len(series)
        return total > 0 and non_null / total >= 0.5
    except Exception:
        return False


def _is_boolean_series(series: pd.Series) -> bool:
    if series.dtype == bool:
        return True
    if series.dtype == object:
        unique = set(series.dropna().astype(str).str.lower().unique())
        return unique.issubset({"true", "false", "1", "0", "yes", "no", "sim", "não", "nao"})
    return False


def _is_float_series(series: pd.Series) -> bool:
    try:
        converted = pd.to_numeric(series, errors="coerce")
        return converted.notna().sum() > 0
    except Exception:
        return False


def infer_column_types(df: pd.DataFrame) -> dict[str, str]:
    """Infer a target type for each column.

    Types follow the Bronze type coercion vocabulary used by ``_df_to_arrow``:
    TEXT, INTEGER, DOUBLE, BOOLEAN. DATE is kept as TEXT in Bronze (source
    snapshot) but exposed to the UI so the user can reason about semantics.
    """
    types: dict[str, str] = {}
    for col in df.columns:
        series = df[col]
        if _is_boolean_series(series):
            types[col] = "BOOLEAN"
        elif _is_date_series(series):
            types[col] = "DATE"
        elif _is_integer_series(series):
            types[col] = "INTEGER"
        elif _is_float_series(series):
            types[col] = "DOUBLE"
        else:
            types[col] = "TEXT"
    return types


def _serialize_value(val: Any) -> Any:
    """Convert pandas/numpy scalars to plain Python JSON-serializable values."""
    if val is None or pd.isna(val):
        return None
    if isinstance(val, (pd.Timestamp, datetime)):
        return val.isoformat()
    if isinstance(val, np.integer):
        return int(val)
    if isinstance(val, np.floating):
        if np.isnan(val):
            return None
        return float(val)
    if isinstance(val, np.bool_):
        return bool(val)
    return val


def build_preview(df: pd.DataFrame, max_rows: int = 10) -> dict[str, Any]:
    """Build a JSON-serializable preview payload.

    Returns columns with inferred type, sample values, null count, and a few
    data rows so the UI can render the grid before ingestion.
    """
    inferred = infer_column_types(df)
    head = df.head(max_rows)
    records = []
    for _, row in head.iterrows():
        record: dict[str, Any] = {col: _serialize_value(row[col]) for col in df.columns}
        records.append(record)

    columns = []
    for col in df.columns:
        sample = df[col].dropna().head(5).tolist()
        sample_serializable = [_serialize_value(v) for v in sample]
        columns.append({
            "name": col,
            "inferred_type": inferred.get(col, "TEXT"),
            "sample_values": sample_serializable,
            "null_count": int(df[col].isna().sum()),
            "total_count": len(df),
        })

    return {
        "row_count": len(df),
        "column_count": len(df.columns),
        "columns": columns,
        "sample_rows": records,
    }


def _sanitize_filename(name: str) -> str:
    """Turn an original filename into a safe blob_path component."""
    base = re.sub(r"\.csv$", "", name, flags=re.IGNORECASE)
    safe = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", base)
    return safe or "upload"


def _derive_billing_period(filename: str, df: pd.DataFrame) -> str:
    """Derive YYYYMM from filename or DataFrame columns."""
    m = re.search(r"(\d{6})", filename)
    if m:
        return m.group(1)
    for col in ["BillingPeriod", "BillingCurrency", "Date", "billing_period"]:
        if col in df.columns and not df[col].dropna().empty:
            val = str(df[col].dropna().iloc[0])
            if col in {"Date", "billing_period"}:
                return val[:7].replace("-", "")
            return val[:6]
    now = datetime.now()
    return f"{now.year}{now.month:02d}"


def load_csv_to_bronze(
    session: Session,
    connection: SourceConnection,
    df: pd.DataFrame,
    filename: str,
    billing_period: str | None,
    column_types: dict[str, str] | None,
) -> dict[str, Any]:
    """Write a manually-uploaded CSV into Bronze and register metadata.

    Args:
        session: synchronous SQLAlchemy session.
        connection: the source_connection row (manual_csv connector).
        df: parsed DataFrame.
        filename: original uploaded filename.
        billing_period: explicit YYYYMM; inferred from filename/data if omitted.
        column_types: optional user-overridden types keyed by column name.

    Returns:
        dict with row_count, billing_period, blob_path, table_name.
    """
    if df.empty:
        raise ValueError("CSV está vazio")

    period = billing_period or _derive_billing_period(filename, df)
    if not re.fullmatch(r"\d{6}", period):
        raise ValueError(f"Período de faturamento inválido: {period} (esperado YYYYMM)")

    blob_path = f"manual/{period}/{_sanitize_filename(filename)}.csv"

    # Bronze stores everything as nullable text by default. If the user asked
    # for specific types, we still write text to Bronze (immutable source
    # snapshot) but coerce the preview/suggested schema metadata. The override
    # is kept in the IngestedBlob metadata so downstream reprocess can use it.
    user_types = {k: v for k, v in (column_types or {}).items() if v in _ALLOWED_TYPES}

    # Remove legacy application-level columns if the user accidentally uploaded
    # an enriched report instead of a raw source.
    derived_markers = {"BusinessRule", "Projetos", "Diretoria"}
    if derived_markers.issubset(set(df.columns)):
        raise ValueError(
            "CSV parece ser um relatório enriquecido (BusinessRule/Projetos/Diretoria). "
            "Carregue apenas a fonte bruta."
        )

    rows = iceberg.write_bronze(
        connection_code=connection.code,
        df=df,
        billing_period=period,
        blob_path=blob_path,
    )

    if rows == 0:
        raise ValueError("Nenhuma linha foi escrita no Bronze")

    # Register ingestion metadata.
    existing = session.query(IngestedBlob).filter_by(
        connection_id=connection.id,
        blob_path=blob_path,
    ).first()
    if existing:
        session.delete(existing)
        session.flush()

    ingested = IngestedBlob(
        connection_id=connection.id,
        blob_path=blob_path,
        blob_prefix="manual",
        billing_period=period,
        last_modified=datetime.now(timezone.utc),
        etag="",
        content_length=0,
        row_count=rows,
        status="INGESTED",
    )
    session.add(ingested)
    session.flush()

    # Persist type overrides in the connection config so future uploads of the
    # same source can reuse them. We avoid altering the source_connection schema.
    config = dict(connection.config_json or {})
    manual_meta = dict(config.get("manual_csv_metadata", {}))
    manual_meta[period] = {
        "filename": filename,
        "blob_path": blob_path,
        "row_count": rows,
        "column_types": user_types,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }
    config["manual_csv_metadata"] = manual_meta
    connection.config_json = config

    return {
        "row_count": rows,
        "billing_period": period,
        "blob_path": blob_path,
        "table_name": f"bronze.raw_{connection.code}",
        "column_types": user_types,
    }
