# 00 — Visão Geral do Sistema

## O que é

**OpenFinops** é uma plataforma FinOps multi-cloud que:

1. **Ingere** dados de billing de Azure Blob Storage (CSV/Parquet), GCP BigQuery e MongoDB Atlas;
2. **Normaliza e enriquece** através de engines de mapeamento, regras de negócio e classificação organizacional;
3. **Armazena** os resultados em um data lakehouse (Apache Iceberg/Parquet no warehouse local compartilhado, catalogado no PostgreSQL);
4. **Analisa** via engine SQL Trino (pivot tables, queries livres, drill-down);
5. **Projeta** custos futuros com um motor de Forecast metadata-driven.

## Princípio arquitetural fundamental

> **PostgreSQL armazena SOMENTE metadata e controle de jobs.**
> Dados financeiros (linhas de custo) vivem exclusivamente em Iceberg/Parquet.

Isso vale também para o Forecast: `forecast_config`, `forecast_adjustment`,
`forecast_business_rule` e `forecast_run` guardam configuração, controle e auditoria;
os resultados financeiros projetados são gravados no namespace Iceberg `forecast`.

## Stack tecnológica

| Componente | Tecnologia |
|---|---|
| Backend | FastAPI, SQLAlchemy (async + sync), Pandas, PyIceberg |
| Frontend | React, TypeScript, Vite, Material UI, react-pivottable, React Flow |
| Metadata DB | PostgreSQL 16 |
| Data Lake | Apache Iceberg/Parquet no warehouse local + catálogo SQL PostgreSQL |
| Query Engine | Trino |
| ETL/Agendamento | Pandas, ThreadPoolExecutor (2 workers), APScheduler (cron) |
| Infra | Docker Compose |

## Estrutura de diretórios

```
OpenFinops/
├── docker-compose.yml          # postgres, api, worker, web e Trino
├── start-local.sh              # sobe API (8001) + worker + web (5173) sem Docker
├── FORECAST.md                 # design do motor de Forecast (fonte de verdade)
├── apps/
│   ├── api/                    # FastAPI backend
│   │   ├── app/
│   │   │   ├── main.py         # app FastAPI, lifespan (seed, migrations leves, scheduler)
│   │   │   ├── routers/        # connections, etl, rules, periods, analysis, destinations,
│   │   │   │                   # schema, forecast, clone_rules, fact
│   │   │   ├── services/       # azure_extractor, task_runner, expression_engine,
│   │   │   │                   # classification_engine, clone_engine, forecast_service,
│   │   │   │                   # iceberg_service, trino_service, scheduler_service,
│   │   │   │                   # fact_service, period_service
│   │   │   ├── models/         # SQLAlchemy ORM (metadata PostgreSQL)
│   │   │   └── schemas/        # Pydantic schemas
│   │   ├── worker.py           # processo worker separado (fila etl_task)
│   │   └── alembic/            # (migrações formais ainda incipientes; ver nota abaixo)
│   └── web/                    # React/Vite frontend
│       └── src/
│           ├── pages/          # 14 páginas
│           ├── components/     # PivotAnalysis, SortableTable, SourceSearch, EmptyState
│           ├── layout/         # Layout com banner global de tarefas
│           └── services/api.ts # cliente axios (baseURL /api)
└── infra/
    └── trino/catalog/iceberg.properties  # Trino → Iceberg (JDBC PostgreSQL)
```

## Como rodar

### Opção 1 — Local sem Docker

`start-local.sh` sobe três processos e supervisiona o worker (reinicia até 3x):

- API: `uvicorn app.main:app` na porta **8001**
- Worker: `python worker.py` (polling da fila a cada 2s, heartbeat em `/tmp/openfinops-worker.heartbeat`)
- Web: `npm run dev` na porta **5173**

Logs em `.runtime-logs/`. O `iceberg_service` usa catálogo SQL PostgreSQL com
warehouse em `./data/iceberg/`.

### Opção 2 — Com Docker

```bash
docker compose up -d postgres
cd apps/api && pip install -r requirements.txt && python -m uvicorn app.main:app --port 8000
cd apps/web && npm install && npx vite --port 5173
docker compose up -d trino trino-worker          # :8080
```

## Startup da API (`apps/api/app/main.py`, lifespan)

1. `Base.metadata.create_all` (não evolui schemas — ver abaixo).
2. Migrações leves idempotentes inline (ALTER TABLE ... IF NOT EXISTS) para colunas
   de snapshot do `forecast_run` e recriação da FK `forecast_run → forecast_config`
   com `ON DELETE SET NULL`.
3. Backfill de `config_snapshot` para runs históricas sem snapshot.
4. `seed()` + `ensure_default_config()` (perfil padrão de forecast) +
   `ensure_focus_forecast_rules()` + `ensure_focus_templates()` (templates de clone FOCUS).
5. Inicia o APScheduler (`scheduler_service.start_scheduler()`), que carrega todos os
   `scheduled_task` ativos do PostgreSQL.

## Processos em execução

| Processo | Papel |
|---|---|
| **API (uvicorn)** | REST, scheduler APScheduler embarcado, proxy de queries Trino |
| **Worker (`worker.py`)** | Consome fila `etl_task` (tipos `extraction`, `reprocess`, `forecast`); lock exclusivo via `fcntl` (`/tmp/openfinops-worker.lock`); heartbeat fora do GIL via processo filho; marca tasks RUNNING órfãs como FAILED no boot |
| **Frontend (vite)** | SPA React; proxy `/api` → API |

## Features-chave

- Ingestão multi-cloud (Azure Blob CSV/Parquet, GCP BigQuery, MongoDB Atlas billing)
- Pipeline ETL: Extract → Transform (mappings, business rules, classificações, clones) → Load Iceberg
- Engine de regras de negócio: expressões aritméticas (`unitPrice * quantity * 13.83`) e condicionais if/then via `pandas.eval`
- Engine de classificação: pattern matching multi-condição (`=`, `~`, `!=`, `|` OR, `;` AND) com herança hierárquica de departamentos
- Clone Engine: gera linhas sintéticas de "Return" (estorno) para custos On-Demand sem billing
- Tabelas de destino dinâmicas (ex.: FOCUS v1.4) definidas pelo usuário
- Agendamento cron com auto-reprocess de destinos e forecast pós-ingestão
- Forecast com backtest automático (WAPE), janelas 7/14/28 dias, média ponderada, regras de compra/refund e ajustes dimensionais
- Análise SQL livre e pivot tables no navegador via Trino
- Pipeline Builder visual (React Flow)
