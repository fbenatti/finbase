"""API da seleção de fontes Azure agrupada por ambiente (P2).

O operador enxerga **um grupo por ambiente/tenant** com todas as fontes
selecionáveis. Cada fonte continua sendo um registro `source_connection` próprio,
porque o pipeline vincula Bronze a `code` — mas isso é detalhe interno e não
aparece como dezenas de cards para configurar.

Endpoints:

- `GET  /azure-sources/`                      catálogo agrupado + estado por fonte;
- `GET  /azure-sources/catalog`               catálogo declarativo (todas as fontes possíveis);
- `POST /azure-sources/{conn_id}/test`        teste de capacidade da fonte (sem coletar);
- `PUT  /azure-sources/{conn_id}/selection`   ligar/desligar e ajustar a janela;
- `POST /azure-sources/environments/{env}/collect`  coletar as fontes ligadas do ambiente.

Nada aqui expõe segredo: o estado devolvido é sanitizado.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.etl_task import EtlTask
from app.models.ingested_blob import IngestedBlob
from app.models.source_connection import SourceConnection
from app.services.azure_source_registry import (
    AZURE_SOURCE_CATALOG,
    accepted_code_pattern,
    catalog_entry,
    environment_slug,
    is_telemetry,
    sanitized_bronze,
    source_key_for,
)
from app.services.cost_sources import is_cost_source

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/azure-sources", tags=["azure-sources"])

#: Estados possíveis por fonte (o plano exige estes cinco).
STATE_NOT_CONFIGURED = "NOT_CONFIGURED"
STATE_AVAILABLE = "AVAILABLE"
STATE_PARTIAL = "PARTIAL"
STATE_UNAVAILABLE = "UNAVAILABLE"
STATE_ERROR = "ERROR"

#: Fontes que não são de custo nunca podem alimentar o FOCUS.
_FOCUS_DESTINATION_ID = 1


class SourceSelectionUpdate(BaseModel):
    """Liga/desliga a coleta de uma fonte e ajusta os parâmetros da conexão."""

    enabled: bool | None = None
    window_days: int | None = Field(default=None, ge=1, le=90)
    metrics: list[str] | None = None
    subscription_ids: list[str] | None = None


class EnvironmentCollectRequest(BaseModel):
    """Coleta as fontes **ligadas** de um ambiente, opcionalmente só algumas."""

    source_keys: list[str] | None = None
    window_days: int | None = Field(default=None, ge=1, le=90)


def _enabled(connection: SourceConnection) -> bool:
    """Fonte ligada? Fontes novas nascem ligadas; desligar é explícito."""
    config = dict(connection.config_json or {})
    value = config.get("collection_enabled")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "sim"}
    return True


def _source_state(connection: SourceConnection, *, last_blob, running: int) -> str:
    """Estado da fonte a partir de dados que já existem.

    Não inventa saúde: usa o último teste, o último blob ingerido e se há coleta
    em andamento. Um erro de uma fonte não afeta as outras.
    """
    if not _enabled(connection):
        return STATE_NOT_CONFIGURED
    status = (connection.last_test_status or "").upper()
    if status in {"FAILED", "ERRO", "ERROR"}:
        return STATE_ERROR
    if running:
        return STATE_AVAILABLE
    if last_blob is None:
        # Nunca coletou e nunca testou: ainda não configurada de fato.
        return STATE_NOT_CONFIGURED if not status else STATE_PARTIAL
    if status in {"OK", "PENDING"} or status == "":
        return STATE_AVAILABLE
    return STATE_PARTIAL


def _source_payload(connection: SourceConnection, entry: dict | None, *, last_blob, running: int) -> dict:
    config = dict(connection.config_json or {})
    key = source_key_for(connection)
    return {
        "connection_id": connection.id,
        "code": connection.code,
        "name": connection.name,
        "source_key": key,
        "display_name": (entry or {}).get("display_name") or connection.name,
        "description": (entry or {}).get("description"),
        "connector_type": connection.connector_type,
        "environment_slug": environment_slug(connection),
        "state": _source_state(connection, last_blob=last_blob, running=running),
        "enabled": _enabled(connection),
        "is_cost_source": is_cost_source(connection),
        "credential_profile": config.get("credential_profile"),
        "credential_configured": bool(
            config.get("client_secret") or config.get("credential_profile")
        ),
        "bronze_table": f"bronze.{sanitized_bronze(connection)}",
        "silver_target": (entry or {}).get("silver_target"),
        "gold_target": (entry or {}).get("gold_target"),
        "api_reference": (entry or {}).get("api_reference"),
        "required_permission": (entry or {}).get("required_permission"),
        "implementation_status": (entry or {}).get("implementation_status"),
        "scope": (entry or {}).get("azure_scope"),
        "position": (entry or {}).get("position", 500),
        "last_test_status": connection.last_test_status,
        "last_test_message": connection.last_test_message,
        "last_tested_at": connection.last_tested_at.isoformat() if connection.last_tested_at else None,
        "last_ingested_at": last_blob.ingested_at.isoformat() if last_blob and last_blob.ingested_at else None,
        "last_billing_period": last_blob.billing_period if last_blob else None,
        "rows": last_blob.row_count if last_blob else None,
        "window_days": config.get("window_days"),
        "metrics": config.get("metrics"),
        "subscription_count": len(config.get("subscription_ids") or []),
        "running_tasks": running,
    }


@router.get("/catalog")
async def get_catalog():
    """Catálogo declarativo: todas as fontes que podem ser ligadas por ambiente."""
    return {
        "sources": [
            {
                "source_key": entry["source_key"],
                "display_name": entry["display_name"],
                "description": entry.get("description"),
                "position": entry.get("position", 500),
                "code_pattern": entry.get("code_pattern"),
                "bronze_pattern": entry.get("bronze_pattern"),
                "silver_target": entry.get("silver_target"),
                "gold_target": entry.get("gold_target"),
                "is_cost_source": bool(entry.get("is_cost_source")),
                "api_reference": entry.get("api_reference"),
                "required_permission": entry.get("required_permission"),
                "implementation_status": entry.get("implementation_status"),
                "scope": entry.get("azure_scope"),
                "default_window_days": entry.get("default_window_days"),
                "default_metrics": entry.get("default_metrics") or [],
            }
            for entry in sorted(AZURE_SOURCE_CATALOG, key=lambda e: e.get("position", 500))
        ]
    }


@router.get("/")
async def list_azure_sources(
    environment: str | None = Query(None, description="Filtra por slug de ambiente (ex.: 4p)"),
    db: AsyncSession = Depends(get_db),
):
    """Catálogo agrupado por ambiente, com o estado real de cada fonte.

    Um ambiente reúne todas as suas fontes sob **uma** credencial, que é o que
    evita configurar dezenas de cards com o mesmo segredo.
    """
    connections = (await db.execute(
        select(SourceConnection)
        .where(SourceConnection.cloud_scope == "azure")
        .order_by(SourceConnection.code)
    )).scalars().all()

    connection_ids = [c.id for c in connections]
    last_blobs: dict[int, IngestedBlob] = {}
    running: dict[int, int] = {}
    if connection_ids:
        blobs = (await db.execute(
            select(IngestedBlob)
            .where(IngestedBlob.connection_id.in_(connection_ids))
            .order_by(IngestedBlob.connection_id, IngestedBlob.ingested_at.desc())
        )).scalars().all()
        for blob in blobs:
            last_blobs.setdefault(blob.connection_id, blob)

        tasks = (await db.execute(
            select(EtlTask.connection_id)
            .where(
                EtlTask.connection_id.in_(connection_ids),
                EtlTask.status.in_(["QUEUED", "RUNNING"]),
            )
        )).all()
        for (connection_id,) in tasks:
            if connection_id is not None:
                running[connection_id] = running.get(connection_id, 0) + 1

    groups: dict[str, dict] = {}
    for connection in connections:
        slug = environment_slug(connection)
        entry = catalog_entry(source_key_for(connection) or "")
        payload = _source_payload(
            connection, entry,
            last_blob=last_blobs.get(connection.id),
            running=running.get(connection.id, 0),
        )
        group = groups.setdefault(slug, {
            "environment_slug": slug,
            "credential_profiles": set(),
            "cloud_names": set(),
            "sources": [],
        })
        if payload["credential_profile"]:
            group["credential_profiles"].add(payload["credential_profile"])
        if connection.cloud_name:
            group["cloud_names"].add(connection.cloud_name)
        group["sources"].append(payload)

    result = []
    for slug, group in sorted(groups.items()):
        if environment and slug != environment:
            continue
        sources = sorted(group["sources"], key=lambda s: (s["position"], s["code"]))
        result.append({
            "environment_slug": slug,
            "credential_profiles": sorted(group["credential_profiles"]),
            "cloud_names": sorted(group["cloud_names"]),
            "source_count": len(sources),
            "enabled_count": sum(1 for s in sources if s["enabled"]),
            "states": {
                state: sum(1 for s in sources if s["state"] == state)
                for state in (
                    STATE_AVAILABLE, STATE_PARTIAL, STATE_NOT_CONFIGURED,
                    STATE_UNAVAILABLE, STATE_ERROR,
                )
            },
            "sources": sources,
        })
    return {"environments": result, "total_environments": len(result)}


@router.post("/{connection_id}/test")
async def test_azure_source(connection_id: int, db: AsyncSession = Depends(get_db)):
    """Teste de capacidade da fonte — nunca executa a coleta completa."""
    connection = await db.get(SourceConnection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Conexão não encontrada")
    if connection.cloud_scope != "azure":
        raise HTTPException(status_code=422, detail="Conexão não é de escopo Azure")

    config = dict(connection.config_json or {})
    connector = connection.connector_type
    try:
        if connector == "azure_resource_graph":
            from app.services.azure_resource_graph_service import discover_scope
            subs = await discover_scope(config)
            capability = {
                "subscriptions": len(subs),
                "resources": sum(int(s.get("resource_count") or 0) for s in subs),
            }
        elif connector == "azure_automation_jobs":
            from app.services.azure_automation_service import discover_accounts
            accounts = await discover_accounts(config)
            capability = {"automation_accounts": len(accounts)}
        elif connector == "azure_monitor_vm_metrics":
            from app.services.azure_monitor_vm_service import discover_scope as discover_monitor
            scope = await discover_monitor(config)
            capability = {
                "vms": scope["vms"],
                "subscriptions": len(scope["subscriptions"]),
                "regions": scope["regions"],
                "metrics_api_ok": scope["metrics_api_ok"],
                "metrics_api_error": scope["metrics_api_error"],
                "metrics_available": scope["metrics_available"],
                "metrics_guest_unsupported": scope["metrics_guest_unsupported"],
            }
        elif connector == "azure_openai_ptu":
            from app.services.azure_ptu_service import discover as discover_ptu
            found = await discover_ptu(config)
            capability = {"accounts": len(found) if isinstance(found, list) else found}
        elif connector == "azure_blob":
            from app.services.azure_extractor import AzureExtractor
            extractor = AzureExtractor(connection)
            items = extractor._scan_sync_for_test() if hasattr(extractor, "_scan_sync_for_test") else []
            capability = {"blobs": len(items)}
        else:
            entry = catalog_entry(source_key_for(connection) or "")
            return {
                "connection_id": connection_id,
                "state": STATE_UNAVAILABLE,
                "message": (
                    f"Fonte '{(entry or {}).get('display_name', connector)}' ainda não "
                    "implementada — sem teste de capacidade disponível."
                ),
                "capability": None,
            }
    except Exception as exc:  # noqa: BLE001 - o estado da fonte é o resultado
        connection.last_test_status = "FAILED"
        connection.last_test_message = str(exc)[:500]
        connection.last_tested_at = datetime.now(timezone.utc)
        await db.commit()
        logger.warning("Teste da fonte %s (%s) falhou: %s", connection.code, connector, exc)
        return {
            "connection_id": connection_id,
            "state": STATE_ERROR,
            "message": str(exc)[:500],
            "capability": None,
        }

    connection.last_test_status = "OK"
    connection.last_test_message = str(capability)[:500]
    connection.last_tested_at = datetime.now(timezone.utc)
    await db.commit()
    return {
        "connection_id": connection_id,
        "code": connection.code,
        "source_key": source_key_for(connection),
        "state": STATE_AVAILABLE,
        "message": "Teste de capacidade concluído",
        "capability": capability,
    }


@router.put("/{connection_id}/selection")
async def update_source_selection(
    connection_id: int,
    body: SourceSelectionUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Liga/desliga a fonte e ajusta janela/métricas/subscriptions."""
    connection = await db.get(SourceConnection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Conexão não encontrada")

    source_key = source_key_for(connection)
    config = dict(connection.config_json or {})
    if body.enabled is not None:
        config["collection_enabled"] = bool(body.enabled)
    if is_telemetry(source_key):
        if body.window_days is not None:
            config["window_days"] = int(body.window_days)
        if body.metrics is not None:
            config["metrics"] = list(body.metrics)
    elif body.window_days is not None:
        raise HTTPException(
            status_code=422,
            detail="Fonte não é de telemetria: janela em dias só se aplica a métricas.",
        )
    if body.subscription_ids is not None:
        config["subscription_ids"] = list(body.subscription_ids)

    connection.config_json = config
    await db.commit()
    await db.refresh(connection)
    return _source_payload(connection, catalog_entry(source_key or ""), last_blob=None, running=0)


@router.post("/environments/{environment_slug_value}/collect")
async def collect_environment(
    environment_slug_value: str,
    body: EnvironmentCollectRequest,
    db: AsyncSession = Depends(get_db),
):
    """Enfileira a coleta das fontes **ligadas** de um ambiente.

    Esta é a operação que o operador espera: escolher o ambiente e mandar coletar
    tudo o que está selecionado, sem abrir fonte por fonte.
    """
    from app.services.worker_runtime import any_worker_healthy

    if not any_worker_healthy():
        raise HTTPException(status_code=503, detail="Worker ETL indisponível; nada foi enfileirado")

    connections = (await db.execute(
        select(SourceConnection).where(SourceConnection.cloud_scope == "azure")
    )).scalars().all()

    selected = [
        c for c in connections
        if environment_slug(c) == environment_slug_value
        and _enabled(c)
        and (not body.source_keys or source_key_for(c) in set(body.source_keys))
    ]
    if not selected:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Nenhuma fonte ligada no ambiente '{environment_slug_value}'"
                + (" para as fontes pedidas." if body.source_keys else ".")
            ),
        )

    from sqlalchemy import insert

    queued: list[dict] = []
    skipped: list[dict] = []
    for connection in selected:
        source_key = source_key_for(connection)
        entry = catalog_entry(source_key or "") or {}
        if entry.get("implementation_status") == "planned":
            skipped.append({
                "connection_id": connection.id,
                "code": connection.code,
                "source_key": source_key,
                "reason": "fonte planejada, ainda sem coletor",
            })
            continue

        config = dict(connection.config_json or {})
        days = body.window_days or (
            int(config["window_days"]) if is_telemetry(source_key) and config.get("window_days") else None
        )
        result_json: dict = {"user_triggered": True, "environment_slug": environment_slug_value}
        phase = "Na fila: coleta"
        if is_telemetry(source_key):
            window = max(1, min(int(days or entry.get("default_window_days") or 15), 90))
            result_json.update({"window_days": window, "metric_window": True})
            phase = f"Na fila: coletar {entry.get('display_name', connection.name)} ({window} dias)"
        else:
            phase = f"Na fila: coletar {entry.get('display_name', connection.name)}"

        task_id = (await db.execute(
            insert(EtlTask).values(
                task_type="extraction", status="QUEUED",
                connection_id=connection.id, destination_table_id=None,
                connection_ids=[connection.id], billing_periods=[],
                selected_blobs=[], total_blobs=0, processed_blobs=0,
                rows_raw=0, rows_normalized=0, rows_destination=0,
                errors=[], phase=phase, result_json=result_json,
            ).returning(EtlTask.id)
        )).scalar_one()
        queued.append({
            "task_id": task_id,
            "connection_id": connection.id,
            "code": connection.code,
            "source_key": source_key,
            "display_name": entry.get("display_name") or connection.name,
        })

    await db.commit()
    return {
        "environment_slug": environment_slug_value,
        "queued": queued,
        "skipped": skipped,
        "message": (
            f"{len(queued)} coleta(s) enfileirada(s) no ambiente '{environment_slug_value}'"
            + (f"; {len(skipped)} fonte(s) planejada(s) ignorada(s)" if skipped else "")
        ),
    }
