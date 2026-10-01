"""Forecast engine over aggregated Gold data.

The service deliberately receives only daily aggregates from Trino. It never
loads the detailed Gold table into pandas.
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, timedelta
from typing import Any

import pandas as pd

from app.services.trino_service import execute as trino_execute
from app.services import iceberg_service as iceberg

logger = logging.getLogger(__name__)

# Each shard used to trigger a full partition scan. Batching keeps the
# configured dimensions in the GROUP BY while bounding Trino's aggregation
# cardinality and avoiding hundreds of repeated scans.
_SHARD_BATCH_SIZE = 64


def _series_key(key_values: dict[str, Any], key_cols: list[str]) -> tuple[tuple[str, Any], ...]:
    return tuple((col, key_values.get(col)) for col in key_cols)


async def _contract_budget_commitments(
    contract_ids: set[int], periods: list[str],
) -> dict[tuple[str, str, str, str], float]:
    """Load current monthly budgets for forecasted contract pools.

    Keys are (period, contract_id, cloud_scope, budget_id), matching the
    contract settlement ledger. This keeps the forecast generic across all
    configured contracts and their current budget versions.
    """
    if not contract_ids or not periods:
        return {}
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.database import async_session
    from app.models.financial_contract import FinancialContract
    from app.services.contract_settlement import normalize_cloud_scope
    from app.services.contract_settlement_materializer import _budgets_by_period

    commitments: dict[tuple[str, str, str, str], float] = {}
    async with async_session() as db:
        contracts = (await db.execute(
            select(FinancialContract)
            .options(selectinload(FinancialContract.budget_scopes))
            .where(FinancialContract.id.in_(contract_ids), FinancialContract.active.is_(True))
        )).scalars().all()
        for contract in contracts:
            contract_periods = [
                period for period in periods
                if contract.valid_from <= period <= contract.valid_until
            ]
            monthly, _ = await _budgets_by_period(db, contract, contract_periods)
            for (period, cloud_scope, budget_id), value in monthly.items():
                key = (
                    str(period), str(contract.id),
                    normalize_cloud_scope(cloud_scope), str(budget_id).strip(),
                )
                commitments[key] = float(value)
    return commitments


async def _actual_contract_realized(
    *,
    source: str,
    type_filter: str,
    period_column: str,
    periods: list[str],
) -> dict[tuple[str, str, str, str], float]:
    """Read already-realized commitment by month/pool to avoid double-counting."""
    if not periods:
        return {}
    from app.services.contract_settlement import normalize_cloud_scope

    quoted_periods = ", ".join(_lit(period) for period in periods)
    sql = f"""
        SELECT CAST({_q(period_column)} AS VARCHAR) AS period,
               CAST({_q('financial_contract_id')} AS VARCHAR) AS contract_id,
               lower(trim(CAST({_q('cloud_origem')} AS VARCHAR))) AS cloud_scope,
               trim(CAST({_q('id_orcamento')} AS VARCHAR)) AS budget_id,
               SUM(COALESCE(TRY_CAST({_q('realizado')} AS DOUBLE), 0)) AS realized
          FROM {source}
         WHERE {type_filter}
           AND upper(CAST({_q('financial_effect')} AS VARCHAR)) = 'BUDGET_COMMITMENT'
           AND CAST({_q(period_column)} AS VARCHAR) IN ({quoted_periods})
         GROUP BY 1, 2, 3, 4
    """
    rows = await trino_execute(sql, timeout_s=120)
    return {
        (
            str(row.get("period") or ""),
            str(int(float(row.get("contract_id") or 0))),
            normalize_cloud_scope(row.get("cloud_scope")),
            str(row.get("budget_id") or "").strip(),
        ): float(row.get("realized") or 0)
        for row in rows
    }


async def _apply_dual_financial_measures(
    output: pd.DataFrame,
    *,
    financial_context_columns: list[str],
    source: str,
    type_filter: str,
    period_column: str,
) -> None:
    """Publish consumption forecast in both measures, budget-allocating commitments.

    DIRECT_REALIZED follows the cloud-consumption forecast in both columns.
    BUDGET_COMMITMENT keeps that forecast in realizado_cloud and allocates the
    current monthly budget to its forecast series in realizado, like settlement.
    """
    output["realizado_cloud"] = pd.to_numeric(output["forecast_value"], errors="coerce").fillna(0.0)
    output["realizado"] = output["realizado_cloud"]
    if "financial_effect" not in output or "BUDGET_COMMITMENT" not in set(
        output["financial_effect"].astype("string").str.upper().dropna()
    ):
        return
    required = {"financial_contract_id", "cloud_origem", "id_orcamento"}
    missing = sorted(required - set(financial_context_columns))
    if missing:
        raise ValueError(
            "Forecast BUDGET_COMMITMENT requer colunas de contrato na Gold: "
            + ", ".join(missing)
        )

    from app.services.contract_settlement import allocate_pool, normalize_cloud_scope

    budget_rows = output[
        output["financial_effect"].astype("string").str.upper().eq("BUDGET_COMMITMENT")
    ]
    contract_ids: set[int] = set()
    for raw_id in budget_rows["financial_contract_id"].dropna().unique():
        try:
            contract_ids.add(int(float(raw_id)))
        except (TypeError, ValueError):
            raise ValueError(f"financial_contract_id inválido no forecast: {raw_id}")
    periods = sorted({str(value) for value in budget_rows["forecast_month"].dropna()})
    commitments = await _contract_budget_commitments(contract_ids, periods)
    actual_realized = await _actual_contract_realized(
        source=source,
        type_filter=type_filter,
        period_column=period_column,
        periods=periods,
    )
    pool_columns = [
        "forecast_month", "financial_contract_id", "cloud_origem", "id_orcamento",
    ]
    pool_indexes: dict[tuple[str, str, str, str], list[int]] = {}
    for index, row in budget_rows.iterrows():
        if any(pd.isna(row.get(column)) for column in pool_columns):
            raise ValueError(
                "Linha BUDGET_COMMITMENT sem mês, contrato, cloud ou ID orçamento "
                "não pode receber forecast de orçamento"
            )
        try:
            contract_id = str(int(float(row["financial_contract_id"])))
        except (TypeError, ValueError):
            raise ValueError(f"financial_contract_id inválido no forecast: {row['financial_contract_id']}")
        key = (
            str(row["forecast_month"]), contract_id,
            normalize_cloud_scope(row["cloud_origem"]), str(row["id_orcamento"]).strip(),
        )
        pool_indexes.setdefault(key, []).append(index)

    for key, indexes in pool_indexes.items():
        # Planning values are additive to actuals in the open month. Settlement
        # writes the full monthly commitment into realizado when consumption
        # exists, so only the unrecognized remainder belongs in the forecast.
        commitment = max(
            0.0,
            commitments.get(key, 0.0) - actual_realized.get(key, 0.0),
        )
        weights = output.loc[indexes, "realizado_cloud"].tolist()
        allocations = allocate_pool(commitment, weights)
        output.loc[indexes, "realizado"] = [float(value) for value in allocations]


def _build_series_context(daily: pd.Series, effective_cutoff: date) -> dict[str, Any]:
    """Summarize realized history per month for business-rule replication."""
    monthly = daily.groupby(daily.index.to_period("M")).sum()
    monthly_realized: dict[str, float] = {}
    for period, total in monthly.items():
        monthly_realized[f"{period.year:04d}{period.month:02d}"] = float(total)
    current_period = pd.Timestamp(effective_cutoff).to_period("M")
    previous_period = current_period - 1
    current_key = f"{effective_cutoff.year:04d}{effective_cutoff.month:02d}"
    last_closed_month_total = 0.0
    cursor = previous_period
    min_period = monthly.index.min() if len(monthly) else cursor
    while cursor >= min_period:
        total = float(monthly.get(cursor, 0.0))
        if total != 0.0:
            last_closed_month_total = total
            break
        cursor = cursor - 1
    current_month_realized = float(monthly.get(current_period, 0.0))
    if last_closed_month_total == 0.0 and len(monthly) > 0:
        found_prior = False
        cursor = previous_period
        while cursor >= min_period:
            if float(monthly.get(cursor, 0.0)) != 0.0:
                found_prior = True
                break
            cursor = cursor - 1
        if not found_prior:
            month_daily = daily[daily.index.to_period("M") == current_period]
            nonzero_days = int((month_daily != 0).sum())
            anchor_total = float(monthly.get(current_period, 0.0))
            last_day = calendar.monthrange(effective_cutoff.year, effective_cutoff.month)[1]
            month_fully_collected = effective_cutoff.day >= last_day or nonzero_days <= 3
            if anchor_total != 0.0 and month_fully_collected:
                last_closed_month_total = anchor_total
    return {
        "monthly_realized": monthly_realized,
        "last_closed_month_total": last_closed_month_total,
        "current_month_realized": current_month_realized,
        "current_key": current_key,
    }


def _month_offset(base_yyyymm: str, target_yyyymm: str) -> int:
    base_year, base_month = int(base_yyyymm[:4]), int(base_yyyymm[4:6])
    target_year, target_month = int(target_yyyymm[:4]), int(target_yyyymm[4:6])
    return (target_year - base_year) * 12 + (target_month - base_month)


def _replicated_month_value(
    *,
    behavior: str,
    forecast_month: str,
    current_month_key: str,
    last_closed_month_total: float,
    current_month_realized: float,
    growth_rate: float,
    lumpy: bool = False,
) -> float | None:
    offset = _month_offset(current_month_key, forecast_month)
    if behavior in {"RECURRING_MONTHLY", "RECURRING_UNTIL_END"} and forecast_month == current_month_key:
        # A monthly lump already represents the full current-month invoice.
        # Suppress it even when this series has no positive closed-month
        # history; requiring that history first lets a newly started recurring
        # subscription be forecast a second time in the month it was billed.
        return 0.0 if lumpy else None
    if last_closed_month_total <= 0:
        return None
    if behavior == "REPEAT_NEXT_MONTH":
        if offset != 1:
            return None
        return last_closed_month_total * ((1.0 + growth_rate) ** 1)
    if behavior in {"RECURRING_MONTHLY", "RECURRING_UNTIL_END"}:
        # Séries lumpy (MONTHLY_LUMP: fatura mensal/assinatura já lançada no
        # mês) não devem repetir o valor do mês fechado no mês aberto — a
        # fatura já cobre o mês inteiro e somar projeção duplicaria.
        # Uso diário (não lumpy — Azure/GCP/OCI) mantém o restante calculado
        # pelo motor (baseline × dias_restantes): estimativa_do_mês =
        # realizado parcial + forecast_restante.
        if offset <= 0:
            return None
        return last_closed_month_total * ((1.0 + growth_rate) ** offset)
    return None


def _rule_identity_mask(
    output: pd.DataFrame,
    rule: Any,
    output_by_lower: dict[str, str],
) -> tuple[pd.Series | None, bool, str | None]:
    """Build identity mask from identification_clauses (OR) or legacy column+values."""
    clauses = list(getattr(rule, "identification_clauses", None) or [])
    if clauses:
        clause_masks: list[pd.Series] = []
        for clause in clauses:
            if not isinstance(clause, dict):
                continue
            column = str(clause.get("column") or "")
            values = list(clause.get("values") or [])
            if not column or not values:
                continue
            identity_field = output_by_lower.get(column.lower())
            if identity_field is None:
                return None, False, column
            clause_masks.append(output[identity_field].astype(str).isin([str(value) for value in values]))
        if not clause_masks:
            return None, False, None
        combined = clause_masks[0]
        for item in clause_masks[1:]:
            combined |= item
        return combined, True, None

    identification_column = str(getattr(rule, "identification_column", "") or "")
    identification_values = list(getattr(rule, "identification_values", None) or [])
    if identification_values:
        identity_field = output_by_lower.get(identification_column.lower())
        if identity_field is None:
            return None, False, identification_column
        return (
            output[identity_field].astype(str).isin([str(value) for value in identification_values]),
            True,
            None,
        )
    return None, False, None


def _apply_forecast_business_rules(
    output: pd.DataFrame,
    business_rules: list[Any],
    series_context: dict[tuple[tuple[str, Any], ...], dict[str, Any]],
    key_cols: list[str],
    effective_cutoff: date,
) -> list[str]:
    applied_rules: list[str] = []
    output_by_lower = {str(column).lower(): column for column in output.columns}

    for rule in sorted(business_rules or [], key=lambda item: item.priority):
        if not getattr(rule, "active", True):
            continue
        if str(getattr(rule, "behavior", "") or "") in {
            "SOURCE_EXCLUDE_PREFIX", "SOURCE_MEAN_30D_PREFIX",
        }:
            continue
        mask = output["tipo"].eq("forecast")
        identity_mask, has_identity_filter, missing_field = _rule_identity_mask(output, rule, output_by_lower)
        if missing_field:
            applied_rules.append(f"{rule.name} (não aplicada: campo fora das dimensões)")
            continue
        if identity_mask is not None:
            mask &= identity_mask
            has_identity_filter = True
        for column, value in (rule.scope or {}).items():
            scope_field = output_by_lower.get(str(column).lower())
            if scope_field is None:
                raise ValueError(f"Regra '{rule.name}' usa escopo fora das dimensões da série: {column}")
            mask &= output[scope_field].astype(str).eq(str(value))
            has_identity_filter = True
        if not has_identity_filter:
            applied_rules.append(f"{rule.name} (não aplicada: sem identificação/escopo)")
            continue
        if rule.start_period:
            mask &= output["forecast_month"] >= rule.start_period
        if rule.end_period:
            mask &= output["forecast_month"] <= rule.end_period

        if not mask.any():
            applied_rules.append(f"{rule.name} (sem séries correspondentes)")
            continue

        behavior = str(getattr(rule, "behavior", "") or "")
        if behavior == "CHARGE_INTERVAL":
            continue
        if behavior == "CANCEL_ON_REFUND" or (
            str(getattr(rule, "charge_type", "") or "").lower() == "refund" and getattr(rule, "cancel_on_refund", False)
        ):
            output.loc[mask, "forecast_value"] = 0.0
            applied_rules.append(rule.name)
            continue

        if behavior == "REPLACE_VALUE":
            if rule.replacement_value is not None:
                output.loc[mask, "forecast_value"] = float(rule.replacement_value)
            if rule.growth_rate:
                periods = pd.to_numeric(output.loc[mask, "forecast_month"].str[-2:], errors="coerce").fillna(1)
                output.loc[mask, "forecast_value"] *= (1.0 + float(rule.growth_rate)) ** (periods - periods.min())
            applied_rules.append(rule.name)
            continue

        if behavior in {"REPEAT_NEXT_MONTH", "RECURRING_MONTHLY", "RECURRING_UNTIL_END"}:
            growth_rate = float(getattr(rule, "growth_rate", 0) or 0)
            for idx in output.index[mask]:
                row = output.loc[idx]
                key = _series_key({col: row.get(col) for col in key_cols}, key_cols)
                ctx = series_context.get(key)
                if not ctx:
                    continue
                replicated = _replicated_month_value(
                    behavior=behavior,
                    forecast_month=str(row["forecast_month"]),
                    current_month_key=str(ctx["current_key"]),
                    last_closed_month_total=float(ctx["last_closed_month_total"]),
                    current_month_realized=float(ctx["current_month_realized"]),
                    growth_rate=growth_rate,
                    lumpy=bool(ctx.get("lumpy")),
                )
                if replicated is not None:
                    output.at[idx, "forecast_value"] = replicated
            applied_rules.append(rule.name)
            continue

        applied_rules.append(f"{rule.name} (não aplicada: comportamento desconhecido)")

    return applied_rules


def _billing_period_end_expr(period_col: str) -> str:
    return f"last_day_of_month(date_parse(CAST({_q(period_col)} AS VARCHAR) || '01', '%Y%m%d'))"


def _expand_charge_interval(frame: pd.DataFrame, dims: list[str]) -> pd.DataFrame:
    """Spread charge totals evenly across [charge_start, charge_end] per row."""
    if frame.empty:
        return pd.DataFrame(columns=[*dims, "forecast_day", "value"])
    working = frame.copy()
    working["charge_start"] = pd.to_datetime(working["charge_start"], errors="coerce")
    working["charge_end"] = pd.to_datetime(working["charge_end"], errors="coerce")
    working["value"] = pd.to_numeric(working["value"], errors="coerce").fillna(0.0)
    rows: list[dict[str, Any]] = []
    for _, row in working.iterrows():
        start = row["charge_start"]
        if pd.isna(start):
            continue
        end = row["charge_end"] if pd.notna(row["charge_end"]) else start
        if end < start:
            end = start
        days = pd.date_range(start.normalize(), end.normalize(), freq="D")
        if len(days) == 0:
            days = pd.DatetimeIndex([start.normalize()])
        share = float(row["value"]) / len(days)
        base = {col: row[col] for col in dims}
        for day in days:
            rows.append({**base, "forecast_day": day, "value": share})
    if not rows:
        return pd.DataFrame(columns=[*dims, "forecast_day", "value"])
    expanded = pd.DataFrame(rows)
    group_cols = [*dims, "forecast_day"]
    return expanded.groupby(group_cols, dropna=False, as_index=False)["value"].sum()


def _charge_interval_end_expr(date_expr: str, charge_end_col: str | None, period: str | None) -> str:
    if charge_end_col:
        end_expr = gold_date_expr(charge_end_col, period)
    else:
        end_expr = "NULL"
    if period:
        period_end = _billing_period_end_expr(period)
        return f"COALESCE({end_expr}, {period_end}, {date_expr})"
    return f"COALESCE({end_expr}, {date_expr})"


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _lit(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _source_exclusion_filter(
    business_rules: list[Any], metadata: dict[str, Any], period_col: str | None,
    *, behaviors: set[str] | None = None,
) -> tuple[str | None, list[str]]:
    """Build safe Gold predicates for exclusions applied before aggregation."""
    behaviors = behaviors or {"SOURCE_EXCLUDE_PREFIX"}
    columns = {
        str(item.get("name") or "").lower(): str(item.get("name") or "")
        for item in (metadata.get("columns") or []) if item.get("name")
    }
    exclusions: list[str] = []
    applied: list[str] = []
    for rule in sorted(business_rules or [], key=lambda item: item.priority):
        if not getattr(rule, "active", True) or getattr(rule, "behavior", "") not in behaviors:
            continue
        clauses = list(getattr(rule, "identification_clauses", None) or []) or [{
            "column": getattr(rule, "identification_column", ""),
            "values": list(getattr(rule, "identification_values", None) or []),
        }]
        groups: list[str] = []
        for clause in clauses:
            if not isinstance(clause, dict):
                continue
            actual = columns.get(str(clause.get("column") or "").lower())
            values = [str(value) for value in (clause.get("values") or []) if str(value)]
            if actual and values:
                groups.append("(" + " OR ".join(
                    f"lower(COALESCE(CAST({_q(actual)} AS VARCHAR), '')) LIKE lower({_lit(value)}) || '%'"
                    for value in values
                ) + ")")
        if not groups:
            continue
        predicates = ["(" + " OR ".join(groups) + ")"]
        valid = True
        for requested, value in (getattr(rule, "scope", None) or {}).items():
            actual = columns.get(str(requested).lower())
            if not actual:
                valid = False
                break
            predicates.append(f"CAST({_q(actual)} AS VARCHAR) = {_lit(value)}")
        if not valid:
            continue
        actual_period = columns.get(str(period_col or "").lower()) if period_col else None
        if actual_period and getattr(rule, "start_period", None):
            predicates.append(f"CAST({_q(actual_period)} AS VARCHAR) >= {_lit(rule.start_period)}")
        if actual_period and getattr(rule, "end_period", None):
            predicates.append(f"CAST({_q(actual_period)} AS VARCHAR) <= {_lit(rule.end_period)}")
        exclusions.append("(" + " AND ".join(predicates) + ")")
        applied.append(str(getattr(rule, "name", "SOURCE_EXCLUDE_PREFIX")))
    return (" OR ".join(exclusions) if exclusions else None), applied


def _and_filters(*filters: str | None) -> str | None:
    parts = [f"({item})" for item in filters if item]
    return " AND ".join(parts) if parts else None


def gold_date_expr(date_col: str, period_col: str | None = None, *, table_alias: str | None = None) -> str:
    """Parse Gold charge dates across Azure slash formats and GCP ISO-8601 timestamps.

    GCP ChargePeriodStart is stored as varchar ``YYYY-MM-DDTHH:MM:SS+00:00``.
    ``TRY_CAST AS DATE`` and ``date_parse(..., '%Y-%m-%d')`` both fail on that
    shape, which previously dropped every GCP series from the forecast.
    """
    def ident(name: str) -> str:
        quoted = _q(name)
        return f"{table_alias}.{quoted}" if table_alias else quoted

    if period_col and date_col == period_col:
        return f"TRY_CAST(date_parse(CAST({ident(period_col)} AS VARCHAR) || '01', '%Y%m%d') AS DATE)"
    raw_date = f"CAST({ident(date_col)} AS VARCHAR)"
    # Do not use TRY_CAST for slash-delimited strings: Trino accepts them
    # using a locale-dependent parser before we can validate the billing
    # period. Limit it strictly to ISO dates and ISO-8601 timestamps.
    iso_date = (
        f"CASE WHEN regexp_like({raw_date}, '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}') "
        f"THEN COALESCE("
        f"TRY_CAST({ident(date_col)} AS DATE), "
        f"TRY(date_parse({raw_date}, '%Y-%m-%d')), "
        f"TRY(CAST(from_iso8601_timestamp({raw_date}) AS DATE)), "
        f"TRY(date_parse(substr({raw_date}, 1, 10), '%Y-%m-%d'))"
        f") END"
    )
    mdy_date = f"TRY(date_parse({raw_date}, '%m/%d/%Y'))"
    dmy_date = f"TRY(date_parse({raw_date}, '%d/%m/%Y'))"
    if not period_col:
        return f"COALESCE({iso_date}, {mdy_date}, {dmy_date})"
    # Azure Cost Management uses MM/DD/YYYY. Treat that as the primary
    # non-ISO format, and when a billing period exists use it to resolve
    # ambiguous day/month values deterministically. For example,
    # ``08/01/2026`` in billing period 202608 is 1 Aug, never 8 Jan.
    period_number = f"TRY_CAST(CAST({ident(period_col)} AS VARCHAR) AS INTEGER)"
    return (
        f"COALESCE({iso_date}, "
        f"CASE WHEN year({mdy_date}) * 100 + month({mdy_date}) = {period_number} THEN {mdy_date} END, "
        f"CASE WHEN year({dmy_date}) * 100 + month({dmy_date}) = {period_number} THEN {dmy_date} END, "
        f"{mdy_date}, {dmy_date})"
    )


async def fetch_metadata(table_name: str, namespace: str = "gold") -> dict[str, Any]:
    schema_rows = await trino_execute(f"DESCRIBE iceberg.{namespace}.{table_name}")
    columns = []
    for row in schema_rows:
        name = str(row.get("Column") or row.get("column_name") or row.get("column"))
        typ = str(row.get("Type") or row.get("data_type") or "")
        lower = typ.lower()
        columns.append({"name": name, "type": typ, "numeric": any(x in lower for x in ("double", "decimal", "real", "float", "int", "bigint")), "temporal": any(x in lower for x in ("date", "timestamp"))})
    names = {c["name"].lower(): c["name"] for c in columns}
    numeric = [c["name"] for c in columns if c["numeric"]]
    dates = [c["name"] for c in columns if c["temporal"]]
    date_priority = {"date": 0, "chargedate": 1, "chargeperiodstart": 2, "billingperiodstart": 3, "usage_date": 4, "chargeperiodend": 5, "billingperiodend": 6}
    date_like = sorted([c["name"] for c in columns if c["name"].lower() in date_priority], key=lambda x: date_priority[x.lower()])
    if date_like:
        dates = date_like + [x for x in dates if x not in date_like]
    default_measure = names.get("realizado")
    if not default_measure:
        default_measure = next((n for n in numeric if n.lower() == "effectivecost"), None)
    period = names.get("_billing_period")
    date_column = dates[0] if dates else period
    dimensions = [c["name"] for c in columns if not c["numeric"] and c["name"] not in {date_column, period}]
    # Do not encode any FOCUS/provider column names here. A destination model
    # may represent another source and must define its own series dimensions.
    # The UI exposes every discovered dimension and the user chooses the set.
    recommended = []
    return {"columns": columns, "measures": numeric, "date_columns": dates, "period_columns": [period] if period else [], "dimensions": dimensions, "recommended_dimensions": recommended, "default_measure": default_measure, "default_date": date_column, "default_period": period}


def _resolve_connection_columns(metadata: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """Return (cloud_origem, source_connection_id) column names when present in Gold."""
    if not metadata:
        return None, None
    names = {c["name"].lower(): c["name"] for c in metadata.get("columns", [])}
    return names.get("cloud_origem"), names.get("source_connection_id")


def _merge_allocation_rules(*rule_lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for rules in rule_lists:
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            key = (
                rule.get("connection_id"),
                rule.get("connection_code"),
                rule.get("charge_end_column"),
                rule.get("period_allocation"),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(rule)
    return merged


def _allocation_rules_from_business_rules(
    business_rules: list[Any],
    cloud_col: str | None,
    id_col: str | None,
    config: Any,
) -> list[dict[str, Any]]:
    """Translate CHARGE_INTERVAL business rules into per-connection fetch rules."""
    rules: list[dict[str, Any]] = []
    for rule in sorted(business_rules or [], key=lambda item: item.priority):
        if not getattr(rule, "active", True):
            continue
        if str(getattr(rule, "behavior", "") or "") != "CHARGE_INTERVAL":
            continue
        meta = getattr(config, "gold_metadata", None) or {}
        names = {c["name"].lower(): c["name"] for c in meta.get("columns", [])}
        charge_end = getattr(rule, "reference_column", None) or getattr(config, "charge_end_column", None)
        if charge_end:
            charge_end = names.get(str(charge_end).lower(), charge_end)
        else:
            charge_end = names.get("chargeperiodend") or "ChargePeriodEnd"
        targets: list[tuple[str | None, int | None]] = []
        identification_column = str(getattr(rule, "identification_column", "") or "")
        identification_values = list(getattr(rule, "identification_values", None) or [])
        if identification_column and identification_values:
            column_lower = identification_column.lower()
            if cloud_col and column_lower == cloud_col.lower():
                targets.extend((str(value), None) for value in identification_values)
            elif id_col and column_lower == id_col.lower():
                targets.extend((None, int(value)) for value in identification_values)
        for column, value in (getattr(rule, "scope", None) or {}).items():
            column_lower = str(column).lower()
            if cloud_col and column_lower == cloud_col.lower():
                targets.append((str(value), None))
            elif id_col and column_lower == id_col.lower():
                targets.append((None, int(value)))
        for conn_code, conn_id in targets:
            rules.append(
                {
                    "connection_code": conn_code,
                    "connection_id": conn_id,
                    "period_allocation": "charge_interval",
                    "charge_end_column": charge_end,
                    "_from_business_rule": getattr(rule, "name", ""),
                }
            )
    return rules


def _normalize_allocation_rules(config: Any) -> list[dict[str, Any]]:
    """Return explicit per-connection rules, or a legacy global charge_interval rule."""
    rules = list(getattr(config, "connection_allocation_rules", None) or [])
    if rules:
        return [rule for rule in rules if isinstance(rule, dict)]
    if getattr(config, "period_allocation", "none") == "charge_interval":
        return [{
            "period_allocation": "charge_interval",
            "charge_end_column": getattr(config, "charge_end_column", None),
            "_global": True,
        }]
    return []


def _connection_rule_filter(
    rules: list[dict[str, Any]],
    cloud_col: str | None,
    id_col: str | None,
    *,
    negate: bool = False,
) -> str | None:
    """Build a Trino WHERE fragment matching configured connections."""
    if not rules or all(rule.get("_global") for rule in rules):
        return None
    clauses: list[str] = []
    for rule in rules:
        parts: list[str] = []
        code = rule.get("connection_code")
        conn_id = rule.get("connection_id")
        if code and cloud_col:
            parts.append(f"CAST({_q(cloud_col)} AS VARCHAR) = {_lit(code)}")
        if conn_id is not None and id_col:
            parts.append(f"TRY_CAST({_q(id_col)} AS BIGINT) = {int(conn_id)}")
        if parts:
            clauses.append("(" + " OR ".join(parts) + ")")
    if not clauses:
        return None
    joined = " OR ".join(clauses)
    return f"NOT ({joined})" if negate else f"({joined})"


def _series_matches_charge_interval(
    key_values: dict[str, Any],
    rules: list[dict[str, Any]],
    cloud_col: str | None,
    id_col: str | None,
) -> bool:
    if not rules:
        return False
    if any(rule.get("_global") for rule in rules):
        return True
    for rule in rules:
        if rule.get("period_allocation", "charge_interval") != "charge_interval":
            continue
        code = rule.get("connection_code")
        conn_id = rule.get("connection_id")
        if code and cloud_col and str(key_values.get(cloud_col) or "") == str(code):
            return True
        if conn_id is not None and id_col:
            raw = key_values.get(id_col)
            if raw is not None and str(raw) == str(conn_id):
                return True
    return False


def _rule_charge_end_column(rule: dict[str, Any], config: Any) -> str | None:
    return rule.get("charge_end_column") or getattr(config, "charge_end_column", None)


def validate_connection_allocation_rules(
    rules: list[Any],
    metadata: dict[str, Any],
    period_col: str | None,
) -> list[str]:
    names = {c["name"] for c in metadata["columns"]}
    cloud_col, id_col = _resolve_connection_columns(metadata)
    errors: list[str] = []
    for index, raw in enumerate(rules or []):
        if not isinstance(raw, dict):
            errors.append(f"Regra de conexão #{index + 1} inválida")
            continue
        if raw.get("period_allocation", "charge_interval") != "charge_interval":
            errors.append(f"Regra de conexão #{index + 1}: alocação não suportada")
            continue
        if not raw.get("connection_code") and raw.get("connection_id") is None:
            errors.append(f"Regra de conexão #{index + 1}: informe connection_code ou connection_id")
        if raw.get("connection_code") and not cloud_col:
            errors.append(f"Regra de conexão #{index + 1}: Gold não possui cloud_origem")
        if raw.get("connection_id") is not None and not id_col:
            errors.append(f"Regra de conexão #{index + 1}: Gold não possui source_connection_id")
        charge_end = raw.get("charge_end_column")
        if charge_end and charge_end not in names:
            errors.append(f"Regra de conexão #{index + 1}: coluna de fim inválida: {charge_end}")
        if not charge_end and not period_col:
            errors.append(f"Regra de conexão #{index + 1}: charge_interval exige charge_end_column ou period_column")
    return errors


def _billing_period_values(history_start: date, effective_cutoff: date, minimum_months: int) -> list[str]:
    """Months to load when Gold is partitioned by billing period.

    Invoice-style sources (IBM FOCUS) stamp the whole month on day 1.  A
    narrow ``history_start`` anchored on the GCP daily cutoff excludes those
    rows even when the billing-period partition is included in the loop.
    """
    period_cursor = history_start.replace(day=1)
    extended = effective_cutoff.replace(day=1)
    for _ in range(max(0, minimum_months - 1)):
        extended = (extended.replace(day=1) - timedelta(days=1)).replace(day=1)
    if extended < period_cursor:
        period_cursor = extended
    period_values: list[str] = []
    while period_cursor <= effective_cutoff.replace(day=1):
        period_values.append(period_cursor.strftime("%Y%m"))
        period_cursor = (period_cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return period_values


def _charge_interval_rules(allocation_rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        rule for rule in allocation_rules
        if rule.get("period_allocation") == "charge_interval"
    ]


async def _fetch_invoice_cloud_cutoffs(
    *,
    source: str,
    type_filter: str,
    period_col: str,
    charge_end_col: str,
    cloud_col: str,
    connection_filter: str | None,
    excluded_days: int,
) -> dict[str, date]:
    """Cutoff for invoice-style clouds from charge end, not ChargePeriodStart.

    IBM FOCUS stamps the full month on day 1. Using MAX(ChargePeriodStart) keeps
    the anchor in the prior month even after the invoice partition is loaded.
    """
    extra = f" AND {connection_filter}" if connection_filter else ""
    charge_end_expr = gold_date_expr(charge_end_col, period_col)
    period_end = _billing_period_end_expr(period_col)
    sql = f"""
        WITH latest_period AS (
            SELECT
                CAST({_q(cloud_col)} AS VARCHAR) AS latest_cloud,
                MAX(CAST({_q(period_col)} AS VARCHAR)) AS max_period
            FROM {source}
            WHERE {type_filter}{extra}
            GROUP BY 1
        )
        SELECT
            CAST(g.{_q(cloud_col)} AS VARCHAR) AS cloud_origem,
            MAX(COALESCE({charge_end_expr}, {period_end})) AS collected_through
        FROM {source} g
        INNER JOIN latest_period lp
            ON CAST(g.{_q(cloud_col)} AS VARCHAR) = lp.latest_cloud
           AND CAST(g.{_q(period_col)} AS VARCHAR) = lp.max_period
        WHERE {type_filter}{extra}
        GROUP BY 1
    """
    rows = await trino_execute(sql, timeout_s=120)
    cutoffs: dict[str, date] = {}
    for row in rows:
        cloud = str(row.get("cloud_origem") or "").strip()
        collected = pd.to_datetime(row.get("collected_through"), errors="coerce")
        if not cloud or pd.isna(collected):
            continue
        cutoffs[cloud] = collected.date() - timedelta(days=excluded_days)
    return cutoffs


async def _apply_interval_cloud_cutoff_overrides(
    cloud_cutoffs: dict[str, date],
    *,
    allocation_rules: list[dict[str, Any]],
    config: Any,
    source: str,
    type_filter: str,
    period_col: str | None,
    cloud_col: str | None,
    id_col: str | None,
    excluded_days: int,
) -> None:
    """Refresh cutoffs for every connection enabled in interval allocation rules."""
    interval_rules = _charge_interval_rules(allocation_rules)
    if not interval_rules or not period_col or not cloud_col:
        return

    global_rules = [rule for rule in interval_rules if rule.get("_global")]
    per_connection_rules = [rule for rule in interval_rules if not rule.get("_global")]

    if global_rules:
        charge_end_col = _rule_charge_end_column(global_rules[0], config)
        if charge_end_col:
            cloud_cutoffs.update(
                await _fetch_invoice_cloud_cutoffs(
                    source=source,
                    type_filter=type_filter,
                    period_col=period_col,
                    charge_end_col=charge_end_col,
                    cloud_col=cloud_col,
                    connection_filter=None,
                    excluded_days=excluded_days,
                )
            )

    if not per_connection_rules:
        return

    grouped: dict[str, list[dict[str, Any]]] = {}
    for rule in per_connection_rules:
        charge_end_col = _rule_charge_end_column(rule, config)
        if not charge_end_col:
            continue
        grouped.setdefault(charge_end_col, []).append(rule)

    for charge_end_col, rules in grouped.items():
        connection_filter = _connection_rule_filter(rules, cloud_col, id_col, negate=False)
        if not connection_filter:
            continue
        cloud_cutoffs.update(
            await _fetch_invoice_cloud_cutoffs(
                source=source,
                type_filter=type_filter,
                period_col=period_col,
                charge_end_col=charge_end_col,
                cloud_col=cloud_col,
                connection_filter=connection_filter,
                excluded_days=excluded_days,
            )
        )


async def _fetch_cloud_cutoffs(
    *,
    source: str,
    type_filter: str,
    date_expr: str,
    cloud_col: str,
    source_connection_col: str | None,
    period_col: str | None,
    excluded_days: int,
) -> dict[str, date]:
    """Return effective cutoffs per cloud, aligned to each source connection.

    Historical rows may contain an old cloud label after a connection was
    renamed or normalized. Using that label's stale maximum would keep a
    retired series forecasting alongside its replacement. A connection is
    ingested as one source, so all its cloud labels share the latest collected
    date while each series still forecasts only its own available history.
    """
    connection_expr = (
        f"CAST({_q(source_connection_col)} AS VARCHAR)"
        if source_connection_col else "CAST(NULL AS VARCHAR)"
    )
    if period_col:
        sql = f"""
            WITH latest_period AS (
                SELECT
                    CAST({_q(cloud_col)} AS VARCHAR) AS latest_cloud,
                    {connection_expr} AS latest_connection,
                    MAX(CAST({_q(period_col)} AS VARCHAR)) AS max_period
                FROM {source}
                WHERE {type_filter}
                GROUP BY 1, 2
            )
            SELECT
                CAST(g.{_q(cloud_col)} AS VARCHAR) AS cloud_origem,
                {f'CAST(g.{_q(source_connection_col)} AS VARCHAR)' if source_connection_col else 'CAST(NULL AS VARCHAR)'} AS source_connection_id,
                MAX({date_expr}) AS collected_through
            FROM {source} g
            INNER JOIN latest_period lp
                ON CAST(g.{_q(cloud_col)} AS VARCHAR) = lp.latest_cloud
               AND {f'CAST(g.{_q(source_connection_col)} AS VARCHAR)' if source_connection_col else 'CAST(NULL AS VARCHAR)'} IS NOT DISTINCT FROM lp.latest_connection
               AND CAST(g.{_q(period_col)} AS VARCHAR) = lp.max_period
            WHERE {type_filter}
            GROUP BY 1, 2
        """
    else:
        sql = f"""
            SELECT
                CAST({_q(cloud_col)} AS VARCHAR) AS cloud_origem,
                {connection_expr} AS source_connection_id,
                MAX({date_expr}) AS collected_through
            FROM {source}
            WHERE {type_filter}
            GROUP BY 1, 2
        """
    rows = await trino_execute(sql, timeout_s=120)
    resolved: list[tuple[str, str | None, date]] = []
    connection_cutoffs: dict[str, date] = {}
    for row in rows:
        cloud = str(row.get("cloud_origem") or "").strip()
        collected = pd.to_datetime(row.get("collected_through"), errors="coerce")
        if not cloud or pd.isna(collected):
            continue
        cutoff = collected.date() - timedelta(days=excluded_days)
        connection_id = str(row.get("source_connection_id") or "").strip() or None
        resolved.append((cloud, connection_id, cutoff))
        if connection_id:
            connection_cutoffs[connection_id] = max(
                cutoff, connection_cutoffs.get(connection_id, cutoff),
            )
    cutoffs: dict[str, date] = {}
    for cloud, connection_id, cutoff in resolved:
        if connection_id:
            cutoff = max(cutoff, connection_cutoffs[connection_id])
        cutoffs[cloud] = max(cutoff, cutoffs.get(cloud, cutoff))
    return cutoffs


def _cloud_effective_cutoff(
    cloud_cutoffs: dict[str, date],
    cloud_col: str | None,
    key_values: dict[str, Any],
    fallback: date,
) -> date:
    if not cloud_col:
        return fallback
    cloud_name = str(key_values.get(cloud_col) or "").strip()
    if cloud_name and cloud_name in cloud_cutoffs:
        return cloud_cutoffs[cloud_name]
    return fallback


async def _fetch_period_records(
    *,
    source: str,
    type_filter: str,
    period: str | None,
    history_start: date,
    effective_cutoff: date,
    dims: list[str],
    measure: str,
    date_expr: str,
    mode: str,
    charge_end_col: str | None,
    period_col: str | None,
    connection_filter: str | None,
    exclusion_filter: str | None = None,
    only_exclusions: bool = False,
    exclusion_sign: int = -1,
    minimum_monthly_points: int = 3,
) -> list[dict[str, Any]]:
    select_dims = ", ".join(_q(x) for x in dims)
    group_prefix = (select_dims + ", ") if select_dims else ""
    date_filter = f"{date_expr} BETWEEN DATE {_lit(history_start.isoformat())} AND DATE {_lit(effective_cutoff.isoformat())}"
    if mode == "interval":
        end_expr = _charge_interval_end_expr(date_expr, charge_end_col, period_col)
        select_sql = f"SELECT {group_prefix}{date_expr} AS charge_start, {end_expr} AS charge_end, SUM(TRY_CAST({_q(measure)} AS DOUBLE)) AS value FROM {source}"
        excluded_select_sql = f"SELECT {group_prefix}{date_expr} AS charge_start, {end_expr} AS charge_end, {exclusion_sign} * SUM(TRY_CAST({_q(measure)} AS DOUBLE)) AS value FROM {source}"
        group_sql = f" GROUP BY {group_prefix}{date_expr}, {end_expr} ORDER BY charge_start"
    else:
        select_sql = f"SELECT {group_prefix}{date_expr} AS forecast_day, SUM(TRY_CAST({_q(measure)} AS DOUBLE)) AS value FROM {source}"
        excluded_select_sql = f"SELECT {group_prefix}{date_expr} AS forecast_day, {exclusion_sign} * SUM(TRY_CAST({_q(measure)} AS DOUBLE)) AS value FROM {source}"
        group_sql = f" GROUP BY {group_prefix}{date_expr} ORDER BY forecast_day"
    extra = f" AND {connection_filter}" if connection_filter else ""
    records: list[dict[str, Any]] = []
    if period_col:
        # Daily facts still need the exact history boundary after partition
        # pruning. Invoice intervals are the sole exception: their full-month
        # amount can be stamped on day one and is expanded downstream.
        daily_date_scope = "" if mode == "interval" else f" AND {date_filter}"
        period_values = _billing_period_values(history_start, effective_cutoff, minimum_monthly_points)
        for billing_period in period_values:
            # Partition already scopes the invoice month; do not also filter
            # ChargePeriodStart or July 1 IBM rows disappear from the window.
            sql = (
                f"{select_sql} WHERE {type_filter} AND CAST({_q(period_col)} AS VARCHAR) = {_lit(billing_period)}"
                f"{daily_date_scope}{extra}{group_sql}"
            )
            if not only_exclusions:
                records.extend(await trino_execute(sql, timeout_s=300))
            if exclusion_filter:
                excluded_sql = (
                    f"{excluded_select_sql} WHERE {type_filter} AND CAST({_q(period_col)} AS VARCHAR) = {_lit(billing_period)}"
                    f"{daily_date_scope} AND ({exclusion_filter}){group_sql}"
                )
                records.extend(await trino_execute(excluded_sql, timeout_s=300))
    else:
        sql = f"{select_sql} WHERE {type_filter} AND {date_filter}{extra}{group_sql}"
        records = [] if only_exclusions else await trino_execute(sql, timeout_s=300)
        if exclusion_filter:
            excluded_sql = (
                f"{excluded_select_sql} WHERE {type_filter} AND {date_filter} "
                f"AND ({exclusion_filter}){group_sql}"
            )
            records.extend(await trino_execute(excluded_sql, timeout_s=300))
    return records


async def _fetch_period_records_by_cloud(
    *,
    cloud_col: str | None,
    clouds: dict[str, date],
    connection_filter: str | None,
    shard_col: str | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Read one source cloud at a time to cap Trino aggregation cardinality.

    The wide FinOps configuration groups by many dimensions. On a single-node
    Trino, aggregating every origin in one hash table can restart the JVM. The
    resulting records are additive, so partitioning by ``cloud_origem`` is
    semantically identical while keeping every query bounded.
    """
    cloud_names = sorted(name for name in clouds if name)
    if not cloud_col or not cloud_names:
        return await _fetch_period_records(connection_filter=connection_filter, **kwargs)
    cloud_expr = f"CAST({_q(cloud_col)} AS VARCHAR)"
    records: list[dict[str, Any]] = []
    # Shards must be discovered in the same window that will be aggregated.
    # Previously this DISTINCT scan had no period/date predicate: every
    # historical SubAccountName of a cloud produced three more aggregation
    # queries, even when it had no data in the forecast window.
    period_col = kwargs.get("period_col")
    global_history_start = kwargs["history_start"]
    global_effective_cutoff = kwargs["effective_cutoff"]
    # Preserve the configured lookback duration, but anchor it at each
    # cloud's own cutoff.  Otherwise an origin whose billing data closes
    # earlier can be fetched only after its cutoff and have every fact removed
    # later by the per-series cutoff (e.g. Azure Marketplace monthly invoices).
    history_span = global_effective_cutoff - global_history_start
    mode = kwargs.get("mode")
    for cloud in cloud_names:
        cloud_effective_cutoff = clouds[cloud]
        cloud_history_start = cloud_effective_cutoff - history_span
        cloud_kwargs = {
            **kwargs,
            "history_start": cloud_history_start,
            "effective_cutoff": cloud_effective_cutoff,
        }
        if period_col:
            periods = _billing_period_values(
                cloud_history_start,
                cloud_effective_cutoff,
                int(kwargs.get("minimum_monthly_points") or 1),
            )
            period_values = ", ".join(_lit(value) for value in periods)
            shard_scope = f" AND CAST({_q(period_col)} AS VARCHAR) IN ({period_values})"
            # Monthly IBM invoices are allocated by their charge interval, so a
            # date predicate would incorrectly drop the invoice stamped on day 1.
            # Daily sources retain the precise 35-day boundary.
            if mode != "interval":
                shard_scope += (
                    f" AND {kwargs['date_expr']} BETWEEN DATE {_lit(cloud_history_start.isoformat())}"
                    f" AND DATE {_lit(cloud_effective_cutoff.isoformat())}"
                )
        else:
            shard_scope = (
                f" AND {kwargs['date_expr']} BETWEEN DATE {_lit(cloud_history_start.isoformat())}"
                f" AND DATE {_lit(cloud_effective_cutoff.isoformat())}"
            )
        cloud_filter = _and_filters(connection_filter, f"{cloud_expr} = {_lit(cloud)}")
        shard_values: list[str | None] = [None]
        if shard_col:
            shard_rows = await trino_execute(
                f"SELECT DISTINCT CAST({_q(shard_col)} AS VARCHAR) AS shard "
                f"FROM {kwargs['source']} WHERE {kwargs['type_filter']} AND ({cloud_filter}){shard_scope}",
                timeout_s=120,
            )
            shard_values = [row.get("shard") for row in shard_rows]
            # No shard values means no realized rows for this cloud/filter.
            if not shard_values:
                continue
        shard_batches = [
            shard_values[index:index + _SHARD_BATCH_SIZE]
            for index in range(0, len(shard_values), _SHARD_BATCH_SIZE)
        ]
        for shard_batch in shard_batches:
            shard_filter = cloud_filter
            if shard_col:
                shard_expr = f"CAST({_q(shard_col)} AS VARCHAR)"
                non_null_shards = [str(shard) for shard in shard_batch if shard is not None]
                shard_predicates: list[str] = []
                if non_null_shards:
                    shard_predicates.append(
                        f"{shard_expr} IN ({', '.join(_lit(shard) for shard in non_null_shards)})"
                    )
                if any(shard is None for shard in shard_batch):
                    shard_predicates.append(f"{shard_expr} IS NULL")
                shard_filter = _and_filters(
                    cloud_filter,
                    f"({' OR '.join(shard_predicates)})",
                )
            records.extend(await _fetch_period_records(
                connection_filter=shard_filter,
                **cloud_kwargs,
            ))
    # Preserve records with absent or unforeseen origins rather than silently
    # dropping them when the cutoff discovery did not return a cloud key.
    known = ", ".join(_lit(cloud) for cloud in cloud_names)
    records.extend(await _fetch_period_records(
        connection_filter=_and_filters(connection_filter, f"({cloud_expr} IS NULL OR {cloud_expr} NOT IN ({known}))"),
        **kwargs,
    ))
    return records


