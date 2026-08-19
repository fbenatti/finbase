# 01 — Arquitetura

## Diagrama de componentes

```
┌──────────────────────────────────────────────────────────────┐
│                 FRONTEND (React + Vite :5173)                │
│  Connections | Extraction | Mappings | Rules | CloneRules    │
│  Classification | Departments | Destinations | Relationship  │
│  Analysis | Explorer | Pipeline | Reprocess | Forecast       │
└──────────────────────┬───────────────────────────────────────┘
                       │ REST (/api → proxy vite → :8000/8001)
┌──────────────────────▼───────────────────────────────────────┐
│                   BACKEND (FastAPI)                          │
│  apps/api/app: routers / services / models / schemas         │
│                                                              │
│  + APScheduler embarcado (scheduled_task, cron)              │
└──────┬──────────────┬───────────────┬────────────┬───────────┘
       │              │               │            │
┌──────▼──────┐ ┌─────▼──────┐ ┌──────▼───────┐ ┌──▼───────────┐
│ PostgreSQL  │ │ MinIO S3   │ │ Nessie       │ │ Trino :8080  │
│ (metadata + │ │ (Parquet)  │ │ (catálogo    │ │ (SQL engine) │
│  fila jobs) │ │            │ │  Iceberg)    │ │              │
└──────▲──────┘ └────────────┘ └──────────────┘ └──────────────┘
       │
┌──────┴──────────────────────────────────────────────────────┐
│              WORKER (apps/api/worker.py)                     │
│  polling etl_task QUEUED a cada 2s — tipos:                  │
│  extraction | reprocess | forecast                           │
└──────────────────────────────────────────────────────────────┘
```

## Arquitetura Medallion (camadas de dados)

| Camada | Storage | Tabelas | Propósito |
|---|---|---|---|
| Bronze | Iceberg/Parquet | `bronze.raw_{connection_code}` | CSV/Parquet bruto, imutável, como ingerido |
| Silver | Iceberg/Parquet | `silver.{source}_{destination}` | Fonte mapeada ao schema de destino |
| Gold | Iceberg/Parquet | `gold.{destination_table}` | Destino final com regras/classificação/clones aplicados |
| Forecast | Iceberg/Parquet | `forecast.result_run_{id}` e planning | Resultados de forecast (snapshot imutável por run) |
| Metadata | PostgreSQL | `destination_table`, `business_rule`, etc. | Metadata estática e controle |

Funções relevantes: `apps/api/app/services/iceberg_service.py`
(`write_bronze`, `read_bronze_batches`, `write_silver`, `write_silver_destination`,
`write_destination_staged` / `swap_staged_table` / `append_to_staged`,
`write_forecast_result`, `write_forecast_planning`).

## Fluxo de dados end-to-end

### 1. Extração (Bronze)

- `POST /etl/connections/{id}/scan` lista blobs do Azure (`azure_extractor.py`),
  deriva `billing_period` do caminho e compara `etag`/`last_modified` com
  `ingested_blob` para classificar NEW/CHANGED/UNCHANGED.
- `POST /etl/run` enfileira `etl_task(task_type='extraction', status='QUEUED')`.
- O **worker** consome a task e executa `_run_etl_sync` (`task_runner.py`) num
  ThreadPoolExecutor (máx. 2 jobs): download CSV → pandas → grava
  `bronze.raw_{connection_code}` via Iceberg. Blobs ignorados manualmente ficam em
  `skipped_blob`. Progresso de download é persistido na própria linha `etl_task`
  (bytes por blob em `download_blobs` JSONB).

### 2. Reprocessamento para Destino (Silver → Gold)

- `POST /etl/reprocess` enfileira `etl_task(task_type='reprocess')` com
  `destination_table_id`, `connection_ids`, `billing_periods`.
