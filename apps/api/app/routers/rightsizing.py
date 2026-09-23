"""API do P9 — decisões de rightsizing e revisão humana.

Regra que atravessa o pacote: **nenhum endpoint altera VM**. O sistema recomenda e
registra a decisão; a execução é humana e fora daqui. Um endpoint que "aplicasse" a
mudança seria o tipo de automação que o plano proíbe nesta fase.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.rightsizing_decision_service import (
    REVIEW_STATUSES, validar_transicao,
)
from app.services.trino_service import execute as trino_execute

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rightsizing", tags=["rightsizing"])


class ReviewIn(BaseModel):
    status: str = Field(..., description="PENDING | APPROVED | REJECTED | DEFERRED")
    reviewer: str | None = Field(None, max_length=160)
    note: str | None = Field(None, max_length=2000)


def _tabela() -> str:
    return "iceberg.silver.rightsizing_decision"


async def _revisoes_atuais(db: AsyncSession, hashes: list[str]) -> dict[str, dict[str, Any]]:
    """Estado de revisão mais recente por `evidence_hash`.

    A revisão não pode ser gravada na tabela Iceberg (ela é *append-only* e o
    snapshot tem de permanecer imutável). Então ela vive no PostgreSQL e é
    **sobreposta na leitura** — sem isso o estado voltaria a `PENDING` a cada
    consulta e uma decisão já aprovada aceitaria nova revisão.
    """
    if not hashes:
        return {}
    from app.models.rightsizing_review import RightsizingReview

    linhas = (await db.execute(
        select(RightsizingReview)
        .where(RightsizingReview.evidence_hash.in_(hashes))
        .order_by(RightsizingReview.id.asc())
    )).scalars().all()
    # A última gravação vence; percorrer em ordem crescente deixa a mais recente.
    atual: dict[str, dict[str, Any]] = {}
    for linha in linhas:
        atual[linha.evidence_hash] = {
            "review_status": linha.review_status,
            "reviewed_by": linha.reviewer,
            "reviewed_at": linha.reviewed_at.isoformat() if linha.reviewed_at else None,
            "review_note": linha.note,
        }
    return atual


@router.get("/decisions")
async def list_decisions(
    verdict: str | None = Query(None, description="VALID | INVALID | REQUIRES_VALIDATION"),
    review_status: str | None = Query(None, description="PENDING | APPROVED | REJECTED | DEFERRED"),
    resource_id: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Lista as decisões com os filtros que a revisão humana precisa."""
    filtros: list[str] = []
    if verdict:
        filtros.append(f"deterministic_verdict = '{verdict}'")
    if review_status:
        filtros.append(f"review_status = '{review_status}'")
    if resource_id:
        # `resource_id` vem do usuário; escapar aspas simples evita quebra de SQL.
        seguro = resource_id.replace("'", "''")
        filtros.append(f"resource_id = '{seguro}'")
    onde = (" WHERE " + " AND ".join(filtros)) if filtros else ""

    try:
        linhas = await trino_execute(
            f"SELECT * FROM {_tabela()}{onde} ORDER BY saving_monthly DESC NULLS LAST LIMIT {int(limit)}",
            timeout_s=120,
        )
    except Exception as exc:  # noqa: BLE001
        # Tabela ausente é estado ("ainda não há decisão"), não erro do cliente.
        logger.info("rightsizing_decision indisponível: %s", exc)
        return {"items": [], "total": 0, "message": "Sem decisões registradas ainda."}

    # Sobrepõe a revisão gravada: o estado real do fluxo humano está no banco.
    revisoes = await _revisoes_atuais(db, [
        str(l.get("evidence_hash")) for l in (linhas or [])
    ])
    for linha in linhas or []:
        revisao = revisoes.get(str(linha.get("evidence_hash")))
        if revisao:
            linha.update(revisao)

    resumo = {
        "PENDING": 0, "APPROVED": 0, "REJECTED": 0, "DEFERRED": 0,
    }
    saving_aprovado = saving_pendente = 0.0
    for linha in linhas or []:
        estado = str(linha.get("review_status") or "PENDING")
        resumo[estado] = resumo.get(estado, 0) + 1
        # Só conta saving com preço comparável: somar ausência como zero mentiria.
        valor = linha.get("saving_monthly")
        if valor is not None and str(linha.get("deterministic_verdict")) == "VALID":
            if estado == "APPROVED":
                saving_aprovado += float(valor)
            elif estado == "PENDING":
                saving_pendente += float(valor)

    return {
        "items": linhas or [],
        "total": len(linhas or []),
        "review_summary": resumo,
        "saving_approved": round(saving_aprovado, 2),
        "saving_pending": round(saving_pendente, 2),
    }


