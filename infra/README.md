# OpenFinops Infrastructure

Infrastructure configuration files for the PostgreSQL + local Iceberg
lakehouse stack backed by PostgreSQL and the shared local Iceberg warehouse.

## Directory Structure

```
infra/
└── trino/
    └── catalog/
        └── iceberg.properties    # Trino → Iceberg connector configuration
```

## Docker Compose Services

The full infrastructure is defined in the root `docker-compose.yml`. Each service can be started independently:

| Service    | Purpose                         | Port(s)           | Profile    |
|------------|---------------------------------|-------------------|------------|
| `postgres` | Metadata store (rules, mappings, connections) | 5432 | (always) |
| `api`      | FastAPI backend                 | 8000              | (always)  |
| `web`      | React/Vite frontend             | 5173              | (always)  |
| `trino`    | Distributed SQL query engine    | 8080              | `analytics`|
| `trino-worker` | Trino query workers          | internal          | (always) |

### Start Commands

```bash
# Full stack with API and Web (dev)
docker compose up -d
```

## Trino Configuration (`iceberg.properties`)

Configures Trino to use the Iceberg JDBC catalog stored in PostgreSQL and the
shared local warehouse:

```properties
connector.name=iceberg
iceberg.catalog.type=jdbc
iceberg.jdbc-catalog.connection-url=jdbc:postgresql://host.docker.internal:5433/finops
iceberg.jdbc-catalog.default-warehouse-dir=file:///Users/fernandohenriquebenatti/projetos/OpenFinops/apps/data/iceberg
fs.local.enabled=true
fs.hadoop.enabled=true
```

Once running, you can query Iceberg tables in Trino:

```sql
-- List tables in a namespace
SHOW TABLES IN iceberg.gold;

-- Query a destination table
SELECT * FROM iceberg.gold.dest_modelo_focus LIMIT 10;

-- Aggregate analysis
SELECT 
  cloud_origem,
  COUNT(*) AS total_rows,
  SUM(CAST(EffectiveCost AS DOUBLE)) AS total_cost
FROM iceberg.gold.dest_modelo_focus
GROUP BY cloud_origem;
```

## Data Flow

```
Azure/GCP/MongoDB
       │
       ▼
  [ETL Pipeline] ──▶ PostgreSQL (metadata only: rules, mappings, departments)
       │
       ▼
  PyIceberg ──▶ Local warehouse (Parquet files)
       │
       ▼
  PostgreSQL SQL catalog (Iceberg metadata)
       │
       ▼
  Trino (SQL queries against Iceberg tables)
```

## Service Endpoints (Local Development)

| Service   | API/UI Endpoint          | Credentials         |
|-----------|--------------------------|---------------------|
| Trino     | `http://localhost:8080`  | (none)              |
| PostgreSQL| `localhost:5432`         | `finops` / `finops` |
