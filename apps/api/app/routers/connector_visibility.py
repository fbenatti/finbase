from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.deps import get_current_user, require, resolve_user_from_request
from app.database import get_db
from app.models.connector_visibility import ConnectorHidden
from app.models.identity import User

router = APIRouter(prefix="/dados/fontes/visibilidade", tags=["connector-visibility"])

# Tipos de conector que a página Fontes de dados conhece (espelha CONNECTOR_TYPES do front).
KNOWN_CONNECTOR_TYPES = frozenset({
    "azure_mca_invoice", "azure_blob", "azure_pricesheet", "azure_openai_ptu",
    "azure_resource_graph", "gcp_bigquery", "oci", "mongodb_atlas",
    "github_billing", "github_license", "github_token",
    "notebooklm_license", "notebooklm_token", "gemini_license", "gemini_token",
    "claude_license", "claude_token", "databricks",
})


class HiddenConnectorsIn(BaseModel):
    hidden: list[str] = Field(default_factory=list)


@router.get("/")
async def get_hidden_connectors(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    rows = (await db.execute(select(ConnectorHidden.connector_type))).scalars().all()
    return {"hidden": sorted(rows)}


@router.put("/")
async def set_hidden_connectors(
    body: HiddenConnectorsIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require("connections", "update")),
):
    hidden = sorted(set(body.hidden))
    unknown = [item for item in hidden if item not in KNOWN_CONNECTOR_TYPES]
    if unknown:
        raise HTTPException(422, f"Conector desconhecido: {', '.join(unknown)}")

    before = sorted((await db.execute(select(ConnectorHidden.connector_type))).scalars().all())
    await db.execute(delete(ConnectorHidden))
    db.add_all([ConnectorHidden(connector_type=item) for item in hidden])
    await db.flush()

    audit_user = await resolve_user_from_request(request, db)
    await audit(db, request, audit_user, module="connections", method="PUT", action_label="update",
                entity_type="connector_visibility", entity_label="Conectores ocultos em Fontes",
                before={"hidden": before}, after={"hidden": hidden}, status_code=200)
    await db.commit()
    return {"hidden": hidden}