def validate_config(config: Any, metadata: dict[str, Any]) -> list[str]:
    names = {c["name"] for c in metadata["columns"]}
    errors = []
    if config.measure_column not in names or config.measure_column not in metadata["measures"]:
        errors.append(f"Medida numérica inválida: {config.measure_column}")
    if config.date_column not in names and config.period_column not in names:
        errors.append("Nenhuma coluna de data/período válida foi configurada")
    for dimension in config.dimensions or []:
        if dimension not in names:
            errors.append(f"Dimensão inválida: {dimension}")
    if config.horizon_months < 1 or config.horizon_months > 36:
        errors.append("O horizonte deve estar entre 1 e 36 meses")
    if not config.enabled_windows or any(int(w) not in (7, 14, 28) for w in config.enabled_windows):
        errors.append("As janelas permitidas são 7, 14 e 28 dias")
    if config.model_mode not in {"AUTO_BACKTEST", "WEIGHTED", "MEAN_7", "MEAN_14", "MEAN_28"}:
        errors.append("Estratégia matemática inválida")
    allocation = getattr(config, "period_allocation", "none") or "none"
    if allocation not in {"none", "charge_interval"}:
        errors.append("Alocação de período inválida")
    explicit_rules = list(getattr(config, "connection_allocation_rules", None) or [])
    if explicit_rules:
        errors.extend(validate_connection_allocation_rules(explicit_rules, metadata, config.period_column))
    elif allocation == "charge_interval":
        charge_end = getattr(config, "charge_end_column", None)
        if charge_end and charge_end not in names:
            errors.append(f"Coluna de fim de cobrança inválida: {charge_end}")
        if not charge_end and not config.period_column:
            errors.append("charge_interval exige charge_end_column ou period_column para fallback")
    return errors


