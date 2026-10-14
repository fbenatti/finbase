"""Migração idempotente e configuração inicial da gestão de anomalias.

O projeto evolui o schema em um bloco DDL idempotente no startup
(`app/main.py`), porque ``Base.metadata.create_all`` cria tabelas ausentes mas
**não** evolui tabelas existentes. Para as tabelas de anomalia — que são novas e
têm um contrato de colunas extenso — a evolução é derivada do próprio
metadata do SQLAlchemy, o que elimina divergência entre modelo e banco.

Regras:

* nada aqui apaga dados;
* qualquer adição é ``IF NOT EXISTS``;
* executar duas vezes seguidas é seguro;
* a configuração inicial nasce **desabilitada** (aceite E1).
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import CreateTable

from app.database import Base
from app.models import anomaly as anomaly_models  # noqa: F401  (registra o metadata)
# Tabelas referenciadas por chave estrangeira: sem estes imports o metadata não
# resolve `forecast_config`, `etl_task` e `user_account`, e a compilação do DDL
# falha com NoReferencedTableError.
from app.models import forecast as _forecast_models  # noqa: F401
from app.models import etl_task as _etl_task_models  # noqa: F401
from app.models import identity as _identity_models  # noqa: F401
from app.models import destination as _destination_models  # noqa: F401

logger = logging.getLogger(__name__)

ANOMALY_TABLE_NAMES: tuple[str, ...] = (
    "anomaly_config",
    "anomaly_run",
    "anomaly_publication",
    "anomaly_case",
    "anomaly_case_event",
    "anomaly_action",
    "anomaly_notification",
)

# Índices parciais que dependem de expressão e por isso não são recriados
# automaticamente por create_all em bancos que já têm a tabela.
ANOMALY_PARTIAL_INDEXES: tuple[str, ...] = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_anomaly_publication_current "
    "ON anomaly_publication (anomaly_config_id) WHERE is_current",
)

_DEFAULT_CONFIG_NAME = "Detecção de anomalias (padrão)"

# Defaults que precisam existir também em tabelas já criadas.
ANOMALY_MIGRATION_STATEMENTS: tuple[str, ...] = (
    "ALTER TABLE anomaly_config ALTER COLUMN measure_column SET DEFAULT 'realizado_cloud'",
    "ALTER TABLE anomaly_config ALTER COLUMN currency SET DEFAULT 'BRL'",
    "ALTER TABLE anomaly_config ALTER COLUMN source_namespace SET DEFAULT 'silver'",
    "ALTER TABLE anomaly_config ALTER COLUMN type_column SET DEFAULT 'tipo'",
    "ALTER TABLE anomaly_config ALTER COLUMN dimensions SET DEFAULT '[]'::jsonb",
    "ALTER TABLE anomaly_config ALTER COLUMN family_keys SET DEFAULT '[]'::jsonb",
    # `ADD COLUMN` em tabela existente não pode impor NOT NULL. Normaliza as
    # linhas antigas e converge a restrição — idempotente.
    "UPDATE anomaly_config SET family_keys = '[]'::jsonb WHERE family_keys IS NULL",
    "ALTER TABLE anomaly_config ALTER COLUMN family_keys SET NOT NULL",
    "ALTER TABLE anomaly_config ALTER COLUMN profile SET DEFAULT 'BALANCED'",
    "ALTER TABLE anomaly_config ALTER COLUMN daily_quantile SET DEFAULT 0.975",
    "ALTER TABLE anomaly_config ALTER COLUMN min_relative_deviation SET DEFAULT 0.20",
    "ALTER TABLE anomaly_config ALTER COLUMN investigate_score SET DEFAULT 55",
    "ALTER TABLE anomaly_config ALTER COLUMN monthly_tolerance_pct SET DEFAULT 0.15",
    "ALTER TABLE anomaly_config ALTER COLUMN monthly_min_events SET DEFAULT 3",
    """ALTER TABLE anomaly_config ALTER COLUMN materiality_by_currency SET DEFAULT '{"BRL": 100.0}'::jsonb""",
    "ALTER TABLE anomaly_config ALTER COLUMN history_days SET DEFAULT 180",
    "ALTER TABLE anomaly_config ALTER COLUMN calibration_days SET DEFAULT 60",
    "ALTER TABLE anomaly_config ALTER COLUMN horizon_days SET DEFAULT 15",
    "ALTER TABLE anomaly_config ALTER COLUMN first_run_days SET DEFAULT 30",
    "ALTER TABLE anomaly_config ALTER COLUMN reevaluation_days SET DEFAULT 7",
    "ALTER TABLE anomaly_config ALTER COLUMN bucket_days SET DEFAULT 1",
    "ALTER TABLE anomaly_config ALTER COLUMN routing_rules SET DEFAULT '[]'::jsonb",
    """ALTER TABLE anomaly_config ALTER COLUMN sla_hours SET DEFAULT '{"CRITICAL": 4, "HIGH": 24, "DEFAULT": 72}'::jsonb""",
    "ALTER TABLE anomaly_config ALTER COLUMN notification_enabled SET DEFAULT true",
    "ALTER TABLE anomaly_config ALTER COLUMN enabled SET DEFAULT false",
    "ALTER TABLE anomaly_config ALTER COLUMN version SET DEFAULT 1",
    "ALTER TABLE anomaly_run ALTER COLUMN config_snapshot SET DEFAULT '{}'::jsonb",
    "ALTER TABLE anomaly_run ALTER COLUMN errors SET DEFAULT '[]'::jsonb",
    "ALTER TABLE anomaly_case ALTER COLUMN state SET DEFAULT 'NEW'",
    "ALTER TABLE anomaly_case ALTER COLUMN severity SET DEFAULT 'LOW'",
    "ALTER TABLE anomaly_case ALTER COLUMN confidence SET DEFAULT 'UNAVAILABLE'",
    "ALTER TABLE anomaly_case ALTER COLUMN case_version SET DEFAULT 1",
    "ALTER TABLE anomaly_case ALTER COLUMN anomalous_cost_cloud SET DEFAULT 0",
    "ALTER TABLE anomaly_case ALTER COLUMN reason_codes SET DEFAULT '[]'::jsonb",
    "ALTER TABLE anomaly_case ALTER COLUMN applied_rule_refs SET DEFAULT '[]'::jsonb",
    # Convergência dos NOT NULL que só tinham default do ORM: sem isso um INSERT
    # por SQL direto (script operacional, verificação, backfill) falha. Mesmo
    # tratamento já dado a `family_keys`.
    "ALTER TABLE anomaly_case ALTER COLUMN series_dimensions SET DEFAULT '{}'::jsonb",
    "ALTER TABLE anomaly_case_event ALTER COLUMN payload SET DEFAULT '{}'::jsonb",
    "ALTER TABLE anomaly_case_event ALTER COLUMN case_version SET DEFAULT 1",
    "ALTER TABLE anomaly_case ALTER COLUMN evaluation_grain SET DEFAULT 'DAY'",
    "ALTER TABLE anomaly_case ALTER COLUMN data_quality SET DEFAULT 'OK'",
    "ALTER TABLE anomaly_case ALTER COLUMN score SET DEFAULT 0",
    "ALTER TABLE anomaly_case ALTER COLUMN valid_days_streak SET DEFAULT 0",
    "ALTER TABLE anomaly_case ALTER COLUMN reopen_count SET DEFAULT 0",
    "ALTER TABLE anomaly_case ALTER COLUMN first_detected_at SET DEFAULT now()",
    "ALTER TABLE anomaly_case ALTER COLUMN state_changed_at SET DEFAULT now()",
    "ALTER TABLE anomaly_run ALTER COLUMN status SET DEFAULT 'QUEUED'",
    "ALTER TABLE anomaly_run ALTER COLUMN trigger_type SET DEFAULT 'MANUAL'",
    "ALTER TABLE anomaly_run ALTER COLUMN config_version SET DEFAULT 1",
    "ALTER TABLE anomaly_run ALTER COLUMN total_series SET DEFAULT 0",
    "ALTER TABLE anomaly_run ALTER COLUMN processed_series SET DEFAULT 0",
    "ALTER TABLE anomaly_run ALTER COLUMN rows_written SET DEFAULT 0",
    "ALTER TABLE anomaly_run ALTER COLUMN cases_created SET DEFAULT 0",
    "ALTER TABLE anomaly_run ALTER COLUMN cases_updated SET DEFAULT 0",
    "ALTER TABLE anomaly_run ALTER COLUMN notifications_created SET DEFAULT 0",
    "ALTER TABLE anomaly_run ALTER COLUMN selected_models SET DEFAULT '{}'::jsonb",
    "ALTER TABLE anomaly_publication ALTER COLUMN row_count SET DEFAULT 0",
    "ALTER TABLE anomaly_publication ALTER COLUMN series_count SET DEFAULT 0",
    "ALTER TABLE anomaly_publication ALTER COLUMN reference_count SET DEFAULT 0",
    "ALTER TABLE anomaly_publication ALTER COLUMN is_current SET DEFAULT true",
    "ALTER TABLE anomaly_action ALTER COLUMN action_type SET DEFAULT 'CORRECTIVE'",
    "ALTER TABLE anomaly_action ALTER COLUMN status SET DEFAULT 'PLANNED'",
    "ALTER TABLE anomaly_action ALTER COLUMN mandatory SET DEFAULT true",
    "ALTER TABLE anomaly_notification ALTER COLUMN is_finops_queue SET DEFAULT false",
    "ALTER TABLE anomaly_notification ALTER COLUMN severity SET DEFAULT 'LOW'",
    # Histórico imutável também no banco: o listener do ORM cobre a aplicação,
    # o gatilho cobre SQL textual. Casos não são apagados — o ciclo termina em
    # CLOSED, nunca em DELETE.
    """
    CREATE OR REPLACE FUNCTION anomaly_case_event_immutable() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION
            'anomaly_case_event é um histórico imutável: registre um novo evento '
            '(tentativa de % na linha %)', TG_OP, COALESCE(OLD.id, 0);
    END;
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS trg_anomaly_case_event_immutable ON anomaly_case_event",
    """
    CREATE TRIGGER trg_anomaly_case_event_immutable
        BEFORE UPDATE OR DELETE ON anomaly_case_event
        FOR EACH ROW EXECUTE FUNCTION anomaly_case_event_immutable()
    """,
    # Correção de dados estreita: materialidade vazia passa a ter o default de
    # 100 na moeda declarada pela configuração. Idempotente.
    """UPDATE anomaly_config
          SET materiality_by_currency = jsonb_build_object(currency, 100.0)
        WHERE materiality_by_currency IS NULL
           OR materiality_by_currency::jsonb = '{}'::jsonb""",
)


