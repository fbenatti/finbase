from fastapi import APIRouter, Depends, HTTPException, Query
from datetime import date
import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import String, cast, delete, func, or_, select
from app.database import get_db
from app.models.source_connection import SourceConnection
from app.models.ingested_blob import IngestedBlob
from app.models.etl_task import EtlTask
from app.models.skipped_blob import SkippedBlob
from app.models.period_status import PeriodStatus
from app.schemas.schemas import SourceConnectionCreate, SourceConnectionUpdate, SourceConnectionOut

router = APIRouter(prefix="/connections", tags=["connections"])


GRID_FIELDS = {
    "name": SourceConnection.name,
    "code": SourceConnection.code,
    "provider": SourceConnection.cloud_scope,
    "connector_type": SourceConnection.connector_type,
    "cloud_name": SourceConnection.cloud_name,
    "updated_at": SourceConnection.updated_at,
    "test_status": SourceConnection.last_test_status,
    "last_test_status": SourceConnection.last_test_status,
}

# Categorias da página de Conexões: provedores de nuvem, ferramentas (IA /
# colaboração) e marketplace. Derivadas do connector_type — a UI filtra por elas.
TOOL_CONNECTOR_TYPES = (
    "github_billing", "github_license", "github_token",
    "notebooklm_license", "notebooklm_token",
    "claude_license", "claude_token",
    "gemini_license", "gemini_token", "gemini_notebooklm",
)
MARKETPLACE_CONNECTOR_TYPES = ("mongodb_atlas",)

GRID_FILTER_VALUES = {
    "provider": {
        "azure": "azure",
        "gcp": "gcp",
        "ibm cloud": "ibm",
        "aws": "aws",
        "oracle cloud (oci)": "oci",
        "mongodb atlas": "atlas",
        "github": "github",
        "notebooklm": "notebooklm",
        "gemini": "gemini",
        "claude": "claude",
    },
    "connector_type": {
        "azure blob storage": "azure_blob",
        "gcp bigquery": "gcp_bigquery",
        "oracle cloud (oci)": "oci",
        "mongodb atlas": "mongodb_atlas",
        "upload manual de csv": "manual_csv",
        "github billing (copilot)": "github_billing",
        "github licenças (copilot)": "github_license",
        "github tokens (copilot)": "github_token",
        "notebooklm licenças": "notebooklm_license",
        "notebooklm tokens": "notebooklm_token",
        "claude licenças": "claude_license",
        "claude tokens": "claude_token",
        "gemini licenças": "gemini_license",
        "gemini tokens": "gemini_token",
        "gemini notebooklm": "gemini_notebooklm",
        "custo manual de ia": "manual_ai_cost",
    },
}


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


@router.get("/grid")
async def list_connections_grid(
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: str | None = None,
    provider: str | None = None,
    category: str | None = None,
    sort_field: str = "name",
    sort_direction: str = "asc",
    filter_field: str | None = None,
    filter_operator: str | None = None,
    filter_value: str | None = None,
    filters: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Server-side query used by the Connections DataGrid."""
    conditions = []
    if provider:
        conditions.append(SourceConnection.cloud_scope == provider)
    if category == "provedor":
        conditions.append(SourceConnection.connector_type.not_in(TOOL_CONNECTOR_TYPES + MARKETPLACE_CONNECTOR_TYPES))
    elif category == "ferramenta":
        conditions.append(SourceConnection.connector_type.in_(TOOL_CONNECTOR_TYPES))
    elif category == "marketplace":
        conditions.append(SourceConnection.connector_type.in_(MARKETPLACE_CONNECTOR_TYPES))
    if search:
        needle = f"%{search.strip()}%"
        conditions.append(or_(
            SourceConnection.name.ilike(needle),
            SourceConnection.code.ilike(needle),
            SourceConnection.cloud_name.ilike(needle),
            SourceConnection.cloud_scope.ilike(needle),
            SourceConnection.connector_type.ilike(needle),
            cast(SourceConnection.config_json, String).ilike(needle),
        ))

    filter_specs: list[dict[str, str]] = []
    if filters:
        try:
            parsed_filters = json.loads(filters)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="Filtros inválidos") from exc
        if not isinstance(parsed_filters, list):
            raise HTTPException(status_code=422, detail="Filtros inválidos")
        filter_specs = [item for item in parsed_filters if isinstance(item, dict)]
    elif filter_field and filter_value:
        filter_specs = [{"field": filter_field, "operator": filter_operator or "equals", "value": filter_value}]

    for filter_spec in filter_specs:
        current_field = str(filter_spec.get("field", ""))
        current_value = str(filter_spec.get("value", ""))
        current_operator = str(filter_spec.get("operator", "equals"))
        column = GRID_FIELDS.get(current_field)
        if current_field == "updated_at" and current_value:
            try:
                conditions.append(func.date(SourceConnection.updated_at) == date.fromisoformat(current_value[:10]))
            except ValueError:
                raise HTTPException(status_code=422, detail="Data de filtro inválida")
        elif column is not None and current_value:
            current_value = GRID_FILTER_VALUES.get(current_field, {}).get(current_value.lower(), current_value)
            if current_operator == "notEquals":
                conditions.append(or_(column.is_(None), cast(column, String) != current_value))
            else:
                conditions.append(cast(column, String) == current_value)

    sort_column = GRID_FIELDS.get(sort_field, SourceConnection.name)
    ordering = sort_column.desc() if sort_direction == "desc" else sort_column.asc()
    where = tuple(conditions)
    total = (await db.execute(select(func.count()).select_from(SourceConnection).where(*where))).scalar_one()
    result = await db.execute(
        select(SourceConnection).where(*where).order_by(ordering, SourceConnection.id).offset(offset).limit(limit)
    )
    return {"items": result.scalars().all(), "total": total}


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

    # Bloqueia deleção se ainda existem dados ingeridos (Bronze não limpo)
    blob_count_result = await db.execute(
        select(func.count(IngestedBlob.id)).where(IngestedBlob.connection_id == connection_id)
    )
    blob_count = blob_count_result.scalar_one()
    if blob_count > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Não é possível remover a conexão '{conn.name}': existem {blob_count} dataset(s) ingerido(s). "
                "Acesse Extração → 'Reprocessar origem limpa' para remover os dados Bronze antes de excluir a conexão."
            ),
        )

    # Guarda o code antes de deletar para dropar a tabela Bronze
    connection_code = conn.code

    # Remove registros filhos com FK sem CASCADE para evitar IntegrityError
    # etl_task — nullifica connection_id (nullable=True)
    await db.execute(
        EtlTask.__table__.update()
        .where(EtlTask.connection_id == connection_id)
        .values(connection_id=None)
    )
    # skipped_blob — remove diretamente
    await db.execute(
        delete(SkippedBlob).where(SkippedBlob.connection_id == connection_id)
    )
    # period_status — remove diretamente
    await db.execute(
        delete(PeriodStatus).where(PeriodStatus.connection_id == connection_id)
    )

    await db.delete(conn)
    await db.commit()

    # Remove a tabela Bronze do Iceberg (non-blocking — falha silenciosa não impede a deleção)
    try:
        from app.services import iceberg_service as iceberg
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, iceberg.reset_bronze, connection_code)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "Falha ao remover tabela Bronze para conexão '%s': %s", connection_code, exc
        )
