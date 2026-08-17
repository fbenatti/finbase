import json
import logging
import sys
import psycopg2
from google.cloud import bigquery
from google.oauth2 import service_account

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("bigquery_test")

def main():
    logger.info("=== Iniciando teste de query BigQuery no OpenFinops ===")
    
    # 1. Obter credencial da conexão do banco de dados
    try:
        conn = psycopg2.connect("postgresql://finops:finops@localhost:5433/finops")
        cur = conn.cursor()
        cur.execute("SELECT id, code, name, config_json FROM source_connection WHERE code = 'gcp-ti'")
        row = cur.fetchone()
        if not row:
            logger.error("Conexão 'gcp-ti' não encontrada no banco de dados!")
            return
        
        conn_id, code, name, config_json = row
        cfg = config_json if isinstance(config_json, dict) else json.loads(config_json or "{}")
        token = cfg.get("token_json")
        if not token:
            logger.error(f"Conexão {code} não possui token_json configurado.")
            return
            
        info = json.loads(token) if isinstance(token, str) else token
        sa_email = info.get("client_email")
        project_id = cfg.get("project_id", "vivo-ti-billing")
        logger.info(f"Conexão encontrada: ID={conn_id}, code={code}, name={name}")
        logger.info(f"Service Account: {sa_email}")
        logger.info(f"Projeto configurado: {project_id}")
        
    except Exception as e:
        logger.exception(f"Erro ao obter configurações do PostgreSQL: {e}")
        return

    # 2. Configurar cliente BigQuery
    try:
        credentials = service_account.Credentials.from_service_account_info(info)
        client = bigquery.Client(project=project_id, credentials=credentials)
        logger.info("Cliente BigQuery inicializado com sucesso.")
    except Exception as e:
        logger.exception(f"Erro ao inicializar cliente BigQuery: {e}")
        return

    # 3. Executar a query exata solicitada pelo usuário
    query = """
    SELECT * 
    FROM `vivo-ti-billing.ttechbilling.gcp_billing_export_resource_v1_015AC4_5FC0B7_B1B758` 
    WHERE TIMESTAMP_TRUNC(_PARTITIONTIME, DAY) = TIMESTAMP("2026-08-17") 
    LIMIT 1000
    """
    logger.info("--- Executando a query solicitada ---")
    logger.info(query.strip())
    
    try:
        job = client.query(query)
        logger.info(f"Job ID: {job.job_id}. Aguardando resultado...")
        results = list(job.result(timeout=120))
        logger.info(f"Sucesso! Total de linhas retornadas: {len(results)}")
        
        if len(results) > 0:
            first_row = dict(results[0].items())
            logger.info(f"Total de colunas retornadas: {len(first_row)}")
            logger.info("Nomes das colunas:")
            for col in sorted(first_row.keys()):
                logger.info(f"  - {col}")
            
            logger.info("--- Exemplo do primeiro registro retornado ---")
            for k, v in list(first_row.items())[:15]:
                logger.info(f"  {k}: {v}")
        else:
            logger.info("A query executou sem erros, mas não encontrou registros para a data '2026-08-17'.")
            
            # Investigar partições disponíveis na tabela
            logger.info("--- Verificando partições recentes disponíveis na tabela ---")
            check_partitions_query = """
            SELECT 
                TIMESTAMP_TRUNC(_PARTITIONTIME, DAY) as partition_day,
                COUNT(1) as total_records,
                SUM(cost) as total_cost
            FROM `vivo-ti-billing.ttechbilling.gcp_billing_export_resource_v1_015AC4_5FC0B7_B1B758`
            WHERE _PARTITIONTIME IS NOT NULL
            GROUP BY 1
            ORDER BY 1 DESC
            LIMIT 10
            """
            try:
                p_job = client.query(check_partitions_query)
                p_results = list(p_job.result(timeout=60))
                logger.info(f"Últimas {len(p_results)} partições encontradas:")
                for r in p_results:
                    logger.info(f"  Partição: {r['partition_day']} | Registros: {r['total_records']} | Custo: {r['total_cost']}")
            except Exception as pe:
                logger.warning(f"Não foi possível listar partições: {pe}")

    except Exception as e:
        logger.error(f"Erro ao executar a query: {e}")
        
        # Testar se a tabela/dataset existe ou se tem outra tabela parecida
        logger.info("--- Verificando datasets e tabelas acessíveis no projeto vivo-ti-billing ---")
        try:
            datasets = list(client.list_datasets())
            logger.info(f"Datasets encontrados ({len(datasets)}): {[d.dataset_id for d in datasets]}")
            for d in datasets:
                tables = list(client.list_tables(d.dataset_id))
                logger.info(f"Dataset '{d.dataset_id}' possui {len(tables)} tabelas: {[t.table_id for t in tables]}")
        except Exception as de:
            logger.error(f"Erro ao inspecionar datasets/tabelas: {de}")

if __name__ == "__main__":
    main()
