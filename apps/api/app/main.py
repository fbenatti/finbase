from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from app.config import settings
from app.database import engine, Base, async_session
from app.routers import connections, csv_upload, etl, rules, periods, analysis, ai_dashboards, ai_copilot, ai_provider_dashboards, destinations, schema, forecast, clone_rules, planning, savings, azure_tag_policy, dimon, azure_ptu, reservations, scheduled_shutdown, azure_resource_graph, oci_operational, auth as auth_router
from app.seed import seed
from app.services import scheduler_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging
    _log = logging.getLogger("app.main")
    if settings.env == "production" and not settings.auth_enabled:
        _log.warning("AUTH_ENABLED=false ignorado em production: autenticação sempre ligada em produção")
    if settings.env == "production" and settings.jwt_secret_key == "dev-insecure-secret-change-me":
        raise RuntimeError("JWT_SECRET_KEY deve ser configurada em production")
    async with engine.begin() as conn:
        from app.models.source_connection import SourceConnection  # noqa: F401
        from app.models.ingested_blob import IngestedBlob  # noqa: F401
        from app.models.rules import EtlMapping, BusinessRule, ClassificationRule, ClassificationFieldValue, OrganizationMoveAudit, DepartmentSharedCost, DepartmentSharedCostTarget  # noqa: F401
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
        from app.models.ai_cost import AiCostLink, AiCostLinkSource, AiCostAllocation, AiCostPoolAllocation  # noqa: F401
        from app.models.dimon_rule import DimonRule, DimonRuleSource  # noqa: F401
        from app.models.shutdown_exception_rule import ShutdownExceptionRule  # noqa: F401
        from app.models.azure_ptu import AzurePtuProjectMapping, AzurePtuSubproject, AzurePtuUsageEvent, AzurePtuCheckpoint  # noqa: F401
        from app.models.orphan_annotation import OrphanAnnotation  # noqa: F401
        from app.models.identity import User, Role, RolePermission, Group, RefreshToken  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
        # create_all does not evolve PostgreSQL tables.  Keep this small,
        # idempotent migration next to startup until Alembic is introduced.
        # asyncpg executes prepared statements and rejects multi-command SQL,
        # so each migration operation must be sent independently.
        for statement in (
            # reservation_collection (22 chars) excede o VARCHAR(20) original.
            "ALTER TABLE etl_task ALTER COLUMN task_type TYPE VARCHAR(40)",
            "ALTER TABLE scheduled_task ADD COLUMN IF NOT EXISTS connection_ids JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE forecast_config ADD COLUMN IF NOT EXISTS period_allocation VARCHAR(30) NOT NULL DEFAULT 'none'",
            "ALTER TABLE forecast_ack ADD COLUMN IF NOT EXISTS severity VARCHAR(20) NOT NULL DEFAULT 'Informação'",
            "ALTER TABLE forecast_ack ADD COLUMN IF NOT EXISTS ordem INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE source_connection ADD COLUMN IF NOT EXISTS last_test_status VARCHAR(30)",
            "ALTER TABLE source_connection ADD COLUMN IF NOT EXISTS last_test_message VARCHAR(500)",
            "ALTER TABLE source_connection ADD COLUMN IF NOT EXISTS last_tested_at TIMESTAMPTZ",
            "ALTER TABLE forecast_config ADD COLUMN IF NOT EXISTS charge_end_column VARCHAR(200)",
            "ALTER TABLE forecast_config ADD COLUMN IF NOT EXISTS connection_allocation_rules JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE forecast_run ADD COLUMN IF NOT EXISTS config_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb",
            "ALTER TABLE forecast_run ADD COLUMN IF NOT EXISTS destination_table_id_snapshot INTEGER",
            "ALTER TABLE forecast_run ADD COLUMN IF NOT EXISTS destination_physical_name VARCHAR(200)",
            "ALTER TABLE forecast_run ADD COLUMN IF NOT EXISTS source_snapshot_id VARCHAR(100)",
            "ALTER TABLE forecast_run ADD COLUMN IF NOT EXISTS classification_version_id INTEGER",
            "ALTER TABLE forecast_run ALTER COLUMN forecast_config_id DROP NOT NULL",
            "ALTER TABLE forecast_business_rule ADD COLUMN IF NOT EXISTS identification_clauses JSONB NOT NULL DEFAULT '[]'::jsonb",
            """CREATE TABLE IF NOT EXISTS department_shared_cost (
              id SERIAL PRIMARY KEY,
              department_id INTEGER NOT NULL UNIQUE REFERENCES department(id) ON DELETE CASCADE,
              enabled BOOLEAN NOT NULL DEFAULT FALSE,
              mode VARCHAR(20) NOT NULL DEFAULT 'consumption',
              created_at TIMESTAMPTZ DEFAULT now(),
              updated_at TIMESTAMPTZ DEFAULT now()
            )""",
            """CREATE TABLE IF NOT EXISTS department_shared_cost_target (
              id SERIAL PRIMARY KEY,
              config_id INTEGER NOT NULL REFERENCES department_shared_cost(id) ON DELETE CASCADE,
              target_department_id INTEGER NOT NULL REFERENCES department(id) ON DELETE CASCADE,
              percent DOUBLE PRECISION,
              created_at TIMESTAMPTZ DEFAULT now(),
              UNIQUE (config_id, target_department_id)
            )""",
            """CREATE TABLE IF NOT EXISTS ai_cost_link (
              id SERIAL PRIMARY KEY,
              name VARCHAR(200) NOT NULL,
              provider VARCHAR(100) NOT NULL,
              product_name VARCHAR(200) NOT NULL,
              license_sku VARCHAR(200),
              valid_from VARCHAR(6),
              valid_until VARCHAR(6),
              active BOOLEAN NOT NULL DEFAULT TRUE,
              distribution_enabled BOOLEAN NOT NULL DEFAULT FALSE,
              distribution_basis VARCHAR(30),
              created_at TIMESTAMPTZ DEFAULT now(),
              updated_at TIMESTAMPTZ DEFAULT now()
            )""",
            """CREATE TABLE IF NOT EXISTS ai_cost_link_source (
              id SERIAL PRIMARY KEY,
              link_id INTEGER NOT NULL REFERENCES ai_cost_link(id) ON DELETE CASCADE,
              connection_id INTEGER REFERENCES source_connection(id) ON DELETE SET NULL,
              filter_json JSONB NOT NULL DEFAULT '{}'::jsonb,
              value_column VARCHAR(50) NOT NULL DEFAULT 'BilledCost',
              percent DOUBLE PRECISION NOT NULL DEFAULT 100.0,
              created_at TIMESTAMPTZ DEFAULT now()
            )""",
            """CREATE TABLE IF NOT EXISTS ai_cost_allocation (
              id SERIAL PRIMARY KEY,
              link_id INTEGER NOT NULL REFERENCES ai_cost_link(id) ON DELETE CASCADE,
              billing_period VARCHAR(6) NOT NULL,
              user_email VARCHAR(320),
              provider VARCHAR(100) NOT NULL,
              product_name VARCHAR(200) NOT NULL,
              allocated_amount DOUBLE PRECISION NOT NULL DEFAULT 0.0,
              allocation_basis VARCHAR(30),
              source_refs JSONB,
              created_at TIMESTAMPTZ DEFAULT now()
            )""",
            "CREATE INDEX IF NOT EXISTS ix_ai_cost_link_provider ON ai_cost_link (provider)",
            "ALTER TABLE ai_cost_link ADD COLUMN IF NOT EXISTS tool_connection_ids JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE ai_cost_link ADD COLUMN IF NOT EXISTS target_type VARCHAR(30) NOT NULL DEFAULT 'license_pool'",
            "ALTER TABLE ai_cost_link ADD COLUMN IF NOT EXISTS operational_connection_id INTEGER",
            "ALTER TABLE ai_cost_link ADD COLUMN IF NOT EXISTS department_id INTEGER",
            "ALTER TABLE ai_cost_link ADD COLUMN IF NOT EXISTS project_key VARCHAR(120)",
            "ALTER TABLE ai_cost_link ADD COLUMN IF NOT EXISTS purchase_mode VARCHAR(30)",
            "ALTER TABLE ai_cost_link ADD COLUMN IF NOT EXISTS deployment_type VARCHAR(40)",
            "ALTER TABLE ai_cost_link ADD COLUMN IF NOT EXISTS cost_component VARCHAR(40)",
            "ALTER TABLE ai_cost_link ADD COLUMN IF NOT EXISTS allocation_level VARCHAR(30)",
            "ALTER TABLE ai_cost_link ADD COLUMN IF NOT EXISTS materialization_table VARCHAR(120)",
            "ALTER TABLE ai_cost_link ADD COLUMN IF NOT EXISTS materialization_status VARCHAR(30) NOT NULL DEFAULT 'PENDING'",
            "ALTER TABLE ai_cost_link ADD COLUMN IF NOT EXISTS materialization_rows INTEGER",
            "ALTER TABLE ai_cost_link ADD COLUMN IF NOT EXISTS materialization_run_id INTEGER",
            "ALTER TABLE ai_cost_link ADD COLUMN IF NOT EXISTS materialized_at TIMESTAMPTZ",
            "ALTER TABLE ai_cost_link ADD COLUMN IF NOT EXISTS materialization_error TEXT",
            "CREATE INDEX IF NOT EXISTS ix_ai_cost_link_target_type ON ai_cost_link (target_type)",
            "CREATE INDEX IF NOT EXISTS ix_ai_cost_link_source_link ON ai_cost_link_source (link_id)",
            "ALTER TABLE azure_ptu_subproject ADD COLUMN IF NOT EXISTS description TEXT",
            "ALTER TABLE azure_ptu_subproject ADD COLUMN IF NOT EXISTS department_id INTEGER",
            """CREATE TABLE IF NOT EXISTS dimon_rule (
              id SERIAL PRIMARY KEY,
              code VARCHAR(120) NOT NULL UNIQUE,
              name VARCHAR(200) NOT NULL,
              description TEXT,
              active BOOLEAN NOT NULL DEFAULT TRUE,
              valid_from VARCHAR(6),
              valid_until VARCHAR(6),
              materialization_table VARCHAR(140),
              materialization_status VARCHAR(30) NOT NULL DEFAULT 'PENDING',
              materialization_rows INTEGER,
              materialization_run_id INTEGER,
              materialized_at TIMESTAMPTZ,
              materialization_error TEXT,
              created_at TIMESTAMPTZ DEFAULT now(),
              updated_at TIMESTAMPTZ DEFAULT now()
            )""",
            """CREATE TABLE IF NOT EXISTS dimon_rule_source (
              id SERIAL PRIMARY KEY,
              rule_id INTEGER NOT NULL REFERENCES dimon_rule(id) ON DELETE CASCADE,
              gold_table VARCHAR(140) NOT NULL,
              filter_json JSONB NOT NULL DEFAULT '{}'::jsonb,
              value_column VARCHAR(50) NOT NULL DEFAULT 'realizado',
              percent DOUBLE PRECISION NOT NULL DEFAULT 100.0,
              created_at TIMESTAMPTZ DEFAULT now()
            )""",
            "CREATE INDEX IF NOT EXISTS ix_dimon_rule_source_rule ON dimon_rule_source (rule_id)",
            "ALTER TABLE dimon_rule ADD COLUMN IF NOT EXISTS dimensions JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE dimon_rule_source ADD COLUMN IF NOT EXISTS gold_tables JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE dimon_rule ADD COLUMN IF NOT EXISTS measure_columns JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE dimon_rule ADD COLUMN IF NOT EXISTS sql_template TEXT",
            "ALTER TABLE dimon_rule ADD COLUMN IF NOT EXISTS snapshot_mode BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE dimon_rule ADD COLUMN IF NOT EXISTS snapshot_col VARCHAR(50) NOT NULL DEFAULT '_snapshot_date'",
            """CREATE TABLE IF NOT EXISTS shutdown_exception_rule (
              id SERIAL PRIMARY KEY,
              name VARCHAR(200) NOT NULL,
              description TEXT,
              owner_email VARCHAR(200),
              scope_type VARCHAR(50) NOT NULL DEFAULT 'resource',
              scope_value VARCHAR(500),
              tag_key VARCHAR(200),
              tag_value VARCHAR(500),
              filter_json JSONB NOT NULL DEFAULT '{}'::jsonb,
              valid_from VARCHAR(6),
              valid_until VARCHAR(6),
              is_active BOOLEAN NOT NULL DEFAULT TRUE,
              created_at TIMESTAMPTZ DEFAULT now(),
              updated_at TIMESTAMPTZ DEFAULT now()
            )""",
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
            "CREATE INDEX IF NOT EXISTS ix_classification_field_value_value_prefix ON classification_field_value (destination_table_id, cloud, field, value text_pattern_ops)",
            "ALTER TABLE source_connection ADD COLUMN IF NOT EXISTS cloud_scope VARCHAR(50)",
            "CREATE INDEX IF NOT EXISTS ix_source_connection_cloud_scope ON source_connection (cloud_scope)",
            # forecast_run.cutoff_date is now NULL until the engine computes the
            # data-driven cutoff (MAX collected − cutoff_days); never "today".
            "ALTER TABLE forecast_run ALTER COLUMN cutoff_date DROP NOT NULL",
        ):
            await conn.execute(text(statement))
        # The Projects feature was removed (UI, API and metadata).  Drop the
        # legacy table and its columns.  Classification rules keep their
        # department target; the project_id column is dropped with its values.
        for statement in (
            "ALTER TABLE classification_rule DROP CONSTRAINT IF EXISTS classification_rule_project_id_fkey",
            "ALTER TABLE classification_rule DROP COLUMN IF EXISTS project_id",
            "ALTER TABLE planning_entry DROP COLUMN IF EXISTS project_id",
            "ALTER TABLE planning_entry DROP COLUMN IF EXISTS project_name",
            "DROP TABLE IF EXISTS project",
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
        from app.seed_identity import seed_identity
        await seed_identity(db)
        from app.routers.forecast import ensure_default_config, ensure_provider_forecast_rules
        await ensure_default_config(db)
        await ensure_provider_forecast_rules(db)
        from app.routers.planning import ensure_default_financial_types, ensure_planning_paths_synced
        await ensure_default_financial_types(db)
        await ensure_planning_paths_synced(db)
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

app.include_router(auth_router.router)
app.include_router(connections.router)
app.include_router(csv_upload.router)
app.include_router(etl.router)
app.include_router(rules.router)
app.include_router(periods.router)
app.include_router(analysis.router)
app.include_router(ai_dashboards.router)
app.include_router(ai_copilot.router)
app.include_router(ai_provider_dashboards.router)
app.include_router(destinations.router)
app.include_router(schema.router)
app.include_router(forecast.router)
app.include_router(clone_rules.router)
app.include_router(planning.router)
app.include_router(savings.router)
app.include_router(azure_tag_policy.router)
app.include_router(dimon.router)
app.include_router(azure_ptu.router)
app.include_router(reservations.router)
app.include_router(scheduled_shutdown.router)
app.include_router(azure_resource_graph.router)
app.include_router(oci_operational.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
