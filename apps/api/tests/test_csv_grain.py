from datetime import date

import pandas as pd

from app.services.azure_extractor import derive_billing_period
from app.services.csv_upload_service import (
    _derive_billing_period,
    analyze_csv_grain,
    derive_billing_period_from_df,
    detect_monthly_grain,
    is_current_billing_period,
    resolve_billing_period,
    should_skip_gold_realizado,
)
from app.services import csv_upload_service
from app.services.forecast_service import _expand_charge_interval


def test_is_current_billing_period():
    today = date.today()
    current = f"{today.year}{today.month:02d}"
    assert is_current_billing_period(current) is True
    assert is_current_billing_period("199901") is False
    assert is_current_billing_period("invalid") is False


def test_detect_monthly_grain_ibm_focus_pattern():
    df = pd.DataFrame({
        "ChargePeriodStart": ["2026-07-01", "2026-07-01"],
        "ChargePeriodEnd": ["2026-07-31", "2026-07-31"],
        "BilledCost": [100.0, 200.0],
    })
    assert detect_monthly_grain(df, "202607") is True


def test_detect_monthly_grain_daily_detail():
    df = pd.DataFrame({
        "ChargePeriodStart": [f"2026-07-{day:02d}" for day in range(1, 11)],
        "BilledCost": [10.0] * 10,
    })
    assert detect_monthly_grain(df, "202607") is False


def test_analyze_csv_grain_closed_month():
    df = pd.DataFrame({
        "ChargePeriodStart": ["2026-07-01"],
        "ChargePeriodEnd": ["2026-07-31"],
        "BilledCost": [100.0],
    })
    grain = analyze_csv_grain(df, "202607")
    assert grain["monthly_grain"] is True
    assert grain["skip_gold_realizado"] is False
    assert grain["warning_message"] is None


def test_should_skip_gold_from_metadata(monkeypatch):
    monkeypatch.setattr(csv_upload_service, "is_current_billing_period", lambda period: period == "202608")
    config = {
        "manual_csv_metadata": {
            "202608": {"skip_gold_realizado": True, "monthly_grain": True},
        }
    }
    skip, message = should_skip_gold_realizado("202608", connection_config=config)
    assert skip is True
    assert message


def test_provider_month_to_date_snapshot_is_not_skipped_without_manual_csv_metadata(monkeypatch):
    monkeypatch.setattr(csv_upload_service, "is_current_billing_period", lambda period: period == "202610")
    # This is the shape of the Azure Dev October extract: a single Date of
    # month-start triggers the generic monthly-grain heuristic, despite being
    # a current-period cost snapshot that must feed realized and forecast.
    df = pd.DataFrame({"Date": ["2026-10-01"], "BilledCost": [125.0]})
    assert detect_monthly_grain(df, "202610") is True

    skip, message = should_skip_gold_realizado("202610", df=df)
    assert skip is False
    assert message is None


def test_monthly_lump_baseline_from_closed_months():
    from app.services.forecast_service import _build_series_context
    index = pd.to_datetime(["2026-06-01", "2026-07-01"])
    daily = pd.Series([310.0, 620.0], index=index)
    ctx = _build_series_context(daily, date(2026, 8, 10))
    assert ctx["last_closed_month_total"] == 620.0
    assert ctx["monthly_realized"]["202607"] == 620.0


def test_expand_charge_interval_spreads_full_month():
    frame = pd.DataFrame({
        "charge_start": ["2026-07-01"],
        "charge_end": ["2026-07-31"],
        "value": [310.0],
    })
    daily = _expand_charge_interval(frame, [])
    assert len(daily) == 31
    assert abs(float(daily["value"].sum()) - 310.0) < 0.01
    assert abs(float(daily["value"].iloc[0]) - (310.0 / 31)) < 0.01


def test_expand_charge_interval_single_day():
    frame = pd.DataFrame({
        "charge_start": ["2026-07-15"],
        "charge_end": ["2026-07-15"],
        "value": [42.0],
    })
    daily = _expand_charge_interval(frame, [])
    assert len(daily) == 1
    assert float(daily["value"].iloc[0]) == 42.0


def test_expand_charge_interval_by_dimension():
    frame = pd.DataFrame({
        "cloud": ["ibm", "ibm"],
        "charge_start": ["2026-07-01", "2026-07-01"],
        "charge_end": ["2026-07-02", "2026-07-02"],
        "value": [20.0, 40.0],
    })
    daily = _expand_charge_interval(frame, ["cloud"])
    assert len(daily) == 2
    assert abs(float(daily.loc[daily["cloud"] == "ibm", "value"].sum()) - 60.0) < 0.01
    assert abs(float(daily["value"].iloc[0]) - 30.0) < 0.01


def test_derive_billing_period_from_df_azure_date_column():
    df = pd.DataFrame({
        "date": ["2025-11-15", "2025-11-16", "2025-11-17"],
        "costInBillingCurrency": [10.0, 20.0, 30.0],
    })
    assert derive_billing_period_from_df(df) == "202511"


