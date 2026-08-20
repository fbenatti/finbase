"""
Script para conferir/disparar o seed dos mappings FOCUS e rodar a extração da conexão MongoDB Atlas Pro.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import SyncSession
from app.models.source_connection import SourceConnection
from app.services.mongodb_extractor import MongoAtlasExtractor
from app.services.task_runner import _run_etl_sync, _inmemory_tasks
from app.models.etl_task import EtlTask
from seed_mongodb_focus_mappings import ensure_mongodb_focus

print("Garantindo mapeamentos FOCUS para todas as conexões MongoDB Atlas (Pro e Enterprise)...")
res = ensure_mongodb_focus()
print(f"Mapeamentos aplicados: {res}")

with SyncSession() as session:
    conn = session.query(SourceConnection).filter_by(code="mongodb-atlas-pro").first()
    if not conn:
        print("ERRO: Conexão 'mongodb-atlas-pro' não encontrada no banco.")
        sys.exit(1)

    print(f"\nIniciando varredura (Scan) para {conn.name} (ID {conn.id})...")
    extractor = MongoAtlasExtractor(conn)
    scan_items = extractor._scan_sync(session)
    print(f"Total de invoices encontradas para Pro: {len(scan_items)}")

    for item in scan_items:
        print(f"  - Invoice: {item.blob_path} | Período: {item.billing_period} | Status: {item.status}")

    print("\nDisparando ETL de importação para a camada Bronze...")
    task = EtlTask(
        task_type="extraction",
        status="QUEUED",
        connection_id=conn.id,
        connection_ids=[conn.id],
        billing_periods=[],
        selected_blobs=[],
        total_blobs=0,
        processed_blobs=0,
        rows_raw=0,
        rows_normalized=0,
        rows_destination=0,
        errors=[],
    )
    session.add(task)
    session.commit()
    session.refresh(task)

    _inmemory_tasks[task.id] = {
        "id": task.id,
        "connection_id": conn.id,
        "status": "QUEUED",
        "task_type": "extraction",
    }

    _run_etl_sync(task.id, conn.id, [], [])

    session.refresh(task)
    print(f"\nETL Finalizada para MongoDB Atlas Pro!")
    print(f"Task ID: {task.id}")
    print(f"Status: {task.status}")
    print(f"Linhas importadas (Raw): {task.rows_raw}")
    if task.errors:
        print(f"Erros encontrados: {task.errors}")
    else:
        print("Importação concluída com sucesso sem erros!")
