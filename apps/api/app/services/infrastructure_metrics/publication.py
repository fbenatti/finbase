"""Idempotent publication of infrastructure metric rows into shared Silver."""
from __future__ import annotations

import json
from typing import Iterable, Mapping


_KEY_FIELDS = (
    "_source_connection_id", "_silver_owner", "cloud_provider", "cloud_partition",
    "provider_account_id", "sub_account_id", "region_id", "resource_id",
    "metric_namespace", "source_metric_name", "metric_scope", "dimensions_hash",
    "dimension_schema_version", "aggregation_interval_seconds", "resolution_seconds",
    "origin_kind", "bucket_start_utc",
)
_REQUIRED_KEY_FIELDS = frozenset({
    "_source_connection_id", "_silver_owner", "cloud_provider", "resource_id",
    "metric_namespace", "source_metric_name", "metric_scope", "dimensions_hash",
    "dimension_schema_version", "aggregation_interval_seconds", "resolution_seconds",
    "origin_kind", "bucket_start_utc",
})
_PRESERVE_IF_MISSING = (
    "value", "value_average", "value_minimum", "value_maximum", "value_total",
    "sample_count", "value_rate", "native_p90", "native_p95", "native_p99",
    "distribution_json", "quantile_sketch_format", "quantile_sketch_version",
)


def _row_key(row: Mapping) -> tuple:
    missing = [field for field in _REQUIRED_KEY_FIELDS if row.get(field) is None]
    if missing:
        raise ValueError(f"metric row is missing identity fields: {', '.join(missing)}")
    # Optional identity components still participate in the key, but their
    # absence is represented canonically as NULL (the Silver model permits it).
    return tuple(row.get(field) for field in _KEY_FIELDS)


def _merge_json_map(old_value, new_value) -> str | None:
    old = json.loads(old_value) if isinstance(old_value, str) and old_value else {}
    new = json.loads(new_value) if isinstance(new_value, str) and new_value else {}
    if not isinstance(old, dict) or not isinstance(new, dict):
        raise ValueError("metric provenance fields must contain JSON objects")
    old.update(new)
    return json.dumps(old, sort_keys=True, separators=(",", ":")) if old or new else None


def _merge_statistics_status(old_value, new_value) -> str | None:
    old = json.loads(old_value) if isinstance(old_value, str) and old_value else {}
    new = json.loads(new_value) if isinstance(new_value, str) and new_value else {}
    if not isinstance(old, dict) or not isinstance(new, dict):
        raise ValueError("statistics status must contain a JSON object")
    merged = {**old, **new}
    for field in ("requested", "returned"):
        if field in old or field in new:
            merged[field] = sorted(set(old.get(field, ())) | set(new.get(field, ())))
    if old.get("errors") or new.get("errors"):
        unique_errors = {}
        for item in (*old.get("errors", ()), *new.get("errors", ())):
            unique_errors[json.dumps(item, sort_keys=True, separators=(",", ":"))] = item
        merged["errors"] = [unique_errors[key] for key in sorted(unique_errors)]
    return json.dumps(merged, sort_keys=True, separators=(",", ":")) if merged else None


