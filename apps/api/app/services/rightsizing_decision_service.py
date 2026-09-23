"""P9 — Histórico, versão e revisão humana.

Três aceites do plano guiam este serviço:

1. **"veto determinístico sobrevive a qualquer provider"** — o veredito do motor é
   gravado e **a revisão não o reescreve**. O Jev (P12) pode opinar; se ele mudar de
   ideia amanhã, o veto de ontem continua registrado.

2. **"revisão não muda premissas históricas"** — o snapshot DE/PARA é congelado no
   momento da decisão. Se o preço mudar depois, a decisão antiga continua mostrando
   o número com que foi tomada.

3. **"reexecução cria versão adequada, sem duplicação indevida"** — o
   `evidence_hash` cobre **as entradas que sustentam a decisão**, não o instante da
   execução. Enquanto a evidência não mudar, reexecutar não cria versão nova; quando
   mudar, cria. É o que distingue "rodei de novo" de "o mundo mudou".

Nenhum endpoint deste pacote altera VM — o sistema recomenda, o humano executa.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

TABLE = "rightsizing_decision"

# Estados de revisão. `PENDING` é o inicial: uma decisão nasce esperando humano.
REVIEW_STATUSES = ("PENDING", "APPROVED", "REJECTED", "DEFERRED")

# Campos que compõem a evidência. **Deliberadamente não inclui `run_id` nem
# `created_at`**: se incluísse, toda reexecução pareceria evidência nova e o
# aceite "sem duplicação indevida" seria violado.
CAMPOS_EVIDENCIA = (
    "resource_id", "sku_from", "sku_to", "deterministic_verdict",
    "veto_reason", "cost_from_current", "cost_to_estimated",
    "saving_monthly", "price_source_from", "price_source_to",
    "cpu_p95", "mem_p95", "observed_days",
)



def _ordenar_pelo_modelo(frame, modelo):
    """Ordena as colunas do frame pela ordem declarada no modelo.

    O writer apenas **anexa** `_billing_period` e `_processed_at` ao frame. Se o
    frame chegar com as colunas em outra ordem, o schema fisico nasce diferente do
    modelo e a proxima gravacao falha com "Mismatch in fields". Ordenar aqui faz a
    convencao valer para os dois servicos.
    """
    declaradas = [c for c in modelo.column_names() if c in frame.columns]
    extras = [c for c in frame.columns if c not in declaradas]
    return frame[declaradas + extras]


def evidence_hash(item: dict[str, Any]) -> str:
    """Hash das evidências materiais da decisão.

    Ordenado e sem carimbo de tempo: a mesma evidência produz o mesmo hash, o que
    torna a decisão reproduzível e impede duplicação por reexecução.
    """
    partes = []
    for campo in CAMPOS_EVIDENCIA:
        valor = item.get(campo)
        if isinstance(valor, float):
            # Formatação estável para não depender da representação binária.
            valor = f"{valor:.6f}"
        partes.append(f"{campo}={valor}")
    base = "|".join(partes)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]


async def build_decisions(*, run_id: str | None = None) -> dict[str, Any]:
    """Converte os candidatos do P8 em decisões versionadas.

    Só grava decisão para veredito `VALID` ou `REQUIRES_VALIDATION`: `INVALID` é
    veto, e um veto não é decisão a revisar — é registro de que não se deve agir.
    """
    from app.services import iceberg_service as iceberg
    from app.services import lakehouse_layers as layers
    from app.services.trino_service import execute as trino

    corrida = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    candidatos = await trino(
        f"""
        SELECT * FROM iceberg.silver.rightsizing_candidate
        WHERE verdict IN ('VALID', 'REQUIRES_VALIDATION')
          AND run_id = (SELECT MAX(run_id) FROM iceberg.silver.rightsizing_candidate)
        """,
        timeout_s=600,
    )
    if not candidatos:
        return {"run_id": corrida, "rows": 0, "message": "Sem candidatos elegíveis para decisão"}

    # Histórico: o que já foi decidido, para não duplicar e para **preservar a
    # revisão** quando a evidência não mudou.
    existentes: dict[str, dict[str, Any]] = {}
    try:
        ja = await trino(f"SELECT * FROM iceberg.silver.{TABLE}", timeout_s=300)
        for linha in ja or []:
            existentes[str(linha.get("evidence_hash"))] = linha
    except Exception:  # noqa: BLE001 - primeira execução
        pass

    carimbo = datetime.now(timezone.utc).isoformat()
    linhas: list[dict[str, Any]] = []
    novos = preservados = 0

    for c in candidatos:
        ev = evidence_hash(c)
        anterior = existentes.get(ev)
        if anterior:
            # Mesma evidência: mantém a revisão já feita. Reexecutar não pode
            # apagar a decisão de quem revisou.
            novos += 0
            preservados += 1
            linhas.append({
                **anterior,
                "run_id": corrida,
                "created_at": carimbo,
            })
            continue
        novos += 1
        linhas.append({
            "evidence_hash": ev,
            "run_id": corrida,
            "resource_id": c.get("resource_id"),
            "resource_name": c.get("resource_name"),
            "region_id": c.get("region_id"),
            "deterministic_verdict": c.get("verdict"),
            "veto_reason": c.get("veto_reason"),
            "confidence": c.get("confidence"),
            "review_status": "PENDING",
            "reviewed_by": None,
            "reviewed_at": None,
            "review_note": None,
            "sku_from": c.get("sku_from"),
            "vcpus_from": c.get("vcpus_from"),
            "memory_gb_from": c.get("memory_gb_from"),
            "cost_from_monthly": c.get("cost_from_current"),
            "sku_to": c.get("sku_to"),
            "vcpus_to": c.get("vcpus_to"),
            "memory_gb_to": c.get("memory_gb_to"),
            "cost_to_monthly": c.get("cost_to_estimated"),
            "saving_monthly": c.get("saving_monthly"),
            "price_source_from": c.get("price_source_from"),
            "price_source_to": c.get("price_source_to"),
            "price_comparable": c.get("price_comparable"),
            "cpu_p95": c.get("cpu_p95"),
            "observed_days": c.get("observed_days"),
            "assisted_status": None,
            "assisted_model": None,
            "assisted_reason": None,
            "created_at": carimbo,
            "_silver_owner": "rightsizing_decision",
            "_silver_ingested_at": carimbo,
            "_source_connection_id": None,
            "_source_connection_code": None,
            "_source_connection_name": c.get("_source_connection_name"),
            "_processed_at": carimbo,
        })

    frame = pd.DataFrame(linhas)

    # Deduplica por `evidence_hash`, ficando com a versão mais recente. Sem isto o
    # `write_silver` (que **acrescenta**) somaria a linha antiga e a nova, e a
    # mesma decisão apareceria duas vezes — violando "sem duplicação indevida".
    frame = frame.sort_values("created_at").drop_duplicates(
        subset=["evidence_hash"], keep="last"
    ).reset_index(drop=True)

    # A tabela é o histórico corrente de versões: substitui o período em vez de
    # acumular, para o rerun não empilhar cópias.
    periodo = datetime.now(timezone.utc).strftime("%Y%m")
    try:
        iceberg._get_catalog().load_table((layers.SILVER, TABLE)).delete(
            f"_billing_period = '{periodo}'"
        )
    except Exception:  # noqa: BLE001 - primeira execução
        pass

    from app.services.silver_models import model as _modelo

    frame = _ordenar_pelo_modelo(frame, _modelo("rightsizing_decision"))
    escrito = iceberg.write_silver(
        connection_code="rightsizing_decision",
        df=frame,
        billing_period=periodo,
        table_name_override=f"silver.{TABLE}",
    )

    por_status: dict[str, int] = {}
    for linha in linhas:
        chave = str(linha.get("review_status") or "")
        por_status[chave] = por_status.get(chave, 0) + 1

    return {
        "run_id": corrida,
        "rows": int(escrito or 0),
        "new_decisions": novos,
        "preserved_reviews": preservados,
        "review_status": por_status,
        "saving_pending_review": round(
            float(sum(
                (l.get("saving_monthly") or 0.0)
                for l in linhas
                if l.get("review_status") == "PENDING" and l.get("deterministic_verdict") == "VALID"
            )), 2
        ),
    }


def validar_transicao(atual: str, novo: str) -> str | None:
    """Devolve o motivo da recusa, ou `None` se a transição é válida.

    `APPROVED` e `REJECTED` são terminais: reabrir uma decisão já revisada
    apagaria o registro de quem decidiu. Para mudar de ideia, o correto é um novo
    ciclo com evidência nova — que gera outra versão e preserva a anterior.
    """
    if novo not in REVIEW_STATUSES:
        return f"Estado '{novo}' inválido. Aceitos: {', '.join(REVIEW_STATUSES)}."
    if atual in ("APPROVED", "REJECTED") and novo != atual:
        return (
            f"Decisão já revisada como {atual} não pode virar {novo}. "
            "Reabra com evidência nova, que gera outra versão."
        )
    return None