def _anomaly_tables() -> list[Any]:
    return [Base.metadata.tables[name] for name in ANOMALY_TABLE_NAMES]


async def _existing_columns(conn: AsyncConnection, table: str) -> set[str]:
    rows = (
        await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :table"
            ),
            {"table": table},
        )
    ).fetchall()
    return {str(row[0]).lower() for row in rows}


async def _table_exists(conn: AsyncConnection, table: str) -> bool:
    row = (
        await conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = :table"
            ),
            {"table": table},
        )
    ).first()
    return row is not None


async def ensure_anomaly_schema(conn: AsyncConnection) -> dict[str, Any]:
    """Garante tabelas, colunas e índices das anomalias.

    Retorna um resumo do que foi criado/alterado, útil para log de startup e
    para os testes de idempotência.
    """
    created_tables: list[str] = []
    added_columns: list[str] = []
    dialect = postgresql.dialect()

    for table in _anomaly_tables():
        existed = await _table_exists(conn, table.name)
        if not existed:
            await conn.execute(text(str(CreateTable(table).compile(dialect=dialect))))
            created_tables.append(table.name)
            continue

        present = await _existing_columns(conn, table.name)
        for column in table.columns:
            if column.name.lower() in present:
                continue
            ddl_type = column.type.compile(dialect=dialect)
            await conn.execute(
                text(f'ALTER TABLE "{table.name}" ADD COLUMN IF NOT EXISTS "{column.name}" {ddl_type}')
            )
            added_columns.append(f"{table.name}.{column.name}")

    # `create_all` não aplica DEFAULT em tabela já existente. Sem convergir os
    # defaults, um INSERT por SQL direto (o seed da configuração inicial) grava
    # NULL em coluna NOT NULL.
    for statement in ANOMALY_MIGRATION_STATEMENTS:
        await conn.execute(text(statement))

    for statement in ANOMALY_PARTIAL_INDEXES:
        await conn.execute(text(statement))

    return {"created_tables": created_tables, "added_columns": added_columns}


