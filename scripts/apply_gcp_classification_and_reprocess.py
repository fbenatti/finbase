#!/usr/bin/env python3
"""
Aplica a correção do organograma e regras de classificação para GCP (TI e 4P),
baseando-se nos arquivos Classificacao-TI-GCP.csv e Classificacao-4P-GCP.csv.
Em seguida, dispara o reprocessamento da camada Gold no Iceberg.
"""

import csv
import json
import logging
import re
import sys
import psycopg2
import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("gcp_classification_fix")

PG_URL = "postgresql://finops:finops@localhost:5433/finops"
API_URL = "http://localhost:8001"

def parse_project_id_from_raw(raw_str: str) -> str:
    """Extrai o project ID a partir do JSON bruto de project."""
    if not raw_str:
        return ""
    try:
        data = json.loads(raw_str)
        if isinstance(data, dict):
            return data.get("id") or data.get("name") or ""
    except Exception:
        pass
    m = re.search(r'"id":\s*"([^"]+)"', raw_str)
    if m:
        return m.group(1)
    return raw_str.strip()

def get_or_create_dept(cur, name: str, parent_id: int | None, centro_custo: str | None = None) -> int:
    cur.execute(
        "SELECT id FROM department WHERE name = %s AND parent_id IS NOT DISTINCT FROM %s LIMIT 1",
        (name, parent_id)
    )
    row = cur.fetchone()
    if row:
        dept_id = row[0]
        if centro_custo:
            cur.execute("UPDATE department SET centro_custo = %s WHERE id = %s AND (centro_custo IS NULL OR centro_custo = '')", (centro_custo, dept_id))
        return dept_id

    cur.execute(
        """
        INSERT INTO department (name, parent_id, centro_custo, active, created_at, updated_at)
        VALUES (%s, %s, %s, true, NOW(), NOW())
        RETURNING id
        """,
        (name, parent_id, centro_custo)
    )
    dept_id = cur.fetchone()[0]
    logger.info("Criado departamento: %s (ID %d) sob pai ID %s", name, dept_id, parent_id)
    return dept_id

