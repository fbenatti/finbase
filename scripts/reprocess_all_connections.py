#!/usr/bin/env python3
"""
Dispara o reprocessamento completo da tabela Gold MODELO FOCUS (ID 1)
para todas as conexões de nuvem ativas com dados:
- 1: azure-4p
- 2: azure-ti
- 3: gcp-4p
- 4: gcp-ti
- 7: ibm-ti
- 8: gcp-ttech
"""

import logging
import sys
import time
import httpx
import psycopg2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("reprocess_all_clouds")

API_URL = "http://localhost:8001"
PG_URL = "postgresql://finops:finops@localhost:5433/finops"

def main():
    logger.info("=== 1. BUSCANDO CONEXÕES COM DADOS INGERIDOS ===")
    conn = psycopg2.connect(PG_URL)
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT sc.id, sc.code, sc.name
        FROM source_connection sc
        JOIN ingested_blob ib ON ib.connection_id = sc.id
        ORDER BY sc.id;
    """)
    rows = cur.fetchall()
    conn_ids = [r[0] for r in rows]
    logger.info("Conexões selecionadas para reprocessamento consolidado Gold: %s", [(r[0], r[1]) for r in rows])

    logger.info("=== 2. ENVIANDO REQUISIÇÃO DE REPROCESSAMENTO COMPLETO ===")
    with httpx.Client(timeout=300) as client:
        resp = client.post(f"{API_URL}/etl/reprocess", json={
            "connection_ids": conn_ids,
            "destination_table_id": 1
        })
        logger.info("Reprocessamento response status: %d", resp.status_code)
        data = resp.json()
        logger.info("Response: %s", data)
        task_ids = data.get("task_ids", [data.get("task_id")])
        logger.info("Total de %d tasks enfileiradas", len(task_ids))

        logger.info("=== 3. MONITORANDO FILA DE REPROCESSAMENTO ===")
        while True:
            cur.execute("""
                SELECT status, COUNT(*)
                FROM etl_task
                WHERE id = ANY(%s)
                GROUP BY status;
            """, (task_ids,))
            counts = dict(cur.fetchall())
            logger.info("Progresso das tasks: %s", counts)

            if "QUEUED" not in counts and "RUNNING" not in counts:
                logger.info("Todas as tasks de reprocessamento terminaram!")
                break
            time.sleep(5)

    logger.info("=== 4. CONCLUÍDO COM SUCESSO! ===")

if __name__ == "__main__":
    main()
