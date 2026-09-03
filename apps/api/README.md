# OpenFinops API — Backend (FastAPI)

The backend is a **FastAPI** application that orchestrates ETL pipelines, manages configuration metadata, and serves data to the React frontend.

**Port:** 8000 (default)

## Directory Structure

```
apps/api/
├── Dockerfile
├── requirements.txt
├── seed_focus_v14.py            # Seed FOCUS v1.4 destination table metadata
├── backfill_mappings.py          # One-shot: backfill EtlMapping from BusinessRule
├── remap_rules_to_dest.py        # One-shot: remap rule columns to destination
├── import_atlas_keys.py          # One-shot: import MongoDB Atlas keys from .env
│
├── migrations/                   # SQL migration scripts
│   ├── add_skipped_blob.sql
│   ├── add_destination_tables.sql
│   ├── focus_v1_4_recreate.sql
│   ├── fix_destination_columns.sql
│   └── add_rows_destination.sql
│
└── app/
    ├── main.py                   # FastAPI app entrypoint, lifespan, router registration
    ├── config.py                 # Settings from environment variables
    ├── database.py               # SQLAlchemy async + sync engines
    ├── seed.py                   # Initial data seeder
    │
    ├── routers/                  # API endpoints (11 routers)
    ├── services/                 # Business logic (9 services)
    ├── models/                   # SQLAlchemy ORM models (10 files)
    └── schemas/                  # Pydantic request/response schemas
```

## Routers — API Endpoints

| Router             | Prefix              | Purpose                                               |
|--------------------|---------------------|-------------------------------------------------------|
| `connections.py`   | `/connections`      | CRUD for source cloud connections                     |
| `etl.py`           | `/etl`              | ETL scan, run, cancel, status, reprocess, schedules    |
| `rules.py`         | (various)           | Mappings, business rules, classifications, departments |
| `analysis.py`      | `/analysis`         | Trino/Iceberg querying, source & destination data      |
| `fact.py`          | `/fact`             | Fact table populate, query, summary, export            |
| `schema.py`        | `/schema`           | Schema discovery from ingested data                    |
| `periods.py`       | `/etl/periods`      | Billing period lifecycle (open/close/auto-close)       |
| `destinations.py`  | `/destinations`     | Destination tables, columns, source-destination mappings |

### Key Endpoints Detail

**ETL Pipeline** (`routers/etl.py`):
- `POST /etl/connections/{id}/scan` — Scan Azure Blob for new files
- `POST /etl/run` — Start ETL task (background thread)
- `GET /etl/status/{task_id}` / `POST /etl/cancel/{task_id}` — Monitor/cancel tasks
- `POST /etl/reprocess` — Reprocess data into destination tables (Iceberg)
- `GET/POST/PUT/DELETE /etl/schedules` — Cron-based scheduled ETL management

**Analysis** (`routers/analysis.py`):
- `POST /analysis/query` — Execute arbitrary SQL via Trino against Iceberg
- `GET /analysis/tables` — List all Iceberg tables (bronze/silver/gold)
- `GET /analysis/destinations` — List destination tables with row counts (Trino)
- `GET /analysis/destinations/{id}/data` — Query destination table data (Trino)

**Rules** (`routers/rules.py`):
- `GET/POST/PUT/DELETE /business-rules/` — Business rule CRUD
- `GET/POST/PUT/DELETE /classifications/` — Classification rule CRUD
- `POST /departments/import` — CSV import (departments, projects, rules)

## Services — Business Logic

| Service                      | Purpose                                                          |
|------------------------------|------------------------------------------------------------------|
| `azure_extractor.py`         | Azure Blob CSV/Parquet extraction, parse, normalize              |
| `task_runner.py`             | Background ETL execution via ThreadPoolExecutor                  |
| `scheduler_service.py`       | APScheduler cron jobs + auto-reprocess destinations              |
| `classification_engine.py`   | Multi-condition classification matching + department inheritance |
| `expression_engine.py`       | Formula/Conditional business rule evaluation via pandas.eval()   |
| `iceberg_service.py`         | PyIceberg read/write (bronze/silver/gold) via PostgreSQL + warehouse local |
| `trino_service.py`           | Trino HTTP client for SQL queries                                |
| `fact_service.py`            | Populate fact_cost from normalized data                          |
| `period_service.py`          | Billing period lifecycle management                              |

### Iceberg Service — Data Lakehouse Layers

```python
from app.services import iceberg_service as iceberg

# Bronze: raw ingested data
iceberg.write_bronze(connection_code, df, billing_period)

# Silver: processed/normalized data
iceberg.write_silver(connection_code, df, billing_period)

# Gold: destination tables (dynamic schema, any physical_name)
iceberg.write_destination(physical_name, df, partition_col="_billing_period")
```

Each layer writes **Parquet files** to the shared local Iceberg warehouse,
cataloged by the PostgreSQL SQL catalog.

## Models — Database Schema

| Model File             | Table(s)                       | What it stores                                         |
|------------------------|--------------------------------|--------------------------------------------------------|
| `source_connection.py` | `source_connection`            | Cloud provider connection configs                       |
| `ingested_blob.py`     | `ingested_blob`                | Processed file records                                 |
| `skipped_blob.py`      | `skipped_blob`                 | Files excluded from processing                         |
| `cost_lines.py`        | legacy SQL row tables (unused by Azure ETL) | Deprecated row storage; financial data lives in Iceberg |
| `rules.py`             | `etl_mapping`, `department`, `project`, `business_rule`, `classification_rule` | ETL config + org hierarchy |
| `fact_cost.py`         | `fact_cost`                    | Normalized fact table (legacy)                         |
| `destination.py`       | `destination_table`, `destination_column`, `source_destination_mapping` | Destination table metadata    |
| `etl_task.py`          | `etl_task`                     | ETL job status tracking                                |
| `period_status.py`     | `period_status`                | Billing period lifecycle                               |
| `scheduled_task.py`    | `scheduled_task`               | Cron-based automation schedules                        |

**Data Storage Principle:** PostgreSQL stores application metadata, job control
and the Iceberg catalog. Bronze keeps the immutable source; Silver maps sources
to destination schemas; Gold applies business rules/classifications. Financial
rows are stored as Iceberg/Parquet in the shared local warehouse.

## Environment Variables

Configured in `.env` files (loaded by `app/config.py`):

| Variable              | Default                                  | Description                    |
|-----------------------|------------------------------------------|--------------------------------|
| `DATABASE_URL`        | `postgresql+asyncpg://finops:finops@localhost:5432/finops` | Async DB URL |
| `DATABASE_URL_SYNC`   | `postgresql://finops:finops@localhost:5432/finops` | Sync DB URL (ETL) |
| `ICEBERG_LOCAL_WAREHOUSE` | `apps/data/iceberg`                 | Shared local warehouse          |
