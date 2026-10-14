"""Verificação da migração idempotente das anomalias (E1).

Roda contra o PostgreSQL de DEV (localhost:5433) e prova:

1. a primeira execução cria tabelas, colunas, defaults e a configuração inicial;
2. a segunda execução é um no-op (nenhuma tabela criada, nenhuma coluna
   adicionada, nenhuma configuração duplicada);
3. os índices parciais existem.

Uso:
    cd apps/api && ../../.venv/bin/python scripts/verify_anomaly_schema.py
"""
from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.anomaly_migrations import (  # noqa: E402
    ANOMALY_TABLE_NAMES,
    migrate_anomalies,
)

DSN = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://finops:finops@localhost:5433/finops"
)


async def main() -> int:
    engine = create_async_engine(DSN)
    failures: list[str] = []

    async with engine.begin() as conn:
        first = await migrate_anomalies(conn)
    async with engine.begin() as conn:
        second = await migrate_anomalies(conn)

    async with engine.connect() as conn:
        tables = {
            row[0]
            for row in (
                await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                        "AND tablename = ANY(:names)"
                    ),
                    {"names": list(ANOMALY_TABLE_NAMES)},
                )
            ).fetchall()
        }
        configs = (await conn.execute(text("SELECT count(*) FROM anomaly_config"))).scalar_one()
        indexes = {
            row[0]
            for row in (
                await conn.execute(
                    text("SELECT indexname FROM pg_indexes WHERE schemaname='public'")
                )
            ).fetchall()
        }
        defaults = dict(
            (
                await conn.execute(
                    text(
                        "SELECT column_name, column_default FROM information_schema.columns "
                        "WHERE table_name='anomaly_config' AND column_name IN "
                        "('measure_column','currency','profile','enabled','materiality_by_currency')"
                    )
                )
            ).fetchall()
        )
        not_null_without_default = (
            await conn.execute(
                text(
                    "SELECT table_name || '.' || column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name = ANY(:names) "
                    "AND is_nullable='NO' AND column_default IS NULL "
                    "AND is_identity='NO' AND table_name <> 'anomaly_case_event'"
                ),
                {"names": list(ANOMALY_TABLE_NAMES)},
            )
        ).fetchall()

    print(f"1ª execução: tabelas criadas={first['created_tables']} colunas={len(first['added_columns'])} config={first['default_config_id']}")
    print(f"2ª execução: tabelas criadas={second['created_tables']} colunas={len(second['added_columns'])} config={second['default_config_id']}")

    if first["created_tables"]:
        failures.append("a primeira execução não deveria criar tabelas (já existiam)")
    if second["created_tables"] or second["added_columns"] or second["default_config_id"]:
        failures.append(f"a segunda execução não foi no-op: {second}")
    if set(ANOMALY_TABLE_NAMES) - tables:
        failures.append(f"tabelas ausentes: {sorted(set(ANOMALY_TABLE_NAMES) - tables)}")
    if configs != 1:
        failures.append(f"esperava exatamente 1 configuração inicial, encontrei {configs}")
    if "uq_anomaly_publication_current" not in indexes:
        failures.append("índice parcial uq_anomaly_publication_current ausente")
    for column in ("measure_column", "currency", "profile", "enabled", "materiality_by_currency"):
        if column not in defaults or defaults[column] is None:
            failures.append(f"default ausente em anomaly_config.{column}")
    print("defaults:", defaults)
    if not_null_without_default:
        print("NOT NULL sem default (inseridos apenas via ORM):")
        for row in not_null_without_default:
            print("   ", row[0])

    # --- gatilho de imutabilidade do histórico --------------------------
    async with engine.begin() as conn:
        triggers = [
            row[0]
            for row in (
                await conn.execute(
                    text(
                        "SELECT tgname FROM pg_trigger "
                        "WHERE tgrelid = 'anomaly_case_event'::regclass AND NOT tgisinternal"
                    )
                )
            ).fetchall()
        ]
    print(f"gatilhos em anomaly_case_event: {triggers}")
    if "trg_anomaly_case_event_immutable" not in triggers:
        failures.append("gatilho de imutabilidade ausente em anomaly_case_event")

    for operation, statement in (
        ("UPDATE", "UPDATE anomaly_case_event SET note = 'alterado'"),
        ("DELETE", "DELETE FROM anomaly_case_event"),
    ):
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO anomaly_case (case_key, series_id, series_dimensions, "
                        "event_family, event_type, event_date, period) VALUES "
                        "('probe-imutavel', 's', '{}'::json, 'f', 'DAILY_CONSUMPTION', "
                        "'2026-10-10', '202610')"
                    )
                )
                await conn.execute(
                    text(
                        "INSERT INTO anomaly_case_event (case_id, event_type) "
                        "SELECT id, 'CREATED' FROM anomaly_case WHERE case_key = 'probe-imutavel'"
                    )
                )
                await conn.execute(text(statement))
            failures.append(f"{operation} em anomaly_case_event NÃO foi bloqueado pelo gatilho")
        except Exception as exc:  # esperado
            blocked = "imutável" in str(exc)
            print(f"   {operation}: bloqueado ({'mensagem correta' if blocked else exc})")
            if not blocked:
                failures.append(f"{operation} falhou por outro motivo: {exc}")
        finally:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "DELETE FROM anomaly_case WHERE case_key = 'probe-imutavel'"
                    )
                )

    await engine.dispose()
    if failures:
        print("\nFALHAS:")
        for failure in failures:
            print(" -", failure)
        return 1
    print("\nOK: migração idempotente verificada em PostgreSQL real.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
