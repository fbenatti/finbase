"""Reset functional forecast and planning data while preserving shared metadata."""

import argparse
import os
from typing import Dict, Iterable

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection


TABLES = (
    "forecast_ack_history",
    "forecast_ack",
    "forecast_run",
    "forecast_adjustment",
    "forecast_business_rule",
    "forecast_config",
    "planning_entry",
    "planning_version",
    "budget_defense_request",
)

PRESERVED_TABLES = (
    "department",
    "financial_type",
    "destination_table",
    "audit_log",
)


class ResetError(RuntimeError):
    """Raised when the reset cannot safely be completed."""


def database_url() -> str:
    return os.getenv(
        "DATABASE_URL_SYNC",
        "postgresql://finops:finops@localhost:5432/finops",
    )


def count_rows(connection: Connection, tables: Iterable[str]) -> Dict[str, int]:
    return {
        table: int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())
        for table in tables
    }


def financial_type_counts(connection: Connection) -> Dict[str, int]:
    rows = connection.execute(
        text(
            """
            SELECT ft.code, COUNT(pv.id)
            FROM financial_type AS ft
            LEFT JOIN planning_version AS pv ON pv.financial_type_id = ft.id
            GROUP BY ft.code
            ORDER BY ft.code
            """
        )
    )
    return {str(code): int(count) for code, count in rows}


def validate_schema(connection: Connection) -> None:
    missing = []
    for table in (*TABLES, *PRESERVED_TABLES):
        exists = connection.execute(
            text("SELECT to_regclass(:table_name) IS NOT NULL"),
            {"table_name": f"public.{table}"},
        ).scalar_one()
        if not exists:
            missing.append(table)
    if missing:
        raise ResetError(f"Tabelas esperadas ausentes: {', '.join(missing)}")


def validate_empty(connection: Connection) -> None:
    remaining = count_rows(connection, TABLES)
    non_empty = {table: count for table, count in remaining.items() if count}
    if non_empty:
        raise ResetError(f"Dados funcionais restantes: {non_empty}")

    orphan_planning = connection.execute(
        text(
            """
            SELECT COUNT(*)
            FROM planning_version AS pv
            LEFT JOIN budget_defense_request AS br ON br.id = pv.defense_request_id
            WHERE pv.defense_request_id IS NOT NULL AND br.id IS NULL
            """
        )
    ).scalar_one()
    if orphan_planning:
        raise ResetError(f"Versões com defesa órfã: {orphan_planning}")


def delete_functional_data(connection: Connection) -> Dict[str, int]:
    deleted = {}  # type: Dict[str, int]
    for table in TABLES:
        result = connection.execute(text(f"DELETE FROM {table}"))
        deleted[table] = int(result.rowcount or 0)
    return deleted


def print_inventory(title: str, counts: dict[str, int]) -> None:
    print(title)
    for table, count in counts.items():
        print(f"  {table}: {count}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Limpa dados funcionais de forecast e planejamento, preservando a estrutura."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Mostra contagens sem apagar dados")
    mode.add_argument("--confirm", action="store_true", help="Executa a limpeza transacional")
    args = parser.parse_args()

    engine = create_engine(database_url(), pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            validate_schema(connection)
            before = count_rows(connection, TABLES)
            print_inventory("Antes:", before)
            print("Por tipo financeiro:")
            for code, count in financial_type_counts(connection).items():
                print(f"  {code}: {count}")

            if args.dry_run:
                print("\nDRY-RUN: nenhum dado foi alterado.")
                return 0

            deleted = delete_functional_data(connection)
            validate_empty(connection)
            preserved = count_rows(connection, PRESERVED_TABLES)
            print_inventory("Removidos:", deleted)
            print_inventory("Estrutura preservada:", preserved)
            print("Reset concluído e validado dentro da transação.")
    except Exception as exc:
        print(f"ERRO: reset não executado; transação revertida: {exc}")
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
