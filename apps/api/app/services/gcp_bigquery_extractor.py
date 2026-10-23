"""GCP BigQuery billing export → Bronze layer extractor."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.ingested_blob import IngestedBlob
from app.models.skipped_blob import SkippedBlob
from app.models.source_connection import SourceConnection
from app.schemas.schemas import BlobScanItem, BlobScanResult
from app.services import iceberg_service as iceberg

logger = logging.getLogger(__name__)


def _serialize_cell(value: Any) -> Any:
    """Convert BigQuery nested types to JSON-safe strings for Bronze TEXT columns."""
    if value is None:
        return None
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return value


def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _serialize_cell(val) for key, val in row.items()}


def _table_fqn(project_id: str, dataset_id: str, table_name: str) -> str:
    return f"{project_id}.{dataset_id}.{table_name}"


def _blob_prefix(project_id: str, dataset_id: str, table_name: str) -> str:
    return f"bq://{project_id}.{dataset_id}.{table_name}"


def _blob_path(project_id: str, dataset_id: str, table_name: str, invoice_month: str) -> str:
    return f"{_blob_prefix(project_id, dataset_id, table_name)}#invoice_month={invoice_month}"


# Business grain aligned with legacy realizado_gcp.py query_bigquery() (daily FinOps report level).
# One row per subscription folder + billing_account + project + country + usage day + service + sku + unit + cost_type + invoice month.
# Numeric fields (cost, usage, credits, cost_at_*) are SUMmed; dimension RECORDs use ANY_VALUE within the group.
_GCP_SUBSCRIPTION_ID_EXPR = """
    CASE
        WHEN ARRAY_LENGTH(src.project.ancestors) >= 3 THEN
            src.project.ancestors[SAFE_OFFSET(ARRAY_LENGTH(src.project.ancestors) - 2)].display_name
        WHEN ARRAY_LENGTH(src.project.ancestors) = 2 THEN
            src.project.ancestors[SAFE_OFFSET(1)].display_name
        ELSE src.billing_account_id
    END
"""

_GCP_AGGREGATE_GROUP_BY = f"""
    {_GCP_SUBSCRIPTION_ID_EXPR},
    src.billing_account_id,
    src.project.name,
    src.location.country,
    TIMESTAMP(DATE(src.usage_start_time)),
    src.service.description,
    src.sku.description,
    src.usage.unit,
    src.cost_type,
    src.invoice.month