- O worker executa `_reprocess_destination_sync` (`apps/api/app/routers/etl.py`),
  blob-a-blob, para economia de memória:
  1. Lê Bronze (`read_bronze_batches`).
  2. Aplica `SourceDestinationMapping` ativos (RENAME/COPY/FORMULA — `_apply_mapping_sync`).
  3. Aplica **CloneEngine** (`clone_engine.py`) — linhas sintéticas de estorno.
  4. Aplica **Business Rules** (`_apply_business_rule_sync`) — EXPRESSION, CONDITIONAL,
     PTAX_BRL, TAX_BASE, TAX_MARKETPLACE, MARKUP — com vigência `valid_from/until`.
  5. Aplica **Classificações** (`_apply_classifications_sync`) com hierarquia de departamentos — somente via `classification_rule.conditions` (sem lookup chumbado de projeto).
  6. Escreve em staging Iceberg (`write_destination_staged` / `append_to_staged`) e faz
     swap atômico ao final (`swap_staged_table`), com guarda de memória
     (`_memory_exceeded`) e gravação em lotes.
- Colunas alvo são as `destination_column` da `destination_table`; `tipo='realizado'`,
  `is_synthetic` e metadados de clone são adicionados quando aplicável.

### 3. Análise

- Pivot e queries SQL são executadas no **Trino** contra `iceberg.gold.{physical_name}`
  (`analysis.py`, `trino_service.py`). Fallback para leitura local do warehouse quando
  Trino indisponível (`_run_local_gold_query`).
- Views de pivot salvas em `pivot_view` (PostgreSQL).

### 4. Forecast

- Detalhado em [03-business-rules.md](03-business-rules.md) e `FORECAST.md`.
- Agregação diária é feita **no Trino** (nunca carrega Gold inteira em pandas);
  projeções em pandas sobre a série agregada; resultados gravados em
  `forecast.result_run_{run_id}` (snapshot imutável) + tabela planning.

### 5. Agendamento (scheduler_service.py)

- APScheduler (AsyncIOScheduler) dentro da API registra cron jobs de `scheduled_task`.
- Ao completar ETL agendado: se `destination_reprocess`, auto-reprocessa todos os
  destinos mapeados da conexão; se `config_json.forecast_after_ingestion` e houve linhas
  novas, enfileira forecast (`trigger_type='POST_INGESTION'`) — somente se config ativa,
  sem run concorrente e worker saudável.
- Fluxo automático completo: `Scan → novos arquivos → Bronze → Gold → Forecast`.

## Worker (`apps/api/worker.py`)

- Processo separado da API; lock exclusivo via `fcntl.flock` (evita workers duplos).
- Heartbeat em processo filho fora do GIL (`/tmp/openfinops-worker.heartbeat`) — a API
  verifica saúde do worker por esse arquivo (`GET /etl/worker-health`) e bloqueia
  forecasts quando offline (`_reconcile_dead_worker_tasks`).
- No boot, marca tasks RUNNING órfãs como FAILED ("Worker reiniciado").
- Polling FIFO (`ORDER BY id LIMIT 1`) sobre `status='QUEUED'`.
- Cancelamento via flags em memória (`_cancel_flags` em `task_runner.py`).

## Catálogo Iceberg — primário e fallback

`iceberg_service.py`:

- **Primário:** Nessie (`NESSIE_URI`, default `http://localhost:19120/api/v1`) +
  warehouse `s3://warehouse/` no MinIO (`S3_ENDPOINT`, default `:9000`).
- **Fallback:** SQL catalog sobre PostgreSQL + warehouse local `./data/iceberg/`
  (automático quando Nessie inalcançável, ou `ICEBERG_CATALOG_TYPE=sql`).

## Comunicação API ↔ Worker

- Fila: tabela `etl_task` (PostgreSQL) — única fonte de verdade do estado das tasks.
- Progresso: dicionário `_inmemory_tasks` (visibilidade imediata no processo que executa)
  + sincronização periódica para a linha `etl_task` (`_sync_db`, `_persist_download_*`),
  pois API e worker são processos distintos.
- Logs de reprocesso: `_append_reprocess_log` (result_json/phase da task).