def main():
    logger.info("=== 1. CONECTANDO AO POSTGRESQL ===")
    conn = psycopg2.connect(PG_URL)
    conn.autocommit = False
    cur = conn.cursor()

    cur.execute("SELECT id FROM department WHERE name = 'Consumo Cloud' AND parent_id IS NULL LIMIT 1")
    consumo_cloud_id = cur.fetchone()[0]

    showback_id = get_or_create_dept(cur, "Showback", consumo_cloud_id)
    gcp_showback_id = get_or_create_dept(cur, "GCP", showback_id)
    logger.info("Nó raiz GCP Showback ID: %d", gcp_showback_id)

    ti_csv = "Classificacao-TI-GCP.csv"
    logger.info("=== 2. PROCESSANDO %s ===", ti_csv)
    ti_rules = []
    with open(ti_csv, encoding="utf-8") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)
        for row in reader:
            if not row or not any(row): continue
            raw_proj, diretoria, projeto, cc = row[0], row[1].strip(), row[2].strip(), row[3].strip() if len(row) > 3 else ""
            proj_id = parse_project_id_from_raw(raw_proj)
            if not proj_id: continue
            if not diretoria:
                diretoria = "DIR INFRAESTRUTURA E CLOUD"
            ti_rules.append({
                "project_id": proj_id,
                "diretoria": diretoria,
                "projeto": projeto or proj_id,
                "centro_custo": cc
            })

    logger.info("Lidas %d regras de classificação de TI GCP", len(ti_rules))

    fourp_csv = "Classificacao-4P-GCP.csv"
    logger.info("=== 3. PROCESSANDO %s ===", fourp_csv)
    fourp_rules = []
    with open(fourp_csv, encoding="utf-8") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)
        for row in reader:
            if not row or not any(row): continue
            raw_proj, diretoria, projeto, cc = row[0], row[1].strip(), row[2].strip(), row[3].strip() if len(row) > 3 else ""
            proj_id = parse_project_id_from_raw(raw_proj)
            if not proj_id: continue
            if not diretoria:
                diretoria = "NAO ENCONTRADO"
            fourp_rules.append({
                "project_id": proj_id,
                "diretoria": diretoria,
                "projeto": projeto or proj_id,
                "centro_custo": cc
            })
    logger.info("Lidas %d regras de classificação de 4P GCP", len(fourp_rules))

    cur.execute("SELECT id, code FROM source_connection WHERE code ILIKE '%gcp%'")
    gcp_connections = cur.fetchall()
    logger.info("Conexões GCP encontradas: %s", gcp_connections)

    conn_ttech = next((c[0] for c in gcp_connections if "ttech" in c[1]), 8)

    for conn_id, code in gcp_connections:
        cur.execute("DELETE FROM classification_rule WHERE connection_id = %s", (conn_id,))
        logger.info("Regras antigas limpas para conexão %s (ID %d)", code, conn_id)

    prio = 10
    for item in ti_rules:
        dir_id = get_or_create_dept(cur, item["diretoria"], gcp_showback_id)
        proj_dept_id = get_or_create_dept(cur, item["projeto"], dir_id, item["centro_custo"])

        rule_name = f"GCP TI | {item['project_id']} -> {item['projeto']}"
        pattern = f"[\"{item['project_id']}\"]"
        
        cur.execute(
            """
            INSERT INTO classification_rule (
                connection_id, destination_table_id, cloud_scope, department_id, name, source_field, pattern, pattern_type,
                match_value, target_field, priority, active, created_at, updated_at
            ) VALUES (
                %s, 1, 'gcp', %s, %s, 'resourceGroupName', %s, 'exact',
                %s, 'departamento', %s, true, NOW(), NOW()
            )
            """,
            (conn_ttech, proj_dept_id, rule_name, pattern, item["projeto"], prio)
        )
        prio += 1

    for item in fourp_rules:
        dir_id = get_or_create_dept(cur, item["diretoria"], gcp_showback_id)
        proj_dept_id = get_or_create_dept(cur, item["projeto"], dir_id, item["centro_custo"])

        for conn_id, code in gcp_connections:
            if "4p" in code or "ttech" in code:
                rule_name = f"GCP 4P | {item['project_id']} -> {item['projeto']}"
                pattern = f"[\"{item['project_id']}\"]"
                cur.execute(
                    """
                    INSERT INTO classification_rule (
                        connection_id, destination_table_id, cloud_scope, department_id, name, source_field, pattern, pattern_type,
                        match_value, target_field, priority, active, created_at, updated_at
                    ) VALUES (
                        %s, 1, 'gcp', %s, %s, 'resourceGroupName', %s, 'exact',
                        %s, 'departamento', %s, true, NOW(), NOW()
                    )
                    """,
                    (conn_id, proj_dept_id, rule_name, pattern, item["projeto"], prio)
                )
                prio += 1

    sem_proj_dir_id = get_or_create_dept(cur, "DIR INFRAESTRUTURA E CLOUD", gcp_showback_id)
    sem_proj_dept_id = get_or_create_dept(cur, "SEM PROJETO DEFINIDO", sem_proj_dir_id)
    cur.execute(
        """
        INSERT INTO classification_rule (
            connection_id, destination_table_id, cloud_scope, department_id, name, source_field, pattern, pattern_type,
            match_value, target_field, priority, active, created_at, updated_at
        ) VALUES (
            %s, 1, 'gcp', %s, 'GCP TI | Fallback Padrão', 'resourceGroupName', '.*', 'regex',
            'SEM PROJETO DEFINIDO', 'departamento', 9999, true, NOW(), NOW()
        )
        """,
        (conn_ttech, sem_proj_dept_id)
    )

    conn.commit()
    logger.info("=== 4. ORGANOGRAMA E REGRAS SALVOS COM SUCESSO! ===")

    logger.info("=== 5. DISPARANDO REPROCESSAMENTO GOLD PARA GCP-TTECH ===")
    with httpx.Client(timeout=300) as client:
        r = client.post(f"{API_URL}/etl/reprocess", json={
            "connection_ids": [conn_ttech],
            "destination_table_id": 1
        })
        logger.info("Reprocessamento response: %s (Status: %d)", r.text, r.status_code)
        task_data = r.json()
        task_id = task_data.get("task_id")

        if task_id:
            import time
            logger.info("Aguardando conclusão da task %s...", task_id)
            for _ in range(60):
                st = client.get(f"{API_URL}/etl/tasks/{task_id}").json()
                status = st.get("status")
                logger.info("Status da task %s: %s (processadas: %s/%s, destino: %s)", task_id, status, st.get("processed_blobs"), st.get("total_blobs"), st.get("rows_destination"))
                if status in ("COMPLETED", "FAILED", "COMPLETED_WITH_ERRORS"):
                    break
                time.sleep(3)

    logger.info("=== PROCESSO FINALIZADO ===")

if __name__ == "__main__":
    main()
