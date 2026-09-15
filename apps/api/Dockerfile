FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Full backups use pg_dump/pg_restore against the configured PostgreSQL.
# xmlsec1 is required by python3-saml to validate signed SAML assertions.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client xmlsec1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY apps/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY apps/api/app ./app
COPY apps/api/*.py ./
COPY apps/api/alembic.ini .
COPY apps/api/alembic ./alembic

# The worker is a separate service. Keep one API process so the scheduler
# lifecycle is not duplicated, and never enable the development file reloader.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
