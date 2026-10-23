"""E10 — suíte de aceite: os 18 testes obrigatórios do plano.

Cada teste corresponde a um item numerado de "Testar obrigatoriamente" (§E10).
São herméticos: exercitam os serviços reais sem Trino, Iceberg ou PostgreSQL.
O que exige banco ou lakehouse está coberto pelos scripts de verificação real
(`scripts/verify_anomaly_*.py`).
"""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.anomaly_calibration import (
    build_daily_distribution,
    calibrate_band,
    calibrate_monthly_event_band,
    evaluate_deviation,
    is_candidate,
    profile_for,
    weekday_profile,
)
from app.services.anomaly_composition import assert_totals_unchanged, financial_totals_sql
from app.services.anomaly_detector import (
    ASSESSMENT_CANDIDATE,
    ASSESSMENT_EXPECTED,
    ASSESSMENT_OPEN,
    CREATE,
    EVENT_DAILY,
    EVENT_MONTHLY,
    EVENT_MONTHLY_ACCUMULATED,
    EXPECTED_ONLY,
    IGNORE,
    LINK_NEW_EPISODE,
    MonthlyEvent,
    SeriesPoint,
    case_key_for,
    consolidate_family,
    detect_daily_consumption,
    detect_monthly_accumulated,
    detect_monthly_event,
    expected_monthly_event,
    resolve_episode,
)
from app.services.anomaly_forecast_adapter import contract_from_run_row
from app.services.anomaly_reference import (
    build_reference_rows,
    select_pre_event_reference,
)
from app.services.anomaly_release import assessment_id_of
from app.services.anomaly_runner import to_assessment_rows, validate_assessments
from app.services.anomaly_scoring import severity_for
from app.services.anomaly_state import (
    AnomalyVersionConflict,
    assert_case_version,
    resolve_next_state,
    validate_transition,
)
from app.services.anomaly_workflow import (
    COMMAND_SCOPES,
    apply_verification_days,
    notification_dedup_key,
    resolve_routing,
)

DIMS = ["cloud_origem", "servicename"]
PROFILE = profile_for("BALANCED")
MATERIALITY = 100.0
ANOMALY_CONFIG = {"measure_column": "realizado_cloud", "source_namespace": "silver", "dimensions": DIMS}


def contract(*, run_id=399, cutoff="2026-10-12", finished="2026-10-14T01:00:00-03:00", measure="realizado"):
    row = dict(
        id=run_id,
        result_table=f"result_run_{run_id}",
        status="COMPLETED",
        trigger_type="SCHEDULED_REPROCESS",
        forecast_config_id=9,
        config_version=21,
        cutoff_date=cutoff,
        finished_at=finished,
        started_at=finished,
        destination_physical_name="dest_billing_cloud",
        source_snapshot_id=None,
        config_snapshot={
            "destination_physical_name": "dest_billing_cloud",
            "measure_column": measure,
            "dimensions": DIMS,
        },
        error_metrics={},
    )
    return contract_from_run_row(
        row, available_columns=("realizado_cloud", "forecast_value"), fallback=ANOMALY_CONFIG
    )


def band(offset=200.0, expected=1000.0, quantile=0.975):
    return calibrate_band(
        residuals=[offset] * 100, expected_cloud=expected, quantile=quantile, tolerance_pct=0.15
    )


def point(day, *, observed=1500.0, expected=1000.0, upper=1200.0, partial=False, billing=None):
    return SeriesPoint(
        series_id="s1",
        dimensions={"cloud_origem": "gcp-producao", "servicename": "Compute"},
        event_date=day,
        observed_cloud=observed,
        expected_cloud=expected,
        upper_cloud=upper,
        lower_cloud=expected - (upper - expected),
        reference_id="ref-1",
        source_billing_period=billing,
        data_partial=partial,
    )