def test_derive_billing_period_prefers_content_over_filename():
    df = pd.DataFrame({
        "billingPeriodStartDate": ["2026-01-01", "2026-01-31"],
        "BilledCost": [100.0, 200.0],
    })
    assert _derive_billing_period("report-202608.csv", df) == "202601"


def test_resolve_billing_period_prefers_content_over_explicit_form():
    df = pd.DataFrame({
        "date": ["2025-12-01", "2025-12-02"],
        "costInBillingCurrency": [10.0, 20.0],
    })
    assert resolve_billing_period("report-202608.csv", df, billing_period="202608") == "202512"


def test_derive_billing_period_azure_blob_prefers_content():
    df = pd.DataFrame({
        "date": ["20251201", "20251202"],
        "productName": ["A", "B"],
    })
    assert derive_billing_period("reports/azure-marketplace/wrong-name.csv", df) == "202512"


def test_df_to_arrow_keeps_all_null_boolean_as_boolean():
    import pyarrow as pa
    from app.services.iceberg_service import _df_to_arrow

    table = _df_to_arrow(
        pd.DataFrame({"is_synthetic": [None, None], "tipo": ["realizado", "realizado"]}),
        column_types={"is_synthetic": "BOOLEAN", "tipo": "TEXT"},
    )
    assert pa.types.is_boolean(table.schema.field("is_synthetic").type)
    assert pa.types.is_string(table.schema.field("tipo").type)


def test_df_to_arrow_uses_existing_iceberg_schema_as_type_contract():
    import pyarrow as pa
    from pyiceberg.schema import Schema
    from pyiceberg.types import BooleanType, DoubleType, LongType, NestedField, StringType
    from app.services.iceberg_service import _df_to_arrow

    target_schema = Schema(
        NestedField(1, "financial_contract_id", LongType(), required=False),
        NestedField(2, "realizado", DoubleType(), required=False),
        NestedField(3, "active", BooleanType(), required=False),
        NestedField(4, "description", StringType(), required=False),
    )
    table = _df_to_arrow(
        pd.DataFrame({
            "financial_contract_id": [1.0, None],
            "realizado": [10, None],
            "active": [True, None],
            "description": [None, None],
        }),
        target_schema=target_schema,
    )

    assert pa.types.is_int64(table.schema.field("financial_contract_id").type)
    assert pa.types.is_float64(table.schema.field("realizado").type)
    assert pa.types.is_boolean(table.schema.field("active").type)
    assert pa.types.is_large_string(table.schema.field("description").type)
    assert table.column("financial_contract_id").to_pylist() == [1, None]


def test_df_to_arrow_rejects_fractional_value_for_iceberg_identifier():
    import pytest
    from pyiceberg.schema import Schema
    from pyiceberg.types import LongType, NestedField
    from app.services.iceberg_service import _df_to_arrow

    target_schema = Schema(
        NestedField(1, "financial_contract_id", LongType(), required=False),
    )
    with pytest.raises(ValueError, match="financial_contract_id.*double -> int64"):
        _df_to_arrow(
            pd.DataFrame({"financial_contract_id": [1.5]}),
            target_schema=target_schema,
        )


def test_df_to_arrow_aligns_columns_to_existing_iceberg_field_order():
    import pyarrow as pa
    from pyiceberg.schema import Schema
    from pyiceberg.types import DoubleType, LongType, NestedField, StringType
    from app.services.iceberg_service import _df_to_arrow

    target_schema = Schema(
        NestedField(1, "name", StringType(), required=False),
        NestedField(2, "financial_contract_id", LongType(), required=False),
        NestedField(3, "realizado", DoubleType(), required=False),
    )
    table = _df_to_arrow(
        pd.DataFrame({
            "realizado": [10],
            "name": ["IBM"],
        }),
        target_schema=target_schema,
    )

    assert table.column_names == ["name", "financial_contract_id", "realizado"]
    assert table.column("financial_contract_id").to_pylist() == [None]
    assert pa.types.is_int64(table.schema.field("financial_contract_id").type)
    assert pa.types.is_float64(table.schema.field("realizado").type)
    assert table.schema.field("name").metadata[b"PARQUET:field_id"] == b"1"
    assert table.schema.field("financial_contract_id").metadata[b"PARQUET:field_id"] == b"2"
    assert table.schema.field("realizado").metadata[b"PARQUET:field_id"] == b"3"


def test_df_to_arrow_rejects_duplicate_input_columns_before_iceberg_write():
    import pytest
    from app.services.iceberg_service import _df_to_arrow

    frame = pd.DataFrame([[1, 2]], columns=["BilledCost", "BilledCost"])
    with pytest.raises(ValueError, match="colunas duplicadas: BilledCost"):
        _df_to_arrow(frame)
