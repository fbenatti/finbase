"""Testes da máquina de estados da gestão de anomalias (E1).

Herméticos: não tocam PostgreSQL, Trino nem Iceberg. A máquina de estados é
Python puro justamente para permitir isso.
"""
from types import SimpleNamespace

import pytest

from app.services.anomaly_state import (
    AnomalyStateError,
    AnomalyValidationError,
    AnomalyVersionConflict,
    CASE_STATES,
    DECISIONS,
    TRANSITIONS,
    assert_case_version,
    available_commands,
    resolve_next_state,
    validate_transition,
)


def case(**overrides):
    base = dict(
        state="NEW",
        case_version=1,
        decision=None,
        cause_category=None,
        learning=None,
        preventive_measure=None,
        no_preventive_measure_reason=None,
        remediation_started_at=None,
        verification_succeeded_at=None,
        verification_note=None,
        assignee_user_id=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# Estrutura
# ---------------------------------------------------------------------------
def test_todos_os_estados_do_plano_existem():
    assert CASE_STATES == (
        "NEW", "ASSIGNED", "INVESTIGATING", "DECIDED",
        "IN_REMEDIATION", "VERIFYING", "RETROSPECTIVE", "CLOSED",
    )


def test_todo_estado_alcancavel_e_com_saida():
    reachable = {"NEW"}
    for transition in TRANSITIONS.values():
        target = transition.to_state
        if target not in ("@same", "@by_decision"):
            reachable.add(target)
    assert reachable == set(CASE_STATES)

    # CLOSED é o único estado sem comando de permanência; tem `reopen`.
    assert "reopen" in available_commands("CLOSED")
    assert available_commands("NEW") == sorted(
        c for c, t in TRANSITIONS.items() if "NEW" in t.from_states
    )


def test_vocabulario_de_decisoes_do_plano():
    assert set(DECISIONS) == {
        "EXPECTED", "DATA_ISSUE", "CONFIRMED_ACTION",
        "CONFIRMED_ACCEPTED", "LOW_PRIORITY_NO_ACTION",
    }


# ---------------------------------------------------------------------------
# Atribuição e ciência
# ---------------------------------------------------------------------------
def test_assign_exige_responsavel():
    with pytest.raises(AnomalyValidationError, match="assignee_user_id"):
        validate_transition(case(state="NEW"), "assign", {})


def test_assign_leva_new_para_assigned():
    transition = validate_transition(case(state="NEW"), "assign", {"assignee_user_id": 7})
    assert resolve_next_state(case(state="NEW"), "assign", {"assignee_user_id": 7}) == "ASSIGNED"
    assert transition.to_state == "ASSIGNED"


def test_assign_permite_reatribuicao_sem_mudar_de_estado():
    assert resolve_next_state(case(state="ASSIGNED"), "assign", {"assignee_user_id": 9}) == "ASSIGNED"


def test_acknowledge_nao_muda_de_estado():
    current = case(state="ASSIGNED", assignee_user_id=7)
    assert resolve_next_state(current, "acknowledge", {}) == "ASSIGNED"


def test_acknowledge_exige_responsavel_definido():
    with pytest.raises(AnomalyValidationError, match="responsável"):
        validate_transition(case(state="NEW"), "acknowledge", {})


def test_return_to_queue_exige_justificativa():
    with pytest.raises(AnomalyValidationError, match="justificativa"):
        validate_transition(case(state="ASSIGNED"), "return_to_queue", {})
    assert resolve_next_state(case(state="ASSIGNED"), "return_to_queue", {"note": "sem dono"}) == "NEW"


# ---------------------------------------------------------------------------
# Investigação
# ---------------------------------------------------------------------------
def test_nao_investiga_sem_estar_atribuido():
    with pytest.raises(AnomalyStateError, match="não é permitido no estado NEW"):
        validate_transition(case(state="NEW"), "start_investigation", {})


def test_start_investigation_a_partir_de_assigned():
    assert resolve_next_state(case(state="ASSIGNED"), "start_investigation", {}) == "INVESTIGATING"


# ---------------------------------------------------------------------------
# Decisão
# ---------------------------------------------------------------------------
def test_decide_exige_decisao_causa_evidencia_e_justificativa():
    current = case(state="INVESTIGATING")
    with pytest.raises(AnomalyValidationError, match="decision"):
        validate_transition(current, "decide", {})
    with pytest.raises(AnomalyValidationError, match="cause_category"):
        validate_transition(current, "decide", {"decision": "EXPECTED"})
    with pytest.raises(AnomalyValidationError, match="justificativa"):
        validate_transition(
            current, "decide", {"decision": "EXPECTED", "cause_category": "LEGITIMATE_GROWTH"}
        )
    with pytest.raises(AnomalyValidationError, match="evidência|evidence"):
        validate_transition(
            current,
            "decide",
            {"decision": "EXPECTED", "cause_category": "LEGITIMATE_GROWTH", "note": "cresceu"},
        )


def test_decide_rejeita_decisao_fora_do_vocabulario():
    with pytest.raises(AnomalyValidationError, match="Decisão inválida"):
        validate_transition(
            case(state="INVESTIGATING"),
            "decide",
            {"decision": "MAYBE", "cause_category": "LEGITIMATE_GROWTH", "note": "x", "evidence": ["e"]},
        )


def test_decide_rejeita_causa_fora_do_vocabulario():
    with pytest.raises(AnomalyValidationError, match="Categoria de causa inválida"):
        validate_transition(
            case(state="INVESTIGATING"),
            "decide",
            {"decision": "EXPECTED", "cause_category": "ALIENS", "note": "x", "evidence": ["e"]},
        )


@pytest.mark.parametrize(
    "decision,expected_state",
    [
        ("EXPECTED", "RETROSPECTIVE"),
        ("DATA_ISSUE", "RETROSPECTIVE"),
        ("CONFIRMED_ACCEPTED", "RETROSPECTIVE"),
        ("LOW_PRIORITY_NO_ACTION", "RETROSPECTIVE"),
        ("CONFIRMED_ACTION", "IN_REMEDIATION"),
    ],
)
def test_decisao_escolhe_o_caminho_do_caso(decision, expected_state):
    payload = {
        "decision": decision,
        "cause_category": "LEGITIMATE_GROWTH",
        "note": "analisado",
        "evidence": ["billing"],
    }
    current = case(state="INVESTIGATING")
    validate_transition(current, "decide", payload)
    assert resolve_next_state(current, "decide", payload) == expected_state


def test_causa_nao_determinada_e_categoria_explicita():
    payload = {
        "decision": "CONFIRMED_ACCEPTED",
        "cause_category": "UNDETERMINED",
        "note": "sem causa conclusiva",
        "evidence": ["ticket"],
    }
    assert resolve_next_state(case(state="INVESTIGATING"), "decide", payload) == "RETROSPECTIVE"


# ---------------------------------------------------------------------------
# Remediação
# ---------------------------------------------------------------------------
def test_start_remediation_exige_acao_cadastrada():
    current = case(state="DECIDED", decision="CONFIRMED_ACTION")
    with pytest.raises(AnomalyValidationError, match="ação corretiva"):
        validate_transition(current, "start_remediation", {}, actions_total=0)
    validate_transition(current, "start_remediation", {}, actions_total=1)


def test_start_verification_bloqueada_por_acao_obrigatoria_pendente():
    current = case(state="IN_REMEDIATION", remediation_started_at="2026-10-14T10:00:00Z")
    with pytest.raises(AnomalyValidationError, match="obrigatória"):
        validate_transition(current, "start_verification", {}, mandatory_actions_open=2)
    validate_transition(current, "start_verification", {}, mandatory_actions_open=0)


def test_start_verification_exige_remediacao_iniciada():
    with pytest.raises(AnomalyValidationError, match="remediation_started_at"):
        validate_transition(case(state="IN_REMEDIATION"), "start_verification", {})


def test_fail_verification_devolve_para_remediacao():
    current = case(state="VERIFYING")
    with pytest.raises(AnomalyValidationError, match="justificativa"):
        validate_transition(current, "fail_verification", {})
    assert resolve_next_state(current, "fail_verification", {"note": "desvio persiste"}) == "IN_REMEDIATION"


def test_validate_result_exige_resultado_no_vocabulario():
    with pytest.raises(AnomalyValidationError, match="Resultado de verificação inválido"):
        validate_transition(case(state="VERIFYING"), "validate_result", {"verification_outcome": "TALVEZ"})


def test_validate_result_sucesso_exige_nota():
    current = case(state="VERIFYING")
    with pytest.raises(AnomalyValidationError, match="verification_note"):
        validate_transition(current, "validate_result", {"verification_outcome": "SUCCESS"})
    validate_transition(
        current,
        "validate_result",
        {"verification_outcome": "SUCCESS", "verification_note": "3 dias dentro da faixa"},
    )


# ---------------------------------------------------------------------------
# Retrospectiva e encerramento
# ---------------------------------------------------------------------------
def test_retrospectiva_exige_aprendizado_e_medida_preventiva():
    current = case(state="DECIDED", decision="EXPECTED")
    with pytest.raises(AnomalyValidationError, match="learning"):
        validate_transition(current, "record_retrospective", {})
    with pytest.raises(AnomalyValidationError, match="preventive_measure"):
        validate_transition(current, "record_retrospective", {"learning": "ok"})
    validate_transition(
        current, "record_retrospective", {"learning": "ok", "preventive_measure": "alerta"}
    )
    # Justificar a ausência de medida preventiva é aceito.
    validate_transition(
        current,
        "record_retrospective",
        {"learning": "ok", "no_preventive_measure_reason": "evento único"},
    )


def test_encerramento_exige_todos_os_requisitos():
    complete = case(
        state="RETROSPECTIVE",
        decision="CONFIRMED_ACCEPTED",
        cause_category="LEGITIMATE_GROWTH",
        learning="crescimento sazonal",
        preventive_measure="revisar limiar",
    )
    validate_transition(complete, "close", {"evidence": ["relatorio"]})

    with pytest.raises(AnomalyValidationError, match="evidência|evidence"):
        validate_transition(complete, "close", {})
    with pytest.raises(AnomalyValidationError, match="decisão"):
        validate_transition(
            case(state="RETROSPECTIVE", cause_category="UNDETERMINED", learning="x", preventive_measure="y"),
            "close",
            {"evidence": ["e"]},
        )


def test_encerramento_exige_resultado_de_verificacao_quando_houve_remediacao():
    remediated = case(
        state="RETROSPECTIVE",
        decision="CONFIRMED_ACTION",
        cause_category="MISCONFIGURATION",
        learning="resize",
        preventive_measure="guardrail",
        remediation_started_at="2026-10-01T00:00:00Z",
        verification_succeeded_at=None,
    )
    with pytest.raises(AnomalyValidationError, match="resultado de verificação"):
        validate_transition(remediated, "close", {"evidence": ["e"]})

    remediated.verification_succeeded_at = "2026-10-05T00:00:00Z"
    validate_transition(remediated, "close", {"evidence": ["e"]})


def test_reopen_exige_justificativa_e_volta_a_investigar():
    current = case(state="CLOSED")
    with pytest.raises(AnomalyValidationError, match="justificativa"):
        validate_transition(current, "reopen", {})
    assert resolve_next_state(current, "reopen", {"note": "episódio continuou"}) == "INVESTIGATING"


def test_caso_encerrado_nao_aceita_mais_nenhum_comando_alem_de_reopen():
    assert available_commands("CLOSED") == ["reopen"]
    for command in available_commands("RETROSPECTIVE"):
        validate_transition  # noqa: B018 - apenas documenta a leitura acima
    with pytest.raises(AnomalyStateError):
        validate_transition(case(state="CLOSED"), "start_investigation", {})


# ---------------------------------------------------------------------------
# Concorrência
# ---------------------------------------------------------------------------
def test_versao_divergente_e_conflito_409():
    with pytest.raises(AnomalyVersionConflict) as excinfo:
        assert_case_version(case(case_version=4), 3)
    assert excinfo.value.status_code == 409
    assert excinfo.value.expected == 3
    assert excinfo.value.current == 4


def test_versao_ausente_nao_bloqueia():
    assert_case_version(case(case_version=4), None)


def test_comando_desconhecido_e_erro_de_estado():
    with pytest.raises(AnomalyStateError, match="Comando desconhecido"):
        validate_transition(case(state="NEW"), "teleport", {})
