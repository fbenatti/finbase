"""P6 — Perfil da janela disponível.

O rightsizing (P8) precisa responder "esta VM está ociosa?" com número, não com
impressão. Este serviço calcula, por recurso e por janela:

- **cobertura**: quantas amostras existem contra quantas seriam esperadas. Lacuna
  não é zero — reduz a confiança da conclusão.
- **percentis** (p50/p95/p99) e picos de cada métrica, calculados sobre as
  amostras que existem.
- **dias observados** contra dias pedidos, para o consumidor saber sobre que
  janela a conclusão vale.
- **custo por recurso** no mesmo período, do custo canônico.
- **profile_hash**: o perfil só muda quando as entradas mudam materialmente. Duas
  execuções sobre o mesmo dado produzem o mesmo hash, então uma decisão pode ser
  reproduzida.

O plano pede exatamente isso em P6: "cobertura por métrica, percentis/limiares/
picos, custo por VM, requested_days, observed_days, perfil versionado e
profile_hash" e "lacunas reduzem confiança".
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from app.services.trino_service import execute as trino_execute

logger = logging.getLogger(__name__)

PROFILE_TABLE = "resource_profile_window"

# Confiança por cobertura de amostras. O limiar é política explícita, não
# constante escondida: 90% de cobertura é ALTA, 60% é MÉDIA, abaixo é BAIXA.
CONFIDENCE_HIGH = 0.90
CONFIDENCE_MEDIUM = 0.60

# Quantas amostras um dia completo tem para uma métrica de 15 minutos.
SAMPLES_PER_DAY = 96

PROFILE_SQL = """
WITH amostras AS (
    -- Só amostra com valor conta para percentil; GAP é ausência declarada.
    SELECT
        resource_id,
        resource_type,
        metric,
        value,
        bucket_start_utc,
        _billing_period
    FROM iceberg.silver.resource_metric_series
    WHERE quality_status = 'OK'
      AND value IS NOT NULL
),
por_metrica AS (
    SELECT
        resource_id,
        MAX(resource_type)                                   AS resource_type,
        metric,
        MAX(_billing_period)                                 AS billing_period,
        COUNT(*)                                             AS samples,
        COUNT(DISTINCT CAST(bucket_start_utc AS DATE))       AS observed_days,
        MIN(value)                                           AS value_min,
        approx_percentile(value, 0.50)                       AS p50,
        approx_percentile(value, 0.95)                       AS p95,
        approx_percentile(value, 0.99)                       AS p99,
        MAX(value)                                           AS value_max,
        AVG(value)                                           AS value_avg
    FROM amostras
    GROUP BY resource_id, metric
),
esperado AS (
    -- Dias observados por recurso: a base da cobertura. Usa o maior entre as
    -- métricas porque uma métrica com menos dias não deve encolher a janela das
    -- outras — a lacuna de uma aparece na cobertura dela.
    SELECT
        resource_id,
        MAX(billing_period) AS billing_period,
        MAX(observed_days)  AS base_days
    FROM por_metrica
    GROUP BY resource_id
),
custo AS (
    -- Custo do recurso na mesma janela, do modelo canônico já normalizado.
    SELECT
        resource_id,
        _billing_period,
        SUM(effective_cost) AS effective_cost,
        SUM(billed_cost)    AS billed_cost,
        SUM(consumed_quantity) AS consumed_quantity,
        MAX(pricing_currency)  AS pricing_currency,
        MAX(_source_connection_name) AS source_connection_name
    FROM iceberg.silver.resource_cost
    GROUP BY resource_id, _billing_period
),
inventario AS (
    SELECT
        resource_id,
        MAX(resource_name) AS resource_name,
        MAX(resource_sku)  AS resource_sku,
        MAX(resource_state) AS resource_state,
        MAX(region_id)     AS region_id,
        MAX(sub_account_id) AS sub_account_id
    FROM iceberg.silver.resource_inventory
    GROUP BY resource_id
)
SELECT
    m.resource_id,
    i.resource_name,
    i.resource_sku,
    i.resource_state,
    i.region_id,
    i.sub_account_id,
    m.resource_type,
    m.metric,
    m.billing_period,
    CAST({requested_days} AS BIGINT)          AS requested_days,
    e.base_days                               AS observed_days,
    m.samples,
    m.value_min, m.p50, m.p95, m.p99, m.value_max, m.value_avg,
    -- Cobertura: amostras reais contra as esperadas para os dias observados.
    CAST(m.samples AS DOUBLE)
      / NULLIF(e.base_days * {samples_per_day}, 0)          AS coverage,
    CASE
      WHEN e.base_days IS NULL OR e.base_days = 0 THEN 'BAIXA'
      WHEN CAST(m.samples AS DOUBLE) / NULLIF(e.base_days * {samples_per_day}, 0) >= {conf_high}
        THEN 'ALTA'
      WHEN CAST(m.samples AS DOUBLE) / NULLIF(e.base_days * {samples_per_day}, 0) >= {conf_med}
        THEN 'MEDIA'
      ELSE 'BAIXA'
    END                                                     AS confidence,
    c.effective_cost, c.billed_cost, c.consumed_quantity, c.pricing_currency,
    c.source_connection_name