@router.get("/decisions/{evidence_hash}")
async def get_decision(evidence_hash: str) -> dict[str, Any]:
    """Uma decisão, com o snapshot DE/PARA congelado."""
    seguro = evidence_hash.replace("'", "''")
    linhas = await trino_execute(
        f"SELECT * FROM {_tabela()} WHERE evidence_hash = '{seguro}'",
        timeout_s=120,
    )
    if not linhas:
        raise HTTPException(status_code=404, detail="Decisão não encontrada")
    return linhas[0]


@router.put("/decisions/{evidence_hash}/review")
async def review_decision(
    evidence_hash: str, payload: ReviewIn, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Registra a revisão humana.

    A revisão **não altera o veredito determinístico**. O que o motor concluiu fica
    na linha; o que o humano fez fica ao lado. É o aceite "veto determinístico
    sobrevive a qualquer provider": trocar o Jev amanhã não reescreve o veto de
    ontem.
    """
    seguro = evidence_hash.replace("'", "''")
    atual = await trino_execute(
        f"SELECT review_status, deterministic_verdict FROM {_tabela()} "
        f"WHERE evidence_hash = '{seguro}'",
        timeout_s=120,
    )
    if not atual:
        raise HTTPException(status_code=404, detail="Decisão não encontrada")

    revisoes = await _revisoes_atuais(db, [evidence_hash])
    estado_atual = str(
        (revisoes.get(evidence_hash) or {}).get("review_status") or "PENDING"
    )
    recusa = validar_transicao(estado_atual, payload.status.upper())
    if recusa:
        raise HTTPException(status_code=409, detail=recusa)

    # A decisão vive numa tabela Iceberg, sem UPDATE linha a linha: a revisão é
    # gravada como metadado próprio, preservando o snapshot.
    from app.models.rightsizing_review import RightsizingReview  # noqa: WPS433

    registro = RightsizingReview(
        evidence_hash=evidence_hash,
        review_status=payload.status.upper(),
        reviewer=payload.reviewer,
        note=payload.note,
        previous_status=estado_atual,
        reviewed_at=datetime.now(timezone.utc),
    )
    db.add(registro)
    await db.commit()
    return {
        "evidence_hash": evidence_hash,
        "previous_status": estado_atual,
        "review_status": payload.status.upper(),
        "deterministic_verdict": atual[0].get("deterministic_verdict"),
        "message": "Revisão registrada. O veredito determinístico não foi alterado.",
    }


@router.get("/history")
async def decisions_history(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    """Histórico por execução — quantas decisões e quanto saving cada uma gerou."""
    try:
        linhas = await trino_execute(
            f"""
            SELECT run_id, count(*) AS decisions,
                   count(*) FILTER (WHERE deterministic_verdict = 'VALID') AS valid,
                   count(*) FILTER (WHERE deterministic_verdict = 'REQUIRES_VALIDATION') AS requires_validation,
                   ROUND(SUM(COALESCE(saving_monthly, 0)), 2) AS saving_total
            FROM {_tabela()}
            GROUP BY run_id ORDER BY run_id DESC LIMIT {int(limit)}
            """,
            timeout_s=180,
        )
    except Exception:  # noqa: BLE001
        return {"items": []}
    return {"items": linhas or []}