async def run_forecast(config: Any, adjustments: list[Any], cutoff: date | None = None, progress=None, business_rules: list[Any] | None = None, result_table: str | None = None, simulated_as_of: date | None = None) -> dict[str, Any]:
    table = config.destination_physical_name
    namespace = getattr(config, "source_namespace", "silver")
    dims = list(config.dimensions or [])
    date_col = config.date_column
    measure = config.measure_column
    period = config.period_column
    date_expr = gold_date_expr(date_col, period)
    # Historical Gold rows produced before ``tipo`` was introduced are still
    # realized facts.  Include NULL for backward compatibility; every new
    # reprocess writes the explicit value below, while forecast/planning rows
    # live in a separate table and are never read here.
    type_filter = (
        f"({_q(config.type_column)} = 'realizado' OR {_q(config.type_column)} IS NULL)"
        if getattr(config, "has_type_column", False) else "TRUE"
    )
    source = f"iceberg.{namespace}.{table}"
    excluded_days = max(0, int(getattr(config, "cutoff_days", 2) or 0))
    metadata = getattr(config, "gold_metadata", None) or {}
    source_exclusion, source_rule_names = _source_exclusion_filter(
        business_rules or [], metadata, period,
        behaviors={"SOURCE_EXCLUDE_PREFIX", "SOURCE_MEAN_30D_PREFIX"},
    )
    mean_30d_source_filter, mean_30d_rule_names = _source_exclusion_filter(
        business_rules or [], metadata, period,
        behaviors={"SOURCE_MEAN_30D_PREFIX"},
    )
    cloud_col, id_col = _resolve_connection_columns(metadata)
    allocation_rules = _merge_allocation_rules(
        _normalize_allocation_rules(config),
        _allocation_rules_from_business_rules(business_rules, cloud_col, id_col, config),
    )
    cloud_cutoffs: dict[str, date] = {}
    if simulated_as_of is not None:
        # Simulation ("como se estivéssemos no dia X"): no data after the
        # simulated collection date may be used.  The cutoff is derived from
        # the simulated date instead of the real Gold maximum.
        collected_through = simulated_as_of
        effective_cutoff = simulated_as_of - timedelta(days=excluded_days)
    else:
        if cloud_col:
            cloud_cutoffs = await _fetch_cloud_cutoffs(
                source=source,
                type_filter=type_filter,
                date_expr=date_expr,
                cloud_col=cloud_col,
                source_connection_col=id_col,
                period_col=period,
                excluded_days=excluded_days,
            )
            await _apply_interval_cloud_cutoff_overrides(
                cloud_cutoffs,
                allocation_rules=allocation_rules,
                config=config,
                source=source,
                type_filter=type_filter,
                period_col=period,
                cloud_col=cloud_col,
                id_col=id_col,
                excluded_days=excluded_days,
            )
        if cloud_cutoffs:
            collected_through = max(
                cutoff + timedelta(days=excluded_days) for cutoff in cloud_cutoffs.values()
            )
            effective_cutoff = max(cloud_cutoffs.values())
        else:
            # Fallback when Gold has no cloud_origem populated.
            latest_period_filter = (
                f" AND CAST({_q(period)} AS VARCHAR) = (SELECT MAX(CAST({_q(period)} AS VARCHAR)) FROM {source} WHERE {type_filter})"
                if period else ""
            )
            latest_sql = f"SELECT MAX({date_expr}) AS collected_through FROM {source} WHERE {type_filter}{latest_period_filter}"
            latest_records = await trino_execute(latest_sql, timeout_s=120)
            latest_value = latest_records[0].get("collected_through") if latest_records else None
            collected_timestamp = pd.to_datetime(latest_value, errors="coerce")
            if pd.isna(collected_timestamp):
                raise ValueError("Não foi possível identificar a última data válida na Gold")
            collected_through = collected_timestamp.date()
            effective_cutoff = collected_through - timedelta(days=excluded_days)
    # The daily forecast uses its longest configured window plus the seven-day
    # AUTO_BACKTEST validation set.  A monthly history is only needed for
    # invoice-style connections (currently IBM) that allocate a full invoice
    # by its charge interval; it must never expand the daily-cloud window.
    max_window = max([int(w) for w in config.enabled_windows or [7, 14, 28]])
    minimum_monthly = max(1, int(getattr(config, "minimum_monthly_points", 3) or 3))
    interval_history_start = effective_cutoff.replace(day=1)
    for _ in range(minimum_monthly - 1):
        interval_history_start = (interval_history_start - timedelta(days=1)).replace(day=1)
    daily_history_start = effective_cutoff - timedelta(days=max_window + 7)
    logger.info(
        "[forecast] daily_history_start=%s (window=%dd) interval_history_start=%s (months=%d)",
        daily_history_start, max_window + 7, interval_history_start, minimum_monthly,
    )
    metadata_columns = {
        str(column.get("name") or "").lower()
        for column in metadata.get("columns", [])
    }
    # Forecast both dashboard measures from the same consumption history.
    # Contract context is added only when the source has it, so this remains
    # compatible with non-contract and legacy forecast tables.
    dual_financial_measures = (
        measure == "realizado"
        and "realizado_cloud" in metadata_columns
        and "financial_effect" in metadata_columns
    )
    model_measure = "realizado_cloud" if dual_financial_measures else measure
    query_dims = list(dims)
    for extra_col in (cloud_col, id_col):
        if extra_col and extra_col not in query_dims:
            query_dims.append(extra_col)
    financial_context_columns = [
        name for name in (
            "financial_effect", "financial_contract_id", "id_orcamento",
        )
        if name in metadata_columns
    ] if dual_financial_measures else []
    for extra_col in financial_context_columns:
        if extra_col not in query_dims:
            query_dims.append(extra_col)
    interval_rule_names = [
        str(rule.get("_from_business_rule"))
        for rule in allocation_rules
        if rule.get("_from_business_rule")
    ]
    per_connection = allocation_rules and not any(rule.get("_global") for rule in allocation_rules)
    global_interval = allocation_rules and any(rule.get("_global") for rule in allocation_rules)
    shard_col = "subaccountname" if any(str(item).lower() == "subaccountname" for item in query_dims) else None
    frames: list[pd.DataFrame] = []
    if global_interval:
        charge_end_col = _rule_charge_end_column(allocation_rules[0], config)
        records = await _fetch_period_records_by_cloud(
            source=source, type_filter=type_filter, period=period, history_start=interval_history_start,
            effective_cutoff=effective_cutoff, dims=query_dims, measure=model_measure, date_expr=date_expr,
            mode="interval", charge_end_col=charge_end_col, period_col=period,
            connection_filter=None, exclusion_filter=None,
            cloud_col=cloud_col, clouds=cloud_cutoffs, shard_col=shard_col,
            minimum_monthly_points=minimum_monthly,
        )
        interval_frame = pd.DataFrame(records)
        if not interval_frame.empty:
            interval_frame = _expand_charge_interval(interval_frame, query_dims)
            frames.append(interval_frame)
    elif per_connection:
        daily_filter = _connection_rule_filter(allocation_rules, cloud_col, id_col, negate=True)
        daily_records = await _fetch_period_records_by_cloud(
            source=source, type_filter=type_filter, period=period, history_start=daily_history_start,
            effective_cutoff=effective_cutoff, dims=query_dims, measure=model_measure, date_expr=date_expr,
            mode="daily", charge_end_col=None, period_col=period,
            connection_filter=daily_filter, exclusion_filter=None,
            cloud_col=cloud_col, clouds=cloud_cutoffs, shard_col=shard_col,
            minimum_monthly_points=1,
        )
        daily_frame = pd.DataFrame(daily_records)
        if not daily_frame.empty:
            frames.append(daily_frame)
        interval_filter = _connection_rule_filter(allocation_rules, cloud_col, id_col, negate=False)
        if interval_filter:
            charge_end_col = _rule_charge_end_column(allocation_rules[0], config)
            interval_records = await _fetch_period_records_by_cloud(
                source=source, type_filter=type_filter, period=period, history_start=interval_history_start,
                effective_cutoff=effective_cutoff, dims=query_dims, measure=model_measure, date_expr=date_expr,
                mode="interval", charge_end_col=charge_end_col, period_col=period,
                connection_filter=interval_filter, exclusion_filter=None,
                cloud_col=cloud_col, clouds=cloud_cutoffs, shard_col=shard_col,
                minimum_monthly_points=minimum_monthly,
            )
            interval_frame = pd.DataFrame(interval_records)
            if not interval_frame.empty:
                interval_frame = _expand_charge_interval(interval_frame, query_dims)
                frames.append(interval_frame)
    else:
        daily_records = await _fetch_period_records_by_cloud(
            source=source, type_filter=type_filter, period=period, history_start=daily_history_start,
            effective_cutoff=effective_cutoff, dims=query_dims, measure=model_measure, date_expr=date_expr,
            mode="daily", charge_end_col=None, period_col=period,
            connection_filter=None, exclusion_filter=None,
            cloud_col=cloud_col, clouds=cloud_cutoffs, shard_col=shard_col,
            minimum_monthly_points=1,
        )
        daily_frame = pd.DataFrame(daily_records)
        if not daily_frame.empty:
            frames.append(daily_frame)
    if source_exclusion:
        excluded_records = await _fetch_period_records(
            source=source, type_filter=type_filter, period=period, history_start=daily_history_start,
            effective_cutoff=effective_cutoff, dims=query_dims, measure=model_measure, date_expr=date_expr,
            mode="daily", charge_end_col=None, period_col=period,
            connection_filter=None, exclusion_filter=source_exclusion, only_exclusions=True,
            minimum_monthly_points=1,
        )
        excluded_frame = pd.DataFrame(excluded_records)
        if not excluded_frame.empty:
            frames.append(excluded_frame)
    # Overage mensal é um evento que reinicia a cada ciclo de créditos. Ele é
    # removido da série-base acima e entra como um componente independente
    # calculado pela média diária dos últimos 30 dias (incluindo dias sem
    # overage), em vez de contaminar a janela curta do custo recorrente.
    if mean_30d_source_filter:
        overage_records = await _fetch_period_records(
            source=source, type_filter=type_filter, period=period, history_start=daily_history_start,
            effective_cutoff=effective_cutoff, dims=query_dims, measure=model_measure, date_expr=date_expr,
            mode="daily", charge_end_col=None, period_col=period,
            connection_filter=None, exclusion_filter=mean_30d_source_filter,
            only_exclusions=True, exclusion_sign=1,
            minimum_monthly_points=1,
        )
        overage_frame = pd.DataFrame(overage_records)
        if not overage_frame.empty:
            overage_frame["__forecast_component"] = "MEAN_30D_SOURCE"
            frames.append(overage_frame)
    if not frames:
        raise ValueError("A Gold não possui dados realizados para a configuração")
    for source_frame in frames:
        if "__forecast_component" not in source_frame:
            source_frame["__forecast_component"] = "BASE"
    frame = pd.concat(frames, ignore_index=True)
    frame["forecast_day"] = pd.to_datetime(frame["forecast_day"], errors="coerce")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce").fillna(0.0)
    frame = frame.dropna(subset=["forecast_day"])
    if frame.empty:
        raise ValueError("Nenhuma data válida foi encontrada na Gold")
    key_cols = list(query_dims)
    if per_connection:
        for col in (cloud_col, id_col):
            if col and col not in key_cols:
                key_cols.append(col)
    component_col = "__forecast_component"
    model_key_cols = [*key_cols, component_col]
    results: list[dict[str, Any]] = []
    methods: dict[str, str] = {}
    series_context: dict[tuple[tuple[str, Any], ...], dict[str, Any]] = {}
    for key, group_frame in frame.groupby(model_key_cols, dropna=False):
        if not isinstance(key, tuple): key = (key,)
        model_key_values = {col: (None if pd.isna(value) else value) for col, value in zip(model_key_cols, key)}
        component = str(model_key_values.pop(component_col) or "BASE")
        key_values = model_key_values
        cloud_effective_cutoff = _cloud_effective_cutoff(
            cloud_cutoffs, cloud_col, key_values, effective_cutoff,
        )
        history = group_frame.set_index("forecast_day")["value"].sort_index()
        if history.empty:
            continue
        series_last_date = history.index.max().date()
        series_cutoff = min(cloud_effective_cutoff, series_last_date)
        history = history[history.index.date <= series_cutoff]
        if history.empty:
            continue
        interval_series = _series_matches_charge_interval(key_values, allocation_rules, cloud_col, id_col)
        # Invoice-interval series that did not appear in the current month have
        # already ended; projecting them alongside the current invoice double-counts.
        if interval_series and series_last_date < cloud_effective_cutoff.replace(day=1):
            continue
        daily = history.resample("D").sum()
        daily = daily.reindex(
            pd.date_range(daily.index.min(), pd.Timestamp(series_cutoff), freq="D"),
            fill_value=0.0,
        )
        series_context[_series_key(key_values, key_cols)] = _build_series_context(daily, series_cutoff)
        enabled_windows = [int(w) for w in config.enabled_windows or [7, 14, 28]]
        # Séries de fatura mensal ("lump"): poucas cobranças no mês, valor alto.
        # Tratar como taxa diária (fatura / dias do mês) evita tanto o
        # zero-fill (MEAN_7 ≈ 0) quanto a multiplicação da fatura por 28-31
        # dias quando o grid termina no dia da cobrança. Documentado no
        # FORECAST.md como baseline MONTHLY_LUMP.
        lump_daily_rate: float | None = None
        nonzero_days = int((daily > 0).sum())
        if nonzero_days <= 3 and len(daily) > 0:
            monthly_lump = daily.groupby(daily.index.to_period("M")).sum()
            charged_months = monthly_lump[monthly_lump > 0]
            if len(charged_months) >= 1:
                last_period = charged_months.index[-1]
                last_total = float(charged_months.iloc[-1])
                days_in_last_month = calendar.monthrange(last_period.year, last_period.month)[1]
                lump_daily_rate = last_total / days_in_last_month
        # Série lumpy (fatura mensal já lançada) vs uso diário: as regras
        # RECURRING_* zeram o mês aberto só para lumpy; uso diário mantém o
        # restante (baseline × dias_restantes).
        series_context[_series_key(key_values, key_cols)]["lumpy"] = lump_daily_rate is not None
        candidates = []
        for window in enabled_windows:
            sample = daily.tail(window)
            if len(sample) >= min(window, int(config.minimum_daily_points or 7)):
                candidates.append((f"MEAN_{window}D", float(sample.mean())))
        if not candidates:
            candidates = [("MEAN_AVAILABLE", float(daily.tail(7).mean()))]
        recent_window = min(enabled_windows)
        extended_window = max(enabled_windows)
        if len(daily) >= max(recent_window, int(config.minimum_daily_points or 7)):
            recent_mean = float(daily.tail(recent_window).mean())
            extended_mean = float(daily.tail(extended_window).mean())
            weight = max(0.0, min(1.0, float(config.recent_weight or 0.7)))
            candidates.append((f"WEIGHTED_{recent_window}D_{extended_window}D", recent_mean * weight + extended_mean * (1.0 - weight)))
        if component == "MEAN_30D_SOURCE":
            method, baseline = "MEAN_30D_RULE", float(daily.tail(30).mean())
        elif lump_daily_rate is not None:
            # A lump monthly bill is not a daily run-rate: force the monthly
            # rate regardless of AUTO_BACKTEST or fixed-window model modes.
            method, baseline = "MONTHLY_LUMP", lump_daily_rate
        elif config.model_mode == "AUTO_BACKTEST":
            scored = []
            validation = daily.tail(7)
            train = daily.iloc[:-7] if len(daily) > 7 else daily.iloc[0:0]
            for candidate_method, candidate_baseline in candidates:
                if candidate_method.startswith("WEIGHTED_"):
                    parts = candidate_method.split("_")
                    recent_w = int(parts[1][:-1]); extended_w = int(parts[2][:-1])
                    if len(train) >= extended_w:
                        recent_mean = float(train.tail(recent_w).mean())
                        extended_mean = float(train.tail(extended_w).mean())
                        weight = max(0.0, min(1.0, float(config.recent_weight or 0.7)))
                        prediction = recent_mean * weight + extended_mean * (1.0 - weight)
                    else:
                        prediction = float(candidate_baseline)
                    window = extended_w
                else:
                    window = int(candidate_method.split("_")[1][:-1]) if candidate_method.startswith("MEAN_") and candidate_method != "MEAN_AVAILABLE" else min(7, len(train))
                    prediction = float(train.tail(window).mean()) if len(train) >= window else float(candidate_baseline)
                if len(train) >= window and len(validation) > 0:
                    denominator = float(validation.abs().sum())
                    wape = float((validation - prediction).abs().sum()) / denominator if denominator else 0.0
                else:
                    wape = 0.0 if candidate_method == candidates[0][0] else 1.0
                scored.append((wape, candidate_method, candidate_baseline))
            _, method, baseline = min(scored, key=lambda item: item[0])
        else:
            if config.model_mode == "WEIGHTED":
                selected = next((item for item in candidates if item[0].startswith("WEIGHTED_")), None)
                if not selected:
                    raise ValueError("Dados insuficientes para aplicar média ponderada")
                method, baseline = selected
            elif config.model_mode.startswith("MEAN_"):
                selected = next((item for item in candidates if item[0] == config.model_mode), None)
                if not selected:
                    raise ValueError(f"Dados insuficientes para aplicar {config.model_mode}")
                method, baseline = selected
            else:
                method, baseline = candidates[0]
        if series_last_date < cloud_effective_cutoff.replace(day=1):
            # A lump that ended in a prior month must not be treated as a
            # daily run-rate for the open month (one purchase day × 30).
            method, baseline = "ENDED_SERIES", 0.0
        methods[method] = methods.get(method, 0) + 1
        # Each series projects from its own last collected date, not the global
        # Gold max date (which may belong to another cloud/month).
        series_anchor = series_cutoff
        anchor_period = pd.Timestamp(series_anchor).to_period("M")
        last_day = calendar.monthrange(series_anchor.year, series_anchor.month)[1]
        realized_value = float(history[history.index.to_period("M") == anchor_period].sum())
        remaining_days = last_day - series_anchor.day
        # Invoice-interval series already hold the full month on a daily grid.
        # Project the whole month only while the invoice is still missing; once
        # realizado is present the month is closed and forecast would double-count.
        if interval_series:
            current_forecast_days = 0 if realized_value > 0 else last_day
        else:
            current_forecast_days = remaining_days
        anchor_month_key = f"{series_anchor.year:04d}{series_anchor.month:02d}"
        if current_forecast_days > 0:
            results.append({**key_values, "forecast_month": anchor_month_key, "tipo": "forecast", "forecast_value": baseline * current_forecast_days, "selected_method": method, "fallback_level": 0, "history_points": int(len(daily)), "backtest_wape": None})
        # NOTE: o realizado do mês âncora NÃO é gravado na planning. A Gold
        # dest_modelo_focus já carrega o realizado; gravá-lo também na planning
        # duplicava o valor (a Gold soma as duas fontes e o dashboard somava
        # o realizado duas vezes). realized_value continua usado acima apenas
        # para decidir se o mês está fechado (current_forecast_days).
        for month_offset in range(1, int(config.horizon_months) + 1):
            month = series_anchor.month - 1 + month_offset
            year = series_anchor.year + month // 12
            month = month % 12 + 1
            # O componente de overage é deliberadamente uma média estável de
            # 30 dias: não herda nem o modelo de curto prazo nem a tendência
            # global do forecast para os próximos meses.
            growth_rate = 0.0 if component == "MEAN_30D_SOURCE" else float(config.growth_rate)
            value = baseline * ((1.0 + growth_rate) ** month_offset) * calendar.monthrange(year, month)[1]
            results.append({**key_values, "forecast_month": f"{year:04d}{month:02d}", "tipo": "forecast", "forecast_value": value, "selected_method": method, "fallback_level": 0, "history_points": int(len(daily)), "backtest_wape": None})
    output = pd.DataFrame(results)
    if output.empty: raise ValueError("Nenhuma série pôde ser projetada")
    applied_rules = _apply_forecast_business_rules(
        output,
        business_rules or [],
        series_context,
        key_cols,
        effective_cutoff,
    )
    applied_rules = list(dict.fromkeys([
        *source_rule_names, *mean_30d_rule_names, *interval_rule_names, *applied_rules,
    ]))
    for adjustment in sorted(adjustments, key=lambda a: a.priority):
        mask = pd.Series(True, index=output.index)
        for column, value in (adjustment.filters or {}).items():
            if column in output: mask &= output[column].astype(str).eq(str(value))
        if adjustment.start_period: mask &= output["forecast_month"] >= adjustment.start_period
        if adjustment.end_period: mask &= output["forecast_month"] <= adjustment.end_period
        if adjustment.adjustment_type == "PERCENTAGE": output.loc[mask, "forecast_value"] *= 1 + float(adjustment.percentage or 0)
        elif adjustment.adjustment_type == "FIXED_VALUE": output.loc[mask, "forecast_value"] = float(adjustment.fixed_value or 0)
        elif adjustment.adjustment_type == "ADD_VALUE": output.loc[mask, "forecast_value"] += float(adjustment.fixed_value or 0)
        elif adjustment.adjustment_type == "SUBTRACT_VALUE": output.loc[mask, "forecast_value"] -= float(adjustment.fixed_value or 0)
        elif adjustment.adjustment_type == "ZERO": output.loc[mask, "forecast_value"] = 0.0
    if dual_financial_measures:
        await _apply_dual_financial_measures(
            output,
            financial_context_columns=[
                *financial_context_columns,
                *([cloud_col] if cloud_col else []),
            ],
            source=source,
            type_filter=type_filter,
            period_column=period,
        )
    # Component is an execution-only key. Planning/forecast tables retain the
    # configured dimensions and sum the base + special components naturally.
    output = output.drop(columns=[component_col], errors="ignore")
    # A forecast run is an immutable audit snapshot.  Callers pass the run ID
    # so two executions on the same day can never overwrite each other.
    # ``cutoff`` is only a naming fallback; the math always uses the
    # data-driven effective_cutoff (MAX collected − cutoff_days).
    if not result_table:
        anchor = cutoff or effective_cutoff
        result_table = f"result_{config.id}_{int(anchor.strftime('%Y%m%d'))}"
    iceberg.write_forecast_result(result_table, output)
    # Simulated runs must never overwrite the current planning snapshot; they
    # are audit/backtest artifacts used only by the accuracy dashboard.
    planning_table = None
    if simulated_as_of is None:
        planning_table = iceberg.write_forecast_planning(table, output, value_column=measure)
    return {
        "rows": len(output),
        "table": result_table,
        "planning_table": planning_table,
        "methods": methods,
        "series": len(output.groupby(key_cols)) if key_cols else 1,
        "business_rules": applied_rules,
        "collected_through": collected_through.isoformat(),
        "effective_cutoff": effective_cutoff.isoformat(),
        "cloud_cutoffs": {cloud: cutoff.isoformat() for cloud, cutoff in sorted(cloud_cutoffs.items())},
        "simulated_as_of": simulated_as_of.isoformat() if simulated_as_of else None,
    }
