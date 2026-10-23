import logging
import io
import json
import re
import configparser
import os
from pathlib import Path
import gzip
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
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
    parser = configparser.ConfigParser(interpolation=None)
    if not parser.read(path):
        raise ValueError(f"Arquivo de config OCI não encontrado: {path}")
    if profile not in parser and profile != "DEFAULT":
        raise ValueError(f"Perfil '{profile}' não existe em {path.name}")
    config = dict(parser[profile])
    # O key_file do arquivo costuma apontar para o caminho da máquina de quem o
    # gerou; procura a chave pelo nome na mesma pasta do config.
    key_file = Path(str(config.get("key_file") or "")).expanduser()
    if not key_file.is_file():
        candidate = (path.parent / key_file.name).resolve()
        if root not in candidate.parents or not candidate.is_file():
            raise ValueError(f"Chave privada '{key_file.name}' não encontrada em {path.parent}")
        config["key_file"] = str(candidate)
    oci.config.validate_config(config)
    return config


USAGE_API_MODE = "usage_api"
USAGE_API_PREFIX = "usage-api"
USAGE_API_HISTORY_MONTHS = 12
# A Usage API aceita no máximo 4 chaves de agrupamento: a consulta diária usa
# as que definem a linha de custo; serviço/descrição/unidade (por SKU), o nome
# do compartimento, a zona e as tags (por recurso) e a assinatura vêm de
# consultas mensais e são juntados depois. A API não agrupa por nome de recurso
# nem devolve preço de lista ou unitário.
USAGE_API_DAILY_GROUP_BY = ["resourceId", "skuPartNumber", "compartmentId", "region"]
USAGE_API_SKU_GROUP_BY = ["skuPartNumber", "skuName", "service", "unit"]
USAGE_API_COMPARTMENT_GROUP_BY = ["compartmentId", "compartmentName"]
USAGE_API_ZONE_GROUP_BY = ["resourceId", "logicalAd"]
USAGE_API_TAG_GROUP_BY = ["resourceId", "tagNamespace", "tagKey", "tagValue"]
USAGE_API_ACCOUNT_GROUP_BY = ["subscriptionId", "tenantName"]
# Compute por hora para o motor de compromissos. Uma consulta por dia: a
# granularidade horária aceita janelas curtas. O serviço é filtrado depois
# (sem diferenciar maiúsculas) para não depender da grafia exata na API.
USAGE_API_HOURLY_GROUP_BY = ["resourceId", "service", "skuPartNumber", "region"]
# Só os dias recentes viram série horária: o motor analisa até 90 dias.
USAGE_API_HOURLY_LOOKBACK_DAYS = 100


def _availability_zone(ad: str | None) -> str | None:
    """``Tota:SA-SAOPAULO-1-AD-1`` → ``SA-SAOPAULO-1-AD-1``; ``NA`` é recurso regional."""
    if not ad or ad == "NA":
        return None
    return ad.split(":", 1)[-1]


def _resource_type(resource_id: str | None) -> str | None:
    """Tipo do recurso pelo OCID (``ocid1.instance.oc1...`` → ``instance``)."""
    parts = (resource_id or "").split(".")
    return parts[1] if len(parts) > 2 and parts[0].startswith("ocid") else None


