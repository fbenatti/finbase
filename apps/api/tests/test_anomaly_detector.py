"""Testes dos detectores e da consolidação de episódios (E4)."""
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.services.anomaly_calibration import (
    calibrate_band,
    calibrate_monthly_event_band,
    profile_for,
)
from app.services.anomaly_detector import (
    ASSESSMENT_BELOW_PROFILE,
    ASSESSMENT_CANDIDATE,
    ASSESSMENT_DATA_INSUFFICIENT,
    ASSESSMENT_EXPECTED,
    ASSESSMENT_OPEN,
    ASSESSMENT_RULE_MISMATCH,
    CREATE,
    EVENT_DAILY,
    EVENT_MONTHLY,
    EVENT_MONTHLY_ACCUMULATED,
    EXPECTED_ONLY,
    IGNORE,
    LINK_NEW_EPISODE,
    MIN_COMPARABLE_EVENTS_FOR_SHIFT,
    NO_WORKFLOW,
    RC_CALENDAR_SHIFT,
    RC_EXPECTED_MONTHLY_EVENT,
    RC_FEW_MONTHLY_EVENTS,
    RC_FORECAST_RULE_MISMATCH,
    RC_PARTIAL_DATA,
    RECOVERY_VALID_DAYS,
    REOPEN,
    UPDATE,
    MonthlyEvent,
    SeriesPoint,
    calendar_shift_days,
    case_key_for,
    consolidate_family,
    consolidation_policy,
    detect_daily_consumption,
    detect_monthly_accumulated,
    detect_monthly_event,
    expected_monthly_event,
    resolve_episode,
)

DIMS = {"cloud_origem": "azure-dev", "servicename": "Virtual Machines"}
PROFILE = profile_for("BALANCED")
MATERIALITY = 100.0


def band_offset(offset: float = 200.0, expected: float = 1000.0):
    """Faixa calibrada com offset conhecido."""
    return calibrate_band(
        residuals=[offset] * 100, expected_cloud=expected, quantile=0.975, tolerance_pct=0.15
    )


def point(
    day: date,
    *,
    observed: float = 1500.0,
    expected: float = 1000.0,
    upper: float = 1200.0,
    partial: bool = False,
    dimensions: dict | None = None,
) -> SeriesPoint:
    dims = dict(DIMS)
    dims.update(dimensions or {})
    return SeriesPoint(
        series_id="s1",
        dimensions=dims,
        event_date=day,
        observed_cloud=observed,
        expected_cloud=expected,
        upper_cloud=upper,
        lower_cloud=expected - (upper - expected),
        reference_id="ref-1",
        data_partial=partial,
    )


def monthly_event(
    day: date,
    *,
    observed: float = 40_000.0,
    expected: float = 30_000.0,
    upper: float = 34_500.0,
    partial: bool = False,
    pre_event: bool = True,
    mismatch: bool = False,
    rules: list | None = None,
) -> MonthlyEvent:
    return MonthlyEvent(
        series_id="s1",
        dimensions=dict(DIMS),
        event_date=day,
        observed_cloud=observed,
        expected_cloud=expected,
        upper_cloud=upper,
        lower_cloud=expected - (upper - expected),
        reference_id="ref-m",
        data_partial=partial,
        pre_event_reference=pre_event,
        matched_rule_refs=rules if rules is not None else [{"name": "Purchase recorrente", "status": "MATCHED_AND_APPLIED"}],
        rule_mismatch=mismatch,
    )


