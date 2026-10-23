"""Databricks (SQL Warehouse) billing/usage → Bronze layer extractor.

Connects to a Databricks SQL Warehouse using the official
``databricks-sql-connector`` and pulls workspace consumption data
(by default the ``system.billing.usage`` system table) into the Bronze
layer, one pseudo-blob per billing month (``databricks://<table>#month=YYYY-MM``).

The SQL is intentionally configurable: ``config_json.query`` replaces the
default ``SELECT * FROM system.billing.usage`` and ``config_json.month_column``
(default ``usage_date``) tells the extractor which column carries the usage
date used to derive billing months.

The access token comes from the connection: ``config_json.token`` /
``config_json.access_token`` (write-only, never returned by the API).
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.ingested_blob import IngestedBlob
from app.models.skipped_blob import SkippedBlob
from app.models.source_connection import SourceConnection
from app.schemas.schemas import BlobScanItem, BlobScanResult
from app.services import iceberg_service as iceberg

logger = logging.getLogger(__name__)

DEFAULT_QUERY = "SELECT * FROM system.billing.usage"
DEFAULT_MONTH_COLUMN = "usage_date"


def _serialize_cell(value: Any) -> Any:
    """Convert Databricks native types to JSON-safe values for Bronze TEXT columns."""
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str, ensure_ascii=False)
    if isinstance(value, bytes):
        return value.hex()
    return value


def _row_to_dict(row: Any, columns: list[str]) -> dict[str, Any]:
    return {col: _serialize_cell(val) for col, val in zip(columns, row)}


def _resolve_access_token(config: dict) -> str:
    token = config.get("token") or config.get("access_token")
    if not token:
        raise ValueError("Token Databricks nao configurado. Informe o Access Token na conexao.")
    return str(token).strip()


class DatabricksExtractor:
    def __init__(self, connection: SourceConnection):
        self.connection = connection
        self.config = connection.config_json or {}

    # ── configuração ────────────────────────────────────────────────────

    @property
    def server_hostname(self) -> str:
        value = self.config.get("server_hostname") or self.config.get("host")
        if not value:
            raise ValueError("config_json.server_hostname e obrigatorio (ex: adb-....azuredatabricks.net)")
        value = str(value).strip()
        # Credenciais são enviadas a este host: só workspaces Databricks conhecidos.
        from app.core.outbound import DATABRICKS_HOSTS, ensure_allowed_host

        try:
            ensure_allowed_host(value, DATABRICKS_HOSTS)
        except ValueError as exc:
            if not value.lower().startswith("dapi"):
                raise ValueError(f"server_hostname inválido: {exc}") from exc
        if value.lower().startswith("dapi"):
            raise ValueError(
                "Parece que o token foi preenchido no campo Server Hostname. "
                "Server Hostname e o endereco do workspace (ex: adb-7405612109211710.10.azuredatabricks.net). "
                "O token vai no campo Access Token da conexao."
            )
        return value

    @property
    def http_path(self) -> str:
        value = self.config.get("http_path")
        if not value:
            raise ValueError("config_json.http_path e obrigatorio (ex: /sql/1.0/warehouses/xxxx)")
        return str(value)

    @property
    def query(self) -> str:
        return str(self.config.get("query") or DEFAULT_QUERY).strip()

    @property
    def month_column(self) -> str:
        return str(self.config.get("month_column") or DEFAULT_MONTH_COLUMN).strip()

    @property
    def table_name(self) -> str:
        """Logical table alias used in blob paths (``databricks://<alias>#month=...``)."""
        return str(self.config.get("table_name") or "system.billing.usage").strip()

    # ── conexão ─────────────────────────────────────────────────────────

    @property
    def socket_timeout_seconds(self) -> float:
        """Tempo máx. (s) por tentativa de socket e janela total de retry.

        O conector Databricks usa 900s por padrão — inaceitável para um teste
        de conexão. Configurável via ``config_json.socket_timeout_seconds``.
        """
        try:
            return float(self.config.get("socket_timeout_seconds", 20))
        except (TypeError, ValueError):
            return 20.0

    def _connect(self):
        from databricks import sql as dbsql

        token = _resolve_access_token(self.config)
        logger.info("Conectando ao Databricks SQL Warehouse %s", self.server_hostname)
        return dbsql.connect(
            server_hostname=self.server_hostname,
            http_path=self.http_path,
            access_token=token,
            # Falha rápida: por padrão o conector espera até 900s por tentativa
            # e retenta por até 900s (cluster startup). Para scan/test/preview
            # de um warehouse bloqueado ou parado, 3 tentativas em ~20s bastam.
            _socket_timeout=self.socket_timeout_seconds,
            _retry_stop_after_attempts_count=3,
            _retry_stop_after_attempts_duration=self.socket_timeout_seconds,
            _retry_delay_min=1,
            _retry_delay_max=3,
            _retry_delay_default=1,
        )

    def _month_expr(self, column: str) -> str:
        # Databricks SQL: substr(CAST(ts AS STRING), 1, 7) -> YYYY-MM
        return f"substr(CAST(src.{column} AS STRING), 1, 7)"

    def _scan_query(self) -> str:
        return (
            "SELECT "
            f"{self._month_expr(self.month_column)} AS month, "
            "COUNT(*) AS row_count, "
            f"MAX(CAST(src.{self.month_column} AS TIMESTAMP)) AS last_modified "
            f"FROM ( {self.query} ) AS src "
            "GROUP BY 1 ORDER BY 1 DESC"
        )

    def _month_query(self, month: str) -> str:
        return (
            "SELECT * FROM ( "
            f"{self.query} "
            ") AS src "
            f"WHERE {self._month_expr(self.month_column)} = '{month}'"
        )

    def _preview_query(self, limit: int) -> str:
        return f"SELECT * FROM ( {self.query} ) AS src LIMIT {int(limit)}"

    def _blob_prefix(self) -> str:
        return f"databricks://{self.table_name}"

    def _blob_path(self, month: str) -> str:
        return f"{self._blob_prefix()}#month={month}"

    def parse_blob_path(self, blob_path: str) -> str:
        """Return the billing month from a Databricks pseudo-blob path."""
        if "#month=" not in blob_path:
            raise ValueError(f"Invalid Databricks blob path: {blob_path}")
        return blob_path.split("#month=", 1)[1]

    # ── execução de queries ─────────────────────────────────────────────

    def _execute(self, query: str) -> tuple[list[dict], list[str]]:
        """Run a query and return (rows as dicts, column names)."""
        conn = self._connect()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(query)
                columns = [desc[0] for desc in (cursor.description or [])]
                rows = []
                while True:
                    batch = cursor.fetchmany(20_000)
                    if not batch:
                        break
                    rows.extend(_row_to_dict(row, columns) for row in batch)
                return rows, columns
            finally:
                cursor.close()
        finally:
            conn.close()

    # ── scan ────────────────────────────────────────────────────────────

    def _scan_sync(self, session: Session) -> BlobScanResult:
        existing_blobs = {
            blob.blob_path: blob
            for blob in session.execute(
                select(IngestedBlob).where(IngestedBlob.connection_id == self.connection.id)
            ).scalars().all()
        }
        skipped_paths = {
            row[0]
            for row in session.execute(
                select(SkippedBlob.blob_path).where(SkippedBlob.connection_id == self.connection.id)
            )
        }

        month_rows, _ = self._execute(self._scan_query())
        now = datetime.now(timezone.utc)
        items: list[BlobScanItem] = []
        for row in month_rows:
            month = str(row.get("month") or "").strip()
            if not month:
                continue
            path = self._blob_path(month)
            row_count = int(row.get("row_count") or 0)
            last_modified = row.get("last_modified") or now
            if isinstance(last_modified, str):
                try:
                    last_modified = datetime.fromisoformat(last_modified)
                except ValueError:
                    last_modified = now
            if last_modified.tzinfo is None:
                last_modified = last_modified.replace(tzinfo=timezone.utc)

            if path in skipped_paths:
                status = "SKIPPED"
            else:
                existing = existing_blobs.get(path)
                if existing is None:
                    status = "NEW"
                elif existing.last_modified != last_modified or (existing.row_count or 0) != row_count:
                    status = "CHANGED"
                else:
                    status = "UNCHANGED"

            items.append(BlobScanItem(
                blob_path=path,
                blob_prefix=self._blob_prefix(),
                billing_period=month,
                last_modified=last_modified,
                content_length=None,
                row_count=row_count,
                status=status,
            ))

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

    async def scan(self, db: AsyncSession) -> BlobScanResult:
        from app.database import SyncSession

        session = SyncSession()
        try:
            return self._scan_sync(session)
        finally:
            session.close()

    # ── ingestão ────────────────────────────────────────────────────────

    def ingest_month(self, month: str, blob_path: str) -> tuple[int, list[str]]:
        """Query one billing month and append it to Bronze. Returns (row_count, errors)."""
        errors: list[str] = []
        conn = self._connect()
        total = 0
        try:
            cursor = conn.cursor()
            try:
                logger.info("Databricks: executando query do mes %s", month)
                cursor.execute(self._month_query(month))
                columns = [desc[0] for desc in (cursor.description or [])]
                while True:
                    batch = cursor.fetchmany(50_000)
                    if not batch:
                        break
                    rows = [_row_to_dict(row, columns) for row in batch]
                    df = pd.DataFrame(rows, columns=columns)
                    if df.empty:
                        continue
                    for col in df.columns:
                        df[col] = df[col].map(_serialize_cell)
                    total += iceberg.write_bronze(
                        self.connection.code, df, month, blob_path
                    )
            finally:
                cursor.close()
        except Exception as exc:  # noqa: BLE001
            logger.error("Databricks ingest failed for %s: %s", month, exc)
            errors.append(f"Databricks ingest failed for {blob_path}: {exc}")
        finally:
            conn.close()
        return total, errors

    # ── preview (header + amostra) ──────────────────────────────────────

    def preview(self, limit: int = 20) -> dict:
        """Return the raw header (column names) plus a sample of rows.

        Used by the Extraction screen so the header is known before any
        normalization/mapping decision is made.
        """
        rows, columns = self._execute(self._preview_query(limit))
        return {"columns": columns, "rows": rows[: int(limit)]}

    # ── teste ───────────────────────────────────────────────────────────

    def _warehouse_id(self) -> str | None:
        import re

        match = re.search(r"/sql/1\.0/(?:warehouses|endpoints)/([^/?#]+)", self.http_path)
        return match.group(1) if match else None

    def _workspace_state(self) -> dict:
        """Consulta o estado do SQL Warehouse via REST API (diagnostico rapido).

        O conector SQL so devolve 'Error during request to server.' quando o
        workspace bloqueia a rede ou o warehouse esta parado; esta checagem
        transforma isso em uma mensagem acionavel para o usuario.
        """
        import httpx

        warehouse_id = self._warehouse_id()
        token = _resolve_access_token(self.config)
        url = f"https://{self.server_hostname}/api/2.0/sql/warehouses/{warehouse_id}"
        try:
            with httpx.Client(timeout=10) as client:
                response = client.get(
                    url, headers={"Authorization": f"Bearer {token}"}
                )
        except Exception as exc:  # noqa: BLE001
            return {"state": "UNREACHABLE", "message": f"API do workspace inacessivel: {str(exc)[:200]}"}

        if response.status_code == 200:
            data = response.json()
            return {
                "state": str(data.get("state") or "?"),
                "message": f"SQL Warehouse '{data.get('name') or warehouse_id}' esta {data.get('state')}.",
            }
        if response.status_code == 403:
            body = response.text
            if "Unauthorized network access" in body:
                return {
                    "state": "IP_BLOCKED",
                    "message": (
                        "O workspace bloqueia esta rede (IP allowlist). Libere o IP de saida "
                        "da maquina/API em Databricks -> Settings -> Access control -> IP access lists "
                        "(ou rode por dentro da VNet)."
                    ),
                }
            return {"state": "FORBIDDEN", "message": f"HTTP 403 do workspace: {body[:200]}"}
        if response.status_code == 401:
            return {
                "state": "UNAUTHORIZED",
                "message": "Token invalido ou sem permissao de acesso ao SQL Warehouse. Confira o token em .env-databriks.",
            }
        if response.status_code == 404:
            return {
                "state": "NOT_FOUND",
                "message": f"SQL Warehouse '{warehouse_id}' nao encontrado. Confira o HTTP Path da conexao.",
            }
        return {"state": f"HTTP_{response.status_code}", "message": response.text[:200]}

    def test_connection(self) -> dict[str, str]:
        try:
            state = self._workspace_state()
            try:
                rows, columns = self._execute("SELECT 1 AS ok")
                return {
                    "status": "OK",
                    "message": (
                        f"Databricks conectado: {self.server_hostname} · "
                        f"{state.get('message', '')} · query: {self.query[:60]} · colunas: {len(columns)}"
                    ),
                }
            except Exception as exc:  # noqa: BLE001
                detail = str(exc)[:200]
                if state.get("state") in ("IP_BLOCKED", "UNAUTHORIZED", "NOT_FOUND", "FORBIDDEN"):
                    return {"status": "FAILED", "message": state.get("message", detail)}
                if state.get("state") and state.get("state") not in ("RUNNING", "STARTING", "RESIZING", "UNREACHABLE", "?"):
                    return {
                        "status": "FAILED",
                        "message": f"{state.get('message', '')} SQL: {detail}",
                    }
                return {"status": "FAILED", "message": f"Falha na conexao SQL: {detail}"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "FAILED", "message": str(exc)[:500]}
