from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.source_connection import SourceConnection
from app.services import iceberg_service as iceberg
from app.services.json_schema_discovery import discover_nested_columns

router = APIRouter(prefix="/dados/esquema", tags=["schema"])


@router.get("/{connection_id}")
async def discover_schema(connection_id: int, db: AsyncSession = Depends(get_db)):
    """Discover available columns from ingested data for a given connection."""
    conn = await db.get(SourceConnection, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    table_name = f"bronze.raw_{conn.code.replace('-', '_').replace('.', '_')}"
    try:
        table = iceberg._get_catalog().load_table(table_name)
        top_level = [
            field.name
            for field in table.schema().fields
            if not field.name.startswith("_")
        ]
        columns = [
            {
                "name": name,
                "nested": False,
                "parent": None,
                "sample_values": [],
                "null_count": 0,
                "total_count": 0,
            }
            for name in top_level
        ]
    except Exception:
        return {
            "connection_id": connection_id,
            "connector_type": conn.connector_type,
            "columns": [],
            "sample_count": 0,
            "message": "Nenhum Bronze carregado para esta fonte. Importe os CSVs corretos antes de criar o relacionamento.",
        }

    sample_count = 0
    try:
        sample_df = iceberg.read_bronze(conn.code, max_rows=200)
        sample_count = len(sample_df)
        nested_columns = discover_nested_columns(sample_df, top_level)
        columns.extend(nested_columns)
    except Exception:
        # Schema discovery must still work when sampling fails.
        pass

    return {
        "connection_id": connection_id,
        "connector_type": conn.connector_type,
        "columns": columns,
        "sample_count": sample_count,
    }
