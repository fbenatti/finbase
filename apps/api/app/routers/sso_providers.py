"""CRUD de provedores SSO (OIDC/SAML2). Segredos cifrados, nunca retornados."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import require
from app.core.sso_crypto import protect_config, sanitize_config, decrypt_config
from app.database import get_db
from app.models.identity import SsoProvider, User

router = APIRouter(prefix="/sso/providers", tags=["sso"])


class ProviderCreate(BaseModel):
    name: str
    protocol: str  # oidc|saml
    enabled: bool = False
    config_json: dict = {}


class ProviderUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    config_json: dict | None = None


def _provider_out(p: SsoProvider) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "protocol": p.protocol,
        "enabled": p.enabled,
        "config_json": sanitize_config(p.config_json),
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


@router.get("")
async def list_providers(db: AsyncSession = Depends(get_db), user: User = Depends(require("auth", "read"))):
    result = await db.execute(select(SsoProvider).order_by(SsoProvider.name))
    return [_provider_out(p) for p in result.scalars().all()]


@router.post("", status_code=201)
async def create_provider(body: ProviderCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require("auth", "create"))):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Nome é obrigatório")
    if body.protocol not in ("oidc", "saml"):
        raise HTTPException(status_code=422, detail="protocol deve ser 'oidc' ou 'saml'")
    existing = (await db.execute(select(SsoProvider).where(SsoProvider.name == name))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=422, detail="Já existe provedor com este nome")
    provider = SsoProvider(
        name=name, protocol=body.protocol, enabled=body.enabled,
        config_json=protect_config(dict(body.config_json)),
    )
    db.add(provider)
    await db.commit()
    return _provider_out(provider)


@router.put("/{provider_id}")
async def update_provider(provider_id: int, body: ProviderUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(require("auth", "update"))):
    provider = (await db.execute(select(SsoProvider).where(SsoProvider.id == provider_id))).scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=404, detail="Provedor não encontrado")
    if body.name:
        provider.name = body.name.strip()
    if body.enabled is not None:
        provider.enabled = body.enabled
    if body.config_json is not None:
        # merge sobre o config existente (cifrado) — segredos vêm só no create ou
        # se o cliente mandar campo em claro novamente
        current = dict(provider.config_json or {})
        current.update(protect_config(dict(body.config_json)))
        provider.config_json = current
    await db.commit()
    return _provider_out(provider)


@router.delete("/{provider_id}")
async def delete_provider(provider_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require("auth", "delete"))):
    provider = (await db.execute(select(SsoProvider).where(SsoProvider.id == provider_id))).scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=404, detail="Provedor não encontrado")
    await db.delete(provider)
    await db.commit()
    return {"ok": True}


@router.post("/{provider_id}/test")
async def test_provider(provider_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require("auth", "update"))):
    provider = (await db.execute(select(SsoProvider).where(SsoProvider.id == provider_id))).scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=404, detail="Provedor não encontrado")
    cfg = decrypt_config(provider.config_json)
    try:
        if provider.protocol == "oidc":
            from authlib.integrations.httpx_client import AsyncOAuth2Client
            discovery = cfg.get("discovery_url") or (cfg.get("issuer", "").rstrip("/") + "/.well-known/openid-configuration")
            async with AsyncOAuth2Client() as client:
                resp = await client.get(discovery)
                if resp.status_code != 200:
                    return {"ok": False, "message": f"Discovery respondeu HTTP {resp.status_code}"}
                data = resp.json()
                if "authorization_endpoint" not in data:
                    return {"ok": False, "message": "Discovery sem authorization_endpoint"}
            return {"ok": True, "message": "Discovery OIDC válido"}
        else:
            return {"ok": True, "message": "SAML: verifique metadata e certificado no IdP"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"Falha: {exc}"}
