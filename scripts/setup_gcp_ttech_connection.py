import json
import logging
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

# Carregar variáveis do apps/api/.env
api_env = Path("apps/api/.env")
if api_env.exists():
    load_dotenv(api_env, override=True)
else:
    load_dotenv(".env", override=True)

# Garantir sys.path
sys.path.insert(0, os.path.abspath("apps/api"))

from app.database import SyncSession
from app.models.source_connection import SourceConnection
from app.services.gcp_bigquery_extractor import GcpBigQueryExtractor
from sqlalchemy import text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("setup_gcp_connection")

def main():
    logger.info("=== Iniciando configuracao da conexao GCP TTech Billing Export ===")
    
    db = SyncSession()
    try:
        # 1. Obter o token_json já existente da conexão gcp-ti
        ti_conn = db.query(SourceConnection).filter(SourceConnection.code == "gcp-ti").first()
        if not ti_conn or not ti_conn.config_json or "token_json" not in ti_conn.config_json:
            logger.error("Conexão 'gcp-ti' não encontrada ou sem token_json!")
            return
        
        token_json = ti_conn.config_json["token_json"]
        logger.info("Token de autenticação obtido com sucesso da conexão gcp-ti.")

        # 2. Configurar os dados da nova conexão
        new_code = "gcp-ttech"
        new_name = "GCP TTech Billing Export"
        new_config = {
            "project_id": "vivo-ti-billing",
            "dataset_id": "ttechbilling",
            "table_name": "gcp_billing_export_resource_v1_015AC4_5FC0B7_B1B758",
            "token_json": token_json
        }
        
        existing = db.query(SourceConnection).filter(SourceConnection.code == new_code).first()
        if existing:
            logger.info(f"Conexão '{new_code}' já existe (ID={existing.id}). Atualizando dados...")
            existing.name = new_name
            existing.connector_type = "gcp_bigquery"
            existing.cloud_name = "GCP-TTech"
            existing.cloud_scope = "gcp"
            existing.config_json = new_config
            existing.blob_prefixes = []
            conn_record = existing
        else:
            logger.info(f"Criando nova conexão '{new_code}'...")
            conn_record = SourceConnection(
                code=new_code,
                name=new_name,
                connector_type="gcp_bigquery",
                cloud_name="GCP-TTech",
                cloud_scope="gcp",
                config_json=new_config,
                blob_prefixes=[]
            )
            db.add(conn_record)
        
        db.commit()
        db.refresh(conn_record)
        logger.info(f"Conexão persistida no banco com sucesso! ID={conn_record.id}, code={conn_record.code}, name={conn_record.name}")

        # 3. Testar a conexão usando o GcpBigQueryExtractor nativo do OpenFinops
        logger.info("--- Executando teste nativo de conexão via GcpBigQueryExtractor ---")
        extractor = GcpBigQueryExtractor(conn_record)
        test_result = extractor.test_connection()
        logger.info(f"Resultado do test_connection(): {test_result}")
        
        if test_result.get("status") != "OK":
            logger.error(f"Falha no teste nativo de conexão: {test_result}")
            return
        
        # 4. Validar scan da tabela e metadados
        logger.info("--- Testando scan da tabela e metadados ---")
        client = extractor._get_client()
        table_fqn = f"{extractor.project_id}.{extractor.dataset_id}.{conn_record.config_json['table_name']}"
        table = client.get_table(table_fqn)
        logger.info(f"Tabela encontrada: {table_fqn}")
        logger.info(f"Total de campos no schema: {len(table.schema)}")
        logger.info(f"Total de linhas estimadas na tabela: {table.num_rows}")
        logger.info(f"Tamanho total em bytes: {table.num_bytes}")

        # 5. Executar seed de mapeamento FOCUS para a nova conexão
        logger.info("--- Verificando mapeamentos FOCUS para a nova conexão ---")
        from seed_gcp_focus_mappings import MAPPINGS

        dest_table_id = db.execute(text("SELECT id FROM destination_table WHERE upper(name) = 'MODELO FOCUS' LIMIT 1")).scalar()
        if dest_table_id:
            logger.info(f"Destination Table 'MODELO FOCUS' encontrada (ID={dest_table_id}). Cadastrando source_destination_mapping...")
            inserted = 0
            for priority, mapping in enumerate(MAPPINGS, start=1):
                cfg = dict(mapping.get("transform_config") or {})
                if cfg.get("value") == "__CLOUD_NAME__":
                    cfg["value"] = "GCP-TTech"
                
                db.execute(text("""
                    INSERT INTO source_destination_mapping (
                        source_connection_id, destination_table_id, name,
                        source_column, destination_column, transform_type, transform_config,
                        priority, active
                    )
                    SELECT :cid, :did, :name, :src, :dest, :ttype, CAST(:cfg AS jsonb), :prio, true
                    WHERE NOT EXISTS (
                        SELECT 1 FROM source_destination_mapping
                        WHERE source_connection_id = :cid
                          AND destination_table_id = :did
                          AND destination_column = :dest
                    )
                """), {
                    "cid": conn_record.id,
                    "did": dest_table_id,
                    "name": f"GCP GCP-TTech: {mapping['source_column']} -> {mapping['destination_column']}",
                    "src": mapping["source_column"],
                    "dest": mapping["destination_column"],
                    "ttype": mapping["transform_type"],
                    "cfg": json.dumps(cfg),
                    "prio": priority,
                })
                inserted += 1
            db.commit()
            logger.info(f"{inserted} mapeamentos FOCUS verificados/cadastrados para a conexão ID={conn_record.id}.")
        else:
            logger.warning("Destination Table 'MODELO FOCUS' não encontrada.")

        logger.info("=== Configuração e validação concluídas com 100% de sucesso! ===")

    except Exception as e:
        logger.exception(f"Erro durante a configuração da conexão: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    main()