def merge_metric_rows(existing: Iterable[Mapping], incoming: Iterable[Mapping]) -> list[dict]:
    """Merge one connection/day slice; absent values never erase valid history."""
    merged: dict[tuple, dict] = {}
    incoming_keys: set[tuple] = set()
    for source_name, source_rows in (("existing", existing), ("incoming", incoming)):
        for source in source_rows:
            row = dict(source)
            key = _row_key(row)
            prior = merged.get(key)
            if prior is None:
                merged[key] = row
                if source_name == "incoming":
                    incoming_keys.add(key)
                continue
            if source_name == "existing":
                raise ValueError("existing Silver slice contains duplicate metric series buckets")
            if key in incoming_keys:
                conflicts = [
                    field for field in _PRESERVE_IF_MISSING
                    if prior.get(field) is not None and row.get(field) is not None
                    and prior[field] != row[field]
                ]
                if conflicts:
                    raise ValueError(
                        "incoming metric batch has conflicting duplicate bucket values: "
                        + ", ".join(conflicts)
                    )
            incoming_keys.add(key)
            for field, value in row.items():
                if field in _PRESERVE_IF_MISSING and value is None:
                    continue
                if field in {"statistic_lineage_json", "statistics_status_json"}:
                    prior[field] = (
                        _merge_statistics_status(prior.get(field), value)
                        if field == "statistics_status_json"
                        else _merge_json_map(prior.get(field), value)
                    )
                elif value is not None:
                    prior[field] = value
            # Retain explicit NULL metrics while merging provenance dictionaries.
            for field in ("statistic_lineage_json", "statistics_status_json"):
                if field in row and row[field] is None:
                    prior.setdefault(field, None)
    return list(merged.values())


def _column_types() -> dict[str, str]:
    from app.services.silver_models import METRIC_SERIES

    mapping = {
        "varchar": "VARCHAR", "decimal": "DOUBLE", "double": "DOUBLE",
        "bigint": "BIGINT", "boolean": "BOOLEAN",
    }
    return {field.name: mapping[field.type] for field in METRIC_SERIES.fields
            if field.type in mapping}


def _ensure_metric_series_table_locked(*, namespace: str = "silver") -> bool:
    """Create the empty shared Silver table, or evolve it to the declared model.

    Returns True when a new table was registered. This runs before provider
    queries on the first persisted collection, so an empty/unsupported metric
    response still leaves the agreed schema visible in the catalog.
    """
    from pyiceberg.schema import Schema
    from pyiceberg.types import (
        BooleanType, DoubleType, LongType, NestedField, StringType, TimestampType,
    )

    from app.services import iceberg_service
    from app.services.destination_slice import (
        build_iceberg_partition_spec,
        resolve_destination_slice_config_for_fields,
    )
    from app.services.silver_models import METRIC_SERIES

    iceberg_types = {
        "varchar": StringType(), "decimal": DoubleType(), "double": DoubleType(),
        "bigint": LongType(), "boolean": BooleanType(), "timestamp": TimestampType(),
    }
    target_schema = Schema(*[
        NestedField(
            field_id=index, name=field.name, field_type=iceberg_types[field.type],
            required=field.required,
        )
        for index, field in enumerate(METRIC_SERIES.fields, start=1)
    ])
    table_name = METRIC_SERIES.table
    if iceberg_service.table_exists(table_name, namespace=namespace):
        table = iceberg_service.get_catalog().load_table((namespace, table_name))
        existing = {field.name for field in table.schema().fields}
        expected = {field.name for field in target_schema.fields}
        if not expected.issubset(existing):
            with table.update_schema() as update:
                update.union_by_name(target_schema)
        return False

    iceberg_service.ensure_namespace(namespace)
    fields = {field.name for field in target_schema.fields}
    slice_config = resolve_destination_slice_config_for_fields(
        table_name,
        {"grain": "metric_day", "connection_column": "_source_connection_id"},
        fields,
    )
    partition_spec = build_iceberg_partition_spec(target_schema, slice_config, pyarrow_safe=True)
    table_id = (namespace, table_name)
    try:
        iceberg_service.get_catalog().create_table(
            table_id, schema=target_schema, partition_spec=partition_spec,
        )
        return True
    except Exception:
        # Another API/worker can initialize the shared table at the same time.
        if not iceberg_service.table_exists(table_name, namespace=namespace):
            raise
        table = iceberg_service.get_catalog().load_table(table_id)
        existing = {field.name for field in table.schema().fields}
        if not fields.issubset(existing):
            with table.update_schema() as update:
                update.union_by_name(target_schema)
        return False


