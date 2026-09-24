"""Materializações leves para dashboards de consumo e licenças de IA.

As tabelas Gold continuam sendo a camada normalizada e auditável. Os
dashboards leem estas cubos Gold, que preservam a granularidade necessária para
ranking e drilldown sem repetir scans nas tabelas normalizadas completas.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from app.services.trino_service import execute as trino_execute
from app.services import lakehouse_layers as layers
from app.services.gold_materialization import GOLD
from app.services.gold_governance import publish_gold_cube

logger = logging.getLogger(__name__)

GOLD = layers.GOLD
AI_CONSUMPTION_TABLE = "dest_dm_ai_consumption_daily"
AI_LICENSE_TABLE = "dest_dm_ai_license_snapshot"
COPILOT_USAGE_TABLE = "dest_dm_copilot_usage_daily"
COPILOT_LICENSE_TABLE = "dest_dm_copilot_license_snapshot"
COPILOT_USER_PERIOD_TABLE = "dest_dm_copilot_user_period"


#: Código da `gold_rule` de cada tabela auxiliar. O manifesto usa o código da
#: regra, para que a linha do manifesto e a linha da governança falem do mesmo
#: objeto.
_CUBE_CODES: dict[str, str] = {
    AI_CONSUMPTION_TABLE: "ai_consumption_daily",
    AI_LICENSE_TABLE: "ai_license_snapshot",
    COPILOT_USAGE_TABLE: "copilot_usage_daily",
    COPILOT_LICENSE_TABLE: "copilot_license_snapshot",
    COPILOT_USER_PERIOD_TABLE: "copilot_user_period",
}


def _cube_code_for(table: str) -> str:
    """Código da regra para uma tabela auxiliar; a própria tabela como fallback."""
    return _CUBE_CODES.get(table, table)


def _number(name: str) -> str:
    return f"COALESCE(TRY_CAST({name} AS DOUBLE), 0)"


async def _publish(
    table: str,
    select_sql: str,
    *,
    partition_column: str = "_billing_period",
    cube_code: str | None = None,
) -> int:
    """Publica a Gold de IA pelo caminho governado.

    Antes chamava staging + swap direto, criando um quarto caminho de publicação
    sem manifesto: as tabelas existiam, mas não havia registro de fontes, hash,
    linhas nem rollback. Agora delega a `gold_governance.publish_gold_cube`, que é
    o único ponto de publicação da Gold.

    Mantém a semântica anterior de recusar materialização vazia
    (`empty_is_error=True`): para estas tabelas, zero linha já foi tratado como
    falha pelo dashboard.
    """
    resultado = await publish_gold_cube(
        cube_code=cube_code or table.removeprefix("dest_dm_"),
        table=table,
        select_sql=select_sql,
        namespace=GOLD,
        partition_column=partition_column,
        empty_is_error=True,
    )
    return resultado.rows


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
      FROM iceberg.silver.dest_consumo_ia
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
          ) AS gold_rn
        FROM iceberg.silver.dest_licencas_ia source
        WHERE _billing_period IS NOT NULL
      ) ranked
      WHERE gold_rn = 1
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
          ) AS gold_rn
        FROM iceberg.bronze.raw_github_token source
        WHERE _billing_period IS NOT NULL
      ) ranked
      WHERE gold_rn = 1
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
          ) AS gold_rn
        FROM iceberg.bronze.raw_github_license source
        WHERE _billing_period IS NOT NULL
      ) ranked
      WHERE gold_rn = 1
    """


def _copilot_user_period_select() -> str:
    """Resumo financeiro usado pela relação de desenvolvedores e pelos KPIs."""
    return f"""
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
      FROM iceberg.{GOLD}.dest_dm_copilot_usage_daily
      WHERE assignee_login IS NOT NULL AND assignee_login <> ''
        AND source_kind IN ('ai_usage_report', 'billing_report')
        AND (source_kind = 'ai_usage_report' OR model = 'copilot_ai_credit')
      GROUP BY 1, 2
    """


#: Cubos que podem ser construídos competência a competência. O agregado é por
#: competência (o `GROUP BY` inclui `_billing_period`), então fatiar e concatenar
#: produz o mesmo resultado — e mantém cada consulta dentro do que um worker de
#: 2,44 GiB sustenta.
PERIOD_SLICED_TABLES: frozenset[str] = frozenset(
    {AI_CONSUMPTION_TABLE, COPILOT_USAGE_TABLE}
)

#: Cubos derivados de outro cubo. Precisam rodar **depois** de o cubo de origem
#: estar publicado — a origem é o cubo Gold, não a Silver.
DERIVED_TABLES: dict[str, str] = {COPILOT_USER_PERIOD_TABLE: COPILOT_USAGE_TABLE}


def _sliced_select(select_sql: str, billing_period: str) -> str:
    """Envolve o SELECT com o filtro de competência.

    Medido em 24/09/2026: `silver.dest_consumo_ia` tem 15.283.192 linhas
    distribuídas em competências de ~1 a 2 M. A agregação de tudo numa CTAS derruba
    o worker; por competência, cabe.
    """
    alvo = "'" + str(billing_period).replace("'", "''") + "'"
    return (
        f"SELECT * FROM (\n{select_sql}\n) AS slice_src "
        f"WHERE CAST(_billing_period AS VARCHAR) = {alvo}"
    )


