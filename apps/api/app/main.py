from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from app.config import settings
from app.database import engine, Base, async_session
from app.routers import connections, csv_upload, etl, rules, periods, analysis, destinations, schema, forecast, clone_rules, planning, savings, azure_tag_policy
from app.seed import seed
from app.services import scheduler_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        from app.models.source_connection import SourceConnection  # noqa: F401
        from app.models.ingested_blob import IngestedBlob  # noqa: F401
        from app.models.rules import EtlMapping, BusinessRule, ClassificationRule, OrganizationMoveAudit  # noqa: F401
        from app.models.period_status import PeriodStatus  # noqa: F401
        from app.models.etl_task import EtlTask  # noqa: F401
        from app.models.destination import DestinationTable, DestinationColumn, SourceDestinationMapping  # noqa: F401
        from app.models.skipped_blob import SkippedBlob  # noqa: F401
        from app.models.scheduled_task import ScheduledTask  # noqa: F401
        from app.models.pivot_view import PivotView  # noqa: F401
        from app.models.dashboard import Dashboard, DashboardWidget  # noqa: F401
        from app.models.forecast import ForecastConfig, ForecastAdjustment, ForecastBusinessRule, ForecastRun  # noqa: F401
        from app.models.planning import FinancialType, PlanningVersion, PlanningEntry  # noqa: F401
        from app.models.clone_rule import CloneRule, ClonePricePendencies  # noqa: F401
        from app.models.saving import SavingAction, SavingMeasurement  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
        # create_all does not evolve PostgreSQL tables.  Keep this small,
        # idempotent migration next to startup until Alembic is introduced.
        # asyncpg executes prepared statements and rejects multi-command SQL,
        # so each migration operation must be sent independently.
        for statement in (
            "ALTER TABLE forecast_config ADD COLUMN IF NOT EXISTS period_allocation VARCHAR(30) NOT NULL DEFAULT 'none'",
            "ALTER TABLE forecast_config ADD COLUMN IF NOT EXISTS charge_end_column VARCHAR(200)",
            "ALTER TABLE forecast_config ADD COLUMN IF NOT EXISTS connection_allocation_rules JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE forecast_run ADD COLUMN IF NOT EXISTS config_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb",
            "ALTER TABLE forecast_run ADD COLUMN IF NOT EXISTS destination_table_id_snapshot INTEGER",
            "ALTER TABLE forecast_run ADD COLUMN IF NOT EXISTS destination_physical_name VARCHAR(200)",
            "ALTER TABLE forecast_run ADD COLUMN IF NOT EXISTS source_snapshot_id VARCHAR(100)",
            "ALTER TABLE forecast_run ADD COLUMN IF NOT EXISTS classification_version_id INTEGER",
            "ALTER TABLE forecast_run ALTER COLUMN forecast_config_id DROP NOT NULL",
            "ALTER TABLE forecast_business_rule ADD COLUMN IF NOT EXISTS identification_clauses JSONB NOT NULL DEFAULT '[]'::jsonb",
            'ALTER TABLE public.dest_modelo_focus ADD COLUMN IF NOT EXISTS "MarketplaceProductName" TEXT',
            'ALTER TABLE public.dest_modelo_focus ADD COLUMN IF NOT EXISTS "MarketplaceOfferUnit" TEXT',
            'ALTER TABLE public.dest_modelo_focus ADD COLUMN IF NOT EXISTS "MarketplaceBillingModel" TEXT',
        ):
            await conn.execute(text(statement))
        await conn.execute(text("""
            DO $$ BEGIN
              IF to_regclass('fact_cost') IS NOT NULL THEN
                ALTER TABLE fact_cost ADD COLUMN IF NOT EXISTS departamento_nivel_6 VARCHAR(200);
                ALTER TABLE fact_cost ADD COLUMN IF NOT EXISTS departamento_nivel_7 VARCHAR(200);
              END IF;
            END $$
        """))
        await conn.execute(text("""
            DO $$ DECLARE fk_name text; BEGIN
              SELECT conname INTO fk_name FROM pg_constraint
              WHERE conrelid = 'forecast_run'::regclass AND contype = 'f'
                AND confrelid = 'forecast_config'::regclass LIMIT 1;
              IF fk_name IS NOT NULL THEN
                EXECUTE format('ALTER TABLE forecast_run DROP CONSTRAINT %I', fk_name);
              END IF;
              IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'forecast_run'::regclass AND conname = 'forecast_run_forecast_config_id_fkey') THEN
                ALTER TABLE forecast_run ADD CONSTRAINT forecast_run_forecast_config_id_fkey
                FOREIGN KEY (forecast_config_id) REFERENCES forecast_config(id) ON DELETE SET NULL;
              END IF;
            END $$
        """))
        # Populate frozen execution contracts for historical runs whose
        # configurations still exist.  Their legacy result table remains
        # queryable; runs without a result are intentionally left unchanged.
        await conn.execute(text("""
            UPDATE forecast_run run
            SET destination_table_id_snapshot = cfg.destination_table_id,
                destination_physical_name = dest.physical_name,
                config_snapshot = jsonb_build_object(
                  'config_id', cfg.id, 'config_version', cfg.version,
                  'destination_table_id', cfg.destination_table_id,
                  'destination_physical_name', dest.physical_name,
                  'measure_column', cfg.measure_column, 'date_column', cfg.date_column,
                  'period_column', cfg.period_column, 'type_column', cfg.type_column,
                  'dimensions', COALESCE(cfg.dimensions::jsonb, '[]'::jsonb),
                  'currency_column', cfg.currency_column, 'cutoff_days', cfg.cutoff_days,
                  'horizon_months', cfg.horizon_months)
            FROM forecast_config cfg JOIN destination_table dest ON dest.id = cfg.destination_table_id
            WHERE run.forecast_config_id = cfg.id AND (run.config_snapshot::jsonb = '{}'::jsonb OR run.config_snapshot IS NULL);
        """))
        for statement in (
            "ALTER TABLE business_rule ADD COLUMN IF NOT EXISTS cloud_scope VARCHAR(50)",
            "ALTER TABLE classification_rule ADD COLUMN IF NOT EXISTS cloud_scope VARCHAR(50)",
            "CREATE INDEX IF NOT EXISTS ix_business_rule_cloud_scope ON business_rule (cloud_scope)",
            "CREATE INDEX IF NOT EXISTS ix_classification_rule_cloud_scope ON classification_rule (cloud_scope)",
            "ALTER TABLE source_connection ADD COLUMN IF NOT EXISTS cloud_scope VARCHAR(50)",
            "CREATE INDEX IF NOT EXISTS ix_source_connection_cloud_scope ON source_connection (cloud_scope)",
        ):
            await conn.execute(text(statement))
        # Backfill the canonical provider slug for legacy rows that only have a
        # free-text cloud_name, so rule matching keeps working without a
        # re-import. Unknown/free-text names are left NULL (match only the
        # generic unscoped rules).
        await conn.execute(text("""
            UPDATE source_connection
            SET cloud_scope = CASE
                WHEN lower(cloud_name) LIKE 'azure%' THEN 'azure'
                WHEN lower(cloud_name) LIKE 'gcp%' THEN 'gcp'
                WHEN lower(cloud_name) LIKE 'ibm%' THEN 'ibm'
                WHEN lower(cloud_name) LIKE 'aws%' THEN 'aws'
                WHEN lower(cloud_name) LIKE 'oci%' OR lower(cloud_name) LIKE 'oracle%' THEN 'oci'
                WHEN lower(cloud_name) LIKE '%atlas%' OR lower(cloud_name) LIKE '%mongodb%' THEN 'atlas'
                ELSE cloud_scope
            END
            WHERE cloud_scope IS NULL
        """))

    async with async_session() as db:
        await seed(db)
        from app.routers.forecast import ensure_default_config, ensure_provider_forecast_rules
        await ensure_default_config(db)
        await ensure_provider_forecast_rules(db)
        from app.routers.planning import ensure_default_financial_types
        await ensure_default_financial_types(db)
        from app.routers.clone_rules import ensure_focus_templates
        await ensure_focus_templates(db)
        from seed_marketplace_focus import ensure_marketplace_focus
        ensure_marketplace_focus()

    # Start the ETL scheduler
    await scheduler_service.start_scheduler()

    yield

    # Stop the scheduler
    await scheduler_service.stop_scheduler()


app = FastAPI(title="FinOps ETL Platform", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(connections.router)
app.include_router(csv_upload.router)
app.include_router(etl.router)
app.include_router(rules.router)
app.include_router(periods.router)
app.include_router(analysis.router)
app.include_router(destinations.router)
app.include_router(schema.router)
app.include_router(forecast.router)
app.include_router(clone_rules.router)
app.include_router(planning.router)
app.include_router(savings.router)
app.include_router(azure_tag_policy.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