def ensure_metric_series_table(*, namespace: str = "silver") -> bool:
    """Initialize/evolve the shared schema under the warehouse write lock."""
    from app.services.write_lock import exclusive_write_lock

    with exclusive_write_lock():
        return _ensure_metric_series_table_locked(namespace=namespace)


def ensure_metric_coverage_table(*, namespace: str = "silver") -> bool:
    """Create/evolve the shared daily coverage model under the write lock."""
    from pyiceberg.schema import Schema
    from pyiceberg.types import BooleanType, DoubleType, LongType, NestedField, StringType, TimestampType

    from app.services import iceberg_service
    from app.services.destination_slice import build_iceberg_partition_spec, resolve_destination_slice_config_for_fields
    from app.services.silver_models import METRIC_COVERAGE
    from app.services.write_lock import exclusive_write_lock

    iceberg_types = {
        "varchar": StringType(), "decimal": DoubleType(), "double": DoubleType(),
        "bigint": LongType(), "boolean": BooleanType(), "timestamp": TimestampType(),
    }
    target = Schema(*[
        NestedField(index, field.name, iceberg_types[field.type], required=field.required)
        for index, field in enumerate(METRIC_COVERAGE.fields, start=1)
    ])
    table_name = METRIC_COVERAGE.table
    fields = {field.name for field in target.fields}
    with exclusive_write_lock():
        if iceberg_service.table_exists(table_name, namespace=namespace):
            table = iceberg_service.get_catalog().load_table((namespace, table_name))
            existing = {field.name for field in table.schema().fields}
            if not fields.issubset(existing):
                with table.update_schema() as update:
                    update.union_by_name(target)
            return False


def ensure_metric_profile_table(*, namespace: str = "silver") -> bool:
    """Create/evolve the versioned daily profile table under the write lock."""
    from pyiceberg.schema import Schema
    from pyiceberg.types import BooleanType, DoubleType, LongType, NestedField, StringType, TimestampType

    from app.services import iceberg_service
    from app.services.destination_slice import build_iceberg_partition_spec, resolve_destination_slice_config_for_fields
    from app.services.silver_models import RESOURCE_PROFILE_DAILY_WINDOW
    from app.services.write_lock import exclusive_write_lock

    types = {"varchar": StringType(), "decimal": DoubleType(), "double": DoubleType(),
             "bigint": LongType(), "boolean": BooleanType(), "timestamp": TimestampType()}
    target = Schema(*[
        NestedField(index, field.name, types[field.type], required=field.required)
        for index, field in enumerate(RESOURCE_PROFILE_DAILY_WINDOW.fields, start=1)
    ])
    name = RESOURCE_PROFILE_DAILY_WINDOW.table
    names = {field.name for field in target.fields}
    spec = {"grain": "snapshot_date", "snapshot_column": "window_to_day",
            "connection_column": "series_source_connection_id"}
    with exclusive_write_lock():
        if iceberg_service.table_exists(name, namespace=namespace):
            table = iceberg_service.get_catalog().load_table((namespace, name))
            existing = {field.name for field in table.schema().fields}
            if not names.issubset(existing):
                with table.update_schema() as update:
                    update.union_by_name(target)
            return False
        iceberg_service.ensure_namespace(namespace)
        config = resolve_destination_slice_config_for_fields(name, spec, names)
        partition_spec = build_iceberg_partition_spec(target, config, pyarrow_safe=True)
        iceberg_service.get_catalog().create_table((namespace, name), schema=target, partition_spec=partition_spec)
        return True