FROM por_metrica m
LEFT JOIN esperado e   ON e.resource_id = m.resource_id
LEFT JOIN custo c      ON c.resource_id = m.resource_id
                      AND c._billing_period = m.billing_period
LEFT JOIN inventario i ON i.resource_id = m.resource_id
"""


def _hash_perfil(linhas: list[tuple]) -> str:
    """Hash estável das entradas do perfil.

    Ordenado e sem o carimbo de tempo: duas execuções sobre o mesmo dado
    produzem o mesmo hash, então o perfil é reproduzível e a decisão que o usa
    também. O plano exige "perfil não muda sem mudança material de entradas".
    """
    base = "|".join(
        ";".join("" if v is None else str(v) for v in linha) for linha in linhas
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]


async def build_profile_window(*, requested_days: int = 15) -> dict[str, Any]:
    """Calcula o perfil e materializa em `silver.resource_profile_window`."""
    from app.services import iceberg_service as iceberg

    dias = max(1, min(int(requested_days or 15), 90))
    sql = PROFILE_SQL.format(
        requested_days=dias,
        samples_per_day=SAMPLES_PER_DAY,
        conf_high=CONFIDENCE_HIGH,
        conf_med=CONFIDENCE_MEDIUM,
    )
    linhas = await trino_execute(sql, timeout_s=900)
    if not linhas:
        return {"table": PROFILE_TABLE, "rows": 0, "message": "Sem métricas para perfilar"}

    # Hash sobre as entradas materiais de cada recurso/métrica.
    assinatura = sorted(
        (
            r.get("resource_id"), r.get("metric"), r.get("billing_period"),
            r.get("observed_days"), r.get("samples"), r.get("p50"),
            r.get("p95"), r.get("value_max"),
        )
        for r in linhas
    )
    perfil_hash = _hash_perfil(assinatura)
    carimbo = datetime.now(timezone.utc).isoformat()

    periodo = str((linhas[0] or {}).get("billing_period") or "") or datetime.now(
        timezone.utc
    ).strftime("%Y%m")

    import pandas as pd

    frame = pd.DataFrame(linhas)
    frame["requested_days"] = dias
    frame["profile_hash"] = perfil_hash
    frame["profile_built_at"] = carimbo
    frame["_billing_period"] = periodo

    from app.services import lakehouse_layers as layers

    catalogo = iceberg._get_catalog()
    try:
        catalogo.load_table((layers.SILVER, PROFILE_TABLE)).delete(
            f"_billing_period = '{periodo}'"
        )
    except Exception:  # noqa: BLE001 - nada a remover na primeira execução
        pass

    written = iceberg.write_silver(
        connection_code="profile_window",
        df=frame.drop(columns=["_billing_period"]),
        billing_period=periodo,
        table_name_override=f"silver.{PROFILE_TABLE}",
    )

    por_confianca: dict[str, int] = {}
    for linha in linhas:
        chave = str(linha.get("confidence") or "")
        por_confianca[chave] = por_confianca.get(chave, 0) + 1

    return {
        "table": PROFILE_TABLE,
        "rows": int(written or 0),
        "period": periodo,
        "requested_days": dias,
        "profile_hash": perfil_hash,
        "resources": len({r.get("resource_id") for r in linhas}),
        "metrics": len({r.get("metric") for r in linhas}),
        # A confiança é resultado, não adorno: o consumidor decide com ela.
        "confidence": por_confianca,
        "has_gaps": any(
            float(r.get("coverage") or 0) < CONFIDENCE_HIGH for r in linhas
        ),
    }
