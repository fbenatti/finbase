"""Azure Resource Graph — coleta real de inventário e recursos órfãos via ARG.

Seguindo o padrão das coletas de Azure Automation (azure_automation_service):
a conexão guarda credenciais (credential_profile ou tenant/client/secret) e a
coleta é executada pelo worker (task de extração). Os dados são persistidos na
Bronze (raw_<connection_code>) com _billing_period, para depois serem mapeados
e reprocessados para a Gold:

- dest_cloud_resource_inventory   (MODELO INVENTÁRIO CLOUD)  ← arg/inventory/
- dest_cloud_orphan_resources     (MODELO RECURSOS ÓRFÃOS)   ← arg/orphans/<rule>/

Endpoint: POST https://management.azure.com/providers/Microsoft.ResourceGraph/resources
Api-version: 2022-10-01
Paginação: $skipToken (1000 linhas/página; ~10.000 linhas por query sem particionar).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd

from app.models.ingested_blob import IngestedBlob
from app.services.azure_identity import arm_headers, credential, resolve_config

logger = logging.getLogger(__name__)

ARG_ENDPOINT = "https://management.azure.com/providers/Microsoft.ResourceGraph/resources"
ARG_API_VERSION = "2022-10-01"
HTTP_TIMEOUT = 60.0
PAGE_SIZE = 1000
MAX_ROWS_PER_QUERY = 10_000  # limite ARG sem particionar (particionamos por subscription)

# ── Queries KQL padrão ──────────────────────────────────────────────────────
# Cada query projeta colunas com os nomes do destino Gold (normalizados), então
# a Bronze já chega no contrato Silver/Gold sem transformação no mapeamento.

INVENTORY_QUERY = """
resources
| project resource_id = id, resource_name = name, resource_type = type,
          subscription_id = subscriptionId, resource_group = resourceGroup,
          location = location, sku = tostring(sku.name),
          tags = tags,
          power_state = tostring(properties.extended.instanceView.powerState.code),
          created_at = tostring(properties.timeCreated)
"""

ORPHAN_RULES: dict[str, str] = {
    "disk_unattached": """
resources
| where type =~ "microsoft.compute/disks"
| where properties.diskState == "Unattached"
// Discos de PersistentVolumeClaims do AKS não são órfãos: mesmo "Unattached"
// (nós em VMSS/maintenance), estão vinculados a um PVC/PV via tags do
// kubernetes-azure-disk driver. Excluímos por tag, por prefixo de nome e por
// resource group gerenciado do AKS (MC_*) — discos de node pool/PV efêmeros.
| where isnull(tags['kubernetes.io-created-for-pvc-name'])
  and isnull(tags['kubernetes.io-created-for-pv-name'])
  and not (name startswith 'pvc-')
  and not (resourceGroup startswith 'MC_')
| project resource_id = id, resource_name = name, resource_type = type,
          subscription_id = subscriptionId, resource_group = resourceGroup,
          location = location, sku = tostring(sku.name),
          disk_size_gb = tostring(properties.diskSizeGB),
          tags = tags, created_at = tostring(properties.timeCreated)
""",
    "vm_deallocated": """
resources
| where type =~ "microsoft.compute/virtualmachines"
| extend power_state = tostring(properties.extended.instanceView.powerState.code)
| where power_state == "PowerState/deallocated"
// VMs com tag "Patch Management" (Desligada ou schedule — ex.: "Servicos - Toda
// Quarta") são intencionalmente desligadas: automação, segurança, DR ou
// patching agendado. Não são órfãs — excluímos pela tag.
| where isnull(tags['Patch Management'])
| project resource_id = id, resource_name = name, resource_type = type,
          subscription_id = subscriptionId, resource_group = resourceGroup,
          location = location, sku = tostring(properties.hardwareProfile.vmSize),
          tags = tags, created_at = tostring(properties.timeCreated)
""",
    "public_ip_idle": """
resources
| where type =~ "microsoft.network/publicipaddresses"
| where isnull(properties.ipConfiguration)
| project resource_id = id, resource_name = name, resource_type = type,
          subscription_id = subscriptionId, resource_group = resourceGroup,
          location = location, sku = tostring(sku.name),
          ip_address = tostring(properties.ipAddress),
          tags = tags, created_at = tostring(properties.timeCreated)
""",
    "snapshot_orphan": """
resources
| where type =~ "microsoft.compute/snapshots"
| extend source_disk = tostring(properties.creationData.sourceResourceId)
| join kind=leftouter (
    resources | where type =~ "microsoft.compute/disks" | project disk_id = id
  ) on $left.source_disk == $right.disk_id
