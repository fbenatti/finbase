from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select
from app.models.pivot_view import PivotView
from app.config import settings


async def seed(db: AsyncSession):
    """Configuração genérica de cada startup; conexões e regras vêm do cadastro."""
    if settings.skip_seed:
        return

    # Seeded rules are reconciled on every startup. These last-priority rules
    # assign only rows left without an organizational classification.
    from app.services.classification_fallbacks import ensure_cloud_classification_fallbacks
    await ensure_cloud_classification_fallbacks(db)

    await db.commit()
    await _seed_pivot_views(db)


async def _seed_pivot_views(db: AsyncSession):
    """Shared FinOps templates. They are config only, never financial rows."""
    focus = (await db.execute(text("SELECT id FROM destination_table WHERE upper(name) = 'BILLING_CLOUD' LIMIT 1"))).scalar()
    if not focus:
        return
    templates = [
        ("Hierarquia / Origem por mês", ["hierarquia_1", "cloud_origem"], ["_billing_period"]),
        ("Serviço por mês", ["ServiceName"], ["_billing_period"]),
        ("Região por mês", ["RegionName"], ["_billing_period"]),
        ("Subconta por mês", ["SubAccountName"], ["_billing_period"]),
        ("Custo por provider", ["cloud_origem"], []),
    ]
    for name, rows, columns in templates:
        exists = (await db.execute(select(PivotView.id).where(PivotView.destination_table_id == focus, PivotView.name == name))).scalar()
        if not exists:
            db.add(PivotView(name=name, destination_table_id=focus, config_json={
                "destination_table_id": focus, "rows": rows, "columns": columns,
                "measures": [{"column": "EffectiveCost", "aggregation": "SUM", "label": None}],
                "filters": [], "row_limit": 500, "column_limit": 60, "include_totals": True,
            }))
    await db.commit()
