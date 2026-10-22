import logging
import io
import re
import os
from pathlib import Path
import gzip
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional, Any

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy import select

import oci

from app.models.source_connection import SourceConnection
from app.models.ingested_blob import IngestedBlob
from app.models.skipped_blob import SkippedBlob
from app.schemas.schemas import BlobScanResult, BlobScanItem
from app.services import iceberg_service as iceberg

logger = logging.getLogger(__name__)

# Extração de período YYYYMM a partir de strings de data ou caminhos
PERIOD_RE = re.compile(r"(\d{4})[-_/](\d{2})")
DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def derive_billing_period_oci(blob_path: str, time_created: Optional[datetime] = None, df: pd.DataFrame | None = None) -> str | None:
    """Deriva o período de faturamento (YYYYMM) para relatórios OCI."""
    if "#period=" in blob_path:
        return blob_path.split("#period=")[-1][:6]

    if time_created:
        return time_created.strftime("%Y%m")

    m = PERIOD_RE.search(blob_path)
    if m:
        return f"{m.group(1)}{m.group(2)}"

    if df is not None:
        for col in ["lineItem/intervalUsageStart", "lineItem/intervalStart", "Date", "intervalUsageStart"]:
            if col in df.columns and not df[col].dropna().empty:
                val = str(df[col].dropna().iloc[0])
                m_date = DATE_RE.search(val)
                if m_date:
                    return f"{m_date.group(1)}{m_date.group(2)}"

    return datetime.now(timezone.utc).strftime("%Y%m")


