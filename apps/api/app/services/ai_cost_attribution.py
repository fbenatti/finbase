"""Resolve FOCUS filters for AI cost links, preview totals, and apply per-user allocation."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from app.services import lakehouse_layers as layers

from app.models.ai_cost import (
    AiCostAllocation,
    AiCostLink,
    AiCostLinkSource,
    AiCostPoolAllocation,
    DISTRIBUTION_BASES,
    VALUE_COLUMNS,
)

logger = logging.getLogger(__name__)

FOCUS_TABLE = "dest_modelo_focus"
LICENSES_TABLE = "dest_licencas_ia"
CONSUMPTION_TABLE = "dest_consumo_ia"

FINGERPRINT_COLUMNS = (
    "source_connection_id",
    "ProviderName",
    "ServiceName",
    "SkuId",
    "SkuName",
    "ChargeCategory",
    "ChargeDescription",
    "ResourceId",
    "BilledCost",
    "EffectiveCost",
    "TotalCostOnDemandBRL",
    "ChargePeriodStart",
    "ChargePeriodEnd",
)


def _quote_ident(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _schema_map(rows: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for row in rows:
        name = str(row.get("Column") or row.get("column_name") or row.get("column") or "")
        if name:
            mapping[name.lower()] = name
    return mapping


def _resolve_column(schema: dict[str, str], name: str) -> str | None:
    return schema.get(str(name or "").lower())


def _filter_clause(schema: dict[str, str], conditions: list[dict[str, Any]]) -> str:
    clauses: list[str] = []
    for item in conditions or []:
        column = _resolve_column(schema, item.get("column") or item.get("field") or "")
        if not column:
            continue
        operator = str(item.get("operator") or item.get("op") or "IN").upper()
        values = item.get("values")
        if values is None and item.get("value") is not None:
            values = [item.get("value")]
        values = [str(v) for v in (values or []) if v is not None and str(v).strip() != ""]
        col_sql = f"lower(CAST({_quote_ident(column)} AS VARCHAR))"
        if operator in {"EMPTY", "IS_EMPTY"}:
            clauses.append(
                f"({_quote_ident(column)} IS NULL OR TRIM(CAST({_quote_ident(column)} AS VARCHAR)) = '')"
            )
        elif operator in {"NOT_EMPTY", "IS_NOT_EMPTY"}:
            clauses.append(
                f"({_quote_ident(column)} IS NOT NULL AND TRIM(CAST({_quote_ident(column)} AS VARCHAR)) <> '')"
            )
        elif operator in {"CONTAINS", "~"} and values:
            likes = " OR ".join(f"{col_sql} LIKE {_quote_literal('%' + v.lower() + '%')}" for v in values)
            clauses.append(f"({likes})")
        elif operator in {"NOT_CONTAINS"} and values:
            likes = " AND ".join(f"{col_sql} NOT LIKE {_quote_literal('%' + v.lower() + '%')}" for v in values)
            clauses.append(f"({likes})")
        elif operator in {"NOT_IN", "!="} and values:
            literals = ", ".join(_quote_literal(v.lower()) for v in values)
            clauses.append(f"({col_sql} NOT IN ({literals}) OR {_quote_ident(column)} IS NULL)")
        elif operator in {"=", "=="} and values:
            clauses.append(f"{col_sql} = {_quote_literal(values[0].lower())}")
        elif values:
            literals = ", ".join(_quote_literal(v.lower()) for v in values)
            clauses.append(f"{col_sql} IN ({literals})")
    return " AND ".join(clauses) if clauses else "1=1"


def _period_clause(schema: dict[str, str], billing_period: str) -> str:
    period_col = _resolve_column(schema, "_billing_period") or _resolve_column(schema, "period")
    if not period_col:
        return "1=1"
    return f"CAST({_quote_ident(period_col)} AS VARCHAR) = {_quote_literal(billing_period)}"


def _focus_date_clause(
    schema: dict[str, str],
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    """Restrict FOCUS rows to a billing/charge period start window (YYYY-MM-DD)."""
    date_col = None
    for candidate in ("billingperiodstart", "chargeperiodstart", "date"):
        date_col = _resolve_column(schema, candidate)
        if date_col:
            break
    if not date_col:
        return "1=1"
    col_sql = f"substr(CAST({_quote_ident(date_col)} AS VARCHAR), 1, 10)"
    clauses: list[str] = []
    if date_from:
        clauses.append(f"{col_sql} >= {_quote_literal(str(date_from)[:10])}")
    if date_to:
        clauses.append(f"{col_sql} <= {_quote_literal(str(date_to)[:10])}")
    return " AND ".join(clauses) if clauses else "1=1"


def _time_window_clause(
    schema: dict[str, str],
    billing_period: str | None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    if date_from or date_to:
        return _focus_date_clause(schema, date_from, date_to)
    if billing_period:
        return _period_clause(schema, billing_period)
    return "1=1"


def _connection_clause(schema: dict[str, str], connection_id: int | None) -> str:
    if not connection_id:
        return "1=1"
    col = _resolve_column(schema, "source_connection_id")
    if not col:
        return "1=1"
    return f"TRY_CAST({_quote_ident(col)} AS BIGINT) = {int(connection_id)}"


def _value_expr(schema: dict[str, str], value_column: str) -> str:
    col = _resolve_column(schema, value_column) or value_column
    if col not in schema.values() and col not in schema:
        canonical = _resolve_column(schema, value_column)
        if canonical:
            col = canonical
    return f"COALESCE(TRY_CAST({_quote_ident(col)} AS DOUBLE), 0)"


def _fingerprint_expr(schema: dict[str, str]) -> str:
    parts: list[str] = []
    for logical in FINGERPRINT_COLUMNS:
        physical = _resolve_column(schema, logical)
        if physical:
            parts.append(
                f"COALESCE(CAST({_quote_ident(physical)} AS VARCHAR), '')"
            )
    if not parts:
        parts.append("'row'")
    joined = " || '|' || ".join(parts)
    return f"to_hex(md5(to_utf8({joined})))"


def link_applies(link: AiCostLink, billing_period: str) -> bool:
    if not link.active:
        return False
    if link.valid_from and billing_period < link.valid_from:
        return False
    if link.valid_until and billing_period > link.valid_until:
        return False
    return True


@dataclass
class ResolvedSource:
    source_id: int
    link_id: int
    connection_id: int | None
    value_column: str
    percent: float
    row_count: int = 0
    total_value: float = 0.0
    fingerprints: set[str] = field(default_factory=set)


@dataclass
class ApplyResult:
    billing_period: str
    links_processed: int
    total_cost: float
    allocations_written: int
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    unmapped_links: list[int] = field(default_factory=list)


async def load_focus_schema() -> dict[str, str]:
    from app.services.trino_service import execute as trino_execute
    rows = await trino_execute(f"DESCRIBE iceberg.{layers.detail_layer()}.{FOCUS_TABLE}", timeout_s=30)
    return _schema_map(rows)


async def preview_source(
    *,
    billing_period: str,
    filter_json: dict[str, Any] | None,
    value_column: str = "BilledCost",
    connection_id: int | None = None,
    percent: float = 100.0,
) -> dict[str, Any]:
    from app.services.trino_service import execute as trino_execute

    schema = await load_focus_schema()
    value_col = value_column if value_column in VALUE_COLUMNS else "BilledCost"
    conditions = (filter_json or {}).get("conditions") or filter_json or []
    if isinstance(conditions, dict):
        conditions = [conditions]
    where = " AND ".join([
        _period_clause(schema, billing_period),
        _connection_clause(schema, connection_id),
        _filter_clause(schema, list(conditions)),
    ])
    sql = f"""
      SELECT COUNT(*) AS row_count,
             SUM({_value_expr(schema, value_col)}) AS total_value
      FROM iceberg.{layers.detail_layer()}.{FOCUS_TABLE}
      WHERE {where}
    """
    rows = await trino_execute(sql, timeout_s=120)
    row = rows[0] if rows else {}
    total = float(row.get("total_value") or 0)
    scaled = total * float(percent or 100) / 100.0
    return {
        "billing_period": billing_period,
        "row_count": int(row.get("row_count") or 0),
        "total_value": total,
        "percent": float(percent or 100),
        "allocated_value": scaled,
        "value_column": value_col,
    }


async def resolve_source_rows(
    schema: dict[str, str],
    source: AiCostLinkSource,
    billing_period: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> ResolvedSource:
    from app.services.trino_service import execute as trino_execute

    value_col = source.value_column if source.value_column in VALUE_COLUMNS else "BilledCost"
    filter_json = source.filter_json or {}
    conditions = filter_json.get("conditions") or []
    if isinstance(conditions, dict):
        conditions = [conditions]
    where = " AND ".join([
        _time_window_clause(schema, billing_period, date_from, date_to),
        _connection_clause(schema, source.connection_id),
        _filter_clause(schema, list(conditions)),
    ])
    fp = _fingerprint_expr(schema)
    sql = f"""
      SELECT {fp} AS fingerprint,
             SUM({_value_expr(schema, value_col)}) AS value
      FROM iceberg.{layers.detail_layer()}.{FOCUS_TABLE}
      WHERE {where}
      GROUP BY 1
    """
    rows = await trino_execute(sql, timeout_s=180)
    fingerprints = {str(r.get("fingerprint")) for r in rows if r.get("fingerprint")}
    total = sum(float(r.get("value") or 0) for r in rows)
    scaled = total * float(source.percent or 100) / 100.0
    return ResolvedSource(
        source_id=source.id,
        link_id=source.link_id,
        connection_id=source.connection_id,
        value_column=value_col,
        percent=float(source.percent or 100),
        row_count=len(rows),
        total_value=scaled,
        fingerprints=fingerprints,
    )


def detect_double_count(resolved: list[ResolvedSource]) -> list[dict[str, Any]]:
    owner: dict[str, tuple[int, int]] = {}
    conflicts: list[dict[str, Any]] = []
    for item in resolved:
        for fp in item.fingerprints:
            if not fp:
                continue
            if fp in owner:
                prev_link, prev_source = owner[fp]
                conflicts.append({
                    "fingerprint": fp,
                    "link_id": item.link_id,
                    "source_id": item.source_id,
                    "conflicts_with_link_id": prev_link,
                    "conflicts_with_source_id": prev_source,
                })
            else:
                owner[fp] = (item.link_id, item.source_id)
    return conflicts


async def count_active_licenses(
    *,
    billing_period: str,
    provider: str,
    product_name: str,
    license_sku: str | None = None,
) -> int:
    from app.services.trino_service import execute as trino_execute

    sku_clause = ""
    if license_sku:
        sku_clause = f" AND lower(CAST(\"LicenseSkuName\" AS VARCHAR)) = {_quote_literal(license_sku.lower())}"
    sql = f"""
      SELECT COUNT(DISTINCT "UserEmail") AS active_count
      FROM iceberg.gold.{LICENSES_TABLE}
      WHERE CAST("_billing_period" AS VARCHAR) = {_quote_literal(billing_period)}
        AND lower(CAST("Provider" AS VARCHAR)) = {_quote_literal(provider.lower())}
        AND lower(CAST("ProductName" AS VARCHAR)) = {_quote_literal(product_name.lower())}
        AND COALESCE(NULLIF(TRIM(CAST("UserEmail" AS VARCHAR)), ''), '') <> ''
        AND lower(COALESCE(CAST("LicenseStatus" AS VARCHAR), 'active')) NOT IN ('inactive', 'suspended', 'cancelled')
        {sku_clause}
    """
    try:
        rows = await trino_execute(sql, timeout_s=120)
        return int((rows[0] if rows else {}).get("active_count") or 0)
    except Exception as exc:
        logger.warning("Active license count skipped: %s", exc)
        return 0


async def _distribution_weights(
    *,
    billing_period: str,
    provider: str,
    product_name: str,
    basis: str,
    license_sku: str | None,
    user_key: str = "email",
) -> dict[str, float]:
    from app.services.trino_service import execute as trino_execute

    lic_user_col = '"UserLogin"' if user_key == "login" else '"UserEmail"'
    con_user_col = '"UserLogin"' if user_key == "login" else '"UserEmail"'

    sku_clause = ""
    if license_sku:
        sku_clause = f" AND lower(CAST(l.\"LicenseSkuName\" AS VARCHAR)) = {_quote_literal(license_sku.lower())}"

    if basis == "equal":
        sql = f"""
          SELECT lower(CAST(l.{lic_user_col} AS VARCHAR)) AS user_email, 1.0 AS weight
          FROM iceberg.gold.{LICENSES_TABLE} l
          WHERE CAST(l."_billing_period" AS VARCHAR) = {_quote_literal(billing_period)}
            AND lower(CAST(l."Provider" AS VARCHAR)) = {_quote_literal(provider.lower())}
            AND lower(CAST(l."ProductName" AS VARCHAR)) = {_quote_literal(product_name.lower())}
            AND COALESCE(NULLIF(TRIM(CAST(l.{lic_user_col} AS VARCHAR)), ''), '') <> ''
            {sku_clause}
        """
    elif basis == "requests":
        sql = f"""
          SELECT lower(CAST(l.{lic_user_col} AS VARCHAR)) AS user_email,
                 COALESCE(SUM(COALESCE(TRY_CAST(c."RequestCount" AS DOUBLE), 0)), 0) AS weight
          FROM iceberg.gold.{LICENSES_TABLE} l
          LEFT JOIN iceberg.gold.{CONSUMPTION_TABLE} c
            ON lower(CAST(c.{con_user_col} AS VARCHAR)) = lower(CAST(l.{lic_user_col} AS VARCHAR))
           AND CAST(c."_billing_period" AS VARCHAR) = {_quote_literal(billing_period)}
           AND lower(CAST(c."Provider" AS VARCHAR)) = {_quote_literal(provider.lower())}
          WHERE CAST(l."_billing_period" AS VARCHAR) = {_quote_literal(billing_period)}
            AND lower(CAST(l."Provider" AS VARCHAR)) = {_quote_literal(provider.lower())}
            AND lower(CAST(l."ProductName" AS VARCHAR)) = {_quote_literal(product_name.lower())}
            AND COALESCE(NULLIF(TRIM(CAST(l.{lic_user_col} AS VARCHAR)), ''), '') <> ''
            {sku_clause}
          GROUP BY 1
        """
    elif basis == "activity_index":
        sql = f"""
          WITH usage AS (
            SELECT lower(CAST({con_user_col} AS VARCHAR)) AS user_email,
                   COALESCE(SUM(COALESCE(TRY_CAST("TotalTokens" AS DOUBLE), 0)), 0)
                   + COALESCE(SUM(COALESCE(TRY_CAST("SuggestionsCount" AS DOUBLE), 0)), 0) AS metric
            FROM iceberg.gold.{CONSUMPTION_TABLE}
            WHERE CAST("_billing_period" AS VARCHAR) = {_quote_literal(billing_period)}
              AND lower(CAST("Provider" AS VARCHAR)) = {_quote_literal(provider.lower())}
            GROUP BY 1
          ),
          ranked AS (
            SELECT user_email,
                   metric,
                   COALESCE(percent_rank() OVER (ORDER BY metric), 0) AS weight
            FROM usage
          )
          SELECT lower(CAST(l.{lic_user_col} AS VARCHAR)) AS user_email,
                 COALESCE(r.weight, 0) AS weight
          FROM iceberg.gold.{LICENSES_TABLE} l
          LEFT JOIN ranked r ON r.user_email = lower(CAST(l.{lic_user_col} AS VARCHAR))
          WHERE CAST(l."_billing_period" AS VARCHAR) = {_quote_literal(billing_period)}
            AND lower(CAST(l."Provider" AS VARCHAR)) = {_quote_literal(provider.lower())}
            AND lower(CAST(l."ProductName" AS VARCHAR)) = {_quote_literal(product_name.lower())}
            AND COALESCE(NULLIF(TRIM(CAST(l.{lic_user_col} AS VARCHAR)), ''), '') <> ''
            {sku_clause}
        """
    else:
        sql = f"""
          SELECT lower(CAST(l.{lic_user_col} AS VARCHAR)) AS user_email,
                 COALESCE(SUM(COALESCE(TRY_CAST(c."TotalTokens" AS DOUBLE), 0)), 0) AS weight
          FROM iceberg.gold.{LICENSES_TABLE} l
          LEFT JOIN iceberg.gold.{CONSUMPTION_TABLE} c
            ON lower(CAST(c.{con_user_col} AS VARCHAR)) = lower(CAST(l.{lic_user_col} AS VARCHAR))
           AND CAST(c."_billing_period" AS VARCHAR) = {_quote_literal(billing_period)}
           AND lower(CAST(c."Provider" AS VARCHAR)) = {_quote_literal(provider.lower())}
          WHERE CAST(l."_billing_period" AS VARCHAR) = {_quote_literal(billing_period)}
            AND lower(CAST(l."Provider" AS VARCHAR)) = {_quote_literal(provider.lower())}
            AND lower(CAST(l."ProductName" AS VARCHAR)) = {_quote_literal(product_name.lower())}
            AND COALESCE(NULLIF(TRIM(CAST(l.{lic_user_col} AS VARCHAR)), ''), '') <> ''
            {sku_clause}
          GROUP BY 1
        """
    rows = await trino_execute(sql, timeout_s=180)
    weights = {str(r.get("user_email") or ""): float(r.get("weight") or 0) for r in rows if r.get("user_email")}
    if basis == "equal":
        return weights
    active = {email: weight for email, weight in weights.items() if weight > 0}
    if active:
        return active
    return weights


def _split_amount(total: float, weights: dict[str, float]) -> dict[str, float]:
    if abs(total) < 1e-12 or not weights:
        return {}
    positive = {email: w for email, w in weights.items() if w > 0}
    if not positive:
        each = total / len(weights)
        return {email: each for email in weights}
    denom = sum(positive.values())
    return {email: total * weight / denom for email, weight in positive.items()}


async def propagate_allocated_costs(
    *,
    billing_period: str,
    link: AiCostLink,
    allocations: list[dict[str, Any]],
    user_key: str = "email",
) -> None:
    from app.services.trino_service import execute as trino_execute

    if not allocations:
        return
    user_col = "UserLogin" if user_key == "login" else "UserEmail"
    sku_clause = ""
    if link.license_sku:
        sku_clause = f" AND lower(CAST(t.\"LicenseSkuName\" AS VARCHAR)) = {_quote_literal(link.license_sku.lower())}"

    valid_allocations = [
        item for item in allocations
        if item.get("user_email") and abs(float(item.get("allocated_amount") or 0)) > 1e-12
    ]
    if not valid_allocations:
        return

    values = []
    for item in valid_allocations:
        email = str(item.get("user_email")).replace("'", "''")
        amount = float(item.get("allocated_amount") or 0)
        values.append(f"('{email}', {amount})")

    method = valid_allocations[0].get("allocation_basis") or link.distribution_basis or "link"
    source_ref = f"ai_cost_link:{link.id}"

    BATCH_SIZE = 1000
    for i in range(0, len(values), BATCH_SIZE):
        batch = values[i:i + BATCH_SIZE]
        val_str = ", ".join(batch)
        
        license_sql = f"""
          MERGE INTO iceberg.gold.{LICENSES_TABLE} t
          USING (VALUES {val_str}) AS s(email, amount)
          ON lower(COALESCE(CAST(t."UserEmail" AS VARCHAR), CAST(t."UserLogin" AS VARCHAR))) = lower(s.email)
             AND CAST(t."_billing_period" AS VARCHAR) = {_quote_literal(billing_period)}
             AND lower(CAST(t."Provider" AS VARCHAR)) = {_quote_literal(link.provider.lower())}
             AND lower(CAST(t."ProductName" AS VARCHAR)) = {_quote_literal(link.product_name.lower())}
             {sku_clause}
          WHEN MATCHED THEN
             UPDATE SET "AllocatedLicenseCost" = s.amount,
                        "CostAllocationMethod" = {_quote_literal(method)},
                        "CostSourceRef" = {_quote_literal(source_ref)},
                        "IsCostEstimated" = false
        """
        
        consumption_sql = f"""
          MERGE INTO iceberg.gold.{CONSUMPTION_TABLE} t
          USING (VALUES {val_str}) AS s(email, amount)
          ON lower(COALESCE(CAST(t."UserEmail" AS VARCHAR), CAST(t."UserLogin" AS VARCHAR))) = lower(s.email)
             AND CAST(t."_billing_period" AS VARCHAR) = {_quote_literal(billing_period)}
             AND lower(CAST(t."Provider" AS VARCHAR)) = {_quote_literal(link.provider.lower())}
          WHEN MATCHED THEN
             UPDATE SET "AllocatedCost" = COALESCE(TRY_CAST(t."AllocatedCost" AS DOUBLE), 0) + s.amount,
                        "CostAllocationMethod" = {_quote_literal(method)},
                        "CostSourceRef" = {_quote_literal(source_ref)},
                        "IsCostEstimated" = false
        """
        try:
            await trino_execute(license_sql, timeout_s=300)
            await trino_execute(consumption_sql, timeout_s=300)
        except Exception as exc:
            logger.warning("Failed to propagate allocation batch: %s", exc)


async def _ptu_token_weights(
    billing_period: str,
    *,
    project_key: str | None,
    allocation_level: str | None,
    metric_hour_from: str | None = None,
    metric_hour_to: str | None = None,
) -> list[dict[str, Any]]:
    from app.services.trino_service import execute as trino_execute

    level = (allocation_level or "project").lower()
    project_filter = f" AND CAST(project_key AS VARCHAR) = {_quote_literal(project_key)}" if project_key else ""
    hour_clauses: list[str] = []
    if metric_hour_from:
        hour_clauses.append(f"CAST(metric_hour AS VARCHAR) >= {_quote_literal(metric_hour_from)}")
    if metric_hour_to:
        hour_clauses.append(f"CAST(metric_hour AS VARCHAR) <= {_quote_literal(metric_hour_to)}")
    hour_filter = (" AND " + " AND ".join(hour_clauses)) if hour_clauses else ""
    period_filter = (
        f" AND CAST(_billing_period AS VARCHAR) = {_quote_literal(billing_period)}"
        if not hour_filter
        else ""
    )
    if level == "subproject":
        sql = f"""
          SELECT CAST(subproject_key AS VARCHAR) AS key,
                 CAST(project_key AS VARCHAR) AS project_key,
                 COALESCE(SUM(TRY_CAST(total_tokens AS DOUBLE)), 0) AS weight
          FROM iceberg.gold.dest_azure_ptu_subproject_usage
          WHERE 1 = 1{period_filter}{hour_filter}{project_filter}
          GROUP BY 1, 2
        """
    elif level == "deployment":
        hint_filter = f" AND CAST(project_hint AS VARCHAR) = {_quote_literal(project_key)}" if project_key else ""
        sql = f"""
          SELECT CAST(deployment_name AS VARCHAR) AS key,
                 CAST(project_hint AS VARCHAR) AS project_key,
                 COALESCE(SUM(TRY_CAST(total_tokens_sum AS DOUBLE)), 0) AS weight
          FROM iceberg.gold.dest_azure_ptu_metrics
          WHERE 1 = 1{period_filter.replace('project_key', 'project_hint')}{hour_filter}{hint_filter}
          GROUP BY 1, 2
        """
    else:
        return []
    try:
        return await trino_execute(sql, timeout_s=120)
    except Exception as exc:
        logger.warning("PTU token weights skipped: %s", exc)
        return []


async def preview_ptu_pool_allocation(
    link: AiCostLink,
    billing_period: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    metric_hour_from: str | None = None,
    metric_hour_to: str | None = None,
    schema: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    if schema is None:
        schema = await load_focus_schema()
    total = 0.0
    refs: list[dict[str, Any]] = []
    for source in link.sources or []:
        resolved = await resolve_source_rows(
            schema, source, billing_period,
            date_from=date_from, date_to=date_to,
        )
        total += resolved.total_value
        refs.append({"source_id": resolved.source_id, "row_count": resolved.row_count, "value": resolved.total_value})

    level = (link.allocation_level or "project").lower()
    rows: list[dict[str, Any]] = []
    if level == "project":
        rows.append({
            "project_key": link.project_key,
            "deployment_key": None,
            "subproject_key": None,
            "allocated_amount": total,
            "allocation_basis": "project",
            "source_refs": refs,
        })
        return rows

    weights = await _ptu_token_weights(
        billing_period,
        project_key=link.project_key,
        allocation_level=level,
        metric_hour_from=metric_hour_from,
        metric_hour_to=metric_hour_to,
    )
    weight_total = sum(float(w.get("weight") or 0) for w in weights)
    if weight_total <= 0:
        rows.append({
            "project_key": link.project_key,
            "deployment_key": None,
            "subproject_key": None,
            "allocated_amount": total,
            "allocation_basis": "project_fallback",
            "source_refs": refs,
        })
        return rows

    for item in weights:
        share = float(item.get("weight") or 0) / weight_total
        rows.append({
            "project_key": item.get("project_key") or link.project_key,
            "deployment_key": item.get("key") if level == "deployment" else None,
            "subproject_key": item.get("key") if level == "subproject" else None,
            "allocated_amount": total * share,
            "allocation_basis": f"tokens_{level}",
            "total_tokens": float(item.get("weight") or 0),
            "coverage_pct": 100.0 * share,
            "source_refs": refs,
        })
    return rows


async def apply_ptu_pool_links(
    session: Any,
    links: list[AiCostLink],
    billing_period: str,
    *,
    dry_run: bool = False,
) -> ApplyResult:
    applicable = [
        link for link in links
        if link_applies(link, billing_period)
        and (getattr(link, "target_type", None) or "license_pool") == "ptu_pool"
    ]
    result = ApplyResult(
        billing_period=billing_period,
        links_processed=len(applicable),
        total_cost=0.0,
        allocations_written=0,
    )
    if dry_run:
        for link in applicable:
            preview = await preview_ptu_pool_allocation(link, billing_period)
            result.total_cost += sum(float(r.get("allocated_amount") or 0) for r in preview)
            result.allocations_written += len(preview)
        return result

    from sqlalchemy import delete

    link_ids = [link.id for link in applicable]
    if link_ids:
        session.execute(
            delete(AiCostPoolAllocation).where(
                AiCostPoolAllocation.billing_period == billing_period,
                AiCostPoolAllocation.link_id.in_(link_ids),
            )
        )
        session.flush()

    for link in applicable:
        preview_rows = await preview_ptu_pool_allocation(link, billing_period)
        if not preview_rows:
            result.unmapped_links.append(link.id)
            continue
        link_total = sum(float(r.get("allocated_amount") or 0) for r in preview_rows)
        result.total_cost += link_total
        for row in preview_rows:
            session.add(AiCostPoolAllocation(
                link_id=link.id,
                billing_period=billing_period,
                deployment_key=row.get("deployment_key"),
                allocated_amount=float(row.get("allocated_amount") or 0),
                allocation_basis=row.get("allocation_basis"),
                total_tokens=row.get("total_tokens"),
                coverage_pct=row.get("coverage_pct"),
                source_refs=row.get("source_refs"),
            ))
            result.allocations_written += 1
    session.flush()
    return result


async def apply_links(
    session: Any,
    links: list[AiCostLink],
    billing_period: str,
    *,
    dry_run: bool = False,
) -> ApplyResult:
    license_result = await _apply_license_pool_links(session, links, billing_period, dry_run=dry_run)
    ptu_result = await apply_ptu_pool_links(session, links, billing_period, dry_run=dry_run)
    return ApplyResult(
        billing_period=billing_period,
        links_processed=license_result.links_processed + ptu_result.links_processed,
        total_cost=license_result.total_cost + ptu_result.total_cost,
        allocations_written=license_result.allocations_written + ptu_result.allocations_written,
        conflicts=license_result.conflicts,
        unmapped_links=license_result.unmapped_links + ptu_result.unmapped_links,
    )


async def _apply_license_pool_links(
    session: Any,
    links: list[AiCostLink],
    billing_period: str,
    *,
    dry_run: bool = False,
) -> ApplyResult:
    schema = await load_focus_schema()
    applicable = [
        link for link in links
        if link_applies(link, billing_period)
        and (getattr(link, "target_type", None) or "license_pool") == "license_pool"
    ]
    resolved: list[ResolvedSource] = []
    for link in applicable:
        for source in link.sources or []:
            resolved.append(await resolve_source_rows(schema, source, billing_period))

    conflicts = detect_double_count(resolved)
    if conflicts:
        return ApplyResult(
            billing_period=billing_period,
            links_processed=0,
            total_cost=0.0,
            allocations_written=0,
            conflicts=conflicts,
        )

    link_costs: dict[int, float] = {}
    link_refs: dict[int, list[dict[str, Any]]] = {}
    source_by_link: dict[int, list[ResolvedSource]] = {}
    for item in resolved:
        link_costs[item.link_id] = link_costs.get(item.link_id, 0.0) + item.total_value
        source_by_link.setdefault(item.link_id, []).append(item)
        link_refs.setdefault(item.link_id, []).append({
            "source_id": item.source_id,
            "row_count": item.row_count,
            "value": item.total_value,
        })

    result = ApplyResult(
        billing_period=billing_period,
        links_processed=len(applicable),
        total_cost=sum(link_costs.values()),
        allocations_written=0,
    )

    if dry_run:
        result.unmapped_links = [link.id for link in applicable if link_costs.get(link.id, 0) <= 0]
        return result

    from sqlalchemy import delete

    session.execute(delete(AiCostAllocation).where(AiCostAllocation.billing_period == billing_period))
    session.flush()

    for link in applicable:
        total = link_costs.get(link.id, 0.0)
        if total <= 0:
            result.unmapped_links.append(link.id)
            continue
        refs = link_refs.get(link.id, [])
        if not link.distribution_enabled:
            session.add(AiCostAllocation(
                link_id=link.id,
                billing_period=billing_period,
                user_email=None,
                provider=link.provider,
                product_name=link.product_name,
                allocated_amount=total,
                allocation_basis=None,
                source_refs=refs,
            ))
            continue

        basis = (link.distribution_basis or "tokens").lower()
        if basis not in DISTRIBUTION_BASES:
            basis = "tokens"
        user_key = (link.distribution_user_key or "email").lower()
        if user_key not in {"email", "login"}:
            user_key = "email"
        weights = await _distribution_weights(
            billing_period=billing_period,
            provider=link.provider,
            product_name=link.product_name,
            basis=basis,
            license_sku=link.license_sku,
            user_key=user_key,
        )
        shares = _split_amount(total, weights)
        allocation_rows: list[dict[str, Any]] = []
        for email, amount in shares.items():
            session.add(AiCostAllocation(
                link_id=link.id,
                billing_period=billing_period,
                user_email=email,
                provider=link.provider,
                product_name=link.product_name,
                allocated_amount=amount,
                allocation_basis=basis,
                source_refs=refs,
            ))
            allocation_rows.append({
                "user_email": email,
                "allocated_amount": amount,
                "allocation_basis": basis,
            })
            result.allocations_written += 1
        await propagate_allocated_costs(
            billing_period=billing_period,
            link=link,
            allocations=allocation_rows,
            user_key=user_key,
        )

    session.flush()
    return result