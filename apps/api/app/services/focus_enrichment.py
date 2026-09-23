"""FOCUS enriquecida: liga a linha de custo ao recurso.

A FOCUS é a espinha do custo e continua sendo. O que faltava era a **ligação**:
os modelos canônicos (`resource_inventory`, `resource_metric_series`,
`price_catalog`) existiam ao lado dela, sem chave em comum — o join dava zero
porque a FOCUS grafa `resourceGroups` e o inventário `resourcegroups`.

Este serviço resolve isso em duas partes:

1. **Identidade normalizada.** O `resourceid` da FOCUS é reescrito em minúsculas,
   que é a forma canônica dos IDs de nuvem. Com isso o join passa a casar.
2. **Enriquecimento sem fan-out.** SKU, estado, SO e agregados de métrica são
   trazidos **por agregação** (uma linha de inventário e uma de métrica por
   recurso), nunca por explosão de linhas. O plano exige: "associar custo FOCUS a
   VM/ambiente/período **sem multiplicar linhas**".

A materialização usa staging + swap, como a Gold: 36 M linhas não passam por
pandas.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from app.services.trino_service import execute as trino_execute

logger = logging.getLogger(__name__)

TABLE = "focus_enriched"
SOURCE = "dest_modelo_focus"

# Colunas de custo preservadas da FOCUS, sem reinterpretação.
COST_COLUMNS: dict[str, str] = {
    "effectivecost": "effective_cost",
    "billedcost": "billed_cost",
    "listcost": "list_cost",
    "consumedquantity": "consumed_quantity",
    "pricingcurrency": "pricing_currency",
}


def _select_costs(columns: dict[str, str]) -> str:
    """Projeta as medidas de custo que existem na origem."""
    partes = []
    for origem, destino in COST_COLUMNS.items():
        if origem in columns:
            partes.append(f"TRY_CAST(f.{origem} AS DOUBLE) AS {destino}")
        else:
            partes.append(f"CAST(NULL AS DOUBLE) AS {destino}")
    return ", ".join(partes)


ENRICH_SQL = """
WITH inventario AS (
    -- Uma linha por recurso: o inventário tem mais de uma fonte (Resource Graph,
    -- coletor de VM, AKS), então a agregação evita fan-out na FOCUS.
    SELECT
        resource_id,
        MAX(resource_name)      AS resource_name,
        MAX(resource_type)      AS resource_type,
        MAX(resource_group_id)  AS resource_group_id,
        MAX(region_id)          AS region_id,
        MAX(resource_sku)       AS resource_sku,
        MAX(resource_state)     AS resource_state,
        MAX(operating_system)   AS operating_system
    FROM iceberg.silver.resource_inventory
    GROUP BY resource_id
),
metricas AS (
    -- Uma linha por recurso e período. `metric_avg_cpu` usa a métrica de CPU
    -- quando ela existe; a contagem de amostras diz se há série.
    SELECT
        resource_id,
        _billing_period,
        COUNT(*) AS metric_samples,
        AVG(CASE WHEN metric = 'Percentage CPU' THEN value END) AS metric_avg_cpu
    FROM iceberg.silver.resource_metric_series
    WHERE quality_status <> 'GAP'
    GROUP BY resource_id, _billing_period
)
SELECT
    'azure' AS cloud_provider,
    lower(CAST(f.resourceid AS VARCHAR)) AS resource_id,
    COALESCE(i.resource_name, CAST(f.resourcename AS VARCHAR)) AS resource_name,
    COALESCE(i.resource_type, CAST(f.resourcetype AS VARCHAR)) AS resource_type,
    lower(CAST(f.subaccountid AS VARCHAR)) AS sub_account_id,
    COALESCE(i.resource_group_id, CAST(f.source_resource_group_name AS VARCHAR)) AS resource_group_id,
    COALESCE(i.region_id, lower(CAST(f.regionid AS VARCHAR))) AS region_id,
    CAST(f.servicename AS VARCHAR) AS service_name,
    CAST(f.servicecategory AS VARCHAR) AS service_category,
    CAST(f.chargecategory AS VARCHAR) AS charge_category,
    CAST(f._billing_period AS VARCHAR) AS billing_period,
    i.resource_sku,
    i.resource_state,
    i.operating_system,
    CASE WHEN i.resource_id IS NULL THEN 'NAO' ELSE 'SIM' END AS has_inventory,
    SUM(TRY_CAST(f.effectivecost AS DOUBLE)) AS effective_cost,
    SUM(TRY_CAST(f.billedcost AS DOUBLE)) AS billed_cost,
    SUM(TRY_CAST(f.listcost AS DOUBLE)) AS list_cost,
    SUM(TRY_CAST(f.consumedquantity AS DOUBLE)) AS consumed_quantity,
    MAX(CAST(f.pricingcurrency AS VARCHAR)) AS pricing_currency,
    MAX(COALESCE(m.metric_samples, 0)) AS metric_samples,
    MAX(m.metric_avg_cpu) AS metric_avg_cpu,
    CAST(f._billing_period AS VARCHAR) AS _billing_period
