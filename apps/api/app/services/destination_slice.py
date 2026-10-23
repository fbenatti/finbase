"""Silver destination slice keys (replace) vs Iceberg partition spec (pruning)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Optional

import pandas as pd

Grain = Literal["billing_period", "snapshot_date", "metric_day"]

_SNAPSHOT_PHYSICAL_DEFAULTS = frozenset({
    "dest_cloud_resource_inventory",
    "dest_cloud_orphan_resources",
    "dest_licencas_ia",
})
# No table defaults to a metric_day slice today; a destination can still opt
# in through partition_spec_json.
_METRIC_DAY_PHYSICAL_DEFAULTS: frozenset[str] = frozenset()

_MAX_EXTRA_PARTITIONS = 2
# Sentinel for null/empty values when a column is used as an Iceberg identity partition.
_PARTITION_UNSET = "__UNSET__"
_BUCKET_CARDINALITY_COLUMNS = frozenset({
    "subaccountname",
    "useremail",
    "resource_id",
    "resourceid",
})
# Display names are unsafe as Iceberg path segments (spaces, punctuation).
_UNSAFE_IDENTITY_PARTITION = frozenset({"subaccountname"})


@dataclass(frozen=True)
class PartitionExtra:
    column: str
    transform: Literal["identity", "bucket"] = "identity"
    bucket_count: int = 16


@dataclass(frozen=True)
class DestinationSliceConfig:
    grain: Grain
    snapshot_column: str = "snapshot_date"
    connection_column: str = "source_connection_id"
    extras: tuple[PartitionExtra, ...] = field(default_factory=tuple)

    @property
    def replace_columns(self) -> tuple[str, str]:
        if self.grain == "billing_period":
            return ("_billing_period", self.connection_column)
        if self.grain == "metric_day":
            return ("metric_day", self.connection_column)
        return (self.snapshot_column, self.connection_column)

    def partition_columns(self) -> list[str]:
        cols = list(self.replace_columns)
        for extra in self.extras:
            if extra.column not in cols:
                cols.append(extra.column)
        return cols

    def physical_iceberg_partition_columns(self, *, pyarrow_safe: bool = False) -> list[str]:
        """Columns that become Iceberg partition fields on write."""
        cols = list(self.replace_columns)
        for extra in self.extras:
            if extra.column in cols:
                continue
            if extra.column.lower() in _UNSAFE_IDENTITY_PARTITION:
                continue
            if pyarrow_safe and extra.transform == "bucket":
                continue
            cols.append(extra.column)
        return cols


def _normalise_physical_name(physical_name: str) -> str:
    return physical_name.replace("-", "_").replace(".", "_").strip().lower()


def snapshot_date_from_blob_path(blob_path: str) -> str | None:
    """Extract YYYY-MM-DD from Bronze blob paths (ARG, licenças IA, etc.)."""
    if not blob_path:
        return None
    path = str(blob_path).strip()
    # Licenças IA (GitHub/Claude/NotebookLM): blob_path = YYYYMM, uma fatia por mês.
    if re.fullmatch(r"\d{6}", path):
        return f"{path[:4]}-{path[4:6]}-01"
    match = re.search(
        r"(?:arg/(?:inventory|orphans/[^/]+)|licenses?|license)/(\d{4}-\d{2}-\d{2})",
        path,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1)
    match = re.search(r"/(\d{4}-\d{2}-\d{2})(?:\.[a-z0-9]+)?$", path, flags=re.IGNORECASE)
    return match.group(1) if match else None


def resolve_snapshot_column(
    partition_spec_json: dict[str, Any] | None,
    column_names: set[str] | None = None,
) -> str:
    if partition_spec_json:
        explicit = partition_spec_json.get("snapshot_column")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()
    names = {c.lower(): c for c in (column_names or ())}
    for candidate in ("snapshot_date", "snapshotdate"):
        if candidate in names:
            return names[candidate]
    return "snapshot_date"


def _parse_extras(
    raw: Any,
    *,
    column_names: set[str] | None,
) -> tuple[PartitionExtra, ...]:
    if not isinstance(raw, list):
        return ()
    extras: list[PartitionExtra] = []
    allowed = {c.lower(): c for c in (column_names or ())}
    for item in raw[:_MAX_EXTRA_PARTITIONS]:
        if not isinstance(item, dict):
            continue
        col = str(item.get("column") or "").strip()
        if not col:
            continue
        canonical = allowed.get(col.lower(), col)
        if column_names is not None and canonical not in column_names:
            continue
        transform = str(item.get("transform") or "identity").strip().lower()
        if transform not in ("identity", "bucket"):
            transform = "bucket" if canonical.lower() in _BUCKET_CARDINALITY_COLUMNS else "identity"
        bucket_count = int(item.get("bucket_count") or 16)
        if transform == "bucket":
            bucket_count = max(2, min(bucket_count, 128))
        extras.append(PartitionExtra(column=canonical, transform=transform, bucket_count=bucket_count))
    return tuple(extras)


def resolve_destination_slice_config_for_fields(
    physical_name: str,
    partition_spec_json: dict[str, Any] | None,
    field_names: set[str] | Iterable[str],
) -> DestinationSliceConfig:
    """Resolve slice keys using the physical column names on an Iceberg table."""
    return resolve_destination_slice_config(
        physical_name,
        partition_spec_json,
        column_names=set(field_names),
    )


def resolve_destination_slice_config(
    physical_name: str,
    partition_spec_json: dict[str, Any] | None = None,
    *,
    column_names: set[str] | None = None,
) -> DestinationSliceConfig:
    spec = dict(partition_spec_json or {})
    safe = _normalise_physical_name(physical_name)
    grain: Grain
    raw_grain = str(spec.get("grain") or "").strip().lower()
    if raw_grain in ("billing_period", "snapshot_date", "metric_day"):
        grain = raw_grain  # type: ignore[assignment]
    elif safe in _METRIC_DAY_PHYSICAL_DEFAULTS:
        grain = "metric_day"
    elif safe in _SNAPSHOT_PHYSICAL_DEFAULTS:
        grain = "snapshot_date"
    else:
        grain = "billing_period"

    snapshot_column = resolve_snapshot_column(spec if grain == "snapshot_date" else None, column_names)
    connection_column = str(spec.get("connection_column") or "source_connection_id").strip()
    if grain == "metric_day" and not spec.get("connection_column"):
        names = {str(name).lower(): str(name) for name in (column_names or ())}
        connection_column = names.get("_source_connection_id", "_source_connection_id")
    extras = _parse_extras(spec.get("extra_partitions"), column_names=column_names)
    return DestinationSliceConfig(
        grain=grain, snapshot_column=snapshot_column,
        connection_column=connection_column, extras=extras,
    )


def primary_partition_column(config: DestinationSliceConfig) -> str:
    return config.replace_columns[0]


def build_slice_row_filter(
    config: DestinationSliceConfig,
    *,
    connection_id: int,
    billing_period: str | None = None,
    snapshot_date: str | None = None,
    metric_day: str | None = None,
):
    from pyiceberg.expressions import And, EqualTo

    period_col, _conn_col = config.replace_columns
    clauses = [EqualTo(config.connection_column, int(connection_id))]
    if config.grain == "billing_period":
        if billing_period is None:
            raise ValueError("billing_period é obrigatório para publicação por mês")
        clauses.append(EqualTo(period_col, str(billing_period)))
    elif config.grain == "snapshot_date":
        if snapshot_date is None:
            raise ValueError("snapshot_date é obrigatório para publicação diária")
        clauses.append(EqualTo(period_col, str(snapshot_date)))
    else:
        if metric_day is None:
            raise ValueError("metric_day é obrigatório para publicação de métricas")
        clauses.append(EqualTo(period_col, str(metric_day)))
    if len(clauses) == 1:
        return clauses[0]
    return And(*clauses)


def sanitize_partition_source_values(
    df: pd.DataFrame,
    config: DestinationSliceConfig,
) -> pd.DataFrame:
    """Normalize null/blank values in columns that may be used as partition sources."""
    if df.empty:
        return df
    out = df.copy()
    for col in config.partition_columns():
        if col not in out.columns:
            continue
        series = out[col]
        if series.dtype == object or str(series.dtype).startswith("string"):
            blank = series.isna() | (series.astype(str).str.strip() == "")
            if blank.any():
                out.loc[blank, col] = _PARTITION_UNSET
    return out


def build_iceberg_partition_spec(
    iceberg_schema,
    config: DestinationSliceConfig,
    *,
    pyarrow_safe: bool = False,
):
    """Build Iceberg partition spec for destination tables.

    When ``pyarrow_safe=True``, bucket extras are skipped (PyArrow cannot write
    them) and ``SubAccountName`` is never a path segment. Identity extras such
    as ``SubAccountId`` are applied. Blank values become ``__UNSET__``.
    """
    from pyiceberg.partitioning import PartitionField, PartitionSpec
    from pyiceberg.transforms import BucketTransform, IdentityTransform

    fields: list[PartitionField] = []
    next_id = iceberg_schema.highest_field_id + 1000
    for col in config.physical_iceberg_partition_columns(pyarrow_safe=pyarrow_safe):
        part_field = iceberg_schema.find_field(col)
        if part_field is None:
            continue
        extra = next((e for e in config.extras if e.column == col), None)
        if extra and extra.transform == "bucket" and not pyarrow_safe:
            transform = BucketTransform(extra.bucket_count)
            name = f"{col}_bucket_{extra.bucket_count}"
        else:
            transform = IdentityTransform()
            name = col
        fields.append(
            PartitionField(
                source_id=part_field.field_id,
                field_id=next_id,
                transform=transform,
                name=name,
            )
        )
        next_id += 1
    if not fields:
        return PartitionSpec()
    return PartitionSpec(*fields)


def validate_staging_slice(
    df: pd.DataFrame,
    config: DestinationSliceConfig,
    *,
    connection_id: int,
    billing_period: str | None = None,
    snapshot_date: str | None = None,
    metric_day: str | None = None,
    expected_row_count: int | None = None,
) -> None:
    if df.empty:
        if expected_row_count not in (None, 0):
            raise ValueError(
                f"Staging vazio, mas o worker reportou {expected_row_count} linhas"
            )
        return

    connection_col = config.connection_column
    if connection_col not in df.columns:
        raise ValueError(f"Staging sem coluna {connection_col}")
    conn_series = pd.to_numeric(df[connection_col], errors="coerce")
    if (conn_series != int(connection_id)).any():
        raise ValueError(f"Staging mistura {connection_col} distintos da fatia")

    period_col = config.replace_columns[0]
    if period_col not in df.columns:
        raise ValueError(f"Staging sem coluna de fatia obrigatória: {period_col}")

    period_values = df[period_col].astype(str).dropna().unique()
    if len(period_values) != 1:
        raise ValueError(f"Staging com múltiplos valores em {period_col}: {period_values[:5]}")

    if config.grain == "billing_period":
        expected = str(billing_period) if billing_period is not None else None
    elif config.grain == "snapshot_date":
        expected = str(snapshot_date) if snapshot_date is not None else None
    else:
        expected = str(metric_day) if metric_day is not None else None
    if expected is not None and str(period_values[0]) != expected:
        raise ValueError(
            f"Valor de {period_col} no staging ({period_values[0]}) diverge da fatia ({expected})"
        )

    if expected_row_count is not None and len(df) != int(expected_row_count):
        raise ValueError(
            f"Contagem no staging ({len(df)}) diverge do worker ({expected_row_count})"
        )

    if config.grain in ("snapshot_date", "metric_day") and {"resource_id", period_col}.issubset(df.columns):
        dupes = df.duplicated(subset=["resource_id", period_col], keep=False)
        if dupes.any():
            n = int(dupes.sum())
            # Alerta operacional: não bloqueia publicação, mas deixa rastro no log.
            import logging

            logging.getLogger(__name__).warning(
                "Staging com %d linhas duplicadas na chave natural resource_id+%s",
                n,
                period_col,
            )


def validate_staged_table_slice(
    staged_table,
    config: DestinationSliceConfig,
    *,
    connection_id: int,
    billing_period: str | None = None,
    snapshot_date: str | None = None,
    metric_day: str | None = None,
    expected_row_count: int | None = None,
) -> None:
    """Valida homogeneidade da fatia lendo apenas colunas-chave do staging Iceberg."""
    period_col = config.replace_columns[0]
    columns = [config.connection_column, period_col]
    if "resource_id" in {field.name for field in staged_table.schema().fields}:
        columns.append("resource_id")
    arrow = staged_table.scan().select(*columns).to_arrow()
    if arrow.num_rows == 0:
        validate_staging_slice(
            pd.DataFrame(),
            config,
            connection_id=connection_id,
            billing_period=billing_period,
            snapshot_date=snapshot_date,
            metric_day=metric_day,
            expected_row_count=expected_row_count,
        )
        return
    frame = arrow.to_pandas()
    validate_staging_slice(
        frame,
        config,
        connection_id=connection_id,
        billing_period=billing_period,
        snapshot_date=snapshot_date,
        metric_day=metric_day,
        expected_row_count=expected_row_count,
    )