"""


def _gcp_aggregated_select_sql(
    fqn: str,
    *,
    invoice_month_filter: bool,
    available_fields: set[str] | None = None,
) -> str:
    where_clause = "WHERE CAST(src.invoice.month AS STRING) = @invoice_month" if invoice_month_filter else ""
    fields = available_fields or set()
    resource_expr = (
        "ANY_VALUE(src.resource) AS resource"
        if "resource" in fields
        else "CAST(NULL AS STRUCT<name STRING, global_name STRING>) AS resource"
    )
    subscription_expr = (
        "ANY_VALUE(src.subscription) AS subscription"
        if "subscription" in fields
        else "CAST(NULL AS STRUCT<instance_id STRING>) AS subscription"
    )
    # Some billing exports (older tables, partial schemas) omit the
    # cost_at_* pricing columns. Reference them only when present so the
    # aggregation query never fails with "not found inside src".
    cost_at_list_expr = (
        "SUM(src.cost_at_list) AS cost_at_list"
        if "cost_at_list" in fields
        else "CAST(NULL AS FLOAT64) AS cost_at_list"
    )
    cost_at_default_expr = (
        "SUM(src.cost_at_effective_price_default) AS cost_at_effective_price_default"
        if "cost_at_effective_price_default" in fields
        else "CAST(NULL AS FLOAT64) AS cost_at_effective_price_default"
    )
    cost_at_consumption_expr = (
        "SUM(src.cost_at_list_consumption_model) AS cost_at_list_consumption_model"
        if "cost_at_list_consumption_model" in fields
        else "CAST(NULL AS FLOAT64) AS cost_at_list_consumption_model"
    )
    consumption_model_expr = (
        "ANY_VALUE(src.consumption_model) AS consumption_model"
        if "consumption_model" in fields
        else "CAST(NULL AS STRING) AS consumption_model"
    )
    return f"""
        SELECT
            src.billing_account_id AS billing_account_id,
            STRUCT(
                ANY_VALUE(src.service.id) AS id,
                src.service.description AS description
            ) AS service,
            STRUCT(
                ANY_VALUE(src.sku.id) AS id,
                src.sku.description AS description
            ) AS sku,
            TIMESTAMP(DATE(src.usage_start_time)) AS usage_start_time,
            MAX(src.usage_end_time) AS usage_end_time,
            STRUCT(
                ANY_VALUE(src.project.id) AS id,
                ANY_VALUE(src.project.number) AS number,
                src.project.name AS name,
                ANY_VALUE(src.project.labels) AS labels,
                ANY_VALUE(src.project.ancestry_numbers) AS ancestry_numbers,
                ANY_VALUE(src.project.ancestors) AS ancestors
            ) AS project,
            ANY_VALUE(src.labels) AS labels,
            ANY_VALUE(src.system_labels) AS system_labels,
            STRUCT(
                ANY_VALUE(src.location.location) AS location,
                src.location.country AS country,
                ANY_VALUE(src.location.region) AS region,
                ANY_VALUE(src.location.zone) AS zone
            ) AS location,
            {resource_expr},
            ANY_VALUE(src.tags) AS tags,
            ANY_VALUE(src.price) AS price,
            {subscription_expr},
            ANY_VALUE(src.transaction_type) AS transaction_type,
            ANY_VALUE(src.seller_name) AS seller_name,
            MAX(src.export_time) AS export_time,
            SUM(src.cost) AS cost,
            ANY_VALUE(src.currency) AS currency,
            ANY_VALUE(src.currency_conversion_rate) AS currency_conversion_rate,
            STRUCT(
                SUM(src.usage.amount) AS amount,
                src.usage.unit AS unit,
                SUM(src.usage.amount_in_pricing_units) AS amount_in_pricing_units,
                ANY_VALUE(src.usage.pricing_unit) AS pricing_unit
            ) AS `usage`,
            IF(
                IFNULL(SUM((SELECT SUM(c.amount) FROM UNNEST(src.credits) c)), 0) = 0,
                ARRAY<STRUCT<name STRING, type STRING, amount FLOAT64>>[],
                [STRUCT(
                    'aggregated' AS name,
                    'CREDIT' AS type,
                    SUM((SELECT SUM(c.amount) FROM UNNEST(src.credits) c)) AS amount
                )]
            ) AS credits,
            ANY_VALUE(src.invoice) AS invoice,
            src.cost_type AS cost_type,
            ANY_VALUE(src.adjustment_info) AS adjustment_info,
            {cost_at_list_expr},
            {cost_at_default_expr},
            {cost_at_consumption_expr},
            {consumption_model_expr}
        FROM `{fqn}` src
        {where_clause}
        GROUP BY
            {_GCP_AGGREGATE_GROUP_BY}
    """


class GcpBigQueryExtractor:
    def __init__(self, connection: SourceConnection):
        self.connection = connection
        self.config = connection.config_json or {}

    @property
    def project_id(self) -> str:
        return self.config["project_id"]

    @property
    def dataset_id(self) -> str:
        return self.config["dataset_id"]

    @property
    def table_names(self) -> list[str]:
        names: list[str] = []
        current = self.config.get("table_name")
        old = self.config.get("table_name_old")
        if current:
            names.append(current)
        if old and old not in names:
            names.append(old)
        return names

    def _get_client(self) -> bigquery.Client:
        token = self.config.get("token_json")
        if not token:
            raise ValueError("config_json.token_json is required for GCP BigQuery connections")
        if isinstance(token, str):
            info = json.loads(token)
        else:
            info = token
        credentials = service_account.Credentials.from_service_account_info(info)
        return bigquery.Client(project=self.project_id, credentials=credentials)

    def _canonical_columns(self, client: bigquery.Client) -> list[str]:
        columns: list[str] = []
        seen: set[str] = set()
        for table_name in self.table_names:
            table = client.get_table(_table_fqn(self.project_id, self.dataset_id, table_name))
            for field in table.schema:
                if field.name not in seen:
                    seen.add(field.name)
                    columns.append(field.name)
        return columns

    def _scan_table(self, client: bigquery.Client, table_name: str, existing_blobs: dict, skipped_paths: set[str]) -> list[BlobScanItem]:
        fqn = _table_fqn(self.project_id, self.dataset_id, table_name)
        prefix = _blob_prefix(self.project_id, self.dataset_id, table_name)
        query = f"""
            SELECT
                CAST(invoice_month AS STRING) AS invoice_month,
                COUNT(*) AS row_count,
                MAX(last_export) AS last_export
            FROM (
                SELECT
                    src.invoice.month AS invoice_month,
                    MAX(src.export_time) AS last_export
                FROM `{fqn}` src
                GROUP BY
                    {_GCP_AGGREGATE_GROUP_BY}
            )
            GROUP BY 1
            ORDER BY 1 DESC
        """
        items: list[BlobScanItem] = []
        for row in client.query(query).result():
            month = str(row.invoice_month or "")
            if not month:
                continue
            path = _blob_path(self.project_id, self.dataset_id, table_name, month)
            if path in skipped_paths:
                items.append(BlobScanItem(
                    blob_path=path,
                    blob_prefix=prefix,
                    billing_period=month,
                    last_modified=row.last_export or datetime.now(timezone.utc),
                    etag=None,
                    content_length=None,
                    row_count=int(row.row_count or 0),
                    status="SKIPPED",
                ))
                continue
            existing = existing_blobs.get(path)
            last_export = row.last_export
            if last_export and last_export.tzinfo is None:
                last_export = last_export.replace(tzinfo=timezone.utc)
            month_rows = int(row.row_count or 0)
            if existing is None:
                status = "NEW"
            elif existing.last_modified != last_export or (existing.row_count or 0) != month_rows:
                status = "CHANGED"
            else:
                status = "UNCHANGED"
            items.append(BlobScanItem(
                blob_path=path,
                blob_prefix=prefix,
                billing_period=month,
                last_modified=last_export or datetime.now(timezone.utc),
                etag=None,
                content_length=None,
                row_count=month_rows,
                status=status,
            ))
        return items

    async def scan(self, db: AsyncSession) -> BlobScanResult:
        from app.database import SyncSession
        session = SyncSession()
        try:
            return self._scan_sync(session)
        finally:
            session.close()

    def _scan_sync(self, session: Session) -> BlobScanResult:
        existing_blobs = {
            b.blob_path: b
            for b in session.execute(
                select(IngestedBlob).where(IngestedBlob.connection_id == self.connection.id)
            ).scalars().all()
        }
        skipped_paths = {
            row[0]
            for row in session.execute(
                select(SkippedBlob.blob_path).where(SkippedBlob.connection_id == self.connection.id)
            )
        }
        client = self._get_client()
        items: list[BlobScanItem] = []
        for table_name in self.table_names:
            items.extend(self._scan_table(client, table_name, existing_blobs, skipped_paths))
        stats = {"new": 0, "changed": 0, "unchanged": 0}
        for item in items:
            if item.status == "NEW":
                stats["new"] += 1
            elif item.status == "CHANGED":
                stats["changed"] += 1
            elif item.status == "UNCHANGED":
                stats["unchanged"] += 1
        return BlobScanResult(
            connection_id=self.connection.id,
            total=len(items),
            new=stats["new"],
            changed=stats["changed"],
            unchanged=stats["unchanged"],
            items=items,
        )

    def test_connection(self) -> dict[str, str]:
        client = self._get_client()
        table_name = self.table_names[0]
        fqn = _table_fqn(self.project_id, self.dataset_id, table_name)
        client.get_table(fqn)
        query = f"SELECT COUNT(*) AS n FROM `{fqn}` LIMIT 1"
        count = list(client.query(query).result())[0].n
        return {
            "status": "OK",
            "message": f"Connected to {fqn} ({count:,} rows total)",
        }

    def _serialize_dataframe(self, df: pd.DataFrame, canonical: list[str]) -> pd.DataFrame:
        if df.empty:
            return df.reindex(columns=canonical)
        for col in df.columns:
            df[col] = df[col].map(_serialize_cell)
        return df.reindex(columns=canonical)

    @staticmethod
    def _storage_api_denied(exc: BaseException) -> bool:
        msg = str(exc).lower()
        return "readsessions.create" in msg or ("403" in msg and "bigquery" in msg)

    def _write_bronze_chunks(
        self,
        result,
        canonical: list[str],
        invoice_month: str,
        blob_path: str,
        *,
        use_storage_api: bool,
        flush_every: int,
        refetch_result,
        credentials: Any,
    ) -> int:
        rows_written = 0
        buffer_frames: list[pd.DataFrame] = []
        buffer_rows = 0
        chunk_idx = 0
        use_storage = use_storage_api
        current_result = result

        def flush() -> None:
            nonlocal rows_written, chunk_idx, buffer_rows
            if not buffer_frames:
                return
            chunk_idx += 1
            merged = pd.concat(buffer_frames, ignore_index=True)
            rows_written += iceberg.write_bronze(self.connection.code, merged, invoice_month, blob_path)
            logger.info("Bronze chunk %s: %s linhas", chunk_idx, len(merged))
            buffer_frames.clear()
            buffer_rows = 0

        while True:
            try:
                if use_storage:
                    from google.cloud import bigquery_storage
                    bq_read = bigquery_storage.BigQueryReadClient(credentials=credentials)
                    batch_iter = current_result.to_arrow_iterable(bqstorage_client=bq_read)
                else:
                    batch_iter = current_result.to_arrow_iterable()
                for batch in batch_iter:
                    df = batch.to_pandas()
                    if df.empty:
                        continue
                    df = self._serialize_dataframe(df, canonical)
                    buffer_frames.append(df)
                    buffer_rows += len(df)
                    if buffer_rows >= flush_every:
                        flush()
                flush()
                return rows_written
            except Exception as exc:
                if use_storage and self._storage_api_denied(exc):
                    logger.warning(
                        "BigQuery Storage API negada (%s); reexecutando query com download padrao.",
                        exc,
                    )
                    use_storage = False
                    buffer_frames.clear()
                    buffer_rows = 0
                    chunk_idx = 0
                    rows_written = 0
                    current_result = refetch_result()
                    continue
                raise

    def ingest_month(self, table_name: str, invoice_month: str, blob_path: str) -> tuple[int, list[str]]:
        """Query one invoice month and write Bronze. Returns (row_count, errors)."""
        errors: list[str] = []
        client = self._get_client()
        fqn = _table_fqn(self.project_id, self.dataset_id, table_name)
        canonical = self._canonical_columns(client)
        table = client.get_table(fqn)
        available_fields = {field.name for field in table.schema}
        query = _gcp_aggregated_select_sql(
            fqn,
            invoice_month_filter=True,
            available_fields=available_fields,
        )
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("invoice_month", "STRING", invoice_month)]
        )

        query_job = client.query(query, job_config=job_config)
        logger.info("Aguardando conclusao da query BigQuery...")
        result = query_job.result()
        total_rows = result.total_rows

        def refetch_result():
            logger.info("Reexecutando query BigQuery para novo iterator...")
            return client.query(query, job_config=job_config).result()

        bqstorage_client = None
        use_storage_api = True
        try:
            from google.cloud import bigquery_storage  # noqa: F401 — probe optional dependency
        except Exception:
            use_storage_api = False

        flush_every = 250_000

        if total_rows is not None and total_rows <= flush_every:
            logger.info("Baixando %s linhas em um unico lote...", f"{total_rows:,}")
            df = None
            if use_storage_api:
                try:
                    df = query_job.to_dataframe(create_bqstorage_client=True)
                except Exception as exc:
                    if self._storage_api_denied(exc):
                        logger.warning(
                            "BigQuery Storage API negada (%s); reexecutando query com download padrao.",
                            exc,
                        )
                        use_storage_api = False
                        df = refetch_result().to_dataframe(create_bqstorage_client=False)
                    else:
                        raise
            else:
                df = query_job.to_dataframe(create_bqstorage_client=False)
            if df is not None and not df.empty:
                df = self._serialize_dataframe(df, canonical)
                rows_written = iceberg.write_bronze(self.connection.code, df, invoice_month, blob_path)
                logger.info("Bronze gravado: %s linhas em 1 append", rows_written)
                return rows_written, errors
            return 0, errors

        logger.info(
            "Baixando %s linhas em chunks...",
            f"{total_rows:,}" if total_rows is not None else "volume desconhecido",
        )
        rows_written = self._write_bronze_chunks(
            result,
            canonical,
            invoice_month,
            blob_path,
            use_storage_api=use_storage_api,
            flush_every=flush_every,
            refetch_result=refetch_result,
            credentials=client._credentials,
        )
        return rows_written, errors

    def parse_blob_path(self, blob_path: str) -> tuple[str, str]:
        """Return (table_name, invoice_month) from pseudo-blob path."""
        if "#invoice_month=" not in blob_path:
            raise ValueError(f"Invalid GCP blob path: {blob_path}")
        prefix, month = blob_path.split("#invoice_month=", 1)
        table_suffix = prefix.replace("bq://", "")
        for table_name in self.table_names:
            expected = f"{self.project_id}.{self.dataset_id}.{table_name}"
            if table_suffix == expected:
                return table_name, month
        raise ValueError(f"Could not resolve table from blob path: {blob_path}")
