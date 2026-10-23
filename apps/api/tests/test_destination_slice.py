import pandas as pd
import pytest

from app.services.destination_slice import (
    build_slice_row_filter,
    resolve_destination_slice_config,
    snapshot_date_from_blob_path,
    validate_staging_slice,
)
from pyiceberg.expressions import And


def test_snapshot_date_from_arg_inventory_path():
    assert snapshot_date_from_blob_path("arg/inventory/2025-09-15.csv") == "2025-09-15"


def test_snapshot_date_from_license_month_blob():
    assert snapshot_date_from_blob_path("202608") == "2026-08-01"


def test_default_grain_for_focus_is_billing_period():
    cfg = resolve_destination_slice_config("dest_billing_cloud", None)
    assert cfg.grain == "billing_period"
    assert cfg.replace_columns == ("_billing_period", "source_connection_id")


def test_default_grain_for_inventory_is_snapshot_date():
    cfg = resolve_destination_slice_config("dest_cloud_resource_inventory", None)
    assert cfg.grain == "snapshot_date"


def test_licencas_ia_uses_snapshotdate_column():
    cfg = resolve_destination_slice_config(
        "dest_licencas_ia",
        None,
        column_names={"SnapshotDate", "source_connection_id", "UserId"},
    )
    assert cfg.grain == "snapshot_date"
    assert cfg.snapshot_column == "SnapshotDate"
    assert cfg.replace_columns[0] == "SnapshotDate"


def test_build_slice_row_filter_billing():
    cfg = resolve_destination_slice_config("dest_billing_cloud", None)
    expr = build_slice_row_filter(cfg, connection_id=7, billing_period="202501")
    assert isinstance(expr, And)


def test_validate_staging_rejects_mixed_periods():
    cfg = resolve_destination_slice_config("dest_billing_cloud", None)
    frame = pd.DataFrame({
        "source_connection_id": [1, 1],
        "_billing_period": ["202501", "202502"],
    })
    with pytest.raises(ValueError, match="múltiplos valores"):
        validate_staging_slice(
            frame, cfg, connection_id=1, billing_period="202501",
        )


def test_build_partition_spec_pyarrow_safe_downgrades_bucket():
    from pyiceberg.schema import Schema
    from pyiceberg.types import LongType, NestedField, StringType

    from app.services.destination_slice import (
        PartitionExtra,
        DestinationSliceConfig,
        build_iceberg_partition_spec,
    )

    schema = Schema(
        NestedField(1, "_billing_period", StringType(), required=False),
        NestedField(2, "source_connection_id", LongType(), required=False),
        NestedField(3, "subaccountname", StringType(), required=False),
    )
    config = DestinationSliceConfig(
        grain="billing_period",
        extras=(PartitionExtra(column="subaccountname", transform="bucket", bucket_count=16),),
    )
    spec = build_iceberg_partition_spec(schema, config, pyarrow_safe=True)
    assert len(spec.fields) == 2
    assert all(f.name != "subaccountname_bucket_16" for f in spec.fields)


def test_build_partition_spec_includes_subaccount_id():
    from pyiceberg.schema import Schema
    from pyiceberg.types import LongType, NestedField, StringType

    from app.services.destination_slice import (
        PartitionExtra,
        DestinationSliceConfig,
        build_iceberg_partition_spec,
    )

    schema = Schema(
        NestedField(1, "_billing_period", StringType(), required=False),
        NestedField(2, "source_connection_id", LongType(), required=False),
        NestedField(3, "SubAccountId", StringType(), required=False),
        NestedField(4, "SubAccountName", StringType(), required=False),
    )
    config = DestinationSliceConfig(
        grain="billing_period",
        extras=(
            PartitionExtra(column="SubAccountId", transform="identity"),
            PartitionExtra(column="SubAccountName", transform="identity"),
        ),
    )
    spec = build_iceberg_partition_spec(schema, config, pyarrow_safe=True)
    names = [field.name for field in spec.fields]
    assert names == ["_billing_period", "source_connection_id", "SubAccountId"]


def test_partition_spec_json_extra_requires_column():
    spec = {
        "grain": "billing_period",
        "extra_partitions": [{"column": "subaccountname", "transform": "bucket", "bucket_count": 16}],
    }
    cfg = resolve_destination_slice_config(
        "dest_billing_cloud",
        spec,
        column_names={"_billing_period", "source_connection_id", "subaccountname"},
    )
    assert len(cfg.extras) == 1
    assert cfg.extras[0].transform == "bucket"


def test_metric_day_grain_uses_daily_metric_and_connection_slice():
    cfg = resolve_destination_slice_config(
        "daily_usage_example",
        {"grain": "metric_day"},
        column_names={"metric_day", "_source_connection_id", "resource_id"},
    )
    assert cfg.grain == "metric_day"
    assert cfg.replace_columns == ("metric_day", "_source_connection_id")
    expr = build_slice_row_filter(cfg, connection_id=7, metric_day="20261023")
    assert isinstance(expr, And)


def test_metric_day_staging_validates_one_connection_and_day():
    cfg = resolve_destination_slice_config(
        "daily_usage_example",
        {"grain": "metric_day"},
        column_names={"metric_day", "_source_connection_id", "resource_id"},
    )
    frame = pd.DataFrame({
        "_source_connection_id": [7, 7],
        "metric_day": ["20261023", "20261023"],
        "resource_id": ["vm-a", "vm-b"],
    })
    validate_staging_slice(
        frame, cfg, connection_id=7, metric_day="20261023",
    )
    with pytest.raises(ValueError, match="distintos"):
        validate_staging_slice(
            frame.assign(_source_connection_id=[7, 8]), cfg,
            connection_id=7, metric_day="20261023",
        )
