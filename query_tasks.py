import asyncio
from sqlalchemy import select
from app.database import async_session
from app.models.etl_task import EtlTask
from app.models.source_connection import SourceConnection
import requests

async def main():
    async with async_session() as session:
        result = await session.execute(
            select(EtlTask, SourceConnection.name, SourceConnection.id)
            .join(SourceConnection, SourceConnection.id == EtlTask.connection_id)
            .where(EtlTask.status == 'FAILED')
        )
        tasks = result.all()
        for task, name, conn_id in tasks:
            if "Limite de memoria" in str(task.current_blob) or "Limite de memoria" in str(task.phase):
                print(f"Task ID: {task.id}, Conn: {name} (ID: {conn_id}), Started: {task.started_at}, Finished: {task.finished_at}")
                print(f"  Phase: {task.phase}")
                print(f"  Blob: {task.current_blob}")
                
                # Make a POST request to reprocess
                try:
                    res = requests.post(f"http://localhost:8000/etl/run", json={"connection_id": conn_id})
                    print(f"  Reprocess response: {res.json()}")
                except Exception as e:
                    print(f"  Reprocess failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
