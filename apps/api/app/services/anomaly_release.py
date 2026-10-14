"""Publicação consistente e ciclo de vida dos casos (E6).

Publicação (plano §6):

1. escrever referências e avaliações em staging;
2. validar reconciliação e unicidade;
3. publicar resultados no Iceberg (commit atômico);
4. atualizar referências dos casos e a publicação no PostgreSQL;
5. gerar eventos/notificações de forma idempotente.

**Falha preserva a publicação anterior.** O `anomaly_publication` corrente só é
trocado depois do commit do Iceberg, em uma única transação. Um retry não
duplica caso nem notificação.

**Referência congelada do episódio:** a referência de um caso ativo nunca é
sobrescrita pela referência mais recente — o desvio em andamento continua
comparável com o que originou o caso.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.anomaly import (
    AnomalyAction,
    AnomalyCase,
    AnomalyCaseEvent,
    AnomalyConfig,
    AnomalyPublication,
    AnomalyRun,
)
from app.services.anomaly_workflow import assign_case, enqueue_case, resolve_routing
from app.services.anomaly_detector import (
    CREATE,
    EXPECTED_ONLY,
    IGNORE,
    LINK_NEW_EPISODE,
    NO_WORKFLOW,
    REOPEN,
    UPDATE,
    resolve_episode,
)

logger = logging.getLogger(__name__)


class PublicationError(RuntimeError):
    """Publicação inválida: nada é trocado."""


@dataclass
class PublicationResult:
    run_id: int | None
    reference_table: str
    assessment_table: str
    reference_rows: int
    assessment_rows: int
    series_count: int
    is_current: bool
    previous_run_id: int | None = None
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "reference_table": self.reference_table,
            "assessment_table": self.assessment_table,
            "reference_rows": self.reference_rows,
            "assessment_rows": self.assessment_rows,
            "series_count": self.series_count,
            "is_current": self.is_current,
            "previous_run_id": self.previous_run_id,
            "warnings": list(self.warnings),
        }


def _dataframe(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]):
    import pandas as pd

    if not rows:
        return pd.DataFrame(columns=list(columns))
    frame = pd.DataFrame(list(rows))
    for column in columns:
        if column not in frame.columns:
            frame[column] = None
    return frame


def publish_iceberg(
    *,
    reference_rows: Sequence[Mapping[str, Any]],
    assessment_rows: Sequence[Mapping[str, Any]],
    reference_columns: Sequence[str],
    assessment_columns: Sequence[str],
    reference_table: str,
    assessment_table: str,
    namespace: str,
    writer: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Publica as duas tabelas de forma atômica (por tabela).

    Cada tabela usa staging + commit atômico. Se a segunda falhar, a primeira já
    está publicada — e a publicação corrente **não** é trocada, porque isso só
    acontece depois, em `commit_publication`. O efeito observável é o exigido:
    falha preserva a publicação anterior.
    """
    from app.services import iceberg_service

    write = writer or iceberg_service.publish_dataframe_atomic
    reference_frame = _dataframe(reference_rows, reference_columns)
    assessment_frame = _dataframe(assessment_rows, assessment_columns)
    reference = write(
        namespace,
        reference_table,
        reference_frame,
        column_types={
            "predicted_cloud": "DOUBLE",
            "normalizer": "DOUBLE",
            "band_lower_cloud": "DOUBLE",
            "band_upper_cloud": "DOUBLE",
            "daily_band_lower_offset": "DOUBLE",
            "daily_band_upper_offset": "DOUBLE",
            "band_alpha": "DOUBLE",
        },
    )
    assessment = write(
        namespace,
        assessment_table,
        assessment_frame,
        column_types={
            "observed_cloud": "DOUBLE",
            "expected_cloud": "DOUBLE",
            "lower_cloud": "DOUBLE",
            "upper_cloud": "DOUBLE",
            "delta_cloud": "DOUBLE",
            "anomalous_cost_cloud": "DOUBLE",
            "realizado_cloud": "DOUBLE",
            "score": "DOUBLE",
            "is_financial_additive": "BOOLEAN",
        },
    )
    return {"reference": reference, "assessment": assessment}


