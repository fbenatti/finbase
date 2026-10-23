# 02 — Modelo de Dados

Duas camadas distintas:

- **PostgreSQL** — metadata, configurações, controle de jobs. NUNCA linhas financeiras detalhadas.
- **Iceberg/Parquet** — dados financeiros (Bronze/Silver/Gold/Forecast).

## 2.1 Tabelas PostgreSQL (metadata)

### Ingestão e conexões

| Tabela | Modelo | Descrição |
|---|---|---|
| `source_connection` | `source_connection.py:SourceConnection` | Conexão de origem (Azure Blob, GCP, Atlas). Campos: `code`, `name`, `connector_type`, `cloud_name`, `config_json` (container, credenciais), `blob_prefixes` |
| `ingested_blob` | `ingested_blob.py:IngestedBlob` | Registro por blob ingerido: `blob_path`, `billing_period`, `etag`, `last_modified`, `content_length`, `row_count`, `status`. Usado para detecção de NEW/CHANGED no scan |
| `skipped_blob` | `skipped_blob.py:SkippedBlob` | Blobs ignorados manualmente (não serão ingeridos) |
| `raw_cost_line` | `cost_lines.py:RawCostLine` | Linha bruta (legado da fase pré-Iceberg) |
| `normalized_cost_line` | `cost_lines.py:NormalizedCostLine` | Linha normalizada em JSONB (`data`), com `billing_period`, `cost_type` (actual/amortized). Alimenta `fact_cost` legado |

### Destinos e mapeamento

| Tabela | Modelo | Descrição |
|---|---|---|
| `destination_table` | `destination.py:DestinationTable` | Tabela de destino lógica (ex.: MODELO_FOCUS). `name`, `physical_name` (nome físico na Gold), `active` |
| `destination_column` | `destination.py:DestinationColumn` | Coluna do destino: `column_name`, `data_type`, `is_required`, `position`, `default_value`. Única por (tabela, coluna) |
| `source_destination_mapping` | `destination.py:SourceDestinationMapping` | Mapeia coluna origem→destino por conexão: `source_column`, `destination_column`, `transform_type` (RENAME/COPY/FORMULA), `transform_config`, `priority`. Única por (conexão, destino, col. origem, col. destino) |

### Regras e classificação (`models/rules.py`)

| Tabela | Descrição |
|---|---|
| `etl_mapping` | Mapeamento legado por conexão (`source_column → target_column`, transform_type) |
| `business_rule` | Regra de negócio: `target_column`, `rule_type` (EXPRESSION, CONDITIONAL, PTAX_BRL, TAX_BASE, TAX_MARKETPLACE, MARKUP), `config_json`, `conditions` (JSON), `expression`, `priority`, vigência `valid_from`/`valid_until` (YYYYMM), escopo por `cloud_scope` (`azure`, `gcp`, `atlas`, nulo = todas) e/ou `destination_table_id` |
| `classification_rule` | Regra de classificação: `source_field`, `pattern`, `pattern_type` (regex/contains/exact/multi_condition), `conditions` (JSON multi-condição), `match_value`, `target_field`, `department_id`, `project_id`, `priority`, `cloud_scope`. Índices por (department, priority) e (destination, priority) |
| `department` | Árvore hierárquica (até 5 níveis): `parent_id`, `name`, `centro_custo`, `ambiente`, `custom_fields` (JSON). A cloud de um nó é derivada do ramo do provedor na hierarquia (o antigo `cloud_filter` foi removido) |
| `project` | Projeto de um departamento: `projeto_name`, `cloud`, `subscription_rg` (mapeamento subscription/resource-group) |

### Clone engine (`models/clone_rule.py`)

| Tabela | Descrição |
|---|---|
| `clone_rule` | Regra de clone/estorno: `destination_table_id`, `connection_id`, `provider`, `conditions` (lista de {field, op, value}), `roles` (mapa de papéis de colunas: meter_id, quantity, unit_price, pricing_model, exchange_rate, billed_cost, effective_cost, on_demand_value), `original_changes`, `clone_changes` (vocabulário declarativo de transformações), vigência, `priority`, `version` |
| `clone_price_pendency` | Pendência quando não há preço On-Demand de referência: `meter_id`, `billing_period`, `reason` ("Preço On-Demand anterior não encontrado") |

### Forecast (`models/forecast.py`)

| Tabela | Descrição |
|---|---|
| `forecast_config` | Configuração versionada: `destination_table_id`, `measure_column`, `date_column`, `period_column`, `type_column` (default `tipo`), `dimensions` (JSON), `cutoff_days` (default 2), `horizon_months` (12), `growth_rate` (0.0083), `model_mode` (AUTO_BACKTEST), `enabled_windows` ([7,14,28]), `recent_weight` (0.7), `minimum_daily_points` (7), `null_dimension_label` ("Não classificado"), `version` |
| `forecast_adjustment` | Ajuste dimensional: `filters` (JSON), `adjustment_type` (PERCENTAGE, FIXED_VALUE, ADD_VALUE, SUBTRACT_VALUE, ZERO, CUSTOM_GROWTH), `percentage`, `fixed_value`, vigência, `priority` |
| `forecast_business_rule` | Regra operacional do forecast: `identification_column` + `identification_values`, `charge_type` (Purchase, Usage, Refund, Credit, Recurring commitment), `behavior` (REPEAT_NEXT_MONTH, RECURRING_MONTHLY, RECURRING_UNTIL_END, CANCEL_ON_REFUND, REPLACE_VALUE), `reference_column/values`, `replacement_value`, `growth_rate`, `cancel_on_refund`, `scope` (JSON por dimensões), vigência, `priority`, `version` |
| `forecast_run` | Auditoria imutável de execução: `forecast_config_id` (FK com ON DELETE SET NULL — run sobrevive à deleção da config), `config_version`, `trigger_type` (MANUAL/POST_INGESTION), `cutoff_date`, `status` (QUEUED/RUNNING/COMPLETED/FAILED/CANCELLED), `phase`, métricas (`total_series`, `processed_series`, `rows_written`, `selected_models`, `error_metrics`), `result_table`, **snapshot congelado** (`config_snapshot` JSONB, `destination_table_id_snapshot`, `destination_physical_name`) |

