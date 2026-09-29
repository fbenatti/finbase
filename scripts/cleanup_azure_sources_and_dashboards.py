#!/usr/bin/env python3
"""Script de inventário, backup e limpeza das fontes auxiliares Azure e dados de Rightsizing/FinOps Center.

Executa:
1. Inventário pré-exclusão e backup em JSON de todos os registros afetados no PostgreSQL.
2. Remoção segura das seleções e metadados no PostgreSQL (azure_connection_source, azure_source_catalog,
   metric_daily_collection, rightsizing_review, gold_rule, gold_rule_source, gold_manifest, destination_table).
3. Exclusão das 12 tabelas Bronze no Trino / Iceberg.
4. Remoção de registros no catálogo iceberg_tables e limpeza de arquivos locais órfãos.
5. Verificação e validação pós-limpeza com logs detalhados.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

# Adiciona apps/api ao path para importar trino_service
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "apps" / "api"))
from app.services import trino_service

PG_DSN = os.getenv(
    "DATABASE_URL_SYNC",
    "postgresql://finops:finops@localhost:5433/finops"
)

OFFICIAL_SOURCES = ("cost_exports", "resource_graph", "automation_jobs", "openai_ptu")

BRONZE_TABLES_TO_DROP = [
    "raw_azure_4p_monitor_vm",
    "raw_azure_4p_aks_cost_allocation",
    "raw_azure_4p_aks_inventory",
    "raw_azure_4p_aks_namespace_allocation",
    "raw_azure_4p_compute_skus",
    "raw_azure_4p_container_insights",
    "raw_azure_4p_customer_price",
    "raw_azure_4p_policy",
    "raw_azure_4p_quota",
    "raw_azure_4p_retail_price",
    "raw_azure_4p_vm_inventory",
    "raw_azure_ti_vm_inventory",
]

LEGACY_DESTINATION_TABLE_IDS = [42, 43, 44, 45, 46, 47, 48, 49, 50]


def log(msg: str):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}", flush=True)


def json_serializer(obj: Any):
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    return str(obj)


def backup_and_inventory(pg_conn, backup_dir: Path):
    log("=== ETAPA 1: INVENTÁRIO E BACKUP ===")
    backup_dir.mkdir(parents=True, exist_ok=True)
    log(f"Diretório de backup: {backup_dir}")

    backup_data: dict[str, Any] = {}

    with pg_conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 1. azure_source_catalog
        cur.execute(
            "SELECT * FROM azure_source_catalog WHERE source_key NOT IN %s",
            (OFFICIAL_SOURCES,)
        )
        backup_data["azure_source_catalog"] = cur.fetchall()
        log(f"  Backup azure_source_catalog (removidos): {len(backup_data['azure_source_catalog'])} registros")

        # 2. azure_connection_source
        cur.execute(
            "SELECT * FROM azure_connection_source WHERE source_key NOT IN %s",
            (OFFICIAL_SOURCES,)
        )
        backup_data["azure_connection_source"] = cur.fetchall()
        log(f"  Backup azure_connection_source (removidos): {len(backup_data['azure_connection_source'])} registros")

        # 3. metric_daily_collection
        cur.execute("SELECT * FROM metric_daily_collection")
        backup_data["metric_daily_collection"] = cur.fetchall()
        log(f"  Backup metric_daily_collection: {len(backup_data['metric_daily_collection'])} registros")

        # 4. rightsizing_review
        cur.execute("SELECT * FROM rightsizing_review")
        backup_data["rightsizing_review"] = cur.fetchall()
        log(f"  Backup rightsizing_review: {len(backup_data['rightsizing_review'])} registros")

        # 5. gold_rule
        cur.execute("""
            SELECT * FROM gold_rule 
            WHERE code LIKE 'mvp_%' 
               OR code LIKE 'finops_%' 
               OR code = 'finops_rightsizing_actions'
               OR materialization_table LIKE '%mvp%' 
               OR materialization_table LIKE 'finops_%'
        """)
        backup_data["gold_rule"] = cur.fetchall()
        gold_rule_ids = [r["id"] for r in backup_data["gold_rule"]]
        log(f"  Backup gold_rule (regras a remover): {len(backup_data['gold_rule'])} registros (IDs: {gold_rule_ids})")

        # 6. gold_rule_source
        if gold_rule_ids:
            cur.execute(
                "SELECT * FROM gold_rule_source WHERE rule_id = ANY(%s)",
                (gold_rule_ids,)
            )
            backup_data["gold_rule_source"] = cur.fetchall()
        else:
            backup_data["gold_rule_source"] = []
        log(f"  Backup gold_rule_source: {len(backup_data['gold_rule_source'])} registros")

        # 7. gold_manifest
        cur.execute("""
            SELECT * FROM gold_manifest 
            WHERE cube_code LIKE 'mvp_%' 
               OR cube_code LIKE 'finops_%' 
               OR cube_code = 'finops_rightsizing_actions'
               OR physical_table LIKE 'dest_dm_cost_mvp_%'
               OR physical_table LIKE 'finops_%'
        """)
        backup_data["gold_manifest"] = cur.fetchall()
        log(f"  Backup gold_manifest: {len(backup_data['gold_manifest'])} registros")

        # 8. destination_table e destination_column
        cur.execute(
            "SELECT * FROM destination_table WHERE id = ANY(%s)",
            (LEGACY_DESTINATION_TABLE_IDS,)
        )
        backup_data["destination_table"] = cur.fetchall()
        cur.execute(
            "SELECT * FROM destination_column WHERE destination_table_id = ANY(%s)",
            (LEGACY_DESTINATION_TABLE_IDS,)
        )
        backup_data["destination_column"] = cur.fetchall()
        log(f"  Backup destination_table: {len(backup_data['destination_table'])} tabelas, {len(backup_data['destination_column'])} colunas")

        # 9. iceberg_tables das Bronze a excluir
        cur.execute(
            "SELECT * FROM iceberg_tables WHERE table_namespace = 'bronze' AND table_name = ANY(%s)",
            (BRONZE_TABLES_TO_DROP,)
        )
        backup_data["iceberg_tables"] = cur.fetchall()
        log(f"  Backup iceberg_tables (Bronze): {len(backup_data['iceberg_tables'])} registros")

    # Salva em JSON formatado
    backup_file = backup_dir / "backup_pre_cleanup.json"
    with open(backup_file, "w", encoding="utf-8") as f:
        json.dump(backup_data, f, indent=2, default=json_serializer, ensure_ascii=False)
    log(f"Backup gravado com sucesso em: {backup_file} ({backup_file.stat().st_size} bytes)")


def cleanup_postgres(pg_conn):
    log("=== ETAPA 2: LIMPEZA NO POSTGRESQL ===")
    with pg_conn.cursor() as cur:
        # 1. azure_connection_source
        cur.execute(
            "DELETE FROM azure_connection_source WHERE source_key NOT IN %s",
            (OFFICIAL_SOURCES,)
        )
        log(f"  [azure_connection_source] Excluídas {cur.rowcount} seleções das 11 fontes removidas.")

        # 2. azure_source_catalog
        cur.execute(
            "DELETE FROM azure_source_catalog WHERE source_key NOT IN %s",
            (OFFICIAL_SOURCES,)
        )
        log(f"  [azure_source_catalog] Excluídas {cur.rowcount} entradas de catálogo das fontes removidas.")

        # 3. metric_daily_collection
        cur.execute(
            "DELETE FROM metric_daily_collection WHERE source_key IN ('monitor_vm_metrics', 'container_insights')"
        )
        log(f"  [metric_daily_collection] Excluídos {cur.rowcount} registros de coletas diárias.")

        # 4. rightsizing_review
        cur.execute("DELETE FROM rightsizing_review")
        log(f"  [rightsizing_review] Excluídos {cur.rowcount} registros de revisão.")

        # 5. gold_rule_source e gold_rule
        cur.execute("""
            DELETE FROM gold_rule_source 
            WHERE rule_id IN (
                SELECT id FROM gold_rule 
                WHERE code LIKE 'mvp_%' 
                   OR code LIKE 'finops_%' 
                   OR code = 'finops_rightsizing_actions'
                   OR materialization_table LIKE '%mvp%' 
                   OR materialization_table LIKE 'finops_%'
            )
        """)
        log(f"  [gold_rule_source] Excluídos {cur.rowcount} vínculos de fontes de regras Gold.")

        cur.execute("""
            DELETE FROM gold_rule 
            WHERE code LIKE 'mvp_%' 
               OR code LIKE 'finops_%' 
               OR code = 'finops_rightsizing_actions'
               OR materialization_table LIKE '%mvp%' 
               OR materialization_table LIKE 'finops_%'
        """)
        log(f"  [gold_rule] Excluídas {cur.rowcount} regras Gold (mvp_*, finops_*).")

        # 6. gold_manifest
        cur.execute("""
            DELETE FROM gold_manifest 
            WHERE cube_code LIKE 'mvp_%' 
               OR cube_code LIKE 'finops_%' 
               OR cube_code = 'finops_rightsizing_actions'
               OR physical_table LIKE 'dest_dm_cost_mvp_%'
               OR physical_table LIKE 'finops_%'
        """)
        log(f"  [gold_manifest] Excluídos {cur.rowcount} manifestos de cubos Gold.")

        # 7. destination_column e destination_table
        cur.execute(
            "DELETE FROM destination_column WHERE destination_table_id = ANY(%s)",
            (LEGACY_DESTINATION_TABLE_IDS,)
        )
        log(f"  [destination_column] Excluídas {cur.rowcount} colunas legadas.")

        cur.execute(
            "DELETE FROM destination_table WHERE id = ANY(%s)",
            (LEGACY_DESTINATION_TABLE_IDS,)
        )
        log(f"  [destination_table] Excluídas {cur.rowcount} tabelas legadas.")

        # 8. iceberg_tables
        cur.execute(
            "DELETE FROM iceberg_tables WHERE table_namespace = 'bronze' AND table_name = ANY(%s)",
            (BRONZE_TABLES_TO_DROP,)
        )
        log(f"  [iceberg_tables] Excluídos {cur.rowcount} metadados de tabelas Bronze.")

    pg_conn.commit()
    log("Alterações no PostgreSQL confirmadas com sucesso (COMMIT).")


async def cleanup_trino():
    log("=== ETAPA 3: EXCLUSÃO DE TABELAS BRONZE NO TRINO ===")
    for table in BRONZE_TABLES_TO_DROP:
        try:
            sql = f'DROP TABLE IF EXISTS bronze."{table}"'
            await trino_service.execute(sql)
            log(f"  [Trino] Tabela bronze.{table} dropada com sucesso.")
        except Exception as e:
            log(f"  [Trino] Aviso ao dropar bronze.{table}: {e}")

    # Remove diretórios físicos locais em bronze.db se restarem arquivos órfãos
    iceberg_dir = ROOT_DIR / "apps" / "data" / "iceberg" / "bronze.db"
    if iceberg_dir.exists():
        for table in BRONZE_TABLES_TO_DROP:
            tbl_path = iceberg_dir / table
            if tbl_path.exists():
                try:
                    shutil.rmtree(tbl_path)
                    log(f"  [FS] Removido diretório local órfão: {tbl_path}")
                except Exception as e:
                    log(f"  [FS] Aviso ao remover diretório {tbl_path}: {e}")


def verify_cleanup(pg_conn):
    log("=== ETAPA 4: VERIFICAÇÃO PÓS-LIMPEZA ===")
    with pg_conn.cursor() as cur:
        # Fontes restantes no catálogo
        cur.execute("SELECT source_key, display_name FROM azure_source_catalog ORDER BY source_key")
        sources = cur.fetchall()
        log(f"  Fontes oficiais restantes em azure_source_catalog ({len(sources)}):")
        for s in sources:
            log(f"    - {s[0]}: {s[1]}")
        assert len(sources) == 4, f"Esperado 4 fontes, encontrado {len(sources)}"

        # Seleções de fontes restantes
        cur.execute("SELECT connection_id, source_key, enabled FROM azure_connection_source ORDER BY connection_id, source_key")
        selections = cur.fetchall()
        log(f"  Seleções restantes em azure_connection_source ({len(selections)}):")
        for sel in selections:
            log(f"    - Conn {sel[0]}: {sel[1]} (enabled={sel[2]})")
            assert sel[1] in OFFICIAL_SOURCES, f"Fonte não oficial encontrada: {sel[1]}"

        # metric_daily_collection
        cur.execute("SELECT count(*) FROM metric_daily_collection")
        cnt_mdc = cur.fetchone()[0]
        log(f"  metric_daily_collection count: {cnt_mdc}")
        assert cnt_mdc == 0, f"Esperado 0 registros em metric_daily_collection, encontrado {cnt_mdc}"

        # rightsizing_review
        cur.execute("SELECT count(*) FROM rightsizing_review")
        cnt_rr = cur.fetchone()[0]
        log(f"  rightsizing_review count: {cnt_rr}")
        assert cnt_rr == 0, f"Esperado 0 registros em rightsizing_review, encontrado {cnt_rr}"

        # Regras Gold restantes
        cur.execute("""
            SELECT id, code, name FROM gold_rule 
            WHERE code LIKE 'mvp_%' OR code LIKE 'finops_%' OR code = 'finops_rightsizing_actions'
        """)
        leftover_rules = cur.fetchall()
        log(f"  Regras mvp_/finops_ restantes em gold_rule: {len(leftover_rules)}")
        assert len(leftover_rules) == 0, f"Ainda existem regras não removidas: {leftover_rules}"

        # Regras legítimas restantes da aba Gold
        cur.execute("SELECT id, code, name FROM gold_rule ORDER BY id")
        active_rules = cur.fetchall()
        log(f"  Regras Gold legítimas preservadas ({len(active_rules)}):")
        for r in active_rules:
            log(f"    - ID {r[0]}: {r[1]} ({r[2]})")

    log("Verificação do PostgreSQL concluída com 100% de sucesso!")


async def verify_trino():
    log("=== ETAPA 5: VERIFICAÇÃO PÓS-LIMPEZA NO TRINO ===")
    tables = await trino_service.execute("SHOW TABLES IN bronze")
    bronze_names = {list(t.values())[0] for t in tables}
    
    for t in BRONZE_TABLES_TO_DROP:
        assert t not in bronze_names, f"Tabela {t} ainda existe no catálogo Trino bronze!"
    log("  Todas as 12 tabelas Bronze foram removidas do catálogo Trino.")

    # Verifica que as tabelas essenciais permanecem
    for essential in ["raw_azure_4p", "raw_azure_ti", "raw_azure_marketplace", "raw_azure_4p_automation", "raw_azure_4p_resource_graph", "raw_azure_4p_ptu"]:
        assert essential in bronze_names, f"Tabela essencial {essential} desapareceu!"
        log(f"  Tabela essencial confirmada: bronze.{essential}")

    log("Verificação do Trino concluída com 100% de sucesso!")


async def main():
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = ROOT_DIR / "backups" / f"cleanup_azure_{timestamp}"

    log(f"Iniciando limpeza das fontes auxiliares Azure e dashboards (sessão {timestamp})...")

    pg_conn = psycopg2.connect(PG_DSN)
    try:
        backup_and_inventory(pg_conn, backup_dir)
        cleanup_postgres(pg_conn)
        await cleanup_trino()
        verify_cleanup(pg_conn)
        await verify_trino()
        log("=== LIMPEZA CONCLUÍDA COM TOTAL SUCESSO! ===")
    finally:
        pg_conn.close()


if __name__ == "__main__":
    asyncio.run(main())