# ---------------------------------------------------------------------------
# Detector diário
# ---------------------------------------------------------------------------
def test_tres_dias_anomalos_consecutivos_sao_um_unico_episodio():
    start = date(2026, 10, 5)
    points = [point(start + timedelta(days=i)) for i in range(3)]
    detection = detect_daily_consumption(
        points, profile=PROFILE, materiality=MATERIALITY, band=band_offset(),
        event_family="azure-dev|Virtual Machines",
    )
    assert detection is not None
    assert detection.event_type == EVENT_DAILY
    assert detection.consecutive_occurrences == 3
    assert detection.observed_cloud == pytest.approx(4500.0)
    assert detection.expected_cloud == pytest.approx(3000.0)
    assert detection.upper_cloud == pytest.approx(3600.0)
    assert detection.anomalous_cost_cloud == pytest.approx(900.0)  # 3 × 300
    assert len(detection.contributors) == 3
    assert detection.assessment_status == ASSESSMENT_OPEN


def test_gap_separa_episodios_e_o_maior_vence():
    start = date(2026, 10, 5)
    points = [
        point(start),
        point(start + timedelta(days=1)),
        point(start + timedelta(days=2)),
        # gap de dois dias
        point(start + timedelta(days=5)),
    ]
    detection = detect_daily_consumption(
        points, profile=PROFILE, materiality=MATERIALITY, band=band_offset(),
        event_family="f",
    )
    assert detection.consecutive_occurrences == 3
    assert detection.anomalous_cost_cloud == pytest.approx(900.0)


def test_excesso_persistente_pertence_ao_mesmo_caso():
    start = date(2026, 10, 5)
    first = detect_daily_consumption(
        [point(start), point(start + timedelta(days=1))],
        profile=PROFILE, materiality=MATERIALITY, band=band_offset(), event_family="f",
    )
    second = detect_daily_consumption(
        [point(start), point(start + timedelta(days=1)), point(start + timedelta(days=2))],
        profile=PROFILE, materiality=MATERIALITY, band=band_offset(), event_family="f",
    )
    assert first.case_key == second.case_key
    assert second.anomalous_cost_cloud > first.anomalous_cost_cloud


def test_dia_dentro_da_faixa_nao_e_candidato():
    detection = detect_daily_consumption(
        [point(date(2026, 10, 5), observed=1100.0)],
        profile=PROFILE, materiality=MATERIALITY, band=band_offset(), event_family="f",
    )
    assert detection.assessment_status == ASSESSMENT_BELOW_PROFILE
    assert "WITHIN_BAND" in detection.reason_codes


def test_below_profile_nao_abre_workflow():
    detection = detect_daily_consumption(
        [point(date(2026, 10, 5), observed=1100.0)],
        profile=PROFILE, materiality=MATERIALITY, band=band_offset(), event_family="f",
    )
    decision = resolve_episode(detection=detection)
    assert decision.action == NO_WORKFLOW


def test_dado_parcial_gera_candidato_provisorio():
    detection = detect_daily_consumption(
        [point(date(2026, 10, 5), partial=True)],
        profile=PROFILE, materiality=MATERIALITY, band=band_offset(), event_family="f",
    )
    assert detection.assessment_status == ASSESSMENT_CANDIDATE
    assert RC_PARTIAL_DATA in detection.reason_codes
    assert detection.confidence == "LOW"


def test_faixa_nao_calibrada_e_declarada():
    fallback = calibrate_band(residuals=[10.0], expected_cloud=1000.0, quantile=0.975, tolerance_pct=0.15)
    detection = detect_daily_consumption(
        [point(date(2026, 10, 5))],
        profile=PROFILE, materiality=MATERIALITY, band=fallback, event_family="f",
    )
    assert "EXPLICIT_TOLERANCE" in detection.reason_codes
    assert detection.components["score"] >= 0


# ---------------------------------------------------------------------------
# Detector de evento mensal
# ---------------------------------------------------------------------------
def test_compra_esperada_nao_abre_caso():
    event = monthly_event(date(2026, 10, 5), observed=30_000.0, expected=30_000.0, upper=34_500.0)
    detection = detect_monthly_event(
        event, profile=PROFILE, materiality=MATERIALITY, band=None,
        event_family="azure-dev|SavingsPlan",
    )
    assert detection.assessment_status == ASSESSMENT_EXPECTED
    assert detection.expected_only is True
    assert RC_EXPECTED_MONTHLY_EVENT in detection.reason_codes
    assert detection.anomalous_cost_cloud == 0.0

    decision = resolve_episode(detection=detection)
    assert decision.action == EXPECTED_ONLY
    assert decision.anomalous_cost_cloud == 0.0


