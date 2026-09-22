"""Azure Automation — coleta real de jobs e schedules via ARM API.

Seguindo o padrão das coletas de Azure PTU: a conexão guarda credenciais e a
coleta é executada pelo worker (task de extração). Os dados são persistidos na
Bronze (raw_<connection_code>) com _billing_period, para depois serem mapeados
e reprocessados para a Gold (MODELO AUTOMATION / MODELO AUTOMATION SCHEDULE).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pandas as pd
from azure.identity import ClientSecretCredential

from app.models.ingested_blob import IngestedBlob

logger = logging.getLogger(__name__)

ARM = "https://management.azure.com"
SUBSCRIPTIONS_API = "2020-01-01"
AUTOMATION_ACCOUNTS_API = "2023-11-01"
AUTOMATION_RESOURCE_API = "2023-11-01"
HTTP_TIMEOUT = 60.0
MAX_JOBS_PER_ACCOUNT = 2000
COLLECT_CONCURRENCY = 6


def resolve_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Return only configuration persisted in source_connection.config_json."""
    return dict(config or {})


def _credential(config: dict[str, Any]):
    cfg = resolve_config(config)
    missing = [k for k in ("tenant_id", "client_id", "client_secret") if not cfg.get(k)]
    if missing:
        raise ValueError(
            f"Credenciais Azure incompletas na conexão: faltam {', '.join(missing)}. "
            "Configure tenant_id, client_id e client_secret no banco de dados."
        )
    return ClientSecretCredential(
        tenant_id=cfg["tenant_id"],
        client_id=cfg["client_id"],
        client_secret=cfg["client_secret"],
    )


async def _arm_headers(config: dict[str, Any]) -> dict[str, str]:
    cred = _credential(config)
    token = cred.get_token("https://management.azure.com/.default").token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def _get_json(client: httpx.AsyncClient, url: str, headers: dict[str, str], params: dict[str, Any] | None = None) -> list[dict]:
    """Percorre paginação (nextLink) de um endpoint ARM e devolve a lista de values."""
    values: list[dict] = []
    next_url: str | None = url
    while next_url:
        resp = await client.get(next_url, headers=headers, params=params if next_url == url else None, timeout=HTTP_TIMEOUT)
        if resp.status_code in (401, 403):
            raise PermissionError(f"Azure ARM {resp.status_code}: acesso negado em {url[:120]}")
        if resp.status_code >= 400:
            body = resp.text[:300]
            raise RuntimeError(f"Azure ARM {resp.status_code} em {url[:120]}: {body}")
        data = resp.json()
        values.extend(data.get("value") or [])
        next_url = data.get("nextLink")
    return values


