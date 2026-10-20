"""API da gestão de anomalias de custo cloud (E7/E8).

Transições são **comandos explícitos**, não edição livre de `status`. Toda
transição valida o estado permitido, os campos obrigatórios e a versão do caso
(conflito concorrente → **409**).

Listagem: paginação padrão 50, máximo 200; ordenação por nota ou data.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.deps import get_current_user, require
from app.core.rbac import get_effective_permissions, has_permission
from app.database import get_db
from app.models.anomaly import (
    AnomalyAction,
    AnomalyCase,
    AnomalyCaseEvent,
    AnomalyConfig,
    AnomalyPublication,
    AnomalyRun,
)
from app.models.identity import User
from app.services import anomaly_workflow as workflow
from app.services.anomaly_v2.settings import detector_settings, validate_detector_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/anomalies", tags=["anomalies"])

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class ConfigUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    profile: Literal["CONSERVATIVE", "BALANCED", "SENSITIVE"] | None = None
    daily_quantile: float | None = Field(default=None, gt=0, lt=1)
    min_relative_deviation: float | None = Field(default=None, ge=0)
    investigate_score: int | None = Field(default=None, ge=0, le=100)
    monthly_tolerance_pct: float | None = Field(default=None, ge=0)
    materiality_by_currency: dict[str, float] | None = None
    currency: str | None = None
    history_days: int | None = Field(default=None, ge=1, le=1095)
    calibration_days: int | None = Field(default=None, ge=1, le=365)
    horizon_days: int | None = Field(default=None, ge=1, le=92)
    data_lag_days: int | None = Field(default=None, ge=0, le=30)
    first_run_days: int | None = Field(default=None, ge=1, le=366)
    reevaluation_days: int | None = Field(default=None, ge=1, le=90)
    family_keys: list[str] | None = None
    routing_rules: list[dict[str, Any]] | None = None
    sla_hours: dict[str, int] | None = None
    notification_enabled: bool | None = None
    enabled: bool | None = None
    detection_engine: Literal["v1", "v2"] | None = None
    materiality_monthly: float | None = Field(default=None, ge=0)
    severity_tiers: dict[str, float] | None = None
    detector_settings: dict[str, Any] | None = None


class TransitionRequest(BaseModel):
    command: str
    case_version: int | None = None
    assignee_user_id: int | None = None
    routing_rule_id: str | None = None
    decision: str | None = None
    cause_category: str | None = None
    cause_hypothesis: str | None = None
    decision_note: str | None = None
    verification_outcome: str | None = None
    verification_note: str | None = None
    verification_reference_id: str | None = None
    learning: str | None = None
    preventive_measure: str | None = None
    no_preventive_measure_reason: str | None = None
    evidence: list[str] | None = None
    note: str | None = None


class ActionCreate(BaseModel):
    description: str = Field(min_length=1, max_length=1000)
    action_type: Literal["CORRECTIVE", "PREVENTIVE"] = "CORRECTIVE"
    owner_user_id: int | None = None
    due_at: datetime | None = None
    mandatory: bool = True


class ActionUpdate(BaseModel):
    description: str | None = None
    status: Literal["PLANNED", "IN_PROGRESS", "COMPLETED", "CANCELLED"] | None = None
    owner_user_id: int | None = None
    due_at: datetime | None = None
    evidence: str | None = None
    external_reference: str | None = None
    mandatory: bool | None = None


class RunRequest(BaseModel):
    trigger_type: Literal["MANUAL", "SCHEDULED"] = "MANUAL"


def _page(limit: int, offset: int) -> tuple[int, int]:
    size = max(1, min(int(limit), MAX_PAGE_SIZE))
    return size, max(0, int(offset))


async def _require_scope(db: AsyncSession, user: User, scope: str) -> None:
    """Escopo específico do comando, exigido dentro do handler.

    O middleware cobre o módulo e a ação (`update`); o escopo
    (`assign`/`investigate`/`decide`/`close`) depende do comando escolhido no
    corpo e por isso é validado aqui.
    """
    permissions = await get_effective_permissions(db, user.id)
    if not has_permission(permissions, "anomalies", "update", scope):
        raise HTTPException(403, f"Sem permissão para o comando ({scope}).")


def _dim(case: AnomalyCase, *keys: str) -> str | None:
    raw = case.series_dimensions if isinstance(case.series_dimensions, dict) else {}
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _case_out(case: AnomalyCase) -> dict[str, Any]:
    return {
        "id": case.id,
        "case_key": case.case_key,
        "state": case.state,
        "severity": case.severity,
        "score": case.score,
        "confidence": case.confidence,
        "event_type": case.event_type,
        "event_family": case.event_family,
        "event_date": case.event_date,
        "period": case.period,
        "source_billing_period": case.source_billing_period,
        "series_id": case.series_id,
        "series_dimensions": case.series_dimensions,
        "observed_cloud": case.observed_cloud,
        "expected_cloud": case.expected_cloud,
        "lower_cloud": case.lower_cloud,
        "upper_cloud": case.upper_cloud,
        "delta_cloud": case.delta_cloud,
        "anomalous_cost_cloud": case.anomalous_cost_cloud,
        "evaluation_grain": case.evaluation_grain,
        "current_reference_id": case.current_reference_id,
        "current_assessment_id": case.current_assessment_id,
        "reference_kind": case.reference_kind,
        "reason_codes": case.reason_codes,
        "applied_rule_refs": case.applied_rule_refs,
        "data_quality": case.data_quality,
        "assignee_user_id": case.assignee_user_id,
        "routing_rule_id": case.routing_rule_id,
        "due_at": case.due_at.isoformat() if case.due_at else None,
        "acknowledged_at": case.acknowledged_at.isoformat() if case.acknowledged_at else None,
        "investigation_started_at": _iso(case.investigation_started_at),
        "cause_category": case.cause_category,
        "cause_hypothesis": case.cause_hypothesis,
        "decision": case.decision,
        "decision_note": case.decision_note,
        "decided_at": _iso(case.decided_at),
        "remediation_started_at": _iso(case.remediation_started_at),
        "verification_started_at": _iso(case.verification_started_at),
        "verification_succeeded_at": _iso(case.verification_succeeded_at),
        "valid_days_streak": case.valid_days_streak,
        "retrospective_at": _iso(case.retrospective_at),
        "learning": case.learning,
        "preventive_measure": case.preventive_measure,
        "no_preventive_measure_reason": case.no_preventive_measure_reason,
        "closed_at": _iso(case.closed_at),
        "reopen_count": case.reopen_count,
        "parent_case_id": case.parent_case_id,
        "case_version": case.case_version,
        "cloud": getattr(case, "cloud", None) or _dim(case, "cloud_origem", "cloud"),
        "subaccount_name": getattr(case, "subaccount_name", None) or _dim(case, "subaccountname", "subaccount_name"),
        "resource_group": getattr(case, "resource_group", None) or _dim(case, "resource_group", "source_resource_group_name", "resourcegroupname"),
        "service_name": getattr(case, "service_name", None) or _dim(case, "servicename", "service_name"),
        "region": getattr(case, "region", None) or _dim(case, "region", "regionname"),
        "detector": getattr(case, "detector", None),
        "deviation_status": getattr(case, "deviation_status", None),
        "suggested_owner_id": getattr(case, "suggested_owner_id", None),
        "projected_month_impact": getattr(case, "projected_month_impact", None),
        "impact_to_date": getattr(case, "impact_to_date", None),
        "explanation": getattr(case, "explanation", None),
        "episode_start": getattr(case, "episode_start", None),
        "episode_end": getattr(case, "episode_end", None),
        "ack_due_at": _iso(getattr(case, "ack_due_at", None)),
        "resolution_due_at": _iso(getattr(case, "resolution_due_at", None)),
        "first_detected_at": _iso(case.first_detected_at),
        "last_evaluated_at": _iso(case.last_evaluated_at),
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


# --------------------------------------------------------------------------
# Configuração
# --------------------------------------------------------------------------
@router.get("/configs")
async def list_configs(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "read")),
):
    rows = (await db.execute(select(AnomalyConfig).order_by(AnomalyConfig.id))).scalars().all()
    configs = []
    for row in rows:
        saved_settings = detector_settings(row.detector_settings)
        legacy_tiers = dict(row.severity_tiers or {})
        for legacy_key, setting_key in (("MEDIUM", "severity_medium_min"), ("HIGH", "severity_high_min"), ("CRITICAL", "severity_critical_min")):
            if legacy_key in legacy_tiers:
                saved_settings[setting_key] = float(legacy_tiers[legacy_key])
        configs.append({
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "forecast_config_id": row.forecast_config_id,
            "measure_column": row.measure_column,
            "currency": row.currency,
            "profile": row.profile,
            "daily_quantile": row.daily_quantile,
            "min_relative_deviation": row.min_relative_deviation,
            "investigate_score": row.investigate_score,
            "monthly_tolerance_pct": row.monthly_tolerance_pct,
            "materiality_by_currency": row.materiality_by_currency,
            "history_days": row.history_days,
            "calibration_days": row.calibration_days,
            "horizon_days": row.horizon_days,
            "data_lag_days": row.data_lag_days,
            "first_run_days": row.first_run_days,
            "reevaluation_days": row.reevaluation_days,
            "family_keys": row.family_keys,
            "routing_rules": row.routing_rules,
            "sla_hours": row.sla_hours,
            "notification_enabled": row.notification_enabled,
            "enabled": row.enabled,
            "version": row.version,
            "source_namespace": row.source_namespace,
            "destination_physical_name": row.destination_physical_name,
            "severity_tiers": row.severity_tiers,
            "detector_settings": saved_settings,
        })
    return configs


@router.put("/configs/{config_id}")
async def update_config(
    config_id: int,
    body: ConfigUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "create")),
):
    config = await db.get(AnomalyConfig, config_id)
    if config is None:
        raise HTTPException(404, "Configuração não encontrada")
    before = {"enabled": config.enabled, "version": config.version, "profile": config.profile}
    data = body.model_dump(exclude_unset=True)
    if "detector_settings" in data and data["detector_settings"] is not None:
        try:
            data["detector_settings"] = validate_detector_settings({
                **detector_settings(config.detector_settings),
                **data["detector_settings"],
            })
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    if "materiality_by_currency" in data and data["materiality_by_currency"] is not None:
        data["materiality_by_currency"] = {
            str(key).upper(): float(value) for key, value in data["materiality_by_currency"].items()
        }
    for field, value in data.items():
        if value is not None:
            setattr(config, field, value)
    if "detector_settings" in data:
        config.severity_tiers = None
    config.version = int(config.version or 1) + 1
    await db.flush()
    await db.refresh(config)
    await audit(
        db, request, user, module="anomalies", method="PUT", action_label="update",
        entity_type="anomaly_config", entity_id=config.id, entity_label=config.name,
        before=before, after={"enabled": config.enabled, "version": config.version, "profile": config.profile},
        status_code=200,
    )
    await db.commit()
    return {"id": config.id, "version": config.version, "enabled": config.enabled}


@router.get("/coverage")
async def coverage(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "read")),
):
    """Cobertura das avaliações: publicação corrente e contagem por estado."""
    publications = (
        await db.execute(
            select(AnomalyPublication)
            .where(AnomalyPublication.is_current.is_(True))
            .order_by(AnomalyPublication.id.desc())
        )
    ).scalars().all()
    summary = await workflow.case_summary(db)
    return {
        "publications": [
            {
                "anomaly_config_id": row.anomaly_config_id,
                "run_id": row.run_id,
                "reference_table": row.reference_table,
                "assessment_table": row.assessment_table,
                "data_cutoff": row.data_cutoff,
                "generated_at": _iso(row.generated_at),
                "reference_count": row.reference_count,
                "row_count": row.row_count,
                "series_count": row.series_count,
                "published_at": _iso(row.published_at),
            }
            for row in publications
        ],
        "cases": summary,
    }


# --------------------------------------------------------------------------
# Execuções
# --------------------------------------------------------------------------
@router.post("/configs/{config_id}/run")
async def enqueue_run(
    config_id: int,
    body: RunRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "create")),
):
    """Enfileira `task_type='anomaly_detection'` para a configuração."""
    from app.database import run_async_isolated
    from app.models.etl_task import EtlTask

    config = await db.get(AnomalyConfig, config_id)
    if config is None:
        raise HTTPException(404, "Configuração não encontrada")
    if not config.enabled:
        raise HTTPException(409, "Habilite a configuração antes de processar")

    running = (
        await db.execute(
            select(AnomalyRun).where(
                AnomalyRun.anomaly_config_id == config_id,
                AnomalyRun.status.in_(("QUEUED", "RUNNING")),
            )
        )
    ).scalars().first()
    if running is not None:
        task = await db.get(EtlTask, running.source_etl_task_id) if running.source_etl_task_id else None
        if task is not None and task.status in ("QUEUED", "RUNNING"):
            raise HTTPException(409, f"Já existe execução em andamento (run {running.id})")
        running.status = "CANCELLED"
        running.phase = "Cancelado pelo usuário"
        running.finished_at = datetime.now(timezone.utc)
        await db.flush()

    run = AnomalyRun(
        anomaly_config_id=config_id,
        config_version=config.version,
        trigger_type=body.trigger_type,
        status="QUEUED",
        phase="Na fila",
        config_snapshot={
            "config_id": config.id,
            "config_version": config.version,
            "profile": config.profile,
            "daily_quantile": config.daily_quantile,
            "min_relative_deviation": config.min_relative_deviation,
            "investigate_score": config.investigate_score,
            "materiality_by_currency": config.materiality_by_currency,
            "currency": config.currency,
            "measure_column": config.measure_column,
            "dimensions": list(config.dimensions or []),
            "family_keys": list(config.family_keys or []),
            "horizon_days": config.horizon_days,
            "calibration_days": config.calibration_days,
            "history_days": config.history_days,
            "data_lag_days": config.data_lag_days,
            "detector_settings": detector_settings(config.detector_settings),
            "severity_tiers": config.severity_tiers,
        },
    )
    db.add(run)
    await db.flush()
    task = EtlTask(
        destination_table_id=config_id,
        task_type="anomaly_detection",
        status="QUEUED",
        phase="Na fila",
        result_json={"anomaly_run_id": run.id},
    )
    db.add(task)
    await db.flush()
    run.source_etl_task_id = task.id
    await db.flush()

    await audit(
        db, request, user, module="anomalies", method="POST", action_label="execute",
        entity_type="anomaly_run", entity_id=run.id, entity_label=config.name, status_code=200,
    )
    await db.commit()
    return {"run_id": run.id, "task_id": task.id, "status": run.status}


@router.get("/runs")
async def list_runs(
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "read")),
):
    size, skip = _page(limit, offset)
    rows = (
        await db.execute(
            select(AnomalyRun).order_by(AnomalyRun.id.desc()).limit(size).offset(skip)
        )
    ).scalars().all()
    return {
        "items": [
            {
                "id": row.id,
                "anomaly_config_id": row.anomaly_config_id,
                "config_version": row.config_version,
                "trigger_type": row.trigger_type,
                "status": row.status,
                "phase": row.phase,
                "forecast_run_id": row.forecast_run_id,
                "data_cutoff": row.data_cutoff,
                "reference_kind": row.reference_kind,
                "reference_table": row.reference_table,
                "assessment_table": row.assessment_table,
                "processed_series": row.processed_series,
                "total_series": row.total_series,
                "rows_written": row.rows_written,
                "cases_created": row.cases_created,
                "cases_updated": row.cases_updated,
                "notifications_created": row.notifications_created,
                "error": row.error,
                "started_at": _iso(row.started_at),
                "finished_at": _iso(row.finished_at),
            }
            for row in rows
        ],
        "limit": size,
        "offset": skip,
    }


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "create")),
):
    run = await db.get(AnomalyRun, run_id)
    if run is None:
        raise HTTPException(404, "Execução não encontrada")
    if run.status in ("COMPLETED", "FAILED", "CANCELLED"):
        raise HTTPException(409, f"Execução já finalizada ({run.status})")
    run.status = "CANCELLED"
    run.phase = "Cancelado pelo usuário"
    run.finished_at = datetime.now(timezone.utc)
    if run.source_etl_task_id:
        from app.models.etl_task import EtlTask

        task = await db.get(EtlTask, run.source_etl_task_id)
        if task is not None and task.status in ("QUEUED", "RUNNING"):
            task.status = "CANCELLED"
            task.phase = "Cancelado pelo usuário"
    await db.flush()
    await audit(
        db, request, user, module="anomalies", method="POST", action_label="update",
        entity_type="anomaly_run", entity_id=run.id, status_code=200,
    )
    await db.commit()
    return {"run_id": run.id, "status": run.status}


# --------------------------------------------------------------------------
# Casos
# --------------------------------------------------------------------------
@router.get("/cases")
async def list_cases(
    state: list[str] | None = Query(default=None),
    severity: list[str] | None = Query(default=None),
    assignee_user_id: int | None = None,
    queue_only: bool = False,
    overdue_only: bool = False,
    unassigned_only: bool = False,
    decision: list[str] | None = Query(default=None),
    deviation_status: list[str] | None = Query(default=None),
    recurrence_only: bool = False,
    period: str | None = None,
    config_id: int | None = None,
    order_by: Literal["score", "due"] = "score",
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "read")),
):
    size, skip = _page(limit, offset)
    rows, total = await workflow.list_cases(
        db,
        limit=size,
        offset=skip,
        order_by=order_by,
        states=state,
        severities=severity,
        assignee_user_id=assignee_user_id,
        queue_only=queue_only,
        overdue_only=overdue_only,
        period=period,
        config_id=config_id,
        unassigned_only=unassigned_only,
        decisions=decision,
        deviation_statuses=deviation_status,
        recurrence_only=recurrence_only,
    )
    return {"items": [_case_out(case) for case in rows], "total": total, "limit": size, "offset": skip}


@router.get("/cases/summary")
async def summary(
    config_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "read")),
):
    return await workflow.case_summary(db, config_id=config_id)


@router.get("/metrics")
async def metrics(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "read")),
):
    """Indicadores de avaliação (plano §5.2)."""
    summary_payload = await workflow.case_summary(db)

    async def count_runs(*conditions) -> int:
        query = select(func.count()).select_from(AnomalyRun)
        for condition in conditions:
            query = query.where(condition)
        return int((await db.execute(query)).scalar_one())

    publications = (
        await db.execute(
            select(func.count())
            .select_from(AnomalyPublication)
            .where(AnomalyPublication.is_current.is_(True))
        )
    ).scalar_one()

    summary_payload["evaluation"] = {
        "runs": await count_runs(),
        "completed_runs": await count_runs(AnomalyRun.status == "COMPLETED"),
        "failed_runs": await count_runs(AnomalyRun.status == "FAILED"),
        "current_publications": int(publications),
    }
    return summary_payload


@router.get("/cases/{case_id}")
async def get_case(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "read")),
):
    case = await db.get(AnomalyCase, case_id)
    if case is None:
        raise HTTPException(404, "Caso não encontrado")
    payload = _case_out(case)
    payload["steps"] = await workflow.case_steps(db, case=case)
    payload["actions"] = [
        _action_out(action)
        for action in (
            await db.execute(
                select(AnomalyAction).where(AnomalyAction.case_id == case_id).order_by(AnomalyAction.id)
            )
        ).scalars().all()
    ]
    return payload


@router.get("/cases/{case_id}/events")
async def get_case_events(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "read")),
):
    events = await workflow.case_events(db, case_id=case_id)
    return {
        "items": [
            {
                "id": event.id,
                "event_type": event.event_type,
                "command": event.command,
                "from_state": event.from_state,
                "to_state": event.to_state,
                "actor_user_id": event.actor_user_id,
                "note": event.note,
                "payload": event.payload,
                "case_version": event.case_version,
                "created_at": _iso(event.created_at),
            }
            for event in events
        ]
    }


async def _publication_or_409(db: AsyncSession, config_id: int):
    from app.services.anomaly_release import current_publication

    publication = await current_publication(db, config_id)
    if publication is None:
        raise HTTPException(409, "Sem publicação corrente para esta configuração")
    return publication


async def _resolve_config(db: AsyncSession, config_id: int | None) -> AnomalyConfig:
    if config_id is not None:
        config = await db.get(AnomalyConfig, config_id)
        if config is None:
            raise HTTPException(404, "Configuração não encontrada")
        return config
    config = (
        await db.execute(select(AnomalyConfig).order_by(AnomalyConfig.id).limit(1))
    ).scalar_one_or_none()
    if config is None:
        raise HTTPException(404, "Nenhuma configuração de anomalia cadastrada")
    return config


@router.get("/expected-events")
async def expected_events(
    config_id: int | None = None,
    period: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "read")),
):
    """Recorrências reconhecidas como esperadas (plano §5.2, visão 4).

    Elas permanecem na tabela analítica e **não** abrem workflow.
    """
    from app.services.anomaly_queries import read_expected_events
    from app.services.anomaly_release import current_publication

    size, skip = _page(limit, offset)
    config = await _resolve_config(db, config_id)
    publication = await current_publication(db, config.id)
    if publication is None:
        return {
            "items": [],
            "total": 0,
            "note": "Motor v2 não usa a publicação antiga de eventos esperados. A recorrência fica fora da fila na própria detecção.",
        }
    try:
        return await read_expected_events(
            namespace=config.source_namespace,
            table=publication.assessment_table,
            period=period,
            limit=size,
            offset=skip,
        )
    except Exception as exc:
        logger.warning("[anomaly] falha ao listar eventos esperados: %s", exc)
        raise HTTPException(503, f"Falha ao consultar o Iceberg: {exc}") from exc


@router.get("/quality")
async def quality(
    config_id: int | None = None,
    period: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "read")),
):
    """Qualidade: dados parciais, referências incompatíveis e tolerâncias."""
    from app.services.anomaly_queries import read_quality
    from app.services.anomaly_release import current_publication

    size, skip = _page(limit, offset)
    config = await _resolve_config(db, config_id)
    publication = await current_publication(db, config.id)
    if publication is None:
        return {
            "items": [],
            "sampled": 0,
            "note": "Motor v2 não publica a tabela antiga de qualidade. A saúde do modelo usa os casos e o status do desvio.",
        }
    try:
        return await read_quality(
            namespace=config.source_namespace,
            table=publication.assessment_table,
            period=period,
            limit=size,
            offset=skip,
        )
    except Exception as exc:
        logger.warning("[anomaly] falha ao listar qualidade: %s", exc)
        raise HTTPException(503, f"Falha ao consultar o Iceberg: {exc}") from exc


@router.get("/cases/{case_id}/series")
async def get_case_series(
    case_id: int,
    horizon_days: int = Query(default=15, ge=1, le=92),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "read")),
):
    """Série diária do caso: observado × faixa × custo anômalo (plano §5.1).

    A grade de 15 dias é **derivada** da referência compacta; nada é copiado
    para o PostgreSQL.
    """
    from app.services.anomaly_release import current_publication
    from app.services.anomaly_series import read_case_series
    from app.models.forecast import ForecastConfig

    case = await db.get(AnomalyCase, case_id)
    if case is None:
        raise HTTPException(404, "Caso não encontrado")
    if not case.anomaly_config_id:
        raise HTTPException(409, "Caso sem configuração de anomalia associada")
    config = await db.get(AnomalyConfig, case.anomaly_config_id)
    if config is None:
        raise HTTPException(409, "Configuração de anomalia do caso não existe mais")
    if (case.reference_kind or "") == "V2_BASELINE":
        from app.services.anomaly_v2.case_series import read_v2_case_series

        lag_days = int(config.data_lag_days if config.data_lag_days is not None else 2)
        as_of = datetime.now(timezone.utc).date() - timedelta(days=lag_days)
        try:
            return await read_v2_case_series(case, config, as_of=as_of)
        except Exception as exc:
            logger.warning("[anomaly] falha ao montar a série v2 do caso %s: %s", case_id, exc)
            raise HTTPException(503, f"Falha ao consultar a série: {exc}") from exc
    publication = await current_publication(db, case.anomaly_config_id)
    if publication is None:
        return {"items": [], "note": "sem publicação corrente para esta configuração"}

    published_run = await db.get(AnomalyRun, publication.run_id) if publication.run_id else None
    analysis_data_end = None
    if published_run and published_run.evaluation_end:
        try:
            analysis_data_end = date.fromisoformat(str(published_run.evaluation_end)[:10])
        except ValueError:
            analysis_data_end = None
    if analysis_data_end is None:
        lag_days = int(config.data_lag_days if config.data_lag_days is not None else 2)
        analysis_data_end = datetime.now(timezone.utc).date() - timedelta(days=lag_days)

    forecast = (
        await db.get(ForecastConfig, config.forecast_config_id) if config.forecast_config_id else None
    )
    dimensions = list(config.dimensions or (forecast.dimensions if forecast else []) or [])
    scope_event = (await db.execute(
        select(AnomalyCaseEvent).where(
            AnomalyCaseEvent.case_id == case_id,
            AnomalyCaseEvent.event_type.in_(("CASE_CREATED", "CASE_UPDATED", "CASE_REOPENED")),
        ).order_by(AnomalyCaseEvent.id.desc()).limit(1)
    )).scalars().first()
    contributor_series = list((scope_event.payload or {}).get("contributor_series") or []) if scope_event else []
    try:
        return await read_case_series(
            case=case,
            publication=publication,
            reference_namespace=config.source_namespace,
            source_namespace=config.source_namespace,
            source_table=config.destination_physical_name,
            date_column=config.date_column,
            period_column=config.period_column,
            type_column=config.type_column,
            measure_column=config.measure_column,
            dimensions=dimensions,
            horizon_days=horizon_days,
            analysis_data_end=analysis_data_end,
            contributor_series=contributor_series,
        )
    except Exception as exc:
        logger.warning("[anomaly] falha ao montar a série do caso %s: %s", case_id, exc)
        raise HTTPException(503, f"Falha ao consultar a série: {exc}") from exc


@router.get("/cases/{case_id}/contributors")
async def get_contributors(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "read")),
):
    """Contribuintes vêm da avaliação publicada, não do PostgreSQL."""
    from app.services.anomaly_release import current_publication
    from app.services.trino_service import execute as trino_execute

    case = await db.get(AnomalyCase, case_id)
    if case is None:
        raise HTTPException(404, "Caso não encontrado")
    publication = await current_publication(db, case.anomaly_config_id) if case.anomaly_config_id else None
    if publication is None or not case.current_assessment_id:
        return {"items": [], "note": "sem publicação corrente para esta configuração"}
    sql = (
        "SELECT * FROM iceberg.silver." + f'"{publication.assessment_table}"'
        f" WHERE assessment_id = '{case.current_assessment_id}' LIMIT 200"
    )
    try:
        rows = await trino_execute(sql, timeout_s=120)
    except Exception as exc:
        logger.warning("[anomaly] falha ao ler contribuintes: %s", exc)
        raise HTTPException(503, f"Falha ao consultar o Iceberg: {exc}") from exc
    return {"items": rows, "assessment_table": publication.assessment_table}


@router.post("/cases/{case_id}/transition")
async def transition(
    case_id: int,
    body: TransitionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "update")),
):
    """Comando explícito do workflow. Conflito de versão → 409."""
    scope = workflow.COMMAND_SCOPES.get(body.command)
    if scope is None:
        raise HTTPException(422, f"Comando desconhecido: {body.command}")
    await _require_scope(db, user, scope)

    payload = body.model_dump(exclude_unset=True)
    payload.pop("case_version", None)
    try:
        case = await workflow.apply_command(
            db,
            case_id=case_id,
            command=body.command,
            payload=payload,
            actor_user_id=user.id,
            expected_version=body.case_version,
        )
    except workflow.CaseNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except workflow.WorkflowError as exc:
        status = 409 if "Versão do caso divergente" in str(exc) else 422
        raise HTTPException(status, str(exc)) from exc

    await audit(
        db, request, user, module="anomalies", method="POST", action_label="update",
        entity_type="anomaly_case", entity_id=case.id, entity_label=case.event_family,
        after={"command": body.command, "state": case.state, "version": case.case_version},
        status_code=200,
    )
    await db.commit()
    return _case_out(case)


# --------------------------------------------------------------------------
# Ações
# --------------------------------------------------------------------------
def _action_out(action: AnomalyAction) -> dict[str, Any]:
    return {
        "id": action.id,
        "case_id": action.case_id,
        "action_type": action.action_type,
        "description": action.description,
        "owner_user_id": action.owner_user_id,
        "status": action.status,
        "due_at": _iso(action.due_at),
        "executed_at": _iso(action.executed_at),
        "evidence": action.evidence,
        "external_reference": action.external_reference,
        "mandatory": action.mandatory,
    }


@router.post("/cases/{case_id}/actions")
async def create_action(
    case_id: int,
    body: ActionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "update")),
):
    await _require_scope(db, user, "decide")
    case = await db.get(AnomalyCase, case_id)
    if case is None:
        raise HTTPException(404, "Caso não encontrado")
    action = AnomalyAction(
        case_id=case_id,
        action_type=body.action_type,
        description=body.description,
        owner_user_id=body.owner_user_id,
        due_at=body.due_at,
        mandatory=body.mandatory,
        created_by_id=user.id,
    )
    db.add(action)
    await db.flush()
    await audit(
        db, request, user, module="anomalies", method="POST", action_label="create",
        entity_type="anomaly_action", entity_id=action.id, status_code=200,
    )
    await db.commit()
    return _action_out(action)


@router.put("/actions/{action_id}")
async def update_action(
    action_id: int,
    body: ActionUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "update")),
):
    action = await db.get(AnomalyAction, action_id)
    if action is None:
        raise HTTPException(404, "Ação não encontrada")
    data = body.model_dump(exclude_unset=True)
    for field, value in data.items():
        if value is not None:
            setattr(action, field, value)
    if data.get("status") == "COMPLETED":
        action.executed_at = datetime.now(timezone.utc)
    await db.flush()
    await audit(
        db, request, user, module="anomalies", method="PUT", action_label="update",
        entity_type="anomaly_action", entity_id=action.id, status_code=200,
    )
    await db.commit()
    return _action_out(action)


# --------------------------------------------------------------------------
# Notificações
# --------------------------------------------------------------------------
@router.get("/notifications")
async def list_notifications(
    unread_only: bool = False,
    include_queue: bool = True,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "read")),
):
    size, skip = _page(limit, offset)
    rows, total = await workflow.inbox(
        db, user_id=user.id, unread_only=unread_only, include_queue=include_queue,
        limit=size, offset=skip,
    )
    unread, _ = await workflow.inbox(
        db, user_id=user.id, unread_only=True, include_queue=include_queue, limit=1, offset=0
    )
    return {
        "items": [
            {
                "id": row.id,
                "case_id": row.case_id,
                "kind": row.kind,
                "title": row.title,
                "body": row.body,
                "severity": row.severity,
                "is_finops_queue": row.is_finops_queue,
                "recipient_user_id": row.recipient_user_id,
                "routing_rule_id": row.routing_rule_id,
                "created_at": _iso(row.created_at),
                "read_at": _iso(row.read_at),
                "acknowledged_at": _iso(row.acknowledged_at),
            }
            for row in rows
        ],
        "total": total,
        "unread": len(unread) if total else 0,
        "limit": size,
        "offset": skip,
    }


@router.post("/notifications/{notification_id}/read")
async def read_notification(
    notification_id: int,
    acknowledge: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "read")),
):
    try:
        notification = await workflow.mark_notification(
            db, notification_id=notification_id, user_id=user.id, acknowledge=acknowledge
        )
    except workflow.CaseNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    await db.commit()
    return {
        "id": notification.id,
        "read_at": _iso(notification.read_at),
        "acknowledged_at": _iso(notification.acknowledged_at),
    }


@router.get("/cases/{case_id}/explanation")
async def case_explanation(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "read")),
):
    case = await db.get(AnomalyCase, case_id)
    if case is None:
        raise HTTPException(404, "Caso não encontrado")
    if case.explanation:
        return {"case_id": case_id, "checks": case.explanation}
    from app.services.anomaly_v2.explanation import explain_signal

    config = await db.get(AnomalyConfig, case.anomaly_config_id) if case.anomaly_config_id else None
    materiality = 100.0
    min_pct = 0.2
    if config:
        materiality = float((config.materiality_by_currency or {}).get(config.currency, 100))
        min_pct = float(config.min_relative_deviation or 0.2)
    signal = {
        "delta": case.delta_cloud,
        "pct_delta": (case.delta_cloud or 0) / max(case.expected_cloud or 1, 1),
        "detector": case.detector or case.event_type,
        "reason_codes": case.reason_codes or [],
    }
    return {"case_id": case_id, "checks": explain_signal(signal, materiality=materiality, min_pct=min_pct)}


@router.get("/impact/summary")
async def impact_summary(
    config_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "read")),
):
    base = select(AnomalyCase)
    if config_id is not None:
        base = base.where(AnomalyCase.anomaly_config_id == config_id)
    rows = (await db.execute(base)).scalars().all()
    by_status: dict[str, int] = {}
    incurred = saving_m = exposure_m = 0.0
    for case in rows:
        status = getattr(case, "deviation_status", None) or case.state
        by_status[str(status)] = by_status.get(str(status), 0) + 1
        incurred += float(getattr(case, "impact_to_date", None) or case.anomalous_cost_cloud or 0)
        if str(getattr(case, "deviation_status", "")) in ("AUTOCORRIGIDO", "CORRIGIDO_COM_ACAO"):
            saving_m += float(getattr(case, "projected_month_impact", 0) or 0)
        if str(getattr(case, "deviation_status", "")) in ("EM_DESVIO", "ACEITO", "REINCIDENTE"):
            exposure_m += float(getattr(case, "projected_month_impact", 0) or 0)
    return {
        "detected": len(rows),
        "by_deviation_status": by_status,
        "incurred_excess_cost": round(incurred, 2),
        "projected_saving_monthly": round(saving_m, 2),
        "projected_saving_annual": round(saving_m * 12, 2),
        "open_exposure_monthly": round(exposure_m, 2),
        "open_exposure_annual": round(exposure_m * 12, 2),
    }


@router.get("/health")
async def anomaly_health(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "read")),
):
    last_run = (
        await db.execute(select(AnomalyRun).order_by(AnomalyRun.id.desc()).limit(1))
    ).scalar_one_or_none()
    config = (await db.execute(select(AnomalyConfig).limit(1))).scalar_one_or_none()
    v2_summary = {}
    if last_run and isinstance(last_run.config_snapshot, dict):
        v2_summary = last_run.config_snapshot.get("v2_summary") or {}
    return {
        "engine": getattr(config, "detection_engine", "v2") if config else "v2",
        "config_enabled": bool(config.enabled) if config else False,
        "last_run_status": last_run.status if last_run else None,
        "last_run_finished_at": _iso(last_run.finished_at) if last_run else None,
        "v2_summary": v2_summary,
    }


@router.post("/configs/{config_id}/simulate")
async def simulate_detection(
    config_id: int,
    days: int = Query(default=30, ge=7, le=90),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("anomalies", "create")),
):
    from app.services.anomaly_v2.backtest import backtest_date_range, run_backtest
    from app.services.anomaly_v2.daily_build import load_daily_history
    from app.services.anomaly_v2.runner import _analysis_end, _config_from_row

    config_row = await db.get(AnomalyConfig, config_id)
    if config_row is None:
        raise HTTPException(404, "Configuração não encontrada")
    config = _config_from_row(config_row)
    as_of = _analysis_end(datetime.now(timezone.utc), int(config_row.data_lag_days or 2))
    history = await load_daily_history(config, as_of=as_of, history_days=days)

    def evaluate(day: date):
        return []

    report = run_backtest(days=backtest_date_range(as_of, history_days=days), evaluate_day=evaluate)
    report["note"] = "Simulação estrutural; conecte evaluate ao motor para contagem real."
    report["history_rows"] = len(history)
    return report
