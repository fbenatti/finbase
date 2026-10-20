"""Contratos Iceberg do motor v2."""
from __future__ import annotations

from typing import Iterable

ColumnDef = tuple[str, str]

GOLD_NS = "gold"
SILVER_NS = "silver"

DAILY_TABLE = "anomaly_cost_daily"
MONTHLY_TABLE = "anomaly_cost_monthly"
SCOPE_ATTR_TABLE = "anomaly_scope_attributes"
COMMITMENT_TABLE = "anomaly_commitment_ledger"
SIGNAL_TABLE = "anomaly_signal"
IMPACT_DAILY_TABLE = "anomaly_impact_daily"
PROFILE_TABLE = "anomaly_series_profile"

DAILY_COLUMNS: tuple[ColumnDef, ...] = (
    ("day", "varchar"),
    ("scope_id", "varchar"),
    ("cloud", "varchar"),
    ("subaccount_name", "varchar"),
    ("resource_group", "varchar"),
    ("service_name", "varchar"),
    ("service_category", "varchar"),
    ("charge_category", "varchar"),
    ("pricing_model", "varchar"),
    ("commitment_type", "varchar"),
    ("region", "varchar"),
    ("currency", "varchar"),
    ("cost_cloud", "double"),
    ("pricing_quantity", "double"),
    ("resource_count", "bigint"),
    ("row_count", "bigint"),
    ("source_snapshot", "varchar"),
    ("loaded_at", "timestamp(6) with time zone"),
)

MONTHLY_COLUMNS: tuple[ColumnDef, ...] = (
    ("month", "varchar"),
    ("scope_id", "varchar"),
    ("total_cloud", "double"),
    ("active_days", "bigint"),
    ("max_daily_cloud", "double"),
)

SCOPE_ATTR_COLUMNS: tuple[ColumnDef, ...] = (
    ("scope_id", "varchar"),
    ("centro_custo", "varchar"),
    ("organograma_path", "varchar"),
    ("owner_tags", "varchar"),
    ("updated_at", "timestamp(6) with time zone"),
)

COMMITMENT_COLUMNS: tuple[ColumnDef, ...] = (
    ("commitment_id", "varchar"),
    ("scope_id", "varchar"),
    ("kind", "varchar"),
    ("description", "varchar"),
    ("cloud", "varchar"),
    ("subaccount_name", "varchar"),
    ("anniversary_day", "bigint"),
    ("expected_amount", "double"),
    ("amount_mad", "double"),
    ("last_charge_date", "varchar"),
    ("next_expected_date", "varchar"),
    ("charges_seen", "bigint"),
    ("status", "varchar"),
    ("updated_at", "timestamp(6) with time zone"),
)

SIGNAL_COLUMNS: tuple[ColumnDef, ...] = (
    ("run_id", "bigint"),
    ("scope_id", "varchar"),
    ("day", "varchar"),
    ("detector", "varchar"),
    ("cost_nature", "varchar"),
    ("observed", "double"),
    ("expected", "double"),
    ("lower", "double"),
    ("upper", "double"),
    ("delta", "double"),
    ("pct_delta", "double"),
    ("z_robust", "double"),
    ("forecast_cloud", "double"),
    ("tier", "varchar"),
    ("reason_codes", "varchar"),
    ("confidence", "varchar"),
    ("model_version", "varchar"),
)

PROFILE_COLUMNS: tuple[ColumnDef, ...] = (
    ("scope_id", "varchar"),
    ("cost_nature", "varchar"),
    ("history_days", "bigint"),
    ("level", "double"),
    ("mad", "double"),
    ("weekday_factors", "varchar"),
    ("monthly_factor", "double"),
    ("seasonality_flags", "varchar"),
    ("confidence", "varchar"),
    ("model_version", "varchar"),
    ("updated_at", "timestamp(6) with time zone"),
)

IMPACT_DAILY_COLUMNS: tuple[ColumnDef, ...] = (
    ("case_id", "bigint"),
    ("scope_id", "varchar"),
    ("day", "varchar"),
    ("phase", "varchar"),
    ("observed", "double"),
    ("expected", "double"),
    ("excess", "double"),
    ("deviation_status", "varchar"),
    ("impact_class", "varchar"),
)


def _ddl(namespace: str, table: str, columns: Iterable[ColumnDef]) -> str:
    rendered = ",\n    ".join(f'"{name}" {ctype}' for name, ctype in columns)
    return f'CREATE TABLE IF NOT EXISTS iceberg.{namespace}.{table} (\n    {rendered}\n)'


def ddl_statements() -> list[str]:
    return [
        _ddl(GOLD_NS, DAILY_TABLE, DAILY_COLUMNS),
        _ddl(GOLD_NS, MONTHLY_TABLE, MONTHLY_COLUMNS),
        _ddl(GOLD_NS, SCOPE_ATTR_TABLE, SCOPE_ATTR_COLUMNS),
        _ddl(GOLD_NS, COMMITMENT_TABLE, COMMITMENT_COLUMNS),
        _ddl(GOLD_NS, IMPACT_DAILY_TABLE, IMPACT_DAILY_COLUMNS),
        _ddl(SILVER_NS, SIGNAL_TABLE, SIGNAL_COLUMNS),
        _ddl(SILVER_NS, PROFILE_TABLE, PROFILE_COLUMNS),
    ]
