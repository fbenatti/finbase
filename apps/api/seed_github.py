import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app.database import SyncSession
from app.models.source_connection import SourceConnection

def seed():
    session = SyncSession()
    
    # Verifica se já existe
    existing = session.query(SourceConnection).filter_by(code="github_billing_default").first()
    if existing:
        print("Conexão GitHub Billing já existe!")
        return

    conn = SourceConnection(
        code="github_billing_default",
        name="GitHub Billing Vivo",
        connector_type="github_billing",
        cloud_name="GitHub",
        cloud_scope="github",
        config_json={
            "enterprise_slug": "vivo",
            "pat_token": "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"
        },
        blob_prefixes=["github_reports"]
    )
    
    session.add(conn)
    session.commit()
    print("Conexão GitHub Billing criada com sucesso no banco de dados!")

if __name__ == "__main__":
    seed()