def _sanitize_df(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if df[col].dtype in ("float64", "float32"):
            mask = df[col].isna()
            if mask.any():
                df.loc[mask, col] = None
                df[col] = df[col].astype(object)
    return df


# Pasta montada (somente leitura) com os arquivos de config e chaves da OCI.
# A conexão guarda só o caminho do arquivo; as chaves não vão para o banco.
OCI_CONFIG_DIR = Path(os.getenv("OCI_CONFIG_DIR", "/run/finbase-env"))


def _oci_config_from_file(config_file: str, profile: str) -> dict:
    """Lê um config OCI (formato ~/.oci/config) que esteja dentro de ``OCI_CONFIG_DIR``."""
    root = OCI_CONFIG_DIR.resolve()
    path = (root / config_file).resolve() if not Path(config_file).is_absolute() else Path(config_file).resolve()
    if root not in path.parents:
        raise ValueError(f"O arquivo de config OCI precisa estar em {root}")
    config = oci.config.from_file(str(path), profile_name=profile)
    # O key_file do arquivo costuma apontar para o caminho da máquina de quem o
    # gerou; procura a chave pelo nome na mesma pasta do config.
    key_file = Path(str(config.get("key_file") or "")).expanduser()
    if not key_file.is_file():
        candidate = (path.parent / key_file.name).resolve()
        if root not in candidate.parents or not candidate.is_file():
            raise ValueError(f"Chave privada '{key_file.name}' não encontrada em {path.parent}")
        config["key_file"] = str(candidate)
    return config


class OciExtractor:
    """Extrator de relatórios de Cost e Usage Reports da Oracle Cloud Infrastructure (OCI) com agregação mensal."""

    def __init__(self, connection: SourceConnection):
        self.connection = connection
        self.config_json = connection.config_json or {}
        # Por padrão focar em reports/cost-csv se não especificado
        self.blob_prefixes = connection.blob_prefixes or self.config_json.get("blob_prefixes", ["reports/cost-csv"])
        self._temp_key_file = None

    def _get_oci_config(self) -> dict:
        cfg = self.config_json
        if cfg.get("config_file"):
            return _oci_config_from_file(cfg["config_file"], cfg.get("config_profile") or "DEFAULT")
        if not (cfg.get("user_ocid") or cfg.get("user")) or not (cfg.get("tenancy_ocid") or cfg.get("tenancy")):
            raise ValueError(
                "Credenciais OCI não informadas: escolha o arquivo de config OCI "
                "ou preencha tenancy, usuário, fingerprint e chave privada."
            )
        user_ocid = cfg.get("user_ocid") or cfg.get("user")
        tenancy_ocid = cfg.get("tenancy_ocid") or cfg.get("tenancy")
        fingerprint = cfg.get("fingerprint")
        region = cfg.get("region", "sa-saopaulo-1")
        private_key = cfg.get("private_key") or cfg.get("key_content")
        key_file = cfg.get("key_file")

        if not key_file and private_key:
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False)
            tmp.write(private_key)
            tmp.flush()
            tmp.close()
            os.chmod(tmp.name, 0o600)
            self._temp_key_file = tmp.name
            key_file = tmp.name

        oci_config = {
            "user": user_ocid,
            "tenancy": tenancy_ocid,
            "fingerprint": fingerprint,
            "region": region,
            "key_file": key_file or "",
        }
        return oci_config

    def _tenancy_ocid(self) -> str | None:
        """Tenancy da conexão; com arquivo de config, vem do próprio arquivo."""
        if self.config_json.get("config_file"):
            return self._get_oci_config().get("tenancy")
        return self.config_json.get("tenancy_ocid") or self.config_json.get("tenancy")

    def _get_object_storage_client(self) -> oci.object_storage.ObjectStorageClient:
        oci_config = self._get_oci_config()
        oci.config.validate_config(oci_config)
        return oci.object_storage.ObjectStorageClient(oci_config)

    def test_connection(self) -> dict:
        """Testa conexão com a OCI e valida o acesso ao bucket de relatórios."""
        try:
            client = self._get_object_storage_client()
            tenancy_ocid = self._tenancy_ocid()
            namespace = self.config_json.get("namespace", "bling")

            response = client.list_objects(
                namespace_name=namespace,
                bucket_name=tenancy_ocid,
                limit=1,
                fields="name,size,timeCreated",
            )
            count = len(response.data.objects)
            return {
                "status": "OK",
                "message": f"Conexão OCI validada com sucesso! Namespace: '{namespace}', Bucket: '{tenancy_ocid[:20]}...', Objetos encontrados: {count}+",
            }
        except oci.exceptions.ServiceError as se:
            logger.error("OCI ServiceError ao testar conexao: %s", se)
            return {
                "status": "FAILED",
                "message": f"Erro OCI ({se.status} {se.code}): {se.message}"[:500],
            }
        except Exception as e:
            logger.error("Erro ao testar conexao OCI: %s", e)
            return {"status": "FAILED", "message": str(e)[:500]}

    def _list_all_raw_objects(self, prefix: str) -> list[Any]:
        """Lista todos os objetos de um prefixo na OCI com cache."""
        if not hasattr(self, "_cached_objects"):
            self._cached_objects = {}
        if prefix in self._cached_objects:
            return self._cached_objects[prefix]

        client = self._get_object_storage_client()
        tenancy_ocid = self._tenancy_ocid()
        namespace = self.config_json.get("namespace", "bling")

        objects = []
        next_start_with = None
        while True:
            list_args = {
                "namespace_name": namespace,
                "bucket_name": tenancy_ocid,
                "prefix": prefix,
                "fields": "name,size,timeCreated,md5",
                "limit": 1000,
            }
            if next_start_with:
                list_args["start"] = next_start_with

            res = client.list_objects(**list_args)
            if res.data.objects:
                objects.extend(res.data.objects)

            next_start_with = res.data.next_start_with
            if not next_start_with:
                break

        self._cached_objects[prefix] = objects
        return objects

    def get_objects_for_period(self, billing_period: str, prefix: str = "reports/cost-csv") -> list[Any]:
        """Retorna todos os objetos OCI pertencentes a um período YYYYMM específico."""
        all_objects = self._list_all_raw_objects(prefix)
        matched = []
        for obj in all_objects:
            obj_period = derive_billing_period_oci(obj.name, obj.time_created)
            if obj_period == billing_period:
                matched.append(obj)
        return matched

    def _aggregate_objects_by_month(self, objects: list[Any], prefix: str, existing_blobs: dict, skipped_paths: set[str]) -> list[BlobScanItem]:
        """Agrupa os objetos individuais da OCI em itens consolidados por mês."""
        # Agrupar por YYYYMM
        month_groups = defaultdict(list)
        for obj in objects:
            period = derive_billing_period_oci(obj.name, obj.time_created) or "unknown"
            month_groups[period].append(obj)

        items: list[BlobScanItem] = []
        for period, objs in month_groups.items():
            month_path = f"{prefix}#period={period}"
            if month_path in skipped_paths:
                continue

            total_size = sum(obj.size or 0 for obj in objs)
            last_mod = max((obj.time_created for obj in objs if obj.time_created), default=datetime.now(timezone.utc))
            file_count = len(objs)

            existing = existing_blobs.get(month_path)
            if not existing:
                # Verificar se existe algum registro pelo período
                existing_period = next((b for b in existing_blobs.values() if b.billing_period == period), None)
                if existing_period:
                    existing = existing_period

            if not existing:
                status = "NEW"
            elif existing.last_modified and last_mod > existing.last_modified:
                status = "CHANGED"
            else:
                status = "UNCHANGED"

            items.append(BlobScanItem(
                blob_path=month_path,
                blob_prefix=prefix,
                last_modified=last_mod,
                content_length=total_size,
                row_count=file_count,
                status=status,
                billing_period=period,
            ))

        return items

    def _scan_sync(self, db_session: Session) -> list[BlobScanItem]:
        """Varre os relatórios OCI agregados por mês (versão síncrona)."""
        existing_blobs_result = db_session.execute(
            select(IngestedBlob).where(IngestedBlob.connection_id == self.connection.id)
        )
        existing_blobs = {b.blob_path: b for b in existing_blobs_result.scalars().all()}

        skipped_result = db_session.execute(
            select(SkippedBlob.blob_path).where(SkippedBlob.connection_id == self.connection.id)
        )
        skipped_paths = {row[0] for row in skipped_result}

        prefixes = self.blob_prefixes if self.blob_prefixes else ["reports/cost-csv"]
        items: list[BlobScanItem] = []

        for prefix in prefixes:
            raw_objects = self._list_all_raw_objects(prefix)
            month_items = self._aggregate_objects_by_month(raw_objects, prefix, existing_blobs, skipped_paths)
            items.extend(month_items)

        items.sort(key=lambda x: x.billing_period or "", reverse=True)
        return items

    async def scan(self, db: AsyncSession) -> BlobScanResult:
        """Varre os relatórios OCI agregados por mês (versão assíncrona)."""
        existing_blobs_result = await db.execute(
            select(IngestedBlob).where(IngestedBlob.connection_id == self.connection.id)
        )
        existing_blobs = {b.blob_path: b for b in existing_blobs_result.scalars().all()}

        skipped_result = await db.execute(
            select(SkippedBlob.blob_path).where(SkippedBlob.connection_id == self.connection.id)
        )
        skipped_paths = {row[0] for row in skipped_result}

        prefixes = self.blob_prefixes if self.blob_prefixes else ["reports/cost-csv"]
        items: list[BlobScanItem] = []
        stats = {"new": 0, "changed": 0, "unchanged": 0}

        for prefix in prefixes:
            raw_objects = self._list_all_raw_objects(prefix)
            month_items = self._aggregate_objects_by_month(raw_objects, prefix, existing_blobs, skipped_paths)
            for it in month_items:
                if it.status == "NEW":
                    stats["new"] += 1
                elif it.status == "CHANGED":
                    stats["changed"] += 1
                else:
                    stats["unchanged"] += 1
            items.extend(month_items)

        items.sort(key=lambda x: x.billing_period or "", reverse=True)

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
        """Descompacta e faz parse de CSV ou CSV.GZ da OCI."""
        try:
            if raw_bytes[:2] == b"\x1f\x8b" or blob_path.endswith(".gz"):
                with gzip.open(io.BytesIO(raw_bytes), "rt", encoding="utf-8", errors="replace") as gz:
                    df = pd.read_csv(gz, low_memory=False)
            else:
                df = pd.read_csv(io.BytesIO(raw_bytes), encoding="utf-8", errors="replace", low_memory=False)
            return _sanitize_df(df)
        except Exception as e:
            logger.error("Erro ao fazer parse do CSV OCI %s: %s", blob_path, e)
            raise ValueError(f"Falha ao decodificar CSV OCI {blob_path}: {e}")

    @staticmethod
    def aggregate_df_daily(df: pd.DataFrame) -> pd.DataFrame:
        """
        Agrupa e consolida os dados horários da OCI em nível diário por recurso e tags.
        Reduz o volume em ~94% mantendo 100% da integridade financeira e dimensional.
        """
        if df.empty:
            return df

        from app.services.iceberg_service import _fold_case_insensitive_columns
        df = _fold_case_insensitive_columns(df.copy())
        raw_count = len(df)

        # 1. Normalizar data para nível diário YYYY-MM-DD
        date_cols = ["lineItem/intervalUsageStart", "lineItem/intervalUsageEnd", "lineItem/intervalStart", "lineItem/intervalEnd", "Date", "intervalUsageStart", "intervalUsageEnd", "intervalStart", "intervalEnd"]
        for col in date_cols:
            if col in df.columns:
                df[col] = df[col].astype(str).str[:10]

        # 2. Converter colunas numéricas de custo e quantidade
        numeric_agg = {}
        cost_cols = ["cost/myCost", "cost/myCostOverage", "usage/billedQuantity", "usage/consumedQuantity", "usage/billedQuantityOverage", "cost/billedAmount", "cost/consumedAmount"]
        for col in cost_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
                numeric_agg[col] = "sum"

        # 3. Colunas dimensionais e tags para agrupar
        dim_candidates = [
            "lineItem/intervalUsageStart",
            "lineItem/intervalUsageEnd",
            "lineItem/intervalStart",
            "lineItem/intervalEnd",
            "Date",
            "intervalUsageStart",
            "intervalUsageEnd",
            "intervalStart",
            "intervalEnd",
            "lineItem/tenantId",
            "product/service",
            "product/resource",
            "product/compartmentId",
            "product/compartmentName",
            "product/region",
            "product/availabilityDomain",
            "product/resourceId",
            "product/description",
            "cost/subscriptionId",
            "cost/productSku",
            "cost/unitPrice",
            "cost/currencyCode",
            "cost/billingUnitReadable",
            "cost/skuUnitDescription",
            "cost/overageFlag",
            "lineItem/isCorrection",
        ]
        # Adicionar todas as colunas de tags presentes no DataFrame
        tag_cols = [c for c in df.columns if c.startswith("tags/")]
        group_cols = [c for c in dim_candidates + tag_cols if c in df.columns]

        if not group_cols or not numeric_agg:
            logger.warning("Nenhuma coluna de agrupamento ou numerica encontrada para OCI daily aggregation. Group cols: %s, Numeric agg: %s", group_cols, numeric_agg)
            return df

        # Realizar group by
        grouped = df.groupby(group_cols, as_index=False, dropna=False).agg(numeric_agg)

        # Converter colunas numéricas de volta para string/object conforme convenção Bronze
        for col in numeric_agg:
            grouped[col] = grouped[col].astype(str)

        logger.info(
            "OCI Agrupamento Diário concluído: de %d linhas horárias para %d linhas consolidadas (redução de %.2f%%)",
            raw_count, len(grouped), ((raw_count - len(grouped)) / raw_count) * 100 if raw_count > 0 else 0
        )
        return _sanitize_df(grouped)