async def ensure_default_anomaly_config(conn: AsyncConnection) -> int | None:
    """Cria a configuração inicial **desabilitada**, vinculada ao forecast ativo.

    Só cria quando não existe nenhuma configuração. Nunca habilita nem altera
    uma configuração existente — habilitar é decisão explícita do usuário (E10).
    Retorna o id criado ou ``None`` quando já havia configuração.
    """
    existing = (await conn.execute(text("SELECT id FROM anomaly_config ORDER BY id LIMIT 1"))).first()
    if existing is not None:
        return None

    row = (
        await conn.execute(
            text(
                """
                SELECT fc.id,
                       fc.destination_table_id,
                       dt.physical_name,
                       fc.date_column,
                       fc.period_column,
                       fc.type_column,
                       fc.dimensions
                  FROM forecast_config fc
                  JOIN destination_table dt ON dt.id = fc.destination_table_id
                 WHERE fc.active IS TRUE
                 ORDER BY fc.id
                 LIMIT 1
                """
            )
        )
    ).first()
    if row is None:
        logger.info("[anomaly] nenhum forecast ativo: configuração inicial não criada")
        return None

    forecast_config_id, _dest_id, physical_name, date_column, period_column, type_column, dimensions = row
    import json

    dims = dimensions
    if isinstance(dims, str):
        try:
            dims = json.loads(dims)
        except (TypeError, ValueError):
            dims = []

    result = await conn.execute(
        text(
            """
            INSERT INTO anomaly_config (
                name, description, forecast_config_id, measure_column, currency,
                source_namespace, destination_physical_name, date_column,
                period_column, type_column, dimensions, enabled, version
            ) VALUES (
                :name, :description, :forecast_config_id, 'realizado_cloud', 'BRL',
                'silver', :physical_name, :date_column,
                :period_column, :type_column, CAST(:dimensions AS JSON), FALSE, 1
            ) RETURNING id
            """
        ),
        {
            "name": _DEFAULT_CONFIG_NAME,
            "description": (
                "Criada automaticamente desabilitada. Compara realizado_cloud observado "
                "contra o esperado antes do evento, sem depender da fatura."
            ),
            "forecast_config_id": forecast_config_id,
            "physical_name": physical_name,
            "date_column": date_column,
            "period_column": period_column,
            "type_column": type_column or "tipo",
            "dimensions": json.dumps(list(dims or [])),
        },
    )
    new_id = result.scalar_one()
    logger.info("[anomaly] configuração inicial criada (desabilitada): id=%s", new_id)
    return int(new_id)


async def migrate_anomalies(conn: AsyncConnection) -> dict[str, Any]:
    """Ponto único chamado pelo startup."""
    summary = await ensure_anomaly_schema(conn)
    created_id = await ensure_default_anomaly_config(conn)
    summary["default_config_id"] = created_id
    if summary["created_tables"] or summary["added_columns"] or created_id:
        logger.info(
            "[anomaly] migração: tabelas=%s colunas=%s config=%s",
            summary["created_tables"] or "-",
            summary["added_columns"] or "-",
            created_id or "-",
        )
    return summary


__all__: Iterable[str] = (
    "ANOMALY_TABLE_NAMES",
    "ANOMALY_MIGRATION_STATEMENTS",
    "ANOMALY_PARTIAL_INDEXES",
    "ensure_anomaly_schema",
    "ensure_default_anomaly_config",
    "migrate_anomalies",
)
