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
from pathlib import Path

import psycopg2
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gcp_logical_department import logical_department_name

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("gcp_classification_fix")

PG_URL = "postgresql://finops:finops@localhost:5433/finops"
API_URL = "http://localhost:8001"
DEST_FIELD = "SubAccountId"


def build_rule_insert(
    connection_id: int,
    destination_table_id: int,
    department_id: int,
    name: str,
    project_id: str,
    match_value: str,
    priority: int,
    *,
    catch_all: bool = False,
) -> tuple:
    if catch_all:
        conditions = [{
            "field": DEST_FIELD,
            "operator": "~",
            "values": [".*"],
            "logic": "AND",
        }]
        pattern = f"{DEST_FIELD}~.*"
    else:
        conditions = [{
            "field": DEST_FIELD,
            "operator": "=",
            "values": [project_id],
            "logic": "AND",
        }]
        pattern = f"{DEST_FIELD}={project_id}"
    return (
        connection_id,
        destination_table_id,
        "gcp",
        department_id,
        name,
        DEST_FIELD,
        pattern[:500],
        "multi_condition",
        match_value,
        "departamento",
        json.dumps(conditions),
        priority,
    )

def parse_project_record(raw_str: str) -> dict:
    if not raw_str:
        return {}
    try:
        data = json.loads(raw_str)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    m = re.search(r'"id":\s*"([^"]+)"', raw_str)
    if m:
        return {"id": m.group(1)}
    return {"id": raw_str.strip()}


def parse_labels(s: str | None) -> dict:
    result: dict[str, str] = {}
    for m in re.finditer(r"'key':\s*'([^']*)',\s*'value':\s*'([^']*)'", s or ""):
        result[m.group(1)] = m.group(2)
    return result


def looks_like_gcp_slug(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return True
    if n in ("landing-zone-gcp", "landing-zone"):
        return True
    lower = n.lower()
    if lower.startswith(("ti-", "vivo-ti-", "vivo-")):
        return True
    if "-" in n and n == lower:
        return True
    return False


def resolve_projeto(rec: dict, csv_projeto: str) -> str:
    if csv_projeto in ("DEPARTAMENTAIS", "Vicky"):
        return csv_projeto
    if csv_projeto and not looks_like_gcp_slug(csv_projeto):
        return csv_projeto
    return logical_department_name(
        rec.get("id") or rec.get("name") or "",
        rec.get("name") or "",
        parse_labels(rec.get("labels")),
    )

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
            rec = parse_project_record(raw_proj)
            proj_id = rec.get("id") or rec.get("name") or ""
            if not proj_id: continue
            if not diretoria:
                diretoria = "DIR INFRAESTRUTURA E CLOUD"
            ti_rules.append({
                "project_id": proj_id,
                "diretoria": diretoria,
                "projeto": resolve_projeto(rec, projeto),
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
            rec = parse_project_record(raw_proj)
            proj_id = rec.get("id") or rec.get("name") or ""
            if not proj_id: continue
            if not diretoria:
                diretoria = "NAO ENCONTRADO"
            fourp_rules.append({
                "project_id": proj_id,
                "diretoria": diretoria,
                "projeto": resolve_projeto(rec, projeto),
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

    cur.execute("SELECT id FROM destination_table WHERE upper(name) = 'MODELO FOCUS' LIMIT 1")
    focus_row = cur.fetchone()
    focus_table_id = focus_row[0] if focus_row else 1

    prio = 10
    for item in ti_rules:
        dir_id = get_or_create_dept(cur, item["diretoria"], gcp_showback_id)
        proj_dept_id = get_or_create_dept(cur, item["projeto"], dir_id, item["centro_custo"])

        rule_name = f"GCP TI | {item['project_id']} -> {item['projeto']}"
        params = build_rule_insert(
            conn_ttech, focus_table_id, proj_dept_id, rule_name,
            item["project_id"], item["projeto"], prio,
        )
        cur.execute(
            """
            INSERT INTO classification_rule (
                connection_id, destination_table_id, cloud_scope, department_id, name,
                source_field, pattern, pattern_type, match_value, target_field,
                conditions, priority, active, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, true, NOW(), NOW()
            )
            """,
            params,
        )
        prio += 1

    for item in fourp_rules:
        dir_id = get_or_create_dept(cur, item["diretoria"], gcp_showback_id)
        proj_dept_id = get_or_create_dept(cur, item["projeto"], dir_id, item["centro_custo"])

        for conn_id, code in gcp_connections:
            if "4p" in code or "ttech" in code:
                rule_name = f"GCP 4P | {item['project_id']} -> {item['projeto']}"
                params = build_rule_insert(
                    conn_id, focus_table_id, proj_dept_id, rule_name,
                    item["project_id"], item["projeto"], prio,
                )
                cur.execute(
                    """
                    INSERT INTO classification_rule (
                        connection_id, destination_table_id, cloud_scope, department_id, name,
                        source_field, pattern, pattern_type, match_value, target_field,
                        conditions, priority, active, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, true, NOW(), NOW()
                    )
                    """,
                    params,
                )
                prio += 1

    sem_proj_dir_id = get_or_create_dept(cur, "DIR INFRAESTRUTURA E CLOUD", gcp_showback_id)
    sem_proj_dept_id = get_or_create_dept(cur, "SEM PROJETO DEFINIDO", sem_proj_dir_id)
    fallback_params = build_rule_insert(
        conn_ttech, focus_table_id, sem_proj_dept_id,
        "GCP TI | Fallback Padrão", "", "SEM PROJETO DEFINIDO", 9999,
        catch_all=True,
    )
    cur.execute(
        """
        INSERT INTO classification_rule (
            connection_id, destination_table_id, cloud_scope, department_id, name,
            source_field, pattern, pattern_type, match_value, target_field,
            conditions, priority, active, created_at, updated_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, true, NOW(), NOW()
        )
        """,
        fallback_params,
    )

    cur.execute(
        """
        UPDATE classification_rule SET match_value = d.name
        FROM department d
        WHERE classification_rule.department_id = d.id
          AND classification_rule.connection_id = ANY(%s)
        """,
        ([c[0] for c in gcp_connections],),
    )

    cur.execute(
        """
        UPDATE department SET active = false, updated_at = NOW()
        WHERE parent_id = %s
          AND id NOT IN (
            SELECT DISTINCT department_id FROM classification_rule WHERE department_id IS NOT NULL
          )
        """,
        (sem_proj_dir_id,),
    )
    logger.info("Departamentos GCP sem regra desativados: %s", cur.rowcount)

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
