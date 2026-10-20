"""Máquina de estados dos casos de anomalia.

Transições são **comandos explícitos**, nunca edição livre de ``status``
(plano §5.3). Este módulo é puro Python: não toca banco, Iceberg ou HTTP, para
que as regras possam ser testadas sem infraestrutura.

Contrato de cada comando:

* estados de origem permitidos;
* estado de destino;
* campos obrigatórios validados no payload (e, quando já persistidos, no caso);
* decisões permitidas, quando o comando registra decisão.

O estado do workflow é separado de (plano §3.1):

* classificação do detector (``score``, ``reason_codes``);
* qualidade dos dados (``data_quality``);
* confirmação humana (``acknowledged_at``);
* evolução financeira (``anomalous_cost_cloud``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

# --------------------------------------------------------------------------
# Vocabulários
# --------------------------------------------------------------------------
CASE_STATES: tuple[str, ...] = (
    "NEW",
    "ASSIGNED",
    "INVESTIGATING",
    "DECIDED",
    "IN_REMEDIATION",
    "VERIFYING",
    "RETROSPECTIVE",
    "CLOSED",
)

# Categorias de causa (plano §3.4). "Causa não determinada" é uma categoria
# explícita: o encerramento exige causa OU a indicação de que não foi
# determinada.
CAUSE_CATEGORIES: tuple[str, ...] = (
    "LEGITIMATE_GROWTH",
    "EXPECTED_PURCHASE",
    "MISCONFIGURATION",
    "FORGOTTEN_RESOURCE",
    "PRICE_OR_DISCOUNT_CHANGE",
    "DATA_ISSUE",
    "SECURITY_INCIDENT",
    "UNDETERMINED",
)

DECISIONS: tuple[str, ...] = (
    "EXPECTED",
    "DATA_ISSUE",
    "CONFIRMED_ACTION",
    "CONFIRMED_ACCEPTED",
    "LOW_PRIORITY_NO_ACTION",
)

# Decisões que seguem direto para retrospectiva, sem remediação (plano §3.5).
DECISIONS_WITHOUT_REMEDIATION: tuple[str, ...] = (
    "EXPECTED",
    "DATA_ISSUE",
    "CONFIRMED_ACCEPTED",
    "LOW_PRIORITY_NO_ACTION",
)

SEVERITIES: tuple[str, ...] = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
CONFIDENCES: tuple[str, ...] = ("HIGH", "MEDIUM", "LOW", "UNAVAILABLE")


class AnomalyWorkflowError(Exception):
    """Transição inválida: estado, comando ou campo obrigatório."""

    status_code = 422


class AnomalyVersionConflict(Exception):
    """Versão do caso divergente — atualização concorrente (HTTP 409)."""

    status_code = 409

    def __init__(self, expected: int, current: int):
        self.expected = expected
        self.current = current
        super().__init__(
            f"Versão do caso divergente: esperada {expected}, atual {current}. "
            "Recarregue o caso e repita o comando."
        )


class AnomalyStateError(AnomalyWorkflowError):
    """Comando não permitido a partir do estado atual."""


class AnomalyValidationError(AnomalyWorkflowError):
    """Campo obrigatório ausente ou valor fora do vocabulário."""


def _blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return False


@dataclass(frozen=True)
class Transition:
    """Definição declarativa de um comando do workflow."""

    command: str
    from_states: frozenset[str]
    to_state: str
    required: tuple[str, ...] = ()
    # Campos obrigatórios apenas quando ausentes no caso persistido.
    required_once: tuple[str, ...] = ()
    decision_in: frozenset[str] | None = None
    summary: str = ""
    # Evento que exige justificativa textual.
    note_required: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def resolve_state(self, case: Any, payload: Mapping[str, Any]) -> str:
        """Destino efetivo: a decisão pode escolher o caminho (plano §3.5).

        ``CONFIRMED_ACTION`` para em ``DECIDED`` — e não salta direto para
        ``IN_REMEDIATION``. O plano descreve o caminho "criar ação → remediação →
        verificação", e é ``start_remediation`` que exige ao menos uma ação
        cadastrada e marca ``remediation_started_at``.

        O salto direto criava um beco sem saída: o caso entrava em
        ``IN_REMEDIATION`` sem ``remediation_started_at``, e ``start_verification``
        exige esse campo — nenhum comando era aceito dali para frente.
        """
        resolved = self.to_state
        if resolved == "@by_decision":
            decision = payload.get("decision")
            if decision in DECISIONS_WITHOUT_REMEDIATION:
                return "RETROSPECTIVE"
            return "DECIDED"
        return resolved


def _t(command: str, from_states: Iterable[str], to_state: str, **kwargs: Any) -> Transition:
    return Transition(
        command=command,
        from_states=frozenset(from_states),
        to_state=to_state,
        **kwargs,
    )


TRANSITIONS: dict[str, Transition] = {
    t.command: t
    for t in (
        # --- Atribuição e ciência (plano §3.3) -----------------------------
        _t(
            "assign",
            ("NEW", "ASSIGNED"),
            "ASSIGNED",
            required=("assignee_user_id",),
            summary="Define o responsável e habilita a notificação interna.",
        ),
        _t(
            "acknowledge",
            ("NEW", "ASSIGNED", "INVESTIGATING"),
            "@same",
            summary="Responsável confirma ciência do caso.",
        ),
        _t(
            "return_to_queue",
            ("NEW", "ASSIGNED"),
            "NEW",
            note_required=True,
            summary="Devolve o caso à fila FinOps sem responsável.",
        ),
        # --- Investigação (plano §3.4) -------------------------------------
        _t(
            "start_investigation",
            ("ASSIGNED",),
            "INVESTIGATING",
            summary="Responsável inicia a análise.",
        ),
        _t(
            "update_investigation",
            ("INVESTIGATING",),
            "@same",
            summary="Registra hipótese, evidências ou dados confiáveis.",
        ),
        # --- Decisão (plano §3.5) ------------------------------------------
        _t(
            "decide",
            ("INVESTIGATING",),
            "@by_decision",
            required=("decision", "cause_category"),
            decision_in=frozenset(DECISIONS),
            note_required=True,
            summary="Registra causa e decisão; escolhe o caminho do caso.",
        ),
        _t(
            "redecide",
            ("DECIDED",),
            "@by_decision",
            required=("decision", "cause_category"),
            decision_in=frozenset(DECISIONS),
            note_required=True,
            summary="Revisa a decisão antes de haver ação em andamento.",
        ),
        # --- Remediação (plano §3.6) ---------------------------------------
        _t(
            "start_remediation",
            ("DECIDED",),
            "IN_REMEDIATION",
            summary="Exige ação corretiva obrigatória cadastrada.",
        ),
        _t(
            "cancel_remediation",
            ("IN_REMEDIATION",),
            "DECIDED",
            note_required=True,
            summary="Cancela as ações e devolve o caso à decisão.",
        ),
        # --- Verificação (plano §3.7) --------------------------------------
        _t(
            "start_verification",
            ("IN_REMEDIATION", "DECIDED"),
            "VERIFYING",
            required_once=("remediation_started_at",),
            summary="Inicia o acompanhamento de três dias válidos ou do próximo evento.",
        ),
        _t(
            "fail_verification",
            ("VERIFYING",),
            "IN_REMEDIATION",
            note_required=True,
            summary="Desvio persistente devolve o caso à remediação.",
        ),
        _t(
            "validate_result",
            ("VERIFYING",),
            "RETROSPECTIVE",
            required=("verification_outcome",),
            decision_in=frozenset(("SUCCESS", "CONFIRMED_ABSENCE")),
            summary="Validação humana do resultado da verificação.",
        ),
        # --- Retrospectiva e encerramento (plano §3.8) ---------------------
        _t(
            "record_retrospective",
            ("DECIDED", "VERIFYING", "RETROSPECTIVE"),
            "RETROSPECTIVE",
            summary="Registra aprendizado e medida preventiva.",
        ),
        _t(
            "close",
            ("RETROSPECTIVE",),
            "CLOSED",
            summary="Encerramento auditado.",
        ),
        _t(
            "reopen",
            ("CLOSED",),
            "INVESTIGATING",
            note_required=True,
            summary="Continuação do mesmo episódio após encerramento.",
        ),
    )
}


def available_commands(state: str) -> list[str]:
    """Comandos válidos a partir de um estado — usado pelo painel de etapas."""
    return sorted(t.command for t in TRANSITIONS.values() if state in t.from_states)


def get_transition(command: str) -> Transition:
    try:
        return TRANSITIONS[command]
    except KeyError:
        raise AnomalyStateError(
            f"Comando desconhecido: {command}. "
            f"Comandos válidos: {', '.join(sorted(TRANSITIONS))}."
        ) from None


# --------------------------------------------------------------------------
# Validação
# --------------------------------------------------------------------------
def _case_value(case: Any, field_name: str) -> Any:
    if case is None:
        return None
    if isinstance(case, Mapping):
        return case.get(field_name)
    return getattr(case, field_name, None)


def _has_evidence(payload: Mapping[str, Any], case: Any) -> bool:
    for key in ("evidence", "evidences", "evidence_refs"):
        if not _blank(payload.get(key)):
            return True
        if not _blank(_case_value(case, key)):
            return True
    return False


def _has_learning(payload: Mapping[str, Any], case: Any) -> bool:
    for key in ("learning", "lessons_learned"):
        if not _blank(payload.get(key)) or not _blank(_case_value(case, key)):
            return True
    return False


def _has_preventive(payload: Mapping[str, Any], case: Any) -> bool:
    for key in ("preventive_measure", "preventive_actions"):
        if not _blank(payload.get(key)) or not _blank(_case_value(case, key)):
            return True
    if not _blank(payload.get("no_preventive_measure_reason")):
        return True
    return not _blank(_case_value(case, "no_preventive_measure_reason"))


def _require_evidence(payload: Mapping[str, Any], case: Any, command: str) -> None:
    if not _has_evidence(payload, case):
        raise AnomalyValidationError(
            f"Comando '{command}' exige evidência ('evidence'). O sistema apresenta "
            "evidências; a decisão precisa registrar quais foram consultadas."
        )


def _has_cause(payload: Mapping[str, Any], case: Any) -> bool:
    """Causa determinada OU indicação explícita de causa não determinada."""
    cause = payload.get("cause_category") or _case_value(case, "cause_category")
    if not _blank(cause):
        if cause not in CAUSE_CATEGORIES:
            raise AnomalyValidationError(
                f"Categoria de causa inválida: {cause}. "
                f"Valores permitidos: {', '.join(CAUSE_CATEGORIES)}."
            )
        return True
    return False


def validate_transition(
    case: Any,
    command: str,
    payload: Mapping[str, Any] | None = None,
    *,
    mandatory_actions_open: int = 0,
    actions_total: int = 0,
) -> Transition:
    """Valida o comando contra o estado e o payload.

    Levanta :class:`AnomalyStateError` quando o comando não é permitido no
    estado atual e :class:`AnomalyValidationError` quando falta campo
    obrigatório. Não altera nada.
    """
    payload = dict(payload or {})
    transition = get_transition(command)
    state = str(_case_value(case, "state") or "NEW")

    if state not in transition.from_states:
        raise AnomalyStateError(
            f"Comando '{command}' não é permitido no estado {state}. "
            f"Estados de origem válidos: {', '.join(sorted(transition.from_states))}."
        )

    for field_name in transition.required:
        if _blank(payload.get(field_name)) and _blank(_case_value(case, field_name)):
            raise AnomalyValidationError(
                f"Comando '{command}' exige o campo '{field_name}'."
            )

    for field_name in transition.required_once:
        if _blank(payload.get(field_name)) and _blank(_case_value(case, field_name)):
            raise AnomalyValidationError(
                f"Comando '{command}' exige o campo '{field_name}'."
            )

    if transition.decision_in is not None:
        if transition.command in ("decide", "redecide"):
            value = payload.get("decision")
            if value not in transition.decision_in:
                raise AnomalyValidationError(
                    f"Decisão inválida: {value}. "
                    f"Valores permitidos: {', '.join(sorted(transition.decision_in))}."
                )
        elif transition.command == "validate_result":
            value = payload.get("verification_outcome")
            if value not in transition.decision_in:
                raise AnomalyValidationError(
                    f"Resultado de verificação inválido: {value}. "
                    f"Valores permitidos: {', '.join(sorted(transition.decision_in))}."
                )

    if transition.note_required:
        note = payload.get("note") or payload.get("decision_note") or payload.get("reason")
        if _blank(note):
            raise AnomalyValidationError(
                f"Comando '{command}' exige justificativa ('note')."
            )

    # Regras específicas por comando -------------------------------------
    if command in ("decide", "redecide"):
        _has_cause(payload, case)
        _require_evidence(payload, case, command)

    if command == "start_remediation":
        if actions_total <= 0:
            raise AnomalyValidationError(
                "Comando 'start_remediation' exige ao menos uma ação corretiva cadastrada."
            )

    if command == "start_verification":
        if mandatory_actions_open > 0:
            raise AnomalyValidationError(
                f"Existem {mandatory_actions_open} ação(ões) obrigatória(s) pendente(s). "
                "Não é possível avançar para verificação (plano §3.6)."
            )

    if command == "validate_result":
        outcome = payload.get("verification_outcome")
        if outcome == "SUCCESS" and _blank(payload.get("verification_note")) and _blank(
            _case_value(case, "verification_note")
        ):
            raise AnomalyValidationError(
                "Comando 'validate_result' exige 'verification_note' para validar sucesso."
            )

    if command == "record_retrospective":
        if not _has_learning(payload, case):
            raise AnomalyValidationError(
                "Comando 'record_retrospective' exige 'learning'."
            )
        if not _has_preventive(payload, case):
            raise AnomalyValidationError(
                "Comando 'record_retrospective' exige 'preventive_measure' "
                "ou 'no_preventive_measure_reason'."
            )

    if command == "close":
        if not _has_cause(payload, case):
            raise AnomalyValidationError(
                "Encerramento exige causa ou indicação explícita de causa não determinada "
                "('cause_category')."
            )
        _require_evidence(payload, case, command)
        if not _has_learning(payload, case):
            raise AnomalyValidationError("Encerramento exige 'learning' registrado.")
        if not _has_preventive(payload, case):
            raise AnomalyValidationError(
                "Encerramento exige 'preventive_measure' ou 'no_preventive_measure_reason'."
            )
        decision = _case_value(case, "decision") or payload.get("decision")
        if _blank(decision):
            raise AnomalyValidationError("Encerramento exige decisão registrada.")
        # O resultado da verificação é exigido quando houve remediação.
        if _case_value(case, "remediation_started_at") is not None:
            succeeded = _case_value(case, "verification_succeeded_at")
            if succeeded is None and payload.get("verification_outcome") not in (
                "SUCCESS",
                "CONFIRMED_ABSENCE",
            ):
                raise AnomalyValidationError(
                    "Encerramento exige resultado de verificação quando houve remediação "
                    "(plano §3.8)."
                )

    if command == "acknowledge":
        if _blank(payload.get("actor_user_id")) and _blank(_case_value(case, "assignee_user_id")):
            raise AnomalyValidationError(
                "Comando 'acknowledge' exige responsável definido."
            )

    return transition


def resolve_next_state(case: Any, command: str, payload: Mapping[str, Any] | None = None) -> str:
    """Estado resultante do comando, tratando ``@same`` e ``@by_decision``."""
    transition = get_transition(command)
    payload = dict(payload or {})
    if transition.to_state == "@same":
        return str(_case_value(case, "state") or "NEW")
    return transition.resolve_state(case, payload)


def assert_case_version(case: Any, expected_version: int | None) -> None:
    """Bloqueio otimista: divergência de versão é conflito (HTTP 409)."""
    if expected_version is None:
        return
    current = int(_case_value(case, "case_version") or 1)
    if int(expected_version) != current:
        raise AnomalyVersionConflict(int(expected_version), current)