FROM iceberg.silver.{source} f
LEFT JOIN inventario i ON i.resource_id = lower(CAST(f.resourceid AS VARCHAR))
LEFT JOIN metricas m
       ON m.resource_id = lower(CAST(f.resourceid AS VARCHAR))
      AND m._billing_period = CAST(f._billing_period AS VARCHAR)
GROUP BY
    lower(CAST(f.resourceid AS VARCHAR)),
    COALESCE(i.resource_name, CAST(f.resourcename AS VARCHAR)),
    COALESCE(i.resource_type, CAST(f.resourcetype AS VARCHAR)),
    lower(CAST(f.subaccountid AS VARCHAR)),
    COALESCE(i.resource_group_id, CAST(f.source_resource_group_name AS VARCHAR)),
    COALESCE(i.region_id, lower(CAST(f.regionid AS VARCHAR))),
    CAST(f.servicename AS VARCHAR),
    CAST(f.servicecategory AS VARCHAR),
    CAST(f.chargecategory AS VARCHAR),
    CAST(f._billing_period AS VARCHAR),
    i.resource_sku, i.resource_state, i.operating_system,
    CASE WHEN i.resource_id IS NULL THEN 'NAO' ELSE 'SIM' END
"""


async def _focus_columns() -> list[str]:
    from app.services.forecast_service import fetch_metadata

    meta = await fetch_metadata(SOURCE, "silver") or {}
    # `fetch_metadata` devolve um dict com a lista em `columns`.
    colunas = meta.get("columns") or []
    return [str(c.get("name") or "").lower() for c in colunas if isinstance(c, dict)]


async def build_focus_enriched(*, task_id: int | None = None) -> dict[str, Any]:
    """Constrói a FOCUS enriquecida por staging + swap."""
    from app.services import iceberg_service as iceberg

    colunas = await _focus_columns()
    if not colunas:
        raise RuntimeError(f"Metadados de iceberg.silver.{SOURCE} indisponíveis")

    staging = f"{TABLE}_stg_{time.time_ns()}"
    sql = (
        f"CREATE TABLE iceberg.silver.{staging} "
        f"WITH (partitioning = ARRAY['_billing_period']) AS "
        + ENRICH_SQL.format(source=SOURCE)
    )
    logger.info("Construindo %s (staging %s)", TABLE, staging)
    await trino_execute(sql, timeout_s=7200)

    contagem = await trino_execute(f"SELECT COUNT(*) AS n FROM iceberg.silver.{staging}", timeout_s=600)
    linhas = int((contagem[0] if contagem else {}).get("n") or 0)
    if not linhas:
        iceberg.cleanup_staged_table(staging, namespace="silver")
        raise RuntimeError("Enriquecimento não produziu linhas")

    # A FOCUS não tem chave única (`source_record_id` é nulo em 97,9% das linhas),
    # então o enriquecimento consolida no grão natural de custo. O que precisa
    # fechar é o **total de custo**, não a contagem de linhas — é o aceite do P3:
    # "associar custo FOCUS sem multiplicar linhas".
    origem = await trino_execute(
        f"SELECT COUNT(*) AS n, SUM(TRY_CAST(effectivecost AS DOUBLE)) AS custo "
        f"FROM iceberg.silver.{SOURCE}",
        timeout_s=900,
    )
    novo = await trino_execute(
        f"SELECT SUM(effective_cost) AS custo FROM iceberg.silver.{staging}",
        timeout_s=600,
    )
    linhas_origem = int((origem[0] if origem else {}).get("n") or 0)
    custo_origem = float((origem[0] if origem else {}).get("custo") or 0)
    custo_novo = float((novo[0] if novo else {}).get("custo") or 0)
    # Tolerância de 0,1%: arredondamento de ponto flutuante em 36 M linhas.
    if custo_origem and abs(custo_novo - custo_origem) / abs(custo_origem) > 0.001:
        iceberg.cleanup_staged_table(staging, namespace="silver")
        raise RuntimeError(
            f"Enriquecimento alterou o custo: {custo_novo:.2f} contra {custo_origem:.2f} da FOCUS"
        )

    iceberg.swap_staged_table(TABLE, staging, namespace="silver", known_row_count=linhas)
    return {
        "table": TABLE,
        "rows": linhas,
        "source_rows": linhas_origem,
        "effective_cost": round(custo_novo, 2),
        "source_effective_cost": round(custo_origem, 2),
    }
