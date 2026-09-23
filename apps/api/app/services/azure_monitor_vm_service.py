"""Coletor Azure Monitor — métricas de plataforma de VM (P5).

Fonte lógica nova (`source_key = monitor_vm_metrics`) com Bronze exclusiva:
`bronze.raw_{code}`. Não compartilha tabela com custo, inventário, AKS ou PTU.

Contrato da fonte (campos que o plano exige em registro único)
--------------------------------------------------------------
- **`source_key`**: `monitor_vm_metrics`.
- **Escopo Azure**: subscriptions visíveis à credencial da conexão, restritas a
  recursos `microsoft.compute/virtualmachines`.
- **APIs**:
  - descoberta: `POST {ARM}/providers/Microsoft.ResourceGraph/resources`
    (`api-version=2022-10-01`), paginada por `$skipToken`;
  - série: `POST https://{region}.metrics.monitor.azure.com/subscriptions/{sub}/metrics:getBatch`
    (`api-version=2023-10-01`) — endpoint **regional de plano de dados**.
- **Batch**: até **50 Resource IDs** por requisição. O batch exige que todos os
  recursos sejam da **mesma subscription, mesma região e mesmo tipo**; os lotes
  são montados respeitando isso (regiões diferentes = requisições diferentes).
- **Permissão**: `Monitoring Reader` **na subscription** (o batch é uma API de
  nível de assinatura: ter o papel só nos recursos não basta) e o resource
  provider `Microsoft.Insights` registrado. 401/403 são levantados, nunca
  silenciados.
- **Limites**: `429`/`5xx`/`529` → backoff exponencial com jitter e retry; erro
  parcial **não** marca a coleta como completa (`PartialCollectionError`).
- **Janela**: `window_days` (padrão **15**, teto 90); o `timespan` de cada
  requisição é fatiado em no máximo 1 dia para não estourar o limite da API.
- **Granularidade**: `interval` (padrão `PT15M`). Uma janela de 15 dias a 15 min
  tem **até** 1.440 buckets por recurso × métrica — não garante 1.440 amostras.
- **Idempotência**: fatia identificada por
  `monitor/{subscription}/{region}/{until}/{window_days}d/{metric}.csv`;
  reexecutar a mesma janela substitui aquela fatia.
- **Chave natural**: `resource_id` + `metric` + `bucket_utc` + `dimension`.
- **Ausência de dado**: métrica sem série vira linha com `quality_status`
  explícito (`NOT_AVAILABLE`/`NO_DATA`), **nunca zero**.
- **Métricas guest**: `azure.vm.linux.guestmetrics` / `azure.vm.windows.guestmetrics`
  **não são suportadas** pela API batch (é limitação documentada). Memória guest
  exige Azure Monitor Agent/DCR e só pode ser coletada por outra via; aqui a
  memória ausente fica registrada como `NOT_AVAILABLE`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx
import pandas as pd

from app.services.azure_identity import arm_headers

logger = logging.getLogger(__name__)

ARM = "https://management.azure.com"
ARG_ENDPOINT = f"{ARM}/providers/Microsoft.ResourceGraph/resources"
ARG_API_VERSION = "2022-10-01"

#: Endpoint de plano de dados de métricas, regional.
METRICS_API_VERSION = "2023-10-01"
METRIC_NAMESPACE = "microsoft.compute/virtualmachines"

#: Teto documentado do `metrics:getBatch`.
MAX_RESOURCES_PER_BATCH = 50

#: O `timespan` de uma requisição é limitado a 1 dia por fatia.
MAX_WINDOW_PER_REQUEST = timedelta(days=1)

#: Métricas de plataforma iniciais do MVP. Nomes descobertos/validados contra a
#: API; ausência por SKU vira `quality_status`, não zero.
#:
#: Memória (`Available Memory Bytes`) **não** é métrica de plataforma: é guest e
#: depende de Azure Monitor Agent/DCR. Fica registrada como ausente até existir
#: instrumentação — nunca é estimada.
DEFAULT_METRICS: tuple[str, ...] = (
    "Percentage CPU",
    "Network In Total",
    "Network Out Total",
    "Disk Read Operations/Sec",
    "Disk Write Operations/Sec",
    "Disk Read Bytes",
    "Disk Write Bytes",
)

#: Métricas guest que o plano prevê mas a API batch não atende. Mantidas
#: explícitas para que a ausência seja registrada com motivo, não confundida
#: com "SKU não expõe".
GUEST_METRICS: tuple[str, ...] = (
    "Available Memory Bytes",
    "Available Memory Percentage",
)

DEFAULT_AGGREGATIONS: tuple[str, ...] = ("Average", "Minimum", "Maximum", "Total", "Count")

RESOURCE_ID_COLUMN = "resource_id"
METRIC_COLUMN = "metric"
BUCKET_COLUMN = "bucket_utc"

ProgressFn = Callable[[int, int, str], None] | None


class PartialCollectionError(RuntimeError):
    """Uma fatia falhou; a task não pode ser marcada COMPLETED."""

    def __init__(self, message: str, *, failures: list[dict[str, Any]]):
        super().__init__(message)
        self.failures = failures


def _subscriptions(config: dict[str, Any]) -> list[str]:
    return [str(s) for s in (config.get("subscription_ids") or []) if s]


def _vm_query() -> str:
    return (
        "resources "
        "| where type =~ 'microsoft.compute/virtualmachines' "
        "| project resource_id = id, subscription_id = subscriptionId, "
        "          resource_group = resourceGroup, location = location, "
        "          vm_size = tostring(properties.hardwareProfile.vmSize), "
        "          power_state = tostring(properties.extended.instanceView.powerState.code), "
        "          os_type = tostring(properties.storageProfile.osDisk.osType)"
    )


async def discover_vms(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    subscriptions: list[str] | None,
) -> list[dict[str, Any]]:
    """Lista VMs via ARG, paginando até esgotar `$skipToken`."""
    rows: list[dict[str, Any]] = []
    skip_token: str | None = None
    while True:
        options: dict[str, Any] = {"$top": 1000}
        if skip_token:
            options["$skipToken"] = skip_token
        body: dict[str, Any] = {"query": _vm_query(), "options": options}
        if subscriptions:
            body["subscriptions"] = subscriptions
        response = await client.post(
            f"{ARG_ENDPOINT}?api-version={ARG_API_VERSION}",
            headers=headers, json=body, timeout=90.0,
        )
        if response.status_code in (401, 403):
            raise PermissionError(
                f"Azure Resource Graph {response.status_code}: sem permissão para listar VMs"
            )
        if response.status_code >= 400:
            raise RuntimeError(f"Azure Resource Graph {response.status_code}: {response.text[:300]}")
        payload = response.json()
        data = payload.get("data")
        if isinstance(data, list):
            # `resultFormat` padrão: a API devolve uma lista de objetos já
            # nomeados pela projeção da query (objectArray).
            rows.extend(dict(item) for item in data if isinstance(item, dict))
        else:
            # `resultFormat: table`: data = {"columns": [...], "rows": [[...]]}
            columns = [c.get("name") for c in ((data or {}).get("columns") or [])]
            for raw in (data or {}).get("rows") or []:
                rows.append({columns[i]: raw[i] for i in range(min(len(columns), len(raw)))})
        skip_token = payload.get("$skipToken")
        if not skip_token:
            return rows


def _subscription_from_resource_id(resource_id: str) -> str | None:
    parts = str(resource_id).split("/")
    try:
        return parts[[p.lower() for p in parts].index("subscriptions") + 1]
    except (ValueError, IndexError):
        return None


def chunk_batches(vms: list[dict[str, Any]], size: int = MAX_RESOURCES_PER_BATCH) -> list[list[dict[str, Any]]]:
    """Agrupa VMs por (subscription, região) respeitando o teto por requisição.

    O `metrics:getBatch` exige mesma subscription, mesma região e mesmo tipo.
    Regiões são agrupadas **antes** do corte por tamanho para nunca misturar.
    """
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    order: list[tuple[str, str]] = []
    for vm in vms:
        key = (str(vm.get("subscription_id") or ""), str(vm.get("location") or ""))
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(vm)

    batches: list[list[dict[str, Any]]] = []
    for key in order:
        group = buckets[key]
        for start in range(0, len(group), size):
            batches.append(group[start:start + size])
    return batches


async def _post_batch_with_retry(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    resource_ids: list[str],
    *,
    max_attempts: int = 5,
) -> httpx.Response:
    """POST do batch com backoff exponencial e jitter para 429/5xx/529."""
    delay = 1.0
    last: httpx.Response | None = None
    for attempt in range(max_attempts):
        response = await client.post(
            url, headers=headers, json={"resourceids": resource_ids}, timeout=120.0,
        )
        if response.status_code < 400:
            return response
        last = response
        if response.status_code in (429, 529) or response.status_code >= 500:
            retry_after = response.headers.get("Retry-After")
            wait = float(retry_after) if (retry_after or "").replace(".", "", 1).isdigit() else delay
            wait += random.uniform(0.0, 0.5 * wait)
            logger.warning(
                "Monitor batch %s — retry %d/%d em %.1fs (%d recursos)",
                response.status_code, attempt + 1, max_attempts, wait, len(resource_ids),
            )
            await asyncio.sleep(wait)
            delay = min(delay * 2, 60.0)
            continue
        return response
    assert last is not None
    return last


def _block_metric_name(block: dict[str, Any], index: int, requested: tuple[str, ...]) -> str:
    """Nome da métrica de um bloco da resposta.

    A API devolve `name.value`; quando ausente, cai para a ordem pedida (a API
    preserva a ordem de `metricnames`).
    """
    name = block.get("name")
    if isinstance(name, dict) and name.get("value"):
        return str(name["value"])
    if index < len(requested):
        return requested[index]
    return f"metric_{index}"


def _rows_from_batch_multi(
    payload: dict[str, Any],
    requested: tuple[str, ...],
    *,
    vm_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Converte a resposta do batch (uma por recurso) em linhas por bucket.

    O batch devolve um item por recurso em `values[]`, cada um com `value[]`
    contendo **um bloco por métrica pedida**. Métrica sem série vira registro de
    ausência com motivo — nunca zero.
    """
    rows: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []

    def _base(resource_id: str) -> dict[str, Any]:
        vm = vm_by_id.get(str(resource_id).lower(), {})
        return {
            RESOURCE_ID_COLUMN: resource_id,
            "subscription_id": vm.get("subscription_id") or _subscription_from_resource_id(resource_id),
            "resource_group": vm.get("resource_group"),
            "location": vm.get("location"),
            "vm_size": vm.get("vm_size"),
            "power_state": vm.get("power_state"),
            "os_type": vm.get("os_type"),
            "resource_name": str(resource_id).rsplit("/", 1)[-1],
        }

    for item in payload.get("values") or []:
        resource_id = item.get("resourceid") or item.get("resourceId")
        if not resource_id:
            continue
        base = _base(str(resource_id))
        metric_blocks = item.get("value") or []
        if not metric_blocks:
            error = item.get("error")
            message = error.get("message") if isinstance(error, dict) else None
            gaps.append({
                **base, METRIC_COLUMN: None, "quality_status": "NOT_AVAILABLE",
                "gap_reason": (message or "API não retornou bloco de métrica para o recurso")[:400],
            })
            continue
        found: set[str] = set()
        for index, block in enumerate(metric_blocks):
            metric = _block_metric_name(block, index, requested)
            found.add(metric)
            if block.get("errorCode") and block.get("errorCode") != "Success":
                gaps.append({
                    **base, METRIC_COLUMN: metric, "quality_status": "NOT_AVAILABLE",
                    "gap_reason": f"errorCode={block.get('errorCode')}",
                })
                continue
            unit = block.get("unit")
            series_list = block.get("timeseries") or []
            if not series_list:
                gaps.append({
                    **base, METRIC_COLUMN: metric, "quality_status": "NO_DATA",
                    "gap_reason": "timeseries vazio na janela pedida",
                })
                continue
            for series in series_list:
                metadata = series.get("metadatavalues") or []
                dimension = json.dumps(
                    {m.get("name", {}).get("value"): m.get("value") for m in metadata},
                    ensure_ascii=False, sort_keys=True,
                ) if metadata else None
                for point in series.get("data") or []:
                    bucket = point.get("timeStamp")
                    if not bucket:
                        continue
                    # Um ponto sem nenhuma agregação é **ausência**, não um
                    # valor zero: a VM estava desligada/desalocada ou não
                    # reportou no bucket. Marcar como OK esconderia isso de quem
                    # consome a série e sugeriria consumo nulo.
                    valores = [
                        point.get("average"), point.get("minimum"),
                        point.get("maximum"), point.get("total"), point.get("count"),
                    ]
                    sem_amostra = all(v is None for v in valores)
                    rows.append({
                        **base, METRIC_COLUMN: metric, "dimension": dimension,
                        BUCKET_COLUMN: bucket,
                        "average": point.get("average"),
                        "minimum": point.get("minimum"),
                        "maximum": point.get("maximum"),
                        "total": point.get("total"),
                        "count": point.get("count"),
                        "unit": unit,
                        "api_version": METRICS_API_VERSION,
                        "quality_status": "GAP" if sem_amostra else "OK",
                        "gap_reason": (
                            "sem amostra no bucket (VM desligada/desalocada ou sem reporte)"
                            if sem_amostra else None
                        ),
                    })
        # Métrica pedida que não veio em bloco nenhum: registra a ausência com o
        # nome correto (a API pode omitir a métrica quando não existe no SKU).
        for metric in requested:
            if metric not in found:
                gaps.append({
                    **base, METRIC_COLUMN: metric, "quality_status": "NOT_AVAILABLE",
                    "gap_reason": "métrica ausente na resposta do batch (não exposta pelo SKU)",
                })
    return rows, gaps


