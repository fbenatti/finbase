"""Silver destination slice keys (replace) vs Iceberg partition spec (pruning)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

import pandas as pd

Grain = Literal["billing_period", "snapshot_date"]

_SNAPSHOT_PHYSICAL_DEFAULTS = frozenset({
    "dest_cloud_resource_inventory",
    "dest_cloud_orphan_resources",
    "dest_licencas_ia",
})

_MAX_EXTRA_PARTITIONS = 2
_BUCKET_CARDINALITY_COLUMNS = frozenset({
    "subaccountname",
    "useremail",
    "resource_id",
    "resourceid",
})


@dataclass(frozen=True)
class PartitionExtra:
    column: str
    transform: Literal["identity", "bucket"] = "identity"
    bucket_count: int = 16


@dataclass(frozen=True)
class DestinationSliceConfig:
    grain: Grain
    snapshot_column: str = "snapshot_date"
    extras: tuple[PartitionExtra, ...] = field(default_factory=tuple)

    @property
    def replace_columns(self) -> tuple[str, str]:
        if self.grain == "billing_period":
            return ("_billing_period", "source_connection_id")
        return (self.snapshot_column, "source_connection_id")

    def partition_columns(self) -> list[str]:
        cols = list(self.replace_columns)
        for extra in self.extras:
            if extra.column not in cols:
                cols.append(extra.column)
        return cols


def _normalise_physical_name(physical_name: str) -> str:
    return physical_name.replace("-", "_").replace(".", "_").strip().lower()


def snapshot_date_from_blob_path(blob_path: str) -> str | None:
    """Extract YYYY-MM-DD from ARG/inventory and similar Bronze paths."""
    if not blob_path:
        return None
    match = re.search(
        r"(?:arg/(?:inventory|orphans/[^/]+)|licenses?|license)/(\d{4}-\d{2}-\d{2})",
        blob_path,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1)
    match = re.search(r"/(\d{4}-\d{2}-\d{2})(?:\.[a-z0-9]+)?$", blob_path, flags=re.IGNORECASE)
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
    if raw_grain in ("billing_period", "snapshot_date"):
        grain = raw_grain  # type: ignore[assignment]
    elif safe in _SNAPSHOT_PHYSICAL_DEFAULTS:
        grain = "snapshot_date"
    else:
        grain = "billing_period"

    snapshot_column = resolve_snapshot_column(spec if grain == "snapshot_date" else None, column_names)
    extras = _parse_extras(spec.get("extra_partitions"), column_names=column_names)
    return DestinationSliceConfig(grain=grain, snapshot_column=snapshot_column, extras=extras)


def primary_partition_column(config: DestinationSliceConfig) -> str:
    return config.replace_columns[0]


def build_slice_row_filter(
    config: DestinationSliceConfig,
    *,
    connection_id: int,
    billing_period: str | None = None,
    snapshot_date: str | None = None,
):
    from pyiceberg.expressions import And, EqualTo

    period_col, _conn_col = config.replace_columns
    clauses = [EqualTo("source_connection_id", int(connection_id))]
    if config.grain == "billing_period":
        if billing_period is None:
            raise ValueError("billing_period é obrigatório para publicação por mês")
        clauses.append(EqualTo(period_col, str(billing_period)))
    else:
        if snapshot_date is None:
            raise ValueError("snapshot_date é obrigatório para publicação diária")
        clauses.append(EqualTo(period_col, str(snapshot_date)))
    if len(clauses) == 1:
        return clauses[0]
    return And(*clauses)


def build_iceberg_partition_spec(iceberg_schema, config: DestinationSliceConfig):
    from pyiceberg.partitioning import PartitionField, PartitionSpec
    from pyiceberg.transforms import BucketTransform, IdentityTransform

    fields: list[PartitionField] = []
    next_id = iceberg_schema.highest_field_id + 1000
    for col in config.partition_columns():
        part_field = iceberg_schema.find_field(col)
        if part_field is None:
            continue
        extra = next((e for e in config.extras if e.column == col), None)
        if extra and extra.transform == "bucket":
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
    expected_row_count: int | None = None,
) -> None:
    if df.empty:
        if expected_row_count not in (None, 0):
            raise ValueError(
                f"Staging vazio, mas o worker reportou {expected_row_count} linhas"
            )
        return

    if "source_connection_id" not in df.columns:
        raise ValueError("Staging sem coluna source_connection_id")
    conn_series = pd.to_numeric(df["source_connection_id"], errors="coerce")
    if (conn_series != int(connection_id)).any():
        raise ValueError("Staging mistura source_connection_id distintos da fatia")

    period_col = config.replace_columns[0]
    if period_col not in df.columns:
        raise ValueError(f"Staging sem coluna de fatia obrigatória: {period_col}")

    period_values = df[period_col].astype(str).dropna().unique()
    if len(period_values) != 1:
        raise ValueError(f"Staging com múltiplos valores em {period_col}: {period_values[:5]}")

    if config.grain == "billing_period":
        expected = str(billing_period) if billing_period is not None else None
    else:
        expected = str(snapshot_date) if snapshot_date is not None else None
    if expected is not None and str(period_values[0]) != expected:
        raise ValueError(
            f"Valor de {period_col} no staging ({period_values[0]}) diverge da fatia ({expected})"
        )

    if expected_row_count is not None and len(df) != int(expected_row_count):
        raise ValueError(
            f"Contagem no staging ({len(df)}) diverge do worker ({expected_row_count})"
        )

    if config.grain == "snapshot_date" and {"resource_id", period_col}.issubset(df.columns):
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
    expected_row_count: int | None = None,
) -> None:
    """Valida homogeneidade da fatia lendo apenas colunas-chave do staging Iceberg."""
    period_col = config.replace_columns[0]
    columns = ["source_connection_id", period_col]
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
        expected_row_count=expected_row_count,
    )
