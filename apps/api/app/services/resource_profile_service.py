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
from app.services.metric_window import MetricDayWindow, resolve_metric_day_window

logger = logging.getLogger(__name__)

PROFILE_TABLE = "resource_profile_daily_window"
PROFILE_SOURCE_CONNECTION_ID = 1
PROFILE_SERIES_OWNER = "monitor_vm_metrics"
MONTHLY_COST_PRORATION_RULE = "MONTHLY_COST_PRORATED_BY_CALENDAR_OVERLAP; monthly_run_rate=window_cost/requested_days*30.4375"

# Confiança por cobertura de amostras. O limiar é política explícita, não
# constante escondida: 90% de cobertura é ALTA, 60% é MÉDIA, abaixo é BAIXA.
CONFIDENCE_HIGH = 0.90
CONFIDENCE_MEDIUM = 0.60

# Quantas amostras um dia completo tem para uma métrica de 15 minutos.
SAMPLES_PER_DAY = 96

PROFILE_SQL = """
WITH janela AS (
    SELECT DATE '{from_date}' AS window_start,
           DATE '{to_date}' AS window_end,
           CAST({requested_days} AS DOUBLE) AS requested_days
), amostras AS (
    -- Só amostra com valor conta para percentil; GAP é ausência declarada.
    SELECT
        resource_id,
        resource_type,
        metric,
        try_cast(m.value AS DOUBLE) AS value,
        bucket_start_utc,
        _billing_period
    FROM iceberg.silver.resource_metric_series m
    CROSS JOIN janela j
    WHERE m._source_connection_id = {source_connection_id}
      AND m._silver_owner = '{series_owner}'
      AND m._billing_period BETWEEN '{from_day}' AND '{to_day}'
      AND m.bucket_start_utc >= CAST(j.window_start AS TIMESTAMP)
      AND m.bucket_start_utc < CAST(date_add('day', 1, j.window_end) AS TIMESTAMP)
      AND m.quality_status = 'OK'
      AND m.value IS NOT NULL
),
por_metrica AS (
    SELECT
        resource_id,
        MAX(resource_type)                                   AS resource_type,
        metric,
        MAX(substr(_billing_period, 1, 6))                   AS billing_period,
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
custo_mensal AS (
    -- Custo FOCUS é mensal. Mantém o mês como dimensão e não iguala YYYYMM
    -- a YYYYMMDD nem atribui o mês inteiro a uma janela parcial.
    SELECT
        resource_id,
        _billing_period,
        SUM(try_cast(effective_cost AS DOUBLE)) AS month_cost,
        MAX(try_cast(billed_cost AS DOUBLE)) AS billed_cost,
        MAX(try_cast(consumed_quantity AS DOUBLE)) AS consumed_quantity,
        MAX(pricing_currency) AS pricing_currency
    FROM iceberg.silver.resource_cost
    WHERE length(_billing_period) = 6
      AND _billing_period BETWEEN substr('{from_day}', 1, 6) AND substr('{to_day}', 1, 6)
    GROUP BY resource_id, _billing_period
), custo_alocado AS (
    SELECT c.resource_id,
           SUM(c.month_cost * date_diff('day', GREATEST(month_start, j.window_start),
               LEAST(date_add('month', 1, month_start), date_add('day', 1, j.window_end)))
               / NULLIF(date_diff('day', month_start, date_add('month', 1, month_start)), 0)) AS cost_window_amount,
           SUM(c.month_cost / NULLIF(date_diff('day', month_start, date_add('month', 1, month_start)), 0)
               * date_diff('day', GREATEST(month_start, j.window_start),
                 LEAST(date_add('month', 1, month_start), date_add('day', 1, j.window_end))))
               * 30.4375 / NULLIF(j.requested_days, 0) AS effective_cost,
           MAX(c._billing_period) AS billing_period,
           SUM(c.billed_cost * date_diff('day', GREATEST(month_start, j.window_start),
               LEAST(date_add('month', 1, month_start), date_add('day', 1, j.window_end)))
               / NULLIF(date_diff('day', month_start, date_add('month', 1, month_start)), 0)) AS billed_cost,
           SUM(c.consumed_quantity * date_diff('day', GREATEST(month_start, j.window_start),
               LEAST(date_add('month', 1, month_start), date_add('day', 1, j.window_end)))
               / NULLIF(date_diff('day', month_start, date_add('month', 1, month_start)), 0)) AS consumed_quantity,
           MAX(c.pricing_currency) AS pricing_currency
    FROM (
        SELECT c.*, CAST(date_parse(concat(substr(c._billing_period, 1, 4), '-', substr(c._billing_period, 5, 2), '-01'), '%Y-%m-%d') AS DATE) AS month_start
        FROM custo_mensal c
    ) c
    CROSS JOIN janela j
    GROUP BY c.resource_id, j.requested_days
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
    c.billing_period,
    '{from_day}' AS window_from_day,
    '{to_day}' AS window_to_day,
    CAST({requested_days} AS BIGINT)          AS requested_days,
    e.base_days                               AS observed_days,
    m.samples,
    m.value_min, m.p50, m.p95, m.p99, m.value_max, m.value_avg,
    -- Cobertura inclui os dias ainda não carregados; ausência reduz confiança.
    CAST(m.samples AS DOUBLE)
      / NULLIF({requested_days} * {samples_per_day}, 0)      AS coverage,
    CASE
      WHEN e.base_days IS NULL OR e.base_days = 0 THEN 'BAIXA'
      WHEN CAST(m.samples AS DOUBLE) / NULLIF({requested_days} * {samples_per_day}, 0) >= {conf_high}
        THEN 'ALTA'
      WHEN CAST(m.samples AS DOUBLE) / NULLIF({requested_days} * {samples_per_day}, 0) >= {conf_med}
        THEN 'MEDIA'
      ELSE 'BAIXA'
    END                                                     AS confidence,
    c.cost_window_amount, c.effective_cost,
    '{cost_proration_rule}' AS cost_proration_rule,
    CAST(c.billed_cost AS VARCHAR) AS billed_cost,
    CAST(c.consumed_quantity AS VARCHAR) AS consumed_quantity,
    c.pricing_currency,
    CAST({source_connection_id} AS BIGINT) AS series_source_connection_id,
    '{series_owner}' AS series_owner
FROM por_metrica m
LEFT JOIN esperado e   ON e.resource_id = m.resource_id
LEFT JOIN custo_alocado c ON c.resource_id = m.resource_id
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


async def build_profile_window(
    *, requested_days: int = 30, from_day: str | None = None, to_day: str | None = None,
) -> dict[str, Any]:
    """Materializa perfil diário 4P sem alterar o agregado legado mensal.

    O custo mensal é alocado aos dias de calendário que intersectam a janela.
    ``effective_cost`` é um run rate mensal calculado a partir desse rateio; a
    proveniência e as datas são gravadas explicitamente na nova tabela.
    """
    from app.services import iceberg_service as iceberg

    dias = max(1, min(int(requested_days or 30), 90))
    if (from_day is None) != (to_day is None):
        raise ValueError("from_day e to_day devem ser informados juntos")
    if from_day is not None:
        window = resolve_metric_day_window(from_day, to_day, max_days=90)
        if window.requested_days != dias:
            dias = window.requested_days
    else:
        window = resolve_metric_day_window(default_days=dias)
    sql = PROFILE_SQL.format(
        requested_days=dias,
        from_date=window.from_date.isoformat(),
        to_date=window.to_date.isoformat(),
        from_day=window.from_day,
        to_day=window.to_day,
        source_connection_id=PROFILE_SOURCE_CONNECTION_ID,
        series_owner=PROFILE_SERIES_OWNER,
        cost_proration_rule=MONTHLY_COST_PRORATION_RULE,
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
            window.from_day, window.to_day, PROFILE_SOURCE_CONNECTION_ID, PROFILE_SERIES_OWNER,
            r.get("observed_days"), r.get("samples"), r.get("p50"),
            r.get("p95"), r.get("value_max"), r.get("cost_window_amount"), r.get("effective_cost"),
        )
        for r in linhas
    )
    perfil_hash = _hash_perfil(assinatura)
    carimbo = datetime.now(timezone.utc).isoformat()

    periodo = window.to_day

    import pandas as pd

    frame = pd.DataFrame(linhas)
    # Ordem = ordem declarada no modelo: o writer anexa _billing_period/_processed_at,
    # entao a ordem do frame determina o schema fisico.
    from app.services.silver_models import model as _modelo

    declaradas = [c for c in _modelo(PROFILE_TABLE).column_names() if c in frame.columns]
    extras = [c for c in frame.columns if c not in declaradas]
    frame = frame[declaradas + extras]
    frame["requested_days"] = dias
    frame["profile_hash"] = perfil_hash
    frame["profile_built_at"] = carimbo
    frame["_billing_period"] = periodo

    from app.services import lakehouse_layers as layers

    catalogo = iceberg._get_catalog()
    try:
        catalogo.load_table((layers.SILVER, PROFILE_TABLE)).delete(
            f"_billing_period = '{periodo}' AND series_source_connection_id = {PROFILE_SOURCE_CONNECTION_ID} "
            f"AND series_owner = '{PROFILE_SERIES_OWNER}'"
        )
    except Exception:  # noqa: BLE001 - nada a remover na primeira execução
        pass

    written = iceberg.write_silver(
        connection_code="profile_window_4p_daily",
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
        "window": window.as_dict(),
        "cost_proration_rule": MONTHLY_COST_PRORATION_RULE,
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
