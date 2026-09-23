"""P10 — Cubos Gold do MVP.

O ADR das camadas fixa o papel da Gold: **descoberta e cruzamento final**, com
agregações no grão que o consumidor usa. Não é cópia linha a linha da FOCUS —
tentar isso derrubou o Trino (36,4 M linhas em um join), e cópia não é cubo.

Quatro cubos, cada um respondendo uma pergunta da UI:

| Cubo | Pergunta | Grão |
|---|---|---|
| `gold.cost_by_resource` | quanto cada recurso custou, por período e conexão | recurso × período |
| `gold.opportunity_summary` | onde está a oportunidade de saving | veredito × conexão |
| `gold.decision_status` | o que foi decidido e o que falta revisar | veredito × revisão |
| `gold.aks_consumption` | como o custo de AKS se distribui | nível × cluster × grupo |

Todos são **agregados dos canônicos da Silver** — a Gold cruza o que já está no
mesmo padrão, e nada aqui reinterpreta custo.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from app.services.trino_service import execute as trino_execute

logger = logging.getLogger(__name__)

# Grão por cubo: as colunas de agrupamento que o drilldown da UI navega.
CUBOS: dict[str, dict[str, Any]] = {}


def _sql_custo_por_recurso() -> str:
    """Custo por recurso e período, com atributos do inventário.

    O grão é recurso × período: o resumo executivo soma este cubo, e o detalhe
    clica nele. Somar SEMPRE por `_source_connection_name` evita misturar 4P e TI
    na mesma linha.
    """
    return """
    SELECT
        c.resource_id,
        MAX(c.resource_name)                          AS resource_name,
        MAX(c.resource_sku)                           AS resource_sku,
        MAX(c.region_id)                              AS region_id,
        MAX(c._source_connection_name)                AS source_connection,
        MAX(c._source_connection_code)                AS connection_code,
        c._billing_period,
        SUM(c.effective_cost)                         AS effective_cost,
        SUM(c.billed_cost)                            AS billed_cost,
        COUNT(*)                                      AS cost_lines
    FROM iceberg.silver.resource_cost c
    GROUP BY c.resource_id, c._billing_period
    """


def _sql_oportunidade() -> str:
    """Oportunidade de saving agregada por veredito e conexão.

    O resumo executivo soma os VALID; somar as alternativas (`REQUIRES_VALIDATION`
    junto) violaria o aceite "total do resumo reconcilia sem somar alternativas" —
    por isso o veredito é coluna, nunca somado junto.
    """
    return """
    SELECT
        d.deterministic_verdict                       AS verdict,
        d._source_connection_name                     AS source_connection,
        d.region_id,
        COUNT(*)                                      AS decisions,
        ROUND(SUM(COALESCE(d.saving_monthly, 0)), 2)  AS saving_with_value,
        COUNT(*) FILTER (WHERE d.saving_monthly IS NULL) AS without_price,
        MIN(d.sku_from)                               AS sample_from,
        MIN(d.sku_to)                                 AS sample_to
    FROM iceberg.silver.rightsizing_decision d
    GROUP BY d.deterministic_verdict, d._source_connection_name, d.region_id
    """


def _sql_decisao() -> str:
    """Estado das revisões humanas.

    Lê o estado **após** a sobreposição da revisão (que vive no PostgreSQL) —
    aqui o cubo recebe o estado consolidado pela API, porque o Iceberg não
    guarda o estado mutável.
    """
    return """
    SELECT
        d.deterministic_verdict                       AS verdict,
        d.review_status,
        d._source_connection_name                     AS source_connection,
        COUNT(*)                                      AS decisions,
        ROUND(SUM(COALESCE(d.saving_monthly, 0)), 2)  AS saving_total,
        COUNT(*) FILTER (WHERE d.deterministic_verdict = 'VALID') AS valid
    FROM iceberg.silver.rightsizing_decision d
    GROUP BY d.deterministic_verdict, d.review_status, d._source_connection_name
    """


def _sql_aks() -> str:
    """Alocação de AKS nos dois níveis.

    Os níveis **não somam entre si**: node_pool cobre os 46 clusters e namespace
    cobre 2. O cubo os mantém separados para o drilldown, e o aceite "filtros por
    namespace não duplicam custos" depende de nunca somar níveis diferentes.
    """
    return """
    SELECT
        a.allocation_level,
        a.parent_resource_id,
        a.allocation_key,
        a.namespace,
        a.allocation_weight,
        a.weight_basis,
        a._source_connection_name                     AS source_connection,
        a._billing_period
    FROM iceberg.silver.resource_allocation a
    """


SQLS = {
    "cost_by_resource": _sql_custo_por_recurso,
    "opportunity_summary": _sql_oportunidade,
    "decision_status": _sql_decisao,
    "aks_consumption": _sql_aks,
}

# Partição por período, onde o cubo tem período.
PARTICIONADOS = {"cost_by_resource", "aks_consumption"}


async def build_cube(nome: str) -> dict[str, Any]:
    """Constrói um cubo Gold por staging + swap."""
    from app.services import iceberg_service as iceberg
    from app.services import lakehouse_layers as layers

    if nome not in SQLS:
        raise ValueError(f"Cubo '{nome}' não é um dos {sorted(SQLS)}")

    sql_fonte = SQLS[nome]()
    staging = f"{nome}_stg_{time.time_ns()}"
    particao = ", '_billing_period'" if nome in PARTICIONADOS else ""
    sql = (
        f"CREATE TABLE iceberg.gold.{staging} "
        f"WITH (partitioning = ARRAY[{particao}]) AS {sql_fonte}"
    )
    await trino_execute(sql, timeout_s=3600)

    contagem = await trino_execute(
        f"SELECT COUNT(*) AS n FROM iceberg.gold.{staging}", timeout_s=600
    )
    linhas = int((contagem[0] if contagem else {}).get("n") or 0)
    if not linhas:
        iceberg.cleanup_staged_table(staging, namespace="gold")
        return {"cube": nome, "rows": 0, "message": "Fonte sem dados"}

    iceberg.swap_staged_table(nome, staging, namespace="gold", known_row_count=linhas)
    return {"cube": nome, "rows": linhas}


async def build_all_cubes() -> dict[str, Any]:
    """Constrói os quatro cubos; falha de um não impede os outros.

    Um cubo sem fonte (por exemplo, sem candidatos ainda) fica vazio e é
    reportado — não derruba os demais.
    """
    resultado: dict[str, Any] = {}
    for nome in SQLS:
        try:
            resultado[nome] = await build_cube(nome)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cubo %s falhou: %s", nome, exc)
            resultado[nome] = {"cube": nome, "rows": 0, "error": str(exc)[:200]}
    return resultado