def _guest_gap_rows(vms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Registra as métricas guest previstas no plano como indisponíveis.

    A API batch não atende guest metrics; sem Azure Monitor Agent/DCR não há
    série. Registrar o motivo evita que a ausência seja lida como "SKU não
    expõe" ou, pior, estimada.
    """
    gaps: list[dict[str, Any]] = []
    for vm in vms:
        for metric in GUEST_METRICS:
            gaps.append({
                RESOURCE_ID_COLUMN: vm.get("resource_id"), METRIC_COLUMN: metric,
                "subscription_id": vm.get("subscription_id"),
                "location": vm.get("location"), "vm_size": vm.get("vm_size"),
                "resource_name": str(vm.get("resource_id") or "").rsplit("/", 1)[-1],
                "quality_status": "NOT_AVAILABLE",
                "gap_reason": "métrica guest: exige Azure Monitor Agent/DCR; não suportada pela API batch",
            })
    return gaps


def anchor_until(now: datetime | None = None, *, granularity_minutes: int = 0) -> datetime:
    """Fim da janela, ancorado no **último dia completo** — não no dia da execução.

    A chave da fatia Bronze inclui o instante final. Com `now()` cru, duas
    execuções da mesma janela geram caminhos diferentes e a coleta **duplica**
    em vez de substituir — foi o que produziu 1.230.004 linhas para 615.002
    reais, com as fatias `20260922T0000` e `20260923T0000`.

    A âncora é o **dia da execução** (00:00Z): assim, repetir a coleta no mesmo
    dia reescreve a mesma fatia, que é o aceite do P5 ("reexecutar os 15 dias
    não duplica"). Coletar em outro dia produz janela nova — correto, porque a
    janela de 15 dias de fato mudou.

    Para reprocessar uma janela histórica exata, o chamador passa `until`
    explícito: a âncora só normaliza o instante, não decide a janela.
    """
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if granularity_minutes and granularity_minutes > 0:
        minutes = (current.minute // granularity_minutes) * granularity_minutes
        return current.replace(minute=minutes, second=0, microsecond=0)
    return current.replace(hour=0, minute=0, second=0, microsecond=0)


async def collect_metrics(
    config: dict[str, Any],
    *,
    window_days: int = 15,
    until: datetime | None = None,
    metrics: tuple[str, ...] | None = None,
    progress: ProgressFn = None,
) -> dict[str, Any]:
    """Coleta a série das VMs do escopo via batch, fatiando a janela por dia."""
    window_days = max(1, min(int(window_days or 15), 90))
    metrics = tuple(metrics or config.get("metrics") or DEFAULT_METRICS)
    until = anchor_until(until)
    since = until - timedelta(days=window_days)

    started = datetime.now(timezone.utc)
    headers = {**(await arm_headers(config)), "Content-Type": "application/json"}
    all_rows: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    requests_made = 0
    rate_limited = 0
    vms: list[dict[str, Any]] = []

    async with httpx.AsyncClient() as client:
        vms = await discover_vms(client, headers, _subscriptions(config) or None)
        logger.info("Monitor VM: %d VMs descobertas (janela %dd)", len(vms), window_days)
        vms = [vm for vm in vms if vm.get("resource_id")]
        vm_by_id = {str(vm["resource_id"]).lower(): vm for vm in vms}
        gaps.extend(_guest_gap_rows(vms))

        batches = chunk_batches(vms)
        slices: list[tuple[datetime, datetime]] = []
        cursor = since
        while cursor < until:
            nxt = min(cursor + MAX_WINDOW_PER_REQUEST, until)
            slices.append((cursor, nxt))
            cursor = nxt

        total_steps = max(1, len(batches) * len(slices))
        step = 0
        for batch in batches:
            subscription = str(batch[0].get("subscription_id") or "")
            region = str(batch[0].get("location") or "")
            resource_ids = [str(vm["resource_id"]) for vm in batch]
            # Todas as métricas vão numa requisição só: a API aceita lista em
            # `metricnames` e devolve um bloco por métrica. Isso corta o número
            # de chamadas por um fator igual ao número de métricas.
            for slice_since, slice_until in slices:
                step += 1
                if progress:
                    progress(step, total_steps, f"{region} · {len(batch)} VM(s) · {len(metrics)} métrica(s)")
                url = (
                    f"https://{region}.metrics.monitor.azure.com/subscriptions/{subscription}"
                    f"/metrics:getBatch?starttime={slice_since.strftime('%Y-%m-%dT%H:%M:%SZ')}"
                    f"&endtime={slice_until.strftime('%Y-%m-%dT%H:%M:%SZ')}"
                    f"&interval=PT15M&metricNamespace={httpx.QueryParams({'n': METRIC_NAMESPACE})['n']}"
                    f"&metricnames={httpx.QueryParams({'m': ','.join(metrics)})['m']}"
                    f"&aggregation={','.join(DEFAULT_AGGREGATIONS)}"
                    f"&api-version={METRICS_API_VERSION}"
                )
                try:
                    response = await _post_batch_with_retry(client, url, headers, resource_ids)
                    requests_made += 1
                except Exception as exc:  # noqa: BLE001
                    failures.append({
                        "subscription": subscription, "region": region,
                        "metric": ",".join(metrics),
                        "window": f"{slice_since.isoformat()}/{slice_until.isoformat()}",
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    continue
                if response.status_code in (401, 403):
                    raise PermissionError(
                        f"Azure Monitor batch {response.status_code} em {region}: é preciso "
                        "Monitoring Reader na subscription e o provider Microsoft.Insights "
                        "registrado. " + response.text[:200]
                    )
                if response.status_code == 429:
                    rate_limited += 1
                if response.status_code >= 400:
                    failures.append({
                        "subscription": subscription, "region": region,
                        "metric": ",".join(metrics),
                        "window": f"{slice_since.isoformat()}/{slice_until.isoformat()}",
                        "error": f"HTTP {response.status_code}: {response.text[:200]}",
                    })
                    continue
                rows, row_gaps = _rows_from_batch_multi(
                    response.json(), metrics, vm_by_id=vm_by_id,
                )
                all_rows.extend(rows)
                gaps.extend(row_gaps)

    finished = datetime.now(timezone.utc)
    frame = pd.DataFrame(all_rows)
    return {
        "frame": frame,
        "gaps": pd.DataFrame(gaps),
        "vms": len(vms),
        "metrics": list(metrics),
        "window_days": window_days,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "api_version": METRICS_API_VERSION,
        "requests": requests_made,
        "rate_limited": rate_limited,
        "failures": failures,
        "duration_seconds": round((finished - started).total_seconds(), 1),
        "rows": int(len(frame)),
        "gap_rows": int(len(gaps)),
        # Pontos sem amostra não são série: separá-los deixa o resumo honesto e
        # evita que a ausência seja lida como consumo.
        "empty_rows": int((frame["quality_status"] == "GAP").sum()) if "quality_status" in frame.columns else 0,
        "series_rows": int((frame["quality_status"] != "GAP").sum()) if "quality_status" in frame.columns else int(len(frame)),
    }


def bronze_blob_path(subscription_id: str, region: str, until: datetime, window_days: int, metric: str) -> str:
    """Chave lógica estável da fatia — reexecutar a mesma janela substitui."""
    safe_metric = metric.replace("/", "_").replace(" ", "_")
    return (
        f"monitor/{subscription_id or 'all'}/{region or 'all'}"
        f"/{until.strftime('%Y%m%dT%H%M')}/{int(window_days)}d/{safe_metric}.csv"
    )


def _frame_for_bronze(frame: pd.DataFrame, gaps: pd.DataFrame) -> pd.DataFrame:
    """Une série e ausências numa única Bronze.

    As duas coisas precisam ficar no mesmo payload: a ausência **é** evidência
    (cobertura, motivo) e não pode ser descartada nem virar zero.
    """
    common = [
        RESOURCE_ID_COLUMN, "subscription_id", "resource_group", "location",
        "vm_size", "power_state", "os_type", "resource_name",
        METRIC_COLUMN, "dimension", BUCKET_COLUMN,
        "average", "minimum", "maximum", "total", "count", "unit",
        "api_version", "quality_status", "gap_reason",
    ]
    parts: list[pd.DataFrame] = []
    if frame is not None and not frame.empty:
        series = frame.copy()
        series["record_kind"] = "SERIES"
        parts.append(series)
    if gaps is not None and not gaps.empty:
        absent = gaps.copy()
        absent["record_kind"] = "GAP"
        parts.append(absent)
    if not parts:
        return pd.DataFrame(columns=common + ["record_kind"])
    combined = pd.concat(parts, ignore_index=True, sort=False)
    for column in common:
        if column not in combined.columns:
            combined[column] = None
    return combined


def run_monitor_vm_collection(
    session,
    connection_id: int,
    *,
    window_days: int | None = None,
    until: datetime | None = None,
    task_id: int | None = None,
    source_code: str | None = None,
) -> dict[str, Any]:
    """Coleta completa de uma conexão e escrita na Bronze exclusiva.

    Segue o padrão de Automation/ARG: resolve a conexão, chama o coletor,
    persiste a fatia por (subscription, região, janela, métrica) e registra
    `IngestedBlob`. Erro parcial **não** marca sucesso: levanta
    `PartialCollectionError` para o worker falhar a task.
    """
    from app.models.ingested_blob import IngestedBlob
    from app.models.source_connection import SourceConnection
    from app.services import iceberg_service as iceberg

    connection = session.get(SourceConnection, connection_id)
    if connection is None:
        raise ValueError(f"conexão {connection_id} não encontrada")

    config = dict(connection.config_json or {})
    days = int(window_days or config.get("window_days") or 15)

    progress = None
    if task_id is not None:
        from app.models.etl_task import EtlTask

        # O progresso é atualizado na linha da task **sem commit**: a coleta roda
        # dentro da transação do worker, e commitar a cada fatia disputava lock
        # com o próprio worker (deadlock em `etl_task`). Automation e ARG seguem
        # o mesmo padrão — o worker persiste o resultado ao terminar.
        def progress(step: int, total: int, label: str) -> None:
            row = session.get(EtlTask, task_id)
            if row is not None:
                row.phase = f"Azure Monitor VM: {step}/{total} · {label}"

    result = asyncio.run(collect_metrics(
        config, window_days=days, until=until, progress=progress,
    ))

    if result["failures"]:
        raise PartialCollectionError(
            f"{len(result['failures'])} fatia(s) de métrica falharam; "
            f"{result['rows']} linha(s) coletada(s) antes das falhas",
            failures=result["failures"],
        )

    payload = _frame_for_bronze(result["frame"], result["gaps"])
    if payload.empty:
        return {
            "bronze_rows": 0, "series_rows": 0, "gap_rows": 0,
            "vms": result["vms"], "periods": [], "slices": [],
            "duration_seconds": result["duration_seconds"],
            "requests": result["requests"], "rate_limited": result["rate_limited"],
            "failures": [], "message": "nenhuma métrica retornada",
        }

    until_dt = datetime.fromisoformat(result["until"])
    written = 0
    slices: list[str] = []
    periods: set[str] = set()
    for (subscription, region), group in payload.groupby(
        ["subscription_id", "location"], dropna=False
    ):
        # Uma fatia por (subscription, região, janela): reexecutar a mesma
        # janela substitui exatamente esta fatia e não toca nas outras.
        blob_path = (
            f"monitor/{subscription or 'all'}/{region or 'all'}"
            f"/{until_dt.strftime('%Y%m%dT%H%M')}/{days}d/metrics.csv"
        )
        period = until_dt.strftime("%Y%m")
        written += _write_bronze_and_register(
            session, connection, group.reset_index(drop=True), period, blob_path,
            source_code=source_code,
        )
        slices.append(blob_path)
        periods.add(period)

    return {
        "bronze_rows": written,
        "series_rows": result["series_rows"],
        "gap_rows": result["gap_rows"],
        "vms": result["vms"],
        "metrics": result["metrics"],
        "window_days": days,
        "since": result["since"],
        "until": result["until"],
        "periods": sorted(periods),
        "slices": slices,
        "duration_seconds": result["duration_seconds"],
        "requests": result["requests"],
        "rate_limited": result["rate_limited"],
        "api_version": result["api_version"],
        "failures": [],
    }


def _write_bronze_and_register(session, connection, df: pd.DataFrame, period: str, blob_path: str, *, source_code: str | None = None) -> int:
    """Escreve a fatia na Bronze e registra o `IngestedBlob` (padrão ARG)."""
    if df.empty:
        return 0
    from app.models.ingested_blob import IngestedBlob
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

    existing = session.query(IngestedBlob).filter_by(
        connection_id=connection.id, blob_path=blob_path
    ).first()
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


async def discover_scope(config: dict[str, Any]) -> dict[str, Any]:
    """Teste de capacidade da fonte: VMs visíveis, regiões e permissão batch.

    Não executa coleta completa — só prova que a credencial enxerga VMs e que a
    API de métricas responde (ou registra o motivo exato da indisponibilidade).
    """
    headers = {**(await arm_headers(config)), "Content-Type": "application/json"}
    async with httpx.AsyncClient() as client:
        vms = await discover_vms(client, headers, _subscriptions(config) or None)
        regions = sorted({str(vm.get("location")) for vm in vms if vm.get("location")})
        subscriptions = sorted({str(vm.get("subscription_id")) for vm in vms if vm.get("subscription_id")})

        metrics_ok: bool | None = None
        metrics_error: str | None = None
        if vms:
            sample = next((vm for vm in vms if vm.get("location")), None)
            if sample:
                until = datetime.now(timezone.utc).replace(second=0, microsecond=0)
                since = until - timedelta(hours=1)
                url = (
                    f"https://{sample['location']}.metrics.monitor.azure.com"
                    f"/subscriptions/{sample['subscription_id']}/metrics:getBatch"
                    f"?starttime={since.strftime('%Y-%m-%dT%H:%M:%SZ')}"
                    f"&endtime={until.strftime('%Y-%m-%dT%H:%M:%SZ')}"
                    f"&interval=PT15M&metricNamespace={httpx.QueryParams({'n': METRIC_NAMESPACE})['n']}"
                    f"&metricnames={httpx.QueryParams({'m': 'Percentage CPU'})['m']}"
                    f"&aggregation=Average&api-version={METRICS_API_VERSION}"
                )
                response = await _post_batch_with_retry(client, url, headers, [str(sample["resource_id"])])
                metrics_ok = response.status_code < 400
                if not metrics_ok:
                    metrics_error = f"HTTP {response.status_code}: {response.text[:200]}"

    return {
        "vms": len(vms),
        "subscriptions": subscriptions,
        "regions": regions,
        "metrics_api_ok": metrics_ok,
        "metrics_api_error": metrics_error,
        "metrics_available": list(DEFAULT_METRICS),
        "metrics_guest_unsupported": list(GUEST_METRICS),
    }