def _unit_price(cost: float, quantity: float) -> float | None:
    return round(cost / quantity, 10) if quantity else None


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
        if self.uses_usage_api():
            return self._test_usage_api()
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

        items = self._scan_items(existing_blobs, skipped_paths)
        items.sort(key=lambda x: x.billing_period or "", reverse=True)
        return items

    def _scan_items(self, existing_blobs: dict, skipped_paths: set[str]) -> list[BlobScanItem]:
        if self.uses_usage_api():
            return self._usage_api_items(existing_blobs, skipped_paths)
        prefixes = self.blob_prefixes if self.blob_prefixes else ["reports/cost-csv"]
        items: list[BlobScanItem] = []
        for prefix in prefixes:
            raw_objects = self._list_all_raw_objects(prefix)
            items.extend(self._aggregate_objects_by_month(raw_objects, prefix, existing_blobs, skipped_paths))
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

        items = self._scan_items(existing_blobs, skipped_paths)
        stats = {"new": 0, "changed": 0, "unchanged": 0}
        for it in items:
            if it.status == "NEW":
                stats["new"] += 1
            elif it.status == "CHANGED":
                stats["changed"] += 1
            else:
                stats["unchanged"] += 1

        items.sort(key=lambda x: x.billing_period or "", reverse=True)

        return BlobScanResult(
            connection_id=self.connection.id,
            total=len(items),
            new=stats["new"],
            changed=stats["changed"],
            unchanged=stats["unchanged"],
            items=items,
        )

    # ── Usage API ─────────────────────────────────────────────────────────
    # Alternativa ao bucket de relatórios (que exige a policy de endorse na
    # tenancy da Oracle): lê os custos da própria tenancy pela Usage API, com
    # a permissão "read usage-report". Cada mês vira um item "usage-api#period=YYYYMM"
    # e o resultado sai com as mesmas colunas dos CSV de relatório, para que o
    # bronze e o restante do processamento não mudem.

    def uses_usage_api(self) -> bool:
        return self.config_json.get("extraction_mode") == USAGE_API_MODE

    def _usage_client(self) -> "oci.usage_api.UsageapiClient":
        return oci.usage_api.UsageapiClient(self._get_oci_config())

    def _test_usage_api(self) -> dict:
        try:
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            start = today.replace(day=1) if today.day > 1 else (today - timedelta(days=1)).replace(day=1)
            response = self._usage_client().request_summarized_usages(
                oci.usage_api.models.RequestSummarizedUsagesDetails(
                    tenant_id=self._tenancy_ocid(), time_usage_started=start, time_usage_ended=today,
                    granularity="MONTHLY", query_type="COST",
                )
            )
            total = sum(float(item.computed_amount or 0) for item in response.data.items)
            currency = next((item.currency for item in response.data.items if item.currency), "")
            return {"status": "OK", "message": f"Usage API da OCI acessível. Custo do mês até agora: {total:,.2f} {currency}"}
        except oci.exceptions.ServiceError as se:
            return {"status": "FAILED", "message": f"Erro OCI Usage API ({se.status} {se.code}): {se.message}"[:500]}
        except Exception as e:
            return {"status": "FAILED", "message": str(e)[:500]}

    def _usage_api_items(self, existing_blobs: dict, skipped_paths: set[str]) -> list[BlobScanItem]:
        """Um item por mês, dos últimos ``history_months`` meses até o atual."""
        months = max(1, int(self.config_json.get("history_months") or USAGE_API_HISTORY_MONTHS))
        now = datetime.now(timezone.utc)
        items: list[BlobScanItem] = []
        year, month = now.year, now.month
        for index in range(months):
            period = f"{year:04d}{month:02d}"
            path = f"{USAGE_API_PREFIX}#period={period}"
            if path not in skipped_paths:
                existing = existing_blobs.get(path)
                # O mês corrente e o anterior ainda recebem ajustes da Oracle.
                status = "NEW" if not existing else "CHANGED" if index < 2 else "UNCHANGED"
                items.append(BlobScanItem(
                    blob_path=path, blob_prefix=USAGE_API_PREFIX, last_modified=now,
                    content_length=0, row_count=0, status=status, billing_period=period,
                ))
            year, month = (year, month - 1) if month > 1 else (year - 1, 12)
        return items

    def fetch_usage_month(self, billing_period: str) -> pd.DataFrame:
        """Custos diários do mês ``YYYYMM`` pela Usage API, nas colunas do CSV de relatório."""
        start = datetime(int(billing_period[:4]), int(billing_period[4:6]), 1, tzinfo=timezone.utc)
        end = datetime(start.year + (start.month == 12), start.month % 12 + 1, 1, tzinfo=timezone.utc)
        tomorrow = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        end = min(end, tomorrow)
        if end <= start:
            return pd.DataFrame()
        daily = self._summarized_usages(start, end, "DAILY", USAGE_API_DAILY_GROUP_BY)
        if not daily:
            return pd.DataFrame()
        skus = {
            item.sku_part_number: item
            for item in self._summarized_usages(start, end, "MONTHLY", USAGE_API_SKU_GROUP_BY)
        }
        compartments = {
            item.compartment_id: item.compartment_name
            for item in self._summarized_usages(start, end, "MONTHLY", USAGE_API_COMPARTMENT_GROUP_BY)
        }
        zones: dict[str, str] = {}
        for item in self._summarized_usages(start, end, "MONTHLY", USAGE_API_ZONE_GROUP_BY):
            zone = _availability_zone(item.ad)
            if zone and item.resource_id:
                zones[item.resource_id] = zone
        tags: dict[str, dict[str, str]] = defaultdict(dict)
        for item in self._summarized_usages(start, end, "MONTHLY", USAGE_API_TAG_GROUP_BY):
            for tag in item.tags or []:
                if item.resource_id and tag.key:
                    key = f"{tag.namespace}.{tag.key}" if tag.namespace else tag.key
                    tags[item.resource_id][key] = tag.value
        accounts = self._summarized_usages(start, end, "MONTHLY", USAGE_API_ACCOUNT_GROUP_BY)
        # Sem a assinatura no agrupamento diário, ela só é atribuída quando é única.
        subscription = accounts[0].subscription_id if len({a.subscription_id for a in accounts}) == 1 else None
        tenant_name = next((a.tenant_name for a in accounts if a.tenant_name), None)
        tenancy = self._tenancy_ocid()
        rows: list[dict] = []
        for item in daily:
            sku = skus.get(item.sku_part_number)
            cost = item.computed_amount or 0.0
            quantity = item.computed_quantity or 0.0
            resource_tags = tags.get(item.resource_id)
            rows.append({
                "lineItem/intervalUsageStart": item.time_usage_started.strftime("%Y-%m-%d") if item.time_usage_started else None,
                "lineItem/intervalUsageEnd": item.time_usage_ended.strftime("%Y-%m-%d") if item.time_usage_ended else None,
                "lineItem/tenantId": tenancy,
                "lineItem/tenantName": tenant_name,
                "product/service": sku.service if sku else None,
                "product/resourceId": item.resource_id,
                "product/resourceType": _resource_type(item.resource_id),
                "product/compartmentId": item.compartment_id,
                "product/compartmentName": compartments.get(item.compartment_id),
                "product/region": item.region,
                "product/availabilityDomain": zones.get(item.resource_id),
                "product/description": sku.sku_name if sku else None,
                "cost/subscriptionId": subscription,
                "cost/productSku": item.sku_part_number,
                "cost/skuUnitDescription": sku.unit if sku else None,
                "cost/currencyCode": item.currency,
                # Preço efetivo (custo ÷ quantidade): a API não traz o de tabela.
                "cost/unitPrice": _unit_price(cost, quantity),
                "cost/myCost": cost,
                "usage/billedQuantity": quantity,
                "tags": json.dumps(resource_tags, ensure_ascii=False, sort_keys=True) if resource_tags else None,
            })
        frame = pd.DataFrame(rows)
        # Linhas sem custo nem quantidade não acrescentam nada ao bronze.
        frame = frame[(pd.to_numeric(frame["cost/myCost"]) != 0) | (pd.to_numeric(frame["usage/billedQuantity"]) != 0)]
        return _sanitize_df(frame.reset_index(drop=True))

    def fetch_compute_hourly_month(self, billing_period: str, connection_id: int) -> pd.DataFrame | None:
        """Compute por hora, recurso e SKU no mês ``YYYYMM`` (só os dias recentes).

        Mesmas colunas que ``compute_hourly_rows`` gera a partir dos relatórios CSV.
        ``None`` quando o mês todo está fora da janela: a série já gravada é mantida.
        """
        start = datetime(int(billing_period[:4]), int(billing_period[4:6]), 1, tzinfo=timezone.utc)
        end = datetime(start.year + (start.month == 12), start.month % 12 + 1, 1, tzinfo=timezone.utc)
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        start = max(start, today - timedelta(days=USAGE_API_HOURLY_LOOKBACK_DAYS))
        end = min(end, today + timedelta(days=1))
        if start >= end:
            return None
        rows: list[dict] = []
        day = start
        while day < end:
            for item in self._summarized_usages(day, day + timedelta(days=1), "HOURLY", USAGE_API_HOURLY_GROUP_BY):
                if str(item.service or "").strip().lower() != "compute" or not item.time_usage_started:
                    continue
                cost, quantity = float(item.computed_amount or 0.0), float(item.computed_quantity or 0.0)
                if not cost and not quantity:
                    continue
                rows.append({
                    "usage_hour": item.time_usage_started.astimezone(timezone.utc).strftime("%Y-%m-%dT%H"),
                    "resource_id": item.resource_id, "region": item.region, "sku": item.sku_part_number,
                    "sku_description": None, "currency": item.currency,
                    "billed_cost": cost, "billed_quantity": quantity,
                })
            day += timedelta(days=1)
        if not rows:
            return pd.DataFrame()
        frame = pd.DataFrame(rows)
        keys = ["usage_hour", "resource_id", "region", "sku", "sku_description", "currency"]
        frame = frame.groupby(keys, as_index=False, dropna=False)[["billed_cost", "billed_quantity"]].sum()
        frame["source_connection_id"] = int(connection_id)
        frame["_billing_period"] = str(billing_period)
        return frame

    def _summarized_usages(self, start: datetime, end: datetime, granularity: str, group_by: list[str]) -> list:
        """Todas as páginas de uma consulta de custo da Usage API."""
        client = self._usage_client()
        items: list = []
        page = None
        while True:
            response = client.request_summarized_usages(
                oci.usage_api.models.RequestSummarizedUsagesDetails(
                    tenant_id=self._tenancy_ocid(), time_usage_started=start, time_usage_ended=end,
                    granularity=granularity, query_type="COST", group_by=group_by,
                    # Obrigatório com compartimento no agrupamento; 6 = o mais específico.
                    compartment_depth=6 if any(key.startswith("compartment") for key in group_by) else None,
                ),
                page=page,
            )
            items.extend(response.data.items)
            page = response.headers.get("opc-next-page")
            if not page:
                return items

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
            "lineItem/tenantName",
            "product/service",
            "product/resource",
            "product/resourceType",
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
            "tags",
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
