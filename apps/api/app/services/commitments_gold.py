"""Gold cube for the RI/SavingPlan executive dashboard.

This cube is deliberately pre-aggregated by month, billing source, commitment
type and project. The dashboard API must not scan FOCUS detail or planning
Silver tables at request time.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.gold_mart_rule import GoldMartRule

COMMITMENTS_GOLD_CODE = "commitments_dashboard"
COMMITMENTS_GOLD_TABLE = "dest_dm_cost_commitments_dashboard"

COMMITMENTS_GOLD_SQL = """
WITH synthetic_returns AS (
    SELECT source_connection_id, _billing_period, clone_source_record_id,
           clone_rule_id, SUM(realizado_cloud) AS return_realized
    FROM iceberg.silver.dest_modelo_focus
    WHERE is_synthetic = TRUE AND clone_rule_id IN (1, 2)
    GROUP BY 1, 2, 3, 4
), baseline_by_project AS (
    SELECT CAST(f._billing_period AS VARCHAR) AS period,
           LOWER(f.cloud_origem) AS source,
           CASE WHEN LOWER(f.commitmentdiscounttype) = 'reservation'
                THEN 'RI' ELSE 'SavingPlan' END AS commitment_type,
           COALESCE(NULLIF(TRIM(f.organograma), ''),
                    NULLIF(TRIM(f.source_resource_group_name), ''),
                    'Sem projeto') AS project,
           SUM(-r.return_realized) AS ondemand_baseline
    FROM iceberg.silver.dest_modelo_focus f
    INNER JOIN synthetic_returns r
      ON f.source_connection_id = r.source_connection_id
     AND f._billing_period = r._billing_period
     AND f.source_record_id = r.clone_source_record_id
     AND r.clone_rule_id = CASE
          WHEN LOWER(f.commitmentdiscounttype) = 'reservation' THEN 1 ELSE 2 END
    WHERE f.chargecategory = 'Usage'
      AND COALESCE(f.is_synthetic, FALSE) = FALSE
      AND LOWER(f.commitmentdiscounttype) IN ('reservation', 'savingsplan')
    GROUP BY 1, 2, 3, 4
), baseline_totals AS (
    SELECT period, source, commitment_type,
           SUM(ondemand_baseline) AS ondemand_baseline
    FROM baseline_by_project
    GROUP BY 1, 2, 3
), purchases AS (
    SELECT CAST(_billing_period AS VARCHAR) AS period,
           LOWER(cloud_origem) AS source,
           CASE WHEN LOWER(commitmentdiscounttype) = 'reservation'
                THEN 'RI' ELSE 'SavingPlan' END AS commitment_type,
           SUM(realizado_cloud) AS purchase
    FROM iceberg.silver.dest_modelo_focus
    WHERE chargecategory = 'Purchase'
      AND LOWER(commitmentdiscounttype) IN ('reservation', 'savingsplan')
    GROUP BY 1, 2, 3
), actual_totals AS (
    SELECT COALESCE(b.period, p.period) AS period,
           COALESCE(b.source, p.source) AS source,
           COALESCE(b.commitment_type, p.commitment_type) AS commitment_type,
           COALESCE(b.ondemand_baseline, 0) AS ondemand_baseline,
           COALESCE(p.purchase, 0) AS purchase
    FROM baseline_totals b
    FULL OUTER JOIN purchases p
      ON b.period = p.period AND b.source = p.source
     AND b.commitment_type = p.commitment_type
), actual_project_rows AS (
    SELECT b.period, b.source, b.commitment_type, b.project,
           'ACTUAL_PROJECT' AS scope,
           b.ondemand_baseline,
           COALESCE(t.purchase * b.ondemand_baseline /
                    NULLIF(t.ondemand_baseline, 0), 0) AS purchase,
           b.ondemand_baseline - COALESCE(t.purchase * b.ondemand_baseline /
                    NULLIF(t.ondemand_baseline, 0), 0) AS saving,
           0.0 AS forecast_purchase, 0.0 AS forecast_return,
           0.0 AS forecast_saving
    FROM baseline_by_project b
    LEFT JOIN actual_totals t
      ON b.period = t.period AND b.source = t.source
     AND b.commitment_type = t.commitment_type
), actual_total_rows AS (
    SELECT period, source, commitment_type, '__TOTAL__' AS project,
           'ACTUAL_TOTAL' AS scope, ondemand_baseline, purchase,
           ondemand_baseline - purchase AS saving,
           0.0 AS forecast_purchase, 0.0 AS forecast_return,
           0.0 AS forecast_saving
    FROM actual_totals
), forecast_by_project AS (
    SELECT CAST(period AS VARCHAR) AS period,
           LOWER(cloud_origem) AS source,
           CASE WHEN LOWER(commitmentdiscounttype) = 'reservation'
                THEN 'RI' ELSE 'SavingPlan' END AS commitment_type,
           COALESCE(NULLIF(TRIM(organograma_nivel_7), ''),
                    NULLIF(TRIM(organograma_nivel_6), ''),
                    NULLIF(TRIM(organograma_nivel_5), ''),
                    NULLIF(TRIM(organograma_nivel_4), ''),
                    NULLIF(TRIM(organograma_nivel_3), ''),
                    NULLIF(TRIM(organograma_nivel_2), ''),
                    NULLIF(TRIM(organograma_nivel_1), ''),
                    'Sem projeto') AS project,
           SUM(CASE WHEN LOWER(chargecategory) = 'purchase'
                    THEN realizado_cloud ELSE 0 END) AS forecast_purchase,
           SUM(CASE WHEN LOWER(chargecategory) = 'return'
                    THEN realizado_cloud ELSE 0 END) AS forecast_return
    FROM iceberg.silver.dest_modelo_focus_planning
    WHERE tipo = 'forecast'
      AND UPPER(financial_effect) = 'DIRECT_REALIZED'
      AND LOWER(commitmentdiscounttype) IN ('reservation', 'savingsplan')
      AND LOWER(chargecategory) IN ('purchase', 'return')
    GROUP BY 1, 2, 3, 4
), forecast_project_rows AS (
    SELECT period, source, commitment_type, project,
           'FORECAST_PROJECT' AS scope, 0.0 AS ondemand_baseline,
           0.0 AS purchase, 0.0 AS saving,
           forecast_purchase, forecast_return,
           -forecast_return - forecast_purchase AS forecast_saving
    FROM forecast_by_project
), forecast_totals AS (
    SELECT period, source, commitment_type,
           SUM(forecast_purchase) AS forecast_purchase,
           SUM(forecast_return) AS forecast_return
    FROM forecast_by_project
    GROUP BY 1, 2, 3
), forecast_total_rows AS (
    SELECT period, source, commitment_type, '__TOTAL__' AS project,
           'FORECAST_TOTAL' AS scope, 0.0 AS ondemand_baseline,
           0.0 AS purchase, 0.0 AS saving,
           forecast_purchase, forecast_return,
           -forecast_return - forecast_purchase AS forecast_saving
    FROM forecast_totals
)
SELECT * FROM actual_project_rows
UNION ALL SELECT * FROM actual_total_rows
UNION ALL SELECT * FROM forecast_project_rows
UNION ALL SELECT * FROM forecast_total_rows
""".strip()


async def ensure_commitments_gold_rule(db: AsyncSession) -> GoldMartRule:
    """Idempotently register/update the dashboard's governed Gold materializer."""
    result = await db.execute(
        select(GoldMartRule).where(GoldMartRule.code == COMMITMENTS_GOLD_CODE)
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        rule = GoldMartRule(
            code=COMMITMENTS_GOLD_CODE,
            name="Compras de RI e SavingPlan — painel executivo",
            description=(
                "Cubo mensal por origem, compromisso e projeto; materializa "
                "realizado, OnDemand sintético, forecast e saving para o dashboard."
            ),
            active=True,
            dimensions=[],
            measure_columns=[],
            sql_template=COMMITMENTS_GOLD_SQL,
            materialization_table=COMMITMENTS_GOLD_TABLE,
            materialization_status="PENDING",
        )
        db.add(rule)
    elif rule.sql_template != COMMITMENTS_GOLD_SQL:
        rule.sql_template = COMMITMENTS_GOLD_SQL
        rule.name = "Compras de RI e SavingPlan — painel executivo"
        rule.description = (
            "Cubo mensal por origem, compromisso e projeto; materializa "
            "realizado, OnDemand sintético, forecast e saving para o dashboard."
        )
        rule.active = True
        rule.materialization_table = COMMITMENTS_GOLD_TABLE
        rule.materialization_status = "PENDING"
        rule.materialization_error = None
    await db.flush()
    return rule