async def commit_publication(
    session: AsyncSession,
    *,
    anomaly_config_id: int,
    run_id: int | None,
    reference_table: str,
    assessment_table: str,
    gold_table: str | None = None,
    data_cutoff: str | None = None,
    generated_at: datetime | None = None,
    reference_rows: int = 0,
    assessment_rows: int = 0,
    series_count: int = 0,
    published_by_id: int | None = None,
) -> AnomalyPublication:
    """Troca a publicação corrente em uma única transação.

    A publicação anterior é marcada como não corrente **na mesma transação** que
    insere a nova: nunca existe um instante sem publicação corrente válida.
    """
    previous = (
        await session.execute(
            select(AnomalyPublication)
            .where(
                AnomalyPublication.anomaly_config_id == anomaly_config_id,
                AnomalyPublication.is_current.is_(True),
            )
            .order_by(AnomalyPublication.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if previous is not None:
        previous.is_current = False
        await session.flush()

    publication = AnomalyPublication(
        anomaly_config_id=anomaly_config_id,
        run_id=run_id,
        previous_run_id=previous.run_id if previous else None,
        reference_table=reference_table,
        assessment_table=assessment_table,
        gold_table=gold_table,
        data_cutoff=data_cutoff,
        generated_at=generated_at,
        row_count=assessment_rows,
        series_count=series_count,
        reference_count=reference_rows,
        is_current=True,
        published_by_id=published_by_id,
    )
    session.add(publication)
    await session.flush()
    return publication


async def current_publication(session: AsyncSession, anomaly_config_id: int) -> AnomalyPublication | None:
    return (
        await session.execute(
            select(AnomalyPublication)
            .where(
                AnomalyPublication.anomaly_config_id == anomaly_config_id,
                AnomalyPublication.is_current.is_(True),
            )
            .order_by(AnomalyPublication.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


# --------------------------------------------------------------------------
# Casos
# --------------------------------------------------------------------------
@dataclass
class CaseOutcome:
    action: str
    case_id: int | None
    case_key: str
    created: bool = False
    updated: bool = False
    reopened: bool = False
    linked_from_case_id: int | None = None
    severity_increased: bool = False
    previous_severity: str | None = None


#: Resultado do roteamento antes de tocar no banco.
ROUTE_ASSIGNED = "ASSIGNED"
ROUTE_QUEUE = "FINOPS_QUEUE"


def routing_decision(match: Any) -> tuple[str, int | None, list[str]]:
    """Decide entre atribuir e enfileirar a partir do casamento de regras.

    Função pura: ambíguo ou sem regra vai para a fila FinOps — nunca uma escolha
    arbitrária (plano §3.3).
    """
    notes: list[str] = []
    if getattr(match, "ambiguous", False):
        notes.append(
            "roteamento ambíguo entre " + ", ".join(match.candidates or []) + ": fila FinOps"
        )
        return ROUTE_QUEUE, None, notes
    if getattr(match, "assignee_user_id", None) is None:
        notes.append("nenhuma regra de roteamento aplicável: fila FinOps")
        return ROUTE_QUEUE, None, notes
    if match.rule_id:
        notes.append(f"roteado pela regra {match.rule_id} (especificidade {match.specificity})")
    return ROUTE_ASSIGNED, int(match.assignee_user_id), notes


def case_identity(detection: Any) -> str:
    return detection.case_key or case_key_for(
        detection.series_id, detection.event_family, detection.event_type, period=detection.period
    )


async def apply_detection_to_case(
    session: AsyncSession,
    *,
    detection: Any,
    anomaly_config_id: int,
    valid_recovery_days: int = 0,
    continuation: bool = False,
) -> CaseOutcome:
    """Cria/atualiza/reabre o caso do episódio de forma idempotente."""
    key = case_identity(detection)
    existing = (
        await session.execute(select(AnomalyCase).where(AnomalyCase.case_key == key))
    ).scalar_one_or_none()

    decision = resolve_episode(
        detection=detection,
        existing_case=existing,
        valid_recovery_days=valid_recovery_days,
        continuation=continuation,
    )

    if decision.action in (EXPECTED_ONLY, NO_WORKFLOW):
        return CaseOutcome(action=decision.action, case_id=None, case_key=key)

    if decision.action == IGNORE:
        await _append_event(
            session,
            case_id=existing.id,
            event_type="REPROCESSING_IGNORED",
            command="ignore",
            from_state=existing.state,
            to_state=existing.state,
            case_version=existing.case_version,
            note="; ".join(decision.notes) or decision.reason,
            payload={"reason": decision.reason},
        )
        return CaseOutcome(action=IGNORE, case_id=existing.id, case_key=key)

    if decision.action == CREATE or decision.action == LINK_NEW_EPISODE:
        case = AnomalyCase(
            case_key=key,
            anomaly_config_id=anomaly_config_id,
            series_id=detection.series_id,
            series_dimensions=dict(detection.series_dimensions),
            event_family=detection.event_family,
            event_type=detection.event_type,
            event_date=detection.event_date,
            period=detection.period,
            source_billing_period=detection.source_billing_period,
            state="NEW",
            severity=detection.severity,
            score=detection.score,
            confidence=detection.confidence,
            observed_cloud=detection.observed_cloud,
            expected_cloud=detection.expected_cloud,
            lower_cloud=detection.lower_cloud,
            upper_cloud=detection.upper_cloud,
            delta_cloud=detection.delta_cloud,
            anomalous_cost_cloud=decision.anomalous_cost_cloud,
            evaluation_grain=detection.evaluation_grain,
            current_reference_id=detection.reference_id,
            current_assessment_id=assessment_id_of(detection),
            reference_kind=detection.reference_kind,
            reason_codes=list(detection.reason_codes),
            applied_rule_refs=list(detection.applied_rule_refs),
            parent_case_id=decision.parent_case_id,
            case_version=1,
        )
        session.add(case)
        await session.flush()

        # Roteamento (plano §3.3): o caso nasce com responsável OU na fila FinOps.
        # Sem isso, "caso tem responsável ou fila explícita" não se cumpre.
        placement_config = (
            await session.get(AnomalyConfig, anomaly_config_id) if anomaly_config_id else None
        )
        routing_rules = list(getattr(placement_config, "routing_rules", None) or [])
        match = resolve_routing(routing_rules, detection.series_dimensions)
        route, assignee_user_id, notes = routing_decision(match)
        if route == ROUTE_ASSIGNED and assignee_user_id is not None:
            await assign_case(
                session,
                case=case,
                assignee_user_id=assignee_user_id,
                routing_rule_id=match.rule_id,
            )
            case.state = "ASSIGNED"
            case.state_changed_at = datetime.now(timezone.utc)
            state_note = "; ".join(notes)
        else:
            await enqueue_case(session, case=case, reason="; ".join(notes))
            state_note = "; ".join(notes)

        await _append_event(
            session,
            case_id=case.id,
            event_type="CASE_CREATED",
            command="create",
            from_state=None,
            to_state=case.state,
            case_version=1,
            note=decision.reason,
            payload={
                "assessment_id": case.current_assessment_id,
                "reference_id": detection.reference_id,
                "routing": route,
                "routing_note": state_note,
                "assignee_user_id": case.assignee_user_id,
            },
        )
        return CaseOutcome(
            action=decision.action,
            case_id=case.id,
            case_key=key,
            created=True,
            linked_from_case_id=decision.parent_case_id,
        )

    # UPDATE / REOPEN: preserva a referência do episódio e o impacto máximo.
    previous_state = existing.state
    previous_severity = existing.severity
    existing.state = "INVESTIGATING" if decision.action == REOPEN else existing.state
    existing.anomalous_cost_cloud = decision.anomalous_cost_cloud
    existing.observed_cloud = detection.observed_cloud
    existing.expected_cloud = detection.expected_cloud
    existing.lower_cloud = detection.lower_cloud
    existing.upper_cloud = detection.upper_cloud
    existing.delta_cloud = detection.delta_cloud
    existing.score = detection.score
    existing.severity = detection.severity
    existing.confidence = detection.confidence
    existing.last_evaluated_at = datetime.now(timezone.utc)
    existing.reason_codes = list(detection.reason_codes)
    existing.applied_rule_refs = list(detection.applied_rule_refs)
    existing.event_date = detection.event_date
    # A referência do caso ativo fica congelada: só um caso novo (ou reaberto com
    # referência explícita) troca de referência.
    if decision.action == REOPEN:
        existing.current_reference_id = detection.reference_id
        existing.current_assessment_id = assessment_id_of(detection)
        existing.reference_kind = detection.reference_kind
        existing.reopen_count = int(existing.reopen_count or 0) + 1
        existing.state_changed_at = datetime.now(timezone.utc)
    elif existing.current_reference_id is None:
        existing.current_reference_id = detection.reference_id
        existing.current_assessment_id = assessment_id_of(detection)
        existing.reference_kind = detection.reference_kind
    existing.case_version = int(existing.case_version or 1) + 1
    await session.flush()

    await _append_event(
        session,
        case_id=existing.id,
        event_type="CASE_UPDATED" if decision.action == UPDATE else "CASE_REOPENED",
        command=decision.action.lower(),
        from_state=previous_state,
        to_state=existing.state,
        case_version=existing.case_version,
        note=decision.reason,
        payload={
            "assessment_id": assessment_id_of(detection),
            "reference_id": detection.reference_id,
            "represerved_reference": existing.current_reference_id,
        },
    )
    return CaseOutcome(
        action=decision.action,
        case_id=existing.id,
        case_key=key,
        updated=decision.action == UPDATE,
        reopened=decision.action == REOPEN,
        severity_increased=_severity_up(previous_severity, detection.severity),
        previous_severity=previous_severity,
    )


def assessment_id_of(detection: Any) -> str | None:
    from app.services.anomaly_runner import assessment_id_for

    return assessment_id_for(
        detection.reference_id,
        detection.series_id,
        detection.event_date,
        detection.evaluation_grain,
        detection.event_type,
    )


def _severity_up(previous: str | None, current: str) -> bool:
    from app.services.anomaly_scoring import severity_increased

    return severity_increased(previous, current)


async def _append_event(
    session: AsyncSession,
    *,
    case_id: int,
    event_type: str,
    command: str | None,
    from_state: str | None,
    to_state: str | None,
    case_version: int,
    note: str | None = None,
    payload: Mapping[str, Any] | None = None,
) -> AnomalyCaseEvent:
    event = AnomalyCaseEvent(
        case_id=case_id,
        event_type=event_type,
        command=command,
        from_state=from_state,
        to_state=to_state,
        case_version=case_version,
        note=note,
        payload=dict(payload or {}),
    )
    session.add(event)
    await session.flush()
    return event


async def active_case_references(session: AsyncSession, anomaly_config_id: int) -> dict[str, str | None]:
    """Referência congelada por caso ativo — insumo para não sobrescrever."""
    rows = (
        await session.execute(
            select(AnomalyCase.case_key, AnomalyCase.current_reference_id).where(
                AnomalyCase.anomaly_config_id == anomaly_config_id,
                AnomalyCase.state != "CLOSED",
            )
        )
    ).all()
    return {str(key): reference for key, reference in rows}


async def open_mandatory_actions(session: AsyncSession, case_id: int) -> int:
    """Ações obrigatórias pendentes: bloqueiam a ida para verificação."""
    rows = (
        await session.execute(
            select(AnomalyAction.id).where(
                AnomalyAction.case_id == case_id,
                AnomalyAction.mandatory.is_(True),
                AnomalyAction.status.in_(("PLANNED", "IN_PROGRESS")),
            )
        )
    ).all()
    return len(rows)


async def finish_run(
    session: AsyncSession,
    *,
    run_id: int | None,
    status: str,
    phase: str | None = None,
    rows_written: int = 0,
    cases_created: int = 0,
    cases_updated: int = 0,
    notifications_created: int = 0,
    error: str | None = None,
    errors: Sequence[Any] = (),
    contract: Any | None = None,
    reference_table: str | None = None,
    assessment_table: str | None = None,
    gold_table: str | None = None,
    generated_at: datetime | None = None,
    horizon_end: Any | None = None,
    evaluation_end: Any | None = None,
    total_series: int = 0,
    processed_series: int = 0,
) -> None:
    """Fecha a execução e grava o contrato de referência usado.

    O `anomaly_run` é o registro de auditoria da execução: guardar aqui o
    `forecast_run_id`, o corte, o tipo de referência e as tabelas publicadas
    torna o resultado rastreável mesmo depois que a configuração mudar.
    """
    if run_id is None:
        return
    run = await session.get(AnomalyRun, run_id)
    if run is None:
        return
    run.status = status
    run.phase = phase
    run.rows_written = rows_written
    run.cases_created = cases_created
    run.cases_updated = cases_updated
    run.notifications_created = notifications_created
    run.error = error
    run.errors = list(errors)
    if total_series:
        run.total_series = int(total_series)
    if processed_series:
        run.processed_series = int(processed_series)
    if contract is not None:
        run.forecast_run_id = int(getattr(contract, "run_id", 0) or 0) or run.forecast_run_id
        run.forecast_config_version = getattr(contract, "config_version", None)
        run.source_snapshot_id = getattr(contract, "source_snapshot_id", None)
        cutoff = getattr(contract, "cutoff_date", None)
        if cutoff is not None:
            run.data_cutoff = cutoff.isoformat()
            run.target_start = cutoff.isoformat()
        run.reference_kind = getattr(contract, "reference_kind", None)
    if reference_table:
        run.reference_table = reference_table
    if assessment_table:
        run.assessment_table = assessment_table
    if gold_table:
        run.gold_table = gold_table
    if generated_at is not None:
        run.generated_at = generated_at
    if horizon_end is not None:
        run.target_end = horizon_end.isoformat() if hasattr(horizon_end, "isoformat") else str(horizon_end)
    if evaluation_end is not None:
        run.evaluation_end = evaluation_end.isoformat() if hasattr(evaluation_end, "isoformat") else str(evaluation_end)
    if status in ("COMPLETED", "FAILED", "CANCELLED", "COMPLETED_WITH_ERRORS"):
        run.finished_at = datetime.now(timezone.utc)
    await session.flush()
