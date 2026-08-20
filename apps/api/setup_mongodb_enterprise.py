"""
Script para cadastrar/atualizar a conexão mongodb-atlas-enterprise e disparar a extração.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import SyncSession
from app.models.source_connection import SourceConnection
from app.config import _load_env_file, _parse_atlas_contract
from seed_mongodb_focus_mappings import ensure_mongodb_focus

ATLAS_FILE = Path(__file__).resolve().parent.parent.parent / ".env-atlas"

print(f"Lendo credenciais de: {ATLAS_FILE}")
env_data = _load_env_file(ATLAS_FILE)
ent_raw = env_data.get("contrato_enterprise", "")
base_url = env_data.get("base_url", "https://cloud.mongodb.com/api/atlas/v1.0")

if not ent_raw:
    print("ERRO: contrato_enterprise não encontrado no .env-atlas")
    sys.exit(1)

gid, pub, priv = _parse_atlas_contract(ent_raw)
print(f"Dados parseados para Enterprise -> Group ID: {gid}, Public Key: {pub[:5]}... Priv Key: {priv[:5]}...")

with SyncSession() as db:
    from sqlalchemy import select

    conn = db.execute(
        select(SourceConnection).where(SourceConnection.code == "mongodb-atlas-enterprise")
    ).scalar_one_or_none()

    config_payload = {
        "base_url": base_url,
        "contract_type": "enterprise",
        "group_id": gid,
        "public_key": pub,
        "private_key": priv,
    }

    if conn:
        print("Conexão 'mongodb-atlas-enterprise' existente encontrada. Atualizando dados...")
        conn.name = "MongoDB Atlas Enterprise"
        conn.cloud_name = "Atlas-MongoDB"
        conn.cloud_scope = "atlas"
        conn.config_json = config_payload
    else:
        print("Criando nova conexão 'mongodb-atlas-enterprise'...")
        conn = SourceConnection(
            code="mongodb-atlas-enterprise",
            name="MongoDB Atlas Enterprise",
            connector_type="mongodb_atlas",
            cloud_name="Atlas-MongoDB",
            cloud_scope="atlas",
            config_json=config_payload,
            blob_prefixes=[],
        )
        db.add(conn)

    db.commit()
    db.refresh(conn)
    conn_id = conn.id
    print(f"Conexão salva com sucesso (ID: {conn_id}).")

    # Mapeamentos FOCUS
    print("Garantindo mapeamentos FOCUS para MongoDB Atlas...")
    ensure_mongodb_focus()

print("Executando teste de conexão...")
from app.services.mongodb_extractor import MongoAtlasExtractor
test_result = MongoAtlasExtractor(conn).test_connection()
print(f"Resultado do Teste de Conexão: {test_result}")

print(f"\n[SUCESSO] Conexão ID {conn_id} pronta para varredura e importação.")
