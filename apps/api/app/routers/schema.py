from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.source_connection import SourceConnection
from app.services import iceberg_service as iceberg

router = APIRouter(prefix="/schema", tags=["schema"])


@router.get("/{connection_id}")
async def discover_schema(connection_id: int, db: AsyncSession = Depends(get_db)):
    """Discover available columns from ingested data for a given connection."""
    # Verify connection exists
    conn = await db.get(SourceConnection, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    # Relationship must reflect exactly what was loaded, never an arbitrary
    # file still present in Azure.  Azure is immutable source storage and may
    # contain old reports that were deliberately removed from ingestion.
    table_name = f"bronze.raw_{conn.code.replace('-', '_').replace('.', '_')}"
    try:
        table = iceberg._get_catalog().load_table(table_name)
        columns = [
            {"name": field.name, "sample_values": [], "null_count": 0, "total_count": 0}
            for field in table.schema().fields
            if not field.name.startswith("_")
        ]
    except Exception:
        return {
            "connection_id": connection_id,
            "connector_type": conn.connector_type,
            "columns": [],
            "sample_count": 0,
            "message": "Nenhum Bronze carregado para esta fonte. Importe os CSVs corretos antes de criar o relacionamento.",
        }

    return {
        "connection_id": connection_id,
        "connector_type": conn.connector_type,
        "columns": columns,
        "sample_count": 0,
    }