async def discover_accounts(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Lista subscriptions → Automation Accounts (com resource group)."""
    headers = await _arm_headers(config)
    subs_filter = resolve_config(config).get("subscription_ids") or []
    accounts: list[dict[str, Any]] = []
    async with httpx.AsyncClient() as client:
        subs = await _get_json(client, f"{ARM}/subscriptions", headers, {"api-version": SUBSCRIPTIONS_API})
        for sub in subs:
            sub_id = sub.get("subscriptionId")
            if subs_filter and sub_id not in subs_filter:
                continue
            accs = await _get_json(
                client,
                f"{ARM}/subscriptions/{sub_id}/providers/Microsoft.Automation/automationAccounts",
                headers,
                {"api-version": AUTOMATION_ACCOUNTS_API},
            )
            for acc in accs:
                acc_id = acc.get("id") or ""
                rg = ""
                if "/resourceGroups/" in acc_id:
                    rg = acc_id.split("/resourceGroups/")[1].split("/")[0]
                accounts.append({
                    "subscription_id": sub_id,
                    "subscription_name": sub.get("displayName") or sub_id,
                    "resource_group": rg,
                    "account_id": acc_id,
                    "account_name": acc.get("name"),
                    "location": (acc.get("location") or ""),
                })
    logger.info("Azure Automation: %d conta(s) descoberta(s)", len(accounts))
    return accounts


def _infer_action(runbook_name: str, parameters: dict[str, Any] | None) -> str:
    name = (runbook_name or "").lower()
    if "start" in name:
        return "Start"
    if "stop" in name or "deallocat" in name or "deslig" in name:
        return "Stop"
    params = {str(k).lower(): v for k, v in (parameters or {}).items()}
    action = str(params.get("action") or params.get("operation") or "")
    if "stop" in action.lower():
        return "Stop"
    if "start" in action.lower():
        return "Start"
    return ""


def _job_billing_period(creation_time: str | None) -> str | None:
    if not creation_time:
        return None
    return creation_time[:7].replace("-", "")


async def collect_jobs(client: httpx.AsyncClient, headers: dict[str, str], account: dict[str, Any], backfill_days: int) -> list[dict[str, Any]]:
    """Coleta jobs de um Automation Account (últimos N dias)."""
    sub = account["subscription_id"]
    rg = account["resource_group"]
    acc = account["account_name"]
    url = (
        f"{ARM}/subscriptions/{sub}/resourceGroups/{rg}"
        f"/providers/Microsoft.Automation/automationAccounts/{acc}/jobs"
    )
    jobs = await _get_json(client, url, headers, {"api-version": AUTOMATION_RESOURCE_API})
    cutoff = (datetime.now(timezone.utc) - timedelta(days=backfill_days)).isoformat()
    rows: list[dict[str, Any]] = []
    for job in jobs[:MAX_JOBS_PER_ACCOUNT]:
        props = job.get("properties") or {}
        created = props.get("creationTime")
        if not created or created < cutoff:
            continue
        params = props.get("parameters") or {}
        vm = str(params.get("VM") or params.get("vmName") or params.get("resourceName") or "")
        rg_param = str(params.get("ResourceGroup") or params.get("resourceGroupName") or "")
        job_id = job.get("name") or job.get("id", "").rsplit("/", 1)[-1]
        runbook = (props.get("runbook") or {}).get("name") or ""
        resource_id = f"/subscriptions/{sub}/resourceGroups/{rg_param or rg}/providers/Microsoft.Compute/virtualMachines/{vm}" if vm else ""
        rows.append({
            "JobId": job_id,
            "Time": created,
            "RunbookName": runbook,
            "Status": props.get("status") or "",
            "VM": vm,
            "ResourceGroup": rg_param or rg,
            "SubscriptionId": sub,
            "Action": _infer_action(runbook, params),
            "CorrelationId": props.get("correlationId") or "",
            "Caller": params.get("Caller") or "",
            "ResourceId": resource_id,
        })
    return rows


async def collect_schedules(client: httpx.AsyncClient, headers: dict[str, str], account: dict[str, Any]) -> list[dict[str, Any]]:
    """Coleta schedules de um Automation Account."""
    sub = account["subscription_id"]
    rg = account["resource_group"]
    acc = account["account_name"]
    url = (
        f"{ARM}/subscriptions/{sub}/resourceGroups/{rg}"
        f"/providers/Microsoft.Automation/automationAccounts/{acc}/schedules"
    )
    schedules = await _get_json(client, url, headers, {"api-version": AUTOMATION_RESOURCE_API})
    snapshot = datetime.now(timezone.utc).date().isoformat()
    rows: list[dict[str, Any]] = []
    for s in schedules:
        props = s.get("properties") or {}
        rows.append({
            "SnapshotDate": snapshot,
            "ScheduleName": s.get("name") or "",
            "ScheduleRunbook": "",  # schedule não expõe runbook; o vínculo está em jobSchedules
            "ResourceId": "",
            "VM": "",
            "ResourceGroup": rg,
            "SubscriptionId": sub,
            "Enabled": bool(props.get("isEnabled")),
            "NextRunTime": props.get("nextRun") or "",
            "LastJobTime": "",
            "ScheduleStatus": "Enabled" if props.get("isEnabled") else "Disabled",
        })
    return rows


def _write_bronze_and_register(session, connection, df: pd.DataFrame, period: str, blob_name: str, *, source_code: str | None = None) -> int:
    """Escreve um DataFrame na Bronze e registra o IngestedBlob."""
    if df.empty:
        return 0
    from app.services import iceberg_service as iceberg

    blob_path = f"automation/{blob_name}"
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
        blob_prefix="automation",
        billing_period=period,
        last_modified=datetime.now(timezone.utc),
        etag="",
        content_length=0,
        row_count=rows,
        status="INGESTED",
    ))
    session.flush()
    return rows


def run_automation_collection(session, connection_id: int, *, backfill_days: int | None = None, task_id: int | None = None, source_code: str | None = None) -> dict[str, Any]:
    """Coleta jobs e schedules de todas as Automation Accounts e persiste na Bronze."""
    from app.database import SyncSession as _Sync
    from app.models.source_connection import SourceConnection
    from app.models.etl_task import EtlTask

    s = session or _Sync()
    try:
        conn = s.get(SourceConnection, connection_id)
        if not conn:
            raise ValueError(f"Conexão {connection_id} não encontrada")
        config = dict(conn.config_json or {})
        days = int(backfill_days or config.get("backfill_days") or 93)

        accounts = asyncio_run(discover_accounts(config))
        if not accounts:
            raise RuntimeError("Nenhuma Automation Account encontrada nas subscriptions configuradas")

        headers = asyncio_run(_arm_headers(config))

        import asyncio

        async def _collect_all():
            async with httpx.AsyncClient() as client:
                jobs_all: list[dict[str, Any]] = []
                sched_all: list[dict[str, Any]] = []
                sem = asyncio.Semaphore(COLLECT_CONCURRENCY)

                async def one(account: dict[str, Any]):
                    async with sem:
                        jobs = await collect_jobs(client, headers, account, days)
                        scheds = await collect_schedules(client, headers, account)
                        return jobs, scheds

                results = await asyncio.gather(*[one(a) for a in accounts])
                for jobs, scheds in results:
                    jobs_all.extend(jobs)
                    sched_all.extend(scheds)
                return jobs_all, sched_all

        jobs_all, sched_all = asyncio_run(_collect_all())

        # Jobs → Bronze por período
        jobs_by_period: dict[str, list[dict]] = {}
        for j in jobs_all:
            bp = _job_billing_period(j.get("Time")) or "unknown"
            jobs_by_period.setdefault(bp, []).append(j)

        bronze_jobs = 0
        periods: list[str] = []
        for bp, rows in jobs_by_period.items():
            df = pd.DataFrame(rows)
            n = _write_bronze_and_register(s, conn, df, bp, f"jobs/{bp}.csv", source_code=source_code)
            bronze_jobs += n
            periods.append(bp)

        # Schedules → Bronze no período corrente
        if sched_all:
            bp = datetime.now(timezone.utc).strftime("%Y%m")
            df = pd.DataFrame(sched_all)
            bronze_scheds = _write_bronze_and_register(s, conn, df, bp, f"schedules/{bp}.csv", source_code=source_code)
        else:
            bronze_scheds = 0

        s.commit()

        result = {
            "accounts": len(accounts),
            "jobs_collected": len(jobs_all),
            "jobs_bronze": bronze_jobs,
            "schedules_collected": len(sched_all),
            "schedules_bronze": bronze_scheds,
            "billing_periods": periods,
            "backfill_days": days,
        }
        if task_id:
            t = s.get(EtlTask, task_id)
            if t:
                t.status = "COMPLETED"
                t.phase = f"Azure Automation: {len(jobs_all)} jobs / {len(sched_all)} schedules em {len(accounts)} conta(s)"
                t.rows_raw = bronze_jobs + bronze_scheds
                t.billing_periods = periods
                t.result_json = result
                t.finished_at = datetime.now(timezone.utc)
                s.commit()
        logger.info("Azure Automation coleta: %s", result)
        return result
    finally:
        if session is None:
            s.close()


def asyncio_run(coro):
    """Executa uma coroutine no event loop (thread worker usa um loop novo)."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        # Já dentro de um loop: usa asyncio.run no thread atual não é possível;
        # cria um loop em nova thread.
        import threading
        out: dict[str, Any] = {}

        def runner():
            out["result"] = asyncio.run(coro)

        t = threading.Thread(target=runner)
        t.start()
        t.join()
        return out["result"]
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Coleta de INVENTÁRIO (jobSchedules + schedules + runbooks) para validação
# de savings — persiste na Bronze como snapshot diário.
# ---------------------------------------------------------------------------

def _infer_resource_from_schedule(schedule_name: str, runbook_name: str) -> dict[str, str]:
    """Infere recurso e ação a partir do nome do schedule e runbook."""
    name_lower = (schedule_name or "").lower()
    rb_lower = (runbook_name or "").lower()
    
    result = {"resource_name": "", "resource_type": "", "action": ""}
    
    # Inferir ação
    if "start" in name_lower or "on" in name_lower or "scaleup" in name_lower:
        result["action"] = "Start"
    elif "stop" in name_lower or "off" in name_lower or "scaledown" in name_lower:
        result["action"] = "Stop"
    elif "housekeeping" in name_lower:
        result["action"] = "Housekeeping"
    elif "mask" in name_lower:
        result["action"] = "Mask"
    
    # Inferir recurso e tipo do nome do schedule
    # Padrão TI: StartVM_BRTAZLTB0003CO -> VM BRTAZLTB0003CO
    if "vm_" in name_lower or "startvm_" in name_lower or "stopvm_" in name_lower:
        parts = schedule_name.split("_")
        if len(parts) >= 2:
            result["resource_name"] = parts[-1]
            result["resource_type"] = "VirtualMachine"
    # Padrão TI: StartMongoDBSingleVM_hlg-mongo01-az -> MongoDB hlg-mongo01-az
    elif "mongodb" in name_lower:
        parts = schedule_name.split("_")
        if len(parts) >= 2:
            result["resource_name"] = parts[-1]
            result["resource_type"] = "MongoDB"
    elif "mysql" in name_lower:
        parts = schedule_name.split("_")
        if len(parts) >= 2:
            result["resource_name"] = parts[-1]
            result["resource_type"] = "MySQL"
    elif "postgresql" in name_lower or "psql" in name_lower:
        parts = schedule_name.split("_")
        if len(parts) >= 2:
            result["resource_name"] = parts[-1]
            result["resource_type"] = "PostgreSQL"
    elif "oracle" in name_lower:
        parts = schedule_name.split("_")
        if len(parts) >= 2:
            result["resource_name"] = parts[-1]
            result["resource_type"] = "Oracle"
    # Padrão 4P: StartAura, stopaura -> Aura
    elif "aura" in name_lower and "aks" not in name_lower:
        result["resource_name"] = "Aura"
        result["resource_type"] = "VirtualMachine"
    elif "noronha" in name_lower:
        result["resource_name"] = "Noronha"
        result["resource_type"] = "VirtualMachine"
    elif "cassandra" in name_lower:
        result["resource_name"] = "Cassandra"
        result["resource_type"] = "VirtualMachine"
    elif "baikal" in name_lower:
        result["resource_name"] = "Baikal"
        result["resource_type"] = "VirtualMachine"
    elif "sftp" in name_lower:
        result["resource_name"] = "SFTP"
        result["resource_type"] = "VirtualMachine"
    elif "callcenter" in name_lower:
        result["resource_name"] = "CallCenter"
        result["resource_type"] = "VirtualMachine"
    elif "ccc" in name_lower:
        result["resource_name"] = "CCC"
        result["resource_type"] = "VirtualMachine"
    elif "sig" in name_lower and "pre" in name_lower:
        result["resource_name"] = "SigPre"
        result["resource_type"] = "VirtualMachine"
    elif "sig" in name_lower and "dev" in name_lower:
        result["resource_name"] = "SigDev"
        result["resource_type"] = "VirtualMachine"
    elif "framework" in name_lower and "brasil" in name_lower:
        result["resource_name"] = "FrameworkBrasil"
        result["resource_type"] = "AKS"
    elif "digital" in name_lower and "analytics" in name_lower:
        result["resource_name"] = "DigitalAnalytics"
        result["resource_type"] = "VirtualMachine"
    elif "proxy" in name_lower:
        result["resource_name"] = "Proxy"
        result["resource_type"] = "AKS"
    elif "fastdata" in name_lower:
        result["resource_name"] = "FastData"
        result["resource_type"] = "AKS"
    elif "next" in name_lower:
        result["resource_name"] = "Next"
        result["resource_type"] = "VirtualMachine"
    elif "portal-dq" in name_lower or "portaldq" in name_lower:
        result["resource_name"] = "PortalDQ"
        result["resource_type"] = "VirtualMachine"
    elif "covid" in name_lower:
        result["resource_name"] = "Covid"
        result["resource_type"] = "VirtualMachine"
    elif "projetos" in name_lower:
        result["resource_name"] = "Projetos"
        result["resource_type"] = "AKS"
    elif "vidav" in name_lower:
        result["resource_name"] = "VidaV"
        result["resource_type"] = "AKS"
    elif "security" in name_lower:
        result["resource_name"] = "Security"
        result["resource_type"] = "VirtualMachine"
    elif "bastion" in name_lower:
        result["resource_name"] = "Bastion"
        result["resource_type"] = "VirtualMachine"
    elif "datalabs" in name_lower:
        result["resource_name"] = "DataLabs"
        result["resource_type"] = "VirtualMachine"
    elif "gis" in name_lower:
        result["resource_name"] = "GIS"
        result["resource_type"] = "VirtualMachine"
    # Padrões TI específicos
    elif "powerbi" in name_lower:
        result["resource_name"] = "PowerBI"
        result["resource_type"] = "VirtualMachine"
    elif "syndp" in name_lower:
        result["resource_name"] = "Syndp"
        result["resource_type"] = "VirtualMachine"
    elif "sinfonia" in name_lower:
        result["resource_name"] = "Sinfonia"
        result["resource_type"] = "VirtualMachine"
    elif "vivomais" in name_lower:
        result["resource_name"] = "VivoMais"
        result["resource_type"] = "VirtualMachine"
    elif "adf" in name_lower:
        result["resource_name"] = "ADF"
        result["resource_type"] = "DataFactory"
    elif "sentinel" in name_lower:
        result["resource_name"] = "Sentinel"
        result["resource_type"] = "Sentinel"
    elif "tax-one" in name_lower or "taxone" in name_lower:
        result["resource_name"] = "TaxOneInstaller"
        result["resource_type"] = "VirtualMachine"
    elif "hana" in name_lower:
        result["resource_name"] = "HANA"
        result["resource_type"] = "VirtualMachine"
    # Fallback: usar nome do schedule como recurso
    else:
        result["resource_name"] = schedule_name
        # Tentar inferir tipo do runbook
        if "vm" in rb_lower or "azurevm" in rb_lower:
            result["resource_type"] = "VirtualMachine"
        elif "aks" in rb_lower:
            result["resource_type"] = "AKS"
        elif "mysql" in rb_lower:
            result["resource_type"] = "MySQL"
        elif "postgresql" in rb_lower:
            result["resource_type"] = "PostgreSQL"
        elif "appservice" in rb_lower or "appserviceplan" in rb_lower:
            result["resource_type"] = "AppServicePlan"
        elif "mongodb" in rb_lower:
            result["resource_type"] = "MongoDB"
        elif "oracle" in rb_lower:
            result["resource_type"] = "Oracle"
    
    return result


async def collect_automation_inventory(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    account: dict[str, Any],
    tenant: str,
) -> list[dict[str, Any]]:
    """Coleta inventário completo de uma Automation Account (jobSchedules + schedules + runbooks)."""
    sub = account["subscription_id"]
    sub_name = account.get("subscription_name", sub)
    rg = account["resource_group"]
    acc_name = account["account_name"]
    base = (
        f"{ARM}/subscriptions/{sub}/resourceGroups/{rg}"
        f"/providers/Microsoft.Automation/automationAccounts/{acc_name}"
    )
    
    snapshot = datetime.now(timezone.utc).date().isoformat()
    rows: list[dict[str, Any]] = []
    
    # 1. Coletar runbooks (para lookup de tipo)
    runbooks: dict[str, dict] = {}
    try:
        rb_data = await _get_json(client, f"{base}/runbooks?api-version=2023-11-01", headers)
        for rb in rb_data:
            props = rb.get("properties", {})
            runbooks[rb["name"]] = {
                "type": props.get("runbookType", ""),
                "state": props.get("state", ""),
            }
    except Exception as e:
        logger.warning("Erro ao listar runbooks em %s: %s", acc_name, e)
    
    # 2. Coletar schedules (para lookup de propriedades)
    schedules: dict[str, dict] = {}
    try:
        sch_data = await _get_json(client, f"{base}/schedules?api-version=2023-11-01", headers)
        for s in sch_data:
            props = s.get("properties", {})
            schedules[s["name"]] = {
                "enabled": bool(props.get("isEnabled")),
                "frequency": props.get("frequency", ""),
                "start_time": props.get("startTime", ""),
                "next_run": props.get("nextRun", ""),
                "week_days": (props.get("advancedSchedule") or {}).get("weekDays") or [],
            }
    except Exception as e:
        logger.warning("Erro ao listar schedules em %s: %s", acc_name, e)
    
    # 3. Coletar jobSchedules (vínculo runbook + schedule)
    try:
        js_data = await _get_json(client, f"{base}/jobSchedules?api-version=2023-11-01", headers)
        for js in js_data:
            props = js.get("properties", {})
            runbook_name = props.get("runbook", {}).get("name", "")
            schedule_name = props.get("schedule", {}).get("name", "")
            
            rb_info = runbooks.get(runbook_name, {})
            sch_info = schedules.get(schedule_name, {})
            inferred = _infer_resource_from_schedule(schedule_name, runbook_name)
            
            rows.append({
                "_snapshot_date": snapshot,
                "tenant": tenant,
                "subscription_id": sub,
                "subscription_name": sub_name,
                "resource_group": rg,
                "automation_account": acc_name,
                "runbook_name": runbook_name,
                "runbook_type": rb_info.get("type", ""),
                "runbook_state": rb_info.get("state", ""),
                "schedule_name": schedule_name,
                "schedule_enabled": sch_info.get("enabled", False),
                "schedule_frequency": sch_info.get("frequency", ""),
                "schedule_start_time": sch_info.get("start_time", ""),
                "schedule_next_run": sch_info.get("next_run", ""),
                "schedule_week_days": ",".join(sch_info.get("week_days", [])),
                "job_schedule_id": props.get("jobScheduleId", ""),
                "inferred_resource_name": inferred["resource_name"],
                "inferred_resource_type": inferred["resource_type"],
                "inferred_action": inferred["action"],
            })
    except Exception as e:
        logger.warning("Erro ao listar jobSchedules em %s: %s", acc_name, e)
    
    return rows


def run_automation_inventory_collection(
    session,
    connection_id: int,
    *,
    task_id: int | None = None,
) -> dict[str, Any]:
    """Coleta inventário completo de TODAS as Automation Accounts e persiste na Bronze."""
    from app.database import SyncSession as _Sync
    from app.models.source_connection import SourceConnection
    from app.models.etl_task import EtlTask

    s = session or _Sync()
    try:
        conn = s.get(SourceConnection, connection_id)
        if not conn:
            raise ValueError(f"Conexão {connection_id} não encontrada")
        config = dict(conn.config_json or {})
        tenant = (config.get("credential_profile") or "unknown").replace("azure-", "")

        accounts = asyncio_run(discover_accounts(config))
        if not accounts:
            raise RuntimeError("Nenhuma Automation Account encontrada")

        headers = asyncio_run(_arm_headers(config))

        import asyncio

        async def _collect_all():
            async with httpx.AsyncClient() as client:
                rows_all: list[dict[str, Any]] = []
                sem = asyncio.Semaphore(COLLECT_CONCURRENCY)

                async def one(account: dict[str, Any]):
                    async with sem:
                        return await collect_automation_inventory(client, headers, account, tenant)

                results = await asyncio.gather(*[one(a) for a in accounts])
                for rows in results:
                    rows_all.extend(rows)
                return rows_all

        rows_all = asyncio_run(_collect_all())

        # Persistir na Bronze como snapshot (usar YYYYMM para billing_period)
        bp = datetime.now(timezone.utc).strftime("%Y%m")
        df = pd.DataFrame(rows_all)
        bronze_rows = _write_bronze_and_register(s, conn, df, bp, f"inventory/{bp}.csv", source_code=source_code)

        s.commit()

        result = {
            "accounts": len(accounts),
            "inventory_rows": len(rows_all),
            "bronze_rows": bronze_rows,
            "snapshot_date": bp,
            "tenant": tenant,
        }
        if task_id:
            t = s.get(EtlTask, task_id)
            if t:
                t.status = "COMPLETED"
                t.phase = f"Azure Automation Inventory: {len(rows_all)} registros em {len(accounts)} conta(s)"
                t.rows_raw = bronze_rows
                t.billing_periods = [bp]
                t.result_json = result
                t.finished_at = datetime.now(timezone.utc)
                s.commit()
        logger.info("Azure Automation Inventory: %s", result)
        return result
    finally:
        if session is None:
            s.close()
