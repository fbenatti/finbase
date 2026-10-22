"""Serviços auxiliares: Jev e itens futuros.

Segue o princípio das conexões: **endpoint e segredo não são chumbados**. O que é
variável vive em `config_json`; o token vive em `secret_json` e **nunca é devolvido
pela API** — o máximo que sai é um indicador de que existe.

O teste de conectividade é o que dá ao operador um estado observável, em vez de
descobrir a falha quando o P12 tentar usar o Jev.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.audit import audit
from app.core.deps import get_current_user
from app.core.outbound import ensure_allowed_url, integration_allowed_hosts
from app.models.source_connection import SystemIntegration
from app.models.identity import User

logger = logging.getLogger(__name__)
_CATALOG_DELETED_KEY = "__catalog_deleted"

router = APIRouter(prefix="/dados/integracoes", tags=["integrations"])

# Serviços previstos. `servicenow` nasce **desabilitado**: o plano pede que
# apareça como futuro, sem enviar dado.
KNOWN_INTEGRATIONS: tuple[dict[str, Any], ...] = (
    {
        "code": "jev",
        "name": "Jev",
        "integration_type": "ai_provider",
        "description": (
            "Serviço de decisão assistida. Não altera fato: candidato INVALID ou "
            "REQUIRES_VALIDATION nunca vira VALID por causa dele."
        ),
        "default_config": {"base_url": "", "model": "", "timeout_s": 60},
        "requires_secret": True,
    },
    {
        "code": "servicenow",
        "name": "ServiceNow",
        "integration_type": "itsm",
        "description": "Abertura de chamado a partir de uma ação aprovada. Item futuro.",
        "default_config": {"instance_url": ""},
        "requires_secret": True,
        "enabled_by_default": False,
    },
)


class IntegrationIn(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    config: dict[str, Any] | None = None
    # Write-only: entra, é gravado e nunca volta.
    secret: dict[str, Any] | None = None


def _public(linha: SystemIntegration) -> dict[str, Any]:
    """Representação segura: diz que o segredo existe, não qual é."""
    segredo = dict(linha.secret_json or {})
    return {
        "id": linha.id,
        "code": linha.code,
        "name": linha.name,
        "integration_type": linha.integration_type,
        "description": linha.description,
        "enabled": bool(linha.enabled),
        "config": dict(linha.config_json or {}),
        "secret_fields": sorted(segredo.keys()),
        "has_secret": bool(segredo),
        "last_test_at": linha.last_test_at.isoformat() if linha.last_test_at else None,
        "last_test_status": linha.last_test_status,
        "last_test_message": linha.last_test_message,
    }


def _catalogado(codigo: str) -> dict[str, Any] | None:
    return next((i for i in KNOWN_INTEGRATIONS if i["code"] == codigo), None)


@router.get("/")
async def list_integrations(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Serviços conhecidos, com o que está cadastrado e o que falta."""
    linhas = {
        i.code: i
        for i in (await db.execute(select(SystemIntegration))).scalars().all()
    }
    itens: list[dict[str, Any]] = []
    for conhecido in KNOWN_INTEGRATIONS:
        linha = linhas.get(conhecido["code"])
        if linha and (linha.config_json or {}).get(_CATALOG_DELETED_KEY):
            continue
        if linha:
            itens.append({**_public(linha), "catalog": conhecido})
        else:
            itens.append({
                "id": None,
                "code": conhecido["code"],
                "name": conhecido["name"],
                "integration_type": conhecido["integration_type"],
                "description": conhecido["description"],
                "enabled": bool(conhecido.get("enabled_by_default", False)),
                "config": dict(conhecido.get("default_config") or {}),
                "secret_fields": [],
                "has_secret": False,
                "last_test_at": None,
                "last_test_status": None,
                "last_test_message": None,
                "catalog": conhecido,
            })
    # Cadastrados que não estão no catálogo continuam visíveis.
    for codigo, linha in linhas.items():
        if not _catalogado(codigo) and not (linha.config_json or {}).get(_CATALOG_DELETED_KEY):
            itens.append({**_public(linha), "catalog": None})
    return {"items": itens}