| where isnull(disk_id)
| project resource_id = id, resource_name = name, resource_type = type,
          subscription_id = subscriptionId, resource_group = resourceGroup,
          location = location, sku = tostring(sku.name),
          disk_size_gb = tostring(properties.diskSizeGB),
          source_disk = source_disk,
          tags = tags, created_at = tostring(properties.timeCreated)
""",
    "lb_idle": """
resources
| where type in ("microsoft.network/loadbalancers", "microsoft.network/applicationgateways")
| extend backend_pools = array_length(properties.backendAddressPools)
| extend configured = tostring(properties.backendAddressPools[0].properties.backendAddresses)
| where isnull(configured) or configured == ""
| project resource_id = id, resource_name = name, resource_type = type,
          subscription_id = subscriptionId, resource_group = resourceGroup,
          location = location, sku = tostring(sku.name),
          tags = tags, created_at = tostring(properties.timeCreated)
""",
}

ORPHAN_REASONS: dict[str, str] = {
    "disk_unattached": "Disco gerenciado não anexado a nenhuma VM",
    "vm_deallocated": "VM desalocada (deallocated) — sem Patch Management, candidata a exclusão",
    "public_ip_idle": "IP público não associado a nenhum recurso",
    "snapshot_orphan": "Snapshot cujo disco de origem não existe mais",
    "lb_idle": "Load Balancer / Application Gateway sem backend configurado",
}

# Classificação de negócio por regra — dimensão "tipo" dos recursos órfãos.
ORPHAN_TIPOS: dict[str, str] = {
    "disk_unattached": "DISCO",
    "vm_deallocated": "VM",
    "public_ip_idle": "IP",
    "snapshot_orphan": "SNAPSHOT",
    "lb_idle": "LOAD_BALANCER",
}

SCOPE_QUERY = """
resourcecontainers
| where type =~ "microsoft.resources/subscriptions"
| project subscriptionId, subscriptionName = name
"""

COUNT_QUERY = """
resources
| summarize count() by subscriptionId
"""


def _resolve_subscriptions(config: dict[str, Any]) -> list[str]:
    subs = config.get("subscription_ids") or []
    return [s.strip() for s in subs if s and str(s).strip()]


async def _post_query(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    query: str,
    subscriptions: list[str] | None,
    skip_token: str | None = None,
) -> tuple[list[dict[str, Any]], str | None, int]:
    """Executa uma query ARG e devolve (rows como dicts, next skipToken, count)."""
    body: dict[str, Any] = {
        "query": query,
        "options": {"$top": PAGE_SIZE, "$skipToken": skip_token} if skip_token else {"$top": PAGE_SIZE},
        "resultFormat": "table",
    }
    if subscriptions:
        body["subscriptions"] = subscriptions
    resp = await client.post(
        f"{ARG_ENDPOINT}?api-version={ARG_API_VERSION}",
        headers=headers,
        json=body,
        timeout=HTTP_TIMEOUT,
    )
    if resp.status_code in (401, 403):
        raise PermissionError(f"Azure Resource Graph {resp.status_code}: acesso negado — verifique Reader/ResourceGraph na SPN")
    if resp.status_code >= 400:
        raise RuntimeError(f"Azure Resource Graph {resp.status_code}: {resp.text[:400]}")
    payload = resp.json()
    data = payload.get("data")
    if isinstance(data, dict):
        # resultFormat "table": data = { "columns": [...], "rows": [[...]] }
        columns = data.get("columns") or []
        rows = data.get("rows") or []
        names = [c.get("name", f"col{i}") for i, c in enumerate(columns)]
        dict_rows: list[dict[str, Any]] = []
        for row in rows:
            record = {}
            for i, value in enumerate(row):
                key = names[i] if i < len(names) else f"col{i}"
                record[key] = value
            dict_rows.append(record)
    else:
        # A API pode devolver "objectArray": data já é uma lista de objetos
        # com os nomes projetados pela query (ex.: {subscriptionId, name}).
        dict_rows = list(data or [])
    return dict_rows, payload.get("$skipToken"), int(payload.get("count") or len(dict_rows))


def _jsonify(value: Any) -> Any:
    """Serializa dicts/listas (ex.: tags do ARG) para texto JSON estável."""
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(value)
    return value


async def run_query_all(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    query: str,
    subscriptions: list[str] | None,
) -> tuple[list[dict[str, Any]], int]:
    """Executa uma query ARG com paginação completa (skipToken)."""
    all_rows: list[dict[str, Any]] = []
    skip_token: str | None = None
    total = 0
    while True:
        rows, skip_token, count = await _post_query(client, headers, query, subscriptions, skip_token)
        all_rows.extend(rows)
        total = count or total
        if not skip_token:
            break
        if len(all_rows) >= MAX_ROWS_PER_QUERY:
            logger.warning(
                "ARG query atingiu o teto de %d linhas (%d coletadas) — considere particionar por subscription",
                MAX_ROWS_PER_QUERY, len(all_rows),
            )
            break
    return all_rows, total


async def collect_snapshot(config: dict[str, Any]) -> dict[str, pd.DataFrame]:
    """Coleta inventário + cada regra de órfão em um DataFrame por query.

    `collect_inventory=false` pula o inventário completo (mais pesado, ~8s e
    8k+ linhas) — use quando a carga é focada apenas em recursos órfãos.
    Inventário é particionado por subscription quando `subscription_ids` está
    configurado (evita o teto de 10.000 linhas por query do ARG).
    """
    cfg = resolve_config(config)
    subs = _resolve_subscriptions(config)
    headers = await arm_headers(config)

    async with httpx.AsyncClient() as client:
        frames: dict[str, pd.DataFrame] = {}

        # Inventário (opcional — só quando collect_inventory não é false).
        if cfg.get("collect_inventory", True) is not False:
            inventory_rows: list[dict[str, Any]] = []
            if subs:
                for sub in subs:
                    rows, _ = await run_query_all(client, headers, INVENTORY_QUERY, [sub])
                    inventory_rows.extend(rows)
            else:
                rows, _ = await run_query_all(client, headers, INVENTORY_QUERY, None)
                inventory_rows.extend(rows)
            frames["inventory"] = _frame_from_rows(inventory_rows)
            logger.info("ARG inventory: %d linhas coletadas", len(inventory_rows))
        else:
            logger.info("ARG inventory: coletado (collect_inventory=false)")

        # Órfãos: cada regra configurada (padrão: todas; override de KQL por regra).
        rules = cfg.get("orphan_rules") or {}
        for rule_name, kql in ORPHAN_RULES.items():
            custom = (rules or {}).get(rule_name)
            if custom and str(custom).strip():
                kql = str(custom)
            rows, _ = await run_query_all(client, headers, kql, subs or None)
            frames[f"orphan_{rule_name}"] = _with_orphan_meta(
                _frame_from_rows(rows), rule_name,
            )
            logger.info("ARG orphan rule '%s': %d linhas", rule_name, len(rows))

    return frames


def _with_orphan_meta(df: pd.DataFrame, rule_name: str) -> pd.DataFrame:
    """Adiciona orphan_rule, orphan_reason, tipo e age_days (dias desde a criação)."""
    if df.empty:
        df = pd.DataFrame({"orphan_rule": pd.Series(dtype="object"),
                           "orphan_reason": pd.Series(dtype="object"),
                           "tipo_recurso": pd.Series(dtype="object"),
                           "age_days": pd.Series(dtype="object")})
    else:
        df = df.copy()
        df["orphan_rule"] = rule_name
        df["orphan_reason"] = ORPHAN_REASONS.get(rule_name, rule_name)
        df["tipo_recurso"] = ORPHAN_TIPOS.get(rule_name, rule_name.upper())
        now = datetime.now(timezone.utc)
        created = pd.to_datetime(df.get("created_at"), errors="coerce", utc=True)
        df["age_days"] = (now - created).dt.days.where(created.notna())
    return df


def _frame_from_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    normalized = [{k: _jsonify(v) for k, v in row.items()} for row in rows]
    df = pd.DataFrame(normalized)
    # Garante as colunas comuns do contrato mesmo quando a query não as projeta.
    for col in ("resource_id", "resource_name", "resource_type", "subscription_id",
                "resource_group", "location", "sku", "tags", "power_state", "created_at"):
        if col not in df.columns:
            df[col] = None
    return df


def _snapshot_period(snapshot_date: str) -> str:
    return snapshot_date[:7].replace("-", "")


def _write_bronze_and_register(session, connection, df: pd.DataFrame, period: str, blob_path: str, *, source_code: str | None = None) -> int:
    """Escreve um DataFrame na Bronze e registra o IngestedBlob (padrão automation)."""
    if df.empty:
        return 0
    from app.services import iceberg_service as iceberg

    code = source_code or connection.code
    iceberg.delete_bronze_slice(code, blob_path=blob_path, billing_period=period)
    rows = iceberg.write_bronze(
        connection_code=code,
        df=df,
        billing_period=period,
        blob_path=blob_path,
    )
    if rows == 0:
        return 0

    existing = session.query(IngestedBlob).filter_by(connection_id=connection.id, blob_path=blob_path).first()
    if existing:
        session.delete(existing)
        session.flush()
    session.add(IngestedBlob(
        connection_id=connection.id,
        blob_path=blob_path,
        blob_prefix=blob_path.split("/")[0],
        billing_period=period,
        last_modified=datetime.now(timezone.utc),
        etag="",
        content_length=0,
        row_count=rows,
        status="INGESTED",
    ))
    session.flush()
    return rows


async def discover_scope(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Lista as subscriptions acessíveis (para scan/test da conexão)."""
    headers = await arm_headers(config)
    async with httpx.AsyncClient() as client:
        subs_rows, _ = await run_query_all(client, headers, SCOPE_QUERY, None)
        count_rows, _ = await run_query_all(client, headers, COUNT_QUERY, None)
    counts = {r.get("subscriptionId"): r.get("count_") or 0 for r in count_rows}
    out = []
    for sub in subs_rows:
        sid = sub.get("subscriptionId") or ""
        out.append({
            "subscription_id": sid,
            "subscription_name": sub.get("subscriptionName") or sid,
            "resource_count": counts.get(sid, 0),
        })
    logger.info("Azure Resource Graph: %d subscription(s) acessíveis", len(out))
    return out


