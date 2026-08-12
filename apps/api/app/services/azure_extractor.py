import logging
import io
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from azure.storage.blob import BlobServiceClient
from azure.identity import ClientSecretCredential

from app.models.source_connection import SourceConnection
from app.models.ingested_blob import IngestedBlob
from app.models.rules import EtlMapping, BusinessRule, ClassificationRule
from app.services.period_service import can_process_period, ensure_period, auto_close_unchanged_periods
from app.schemas.schemas import BlobScanResult, BlobScanItem, EtlRunRequest, EtlRunStatus
from app.services import iceberg_service as iceberg


DATE_RANGE_RE = re.compile(r"(\d{8})-(\d{8})")
PERIOD_RE = re.compile(r"[_-](\d{6})(?:\.csv)?$")


def derive_billing_period(blob_path: str, df: pd.DataFrame | None = None) -> str | None:
    m = DATE_RANGE_RE.search(blob_path)
    if m:
        return m.group(1)[:6]

    m = PERIOD_RE.search(blob_path)
    if m:
        return m.group(1)

    if df is not None:
        for col in ["BillingPeriod", "BillingCurrency", "Date"]:
            if col in df.columns and not df[col].dropna().empty:
                val = str(df[col].dropna().iloc[0])
                if col == "Date":
                    return val[:7].replace("-", "")
                return val[:6]

    logger.warning(
        "Could not derive billing_period for blob=%s (no date range in path, no BillingPeriod/Date column in CSV)",
        blob_path,
    )
    return None


def derive_cost_type(blob_path: str) -> str:
    lower_path = blob_path.lower()
    if re.search(r"(?:^|/)amortized(?:-|/)", lower_path):
        return "amortized"
    if "-amortized" in lower_path:
        return "amortized"
    return "actual"