def publish_metric_profile_rows(
    rows: Iterable[Mapping], *, connection_id: int, snapshot_date: str,
    namespace: str = "silver",
) -> int:
    """Idempotently replace profile rows for a connection and end day."""
    import pandas as pd

    from app.services import iceberg_service
    from app.services.destination_slice import build_slice_row_filter, resolve_destination_slice_config_for_fields
    from app.services.silver_models import RESOURCE_PROFILE_DAILY_WINDOW
    from app.services.write_lock import exclusive_write_lock

    incoming = [dict(row) for row in rows]
    if not incoming:
        return 0
    name = RESOURCE_PROFILE_DAILY_WINDOW.table
    spec = {"grain": "snapshot_date", "snapshot_column": "window_to_day",
            "connection_column": "series_source_connection_id"}
    for row in incoming:
        if int(row.get("series_source_connection_id", -1)) != int(connection_id):
            raise ValueError("profile rows mix source connections")
        if str(row.get("window_to_day")) != snapshot_date:
            raise ValueError("profile rows mix snapshot dates")
    with exclusive_write_lock():
        table = iceberg_service.get_catalog().load_table((namespace, name))
        config = resolve_destination_slice_config_for_fields(name, spec, {f.name for f in table.schema().fields})
        old = table.scan(row_filter=build_slice_row_filter(
            config, connection_id=connection_id, snapshot_date=snapshot_date,
        )).to_arrow().to_pylist()
        key_fields = ("series_source_connection_id", "series_owner", "series_id", "resolution_seconds",
                      "value_statistic", "window_start_utc", "window_end_utc", "algorithm_version", "policy_version")
        merged = {tuple(row.get(key) for key in key_fields): dict(row) for row in old}
        for row in incoming:
            merged[tuple(row.get(key) for key in key_fields)] = row
        frame = pd.DataFrame(list(merged.values()))
        columns = {field.name: {"varchar": "VARCHAR", "bigint": "BIGINT", "double": "DOUBLE",
                               "decimal": "DOUBLE", "boolean": "BOOLEAN"}.get(field.type, "TIMESTAMP")
                   for field in RESOURCE_PROFILE_DAILY_WINDOW.fields}
        for field in RESOURCE_PROFILE_DAILY_WINDOW.fields:
            if field.type == "timestamp" and field.name in frame.columns:
                frame[field.name] = pd.to_datetime(frame[field.name], errors="coerce", utc=True).dt.tz_localize(None)
        iceberg_service.publish_silver_dataframe_slice(
            name, frame, connection_id=connection_id, snapshot_date=snapshot_date,
            namespace=namespace, partition_spec_json=spec, column_types=columns,
        )
    return len(merged)
        iceberg_service.ensure_namespace(namespace)
        config = resolve_destination_slice_config_for_fields(
            table_name, {"grain": "metric_day", "connection_column": "_source_connection_id"}, fields,
        )
        partition_spec = build_iceberg_partition_spec(target, config, pyarrow_safe=True)
        try:
            iceberg_service.get_catalog().create_table(
                (namespace, table_name), schema=target, partition_spec=partition_spec,
            )
            return True
        except Exception:
            if not iceberg_service.table_exists(table_name, namespace=namespace):
                raise
            table = iceberg_service.get_catalog().load_table((namespace, table_name))
            existing = {field.name for field in table.schema().fields}
            if not fields.issubset(existing):
                with table.update_schema() as update:
                    update.union_by_name(target)
            return False


