"""Testes do workflow operacional (E7/E8)."""
from datetime import date

import pytest

from app.services.anomaly_state import TRANSITIONS
from app.services.anomaly_workflow import (
    COMMAND_SCOPES,
    KIND_ASSIGNMENT,
    KIND_OVERDUE,
    KIND_QUEUE,
    KIND_SEVERITY_INCREASE,
    NOTIFICATION_KINDS,
    OPEN_STATES,
    apply_verification_days,
    evaluate_verification_day,
    notification_dedup_key,
    resolve_routing,
)

# ---------------------------------------------------------------------------
# Roteamento (plano §3.3)
# ---------------------------------------------------------------------------
RULES = [
    {"id": "generico", "when": {"cloud_origem": "azure-dev"}, "assignee_user_id": 10},
    {"id": "especifico", "when": {"cloud_origem": "azure-dev", "servicename": "Virtual Machines"}, "assignee_user_id": 20},
    {"id": "outro", "when": {"cloud_origem": "gcp-producao"}, "assignee_user_id": 30},
]


def test_maior_especificidade_prevalece():
    match = resolve_routing(RULES, {"cloud_origem": "azure-dev", "servicename": "Virtual Machines"})
    assert match.rule_id == "especifico"
    assert match.assignee_user_id == 20
    assert match.specificity == 2


def test_regra_menos_especifica_quando_nao_ha_match_mais_fino():
    match = resolve_routing(RULES, {"cloud_origem": "azure-dev", "servicename": "Storage"})
    assert match.rule_id == "generico"
    assert match.assignee_user_id == 10
    assert match.specificity == 1


def test_ambiguidade_e_rejeitada_e_vai_para_a_fila():
    ambiguous = [
        {"id": "a", "when": {"cloud_origem": "x"}, "assignee_user_id": 1},
        {"id": "b", "when": {"cloud_origem": "x"}, "assignee_user_id": 2},
    ]
    match = resolve_routing(ambiguous, {"cloud_origem": "x"})
    assert match.ambiguous is True
    assert match.assignee_user_id is None
    assert match.rule_id is None
    assert set(match.candidates) == {"a", "b"}


def test_sem_regra_aplicavel_nao_atribui():
    match = resolve_routing(RULES, {"cloud_origem": "oci"})
    assert match.assignee_user_id is None
    assert match.rule_id is None
    assert match.ambiguous is False
    assert match.specificity == 0


def test_sem_regras_configuradas():
    assert resolve_routing(None, {"cloud_origem": "x"}).assignee_user_id is None
    assert resolve_routing([], {"cloud_origem": "x"}).rule_id is None


def test_roteamento_compara_sem_case_e_espaco():
    match = resolve_routing(
        [{"id": "r", "when": {"cloud_origem": "Azure-Dev"}, "assignee_user_id": 5}],
        {"cloud_origem": " azure-dev "},
    )
    assert match.assignee_user_id == 5


def test_nao_infere_usuario_de_texto_livre_em_tags():
    """Roteamento só atribui por regra explícita; tag livre nunca vira usuário."""
    match = resolve_routing(
        [{"id": "r", "when": {"cloud_origem": "x"}, "assignee_user_id": None}],
        {"cloud_origem": "x", "tags": "owner=joao.silva"},
    )
    assert match.assignee_user_id is None


# ---------------------------------------------------------------------------
# Notificações (plano §3.3)
# ---------------------------------------------------------------------------
def test_dedup_por_caso_evento_destinatario():
    a = notification_dedup_key(case_id=1, kind=KIND_ASSIGNMENT, recipient_user_id=7)
    b = notification_dedup_key(case_id=1, kind=KIND_ASSIGNMENT, recipient_user_id=7)
    c = notification_dedup_key(case_id=1, kind=KIND_ASSIGNMENT, recipient_user_id=8)
    d = notification_dedup_key(case_id=1, kind=KIND_SEVERITY_INCREASE, recipient_user_id=7)
    e = notification_dedup_key(case_id=2, kind=KIND_ASSIGNMENT, recipient_user_id=7)
    assert a == b
    assert len({a, c, d, e}) == 4