@router.put("/{code}")
async def upsert_integration(
    code: str, payload: IntegrationIn, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Cria ou atualiza o serviço. O segredo é write-only."""
    conhecido = _catalogado(code)
    linha = (
        await db.execute(select(SystemIntegration).where(SystemIntegration.code == code))
    ).scalars().first()
    if not linha:
        linha = SystemIntegration(
            code=code,
            name=payload.name or (conhecido or {}).get("name") or code,
            integration_type=(conhecido or {}).get("integration_type") or "auxiliary",
            description=payload.description or (conhecido or {}).get("description"),
            enabled=(
                payload.enabled
                if payload.enabled is not None
                else bool((conhecido or {}).get("enabled_by_default", False))
            ),
            config_json=dict((conhecido or {}).get("default_config") or {}),
            secret_json={},
        )
        db.add(linha)
    elif _catalogado(code) and (linha.config_json or {}).get(_CATALOG_DELETED_KEY):
        # Salvar explicitamente um item removido do catálogo o restaura com os
        # valores padrão, sem reviver credenciais antigas.
        linha.config_json = dict((conhecido or {}).get("default_config") or {})
        linha.secret_json = {}
        linha.enabled = bool((conhecido or {}).get("enabled_by_default", False))

    if payload.name:
        linha.name = payload.name
    if payload.description is not None:
        linha.description = payload.description
    if payload.enabled is not None:
        linha.enabled = payload.enabled
    if payload.config:
        # Merge: configurar o endpoint não apaga o modelo já escolhido.
        atual = dict(linha.config_json or {})
        atual.update(payload.config)
        linha.config_json = atual
    if payload.secret:
        atual = dict(linha.secret_json or {})
        atual.update(payload.secret)
        linha.secret_json = atual

    await db.commit()
    await db.refresh(linha)
    return _public(linha)


@router.delete("/{code}", status_code=204)
async def delete_integration(
    code: str,
    request: Request,
    audit_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Remove o serviço auxiliar ou oculta permanentemente um item do catálogo."""
    linha = (
        await db.execute(select(SystemIntegration).where(SystemIntegration.code == code))
    ).scalars().first()
    conhecido = _catalogado(code)
    if not linha and not conhecido:
        raise HTTPException(status_code=404, detail=f"Serviço '{code}' não cadastrado")

    if not linha:
        linha = SystemIntegration(
            code=code,
            name=conhecido["name"],
            integration_type=conhecido["integration_type"],
            description=conhecido.get("description"),
            enabled=False,
            config_json={_CATALOG_DELETED_KEY: True},
            secret_json={},
        )
        db.add(linha)
        await db.flush()

    before = {
        "code": linha.code,
        "name": linha.name,
        "integration_type": linha.integration_type,
        "enabled": bool(linha.enabled),
        "has_secret": bool(linha.secret_json),
    }
    await audit(
        db, request, audit_user, module="connections", method="DELETE",
        action_label="delete", entity_type="integration", entity_id=linha.id,
        entity_label=linha.code, before=before, status_code=204,
    )
    if conhecido:
        linha.enabled = False
        linha.config_json = {_CATALOG_DELETED_KEY: True}
        linha.secret_json = {}
        linha.last_test_at = None
        linha.last_test_status = None
        linha.last_test_message = None
    else:
        await db.delete(linha)
    await db.commit()
    return Response(status_code=204)


@router.post("/{code}/testar")
async def test_integration(code: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Testa a conectividade e registra o resultado.

    Falha de teste **não** desabilita o serviço nem apaga configuração: é estado,
    não erro fatal.
    """
    linha = (
        await db.execute(select(SystemIntegration).where(SystemIntegration.code == code))
    ).scalars().first()
    if not linha:
        raise HTTPException(status_code=404, detail=f"Serviço '{code}' não cadastrado")

    config = dict(linha.config_json or {})
    segredo = dict(linha.secret_json or {})
    base_url = str(config.get("base_url") or "").strip()
    token = str(segredo.get("api_key") or "").strip()

    # O token é enviado a base_url: só para hosts configurados em INTEGRATION_ALLOWED_HOSTS.
    if base_url and token:
        try:
            ensure_allowed_url(base_url, integration_allowed_hosts())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    status, mensagem = "NOT_CONFIGURED", "Informe `base_url` e o token."
    if base_url and token:
        if str(linha.integration_type) == "ai_provider" and code == "jev":
            # O Jev é serviço de decisão tipada: o teste é uma pergunta trivial,
            # que prova endpoint, token e contrato de uma vez.
            from app.services.jev_service import smoke_test

            try:
                resultado = await smoke_test(linha)
                if resultado.get("status") == "OK":
                    status = "OK"
                    respostas = resultado.get("answers") or {}
                    mensagem = f"Decisão tipada recebida: {str(respostas)[:200]}"
                else:
                    status = "ERROR"
                    mensagem = str(resultado.get("reason"))[:250]
            except Exception as exc:  # noqa: BLE001
                status, mensagem = "ERROR", f"{type(exc).__name__}: {str(exc)[:200]}"
        else:
            try:
                async with httpx.AsyncClient(timeout=float(config.get("timeout_s") or 60)) as cliente:
                    resposta = await cliente.get(
                        base_url.rstrip("/") + "/v1/models",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                if resposta.status_code < 400:
                    status, mensagem = "OK", f"HTTP {resposta.status_code}"
                else:
                    status = "UNAUTHORIZED" if resposta.status_code in (401, 403) else "ERROR"
                    mensagem = f"HTTP {resposta.status_code}: {resposta.text[:200]}"
            except Exception as exc:  # noqa: BLE001
                status, mensagem = "UNREACHABLE", f"{type(exc).__name__}: {str(exc)[:200]}"

    linha.last_test_at = datetime.now(timezone.utc)
    linha.last_test_status = status
    linha.last_test_message = mensagem
    await db.commit()
    return {"code": code, "status": status, "message": mensagem}