def test_compra_adicional_gera_caso_com_excesso():
    event = monthly_event(date(2026, 10, 5), observed=40_000.0, expected=30_000.0, upper=34_500.0)
    detection = detect_monthly_event(
        event, profile=PROFILE, materiality=MATERIALITY, band=None,
        event_family="azure-dev|SavingsPlan",
    )
    assert detection.assessment_status == ASSESSMENT_OPEN
    assert detection.anomalous_cost_cloud == pytest.approx(5_500.0)
    assert resolve_episode(detection=detection).action == CREATE


def test_regra_incompativel_produz_mismatch_sem_caso():
    event = monthly_event(date(2026, 10, 5), mismatch=True)
    detection = detect_monthly_event(
        event, profile=PROFILE, materiality=MATERIALITY, band=None, event_family="f",
    )
    assert detection.assessment_status == ASSESSMENT_RULE_MISMATCH
    assert RC_FORECAST_RULE_MISMATCH in detection.reason_codes
    assert resolve_episode(detection=detection).action == NO_WORKFLOW


def test_sem_referencia_pre_compra_nao_abre_caso():
    event = monthly_event(date(2026, 10, 5), pre_event=False)
    detection = detect_monthly_event(
        event, profile=PROFILE, materiality=MATERIALITY, band=None, event_family="f",
    )
    assert detection.assessment_status == ASSESSMENT_DATA_INSUFFICIENT
    assert resolve_episode(detection=detection).action == NO_WORKFLOW


def test_poucos_eventos_mensais_marcam_confianca_limitada():
    band = calibrate_monthly_event_band(
        residuals=[100.0, 200.0], expected_cloud=30_000.0, tolerance_pct=0.15
    )
    event = monthly_event(date(2026, 10, 5))
    detection = detect_monthly_event(
        event, profile=PROFILE, materiality=MATERIALITY, band=band, event_family="f",
        previous_events=[],
    )
    assert RC_FEW_MONTHLY_EVENTS in detection.reason_codes
    assert detection.confidence == "LOW"


def test_compra_mensal_nao_exige_conhecimento_do_dia_exato():
    # Uma regra mensal não implica dia exato: sem 3 eventos comparáveis, o
    # detector se abstém de julgar calendário.
    shift, evidence = calendar_shift_days([date(2026, 8, 5), date(2026, 9, 5)], date(2026, 10, 20))
    assert evidence is False and shift is None

    shift, evidence = calendar_shift_days(
        [date(2026, 6, 5), date(2026, 7, 5), date(2026, 8, 5), date(2026, 9, 5)],
        date(2026, 10, 20),
    )
    assert evidence is True and shift is not None

    # Dentro da tolerância, sem evidência de deslocamento.
    _, evidence = calendar_shift_days(
        [date(2026, 6, 5), date(2026, 7, 5), date(2026, 8, 5)], date(2026, 10, 7)
    )
    assert evidence is False
    assert MIN_COMPARABLE_EVENTS_FOR_SHIFT == 3


def test_evidencia_de_deslocamento_aparece_nos_motivos():
    previous = [
        monthly_event(date(2026, 6, 5), observed=30_000.0, expected=30_000.0),
        monthly_event(date(2026, 7, 5), observed=30_000.0, expected=30_000.0),
        monthly_event(date(2026, 8, 5), observed=30_000.0, expected=30_000.0),
    ]
    event = monthly_event(date(2026, 10, 25))
    detection = detect_monthly_event(
        event, profile=PROFILE, materiality=MATERIALITY, band=None, event_family="f",
        previous_events=previous,
    )
    assert RC_CALENDAR_SHIFT in detection.reason_codes
    assert detection.components["calendar_shift_days"] is not None


