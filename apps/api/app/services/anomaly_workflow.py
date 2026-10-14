"""Workflow operacional dos casos: atribuição, investigação, decisão,
remediação, verificação e encerramento (E7/E8).

Camada de serviço sobre a máquina de estados (E1). Ela:

* valida o comando com `validate_transition` (estado, vocabulário, campos);
* aplica **bloqueio otimista** por `case_version` (conflito → 409);
* registra cada transição em `anomaly_case_event` (imutável);
* roteia e notifica sem duplicar (`dedup_key`);
* monitora a verificação de três dias válidos sem usar dado parcial para
  confirmar recuperação;
* impede encerrar caso apenas porque o forecast aumentou.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.anomaly import (
    AnomalyAction,
    AnomalyCase,
    AnomalyCaseEvent,
    AnomalyConfig,
    AnomalyNotification,
)
from app.services.anomaly_detector import RECOVERY_VALID_DAYS
from app.services.anomaly_scoring import default_sla_hours, severity_rank

logger = logging.getLogger(__name__)

OPEN_STATES = ("NEW", "ASSIGNED", "INVESTIGATING", "DECIDED", "IN_REMEDIATION", "VERIFYING", "RETROSPECTIVE")
TERMINAL_STATES = ("CLOSED",)

#: Tipos de comando que exigem escopo de permissão específico.
COMMAND_SCOPES: dict[str, str] = {
    "assign": "assign",
    "return_to_queue": "assign",
    "acknowledge": "investigate",
    "start_investigation": "investigate",
    "update_investigation": "investigate",
    "decide": "decide",
    "redecide": "decide",
    "start_remediation": "decide",
    "cancel_remediation": "decide",
    "start_verification": "close",
    "fail_verification": "close",
    "validate_result": "close",
    "record_retrospective": "close",
    "close": "close",
    "reopen": "close",
}

#: Tipos de notificação interna (plano §3.3).
KIND_ASSIGNMENT = "ASSIGNMENT"
KIND_SEVERITY_INCREASE = "SEVERITY_INCREASE"
KIND_DUE_SOON = "DUE_SOON"
KIND_OVERDUE = "OVERDUE"
KIND_VERIFICATION_COMPLETE = "VERIFICATION_COMPLETE"
KIND_QUEUE = "FINOPS_QUEUE"

NOTIFICATION_KINDS = (
    KIND_ASSIGNMENT,
    KIND_SEVERITY_INCREASE,
    KIND_DUE_SOON,
    KIND_OVERDUE,
    KIND_VERIFICATION_COMPLETE,
    KIND_QUEUE,
)


class WorkflowError(RuntimeError):
    status_code = 422


class CaseNotFound(WorkflowError):
    status_code = 404


class PermissionScopeError(WorkflowError):
    status_code = 403


# --------------------------------------------------------------------------
# Roteamento
# --------------------------------------------------------------------------
@dataclass
class RoutingMatch:
    rule_id: str | None
    assignee_user_id: int | None
    specificity: int
    rule: Mapping[str, Any] | None = None
    ambiguous: bool = False
    candidates: list[str] | None = None


def _rule_criteria(rule: Mapping[str, Any]) -> dict[str, Any]:
    criteria = rule.get("when") or rule.get("match") or {}
    return {str(key): value for key, value in dict(criteria).items() if value not in (None, "", [])}


def resolve_routing(
    routing_rules: Sequence[Mapping[str, Any]] | None,
    dimensions: Mapping[str, Any],
) -> RoutingMatch:
    """Roteamento por especificidade; ambiguidade é rejeitada (plano §3.3).

    "Maior especificidade prevalece" = mais critérios atendidos. Empate na maior
    especificidade é ambiguidade: nenhum responsável é atribuído e o caso fica na
    fila FinOps, em vez de escolher arbitrariamente.
    """
    normalized = [dict(rule) for rule in (routing_rules or []) if isinstance(rule, Mapping)]
    if not normalized:
        return RoutingMatch(rule_id=None, assignee_user_id=None, specificity=0)

    matches: list[tuple[int, dict[str, Any]]] = []
    for rule in normalized:
        criteria = _rule_criteria(rule)
        if not criteria:
            continue
        if all(_norm(dimensions.get(key)) == _norm(value) for key, value in criteria.items()):
            matches.append((len(criteria), rule))

    if not matches:
        return RoutingMatch(rule_id=None, assignee_user_id=None, specificity=0)

    top = max(specificity for specificity, _ in matches)
    winners = [rule for specificity, rule in matches if specificity == top]
    if len(winners) > 1:
        return RoutingMatch(
            rule_id=None,
            assignee_user_id=None,
            specificity=top,
            ambiguous=True,
            candidates=[str(rule.get("id") or rule.get("name") or "?") for rule in winners],
        )
    winner = winners[0]
    assignee = winner.get("assignee_user_id") or winner.get("user_id")
    return RoutingMatch(
        rule_id=str(winner.get("id") or winner.get("name") or "") or None,
        assignee_user_id=int(assignee) if assignee not in (None, "") else None,
        specificity=top,
        rule=winner,
    )


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


# --------------------------------------------------------------------------
# Notificações
# --------------------------------------------------------------------------
def notification_dedup_key(*, case_id: int, kind: str, recipient_user_id: int | None) -> str:
    target = f"user:{recipient_user_id}" if recipient_user_id is not None else "queue"
    raw = f"case:{case_id}:{kind}:{target}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


async def notify_case(
    session: AsyncSession,
    *,
    case: AnomalyCase,
    kind: str,
    title: str,
    body: str | None = None,
    recipient_user_id: int | None = None,
    routing_rule_id: str | None = None,
    is_finops_queue: bool | None = None,
) -> AnomalyNotification | None:
    """Cria notificação **idempotente** por caso + evento + destinatário.

    Um novo ETL que reavalia o mesmo caso não gera outra notificação: o
    `dedup_key` é único no banco.
    """
    if is_finops_queue is None:
        is_finops_queue = recipient_user_id is None
    key = notification_dedup_key(case_id=case.id, kind=kind, recipient_user_id=recipient_user_id)
    existing = (
        await session.execute(select(AnomalyNotification).where(AnomalyNotification.dedup_key == key))
    ).scalar_one_or_none()
    if existing is not None:
        return None

    notification = AnomalyNotification(
        case_id=case.id,
        anomaly_config_id=case.anomaly_config_id,
        recipient_user_id=recipient_user_id,
        is_finops_queue=bool(is_finops_queue),
        kind=kind,
        dedup_key=key,
        title=title[:300],
        body=body[:2000] if body else None,
        severity=case.severity,
        routing_rule_id=routing_rule_id,
    )
    session.add(notification)
    await session.flush()
    return notification


async def assign_case(
    session: AsyncSession,
    *,
    case: AnomalyCase,
    assignee_user_id: int,
    routing_rule_id: str | None = None,
) -> None:
    """Define o responsável, calcula o prazo de ciência e notifica."""
    config = await session.get(AnomalyConfig, case.anomaly_config_id) if case.anomaly_config_id else None
    sla = dict(getattr(config, "sla_hours", None) or {}) if config else {}
    hours = default_sla_hours(case.severity, sla)
    case.assignee_user_id = int(assignee_user_id)
    case.routing_rule_id = routing_rule_id or case.routing_rule_id
    case.due_at = datetime.now(timezone.utc) + timedelta(hours=hours)
    await session.flush()
    if not config or bool(getattr(config, "notification_enabled", True)):
        await notify_case(
            session,
            case=case,
            kind=KIND_ASSIGNMENT,
            title=f"Caso #{case.id} atribuído a você",
            body=f"{case.event_family} — nota {case.score:.0f} ({case.severity}) prazo {hours}h",
            recipient_user_id=int(assignee_user_id),
            routing_rule_id=case.routing_rule_id,
        )


async def enqueue_case(session: AsyncSession, *, case: AnomalyCase, reason: str | None = None) -> None:
    """Sem roteamento: o caso vai para a fila FinOps, visível e monitorado."""
    await notify_case(
        session,
        case=case,
        kind=KIND_QUEUE,
        title=f"Caso #{case.id} na fila FinOps (sem responsável)",
        body=reason or f"{case.event_family} — nota {case.score:.0f} ({case.severity})",
        recipient_user_id=None,
        is_finops_queue=True,
    )


async def notify_severity_increase(
    session: AsyncSession, *, case: AnomalyCase, previous: str | None
) -> None:
    if severity_rank(case.severity) <= severity_rank(previous or "LOW"):
        return
    await notify_case(
        session,
        case=case,
        kind=KIND_SEVERITY_INCREASE,
        title=f"Caso #{case.id}: severidade {previous or '-'} → {case.severity}",
        body=f"{case.event_family} — nota {case.score:.0f}",
        recipient_user_id=case.assignee_user_id,
    )


async def notify_due_soon(
    session: AsyncSession, *, horizon_hours: int = 4, limit: int = 200
) -> int:
    """Avisa sobre prazos de ciência a vencer na janela `[agora, agora + horas]`.

    Só considera casos abertos e ainda não confirmados. A deduplicação por caso +
    evento garante uma única notificação por caso, por mais que o agendador rode.
    """
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=max(1, int(horizon_hours)))
    rows = (
        await session.execute(
            select(AnomalyCase)
            .where(
                AnomalyCase.due_at.is_not(None),
                AnomalyCase.due_at >= now,
                AnomalyCase.due_at <= horizon,
                AnomalyCase.acknowledged_at.is_(None),
                AnomalyCase.state.in_(OPEN_STATES),
            )
            .limit(limit)
        )
    ).scalars().all()
    created = 0
    for case in rows:
        notification = await notify_case(
            session,
            case=case,
            kind=KIND_DUE_SOON,
            title=f"Caso #{case.id}: prazo de ciência a vencer",
            body=(
                f"{case.event_family} — vence em "
                f"{case.due_at.isoformat() if case.due_at else '?'} ({case.severity})"
            ),
            recipient_user_id=case.assignee_user_id,
        )
        if notification is not None:
            created += 1
        if case.assignee_user_id is not None:
            queue_copy = await notify_case(
                session,
                case=case,
                kind=KIND_DUE_SOON,
                title=f"Caso #{case.id}: prazo de ciência a vencer (fila)",
                body=case.event_family,
                recipient_user_id=None,
                is_finops_queue=True,
            )
            created += 1 if queue_copy else 0
    return created


async def expire_overdue_notifications(session: AsyncSession, *, limit: int = 200) -> int:
    """Vencimento de prazo gera notificação interna à fila FinOps."""
    now = datetime.now(timezone.utc)
    rows = (
        await session.execute(
            select(AnomalyCase)
            .where(
                AnomalyCase.due_at.is_not(None),
                AnomalyCase.due_at < now,
                AnomalyCase.acknowledged_at.is_(None),
                AnomalyCase.state.in_(OPEN_STATES),
            )
            .limit(limit)
        )
    ).scalars().all()
    created = 0
    for case in rows:
        recipient = case.assignee_user_id
        notification = await notify_case(
            session,
            case=case,
            kind=KIND_OVERDUE,
            title=f"Caso #{case.id} com prazo vencido",
            body=f"{case.event_family} — responsável {'definido' if recipient else 'ausente'}",
            recipient_user_id=recipient,
        )
        # O vencimento sempre aparece na fila FinOps, mesmo com responsável.
        if recipient is not None:
            queue_copy = await notify_case(
                session,
                case=case,
                kind=KIND_OVERDUE,
                title=f"Caso #{case.id} com prazo vencido (fila)",
                body=case.event_family,
                recipient_user_id=None,
                is_finops_queue=True,
            )
            created += 1 if queue_copy else 0
        created += 1 if notification else 0
    return created


async def inbox(
    session: AsyncSession,
    *,
    user_id: int,
    unread_only: bool = False,
    include_queue: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AnomalyNotification], int]:
    """Notificações do usuário (caixa interna) e contador de não lidas."""
    filters = []
    if include_queue:
        filters.append(
            or_(
                AnomalyNotification.recipient_user_id == user_id,
                AnomalyNotification.is_finops_queue.is_(True),
            )
        )
    else:
        filters.append(AnomalyNotification.recipient_user_id == user_id)
    if unread_only:
        filters.append(AnomalyNotification.read_at.is_(None))

    query: Select = select(AnomalyNotification).where(*filters)
    total = (
        await session.execute(select(func.count()).select_from(query.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            query.order_by(AnomalyNotification.created_at.desc(), AnomalyNotification.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return list(rows), int(total)


async def mark_notification(
    session: AsyncSession, *, notification_id: int, user_id: int, acknowledge: bool = False
) -> AnomalyNotification:
    notification = await session.get(AnomalyNotification, notification_id)
    if notification is None:
        raise CaseNotFound(f"notificação {notification_id} não encontrada")
    now = datetime.now(timezone.utc)
    if notification.read_at is None:
        notification.read_at = now
    if acknowledge and notification.acknowledged_at is None:
        notification.acknowledged_at = now
    await session.flush()
    return notification


# --------------------------------------------------------------------------
# Transições
# --------------------------------------------------------------------------
async def _mandatory_actions_open(session: AsyncSession, case_id: int) -> int:
    rows = (
        await session.execute(
            select(func.count())
            .select_from(AnomalyAction)
            .where(
                AnomalyAction.case_id == case_id,
                AnomalyAction.mandatory.is_(True),
                AnomalyAction.status.in_(("PLANNED", "IN_PROGRESS")),
            )
        )
    ).scalar_one()
    return int(rows)


async def _actions_total(session: AsyncSession, case_id: int, *, action_type: str = "CORRECTIVE") -> int:
    rows = (
        await session.execute(
            select(func.count())
            .select_from(AnomalyAction)
            .where(
                AnomalyAction.case_id == case_id,
                AnomalyAction.action_type == action_type,
                AnomalyAction.status != "CANCELLED",
            )
        )
    ).scalar_one()
    return int(rows)


_TRANSITION_FIELDS = (
    "cause_category",
    "cause_hypothesis",
    "decision",
    "decision_note",
    "learning",
    "preventive_measure",
    "no_preventive_measure_reason",
    "verification_note",
    "verification_reference_id",
)


async def apply_command(
    session: AsyncSession,
    *,
    case_id: int,
    command: str,
    payload: Mapping[str, Any] | None = None,
    actor_user_id: int | None = None,
    expected_version: int | None = None,
    recovery_valid_days: int = RECOVERY_VALID_DAYS,
) -> AnomalyCase:
    """Aplica um comando explícito do workflow.

    Valida estado, campos e versão; grava o evento imutável; mantém o caso
    coerente com o caminho escolhido pela decisão.
    """
    from app.services.anomaly_state import (
        AnomalyValidationError,
        AnomalyVersionConflict,
        assert_case_version,
        resolve_next_state,
        validate_transition,
    )

    payload = dict(payload or {})
    case = await session.get(AnomalyCase, case_id)
    if case is None:
        raise CaseNotFound(f"caso {case_id} não encontrado")

    try:
        assert_case_version(case, expected_version)
    except AnomalyVersionConflict as exc:
        raise WorkflowError(str(exc)) from exc

    try:
        transition = validate_transition(
            case,
            command,
            payload,
            mandatory_actions_open=await _mandatory_actions_open(session, case_id),
            actions_total=await _actions_total(session, case_id),
        )
    except AnomalyValidationError as exc:
        raise WorkflowError(str(exc)) from exc
    except Exception as exc:  # AnomalyStateError
        raise WorkflowError(str(exc)) from exc

    next_state = resolve_next_state(case, command, payload)
    from_state = case.state
    now = datetime.now(timezone.utc)

    if command == "assign":
        await assign_case(
            session,
            case=case,
            assignee_user_id=int(payload["assignee_user_id"]),
            routing_rule_id=payload.get("routing_rule_id"),
        )
    elif command == "return_to_queue":
        case.assignee_user_id = None
        case.due_at = None
        await enqueue_case(session, case=case, reason=payload.get("note"))
    elif command == "acknowledge":
        if case.acknowledged_at is None:
            case.acknowledged_at = now
            case.acknowledged_by_id = actor_user_id
    elif command == "start_investigation":
        case.investigation_started_at = now

    for field in _TRANSITION_FIELDS:
        if field in payload and payload[field] is not None:
            setattr(case, field, payload[field])

    if command in ("decide", "redecide"):
        case.decided_at = now
        case.decided_by_id = actor_user_id
        case.decision = str(payload.get("decision"))
    elif command == "start_remediation":
        case.remediation_started_at = now
    elif command == "start_verification":
        case.verification_started_at = now
        case.valid_days_streak = 0
    elif command == "validate_result":
        case.verification_succeeded_at = now
    elif command == "record_retrospective":
        case.retrospective_at = now
    elif command == "close":
        case.closed_at = now
        case.closed_by_id = actor_user_id
    elif command == "reopen":
        case.reopen_count = int(case.reopen_count or 0) + 1
        case.closed_at = None
        case.closed_by_id = None
        case.verification_started_at = None
        case.verification_succeeded_at = None
        case.valid_days_streak = 0

    if next_state != from_state:
        case.state = next_state
        case.state_changed_at = now
    case.case_version = int(case.case_version or 1) + 1
    await session.flush()

    event = AnomalyCaseEvent(
        case_id=case.id,
        event_type="STATE_TRANSITION" if next_state != from_state else "CASE_NOTE",
        command=command,
        from_state=from_state,
        to_state=case.state,
        actor_user_id=actor_user_id,
        note=payload.get("note") or payload.get("decision_note"),
        payload={key: value for key, value in payload.items() if key != "note"},
        case_version=case.case_version,
    )
    session.add(event)
    await session.flush()

    if command == "validate_result" and payload.get("verification_outcome") in ("SUCCESS", "CONFIRMED_ABSENCE"):
        await notify_case(
            session,
            case=case,
            kind=KIND_VERIFICATION_COMPLETE,
            title=f"Caso #{case.id}: verificação validada",
            body=payload.get("verification_note"),
            recipient_user_id=case.assignee_user_id,
        )
    return case


# --------------------------------------------------------------------------
# Verificação (plano §3.7)
# --------------------------------------------------------------------------
@dataclass
class VerificationUpdate:
    valid_days: int
    outcome: str
    reason: str
    note: str


def evaluate_verification_day(
    *,
    observed_cloud: float,
    upper_cloud: float,
    data_partial: bool,
) -> tuple[bool, str]:
    """Um dia válido é observado dentro do limite **e** sem dado parcial.

    Gap interrompe a sequência; dado parcial nunca confirma recuperação.
    """
    if data_partial:
        return False, "PARTIAL_DATA"
    if float(observed_cloud) > float(upper_cloud):
        return False, "ABOVE_BAND"
    return True, "WITHIN_BAND"


def apply_verification_days(
    *,
    current_streak: int,
    days: Iterable[Mapping[str, Any]],
    required_days: int = RECOVERY_VALID_DAYS,
) -> VerificationUpdate:
    """Sequência de dias válidos **consecutivos** posteriores à ação.

    Um gap (dia ausente) ou um dia inválido zera a sequência: o plano exige dias
    válidos consecutivos.
    """
    ordered = sorted(days, key=lambda row: row.get("event_date") or "")
    streak = int(current_streak or 0)
    previous: str | None = None
    last_reason = "NO_DATA"
    for row in ordered:
        day = str(row.get("event_date") or "")
        if previous is not None:
            try:
                gap = (
                    datetime.fromisoformat(day).date() - datetime.fromisoformat(previous).date()
                ).days
            except ValueError:
                gap = 1
            if gap != 1:
                streak = 0
        valid, reason = evaluate_verification_day(
            observed_cloud=float(row.get("observed_cloud") or 0.0),
            upper_cloud=float(row.get("upper_cloud") or 0.0),
            data_partial=bool(row.get("data_partial")),
        )
        streak = streak + 1 if valid else 0
        last_reason = reason
        previous = day

    if streak >= int(required_days):
        return VerificationUpdate(
            valid_days=streak,
            outcome="SUCCESS_PENDING_VALIDATION",
            reason="VALID_DAYS_REACHED",
            note=f"{streak} dia(s) válido(s) consecutivo(s): sucesso habilita validação humana",
        )
    return VerificationUpdate(
        valid_days=streak,
        outcome="IN_PROGRESS",
        reason=last_reason,
        note=f"{streak} de {required_days} dia(s) válido(s) consecutivo(s)",
    )


# --------------------------------------------------------------------------
# Listagens e métricas
# --------------------------------------------------------------------------
def case_query(
    *,
    states: Sequence[str] | None = None,
    severities: Sequence[str] | None = None,
    assignee_user_id: int | None = None,
    queue_only: bool = False,
    period: str | None = None,
    config_id: int | None = None,
    overdue_only: bool = False,
) -> Select:
    query = select(AnomalyCase)
    if states:
        query = query.where(AnomalyCase.state.in_(tuple(states)))
    if severities:
        query = query.where(AnomalyCase.severity.in_(tuple(severities)))
    if assignee_user_id is not None:
        query = query.where(AnomalyCase.assignee_user_id == assignee_user_id)
    if queue_only:
        query = query.where(AnomalyCase.assignee_user_id.is_(None), AnomalyCase.state != "CLOSED")
    if period:
        query = query.where(AnomalyCase.period == str(period))
    if config_id is not None:
        query = query.where(AnomalyCase.anomaly_config_id == config_id)
    if overdue_only:
        query = query.where(
            AnomalyCase.due_at.is_not(None),
            AnomalyCase.due_at < datetime.now(timezone.utc),
            AnomalyCase.acknowledged_at.is_(None),
        )
    return query


async def list_cases(
    session: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
    order_by: str = "score",
    **filters: Any,
) -> tuple[list[AnomalyCase], int]:
    query = case_query(**filters)
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    order = (
        AnomalyCase.due_at.asc().nullslast()
        if order_by == "due"
        else AnomalyCase.score.desc()
    )
    secondary = AnomalyCase.event_date.desc()
    rows = (
        await session.execute(query.order_by(order, secondary).limit(limit).offset(offset))
    ).scalars().all()
    return list(rows), int(total)


async def case_summary(session: AsyncSession, *, config_id: int | None = None) -> dict[str, Any]:
    """Indicadores do plano §5.2 (sem estimar falsos negativos)."""
    base = select(AnomalyCase)
    if config_id is not None:
        base = base.where(AnomalyCase.anomaly_config_id == config_id)
    rows = (await session.execute(base)).scalars().all()
    now = datetime.now(timezone.utc)

    by_state: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    decisions: dict[str, int] = {}
    unassigned = overdue = closed = 0
    anomalous_total = 0.0
    acknowledgement_hours: list[float] = []
    resolution_hours: list[float] = []
    recurrence: dict[str, int] = {}

    for case in rows:
        by_state[case.state] = by_state.get(case.state, 0) + 1
        by_severity[case.severity] = by_severity.get(case.severity, 0) + 1
        if case.decision:
            decisions[case.decision] = decisions.get(case.decision, 0) + 1
        if case.state != "CLOSED" and case.assignee_user_id is None:
            unassigned += 1
        if case.due_at and case.acknowledged_at is None and case.state != "CLOSED" and case.due_at < now:
            overdue += 1
        if case.state == "CLOSED":
            closed += 1
        anomalous_total += float(case.anomalous_cost_cloud or 0.0)
        if case.acknowledged_at and case.first_detected_at:
            acknowledgement_hours.append(
                (case.acknowledged_at - case.first_detected_at).total_seconds() / 3600.0
            )
        if case.closed_at and case.first_detected_at:
            resolution_hours.append(
                (case.closed_at - case.first_detected_at).total_seconds() / 3600.0
            )
        if case.event_family:
            recurrence[case.event_family] = recurrence.get(case.event_family, 0) + 1

    classified = sum(
        decisions.get(decision, 0)
        for decision in ("EXPECTED", "DATA_ISSUE", "CONFIRMED_ACTION", "CONFIRMED_ACCEPTED", "LOW_PRIORITY_NO_ACTION")
    )
    return {
        "total": len(rows),
        "by_state": by_state,
        "by_severity": by_severity,
        "decisions": decisions,
        "new": by_state.get("NEW", 0),
        "open": len(rows) - closed,
        "overdue": overdue,
        "closed": closed,
        "unassigned": unassigned,
        "anomalous_cost_cloud": round(anomalous_total, 2),
        "median_acknowledgement_hours": _median(acknowledgement_hours),
        "median_resolution_hours": _median(resolution_hours),
        "classified": classified,
        "recurrence_families": sum(1 for count in recurrence.values() if count > 1),
        "notes": [
            "falsos negativos não são estimados sem evidência de evento perdido",
            "aceitação de risco não é contada como falso positivo",
        ],
    }


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 2)
    return round((ordered[middle - 1] + ordered[middle]) / 2, 2)


async def case_events(session: AsyncSession, *, case_id: int, limit: int = 200) -> list[AnomalyCaseEvent]:
    rows = (
        await session.execute(
            select(AnomalyCaseEvent)
            .where(AnomalyCaseEvent.case_id == case_id)
            .order_by(AnomalyCaseEvent.id.asc())
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)


async def case_steps(session: AsyncSession, *, case: AnomalyCase) -> list[dict[str, Any]]:
    """Painel de etapas: responsável, prazo e próxima ação (plano §5.2)."""
    from app.services.anomaly_state import available_commands

    actions = (
        await session.execute(select(AnomalyAction).where(AnomalyAction.case_id == case.id))
    ).scalars().all()
    open_actions = [a for a in actions if a.status in ("PLANNED", "IN_PROGRESS")]
    return [
        {"etapa": "Detecção", "quando": _iso(case.first_detected_at), "responsavel": None},
        {"etapa": "Atribuição", "quando": _iso(case.state_changed_at), "responsavel": case.assignee_user_id},
        {"etapa": "Ciência", "quando": _iso(case.acknowledged_at), "responsavel": case.acknowledged_by_id},
        {"etapa": "Investigação", "quando": _iso(case.investigation_started_at), "responsavel": case.assignee_user_id},
        {"etapa": "Decisão", "quando": _iso(case.decided_at), "responsavel": case.decided_by_id, "decisao": case.decision},
        {"etapa": "Remediação", "quando": _iso(case.remediation_started_at), "acoes_abertas": len(open_actions)},
        {"etapa": "Verificação", "quando": _iso(case.verification_started_at), "dias_validos": case.valid_days_streak},
        {"etapa": "Retrospectiva", "quando": _iso(case.retrospective_at)},
        {"etapa": "Encerramento", "quando": _iso(case.closed_at), "responsavel": case.closed_by_id},
    ] + [
        {
            "proxima_acao": command,
            "prazo": _iso(case.due_at),
            "responsavel": case.assignee_user_id,
        }
        for command in available_commands(case.state)[:1]
    ]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
