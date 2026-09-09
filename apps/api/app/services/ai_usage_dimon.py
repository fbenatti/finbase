"""Materializações leves para dashboards de consumo e licenças de IA.

As tabelas Gold continuam sendo a camada normalizada e auditável. Os
dashboards leem estas Dimons, que preservam a granularidade necessária para
ranking e drilldown sem repetir scans nas tabelas normalizadas completas.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from app.services.trino_service import execute as trino_execute

logger = logging.getLogger(__name__)

DIMON_NAMESPACE = "dimon"
AI_CONSUMPTION_TABLE = "dest_dm_ai_consumption_daily"
AI_LICENSE_TABLE = "dest_dm_ai_license_snapshot"
COPILOT_USAGE_TABLE = "dest_dm_copilot_usage_daily"
COPILOT_LICENSE_TABLE = "dest_dm_copilot_license_snapshot"
COPILOT_USER_PERIOD_TABLE = "dest_dm_copilot_user_period"


def _number(name: str) -> str:
    return f"COALESCE(TRY_CAST({name} AS DOUBLE), 0)"


async def _publish(table: str, select_sql: str, *, partition_column: str = "_billing_period") -> int:
    """Publica uma CTAS validada usando o mesmo swap atômico das demais Dimons."""
    from app.services import iceberg_service as iceberg

    staging = f"{table}_stg_{time.time_ns()}"
    iceberg.ensure_namespace(DIMON_NAMESPACE)
    sql = f"""
      CREATE TABLE iceberg.{DIMON_NAMESPACE}.{staging}
      WITH (partitioning = ARRAY['{partition_column}']) AS
      {select_sql}
    """
    try:
        await trino_execute(sql, timeout_s=1800)
        rows = await trino_execute(
            f"SELECT COUNT(*) AS row_count FROM iceberg.{DIMON_NAMESPACE}.{staging}",
            timeout_s=180,
        )
        row_count = int((rows[0] if rows else {}).get("row_count") or 0)
        if row_count <= 0:
            raise RuntimeError(f"Materialização {table} não produziu linhas")
        iceberg.swap_staged_table(
            table,
            staging,
            namespace=DIMON_NAMESPACE,
            known_row_count=row_count,
        )
        return row_count
    except Exception:
        try:
            iceberg.cleanup_staged_table(staging, namespace=DIMON_NAMESPACE)
        except Exception:
            pass
        raise


def _consumption_select() -> str:
    dimensions = [
        "provider", "productname", "modelname", "recordtype", "useremail", "userlogin",
        "aggregationlevel", "editor", "language", "departamento",
        *(f"departamento_nivel_{level}" for level in range(1, 8)),
    ]
    dim_select = ",\n        ".join(f"CAST({name} AS VARCHAR) AS {name}" for name in dimensions)
    group_positions = ", ".join(str(position) for position in range(1, 3 + len(dimensions)))
    sums = [
        "inputtokens", "outputtokens", "cachereadtokens", "cachewritetokens", "totaltokens",
        "requestcount", "suggestionscount", "acceptancescount", "linessuggested", "linesaccepted",
        "chatturns", "chatacceptances", "activeuserscount", "audiodurationseconds", "notebookcount",
        "allocatedcost", "billedcost", "realizado", "creditsamount", "grossamount",
        "discountamount", "netamount", "listcost",
    ]
    measures = ",\n        ".join(f"SUM({_number(name)}) AS {name}" for name in sums)
    return f"""
      SELECT
        substr(CAST(usagedate AS VARCHAR), 1, 10) AS usagedate,
        CAST(_billing_period AS VARCHAR) AS _billing_period,
        {dim_select},
        {measures},
        COUNT(*) AS source_rows,
        SUM(CASE WHEN {_number('totaltokens')} > 0 THEN 1 ELSE 0 END) AS rows_with_tokens,
        SUM(CASE WHEN {_number('inputtokens')} + {_number('outputtokens')} > 0 THEN 1 ELSE 0 END) AS rows_with_io_tokens,
        SUM(CASE WHEN COALESCE(NULLIF({_number('allocatedcost')}, 0), NULLIF({_number('realizado')}, 0), {_number('billedcost')}) > 0 THEN 1 ELSE 0 END) AS rows_with_cost
      FROM iceberg.gold.dest_consumo_ia
      WHERE usagedate IS NOT NULL AND _billing_period IS NOT NULL
      GROUP BY {group_positions}
    """


def _license_select() -> str:
    return """
      SELECT
        CAST(useremail AS VARCHAR) AS useremail,
        CAST(userlogin AS VARCHAR) AS userlogin,
        CAST(userid AS VARCHAR) AS userid,
        CAST(username AS VARCHAR) AS username,
        CAST(userrole AS VARCHAR) AS userrole,
        CAST(provider AS VARCHAR) AS provider,
        CAST(productname AS VARCHAR) AS productname,
        CAST(licenseskuid AS VARCHAR) AS licenseskuid,
        CAST(licenseskuname AS VARCHAR) AS licenseskuname,
        CAST(licensestatus AS VARCHAR) AS licensestatus,
        CAST(organizationid AS VARCHAR) AS organizationid,
        CAST(lastactivityat AS VARCHAR) AS lastactivityat,
        TRY_CAST(dayssincelastactivity AS DOUBLE) AS dayssincelastactivity,
        CAST(activitystatus AS VARCHAR) AS activitystatus,
        TRY_CAST(allocatedlicensecost AS DOUBLE) AS allocatedlicensecost,
        TRY_CAST(listcost AS DOUBLE) AS listcost,
        CAST(departamento AS VARCHAR) AS departamento,
        CAST(departamento_nivel_1 AS VARCHAR) AS departamento_nivel_1,
        CAST(departamento_nivel_2 AS VARCHAR) AS departamento_nivel_2,
        CAST(departamento_nivel_3 AS VARCHAR) AS departamento_nivel_3,
        CAST(departamento_nivel_4 AS VARCHAR) AS departamento_nivel_4,
        CAST(departamento_nivel_5 AS VARCHAR) AS departamento_nivel_5,
        CAST(departamento_nivel_6 AS VARCHAR) AS departamento_nivel_6,
        CAST(departamento_nivel_7 AS VARCHAR) AS departamento_nivel_7,
        CAST(snapshotdate AS VARCHAR) AS snapshotdate,
        CAST(_billing_period AS VARCHAR) AS _billing_period
      FROM (
        SELECT source.*,
          ROW_NUMBER() OVER (
            PARTITION BY lower(COALESCE(NULLIF(useremail, ''), NULLIF(userlogin, ''))),
                         lower(provider), lower(productname), _billing_period
            ORDER BY snapshotdate DESC NULLS LAST, licenseupdatedat DESC NULLS LAST,
                     lastactivityat DESC NULLS LAST
          ) AS dimon_rn
        FROM iceberg.gold.dest_licencas_ia source
        WHERE _billing_period IS NOT NULL
      ) ranked
      WHERE dimon_rn = 1
    """


def _copilot_usage_select() -> str:
    return """
      SELECT
        CAST(day AS VARCHAR) AS day,
        CAST(assignee_login AS VARCHAR) AS assignee_login,
        CAST(aggregation_level AS VARCHAR) AS aggregation_level,
        CAST(model AS VARCHAR) AS model,
        TRY_CAST(total_suggestions_count AS DOUBLE) AS total_suggestions_count,
        TRY_CAST(total_acceptances_count AS DOUBLE) AS total_acceptances_count,
        TRY_CAST(total_lines_suggested AS DOUBLE) AS total_lines_suggested,
        TRY_CAST(total_lines_accepted AS DOUBLE) AS total_lines_accepted,
        TRY_CAST(total_chat_turns AS DOUBLE) AS total_chat_turns,
        TRY_CAST(total_chat_acceptances AS DOUBLE) AS total_chat_acceptances,
        TRY_CAST(total_active_users AS DOUBLE) AS total_active_users,
        CAST(editor AS VARCHAR) AS editor,
        CAST(language AS VARCHAR) AS language,
        CAST(source_kind AS VARCHAR) AS source_kind,
        TRY_CAST(ai_credits AS DOUBLE) AS ai_credits,
        TRY_CAST(gross_amount AS DOUBLE) AS gross_amount,
        TRY_CAST(discount_amount AS DOUBLE) AS discount_amount,
        TRY_CAST(net_amount AS DOUBLE) AS net_amount,
        CAST(billing_currency AS VARCHAR) AS billing_currency,
        TRY_CAST(input_tokens AS DOUBLE) AS input_tokens,
        TRY_CAST(output_tokens AS DOUBLE) AS output_tokens,
        TRY_CAST(cache_read_tokens AS DOUBLE) AS cache_read_tokens,
        TRY_CAST(cache_write_tokens AS DOUBLE) AS cache_write_tokens,
        TRY_CAST(monthly_quota AS DOUBLE) AS monthly_quota,
        CAST(_billing_period AS VARCHAR) AS _billing_period,
        CAST(_ingested_at AS VARCHAR) AS _ingested_at
      FROM (
        SELECT source.*,
          ROW_NUMBER() OVER (
            PARTITION BY day, lower(assignee_login), model, language, editor, source_kind, _billing_period
            ORDER BY _ingested_at DESC NULLS LAST
          ) AS dimon_rn
        FROM iceberg.bronze.raw_github_token source
        WHERE _billing_period IS NOT NULL
      ) ranked
      WHERE dimon_rn = 1
    """


def _copilot_license_select() -> str:
    return """
      SELECT
        CAST(assignee_login AS VARCHAR) AS assignee_login,
        CAST(assignee_id AS VARCHAR) AS id,
        CAST(assignee_email AS VARCHAR) AS email,
        CAST(assigning_team_name AS VARCHAR) AS team,
        CAST(created_at AS VARCHAR) AS created_at,
        CAST(updated_at AS VARCHAR) AS updated_at,
        CAST(pending_cancellation_date AS VARCHAR) AS pending_cancellation_date,
        CAST(last_activity_at AS VARCHAR) AS last_activity_at,
        CAST(last_activity_editor AS VARCHAR) AS last_activity_editor,
        CAST(plan_type AS VARCHAR) AS plan_type,
        CAST(snapshot_date AS VARCHAR) AS snapshot_date,
        CAST(_billing_period AS VARCHAR) AS _billing_period,
        CAST(_ingested_at AS VARCHAR) AS _ingested_at
      FROM (
        SELECT source.*,
          ROW_NUMBER() OVER (
            PARTITION BY lower(assignee_login), snapshot_date, _billing_period
            ORDER BY _ingested_at DESC NULLS LAST
          ) AS dimon_rn
        FROM iceberg.bronze.raw_github_license source
        WHERE _billing_period IS NOT NULL
      ) ranked
      WHERE dimon_rn = 1
    """


def _copilot_user_period_select() -> str:
    """Resumo financeiro usado pela relação de desenvolvedores e pelos KPIs."""
    return """
      SELECT
        CAST(_billing_period AS VARCHAR) AS _billing_period,
        lower(CAST(assignee_login AS VARCHAR)) AS assignee_login,
        SUM(COALESCE(TRY_CAST(ai_credits AS DOUBLE), TRY_CAST(total_chat_turns AS DOUBLE), 0)) AS ai_credits,
        SUM(COALESCE(TRY_CAST(gross_amount AS DOUBLE), 0)) AS gross_amount,
        SUM(COALESCE(TRY_CAST(discount_amount AS DOUBLE), 0)) AS discount_amount,
        SUM(COALESCE(TRY_CAST(net_amount AS DOUBLE), 0)) AS net_amount,
        MAX(COALESCE(TRY_CAST(monthly_quota AS DOUBLE), 0)) AS monthly_quota,
        MIN(CAST(day AS VARCHAR)) AS period_start,
        MAX(CAST(day AS VARCHAR)) AS period_end,
        COUNT(DISTINCT day) AS period_days
      FROM iceberg.dimon.dest_dm_copilot_usage_daily
      WHERE assignee_login IS NOT NULL AND assignee_login <> ''
        AND source_kind IN ('ai_usage_report', 'billing_report')
        AND (source_kind = 'ai_usage_report' OR model = 'copilot_ai_credit')
      GROUP BY 1, 2
    """


async def refresh_ai_usage_dimons(only_tables: list[str] | None = None) -> dict[str, Any]:
    """Reconstrói as Dimons auxiliares pela fila oficial de Dimon."""
    definitions = [
        (AI_CONSUMPTION_TABLE, _consumption_select()),
        (AI_LICENSE_TABLE, _license_select()),
        (COPILOT_USAGE_TABLE, _copilot_usage_select()),
        (COPILOT_LICENSE_TABLE, _copilot_license_select()),
        (COPILOT_USER_PERIOD_TABLE, _copilot_user_period_select()),
    ]
    if only_tables:
        wanted = set(only_tables)
        definitions = [(table, sql) for table, sql in definitions if table in wanted]
    results: dict[str, int] = {}
    for table, select_sql in definitions:
        results[table] = await _publish(table, select_sql)
        logger.info("AI Dimon refreshed: %s (%d rows)", table, results[table])
    return {"namespace": DIMON_NAMESPACE, "tables": results}
