FROM python:3.12-slim

# Full backups use pg_dump/pg_restore against the configured PostgreSQL.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY apps/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY apps/api/app ./app
COPY apps/api/*.py ./
COPY apps/api/alembic.ini .
COPY apps/api/alembic ./alembic

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
