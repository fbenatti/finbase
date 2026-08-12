# OpenFinops — Multi-Cloud FinOps Data Platform

**OpenFinops** is a multi-cloud FinOps ETL platform that ingests billing data from Azure, GCP, and MongoDB Atlas, normalizes and enriches it through business rules and classification engines, and stores the results in a **data lakehouse** (Iceberg/Parquet on MinIO S3) for analytical querying via Trino.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    FRONTEND (React + Vite)                    │
│  apps/web/src/pages/                                         │
│  Connections │ Extraction │ Rules │ Classification │ Analysis │
└──────────────────────┬───────────────────────────────────────┘
                       │ REST API (:5173 → proxy → :8000)
┌──────────────────────▼───────────────────────────────────────┐
│                    BACKEND (FastAPI)                          │
│  apps/api/app/                                               │
│  routers/  services/  models/  schemas/                      │
└──────┬──────────────────────┬────────────────┬───────────────┘
       │                      │                │
┌──────▼──────┐  ┌────────────▼─────┐  ┌───────▼──────────────┐
│  PostgreSQL │  │  MinIO S3        │  │  Nessie Catalog      │
│  (metadata) │  │  (data lake)     │  │  (Iceberg versioning)│
│             │  │  Parquet files   │  │                      │
│ connections │  │  bronze/         │  │  Table metadata      │
│ rules       │  │  silver/         │  │  Schema versions     │
│ mappings    │  │  gold/           │  │  Branches/tags       │
│ departments │  │                  │  │                      │
│ schedules   │  │                  │  │                      │
└─────────────┘  └──────────────────┘  └──────────────────────┘
                                               │
                                        ┌──────▼──────┐
                                        │    Trino    │
                                        │  (SQL query │
                                        │   engine)   │
                                        │  :8080      │
                                        └─────────────┘
```

### Data Medallion Architecture

| Layer   | Storage        | Tables                                    | Purpose                              |
|---------|----------------|-------------------------------------------|--------------------------------------|
| Bronze  | Iceberg/Parquet | `bronze.raw_{connection_code}`            | Raw ingested CSV/Parquet as-is       |
| Silver  | Iceberg/Parquet | `silver.{source}_{destination}`          | Source mapped to destination schema  |
| Gold    | Iceberg/Parquet | `gold.{destination_table}`                | Destination tables (FOCUS, custom)   |
| Gold    | Iceberg/Parquet | `gold.fact_cost`                          | Legacy fact table (analytics)        |
| Metadata| PostgreSQL      | `destination_table`, `business_rule`, etc.| Static metadata (rules, mappings)    |

**Key principle:** PostgreSQL stores ONLY metadata and job control. Bronze keeps the immutable source; Silver maps each source to a destination schema; Gold applies business rules/classifications. Financial rows live in **Iceberg/Parquet** on MinIO S3.

## Project Structure

```
OpenFinops/
├── docker-compose.yml          # Infrastructure orchestration
├── .env-4p, .env-atlas, .env-ti # Environment configs per connection
│
├── apps/
│   ├── api/                     # FastAPI backend
│   │   └── app/
│   │       ├── routers/         # API endpoints (11 routers)
│   │       ├── services/        # Business logic (9 services)
│   │       ├── models/          # SQLAlchemy ORM models (10 files)
│   │       └── schemas/         # Pydantic request/response schemas
│   │
│   └── web/                     # React/Vite frontend
│       └── src/
│           ├── pages/           # Page components (12 pages)
│           ├── components/      # Reusable UI components
│           └── services/        # API client (api.ts)
│
├── infra/                       # Infrastructure configs
│   └── trino/catalog/           # Trino→Iceberg connector config
│
└── scripts/                     # Utility scripts
```

## Quick Start

### Prerequisites
- Docker Desktop
- Python 3.10+
- Node.js 18+

### 1. Start Infrastructure
```bash
docker compose up -d postgres minio nessie
```

### 2. Start Backend API
```bash
cd apps/api
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 3. Start Frontend (Dev)
```bash
cd apps/web
npm install
npx vite --host 0.0.0.0 --port 5173
```

### 4. For Analytics (optional)
```bash
docker compose --profile analytics up -d trino
```

## Technology Stack

| Component    | Technology                              |
|--------------|-----------------------------------------|
| Backend      | FastAPI, SQLAlchemy, Pandas, PyIceberg  |
| Frontend     | React, TypeScript, Vite, Material UI    |
| Database     | PostgreSQL 16 (metadata)                |
| Data Lake    | MinIO S3, Apache Iceberg, Nessie        |
| Query Engine | Trino (optional, for SQL analytics)     |
| ETL          | Pandas, ThreadPoolExecutor, APScheduler |
| Infra        | Docker Compose                          |

## Module Documentation

- **[apps/api/](apps/api/README.md)** — Backend API (routers, services, models)
- **[apps/web/](apps/web/README.md)** — Frontend React application (pages, components)
- **[infra/](infra/README.md)** — Infrastructure configuration (Trino, Docker)

## Key Features

- **Multi-cloud ingestion:** Azure Blob Storage (CSV/Parquet), GCP BigQuery, MongoDB Atlas billing
- **ETL Pipeline:** Extract → Transform (mappings, business rules, classifications) → Load to Iceberg
- **Business Rules Engine:** Expression-based formulas (`unitPrice * quantity * 13.83`) and conditional rules (if/then) using pandas.eval()
- **Classification Engine:** Multi-condition pattern matching (= exact, ~ contains, | OR, ; AND) with department hierarchy inheritance
- **Scheduled ETL:** Cron-based automation via APScheduler with auto-reprocess of destination tables
- **SQL Analytics:** Trino SQL queries against Iceberg tables in the browser
- **Visual Pipeline Builder:** React Flow-based drag-and-drop pipeline visualization
- **Destination Tables:** Dynamic user-defined schemas (e.g., FOCUS v1.4) with columns from multiple sources
