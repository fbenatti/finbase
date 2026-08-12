from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from app.database import get_db
from app.models.destination import DestinationTable, DestinationColumn, SourceDestinationMapping
from app.schemas.schemas import (
    DestinationTableCreate, DestinationTableUpdate, DestinationTableOut,
    DestinationColumnCreate, DestinationColumnOut,
    SourceDestinationMappingCreate, SourceDestinationMappingUpdate, SourceDestinationMappingOut,
    DestinationTableDetailOut,
)

router = APIRouter(prefix="/destinations", tags=["destinations"])


# ─── Destination Tables ──────────────────────────────────────────────────

@router.get("/tables/", response_model=list[DestinationTableOut])
async def list_destination_tables(
    active_only: bool = Query(True),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(DestinationTable).order_by(DestinationTable.name)
    if active_only:
        stmt = stmt.where(DestinationTable.active == True)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/tables/{table_id}", response_model=DestinationTableDetailOut)
async def get_destination_table(table_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DestinationTable).where(DestinationTable.id == table_id))
    dt = result.scalar_one_or_none()
    if not dt:
        raise HTTPException(status_code=404, detail="Destination table not found")

    cols_result = await db.execute(
        select(DestinationColumn)
        .where(DestinationColumn.destination_table_id == table_id)
        .order_by(DestinationColumn.position)
    )
    columns = cols_result.scalars().all()

    return DestinationTableDetailOut(
        id=dt.id, name=dt.name, description=dt.description,
        physical_name=dt.physical_name, active=dt.active,
        created_at=dt.created_at, updated_at=dt.updated_at,
        columns=[DestinationColumnOut.model_validate(c) for c in columns],
    )


@router.post("/tables/", response_model=DestinationTableOut, status_code=201)
async def create_destination_table(data: DestinationTableCreate, db: AsyncSession = Depends(get_db)):
    physical_name = data.physical_name or f"dest_{data.name.lower().replace(' ', '_').replace('-', '_')}"

    dt = DestinationTable(
        name=data.name,
        description=data.description,
        physical_name=physical_name,
        active=data.active,
    )
    db.add(dt)
    await db.commit()
    await db.refresh(dt)
    return dt


# ─── Destination Columns ─────────────────────────────────────────────────

@router.get("/tables/{table_id}/columns/", response_model=list[DestinationColumnOut])
async def list_columns(table_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(DestinationColumn)
        .where(DestinationColumn.destination_table_id == table_id)
        .order_by(DestinationColumn.position)
    )
    return result.scalars().all()


@router.post("/tables/{table_id}/columns/", response_model=DestinationColumnOut, status_code=201)
async def add_column(table_id: int, data: DestinationColumnCreate, db: AsyncSession = Depends(get_db)):
    dt_result = await db.execute(select(DestinationTable).where(DestinationTable.id == table_id))
    dt = dt_result.scalar_one_or_none()
    if not dt:
        raise HTTPException(status_code=404, detail="Destination table not found")

    existing = await db.execute(
        select(DestinationColumn).where(
            DestinationColumn.destination_table_id == table_id,
            DestinationColumn.column_name == data.column_name,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Column '{data.column_name}' already exists")

    if data.position is None:
        pos_result = await db.execute(
            select(DestinationColumn)
            .where(DestinationColumn.destination_table_id == table_id)
            .order_by(DestinationColumn.position.desc())
        )
        last = pos_result.scalars().first()
        position = (last.position + 1) if last else 1
    else:
        position = data.position

    col = DestinationColumn(
        destination_table_id=table_id,
        column_name=data.column_name,
        data_type=data.data_type,
        is_required=data.is_required,
        position=position,
        default_value=data.default_value,
    )
    db.add(col)
    await db.commit()
    await db.refresh(col)
    return col


@router.delete("/columns/{column_id}", status_code=204)
async def delete_column(column_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DestinationColumn).where(DestinationColumn.id == column_id))
    col = result.scalar_one_or_none()
    if not col:
        raise HTTPException(status_code=404, detail="Column not found")

    await db.delete(col)
    await db.commit()


@router.get("/tables/{table_id}/schema")
async def destination_table_schema(table_id: int, db: AsyncSession = Depends(get_db)):
    """Return column names and types from a destination table — for use in rule autocomplete."""
    dt_result = await db.execute(select(DestinationTable).where(DestinationTable.id == table_id))
    dt = dt_result.scalar_one_or_none()
    if not dt:
        raise HTTPException(status_code=404, detail="Destination table not found")

    cols_result = await db.execute(
        select(DestinationColumn)
        .where(DestinationColumn.destination_table_id == table_id)
        .order_by(DestinationColumn.position)
    )
    columns = cols_result.scalars().all()

    return {
        "table_id": table_id,
        "table_name": dt.name,
        "physical_name": dt.physical_name,
        "columns": [{"name": c.column_name, "type": c.data_type, "required": c.is_required} for c in columns],
    }


# ─── Source → Destination Mappings ───────────────────────────────────────

@router.get("/mappings/", response_model=list[SourceDestinationMappingOut])
async def list_source_dest_mappings(
    source_connection_id: int | None = Query(None),
    destination_table_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(SourceDestinationMapping).order_by(SourceDestinationMapping.priority)
    if source_connection_id is not None:
        stmt = stmt.where(SourceDestinationMapping.source_connection_id == source_connection_id)
    if destination_table_id is not None:
        stmt = stmt.where(SourceDestinationMapping.destination_table_id == destination_table_id)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/mappings/", response_model=SourceDestinationMappingOut, status_code=201)
async def create_source_dest_mapping(
    data: SourceDestinationMappingCreate,
    db: AsyncSession = Depends(get_db),
):
    mapping = SourceDestinationMapping(**data.model_dump())
    db.add(mapping)
    await db.commit()
    await db.refresh(mapping)
    return mapping


@router.put("/mappings/{mapping_id}", response_model=SourceDestinationMappingOut)
async def update_source_dest_mapping(
    mapping_id: int,
    data: SourceDestinationMappingUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SourceDestinationMapping).where(SourceDestinationMapping.id == mapping_id)
    )
    mapping = result.scalar_one_or_none()
    if not mapping:
        raise HTTPException(status_code=404, detail="Mapping not found")
    update_data = data.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(mapping, k, v)
    await db.commit()
    await db.refresh(mapping)
    return mapping


@router.delete("/mappings/{mapping_id}", status_code=204)
async def delete_source_dest_mapping(mapping_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SourceDestinationMapping).where(SourceDestinationMapping.id == mapping_id)
    )
    mapping = result.scalar_one_or_none()
    if not mapping:
        raise HTTPException(status_code=404)
    await db.delete(mapping)
    await db.commit()