def publish_metric_coverage_rows(
    rows: Iterable[Mapping], *, connection_id: int, metric_day: str,
    namespace: str = "silver",
) -> int:
    """Idempotently publish one connection/day coverage snapshot."""
    import pandas as pd

    from app.services import iceberg_service
    from app.services.destination_slice import build_slice_row_filter, resolve_destination_slice_config_for_fields
    from app.services.silver_models import METRIC_COVERAGE
    from app.services.write_lock import exclusive_write_lock

    incoming = [dict(row) for row in rows]
    if not incoming:
        return 0
    for row in incoming:
        if int(row.get("_source_connection_id", -1)) != int(connection_id) or str(row.get("metric_day")) != metric_day:
            raise ValueError("coverage rows mix source connections or metric days")
    with exclusive_write_lock():
        table = iceberg_service.get_catalog().load_table((namespace, METRIC_COVERAGE.table))
        schema_names = {field.name for field in table.schema().fields}
        slice_config = resolve_destination_slice_config_for_fields(
            METRIC_COVERAGE.table, {"grain": "metric_day", "connection_column": "_source_connection_id"}, schema_names,
        )
        existing = table.scan(row_filter=build_slice_row_filter(
            slice_config, connection_id=connection_id, metric_day=metric_day,
        )).to_arrow().to_pylist()
        key_fields = ("_source_connection_id", "series_id", "resolution_seconds", "origin_kind", "metric_day")
        merged = {tuple(row.get(name) for name in key_fields): dict(row) for row in existing}
        for row in incoming:
            merged[tuple(row.get(name) for name in key_fields)] = row
        frame = pd.DataFrame(list(merged.values()))
        for field in METRIC_COVERAGE.fields:
            if field.type == "timestamp" and field.name in frame.columns:
                frame[field.name] = pd.to_datetime(frame[field.name], errors="coerce", utc=True).dt.tz_localize(None)
        column_types = {
            field.name: {"varchar": "VARCHAR", "bigint": "BIGINT", "double": "DOUBLE",
                         "decimal": "DOUBLE", "boolean": "BOOLEAN"}.get(field.type, "TIMESTAMP")
            for field in METRIC_COVERAGE.fields
        }
        iceberg_service.publish_silver_dataframe_slice(
            METRIC_COVERAGE.table, frame, connection_id=connection_id,
            metric_day=metric_day, namespace=namespace,
            partition_spec_json={"grain": "metric_day", "connection_column": "_source_connection_id"},
            column_types=column_types,
        )
    return len(merged)


def publish_metric_rows(
    rows: Iterable[Mapping], *, connection_id: int, metric_day: str,
    namespace: str = "silver",
) -> int:
    """Merge and atomically replace a single connection/day Silver slice."""
    import pandas as pd

    incoming = [dict(row) for row in rows]
    if not incoming:
        return 0
    if not metric_day.isdigit() or len(metric_day) != 8:
        raise ValueError("metric_day must use YYYYMMDD")
    for row in incoming:
        if int(row.get("_source_connection_id", -1)) != int(connection_id):
            raise ValueError("incoming rows mix source connections")
        if str(row.get("metric_day")) != metric_day:
            raise ValueError("incoming rows mix metric days")

    from app.services import iceberg_service
    from app.services.destination_slice import build_slice_row_filter, resolve_destination_slice_config_for_fields
    from app.services.write_lock import exclusive_write_lock

    physical_name = "resource_metric_series"
    existing_rows: list[dict] = []
    with exclusive_write_lock():
        if iceberg_service.table_exists(physical_name, namespace=namespace):
            table = iceberg_service.get_catalog().load_table((namespace, physical_name))
            field_names = {field.name for field in table.schema().fields}
            slice_config = resolve_destination_slice_config_for_fields(
                physical_name, None, field_names,
            )
            row_filter = build_slice_row_filter(
                slice_config, connection_id=connection_id, metric_day=metric_day,
            )
            existing_arrow = table.scan(row_filter=row_filter).to_arrow()
            existing_rows = existing_arrow.to_pylist()

        merged_rows = merge_metric_rows(existing_rows, incoming)
        frame = pd.DataFrame(merged_rows)
        from app.services.silver_models import METRIC_SERIES
        for field in METRIC_SERIES.fields:
            if field.type == "timestamp" and field.name in frame.columns:
                frame[field.name] = pd.to_datetime(frame[field.name], errors="coerce", utc=True).dt.tz_localize(None)
        iceberg_service.publish_silver_dataframe_slice(
            physical_name, frame, connection_id=connection_id,
            metric_day=metric_day, namespace=namespace,
            partition_spec_json={
                "grain": "metric_day", "connection_column": "_source_connection_id",
            },
            column_types=_column_types(),
        )
    return len(merged_rows)
