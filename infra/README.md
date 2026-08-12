# OpenFinops Infrastructure

Infrastructure configuration files for the data lakehouse stack.

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
| `minio`    | S3-compatible object storage    | 9000, 9001        | (always)  |
| `nessie`   | Iceberg catalog (versioning)    | 19120             | (always)  |
| `trino`    | Distributed SQL query engine    | 8080              | `analytics`|

### Start Commands

```bash
# Core infrastructure (PostgreSQL, MinIO, Nessie)
docker compose up -d postgres minio nessie

# Full stack with API and Web (dev)
docker compose up -d

# With Trino analytics engine
docker compose --profile analytics up -d

# Individual service
docker compose up -d minio
```

## Trino Configuration (`iceberg.properties`)

Configures Trino to use the Iceberg connector backed by Nessie catalog and MinIO S3 storage:

```properties
connector.name=iceberg
iceberg.catalog.type=nessie
iceberg.catalog.warehouse=s3://warehouse/
iceberg.catalog.uri=http://nessie:19120/api/v1

fs.native-s3.enabled=true
s3.endpoint=http://minio:9000
s3.access-key=minioadmin
s3.secret-key=minioadmin
s3.path-style-access=true
s3.region=us-east-1
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
  PyIceberg ──▶ MinIO S3 (Parquet files)
       │
       ▼
  Nessie Catalog (Iceberg table metadata, versioning, branches)
       │
       ▼
  Trino (SQL queries against Iceberg tables)
```

## Service Endpoints (Local Development)

| Service   | API/UI Endpoint          | Credentials         |
|-----------|--------------------------|---------------------|
| MinIO API | `http://localhost:9000`  | `minioadmin` / `minioadmin` |
| MinIO UI  | `http://localhost:9001`  | `minioadmin` / `minioadmin` |
| Nessie    | `http://localhost:19120/api/v1` | (none)              |
| Trino     | `http://localhost:8080`  | (none)              |
| PostgreSQL| `localhost:5432`         | `finops` / `finops` |