async def _periods_for(table: str) -> list[str]:
    """Competências presentes na origem de um cubo fatiado."""
    origem = {
        AI_CONSUMPTION_TABLE: "iceberg.silver.dest_consumo_ia",
        COPILOT_USAGE_TABLE: "iceberg.bronze.raw_github_token",
    }[table]
    linhas = await trino_execute(
        f"SELECT DISTINCT CAST(_billing_period AS VARCHAR) AS bp FROM {origem} "
        f"WHERE _billing_period IS NOT NULL ORDER BY 1",
        timeout_s=900,
    )
    return [
        str(r.get("bp"))
        for r in linhas or []
        if str(r.get("bp") or "").isdigit() and len(str(r.get("bp"))) == 6
    ]


async def _accumulate(
    table: str, slices: list[tuple[str, str]], *, cube_code: str
) -> int:
    """Publica a tabela a partir das fatias já construídas, com troca atômica.

    As fatias foram publicadas em tabelas próprias; aqui elas são unidas numa única
    CTAS e trocadas de uma vez, para que o leitor nunca veja um cubo pela metade.
    """
    from app.services import iceberg_service as iceberg
    from app.services.gold_governance import (
        current_snapshot_id,
        extract_sources,
        record_frame_publication,
    )

    if not slices:
        return 0
    uniao = "\nUNION ALL\n".join(
        f"SELECT * FROM iceberg.{layers.GOLD}.{nome}" for nome, _ in slices
    )
    staging = f"{table}_stg_{time.time_ns()}"
    try:
        await trino_execute(
            f"CREATE TABLE iceberg.{layers.GOLD}.{staging} AS {uniao}", timeout_s=3600
        )
        contagem = await trino_execute(
            f"SELECT COUNT(*) AS n FROM iceberg.{layers.GOLD}.{staging}", timeout_s=300
        )
        linhas = int((contagem[0] if contagem else {}).get("n") or 0)
        anterior = current_snapshot_id(layers.GOLD, table)
        if anterior is None:
            iceberg.swap_staged_table(
                table, staging, namespace=layers.GOLD, known_row_count=linhas
            )
        else:
            await trino_execute(
                f"DELETE FROM iceberg.{layers.GOLD}.{table}", timeout_s=600
            )
            await trino_execute(
                f"INSERT INTO iceberg.{layers.GOLD}.{table} "
                f"SELECT * FROM iceberg.{layers.GOLD}.{staging}",
                timeout_s=3600,
            )
    finally:
        try:
            await trino_execute(
                f"DROP TABLE IF EXISTS iceberg.{layers.GOLD}.{staging}", timeout_s=300
            )
        except Exception:  # noqa: BLE001 — limpeza não é caminho crítico
            pass
    # Assíncrona: esta função já roda em contexto async, e a versão `_blocking`
    # recusa registrar com loop ativo — em modo obrigatório ela levanta.
    await record_frame_publication(
        cube_code=_cube_code_for(table),
        physical_table=table,
        rows_written=int(linhas),
        namespace=layers.GOLD,
        source_tables=extract_sources(uniao),
    )
    return linhas


async def refresh_ai_usage_gold_cubes(only_tables: list[str] | None = None) -> dict[str, Any]:
    """Reconstrói os cubos Gold auxiliares pela fila oficial de Gold.

    Os cubos fatiados por competência (consumo de IA e uso do Copilot) são
    construídos fatia a fatia e acumulados numa única publicação. Os cubos pequenos
    (licenças) seguem numa consulta. Os derivados rodam por último, porque leem o
    cubo de origem já publicado.
    """
    definitions = [
        (AI_CONSUMPTION_TABLE, _consumption_select),
        (AI_LICENSE_TABLE, _license_select),
        (COPILOT_USAGE_TABLE, _copilot_usage_select),
        (COPILOT_LICENSE_TABLE, _copilot_license_select),
        (COPILOT_USER_PERIOD_TABLE, _copilot_user_period_select),
    ]
    if only_tables:
        wanted = set(only_tables)
        definitions = [(t, f) for t, f in definitions if t in wanted]

    results: dict[str, int] = {}
    independent = [(t, f) for t, f in definitions if t not in DERIVED_TABLES]
    derived = [(t, f) for t, f in definitions if t in DERIVED_TABLES]

    for table, builder in independent:
        if table in PERIOD_SLICED_TABLES:
            periodos = await _periods_for(table)
            fatias: list[tuple[str, str]] = []
            for periodo in periodos:
                nome_fatia = f"{table}_slice_{periodo}"
                linhas = await _publish(
                    nome_fatia,
                    _sliced_select(builder(), periodo),
                    cube_code=_cube_code_for(table),
                )
                if linhas:
                    fatias.append((nome_fatia, periodo))
            results[table] = await _accumulate(
                table, fatias, cube_code=_cube_code_for(table)
            )
            for nome, _ in fatias:
                try:
                    await trino_execute(
                        f"DROP TABLE IF EXISTS iceberg.{layers.GOLD}.{nome}", timeout_s=300
                    )
                except Exception:  # noqa: BLE001
                    pass
        else:
            results[table] = await _publish(
                table, builder(), cube_code=_cube_code_for(table)
            )
        logger.info("AI Gold refreshed: %s (%d rows)", table, results[table])

    for table, builder in derived:
        results[table] = await _publish(
            table, builder(), cube_code=_cube_code_for(table)
        )
        logger.info("AI Gold refreshed (derivado): %s (%d rows)", table, results[table])

    return {"namespace": GOLD, "tables": results}