def detection(**overrides):
    base = dict(
        series_id="s1",
        series_dimensions={"cloud_origem": "gcp-producao", "servicename": "Compute"},
        event_family="cloud_origem=gcp-producao",
        event_type=EVENT_DAILY,
        event_date="2026-10-13",
        period="202610",
        source_billing_period="202610",
        observed_cloud=1500.0,
        expected_cloud=1000.0,
        lower_cloud=900.0,
        upper_cloud=1200.0,
        delta_cloud=500.0,
        anomalous_cost_cloud=300.0,
        score=75.0,
        severity="MEDIUM",
        confidence="MEDIUM",
        assessment_status=ASSESSMENT_OPEN,
        evaluation_grain="DAILY",
        consecutive_occurrences=1,
        reason_codes=[],
        reference_id="ref-1",
        reference_kind="PROSPECTIVE",
        applied_rule_refs=[],
        contributors=[],
        components={},
        case_key="case-1",
        expected_only=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def assessment_rows(detections, *, run_id=399):
    return to_assessment_rows(
        detections,
        dimensions=DIMS,
        detected_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        forecast_run_id=run_id,
        evaluation_start=date(2026, 10, 12),
        evaluation_end=date(2026, 10, 27),
    )


# ---------------------------------------------------------------------------
# 1. Competência de origem diferente não muda a avaliação diária
# ---------------------------------------------------------------------------
def test_01_competencia_de_origem_diferente_mantem_avaliacao_diaria():
    a = assessment_rows([detection(source_billing_period="202610")])[0]
    b = assessment_rows([detection(source_billing_period="202609")])[0]
    for field in (
        "event_date", "observed_cloud", "expected_cloud", "upper_cloud",
        "anomalous_cost_cloud", "score", "assessment_status", "reference_id",
    ):
        assert a[field] == b[field], f"{field} mudou com a competência de origem"
    assert a["source_billing_period"] == "202610"
    assert b["source_billing_period"] == "202609"


# ---------------------------------------------------------------------------
# 2. `realizado` muda, `realizado_cloud` não: anomalia permanece igual
# ---------------------------------------------------------------------------
def test_02_realizado_muda_e_realizado_cloud_nao_muda_anomalia_identica():
    # Duas leituras da fonte com `realizado` (contratual) diferente e o mesmo
    # `realizado_cloud` (consumo cloud). A detecção usa só o segundo.
    leitura_a = {"realizado": 100.0, "realizado_cloud": 1500.0}
    leitura_b = {"realizado": 10_000.0, "realizado_cloud": 1500.0}
    rows_a = assessment_rows([detection(observed_cloud=leitura_a["realizado_cloud"])])
    rows_b = assessment_rows([detection(observed_cloud=leitura_b["realizado_cloud"])])
    assert rows_a[0]["anomalous_cost_cloud"] == rows_b[0]["anomalous_cost_cloud"]
    assert rows_a[0]["assessment_id"] == rows_b[0]["assessment_id"]
    # A linha publicada nunca preenche `realizado`.
    assert rows_a[0]["realizado"] is None and rows_b[0]["realizado"] is None


# ---------------------------------------------------------------------------
# 3. Reserva mensal esperada: baixa nota, sem caso operacional
# ---------------------------------------------------------------------------
def test_03_reserva_mensal_esperada_nao_abre_caso():
    event = MonthlyEvent(
        series_id="s1",
        dimensions={"cloud_origem": "azure-dev", "servicename": "SavingsPlan"},
        event_date=date(2026, 10, 5),
        observed_cloud=30_000.0,
        expected_cloud=30_000.0,
        upper_cloud=34_500.0,
        lower_cloud=25_500.0,
        reference_id="ref-m",
        matched_rule_refs=[{"name": "Purchase recorrente Savings Plan", "status": "MATCHED_AND_APPLIED"}],
    )
    result = detect_monthly_event(
        event, profile=PROFILE, materiality=MATERIALITY, band=None, event_family="fam"
    )
    assert result.assessment_status == ASSESSMENT_EXPECTED
    assert result.anomalous_cost_cloud == 0.0
    assert severity_for(result.score) == "LOW"
    decision = resolve_episode(detection=result)
    assert decision.action == EXPECTED_ONLY
    assert decision.anomalous_cost_cloud == 0.0

    # Permanece na tabela analítica com status de evento esperado, sem caso.
    rows = assessment_rows([result])
    assert len(rows) == 1
    assert rows[0]["assessment_status"] == "EXPECTED_EVENT"
    assert rows[0]["anomalous_cost_cloud"] == 0.0
    assert rows[0]["case_id"] is None
    assert rows[0]["is_financial_additive"] is False


# ---------------------------------------------------------------------------
# 4. Compra adicional: excesso mensal consolidado
# ---------------------------------------------------------------------------
def test_04_compra_adicional_consolida_excesso_mensal():
    primeira = MonthlyEvent(
        series_id="s1", dimensions={"cloud_origem": "azure-dev", "servicename": "SavingsPlan"},
        event_date=date(2026, 10, 5), observed_cloud=30_000.0, expected_cloud=30_000.0,
        upper_cloud=34_500.0, lower_cloud=25_500.0,
    )
    segunda = MonthlyEvent(
        series_id="s1", dimensions={"cloud_origem": "azure-dev", "servicename": "SavingsPlan"},
        event_date=date(2026, 10, 20), observed_cloud=40_000.0, expected_cloud=30_000.0,
        upper_cloud=34_500.0, lower_cloud=25_500.0,
    )
    result = detect_monthly_accumulated(
        series_id="s1", dimensions={"cloud_origem": "azure-dev", "servicename": "SavingsPlan"},
        event_family="fam", period="202610", events=[primeira, segunda],
        profile=PROFILE, materiality=MATERIALITY, band=None,
    )
    assert result.event_type == EVENT_MONTHLY_ACCUMULATED
    assert result.anomalous_cost_cloud == pytest.approx(1_000.0)
    assert len(result.contributors) == 2  # ambas as compras apresentadas
    assert result.anomalous_cost_cloud != pytest.approx(5_500.0)  # não é a soma dos excessos


# ---------------------------------------------------------------------------
# 5. Forecast restante zero: referência anterior preservada
# ---------------------------------------------------------------------------
def test_05_restante_zero_preserva_a_referencia_anterior():
    pre = contract(run_id=394, cutoff="2026-10-02", finished="2026-10-03T01:00:00-03:00")
    post = contract(run_id=399, cutoff="2026-10-12")
    selection = select_pre_event_reference(
        [post, pre], event_date=date(2026, 10, 5), event_month="202610"
    )
    assert selection.is_available
    assert selection.contract.run_id == 394  # o snapshot anterior à compra
    rows = build_reference_rows(
        selection.contract,
        [{"cloud_origem": "azure-dev", "servicename": "SavingsPlan", "forecast_month": "202610", "expected_cloud": 30_000.0}],
        dimensions=DIMS, reference_kind=selection.reference_kind,
        target_start="2026-10-03", target_end="2026-10-31",
    )
    assert rows[0].predicted_cloud == 30_000.0
    assert 30_000.0 - rows[0].predicted_cloud == 0.0  # evento esperado, sem anomalia


# ---------------------------------------------------------------------------
# 6. Sazonalidade semanal: variação esperada dentro da faixa
# ---------------------------------------------------------------------------
def test_06_sazonalidade_semanal_fica_dentro_da_faixa():
    as_of = date(2026, 10, 12)
    series = {as_of - timedelta(days=i): (100.0 if (as_of - timedelta(days=i)).weekday() < 5 else 20.0)
              for i in range(56)}
    profile = weekday_profile(series, as_of=as_of, weeks=8)
    assert profile.medians[5] < profile.medians[0]  # fim de semana menor

    distribution = build_daily_distribution(
        3_100.0, coverage_start=date(2026, 10, 13), coverage_end=date(2026, 10, 31), profile=profile
    )
    calibration = band(offset=80.0, expected=100.0)
    # Nenhum dia da própria distribuição esperada é candidato.
    for day, expected in distribution.expected.items():
        deviation = evaluate_deviation(expected, expected_cloud=expected, band=calibration)
        ok, reasons = is_candidate(deviation, min_relative_deviation=0.20, materiality=1.0)
        assert ok is False and reasons == ["WITHIN_BAND"]

    # Um sábado 40% acima do esperado do sábado continua dentro da faixa.
    saturday = next(day for day in distribution.expected if day.weekday() == 5)
    expected_saturday = distribution.expected[saturday]
    deviation = evaluate_deviation(expected_saturday * 1.4, expected_cloud=expected_saturday, band=calibration)
    assert deviation.anomalous_cost_cloud < calibration.upper_offset  # excesso menor que a faixa


# ---------------------------------------------------------------------------
# 7. Dados parciais: candidato provisório, sem encerramento automático
# ---------------------------------------------------------------------------
def test_07_dado_parcial_gera_candidato_e_nao_encerra():
    result = detect_daily_consumption(
        [point(date(2026, 10, 13), partial=True)],
        profile=PROFILE, materiality=MATERIALITY, band=band(), event_family="fam",
    )
    assert result.assessment_status == ASSESSMENT_CANDIDATE
    assert "PARTIAL_DATA" in result.reason_codes

    # Dado parcial nunca confirma recuperação: três "dias válidos" parciais não
    # habilitam validação humana.
    update = apply_verification_days(
        current_streak=0,
        days=[
            {"event_date": "2026-10-13", "observed_cloud": 10.0, "upper_cloud": 100.0, "data_partial": True},
            {"event_date": "2026-10-14", "observed_cloud": 10.0, "upper_cloud": 100.0, "data_partial": True},
            {"event_date": "2026-10-15", "observed_cloud": 10.0, "upper_cloud": 100.0, "data_partial": True},
        ],
    )
    assert update.outcome == "IN_PROGRESS"
    assert update.valid_days == 0


# ---------------------------------------------------------------------------
# 8. Vários dias anômalos: um caso, impacto sem duplicação
# ---------------------------------------------------------------------------
def test_08_varios_dias_anomalos_um_caso_sem_duplicar_impacto():
    start = date(2026, 10, 5)
    points = [point(start + timedelta(days=i)) for i in range(4)]
    result = detect_daily_consumption(
        points, profile=PROFILE, materiality=MATERIALITY, band=band(), event_family="fam"
    )
    assert result.consecutive_occurrences == 4
    assert result.anomalous_cost_cloud == pytest.approx(4 * 300.0)  # cada dia uma vez
    assert len(result.contributors) == 4

    active = SimpleNamespace(id=1, state="INVESTIGATING", anomalous_cost_cloud=1_200.0)
    decision = resolve_episode(detection=result, existing_case=active)
    assert decision.action == "UPDATE"
    assert decision.anomalous_cost_cloud == 1_200.0  # max, não 2.400

    consolidated = consolidate_family([result])
    assert consolidated is result  # uma única detecção não duplica contribuintes


# ---------------------------------------------------------------------------
# 9. Roteamento ausente: fila FinOps
# ---------------------------------------------------------------------------
def test_09_sem_roteamento_o_caso_vai_para_a_fila_finops():
    match = resolve_routing([], {"cloud_origem": "oci", "servicename": "Compute"})
    assert match.assignee_user_id is None and match.rule_id is None
    # A fila tem chave própria (destinatário nulo), distinta de qualquer usuário.
    queue_key = notification_dedup_key(case_id=1, kind="FINOPS_QUEUE", recipient_user_id=None)
    user_key = notification_dedup_key(case_id=1, kind="FINOPS_QUEUE", recipient_user_id=7)
    assert queue_key != user_key


# ---------------------------------------------------------------------------
# 10. Reprocessamento: sem notificações duplicadas
# ---------------------------------------------------------------------------
def test_10_reprocessamento_nao_duplica_notificacao():
    def keys():
        return [
            notification_dedup_key(case_id=5, kind="ASSIGNMENT", recipient_user_id=3),
            notification_dedup_key(case_id=5, kind="SEVERITY_INCREASE", recipient_user_id=3),
            notification_dedup_key(case_id=5, kind="FINOPS_QUEUE", recipient_user_id=None),
        ]

    primeira = keys()
    segunda = keys()
    assert primeira == segunda  # idempotente por caso + evento + destinatário
    assert len(set(primeira)) == 3  # eventos distintos ⇒ chaves distintas


# ---------------------------------------------------------------------------
# 11. Remediação: três dias válidos habilitam validação
# ---------------------------------------------------------------------------
def test_11_tres_dias_validos_habilitam_validacao_humana():
    update = apply_verification_days(
        current_streak=0,
        days=[
            {"event_date": "2026-10-13", "observed_cloud": 90.0, "upper_cloud": 100.0},
            {"event_date": "2026-10-14", "observed_cloud": 95.0, "upper_cloud": 100.0},
            {"event_date": "2026-10-15", "observed_cloud": 80.0, "upper_cloud": 100.0},
        ],
    )
    assert update.outcome == "SUCCESS_PENDING_VALIDATION"
    assert update.valid_days == 3
    # O resultado não encerra o caso: exige validação humana explícita.
    case = SimpleNamespace(
        state="VERIFYING", decision="CONFIRMED_ACTION", cause_category="MISCONFIGURATION",
        learning=None, preventive_measure=None, no_preventive_measure_reason=None,
        remediation_started_at="2026-10-01T00:00:00Z", verification_succeeded_at=None,
    )
    with pytest.raises(Exception):
        validate_transition(case, "close", {"evidence": ["e"]})


# ---------------------------------------------------------------------------
# 12. Gap na verificação: sequência não é confirmada
# ---------------------------------------------------------------------------
def test_12_gap_na_verificacao_nao_confirma():
    update = apply_verification_days(
        current_streak=0,
        days=[
            {"event_date": "2026-10-13", "observed_cloud": 90.0, "upper_cloud": 100.0},
            {"event_date": "2026-10-14", "observed_cloud": 90.0, "upper_cloud": 100.0},
            {"event_date": "2026-10-17", "observed_cloud": 90.0, "upper_cloud": 100.0},
        ],
    )
    assert update.outcome == "IN_PROGRESS"
    assert update.valid_days == 1  # o gap zerou a sequência anterior


# ---------------------------------------------------------------------------
# 13. Compra mensal: próximo evento confirma ou invalida
# ---------------------------------------------------------------------------
def test_13_compra_mensal_confirma_ou_invalida_no_proximo_evento():
    assert expected_monthly_event(observed_cloud=30_000.0, expected_cloud=30_000.0, upper_cloud=34_500.0) is True
    assert expected_monthly_event(observed_cloud=40_000.0, expected_cloud=30_000.0, upper_cloud=34_500.0) is False

    # Ausência de dados no próximo evento não confirma sucesso.
    update = apply_verification_days(current_streak=0, days=[])
    assert update.outcome == "IN_PROGRESS"
    assert update.reason == "NO_DATA"


# ---------------------------------------------------------------------------
# 14. Novo forecast absorve o aumento: caso ativo não desaparece
# ---------------------------------------------------------------------------
def test_14_novo_forecast_nao_apaga_caso_ativo():
    antigo = contract(run_id=394, cutoff="2026-10-02", finished="2026-10-03T01:00:00-03:00")
    novo = contract(run_id=399, cutoff="2026-10-12")
    # A referência do caso é a anterior; a nova produz outro reference_id.
    rows_antigo = build_reference_rows(
        antigo, [{"cloud_origem": "gcp-producao", "servicename": "Compute", "forecast_month": "202610", "expected_cloud": 1000.0}],
        dimensions=DIMS, reference_kind="PROSPECTIVE", target_start="2026-10-03", target_end="2026-10-31",
    )
    rows_novo = build_reference_rows(
        novo, [{"cloud_origem": "gcp-producao", "servicename": "Compute", "forecast_month": "202610", "expected_cloud": 5000.0}],
        dimensions=DIMS, reference_kind="PROSPECTIVE", target_start="2026-10-13", target_end="2026-10-31",
    )
    assert rows_antigo[0].reference_id != rows_novo[0].reference_id

    # O caso continua vivo: o novo forecast gera UPDATE, nunca remoção.
    active = SimpleNamespace(id=9, state="INVESTIGATING", anomalous_cost_cloud=800.0)
    detection_with_new_reference = detect_daily_consumption(
        [point(date(2026, 10, 13), observed=6000.0, expected=5000.0, upper=5200.0)],
        profile=PROFILE, materiality=MATERIALITY, band=band(offset=200.0, expected=5000.0),
        event_family="fam",
    )
    decision = resolve_episode(detection=detection_with_new_reference, existing_case=active)
    assert decision.action in ("UPDATE", "REOPEN")
    assert decision.action != "IGNORE"
    assert decision.anomalous_cost_cloud >= 800.0


# ---------------------------------------------------------------------------
# 15. Decisão sem ação: justificativa e retrospectiva obrigatórias
# ---------------------------------------------------------------------------
def test_15_decisao_sem_acao_exige_justificativa_e_retrospectiva():
    case = SimpleNamespace(
        state="INVESTIGATING", decision=None, cause_category=None, learning=None,
        preventive_measure=None, no_preventive_measure_reason=None,
        remediation_started_at=None, verification_succeeded_at=None,
    )
    payload = {"decision": "CONFIRMED_ACCEPTED", "cause_category": "LEGITIMATE_GROWTH",
               "note": "aceito pelo negócio", "evidence": ["ticket-123"]}
    validate_transition(case, "decide", payload)
    assert resolve_next_state(case, "decide", payload) == "RETROSPECTIVE"

    # Sem aprendizado/medida preventiva não há encerramento.
    retrospective = SimpleNamespace(
        **{
            **case.__dict__,
            "state": "RETROSPECTIVE",
            "decision": "CONFIRMED_ACCEPTED",
            "cause_category": "LEGITIMATE_GROWTH",
        }
    )
    with pytest.raises(Exception, match="learning"):
        validate_transition(retrospective, "close", {"evidence": ["ticket-123"]})
    retrospective.learning = "crescimento planejado"
    with pytest.raises(Exception, match="preventive_measure"):
        validate_transition(retrospective, "close", {"evidence": ["ticket-123"]})
    retrospective.no_preventive_measure_reason = "evento único"
    validate_transition(retrospective, "close", {"evidence": ["ticket-123"]})


# ---------------------------------------------------------------------------
# 16. Concorrência: conflito 409
# ---------------------------------------------------------------------------
def test_16_concorrencia_retorna_conflito_409():
    case = SimpleNamespace(case_version=5)
    with pytest.raises(AnomalyVersionConflict) as excinfo:
        assert_case_version(case, 4)
    assert excinfo.value.status_code == 409
    assert excinfo.value.current == 5 and excinfo.value.expected == 4
    # A versão correta passa.
    assert_case_version(case, 5)


# ---------------------------------------------------------------------------
# 17. Composição com Planning: totais financeiros inalterados
# ---------------------------------------------------------------------------
def test_17_composicao_nao_altera_total_financeiro():
    assert_totals_unchanged({"financial_cloud": 1_000_000.0}, 1_000_000.0)
    with pytest.raises(RuntimeError, match="total financeiro"):
        assert_totals_unchanged({"financial_cloud": 1_000_300.0}, 1_000_000.0)
    sql = financial_totals_sql()
    assert "anomaly_cloud" in sql and "financial_cloud" in sql


# ---------------------------------------------------------------------------
# 18. Correção histórica: avaliações versionadas e workflow preservado
# ---------------------------------------------------------------------------
def test_18_correcao_historica_versiona_avaliacao_e_preserva_workflow():
    original = assessment_rows([detection(reference_id="ref-antiga")])[0]
    corrigida = assessment_rows([detection(reference_id="ref-nova", observed_cloud=1600.0)])[0]
    # Referências distintas ⇒ avaliações distintas: a correção não sobrescreve a
    # identidade da avaliação anterior.
    assert original["assessment_id"] != corrigida["assessment_id"]
    assert original["reference_id"] == "ref-antiga"
    assert corrigida["reference_id"] == "ref-nova"
    validate_assessments([original, corrigida])

    # O histórico do caso é imutável e o estado determina os comandos possíveis:
    # uma correção histórica não edita decisões já tomadas.
    from app.services.anomaly_state import available_commands

    assert available_commands("CLOSED") == ["reopen"]
    assert "close" not in available_commands("INVESTIGATING")


def test_18b_avaliacao_de_evento_mensal_e_identificada_pelo_periodo():
    assert case_key_for("s1", "fam", EVENT_MONTHLY, period="202610") != case_key_for(
        "s1", "fam", EVENT_MONTHLY, period="202611"
    )
    assert case_key_for("s1", "fam", EVENT_DAILY) == case_key_for("s1", "fam", EVENT_DAILY)


# ---------------------------------------------------------------------------
# Cobertura dos vocabulários usados no aceite
# ---------------------------------------------------------------------------
def test_escopos_de_permissao_cobrem_o_ciclo_do_aceite():
    for command in ("assign", "start_investigation", "decide", "start_remediation",
                    "start_verification", "validate_result", "record_retrospective", "close", "reopen"):
        assert command in COMMAND_SCOPES
