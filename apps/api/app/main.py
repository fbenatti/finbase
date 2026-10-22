import logging
import re
import asyncio
import contextlib
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text
from app.config import settings
from app.database import engine, Base, async_session
from app.routers import connections, csv_upload, etl, rules, periods, analysis, destinations, schema, forecast, clone_rules, planning, anomalies, savings, azure_tag_policy, gold, reservations, scheduled_shutdown, azure_resource_graph, azure_sources, azure_mca_commitment, oci_operational, ideas, financial_contracts, auth as auth_router, users as users_router, groups as groups_router, roles as roles_router, audit as audit_router, sso as sso_router, sso_providers as sso_providers_router, integrations, processing_logs
from app.routers import modules as modules_router
from app.routers import connector_visibility as connector_visibility_router
from app.core.navigation_middleware import NavigationPolicyMiddleware
from app.seed import seed
from app.services import scheduler_service

logger = logging.getLogger(__name__)


async def _refresh_processing_log_cube_loop() -> None:
    import asyncio

    from app.services.processing_logs import publish_processing_log_cube

    while True:
        try:
            await publish_processing_log_cube()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Falha ao atualizar cubo operacional de logs")
        await asyncio.sleep(15)


async def _execute_migration(conn, statement: str, *, attempts: int = 5) -> None:
    """Executa uma migração tolerando contenção de lock.

    O `lock_timeout` evita esperar minutos por uma tabela em uso, mas transforma
    a espera em erro. Como as migrações são idempotentes (`IF NOT EXISTS`),
    tentar de novo com espera crescente é seguro — e impede que uma coleta em
    andamento derrube o startup da API.
    """
    import asyncio as _asyncio

    from sqlalchemy import text as _text

    # PostgreSQL pede ACCESS EXCLUSIVE mesmo para ADD COLUMN IF NOT EXISTS.
    # Em um startup com worker ativo, esse lock pode ficar na fila atrás da
    # transação da coleta e bloquear as atualizações de progresso de etl_task.
    # Evite emitir DDL quando o schema já está na versão solicitada.
    add = re.fullmatch(
        r"ALTER TABLE (\w+) ADD COLUMN IF NOT EXISTS (\w+)\s+.+",
        statement.strip(), re.IGNORECASE | re.DOTALL,
    )
    alter_type = re.fullmatch(
        r"ALTER TABLE (\w+) ALTER COLUMN (\w+) TYPE VARCHAR\((\d+)\)",
        statement.strip(), re.IGNORECASE,
    )
    drop_not_null = re.fullmatch(
        r"ALTER TABLE (\w+) ALTER COLUMN (\w+) DROP NOT NULL",
        statement.strip(), re.IGNORECASE,
    )
    match = add or alter_type or drop_not_null
    if match:
        table_name, column_name = match.group(1), match.group(2)
        column = (await conn.execute(_text("""
            SELECT data_type, character_maximum_length, is_nullable
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = :table_name AND column_name = :column_name
        """), {"table_name": table_name, "column_name": column_name})).first()
        if add and column:
            return
        if alter_type and column and column.data_type == "character varying" \
                and column.character_maximum_length == int(alter_type.group(3)):
            return
        if drop_not_null and column and column.is_nullable == "YES":
            return

    atraso = 1.0
    for tentativa in range(1, attempts + 1):
        # SAVEPOINT por statement: sem ele, **um** erro de lock aborta a
        # transação e todas as migrações seguintes falham em cascata com
        # "current transaction is aborted", escondendo a causa original.
        ponto = f"migracao_{tentativa}"
        await conn.execute(_text(f"SAVEPOINT {ponto}"))
        try:
            await conn.execute(_text(statement))
            await conn.execute(_text(f"RELEASE SAVEPOINT {ponto}"))
            return
        except Exception as exc:  # noqa: BLE001
            await conn.execute(_text(f"ROLLBACK TO SAVEPOINT {ponto}"))
            nome = type(exc).__name__
            mensagem = str(exc).lower()
            transitorio = (
                "LockNotAvailable" in nome
                or "lock timeout" in mensagem
                or "DeadlockDetected" in nome
            )
            if not transitorio:
                raise
            if tentativa == attempts:
                # Migração idempotente que não conseguiu o lock depois de todas
                # as tentativas: registra e segue. Derrubar o startup por causa
                # dela deixava a API fora do ar enquanto uma coleta rodava — o
                # oposto do que se quer. A próxima subida tenta de novo.
                logger.error(
                    "Migração não aplicada por contenção de lock (seguindo): %s",
                    statement[:90],
                )
                return
            logger.warning(
                "Migração com lock indisponível (tentativa %d/%d, %s): %s",
                tentativa, attempts, nome, statement[:70],
            )
            await _asyncio.sleep(atraso)
            atraso = min(atraso * 2, 8.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging
    _log = logging.getLogger("app.main")
    if settings.env == "production" and not settings.auth_enabled:
        _log.warning("AUTH_ENABLED=false ignorado em production: autenticação sempre ligada em produção")
    if settings.env == "production" and settings.jwt_secret_key == "dev-insecure-secret-change-me":
        raise RuntimeError("JWT_SECRET_KEY deve ser configurada em production")

    # Scaled API replicas must not race database bootstrapping/migrations.
    # The dedicated scheduler service performs this once before replicas start.
    if not settings.bootstrap_enabled:
        log_cube_task = asyncio.create_task(_refresh_processing_log_cube_loop())
        if settings.scheduler_enabled:
            await scheduler_service.start_scheduler()
        yield
        log_cube_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await log_cube_task
        if settings.scheduler_enabled:
            await scheduler_service.stop_scheduler()
        return

    async with engine.begin() as conn:
        from app.models.source_connection import SourceConnection  # noqa: F401
        from app.models.ingested_blob import IngestedBlob  # noqa: F401
        from app.models.rules import EtlMapping, BusinessRule, ClassificationRule, ClassificationFieldValue, OrganizationMoveAudit, DepartmentSharedCost, DepartmentSharedCostTarget  # noqa: F401
        from app.models.financial_contract import FinancialContract, FinancialContractConnection, FinancialContractBudgetScope, ContractSettlementRun, ContractSettlementMonth  # noqa: F401
        from app.models.contract_story import ContractStoryLedger  # noqa: F401
        from app.models.period_status import PeriodStatus  # noqa: F401
        from app.models.etl_task import EtlTask  # noqa: F401
        from app.models.processing_log import ProcessingLog  # noqa: F401
        from app.models.destination import DestinationTable, DestinationColumn, SourceDestinationMapping  # noqa: F401
        from app.models.skipped_blob import SkippedBlob  # noqa: F401
        from app.models.scheduled_task import ScheduledTask  # noqa: F401
        from app.models.pivot_view import PivotView  # noqa: F401
        from app.models.dashboard import Dashboard, DashboardWidget  # noqa: F401
        from app.models.forecast import ForecastConfig, ForecastAdjustment, ForecastBusinessRule, ForecastRun  # noqa: F401
        from app.models.planning import FinancialType, PlanningVersion, PlanningEntry, BudgetDefenseRequest  # noqa: F401
        from app.models.budget_accuracy import BudgetAccuracyAck, BudgetAccuracyAckHistory  # noqa: F401
        from app.models.clone_rule import CloneRule, ClonePricePendencies  # noqa: F401
        from app.models.saving import SavingAction, SavingMeasurement  # noqa: F401
        from app.models.ai_cost import AiCostLink, AiCostLinkSource, AiCostAllocation, AiCostPoolAllocation  # noqa: F401
        from app.models.gold_mart_rule import GoldMartRule, GoldMartRuleSource  # noqa: F401
        from app.models.shutdown_exception_rule import ShutdownExceptionRule  # noqa: F401
        from app.models.azure_ptu import AzurePtuProjectMapping, AzurePtuSubproject, AzurePtuUsageEvent, AzurePtuCheckpoint  # noqa: F401
        from app.models.orphan_annotation import OrphanAnnotation  # noqa: F401
        from app.models.idea import Idea, IdeaAssignee, IdeaVote, IdeaComment, IdeaStatusInterval  # noqa: F401
        from app.models.identity import User, Role, RolePermission, Group, RefreshToken, UserGrant, SsoProvider  # noqa: F401
        from app.models.audit_log import AuditLog  # noqa: F401
        from app.models.connector_visibility import ConnectorHidden  # noqa: F401
        from app.models.azure_tag_mapping import AzureTagMapping  # noqa: F401
        from app.models.gold_manifest import GoldManifest  # noqa: F401
        from app.models.anomaly import (  # noqa: F401
            AnomalyConfig, AnomalyRun, AnomalyPublication,
            AnomalyCase, AnomalyCaseEvent, AnomalyAction, AnomalyNotification,
        )
        await conn.run_sync(Base.metadata.create_all)
        # As migrações são DDL: `ALTER TABLE` pede ACCESS EXCLUSIVE e fica atrás
        # de qualquer worker que esteja no meio de uma transação. Sem limite, o
        # startup esperava minutos — e, com a API já servindo, travava as coletas
        # que tocavam as mesmas tabelas. O limite transforma a espera longa em
        # erro rápido; o retry abaixo tenta de novo.
        await conn.execute(text("SET LOCAL lock_timeout = '5s'"))
        await conn.execute(text("SET LOCAL statement_timeout = '120s'"))
        await _execute_migration(conn, "ALTER TABLE processing_log ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()")
        await _execute_migration(conn, "CREATE INDEX IF NOT EXISTS ix_processing_log_updated ON processing_log (updated_at)")
        await _execute_migration(conn, "ALTER TABLE navigation_module ADD COLUMN IF NOT EXISTS visible BOOLEAN NOT NULL DEFAULT true")
        # Retenção de auditoria (roda depois do create_all)
        await conn.execute(
            text("DELETE FROM audit_log WHERE created_at < now() - make_interval(days => :days)").bindparams(
                days=settings.audit_retention_days
            )
        )
        # create_all does not evolve PostgreSQL tables.  Keep this small,
        # idempotent migration next to startup until Alembic is introduced.
        await _execute_migration(conn, "ALTER TABLE user_account ADD COLUMN IF NOT EXISTS failed_attempts INTEGER NOT NULL DEFAULT 0")
        await _execute_migration(conn, "ALTER TABLE user_account ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ")
        await _execute_migration(conn, "ALTER TABLE navigation_module ADD COLUMN IF NOT EXISTS custom_label VARCHAR(160)")
        await _execute_migration(conn, "ALTER TABLE role_permission ADD COLUMN IF NOT EXISTS scope VARCHAR(60)")
        await _execute_migration(conn, "ALTER TABLE user_grant ADD COLUMN IF NOT EXISTS scope VARCHAR(60)")
        await _execute_migration(conn, "ALTER TABLE role_permission DROP CONSTRAINT IF EXISTS uq_role_permission")
        await _execute_migration(conn, "ALTER TABLE user_grant DROP CONSTRAINT IF EXISTS uq_user_grant")
        await _execute_migration(conn, "ALTER TABLE role_permission ADD CONSTRAINT uq_role_permission UNIQUE (role_id, module, action, scope)")
        await _execute_migration(conn, "ALTER TABLE user_grant ADD CONSTRAINT uq_user_grant UNIQUE (user_id, module, action, scope)")
        # asyncpg executes prepared statements and rejects multi-command SQL,
        # so each migration operation must be sent independently.
        for statement in (
            "ALTER TABLE department ADD COLUMN IF NOT EXISTS id_orcamento VARCHAR(80)",
            "CREATE INDEX IF NOT EXISTS ix_department_id_orcamento ON department (id_orcamento)",
            "UPDATE department SET id_orcamento = NULLIF(BTRIM(custom_fields->>'IDOrcamento'), '') WHERE id_orcamento IS NULL AND custom_fields->>'IDOrcamento' IS NOT NULL",
            "ALTER TABLE planning_version ADD COLUMN IF NOT EXISTS title VARCHAR(200)",
            "ALTER TABLE planning_version ADD COLUMN IF NOT EXISTS description TEXT",
            "ALTER TABLE planning_version ADD COLUMN IF NOT EXISTS event_type VARCHAR(40)",
            "ALTER TABLE planning_version ADD COLUMN IF NOT EXISTS effective_date DATE",
            "ALTER TABLE planning_version ADD COLUMN IF NOT EXISTS scope_id_orcamento VARCHAR(80)",
            "ALTER TABLE planning_version ADD COLUMN IF NOT EXISTS scope_cloud VARCHAR(80)",
            "ALTER TABLE planning_version ADD COLUMN IF NOT EXISTS published_to_budget_at TIMESTAMPTZ",
            "ALTER TABLE planning_version ADD COLUMN IF NOT EXISTS published_budget_version_id INTEGER REFERENCES planning_version(id)",
            "CREATE INDEX IF NOT EXISTS ix_planning_version_type_year_effective_date ON planning_version (financial_type_id, year, effective_date)",
            # Defesa orçamentária de projeto: a solicitação guarda a linha
            # individual (ID Orçamento pedido) antes de tocar o organograma.
            """CREATE TABLE IF NOT EXISTS budget_defense_request (
              id SERIAL PRIMARY KEY,
              year INTEGER NOT NULL,
              department_id INTEGER NOT NULL REFERENCES department(id) ON DELETE CASCADE,
              parent_department_id INTEGER REFERENCES department(id) ON DELETE SET NULL,
              is_new_project BOOLEAN NOT NULL DEFAULT FALSE,
              requested_id_orcamento VARCHAR(80) NOT NULL,
              requested_cloud VARCHAR(80),
              title VARCHAR(200),
              description TEXT,
              status VARCHAR(30) NOT NULL DEFAULT 'RASCUNHO',
              requested_by_id INTEGER REFERENCES user_account(id) ON DELETE SET NULL,
              requested_at TIMESTAMPTZ DEFAULT now(),
              decided_by_id INTEGER REFERENCES user_account(id) ON DELETE SET NULL,
              decided_at TIMESTAMPTZ,
              final_id_orcamento VARCHAR(80),
              final_cloud VARCHAR(80),
              decision_note TEXT,
              defense_version_id INTEGER REFERENCES planning_version(id) ON DELETE SET NULL,
              published_budget_version_id INTEGER REFERENCES planning_version(id) ON DELETE SET NULL,
              published_at TIMESTAMPTZ,
              created_at TIMESTAMPTZ DEFAULT now(),
              updated_at TIMESTAMPTZ DEFAULT now()
            )""",
            "CREATE INDEX IF NOT EXISTS ix_budget_defense_request_year ON budget_defense_request (year)",
            "CREATE INDEX IF NOT EXISTS ix_budget_defense_request_department_id ON budget_defense_request (department_id)",
            "CREATE INDEX IF NOT EXISTS ix_budget_defense_request_status ON budget_defense_request (status)",
            "CREATE INDEX IF NOT EXISTS ix_budget_defense_request_requested_id_orcamento ON budget_defense_request (requested_id_orcamento)",
            "CREATE INDEX IF NOT EXISTS ix_budget_defense_request_requested_cloud ON budget_defense_request (requested_cloud)",
            "CREATE INDEX IF NOT EXISTS ix_budget_defense_request_scope ON budget_defense_request (year, requested_id_orcamento, status)",
            "ALTER TABLE planning_version ADD COLUMN IF NOT EXISTS defense_request_id INTEGER REFERENCES budget_defense_request(id) ON DELETE SET NULL",
            "CREATE INDEX IF NOT EXISTS ix_planning_version_defense_request_id ON planning_version (defense_request_id)",
            # reservation_collection (22 chars) excede o VARCHAR(20) original.
            "ALTER TABLE etl_task ALTER COLUMN task_type TYPE VARCHAR(40)",
            "ALTER TABLE etl_task ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ",
            "ALTER TABLE etl_task ADD COLUMN IF NOT EXISTS worker_pid INTEGER",
            "ALTER TABLE etl_task ADD COLUMN IF NOT EXISTS last_progress_at TIMESTAMPTZ",
            "ALTER TABLE etl_task ADD COLUMN IF NOT EXISTS recovery_attempts INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE ingested_blob ALTER COLUMN billing_period TYPE VARCHAR(8)",
            "CREATE INDEX IF NOT EXISTS ix_etl_task_worker_status ON etl_task (worker_pid, status)",
            "ALTER TABLE scheduled_task ADD COLUMN IF NOT EXISTS connection_ids JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE scheduled_task ADD COLUMN IF NOT EXISTS schedule_type VARCHAR(30) NOT NULL DEFAULT 'collection'",
            "ALTER TABLE scheduled_task ALTER COLUMN connection_id DROP NOT NULL",
            "ALTER TABLE forecast_config ADD COLUMN IF NOT EXISTS period_allocation VARCHAR(30) NOT NULL DEFAULT 'none'",
            "ALTER TABLE forecast_config ADD COLUMN IF NOT EXISTS recent_weight DOUBLE PRECISION NOT NULL DEFAULT 0.7",
            "ALTER TABLE forecast_config ADD COLUMN IF NOT EXISTS minimum_daily_points INTEGER NOT NULL DEFAULT 7",
            "ALTER TABLE forecast_config ADD COLUMN IF NOT EXISTS minimum_monthly_points INTEGER NOT NULL DEFAULT 3",
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
            "ALTER TABLE forecast_business_rule ADD COLUMN IF NOT EXISTS behavior_options JSONB NOT NULL DEFAULT '{}'::jsonb",
            """CREATE TABLE IF NOT EXISTS department_shared_cost (
              id SERIAL PRIMARY KEY,
              department_id INTEGER NOT NULL UNIQUE REFERENCES department(id) ON DELETE CASCADE,
              enabled BOOLEAN NOT NULL DEFAULT FALSE,
              mode VARCHAR(20) NOT NULL DEFAULT 'consumption',
              created_at TIMESTAMPTZ DEFAULT now(),
              updated_at TIMESTAMPTZ DEFAULT now()
            )""",
            "ALTER TABLE department_shared_cost ADD COLUMN IF NOT EXISTS allocation_type VARCHAR(40) NOT NULL DEFAULT 'shared_cost'",
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
            """CREATE TABLE IF NOT EXISTS azure_tag_mapping (
              id SERIAL PRIMARY KEY,
              tag_key VARCHAR(512) NOT NULL,
              focus_field VARCHAR(200) NOT NULL,
              active BOOLEAN NOT NULL DEFAULT TRUE,
              created_at TIMESTAMPTZ DEFAULT now(),
              updated_at TIMESTAMPTZ DEFAULT now(),
              CONSTRAINT uq_azure_tag_mapping_key UNIQUE (tag_key)
            )""",
            """INSERT INTO azure_tag_mapping (tag_key, focus_field, active)
               SELECT seed.tag_key, seed.focus_field, TRUE
               FROM (VALUES ('projeto', 'organograma_nivel_5'), ('diretoria', 'organograma_nivel_4'), ('centro-de-custos', 'centro_custo')) AS seed(tag_key, focus_field)
               WHERE NOT EXISTS (SELECT 1 FROM azure_tag_mapping)""",
            """UPDATE azure_tag_mapping AS old
               SET tag_key = 'centro-de-custos', updated_at = now()
               WHERE old.tag_key = 'centro_custo'
                 AND NOT EXISTS (
                   SELECT 1 FROM azure_tag_mapping current
                   WHERE current.tag_key = 'centro-de-custos'
                 )""",
            """UPDATE azure_tag_mapping AS old
               SET active = FALSE, updated_at = now()
               WHERE old.tag_key = 'centro_custo'
                 AND EXISTS (
                   SELECT 1 FROM azure_tag_mapping current
                   WHERE current.tag_key = 'centro-de-custos'
                 )""",
            # `blob_prefixes` passa de lista de caminhos para mapa
            # `{tipo de relatório: caminho}`: o tipo é variável, não código.
            # Idempotente e restrito ao formato antigo.
            """UPDATE source_connection
               SET blob_prefixes = json_build_object(
                     CASE
                       WHEN blob_prefixes::text ILIKE '%amortized%' THEN 'amortized_cost'
                       WHEN blob_prefixes::text ILIKE '%focus%' THEN 'focus'
                       ELSE 'actual_cost'
                     END,
                     blob_prefixes->>0
                   )::json
               WHERE blob_prefixes IS NOT NULL
                 AND json_typeof(blob_prefixes) = 'array'
                 AND blob_prefixes::text <> '[]'""",
            # O mapeamento passa a registrar a fonte dona: várias fontes
            # compartilham a conexão e várias miram a mesma coluna canônica.
            "ALTER TABLE etl_mapping ADD COLUMN IF NOT EXISTS source_key VARCHAR(60)",
            "CREATE INDEX IF NOT EXISTS ix_etl_mapping_source_key ON etl_mapping (source_key)",
            # Decisão assistida pelo Jev: o plano exige modelo, versão e motivo
            # auditados, e que o assistido nunca altere o fato.
            """CREATE TABLE IF NOT EXISTS assisted_decision_audit (
                 id SERIAL PRIMARY KEY,
                 integration_code VARCHAR(60) NOT NULL,
                 input_hash VARCHAR(64) NOT NULL,
                 status VARCHAR(30) NOT NULL,
                 model VARCHAR(80),
                 model_version VARCHAR(80),
                 answers JSON,
                 reason TEXT,
                 -- O que o determinístico decidiu antes do Jev opinar.
                 deterministic_verdict VARCHAR(30),
                 -- O Jev nunca rebaixa INVALID nem promove a VALID.
                 applied BOOLEAN NOT NULL DEFAULT false,
                 created_at TIMESTAMPTZ DEFAULT now()
               )""",
            "CREATE INDEX IF NOT EXISTS ix_assisted_decision_hash ON assisted_decision_audit (input_hash)",
            # Serviços auxiliares (Jev, ServiceNow): o segredo fica em
            # `secret_json` e nunca é devolvido pela API.
            """CREATE TABLE IF NOT EXISTS system_integration (
                 id SERIAL PRIMARY KEY,
                 code VARCHAR(60) NOT NULL UNIQUE,
                 name VARCHAR(200) NOT NULL,
                 integration_type VARCHAR(60) NOT NULL,
                 description TEXT,
                 enabled BOOLEAN NOT NULL DEFAULT false,
                 config_json JSON NOT NULL DEFAULT '{}',
                 secret_json JSON NOT NULL DEFAULT '{}',
                 last_test_at TIMESTAMPTZ,
                 last_test_status VARCHAR(30),
                 last_test_message TEXT,
                 created_at TIMESTAMPTZ DEFAULT now(),
                 updated_at TIMESTAMPTZ
               )""",
            "CREATE INDEX IF NOT EXISTS ix_system_integration_code ON system_integration (code)",
            """CREATE TABLE IF NOT EXISTS azure_connection_source (
              id SERIAL PRIMARY KEY,
              connection_id INTEGER NOT NULL REFERENCES source_connection(id) ON DELETE CASCADE,
              source_key VARCHAR(60) NOT NULL,
              enabled BOOLEAN NOT NULL DEFAULT FALSE,
              window_days INTEGER,
              metrics VARCHAR(2000),
              subscription_ids VARCHAR(4000),
              created_at TIMESTAMPTZ DEFAULT now(),
              updated_at TIMESTAMPTZ DEFAULT now(),
              CONSTRAINT uq_azure_conn_source UNIQUE (connection_id, source_key)
            )""",
            "CREATE INDEX IF NOT EXISTS ix_azure_connection_source_conn ON azure_connection_source (connection_id)",
            # Nomes de tabela históricos (`gold_rule*`): são o registro das regras de
            # cubo Gold. Renomear exigiria migração; o conteúdo já vive na Gold.
            """CREATE TABLE IF NOT EXISTS gold_rule (
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
            """CREATE TABLE IF NOT EXISTS gold_rule_source (
              id SERIAL PRIMARY KEY,
              rule_id INTEGER NOT NULL REFERENCES gold_rule(id) ON DELETE CASCADE,
              gold_table VARCHAR(140) NOT NULL,
              filter_json JSONB NOT NULL DEFAULT '{}'::jsonb,
              value_column VARCHAR(50) NOT NULL DEFAULT 'realizado',
              percent DOUBLE PRECISION NOT NULL DEFAULT 100.0,
              created_at TIMESTAMPTZ DEFAULT now()
            )""",
            "CREATE INDEX IF NOT EXISTS ix_gold_rule_source_rule ON gold_rule_source (rule_id)",
            # Manifesto de cada materialização Gold (G2). PostgreSQL guarda
            # metadata de execução; o dado financeiro continua no lakehouse.
            # Existe porque a coluna materialization_rows de gold_rule não é
            # confiável: cinco regras ficaram com status ERROR e contagem
            # preenchida, e a tabela publicada estava em outro namespace.
            """CREATE TABLE IF NOT EXISTS gold_manifest (
              id BIGSERIAL PRIMARY KEY,
              cube_code VARCHAR(120) NOT NULL,
              physical_table VARCHAR(140) NOT NULL,
              namespace VARCHAR(40) NOT NULL DEFAULT 'gold',
              contract_version VARCHAR(20) NOT NULL,
              run_id INTEGER,
              rule_id INTEGER,
              status VARCHAR(20) NOT NULL,
              reason VARCHAR(400),
              sql_hash VARCHAR(64),
              source_tables JSONB NOT NULL DEFAULT '[]'::jsonb,
              source_freshness_at TIMESTAMPTZ,
              rows_read BIGINT,
              rows_written BIGINT,
              financial_total_in DOUBLE PRECISION,
              financial_total_out DOUBLE PRECISION,
              financial_delta DOUBLE PRECISION,
              tolerance DOUBLE PRECISION NOT NULL DEFAULT 0.01,
              reconciliation_status VARCHAR(20),
              currency VARCHAR(8),
              previous_snapshot_id VARCHAR(64),
              rollback_table VARCHAR(140),
              error TEXT,
              started_at TIMESTAMPTZ,
              finished_at TIMESTAMPTZ DEFAULT now()
            )""",
            "CREATE INDEX IF NOT EXISTS ix_gold_manifest_cube ON gold_manifest (cube_code, started_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_gold_manifest_status ON gold_manifest (status, started_at DESC)",
            # Lock de materialização por cubo: duas execuções de C2 não podem rodar
            # simultaneamente, e um worker morto não pode bloquear o cubo para
            # sempre (expires_at permite roubar o lock após o timeout).
            """CREATE TABLE IF NOT EXISTS gold_cube_lock (
              cube_code VARCHAR(120) PRIMARY KEY,
              holder VARCHAR(200) NOT NULL,
              acquired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              expires_at TIMESTAMPTZ NOT NULL
            )""",
            "ALTER TABLE gold_rule ADD COLUMN IF NOT EXISTS dimensions JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE gold_rule_source ADD COLUMN IF NOT EXISTS gold_tables JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE gold_rule ADD COLUMN IF NOT EXISTS measure_columns JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE gold_rule ADD COLUMN IF NOT EXISTS sql_template TEXT",
            "ALTER TABLE gold_rule ADD COLUMN IF NOT EXISTS snapshot_mode BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE gold_rule ADD COLUMN IF NOT EXISTS snapshot_col VARCHAR(50) NOT NULL DEFAULT '_snapshot_date'",
            # Chave de materializador para cubos cuja construção é multi-estágio (não
            # é um único SELECT sobre uma fonte). Resolvida por allowlist no código:
            # **nunca** armazena nome de função ou Python vindo do usuário.
            "ALTER TABLE gold_rule ADD COLUMN IF NOT EXISTS materializer_key VARCHAR(60)",
            "CREATE INDEX IF NOT EXISTS ix_gold_rule_materializer ON gold_rule (materializer_key)",
            """INSERT INTO gold_rule (code, name, description, active, dimensions, measure_columns, snapshot_mode, snapshot_col, materialization_table, materialization_status)
               VALUES
                 ('ai_consumption_daily', 'IA — Consumo diário (Claude/NotebookLM)', 'Gold auxiliar de consumo de IA; publicada pela fila Gold a partir de dest_consumo_ia.', TRUE, '[]'::jsonb, '[]'::jsonb, FALSE, '_snapshot_date', 'dest_dm_ai_consumption_daily', 'PENDING'),
                 ('ai_license_snapshot', 'IA — Licenças (Claude/NotebookLM)', 'Gold auxiliar de licenças de IA; publicada pela fila Gold a partir de dest_licencas_ia.', TRUE, '[]'::jsonb, '[]'::jsonb, FALSE, '_snapshot_date', 'dest_dm_ai_license_snapshot', 'PENDING'),
                 ('copilot_usage_daily', 'Copilot — Uso diário', 'Gold auxiliar de uso do GitHub Copilot.', TRUE, '[]'::jsonb, '[]'::jsonb, FALSE, '_snapshot_date', 'dest_dm_copilot_usage_daily', 'PENDING'),
                 ('copilot_license_snapshot', 'Copilot — Licenças', 'Gold auxiliar de licenças do GitHub Copilot.', TRUE, '[]'::jsonb, '[]'::jsonb, FALSE, '_snapshot_date', 'dest_dm_copilot_license_snapshot', 'PENDING'),
                 ('copilot_user_period', 'Copilot — Usuário/período', 'Gold auxiliar de adoção por usuário e período.', TRUE, '[]'::jsonb, '[]'::jsonb, FALSE, '_snapshot_date', 'dest_dm_copilot_user_period', 'PENDING')
               ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description, active = TRUE, materialization_table = EXCLUDED.materialization_table""",
            """INSERT INTO gold_rule (code, name, description, active, dimensions, measure_columns, snapshot_mode, snapshot_col, materialization_table, materialization_status)
               VALUES ('vm_monthly_history', 'VM — Histórico mensal agregado', 'Cubo Gold de horas e custo por VM/mês, derivado da Silver e consumido pelo cubo Saving por desligue.', FALSE, '[]'::jsonb, '[]'::jsonb, FALSE, '_snapshot_date', 'dest_dm_cost_vm_monthly_history', 'PENDING')
               ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description, materialization_table = EXCLUDED.materialization_table""",
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
            await _execute_migration(conn, statement)
        # A auxiliar mensal de VMs saiu de Gold (`_tmp_vm_monthly`) para Silver
        # (`vm_monthly_history`). Atualiza apenas a regra conhecida que contém
        # a referência antiga; SQLs personalizados de outras regras são intactos.
        await _execute_migration(conn, """
            UPDATE gold_rule
            SET sql_template = REPLACE(
                sql_template,
                'iceberg.gold._tmp_vm_monthly',
                'iceberg.silver.vm_monthly_history'
            ), updated_at = now()
            WHERE code = 'saving_desligue_vm'
              AND sql_template LIKE '%iceberg.gold._tmp_vm_monthly%'
        """)
        # Built-in schedules are idempotent and remain editable by users.
        await _execute_migration(conn, """
            INSERT INTO scheduled_task
              (name, connection_id, connection_ids, destination_reprocess,
               cron_expression, timezone, active, config_json, schedule_type)
            SELECT 'Backup Diário Full', NULL, '[]'::jsonb, FALSE,
                   '0 1 * * *', 'America/Sao_Paulo', TRUE,
                   json_build_object('retention_days', 30), 'backup'
            WHERE NOT EXISTS (SELECT 1 FROM scheduled_task WHERE schedule_type = 'backup')
        """)
        await _execute_migration(conn, """
            INSERT INTO scheduled_task
              (name, connection_id, connection_ids, destination_reprocess,
               cron_expression, timezone, active, config_json, schedule_type)
            SELECT 'Detecção de anomalias', NULL, '[]'::jsonb, FALSE,
                   '0 6 * * *', 'America/Sao_Paulo', FALSE,
                   json_build_object('anomaly_config_ids', '[]'::json), 'anomaly_detection'
            WHERE NOT EXISTS (SELECT 1 FROM scheduled_task WHERE schedule_type = 'anomaly_detection')
        """)
        await _execute_migration(conn, """
            INSERT INTO scheduled_task
              (name, connection_id, connection_ids, destination_reprocess,
               cron_expression, timezone, active, config_json, schedule_type)
            SELECT 'Prazos de anomalias (SLA)', NULL, '[]'::jsonb, FALSE,
                   '*/30 * * * *', 'America/Sao_Paulo', TRUE,
                   json_build_object('due_soon_hours', 4), 'anomaly_sla'
            WHERE NOT EXISTS (SELECT 1 FROM scheduled_task WHERE schedule_type = 'anomaly_sla')
        """)
        await _execute_migration(conn, """
            INSERT INTO scheduled_task
              (name, connection_id, connection_ids, destination_reprocess,
               cron_expression, timezone, active, config_json, schedule_type)
            SELECT 'Reprocessamento', NULL, '[]'::jsonb, FALSE,
                   '0 2 * * 0', 'America/Sao_Paulo', FALSE,
                   json_build_object('period_mode', 'all', 'accuracy', TRUE, 'shared_cost', FALSE, 'forecast_config_ids', '[]'::json, 'gold_rule_ids', '[]'::json), 'reprocess'
            WHERE NOT EXISTS (SELECT 1 FROM scheduled_task WHERE schedule_type = 'reprocess')
        """)
        await _execute_migration(conn, """
            DO $$ BEGIN
              IF to_regclass('fact_cost') IS NOT NULL THEN
                ALTER TABLE fact_cost ADD COLUMN IF NOT EXISTS organograma_nivel_6 VARCHAR(200);
                ALTER TABLE fact_cost ADD COLUMN IF NOT EXISTS organograma_nivel_7 VARCHAR(200);
              END IF;
            END $$
        """)
        await _execute_migration(conn, """
            DO $$
            DECLARE level int;
            BEGIN
              IF to_regclass('fact_cost') IS NOT NULL THEN
                FOR level IN 1..7 LOOP
                  IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'fact_cost'
                      AND column_name = 'departamento_nivel_' || level
                  ) AND NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'fact_cost'
                      AND column_name = 'organograma_nivel_' || level
                  ) THEN
                    EXECUTE format(
                      'ALTER TABLE fact_cost RENAME COLUMN departamento_nivel_%s TO organograma_nivel_%s',
                      level, level
                    );
                  END IF;
                END LOOP;
              END IF;
            END $$
        """)
        # Hierarquia (Fase 1, aditiva): caminho raiz→folha em JSONB e id da folha.
        # Os níveis fixos organograma_nivel_* continuam como fallback de leitura.
        await _execute_migration(conn, """
            DO $$ BEGIN
              IF to_regclass('fact_cost') IS NOT NULL THEN
                ALTER TABLE fact_cost ADD COLUMN IF NOT EXISTS hierarquia JSONB;
                ALTER TABLE fact_cost ADD COLUMN IF NOT EXISTS hierarquia_id INTEGER;
                UPDATE fact_cost
                SET hierarquia = to_jsonb(ARRAY_REMOVE(ARRAY[
                      organograma_nivel_1, organograma_nivel_2, organograma_nivel_3,
                      organograma_nivel_4, organograma_nivel_5, organograma_nivel_6,
                      organograma_nivel_7], NULL))
                WHERE hierarquia IS NULL;
                -- Id da folha: caminho de nomes da árvore department (menor id em empate).
                WITH RECURSIVE tree AS (
                  SELECT id, name, ARRAY[name]::TEXT[] AS path
                  FROM department WHERE parent_id IS NULL
                  UNION ALL
                  SELECT d.id, d.name, t.path || d.name::TEXT
                  FROM department d JOIN tree t ON d.parent_id = t.id
                ), leaf AS (
                  SELECT path, MIN(id) AS id FROM tree GROUP BY path
                )
                UPDATE fact_cost f
                SET hierarquia_id = leaf.id
                FROM leaf
                WHERE f.hierarquia_id IS NULL
                  AND f.hierarquia IS NOT NULL
                  AND leaf.path = ARRAY(SELECT jsonb_array_elements_text(f.hierarquia));
              END IF;
            END $$
        """)
        # Grade de planejamento (Fase 1, aditiva): o nó da hierarquia substitui o
        # ID Orçamento como escopo. Versões antigas recebem o menor nó com esse id.
        await _execute_migration(conn, """
            DO $$ BEGIN
              IF to_regclass('planning_version') IS NOT NULL THEN
                ALTER TABLE planning_version ADD COLUMN IF NOT EXISTS scope_hierarquia_id INTEGER REFERENCES department(id) ON DELETE SET NULL;
                CREATE INDEX IF NOT EXISTS ix_planning_version_scope_hierarquia_id ON planning_version (scope_hierarquia_id);
                UPDATE planning_version pv
                SET scope_hierarquia_id = (
                  SELECT MIN(d.id) FROM department d WHERE d.id_orcamento = pv.scope_id_orcamento
                )
                WHERE pv.scope_hierarquia_id IS NULL AND pv.scope_id_orcamento IS NOT NULL;
              END IF;
            END $$
        """)
        await _execute_migration(conn, """
            DO $$
            DECLARE
              rec record;
            BEGIN
              IF to_regclass('azure_tag_mapping') IS NOT NULL THEN
                UPDATE azure_tag_mapping
                SET focus_field = REPLACE(focus_field, 'departamento_nivel_', 'organograma_nivel_')
                WHERE focus_field LIKE 'departamento_nivel_%';
                UPDATE azure_tag_mapping
                SET focus_field = 'organograma_nivel_5', updated_at = now()
                WHERE tag_key = 'projeto' AND focus_field LIKE 'departamento%';
                UPDATE azure_tag_mapping
                SET focus_field = 'organograma_nivel_4', updated_at = now()
                WHERE tag_key = 'diretoria' AND focus_field LIKE 'departamento%';
              END IF;

              IF to_regclass('destination_column') IS NOT NULL THEN
                UPDATE destination_column
                SET column_name = REPLACE(column_name, 'departamento_nivel_', 'organograma_nivel_')
                WHERE column_name LIKE 'departamento_nivel_%';
                UPDATE destination_column
                SET column_name = 'organograma'
                WHERE column_name = 'departamento';
              END IF;

              IF to_regclass('source_destination_mapping') IS NOT NULL THEN
                UPDATE source_destination_mapping
                SET
                  source_column = REPLACE(source_column, 'departamento_nivel_', 'organograma_nivel_'),
                  destination_column = REPLACE(destination_column, 'departamento_nivel_', 'organograma_nivel_')
                WHERE source_column LIKE 'departamento_nivel_%'
                   OR destination_column LIKE 'departamento_nivel_%';
                UPDATE source_destination_mapping
                SET
                  source_column = 'organograma'
                WHERE source_column = 'departamento';
                UPDATE source_destination_mapping
                SET destination_column = 'organograma'
                WHERE destination_column = 'departamento';
              END IF;

              IF to_regclass('etl_mapping') IS NOT NULL THEN
                UPDATE etl_mapping
                SET
                  source_column = REPLACE(source_column, 'departamento_nivel_', 'organograma_nivel_'),
                  target_column = REPLACE(target_column, 'departamento_nivel_', 'organograma_nivel_')
                WHERE source_column LIKE 'departamento_nivel_%'
                   OR target_column LIKE 'departamento_nivel_%';
                UPDATE etl_mapping
                SET source_column = 'organograma'
                WHERE source_column = 'departamento';
                UPDATE etl_mapping
                SET target_column = 'organograma'
                WHERE target_column = 'departamento';
              END IF;

              IF to_regclass('classification_field_value') IS NOT NULL THEN
                UPDATE classification_field_value
                SET field = REPLACE(field, 'departamento_nivel_', 'organograma_nivel_')
                WHERE field LIKE 'departamento_nivel_%';
                UPDATE classification_field_value
                SET field = 'organograma'
                WHERE field = 'departamento';
              END IF;

              IF to_regclass('classification_rule') IS NOT NULL THEN
                UPDATE classification_rule
                SET
                  source_field = REPLACE(source_field, 'departamento_nivel_', 'organograma_nivel_'),
                  target_field = REPLACE(target_field, 'departamento_nivel_', 'organograma_nivel_')
                WHERE source_field LIKE 'departamento_nivel_%'
                   OR target_field LIKE 'departamento_nivel_%';
                UPDATE classification_rule
                SET source_field = 'organograma'
                WHERE source_field = 'departamento';
                UPDATE classification_rule
                SET target_field = 'organograma'
                WHERE target_field = 'departamento';
              END IF;

              IF to_regclass('business_rule') IS NOT NULL THEN
                UPDATE business_rule
                SET target_column = REPLACE(target_column, 'departamento_nivel_', 'organograma_nivel_')
                WHERE target_column LIKE 'departamento_nivel_%';
                UPDATE business_rule
                SET target_column = 'organograma'
                WHERE target_column = 'departamento';
              END IF;

              IF to_regclass('gold_rule') IS NOT NULL THEN
                UPDATE gold_rule
                SET sql_template = REPLACE(sql_template, 'departamento_nivel_', 'organograma_nivel_')
                WHERE sql_template IS NOT NULL
                  AND sql_template LIKE '%departamento_nivel_%';
                UPDATE gold_rule
                SET sql_template = REPLACE(sql_template, '"departamento"', '"organograma"')
                WHERE sql_template IS NOT NULL
                  AND sql_template LIKE '%"departamento"%';
              END IF;

              FOR rec IN
                SELECT *
                FROM (VALUES
                  ('gold_rule', 'filter_json', 'json'),
                  ('gold_rule', 'dimensions', 'json'),
                  ('gold_rule', 'measure_columns', 'json'),
                  ('gold_rule_source', 'filter_json', 'json'),
                  ('gold_rule_source', 'gold_tables', 'json'),
                  ('forecast_config', 'dimensions', 'json'),
                  ('forecast_config', 'filters', 'json'),
                  ('forecast_config', 'connection_allocation_rules', 'json'),
                  ('forecast_business_rule', 'scope', 'json'),
                  ('forecast_business_rule', 'identification_clauses', 'json'),
                  ('forecast_business_rule', 'reference_values', 'json'),
                  ('forecast_run', 'config_snapshot', 'json'),
                  ('classification_rule', 'conditions', 'json'),
                  ('business_rule', 'conditions', 'json'),
                  ('saving_action', 'filters', 'json'),
                  ('shutdown_exception_rule', 'filter_json', 'json'),
                  ('pivot_view', 'config_json', 'json'),
                  ('dashboard', 'global_filters', 'json'),
                  ('dashboard_widget', 'config_json', 'json'),
                  ('dashboard_widget', 'position_json', 'json'),
                  ('ai_cost_link', 'filter_json', 'json'),
                  ('ai_cost_link_source', 'filter_json', 'json'),
                  ('ai_cost_allocation', 'source_refs', 'json'),
                  ('ai_cost_pool_allocation', 'source_refs', 'json'),
                  ('clone_rule', 'conditions', 'json'),
                  ('clone_rule', 'roles', 'json'),
                  ('clone_rule', 'original_changes', 'json'),
                  ('clone_rule', 'clone_changes', 'json'),
                  ('scheduled_task', 'config_json', 'json'),
                  ('etl_task', 'result_json', 'json'),
                  ('audit_log', 'before_json', 'jsonb'),
                  ('audit_log', 'after_json', 'jsonb'),
                  ('audit_log', 'changes_json', 'jsonb')
                ) AS payload(table_name, column_name, storage_type)
              LOOP
                IF to_regclass(rec.table_name) IS NOT NULL
                   AND EXISTS (
                     SELECT 1
                     FROM information_schema.columns
                     WHERE table_schema = current_schema()
                       AND table_name = rec.table_name
                       AND column_name = rec.column_name
                   ) THEN
                  IF rec.storage_type = 'jsonb' THEN
                    EXECUTE format(
                      'UPDATE %I SET %I = replace(%I::text, ''departamento_nivel_'', ''organograma_nivel_'')::jsonb
                       WHERE %I IS NOT NULL AND %I::text LIKE ''%%departamento_nivel_%%''',
                      rec.table_name, rec.column_name, rec.column_name,
                      rec.column_name, rec.column_name
                    );
                  ELSE
                    EXECUTE format(
                      'UPDATE %I SET %I = replace(%I::text, ''departamento_nivel_'', ''organograma_nivel_'')::json
                       WHERE %I IS NOT NULL AND %I::text LIKE ''%%departamento_nivel_%%''',
                      rec.table_name, rec.column_name, rec.column_name,
                      rec.column_name, rec.column_name
                    );
                  END IF;
                END IF;
              END LOOP;

              FOR rec IN
                SELECT *
                FROM (VALUES
                  ('gold_rule', 'filter_json', 'json'),
                  ('gold_rule', 'dimensions', 'json'),
                  ('gold_rule', 'measure_columns', 'json'),
                  ('gold_rule_source', 'filter_json', 'json'),
                  ('forecast_config', 'dimensions', 'json'),
                  ('forecast_config', 'filters', 'json'),
                  ('forecast_config', 'connection_allocation_rules', 'json'),
                  ('forecast_business_rule', 'scope', 'json'),
                  ('forecast_business_rule', 'identification_clauses', 'json'),
                  ('forecast_business_rule', 'reference_values', 'json'),
                  ('forecast_run', 'config_snapshot', 'json'),
                  ('classification_rule', 'conditions', 'json'),
                  ('business_rule', 'conditions', 'json'),
                  ('saving_action', 'filters', 'json'),
                  ('shutdown_exception_rule', 'filter_json', 'json'),
                  ('pivot_view', 'config_json', 'json'),
                  ('dashboard', 'global_filters', 'json'),
                  ('dashboard_widget', 'config_json', 'json'),
                  ('ai_cost_link', 'filter_json', 'json'),
                  ('ai_cost_link_source', 'filter_json', 'json'),
                  ('clone_rule', 'conditions', 'json'),
                  ('scheduled_task', 'config_json', 'json'),
                  ('etl_task', 'result_json', 'json'),
                  ('audit_log', 'before_json', 'jsonb'),
                  ('audit_log', 'after_json', 'jsonb'),
                  ('audit_log', 'changes_json', 'jsonb')
                ) AS payload(table_name, column_name, storage_type)
              LOOP
                IF to_regclass(rec.table_name) IS NOT NULL
                   AND EXISTS (
                     SELECT 1
                     FROM information_schema.columns
                     WHERE table_schema = current_schema()
                       AND table_name = rec.table_name
                       AND column_name = rec.column_name
                   ) THEN
                  IF rec.storage_type = 'jsonb' THEN
                    EXECUTE format(
                      'UPDATE %I SET %I = replace(replace(replace(replace(%I::text,
                        ''"column": "departamento"'', ''"column": "organograma"''),
                        ''"field": "departamento"'', ''"field": "organograma"''),
                        ''"target_field": "departamento"'', ''"target_field": "organograma"''),
                        ''"rows": ["departamento"'', ''"rows": ["organograma"'')::jsonb
                       WHERE %I IS NOT NULL AND %I::text LIKE ''%%"departamento"%%''',
                      rec.table_name, rec.column_name, rec.column_name,
                      rec.column_name, rec.column_name
                    );
                  ELSE
                    EXECUTE format(
                      'UPDATE %I SET %I = replace(replace(replace(replace(%I::text,
                        ''"column": "departamento"'', ''"column": "organograma"''),
                        ''"field": "departamento"'', ''"field": "organograma"''),
                        ''"target_field": "departamento"'', ''"target_field": "organograma"''),
                        ''"rows": ["departamento"'', ''"rows": ["organograma"'')::json
                       WHERE %I IS NOT NULL AND %I::text LIKE ''%%"departamento"%%''',
                      rec.table_name, rec.column_name, rec.column_name,
                      rec.column_name, rec.column_name
                    );
                  END IF;
                END IF;
              END LOOP;
            END $$
        """)
        await _execute_migration(conn, """
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
        """)
        # Populate frozen execution contracts for historical runs whose
        # configurations still exist.  Their legacy result table remains
        # queryable; runs without a result are intentionally left unchanged.
        await _execute_migration(conn, """
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
        """)
        for statement in (
            "ALTER TABLE business_rule ADD COLUMN IF NOT EXISTS cloud_scope VARCHAR(50)",
            "ALTER TABLE business_rule ADD COLUMN IF NOT EXISTS financial_effect VARCHAR(30) NOT NULL DEFAULT 'NONE'",
            "ALTER TABLE business_rule ADD COLUMN IF NOT EXISTS financial_contract_id INTEGER",
            "CREATE INDEX IF NOT EXISTS ix_business_rule_financial_contract ON business_rule (financial_contract_id)",
            "ALTER TABLE financial_contract ALTER COLUMN synthetic_department_id DROP NOT NULL",
            "ALTER TABLE contract_settlement_month ADD COLUMN IF NOT EXISTS credit_overage_delta NUMERIC(20,6) NOT NULL DEFAULT 0",
            "ALTER TABLE contract_settlement_month ADD COLUMN IF NOT EXISTS contractual_adjustment NUMERIC(20,6) NOT NULL DEFAULT 0",
            "ALTER TABLE contract_settlement_month ADD COLUMN IF NOT EXISTS opening_overage NUMERIC(20,6) NOT NULL DEFAULT 0",
            "ALTER TABLE contract_settlement_month ADD COLUMN IF NOT EXISTS closing_overage NUMERIC(20,6) NOT NULL DEFAULT 0",
            "ALTER TABLE contract_settlement_month ADD COLUMN IF NOT EXISTS settled_overage NUMERIC(20,6) NOT NULL DEFAULT 0",
            "ALTER TABLE contract_settlement_run ADD COLUMN IF NOT EXISTS budget_version_ids JSONB NOT NULL DEFAULT '[]'::jsonb",
            "UPDATE business_rule SET financial_effect = 'DIRECT_REALIZED', target_column = 'consumo_realizado' WHERE financial_effect = 'NONE' AND lower(target_column) = 'realizado'",
            "UPDATE gold_rule SET measure_columns = '[\"realizado\", \"realizado_cloud\", \"consumo_realizado\", \"ajuste_contratual\", \"billedcost\", \"consumedquantity\"]'::json WHERE code = 'visao-completa' AND measure_columns::jsonb IS DISTINCT FROM '[\"realizado\", \"realizado_cloud\", \"consumo_realizado\", \"ajuste_contratual\", \"billedcost\", \"consumedquantity\"]'::jsonb",
            "ALTER TABLE classification_rule ADD COLUMN IF NOT EXISTS cloud_scope VARCHAR(50)",
            "CREATE INDEX IF NOT EXISTS ix_business_rule_cloud_scope ON business_rule (cloud_scope)",
            "CREATE INDEX IF NOT EXISTS ix_classification_rule_cloud_scope ON classification_rule (cloud_scope)",
            "CREATE INDEX IF NOT EXISTS ix_classification_field_value_value_prefix ON classification_field_value (destination_table_id, cloud, field, value text_pattern_ops)",
            "ALTER TABLE destination_table ADD COLUMN IF NOT EXISTS partition_spec_json JSONB",
            "ALTER TABLE source_connection ADD COLUMN IF NOT EXISTS cloud_scope VARCHAR(50)",
            "CREATE INDEX IF NOT EXISTS ix_source_connection_cloud_scope ON source_connection (cloud_scope)",
            # forecast_run.cutoff_date is now NULL until the engine computes the
            # data-driven cutoff (MAX collected − cutoff_days); never "today".
            "ALTER TABLE forecast_run ALTER COLUMN cutoff_date DROP NOT NULL",
        ):
            await _execute_migration(conn, statement)

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
            await _execute_migration(conn, statement)
        from app.services.anomaly_migrations import migrate_anomalies
        await migrate_anomalies(conn)
        # Backfill the canonical provider slug for legacy rows that only have a
        # free-text cloud_name, so rule matching keeps working without a
        # re-import. Unknown/free-text names are left NULL (match only the
        # generic unscoped rules).
        await _execute_migration(conn, """
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
        """)
        # PyIceberg's SQL catalog is external to our ORM metadata. Repair old
        # host-specific file:// URIs only when that native table already exists.
        from app.services.iceberg_catalog_migration import migrate_iceberg_catalog_paths
        from app.services.iceberg_service import _LOCAL_WAREHOUSE
        await migrate_iceberg_catalog_paths(conn, _LOCAL_WAREHOUSE)

    # Run the backfill only after the schema transaction commits. A separate
    # session cannot see tables created by the still-open transaction above.
    from app.services.processing_logs import backfill_processing_logs
    async with async_session() as backfill_session:
        await backfill_session.run_sync(backfill_processing_logs)
        await backfill_session.commit()

    async with async_session() as db:
        await seed(db)
        # Registra o cubo agregado consumido pelo dashboard de compromissos;
        # a consulta do painel não deve varrer FOCUS/planning Silver no Trino.
        from app.services.commitments_gold import ensure_commitments_gold_rule
        await ensure_commitments_gold_rule(db)
        await db.commit()
        # Reconcile ARG destinations for connections created before the
        # automatic setup hook existed. The setup is idempotent and also
        # repairs missing mappings for existing Azure TI/4P connections,
        # including deployments that keep cost ingestion as azure_blob.
        from app.models.source_connection import SourceConnection
        from app.routers.azure_resource_graph import _setup_for_connection as setup_arg_connection
        arg_connections = (await db.execute(
          select(SourceConnection).where(
            (SourceConnection.connector_type == "azure_resource_graph")
            | (
              (SourceConnection.connector_type == "azure_blob")
              & SourceConnection.code.in_(("azure-4p", "azure-ti"))
            )
          )
        )).scalars().all()
        for arg_connection in arg_connections:
            await setup_arg_connection(db, arg_connection)
        from app.seed_identity import seed_identity
        await seed_identity(db)
        from app.services.navigation import sync_catalog
        await sync_catalog(db)
        from app.routers.forecast import ensure_default_config, ensure_provider_forecast_rules
        await ensure_default_config(db)
        await ensure_provider_forecast_rules(db)
        from app.routers.planning import ensure_default_budget_ids, ensure_default_financial_types, ensure_planning_paths_synced
        await ensure_default_financial_types(db)
        await ensure_default_budget_ids(db)
        await ensure_planning_paths_synced(db)
        from app.routers.clone_rules import ensure_focus_templates
        await ensure_focus_templates(db)
        from seed_marketplace_focus import ensure_marketplace_focus
        ensure_marketplace_focus()

    # Only the dedicated scheduler service owns APScheduler in production.
    if settings.scheduler_enabled:
        await scheduler_service.start_scheduler()

    yield

    if settings.scheduler_enabled:
        await scheduler_service.stop_scheduler()


app = FastAPI(title="FinOps ETL Platform", version="0.1.0", lifespan=lifespan)

from app.core.security_headers_middleware import SecurityHeadersMiddleware
from app.core.audit_middleware import AuditCaptureMiddleware
from app.core.authz_middleware import RBACMiddleware

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(AuditCaptureMiddleware)
app.add_middleware(RBACMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(NavigationPolicyMiddleware)

from app.core.route_aliases import RouteAliasMiddleware  # noqa: E402
app.add_middleware(RouteAliasMiddleware)  # mais externo: autorização e auditoria veem o caminho canônico
app.include_router(modules_router.router)
app.include_router(auth_router.router)
app.include_router(users_router.router)
app.include_router(groups_router.router)
app.include_router(roles_router.router)
app.include_router(audit_router.router)
app.include_router(sso_router.router)
app.include_router(sso_providers_router.router)
app.include_router(connections.router)
app.include_router(connector_visibility_router.router)
app.include_router(csv_upload.router)
app.include_router(etl.router)
app.include_router(processing_logs.router)
app.include_router(rules.router)
app.include_router(periods.router)
app.include_router(analysis.router)
app.include_router(destinations.router)
app.include_router(schema.router)
app.include_router(forecast.router)
app.include_router(anomalies.router)
app.include_router(clone_rules.router)
app.include_router(planning.router)
app.include_router(savings.router)
app.include_router(azure_tag_policy.router)
app.include_router(gold.router)
app.include_router(reservations.router)
app.include_router(scheduled_shutdown.router)
app.include_router(azure_resource_graph.router)
app.include_router(azure_sources.router)
app.include_router(azure_mca_commitment.router)
app.include_router(integrations.router)
app.include_router(financial_contracts.router)
app.include_router(oci_operational.router)
app.include_router(ideas.router)
from app.routers import access as access_router  # noqa: E402
app.include_router(access_router.router)


@app.get("/saude")
async def health():
    return {"status": "ok"}
