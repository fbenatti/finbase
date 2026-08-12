from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.source_connection import SourceConnection
from app.models.ingested_blob import IngestedBlob
from app.schemas.schemas import SourceConnectionCreate, SourceConnectionUpdate, SourceConnectionOut

router = APIRouter(prefix="/connections", tags=["connections"])


@router.get("/", response_model=list[SourceConnectionOut])
async def list_connections(
    with_data: bool = Query(False, description="Only return connections that have ingested data"),
    db: AsyncSession = Depends(get_db),
):
    if with_data:
        # Subquery: connection IDs that have at least one ingested blob
        sub = select(IngestedBlob.connection_id).distinct().subquery()
        result = await db.execute(
            select(SourceConnection)
            .where(SourceConnection.id.in_(select(sub.c.connection_id)))
            .order_by(SourceConnection.code)
        )
    else:
        result = await db.execute(select(SourceConnection).order_by(SourceConnection.code))
    return result.scalars().all()


@router.get("/{connection_id}", response_model=SourceConnectionOut)
async def get_connection(connection_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SourceConnection).where(SourceConnection.id == connection_id)
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    return conn


@router.post("/", response_model=SourceConnectionOut, status_code=201)
async def create_connection(data: SourceConnectionCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(
        select(SourceConnection).where(SourceConnection.code == data.code)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Code '{data.code}' already exists")

    conn = SourceConnection(**data.model_dump())
    db.add(conn)
    await db.commit()
    await db.refresh(conn)
    return conn


@router.put("/{connection_id}", response_model=SourceConnectionOut)
async def update_connection(
    connection_id: int, data: SourceConnectionUpdate, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(SourceConnection).where(SourceConnection.id == connection_id)
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(conn, key, value)
    await db.commit()
    await db.refresh(conn)
    return conn


@router.delete("/{connection_id}", status_code=204)
async def delete_connection(connection_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SourceConnection).where(SourceConnection.id == connection_id)
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    await db.delete(conn)
    await db.commit()