def _sanitize_df(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if df[col].dtype in ("float64", "float32"):
            mask = df[col].isna()
            if mask.any():
                df.loc[mask, col] = None
                df[col] = df[col].astype(object)
    return df


def _sanitize_records(records: list[dict]) -> list[dict]:
    for r in records:
        for k, v in list(r.items()):
            if isinstance(v, float) and (v != v):
                r[k] = None
    return records


class AzureExtractor:
    def __init__(self, connection: SourceConnection):
        self.connection = connection
        config = connection.config_json
        self.blob_prefixes = connection.blob_prefixes or config.get("blob_prefixes", [])

    def _get_blob_service_client(self):
        config = self.connection.config_json
        credential = ClientSecretCredential(
            tenant_id=config["tenant_id"],
            client_id=config["client_id"],
            client_secret=config["client_secret"],
        )
        account_url = f"https://{config['storage_account']}.blob.core.windows.net"
        return BlobServiceClient(
            account_url=account_url, credential=credential,
            connection_timeout=60,
            read_timeout=300,
        )

    async def scan(self, db: AsyncSession) -> BlobScanResult:
        blob_service = self._get_blob_service_client()
        container_name = self.connection.config_json["container_name"]

        result = await db.execute(
            select(IngestedBlob).where(
                IngestedBlob.connection_id == self.connection.id
            )
        )
        existing_blobs = {
            b.blob_path: b for b in result.scalars().all()
        }

        # Load skipped blob paths
        from app.models.skipped_blob import SkippedBlob
        skipped_result = await db.execute(
            select(SkippedBlob.blob_path).where(
                SkippedBlob.connection_id == self.connection.id
            )
        )
        skipped_paths = {row[0] for row in skipped_result}

        container_client = blob_service.get_container_client(container_name)
        items: list[BlobScanItem] = []
        stats = {"new": 0, "changed": 0, "unchanged": 0}

        for prefix in self.blob_prefixes:
            for blob in container_client.list_blobs(name_starts_with=prefix):
                blob_path = blob.name
                if not blob_path.endswith(".csv"):
                    continue

                last_modified = blob.last_modified.replace(tzinfo=timezone.utc)
                existing = existing_blobs.get(blob_path)

                # Mark skipped blobs with SKIPPED status instead of filtering
                if blob_path in skipped_paths:
                    status = "SKIPPED"
                    stats["unchanged"] += 1
                elif existing is None:
                    status = "NEW"
                    stats["new"] += 1
                elif last_modified > existing.last_modified.replace(tzinfo=timezone.utc):
                    status = "CHANGED"
                    stats["changed"] += 1
                else:
                    status = "UNCHANGED"
                    stats["unchanged"] += 1

                billing_period = derive_billing_period(blob_path, None)

                items.append(BlobScanItem(
                    blob_path=blob_path,
                    blob_prefix=prefix,
                    last_modified=last_modified,
                    content_length=getattr(blob, 'size', None),
                    status=status,
                    billing_period=billing_period,
                ))

        return BlobScanResult(
            connection_id=self.connection.id,
            total=len(items),
            new=stats["new"],
            changed=stats["changed"],
            unchanged=stats["unchanged"],
            items=items,
        )

    @staticmethod
    def _parse_csv_bytes(raw_bytes: bytes, blob_path: str = "") -> pd.DataFrame:
        """Parse a downloaded CSV using the same rules in every ETL path.

        Azure reports are not guaranteed to use the same encoding or delimiter.
        Parsing is deliberately done before any mapping so the resulting schema
        is the immutable source schema used by Bronze.
        """
        encodings = ["utf-8-sig", "utf-8", "utf-16", "latin-1"]

        def _try_read(enc: str, sep: str, dec: str, thou: str) -> pd.DataFrame | None:
            try:
                df = pd.read_csv(
                    io.BytesIO(raw_bytes), encoding=enc, sep=sep, decimal=dec,
                    thousands=thou, low_memory=False,
                )
                return _sanitize_df(df)
            except (UnicodeDecodeError, UnicodeError, pd.errors.ParserError):
                return None

        # Try multiple separator configurations; pick the one with most columns
        best_df: pd.DataFrame | None = None
        best_cols = 0
        candidates = [
            (";", ",", "."),   # Brazilian / European CSV (semicolon, comma decimal, dot thousands)
            (",", ".", ","),   # Standard / US CSV (comma separator, dot decimal, comma thousands)
            (",", ".", None),  # Standard without thousands
            (";", ".", None),  # Semicolon without thousands
        ]

        for enc in encodings:
            for sep, dec, thou in candidates:
                df = _try_read(enc, sep, dec, thou)
                if df is not None and len(df.columns) > best_cols:
                    best_df = df
                    best_cols = len(df.columns)
                    if best_cols > 1:
                        # Good enough — no need to try more encodings for this sep
                        pass

        if best_df is not None:
            df = best_df
        else:
            # Last resort: chardet
            try:
                import chardet
                detected = chardet.detect(raw_bytes)
                if detected and detected.get("encoding"):
                    for sep, dec, thou in candidates:
                        df = _try_read(detected["encoding"], sep, dec, thou)
                        if df is not None and len(df.columns) > best_cols:
                            best_df = df
                            best_cols = len(df.columns)
                df = best_df
            except ImportError:
                df = None

        if df is None:
            raise ValueError(f"Failed to decode CSV {blob_path!r} with any known encoding/separator")

        if len(df.columns) < 2:
            raise ValueError(
                f"CSV {blob_path!r} was parsed as a single column; delimiter detection failed"
            )
        if df.columns.duplicated().any():
            duplicates = df.columns[df.columns.duplicated()].tolist()
            raise ValueError(f"CSV {blob_path!r} contains duplicate columns: {duplicates}")

        # A raw Azure cost export must not already contain application-level
        # enrichments. This signature identifies the legacy derived report
        # that previously polluted the TI source schema.
        derived_markers = {"BusinessRule", "Projetos", "Diretoria"}
        if derived_markers.issubset(set(str(col).lstrip("\ufeff") for col in df.columns)):
            raise ValueError(
                f"CSV {blob_path!r} appears to be a legacy enriched report "
                "(BusinessRule/Projetos/Diretoria). It is not a raw source and was rejected."
            )

        logger.info(
            "CSV parsed: blob=%s rows=%d columns=%d schema=%s",
            blob_path, len(df), len(df.columns), list(df.columns),
        )
        return df

    async def _read_csv_blob(self, blob_client, blob_path: str = "") -> pd.DataFrame:
        stream = blob_client.download_blob()
        return self._parse_csv_bytes(stream.readall(), blob_path)

    async def _apply_mappings(self, df: pd.DataFrame, db: AsyncSession) -> dict[str, str]:
        result = await db.execute(
            select(EtlMapping).where(
                EtlMapping.active == True,
                (EtlMapping.connection_id == self.connection.id) | (EtlMapping.connection_id == None),
            ).order_by(EtlMapping.priority)
        )
        mappings = result.scalars().all()

        renames = [m for m in mappings if m.transform_type == "RENAME" and m.source_column in df.columns]
        transforms = [m for m in mappings if m.transform_type != "RENAME"]

        column_map = {}
        for m in renames:
            column_map[m.source_column] = m.target_column
        df.rename(columns=column_map, inplace=True)

        for m in transforms:
            if m.transform_type == "CONSTANT":
                df[m.target_column] = m.transform_config.get("value", "")
            elif m.transform_type == "CAST":
                source_col = m.source_column if m.source_column in df.columns else column_map.get(m.source_column, m.source_column)
                if source_col in df.columns:
                    df[m.target_column] = df[source_col].astype(
                        m.transform_config.get("dtype", "str")
                    )
            elif m.transform_type == "EXPRESSION":
                expr = m.transform_config.get("expression", "")
                if expr:
                    df[m.target_column] = df.eval(expr)

        return column_map

    async def _apply_business_rules(
        self, df: pd.DataFrame, billing_period: str, db: AsyncSession
    ):
        from app.services.expression_engine import evaluate_expression, evaluate_conditional

        result = await db.execute(
            select(BusinessRule)
            .where(
                BusinessRule.active == True,
                (BusinessRule.connection_id == self.connection.id) | (BusinessRule.connection_id == None),
            )
            .order_by(BusinessRule.priority)
        )
        rules = result.scalars().all()

        for rule in rules:
            if rule.valid_from and rule.valid_from > billing_period:
                continue
            if rule.valid_until and rule.valid_until < billing_period:
                continue

            # --- CONDITIONAL: if/then rules ---
            if rule.rule_type == "CONDITIONAL":
                conds = rule.conditions
                if not conds and isinstance(rule.config_json, dict):
                    conds = rule.config_json.get("conditions", [])
                if conds:
                    evaluate_conditional(df, conds)
                continue

            # --- EXPRESSION: formula with DataFrame columns as variables ---
            if rule.rule_type == "EXPRESSION":
                expr = rule.expression
                if not expr and isinstance(rule.config_json, dict):
                    expr = rule.config_json.get("expression", "")
                if expr:
                    evaluate_expression(df, expr, rule.target_column)
                continue

            # --- Legacy rule types ---
            cfg = rule.config_json or {}
            source_col = cfg.get("source_column", "")
            if source_col and source_col not in df.columns:
                continue

            if rule.rule_type == "PTAX_BRL":
                df[rule.target_column] = (
                    df[source_col]
                    .astype(float)
                    .mul(float(cfg.get("rate", 5.0)))
                )
            elif rule.rule_type == "TAX_BASE":
                rate = float(cfg.get("rate", 0.0))
                df[rule.target_column] = df[source_col].astype(float).mul(rate)
            elif rule.rule_type == "MARKUP":
                pct = float(cfg.get("percentage", 0.0))
                df[rule.target_column] = df[source_col].astype(float).mul(1 + pct)

    async def _apply_classifications(self, df: pd.DataFrame, db: AsyncSession):
        from app.services.classification_engine import apply_classification_rules

        await apply_classification_rules(
            df=df,
            db=db,
            connection_id=self.connection.id,
        )

    async def _process_blob(
        self,
        db: AsyncSession,
        blob_path: str,
        blob_prefix: str,
        last_modified: datetime,
        content_length: int | None,
        etag: str | None,
    ) -> tuple[int, int, list[str]]:
        errors: list[str] = []
        blob_service = self._get_blob_service_client()
        container_name = self.connection.config_json["container_name"]
        blob_client = blob_service.get_blob_client(
            container=container_name,
            blob=blob_path,
        )

        try:
            df = await self._read_csv_blob(blob_client, blob_path)
            # Save a raw copy for Iceberg Bronze before any transformations
            raw_df_bronze = df.copy()
        except Exception as e:
            errors.append(f"Failed to read blob {blob_path}: {e}")
            return 0, 0, errors

        billing_period = derive_billing_period(blob_path, df)
        cost_type = derive_cost_type(blob_path)

        existing_ingested = await db.execute(
            select(IngestedBlob).where(
                IngestedBlob.connection_id == self.connection.id,
                IngestedBlob.blob_path == blob_path,
            )
        )
        existing = existing_ingested.scalar_one_or_none()

        if existing:
            await db.delete(existing)
            await db.flush()

        ingested = IngestedBlob(
            connection_id=self.connection.id,
            blob_path=blob_path,
            blob_prefix=blob_prefix,
            billing_period=billing_period,
            last_modified=last_modified,
            etag=etag or "",
            content_length=content_length or 0,
            row_count=len(df),
            status="INGESTED",
        )
        db.add(ingested)
        await db.flush()

        # Cost rows are not persisted in PostgreSQL. IngestedBlob is metadata
        # only; the source snapshot is written to Bronze below.
        raw_count = len(df)

        # --- Iceberg Bronze: write raw data immediately after download ---
        try:
            if not raw_df_bronze.empty and billing_period:
                iceberg.write_bronze(
                    connection_code=self.connection.code,
                    df=raw_df_bronze,
                    billing_period=billing_period,
                    blob_path=blob_path,
                )
                logger.info("Iceberg Bronze: %s / %s — %d rows", self.connection.code, billing_period, len(raw_df_bronze))
        except Exception as exc:
            logger.warning("Iceberg Bronze write skipped (%s): %s", blob_path, exc)
            await db.delete(ingested)
            await db.flush()
            return raw_count, 0, [f"Bronze write failed: {exc}"]

        # Mapping, normalização, regras e classificações são executados
        # exclusivamente no reprocessamento de uma tabela de destino. A
        # extração não pode mais criar Silver por conexão (ex.:
        # silver.normalized_azure_ti).
        return raw_count, 0, errors

    async def run_full_pipeline(
        self, db: AsyncSession, request: EtlRunRequest
    ) -> EtlRunStatus:
        scan_result = await self.scan(db)

        force_set = set(request.force_periods or [])
        closed = await auto_close_unchanged_periods(db, self.connection.id)

        to_process: list[BlobScanItem] = []
        skipped_closed: list[str] = []

        for item in scan_result.items:
            if item.status in ("NEW", "CHANGED"):
                is_forced = item.billing_period is not None and item.billing_period in force_set
                if not is_forced and item.billing_period:
                    ok = await can_process_period(
                        db, self.connection.id, item.billing_period, force=False
                    )
                    if not ok:
                        skipped_closed.append(item.blob_path)
                        continue
                to_process.append(item)

        if force_set:
            additional = [
                item for item in scan_result.items
                if item.status == "UNCHANGED"
                and item.billing_period in force_set
            ]
            to_process.extend(additional)

        if request.selected_blobs:
            to_process = [item for item in to_process if item.blob_path in request.selected_blobs]

        total_raw = 0
        total_norm = 0
        all_errors: list[str] = []

        for item in to_process:
            if item.billing_period:
                await ensure_period(db, self.connection.id, item.billing_period)

            raw, norm, errs = await self._process_blob(
                db, item.blob_path, item.blob_prefix,
                item.last_modified, item.content_length, None,
            )
            total_raw += raw
            total_norm += norm
            all_errors.extend(errs)

        if skipped_closed:
            all_errors.append(
                f"Skipped {len(skipped_closed)} blob(s) in CLOSED periods. "
                f"Use force_periods to reprocess: {skipped_closed[:3]}..."
            )

        await db.commit()

        return EtlRunStatus(
            connection_id=self.connection.id,
            status="COMPLETED" if not all_errors else "COMPLETED_WITH_ERRORS",
            blobs_processed=len(to_process),
            rows_raw=total_raw,
            rows_normalized=total_norm,
            errors=all_errors,
        )