### Jobs e controle

| Tabela | Descrição |
|---|---|
| `etl_task` | Fila e estado de todas as tasks: `task_type` (extraction/reprocess/forecast), `status` (QUEUED/RUNNING/COMPLETED/FAILED/CANCELLED), `connection_id(s)`, `destination_table_id`, `billing_periods`, `selected_blobs`, contadores (`rows_raw`, `rows_normalized`, `rows_destination`), progresso de download (`download_blobs`, `downloaded_bytes_total`), `phase`, `result_json`, `errors` |
| `scheduled_task` | Agendamento cron: `name`, `cron_expression`, `timezone`, `connection_id`, `destination_reprocess` (bool), `config_json` (ex.: `forecast_config_id`, `forecast_after_ingestion`), `last_run_at`, `next_run_at` |
| `period_status` | Status de fechamento de período (YYYYMM) por conexão — gerenciado por `period_service.py` (open/closed, auto-close de períodos sem mudança) |
| `pivot_view` | Views de pivot salvas (config de linhas/colunas/medidas/filtros) |
| `fact_cost` | Tabela fato legada (pré-lakehouse): colunas analíticas (date, subscription, service, meter, cost_usd, cost_brl, tax_base, tax_marketplace, final_cost_brl, diretoria, department_path, centro_custo, ambiente, tags, extra JSONB). Populada por `fact_service.py` a partir de `normalized_cost_line` |

## 2.2 Namespaces e tabelas Iceberg

| Namespace | Tabela | Conteúdo |
|---|---|---|
| `bronze` | `raw_{connection_code}` | Dados brutos por conexão (Azure CSV ou GCP BigQuery `SELECT *`), append imutável |
| `silver` | `{source}_{destination}` | Resultado do mapeamento origem→destino |
| `gold` | `{destination_table.physical_name}` | Tabela final analítica. Colunas especiais adicionadas pelo pipeline: `tipo` ('realizado'), `is_synthetic`, `source_record_id`, e para clones: `clone_source_record_id`, `clone_rule_id`, `clone_rule_version`, `ondemand_reference_price`, `ondemand_reference_period`. Período em `_billing_period` (YYYYMM) |
| `forecast` | `result_run_{run_id}` | Snapshot imutável do resultado de uma run |
| `forecast` | planning (por tabela Gold) | Visão consolidada realizado+forecast publicada por `write_forecast_planning` |

### Schema do resultado de forecast

```
forecast_month   YYYYMM
forecast_value   double
tipo             'realizado' | 'forecast'
selected_method  'REALIZADO' | 'MEAN_7D' | 'MEAN_14D' | 'MEAN_28D' | 'WEIGHTED_7D_28D' | 'MEAN_AVAILABLE'
fallback_level   int
history_points   int
backtest_wape    double | null
+ colunas de dimensão configuradas
```

## 2.3 Mapa de campos JSON→Fato (legado)

`fact_service.JSON_TO_FACT_MAP` traduz chaves do JSONB normalizado para colunas da
`fact_cost` (ex.: `CostInBillingCurrency→cost_usd`, `PayGPrice→payg_cost_usd`,
`VL_CUSTO_FINAL_BRL→final_cost_brl`, `Diretoria→diretoria`). Chaves não mapeadas vão
para o JSONB `extra`; `Tags` vai para `tags`.

## 2.5 Mapeamento GCP → MODELO FOCUS

O seed `apps/api/seed_gcp_focus_mappings.py` aplica o mapeamento oficial Google/FOCUS
para as conexões `gcp-producao` e `gcp-dev`, cobrindo os 50 campos folha do export
`gcp_billing_export_resource_v1_*`. Campos canônicos FOCUS usam transforms
(`JSON_PATH`, `JSON_SUM`, `VALUE_MAP`, `MONTH_START/END`, `CONST`); campos sem
coluna FOCUS vão para colunas provider-specific `x_*` (26 colunas adicionais).
A chave de classificação GCP é `SubAccountId` ← `project.id` (via `JSON_PATH`).
Azure continua usando `source_resource_group_name` ← `resourceGroupName`.

## 2.6 Migrações

Não há Alembic funcional em produção — `main.py` aplica `create_all` + um pequeno
conjunto de `ALTER TABLE ... IF NOT EXISTS` idempotentes no lifespan (colunas de
snapshot do forecast_run e recriação da FK com ON DELETE SET NULL). O diretório
`apps/api/alembic/` existe mas a evolução de schema hoje é feita pelo startup.
