from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from app.database import get_db
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.classification import ClassificationVersion, FinancialBaseVersion
from app.services.classification_service import create_version, build_classification, publish_classification
from app.models.etl_task import EtlTask

router = APIRouter(prefix="/classification", tags=["classification"])

class BuildRequest(BaseModel):
    destination_table_id: int
    mode: str = "FULL"

@router.get("/versions")
async def versions(destination_table_id: int | None = None, db: AsyncSession = Depends(get_db)):
    q = select(ClassificationVersion).order_by(ClassificationVersion.created_at.desc())
    if destination_table_id: q = q.where(ClassificationVersion.destination_table_id == destination_table_id)
    return [{c.name: getattr(v, c.name) for c in v.__table__.columns} for v in (await db.execute(q)).scalars().all()]

@router.get("/active/{destination_table_id}")
async def active(destination_table_id: int, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(ClassificationVersion).where(ClassificationVersion.destination_table_id == destination_table_id, ClassificationVersion.status == "ACTIVE").order_by(ClassificationVersion.version.desc()))).scalars().first()
    if not row: raise HTTPException(404, "Nenhuma classificação ativa")
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}

@router.post("/build")
async def build(body: BuildRequest):
    try:
        version_id = create_version(body.destination_table_id, body.mode)
        from app.database import SyncSession
        s = SyncSession(); task = EtlTask(task_type="classification_build", status="QUEUED", destination_table_id=body.destination_table_id, result_json={"classification_version_id": version_id}, errors=[]); s.add(task); s.commit(); tid=task.id; s.close()
        return {"task_id": tid, "version_id": version_id, "status": "QUEUED"}
    except Exception as exc:
        raise HTTPException(500, str(exc))

@router.post("/versions/{version_id}/publish")
async def publish(version_id: int):
    import asyncio
    try: return await asyncio.to_thread(publish_classification, version_id)
    except Exception as exc: raise HTTPException(500, str(exc))
