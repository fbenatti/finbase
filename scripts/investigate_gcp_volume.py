import json
import logging
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

api_env = Path("apps/api/.env")
if api_env.exists():
    load_dotenv(api_env, override=True)
else:
    load_dotenv(".env", override=True)

import psycopg2
from google.cloud import bigquery
from google.oauth2 import service_account

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('bq_investigate')

def main():
    conn = psycopg2.connect("postgresql://finops:finops@localhost:5433/finops")
    cur = conn.cursor()
    cur.execute("SELECT code, name, config_json FROM source_connection WHERE code in ('gcp-ti', 'gcp-ttech') ORDER BY code")
    rows = cur.fetchall()

    for code, name, config in rows:
        cfg = config if isinstance(config, dict) else json.loads(config or '{}')
        info = json.loads(cfg['token_json']) if isinstance(cfg['token_json'], str) else cfg['token_json']
        credentials = service_account.Credentials.from_service_account_info(info)
        client = bigquery.Client(project=cfg['project_id'], credentials=credentials)
        
        table_name = cfg.get('table_name')
        dataset_id = cfg.get('dataset_id')
        fqn = f"{cfg['project_id']}.{dataset_id}.{table_name}"
        logger.info(f"\n=======================================================")
        logger.info(f"Análise de volumetria para: {code} ({name})")
        logger.info(f"Tabela FQN: {fqn}")
        logger.info(f"=======================================================")
        
        # 1. Total de linhas brutas por invoice.month
        q_raw = f"""
        SELECT 
            src.invoice.month as invoice_month,
            COUNT(1) as raw_rows,
            SUM(src.cost) as total_cost
        FROM `{fqn}` AS src
        WHERE src.invoice.month IS NOT NULL
        GROUP BY 1
        ORDER BY 1 DESC
        LIMIT 10
        """
        try:
            res_raw = list(client.query(q_raw).result())
            logger.info("--- LINHAS BRUTAS (Raw no BigQuery) ---")
            for r in res_raw:
                logger.info(f"  Mês {r['invoice_month']}: {r['raw_rows']:,} linhas brutas | Custo: {r['total_cost']}")
        except Exception as e:
            logger.error(f"Erro ao contar linhas brutas em {fqn}: {e}")

        # 2. Total de linhas que o extractor gera com o GROUP BY
        # Usando a mesma query agregada do gcp_bigquery_extractor.py
        from app.services.gcp_bigquery_extractor import _gcp_aggregated_select_sql
        
        # Obter colunas
        table = client.get_table(fqn)
        available_fields = {field.name for field in table.schema}
        
        q_agg_count = f"""
        WITH aggregated AS (
            {_gcp_aggregated_select_sql(fqn, invoice_month_filter=False, available_fields=available_fields)}
        )
        SELECT 
            invoice.month as invoice_month,
            COUNT(1) as agg_rows,
            SUM(cost) as agg_cost
        FROM aggregated
        WHERE invoice.month IS NOT NULL
        GROUP BY 1
        ORDER BY 1 DESC
        LIMIT 10
        """
        try:
            res_agg = list(client.query(q_agg_count).result())
            logger.info("--- LINHAS AGREGADAS (Pelo Extrator do OpenFinops com GROUP BY) ---")
            for r in res_agg:
                logger.info(f"  Mês {r['invoice_month']}: {r['agg_rows']:,} linhas agregadas | Custo: {r['agg_cost']}")
        except Exception as e:
            logger.error(f"Erro ao contar linhas agregadas em {fqn}: {e}")

if __name__ == "__main__":
    sys.path.insert(0, os.path.abspath("apps/api"))
    main()