def test_evento_esperado_por_tolerancia_de_valor():
    assert expected_monthly_event(observed_cloud=30_000.0, expected_cloud=30_000.0, upper_cloud=34_500.0)
    assert not expected_monthly_event(observed_cloud=40_000.0, expected_cloud=30_000.0, upper_cloud=34_500.0)


# ---------------------------------------------------------------------------
# Excesso mensal acumulado
# ---------------------------------------------------------------------------
def test_duas_compras_no_mes_consolidam_o_excesso_sem_somar_duas_vezes():
    first = monthly_event(date(2026, 10, 5), observed=30_000.0, expected=30_000.0, upper=34_500.0)
    second = monthly_event(date(2026, 10, 20), observed=40_000.0, expected=30_000.0, upper=34_500.0)
    detection = detect_monthly_accumulated(
        series_id="s1", dimensions=DIMS, event_family="azure-dev|SavingsPlan", period="202610",
        events=[first, second], profile=PROFILE, materiality=MATERIALITY, band=None,
    )
    assert detection.event_type == EVENT_MONTHLY_ACCUMULATED
    assert detection.observed_cloud == pytest.approx(70_000.0)
    assert detection.expected_cloud == pytest.approx(60_000.0)
    assert detection.upper_cloud == pytest.approx(69_000.0)
    # Excesso do acumulado (1.000), não a soma dos excessos individuais (5.500).
    assert detection.anomalous_cost_cloud == pytest.approx(1_000.0)
    assert len(detection.contributors) == 2


def test_acumulado_sem_excesso_e_evento_esperado():
    first = monthly_event(date(2026, 10, 5), observed=30_000.0, expected=30_000.0, upper=34_500.0)
    second = monthly_event(date(2026, 10, 20), observed=30_000.0, expected=30_000.0, upper=34_500.0)
    detection = detect_monthly_accumulated(
        series_id="s1", dimensions=DIMS, event_family="f", period="202610",
        events=[first, second], profile=PROFILE, materiality=MATERIALITY, band=None,
    )
    assert detection.assessment_status == ASSESSMENT_EXPECTED
    assert detection.anomalous_cost_cloud == 0.0
    assert resolve_episode(detection=detection).action == EXPECTED_ONLY


# ---------------------------------------------------------------------------
# Contribuintes
# ---------------------------------------------------------------------------
def test_contribuintes_nao_duplicam_impacto():
    start = date(2026, 10, 5)
    # Pai acima da faixa; filho dentro da faixa (folga absorve parte do excesso).
    parent = detect_daily_consumption(
        [point(start, observed=2000.0)], profile=PROFILE, materiality=MATERIALITY,
        band=band_offset(), event_family="f",
    )
    child = detect_daily_consumption(
        [point(start, observed=1100.0, dimensions={"servicename": "Storage"})],
        profile=PROFILE, materiality=MATERIALITY, band=band_offset(), event_family="f",
    )
    assert parent.anomalous_cost_cloud == pytest.approx(800.0)
    assert child.anomalous_cost_cloud == 0.0

    consolidated = consolidate_family([parent, child])
    # O consolidado é o excesso no nível da família (700), nunca o gasto total
    # nem a soma pai+filho tratados como gastos distintos.
    assert consolidated.observed_cloud == pytest.approx(3100.0)
    assert consolidated.upper_cloud == pytest.approx(2400.0)
    assert consolidated.anomalous_cost_cloud == pytest.approx(700.0)
    assert consolidated.anomalous_cost_cloud <= (
        parent.anomalous_cost_cloud + child.anomalous_cost_cloud
    )
    assert consolidated.anomalous_cost_cloud != consolidated.observed_cloud
    assert len(consolidated.contributors) == 2