def test_fila_finops_tem_chave_propria():
    queue = notification_dedup_key(case_id=1, kind=KIND_QUEUE, recipient_user_id=None)
    user = notification_dedup_key(case_id=1, kind=KIND_QUEUE, recipient_user_id=7)
    assert queue != user


def test_tipos_de_notificacao_do_plano():
    for kind in (
        KIND_ASSIGNMENT,
        KIND_SEVERITY_INCREASE,
        KIND_OVERDUE,
        KIND_QUEUE,
    ):
        assert kind in NOTIFICATION_KINDS


# ---------------------------------------------------------------------------
# Verificação (plano §3.7)
# ---------------------------------------------------------------------------
def day(value: str, observed: float, upper: float = 100.0, partial: bool = False):
    return {"event_date": value, "observed_cloud": observed, "upper_cloud": upper, "data_partial": partial}


def test_tres_dias_validos_consecutivos_habilitam_validacao_humana():
    update = apply_verification_days(
        current_streak=0,
        days=[day("2026-10-13", 90.0), day("2026-10-14", 95.0), day("2026-10-15", 80.0)],
    )
    assert update.valid_days == 3
    assert update.outcome == "SUCCESS_PENDING_VALIDATION"
    assert "validação humana" in update.note


def test_gap_interrompe_a_sequencia():
    update = apply_verification_days(
        current_streak=0,
        days=[day("2026-10-13", 90.0), day("2026-10-14", 90.0), day("2026-10-16", 90.0)],
    )
    assert update.valid_days == 1
    assert update.outcome == "IN_PROGRESS"


def test_dia_acima_da_faixa_zera_a_sequencia():
    update = apply_verification_days(
        current_streak=2,
        days=[day("2026-10-13", 150.0)],
    )
    assert update.valid_days == 0
    assert update.reason == "ABOVE_BAND"


def test_dado_parcial_nao_confirma_recuperacao():
    valid, reason = evaluate_verification_day(observed_cloud=10.0, upper_cloud=100.0, data_partial=True)
    assert valid is False
    assert reason == "PARTIAL_DATA"

    update = apply_verification_days(
        current_streak=0,
        days=[day("2026-10-13", 10.0, partial=True), day("2026-10-14", 10.0, partial=True),
              day("2026-10-15", 10.0, partial=True)],
    )
    assert update.valid_days == 0
    assert update.outcome == "IN_PROGRESS"


def test_sem_dias_suficientes_permanece_em_andamento():
    update = apply_verification_days(current_streak=0, days=[day("2026-10-13", 90.0)])
    assert update.outcome == "IN_PROGRESS"
    assert update.valid_days == 1
    assert "1 de 3" in update.note


def test_sequencia_continua_a_partir_do_estado_atual():
    update = apply_verification_days(
        current_streak=2, days=[day("2026-10-13", 90.0)]
    )
    assert update.valid_days == 3
    assert update.outcome == "SUCCESS_PENDING_VALIDATION"


# ---------------------------------------------------------------------------
# Escopos e estados
# ---------------------------------------------------------------------------
def test_todo_comando_do_workflow_tem_escopo():
    missing = set(TRANSITIONS) - set(COMMAND_SCOPES)
    assert not missing, f"comandos sem escopo: {sorted(missing)}"
    extra = set(COMMAND_SCOPES) - set(TRANSITIONS)
    assert not extra, f"escopos órfãos: {sorted(extra)}"


def test_escopos_permitidos_sao_os_do_plano():
    assert set(COMMAND_SCOPES.values()) == {"assign", "investigate", "decide", "close"}


def test_estados_abertos_excluem_encerrado():
    assert "CLOSED" not in OPEN_STATES
    for state in ("NEW", "ASSIGNED", "INVESTIGATING", "DECIDED", "IN_REMEDIATION", "VERIFYING", "RETROSPECTIVE"):
        assert state in OPEN_STATES


def test_comandos_por_escopo():
    assign = [c for c, s in COMMAND_SCOPES.items() if s == "assign"]
    assert set(assign) == {"assign", "return_to_queue"}
    decide = [c for c, s in COMMAND_SCOPES.items() if s == "decide"]
    assert set(decide) == {"decide", "redecide", "start_remediation", "cancel_remediation"}