def run_resource_graph_collection(
    session,
    connection_id: int,
    *,
    snapshot_date: str | None = None,
    task_id: int | None = None,
    source_code: str | None = None,
) -> dict[str, Any]:
    """Coleta inventário + órfãos via ARG e persiste na Bronze (por período)."""
    from app.database import SyncSession as _Sync
    from app.models.source_connection import SourceConnection
    from app.models.etl_task import EtlTask
    from app.services.azure_automation_service import asyncio_run

    s = session or _Sync()
    try:
        conn = s.get(SourceConnection, connection_id)
        if not conn:
            raise ValueError(f"Conexão {connection_id} não encontrada")
        config = dict(conn.config_json or {})
        snapshot_date = snapshot_date or datetime.now(timezone.utc).date().isoformat()
        period = _snapshot_period(snapshot_date)

        frames = asyncio_run(collect_snapshot(config))

        # snapshot_date é contrato dos destinos Gold (inventário e órfãos).
        for name, df in frames.items():
            if df is not None and not df.empty and "snapshot_date" not in df.columns:
                df["snapshot_date"] = snapshot_date

        bronze_rows = 0
        slices: list[str] = []
        # Inventário → arg/inventory/{snapshot}.csv
        inventory = frames.get("inventory")
        if inventory is not None:
            blob_path = f"arg/inventory/{snapshot_date}.csv"
            n = _write_bronze_and_register(s, conn, inventory, period, blob_path, source_code=source_code)
            bronze_rows += n
            slices.append(blob_path)

        # Órfãos → arg/orphans/<rule>/{snapshot}.csv
        orphan_counts: dict[str, int] = {}
        for name, kql in ORPHAN_RULES.items():
            df = frames.get(f"orphan_{name}")
            if df is None:
                continue
            blob_path = f"arg/orphans/{name}/{snapshot_date}.csv"
            n = _write_bronze_and_register(s, conn, df, period, blob_path, source_code=source_code)
            bronze_rows += n
            orphan_counts[name] = n
            slices.append(blob_path)

        s.commit()

        result = {
            "snapshot_date": snapshot_date,
            "billing_period": period,
            "bronze_rows": bronze_rows,
            "inventory_rows": len(inventory) if inventory is not None else 0,
            "orphan_counts": orphan_counts,
            "slices": slices,
        }
        if task_id:
            t = s.get(EtlTask, task_id)
            if t:
                t.status = "COMPLETED"
                t.phase = (
                    f"Azure Resource Graph: {result['inventory_rows']} recursos no inventário, "
                    f"{sum(orphan_counts.values())} órfãos em {len(orphan_counts)} regra(s)"
                )
                t.rows_raw = bronze_rows
                t.billing_periods = [period]
                t.result_json = result
                t.finished_at = datetime.now(timezone.utc)
                s.commit()
        logger.info("Azure Resource Graph coleta: %s", result)
        return result
    finally:
        if session is None:
            s.close()