def test_consolidacao_de_uma_unica_deteccao_e_identidade():
    detection = detect_daily_consumption(
        [point(date(2026, 10, 5))], profile=PROFILE, materiality=MATERIALITY,
        band=band_offset(), event_family="f",
    )
    assert consolidate_family([detection]) is detection
    assert consolidate_family([]) is None


def test_politica_de_consolidacao_declarada():
    policy = consolidation_policy()
    assert "episodio" in policy["case_per"]
    assert "3 dias" in policy["group_while"]
    assert "max" in policy["impact_rule"]


# ---------------------------------------------------------------------------
# Episódios
# ---------------------------------------------------------------------------
def detection_for_episode(*, anomalous: float = 900.0):
    detection = detect_daily_consumption(
        [point(date(2026, 10, 5))], profile=PROFILE, materiality=MATERIALITY,
        band=band_offset(), event_family="f",
    )
    detection.anomalous_cost_cloud = anomalous
    return detection


def test_reprocessamento_nao_duplica_impacto():
    detection = detection_for_episode(anomalous=900.0)
    existing = SimpleNamespace(id=7, state="INVESTIGATING", anomalous_cost_cloud=1500.0)
    decision = resolve_episode(detection=detection, existing_case=existing)
    assert decision.action == UPDATE
    assert decision.anomalous_cost_cloud == 1500.0  # max, nunca 1500 + 900


def test_reprocessamento_atualiza_quando_o_novo_impacto_e_maior():
    detection = detection_for_episode(anomalous=2400.0)
    existing = SimpleNamespace(id=7, state="DECIDED", anomalous_cost_cloud=1500.0)
    decision = resolve_episode(detection=detection, existing_case=existing)
    assert decision.action == UPDATE
    assert decision.anomalous_cost_cloud == 2400.0


def test_caso_encerrado_nao_reabre_por_repeticao_tecnica():
    detection = detection_for_episode()
    existing = SimpleNamespace(id=7, state="CLOSED", anomalous_cost_cloud=900.0)
    decision = resolve_episode(detection=detection, existing_case=existing, valid_recovery_days=0)
    assert decision.action == IGNORE
    assert decision.anomalous_cost_cloud == 900.0
    assert "repetição" in decision.notes[0]


def test_novo_episodio_apos_recuperacao_vira_caso_vinculado():
    detection = detection_for_episode(anomalous=1200.0)
    existing = SimpleNamespace(id=7, state="CLOSED", anomalous_cost_cloud=900.0)
    decision = resolve_episode(
        detection=detection, existing_case=existing, valid_recovery_days=RECOVERY_VALID_DAYS
    )
    assert decision.action == LINK_NEW_EPISODE
    assert decision.parent_case_id == 7
    assert decision.anomalous_cost_cloud == 1200.0


def test_continuacao_do_mesmo_episodio_reabre_com_evento_auditado():
    detection = detection_for_episode(anomalous=900.0)
    existing = SimpleNamespace(id=7, state="CLOSED", anomalous_cost_cloud=1500.0)
    decision = resolve_episode(detection=detection, existing_case=existing, continuation=True)
    assert decision.action == REOPEN
    assert decision.anomalous_cost_cloud == 1500.0
    assert "auditado" in decision.notes[0]


def test_sem_caso_existente_cria():
    decision = resolve_episode(detection=detection_for_episode())
    assert decision.action == CREATE
    assert decision.anomalous_cost_cloud == 900.0


def test_case_key_mensal_inclui_o_periodo_esperado():
    a = case_key_for("s1", "fam", EVENT_MONTHLY, period="202610")
    b = case_key_for("s1", "fam", EVENT_MONTHLY, period="202611")
    c = case_key_for("s1", "fam", EVENT_MONTHLY, period="202610")
    assert a != b
    assert a == c
    # Diário não inclui período: o episódio atravessa dias.
    assert case_key_for("s1", "fam", EVENT_DAILY) == case_key_for("s1", "fam", EVENT_DAILY)
