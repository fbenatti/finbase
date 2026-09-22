"""Dimon leve para os dashboards operacionais de Azure OpenAI PTU.

A Gold ``dest_azure_ptu_metrics`` preserva a observação horária e continua
sendo a fonte do heatmap. Os demais widgets leem esta materialização diária,
reduzida por deployment/projeto, para não varrer o histórico horário em cada
abertura da tela.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.trino_service import execute as trino_execute

logger = logging.getLogger(__name__)

MART_NAMESPACE = "dimon"
MART_TABLE = "dest_dm_azure_ptu_daily"
DIMON_SQL = f"iceberg.{MART_NAMESPACE}.{MART_TABLE}"
GOLD_SQL = "iceberg.gold.dest_azure_ptu_metrics"
DASHBOARD_TRINO_TIMEOUT_S = 25

_DIMON_COLUMNS: set[str] | None = None


def _gold_metrics_window(hours: int) -> tuple[str, str]:
    end = datetime.now(timezone.utc) - timedelta(minutes=10)
    start = end - timedelta(hours=max(1, min(hours, 93 * 24)))
    return start.strftime("%Y-%m-%dT%H:00:00Z"), end.strftime("%Y-%m-%dT%H:00:00Z")


def _gold_connection_filter(connection_ids: list[int] | None) -> str:
    if not connection_ids:
        return ""
    return " AND CAST(connection_id AS BIGINT) IN (" + ", ".join(str(int(x)) for x in connection_ids) + ")"


def _gold_project_hint_filter(project_hints: list[str] | None) -> str:
    if not project_hints:
        return ""
    hints = ", ".join("'" + str(value).replace("'", "''") + "'" for value in project_hints)
    return f" AND CAST(project_hint AS VARCHAR) IN ({hints})"


def _gold_metric_hour_filter(start_iso: str, end_iso: str, column: str = "metric_hour") -> str:
    return f"CAST({column} AS VARCHAR) >= '{start_iso}' AND CAST({column} AS VARCHAR) <= '{end_iso}'"


def _sum(columns: set[str], name: str) -> str:
    if name in columns:
        return f"COALESCE(SUM(TRY_CAST({name} AS DOUBLE)), 0) AS {name}"
    return f"CAST(NULL AS DOUBLE) AS {name}"


def _max(columns: set[str], name: str) -> str:
    if name in columns:
        return f"MAX(TRY_CAST({name} AS DOUBLE)) AS {name}"
    return f"CAST(NULL AS DOUBLE) AS {name}"


def _weighted_avg(columns: set[str], name: str, weight: str = "request_count") -> str:
    if name not in columns:
        return f"CAST(NULL AS DOUBLE) AS {name}"
    if weight in columns:
        return f"""CASE WHEN SUM(COALESCE(TRY_CAST({weight} AS DOUBLE), 0)) > 0
          THEN SUM(TRY_CAST({name} AS DOUBLE) * COALESCE(TRY_CAST({weight} AS DOUBLE), 0))
               / SUM(COALESCE(TRY_CAST({weight} AS DOUBLE), 0))
          ELSE AVG(TRY_CAST({name} AS DOUBLE)) END AS {name}"""
    return f"AVG(TRY_CAST({name} AS DOUBLE)) AS {name}"


async def _columns(table: str) -> set[str]:
    rows = await trino_execute(f"DESCRIBE {table}", timeout_s=15)
    return {
        str(row.get("Column") or row.get("column") or row.get("column_name") or "")
        for row in (rows or [])
        if row
    }


def _daily_select(columns: set[str]) -> str:
    utilization_avg = (
        """CASE WHEN SUM(COALESCE(TRY_CAST(provisioned_ptus AS DOUBLE), 0)) > 0
          THEN SUM(TRY_CAST(utilization_avg AS DOUBLE) * COALESCE(TRY_CAST(provisioned_ptus AS DOUBLE), 0))
               / SUM(COALESCE(TRY_CAST(provisioned_ptus AS DOUBLE), 0))
          ELSE AVG(TRY_CAST(utilization_avg AS DOUBLE)) END AS utilization_avg"""
        if "utilization_avg" in columns
        else "CAST(NULL AS DOUBLE) AS utilization_avg"
    )
    quality = (
        "MAX(CASE WHEN TRY_CAST(utilization_quality_anomaly AS BOOLEAN) THEN 1 ELSE 0 END) AS utilization_quality_anomaly"
        if "utilization_quality_anomaly" in columns
        else "CAST(NULL AS BIGINT) AS utilization_quality_anomaly"
    )
    sums = [
        "input_tokens_sum", "output_tokens_sum", "total_tokens_sum", "request_count",
        "success_count", "client_error_count", "server_error_count", "rate_limited_count",
        "spillover_request_count", "streaming_request_count", "non_streaming_request_count",
    ]
    weighted = [
        "time_to_response_avg_ms", "time_to_last_byte_avg_ms", "normalized_ttft_avg_ms",
        "normalized_tbt_avg_ms", "context_cache_match_rate_avg", "availability_rate_avg",
    ]
    maxima = [
        "utilization_max", "utilization_p95", "request_peak_per_minute",
        "time_to_response_max_ms", "normalized_ttft_max_ms", "normalized_tbt_max_ms",
        "tokens_per_second_max",
    ]
    measures = [*(_sum(columns, name) for name in sums)]
    measures.extend(_weighted_avg(columns, name) for name in weighted)
    measures.extend(_max(columns, name) for name in maxima)
    measures.append(
        "AVG(TRY_CAST(tokens_per_second_avg AS DOUBLE)) AS tokens_per_second_avg"
        if "tokens_per_second_avg" in columns else "CAST(NULL AS DOUBLE) AS tokens_per_second_avg"
    )
    measures.extend([
        utilization_avg,
        "SUM(CASE WHEN TRY_CAST(utilization_max AS DOUBLE) >= 80 THEN 1 ELSE 0 END) AS hours_at_or_above_80"
        if "utilization_max" in columns else "CAST(NULL AS BIGINT) AS hours_at_or_above_80",
        "SUM(CASE WHEN TRY_CAST(utilization_max AS DOUBLE) >= 95 THEN 1 ELSE 0 END) AS hours_at_or_above_95"
        if "utilization_max" in columns else "CAST(NULL AS BIGINT) AS hours_at_or_above_95",
        quality,
        "COUNT(*) AS sample_count",
    ])
    return ",\n        ".join(measures)


async def refresh_azure_ptu_dimon(*, days: int = 400) -> dict[str, Any]:
    """Reconstrói atomicamente o histórico operacional e financeiro recente."""
    # PyArrow/PyIceberg são dependências do worker de materialização, não da
    # API de leitura nem dos testes unitários do dashboard.
    from app.services import iceberg_service as iceberg

    columns = await _columns(GOLD_SQL)
    if not columns:
        raise RuntimeError("Gold Azure PTU indisponível ou sem colunas")
    end = datetime.now(timezone.utc) - timedelta(minutes=10)
    start = end - timedelta(days=max(1, days))
    start_iso = start.strftime("%Y-%m-%dT%H:00:00Z")
    end_iso = end.strftime("%Y-%m-%dT%H:00:00Z")
    where = _gold_metric_hour_filter(start_iso, end_iso)
    staging = f"{MART_TABLE}_stg_{time.time_ns()}"
    iceberg.ensure_namespace(MART_NAMESPACE)
    sql = f"""
      CREATE TABLE iceberg.{MART_NAMESPACE}.{staging}
      WITH (partitioning = ARRAY['_billing_period']) AS
      SELECT
        substr(CAST(metric_hour AS VARCHAR), 1, 10) AS metric_day,
        substr(CAST(metric_hour AS VARCHAR), 1, 7) AS _billing_period,
        CAST(connection_id AS BIGINT) AS connection_id,
        CAST(account_resource_id AS VARCHAR) AS account_resource_id,
        CAST(deployment_name AS VARCHAR) AS deployment_name,
        CAST(model_name AS VARCHAR) AS model_name,
        CAST(model_version AS VARCHAR) AS model_version,
        CAST(region AS VARCHAR) AS region,
        CAST(sku_name AS VARCHAR) AS sku_name,
        MAX(CAST(project_hint AS VARCHAR)) AS project_hint,
        MAX(TRY_CAST(provisioned_ptus AS DOUBLE)) AS provisioned_ptus,
        {_daily_select(columns)}
      FROM {GOLD_SQL}
      WHERE {where}
      GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9
    """
    try:
        await trino_execute(sql, timeout_s=1800)
        count_rows = await trino_execute(
            f"SELECT COUNT(*) AS row_count FROM iceberg.{MART_NAMESPACE}.{staging}", timeout_s=120,
        )
        row_count = int((count_rows[0] if count_rows else {}).get("row_count") or 0)
        if row_count:
            iceberg.swap_staged_table(MART_TABLE, staging, namespace=MART_NAMESPACE, known_row_count=row_count)
        else:
            iceberg.cleanup_staged_table(staging, namespace=MART_NAMESPACE)
        global _DIMON_COLUMNS
        _DIMON_COLUMNS = None
        return {"table": MART_TABLE, "rows": row_count, "days": days}
    except Exception:
        try:
            iceberg.cleanup_staged_table(staging, namespace=MART_NAMESPACE)
        except Exception:
            pass
        raise


async def load_dimon_metrics(
    *,
    hours: int,
    connection_ids: list[int] | None = None,
    project_hints: list[str] | None = None,
    grain: str = "day",
    timeout_s: float = DASHBOARD_TRINO_TIMEOUT_S,
) -> list[dict[str, Any]]:
    """Lê a Dimon diária; nunca consulta a Gold como fallback silencioso."""
    global _DIMON_COLUMNS
    if _DIMON_COLUMNS is None:
        try:
            _DIMON_COLUMNS = await _columns(DIMON_SQL)
        except Exception as exc:
            logger.warning("Dimon Azure PTU indisponível: %s", exc)
            return []
    start_iso, end_iso = _gold_metrics_window(hours)
    start_day, end_day = start_iso[:10], end_iso[:10]
    conn_filter = _gold_connection_filter(connection_ids)
    project_filter = _gold_project_hint_filter(project_hints)
    bucket = (
        "concat(substr(metric_day, 1, 7), '-01T00:00:00Z')"
        if grain == "month" else "concat(metric_day, 'T00:00:00Z')"
    )
    columns = _DIMON_COLUMNS
    sums = [
        "input_tokens_sum", "output_tokens_sum", "total_tokens_sum", "request_count",
        "success_count", "client_error_count", "server_error_count", "rate_limited_count",
        "spillover_request_count", "streaming_request_count", "non_streaming_request_count",
        "hours_at_or_above_80", "hours_at_or_above_95", "sample_count",
    ]
    measures = [*(_sum(columns, name) for name in sums)]
    measures.extend([
        _weighted_avg(columns, name) for name in (
            "time_to_response_avg_ms", "time_to_last_byte_avg_ms", "normalized_ttft_avg_ms",
            "normalized_tbt_avg_ms", "context_cache_match_rate_avg", "availability_rate_avg",
        )
    ])
    measures.extend(_max(columns, name) for name in (
        "utilization_max", "utilization_p95", "request_peak_per_minute", "time_to_response_max_ms",
        "normalized_ttft_max_ms", "normalized_tbt_max_ms", "tokens_per_second_max",
    ))
    measures.extend([
        "AVG(TRY_CAST(tokens_per_second_avg AS DOUBLE)) AS tokens_per_second_avg",
        "AVG(TRY_CAST(utilization_avg AS DOUBLE)) AS utilization_avg",
        "MAX(TRY_CAST(utilization_quality_anomaly AS BIGINT)) AS utilization_quality_anomaly",
    ])
    sql = f"""
      SELECT CAST(connection_id AS BIGINT) AS connection_id,
             {bucket} AS metric_hour,
             CAST(account_resource_id AS VARCHAR) AS account_resource_id,
             CAST(deployment_name AS VARCHAR) AS deployment_name,
             CAST(model_name AS VARCHAR) AS model_name,
             CAST(model_version AS VARCHAR) AS model_version,
             CAST(region AS VARCHAR) AS region,
             CAST(sku_name AS VARCHAR) AS sku_name,
             MAX(TRY_CAST(provisioned_ptus AS DOUBLE)) AS provisioned_ptus,
             MAX(CAST(project_hint AS VARCHAR)) AS project_hint,
             MAX(CAST(project_hint AS VARCHAR)) AS project_key,
             {', '.join(measures)}
      FROM {DIMON_SQL}
      WHERE metric_day >= '{start_day}' AND metric_day <= '{end_day}'{conn_filter}{project_filter}
      GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
      ORDER BY metric_hour DESC
    """
    try:
        rows = await trino_execute(sql, timeout_s=timeout_s)
        return [dict(row) for row in (rows or [])]
    except Exception as exc:
        logger.warning("Dimon Azure PTU query failed: %s", exc, exc_info=True)
        return []


async def load_project_token_totals(
    project_key: str,
    periods: list[str] | None,
    cancel_event: asyncio.Event | None = None,
) -> dict[str, dict[str, float]]:
    """Tokens por mês para a correlação financeira Aura Brain/Vivo Mais."""
    period_filter = ""
    if periods:
        safe_periods = ", ".join("'" + p.replace("'", "''") + "'" for p in periods)
        period_filter = f" AND replace(_billing_period, '-', '') IN ({safe_periods})"
    project = project_key.replace("'", "''").lower()
    rows = await trino_execute(
        f"""
        SELECT replace(_billing_period, '-', '') AS period,
               COALESCE(SUM(TRY_CAST(input_tokens_sum AS DOUBLE)), 0) AS input_tokens,
               COALESCE(SUM(TRY_CAST(output_tokens_sum AS DOUBLE)), 0) AS output_tokens,
               COALESCE(SUM(TRY_CAST(total_tokens_sum AS DOUBLE)), 0) AS total_tokens
        FROM {DIMON_SQL}
        WHERE lower(CAST(project_hint AS VARCHAR)) = '{project}'{period_filter}
        GROUP BY 1
        """,
        timeout_s=60,
        cancel_event=cancel_event,
    )
    return {
        str(row.get("period")): {
            "input_tokens": float(row.get("input_tokens") or 0),
            "output_tokens": float(row.get("output_tokens") or 0),
            "total_tokens": float(row.get("total_tokens") or 0),
        }
        for row in (rows or [])
    }


async def load_project_tokens_last_30d(
    project_key: str,
    cancel_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    """Consumo móvel dos últimos 30 dias, base oficial do custo por MM."""
    project = project_key.replace("'", "''").lower()
    rows = await trino_execute(
        f"""
        SELECT COALESCE(SUM(TRY_CAST(input_tokens_sum AS DOUBLE)), 0) AS input_tokens,
               COALESCE(SUM(TRY_CAST(output_tokens_sum AS DOUBLE)), 0) AS output_tokens,
               COALESCE(SUM(TRY_CAST(total_tokens_sum AS DOUBLE)), 0) AS total_tokens,
               MIN(metric_day) AS date_from,
               MAX(metric_day) AS date_to
        FROM {DIMON_SQL}
        WHERE lower(CAST(project_hint AS VARCHAR)) = '{project}'
          AND CAST(metric_day AS DATE) >= current_date - INTERVAL '30' DAY
          AND CAST(metric_day AS DATE) <= current_date
        """,
        timeout_s=60,
        cancel_event=cancel_event,
    )
    row = rows[0] if rows else {}
    return {
        "input_tokens": float(row.get("input_tokens") or 0),
        "output_tokens": float(row.get("output_tokens") or 0),
        "total_tokens": float(row.get("total_tokens") or 0),
        "date_from": row.get("date_from"),
        "date_to": row.get("date_to"),
    }
