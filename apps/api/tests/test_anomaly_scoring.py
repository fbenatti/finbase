"""Testes de nota, severidade e confiança (E4, plano §2.7)."""
import pytest

from app.services.anomaly_scoring import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_UNAVAILABLE,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    WEIGHT_IMPACT,
    WEIGHT_INTENSITY,
    WEIGHT_PERSISTENCE,
    confidence_from,
    default_sla_hours,
    impact,
    intensity,
    persistence,
    score_detection,
    score_from_components,
    severity_for,
    severity_increased,
)


def test_pesos_do_plano_somam_um():
    assert WEIGHT_INTENSITY == 0.55
    assert WEIGHT_IMPACT == 0.30
    assert WEIGHT_PERSISTENCE == 0.15
    assert WEIGHT_INTENSITY + WEIGHT_IMPACT + WEIGHT_PERSISTENCE == pytest.approx(1.0)


def test_nota_e_combinacao_linear_dos_componentes():
    assert score_from_components(intensity_value=100, impact_value=100, persistence_value=100) == 100.0
    assert score_from_components(intensity_value=0, impact_value=0, persistence_value=0) == 0.0
    # Só intensidade: 0,55 × 100
    assert score_from_components(intensity_value=100, impact_value=0, persistence_value=0) == 55.0
    # Só impacto: 0,30 × 100
    assert score_from_components(intensity_value=0, impact_value=100, persistence_value=0) == 30.0
    # Só persistência: 0,15 × 100
    assert score_from_components(intensity_value=0, impact_value=0, persistence_value=100) == 15.0


# ---------------------------------------------------------------------------
# Intensidade
# ---------------------------------------------------------------------------
def test_intensidade_satura_em_ratio_dois():
    value, ratio = intensity(400.0, expected_cloud=1000.0, upper_cloud=1200.0)
    assert ratio == pytest.approx(2.0)
    assert value == 100.0

    value, ratio = intensity(800.0, expected_cloud=1000.0, upper_cloud=1200.0)
    assert ratio == pytest.approx(4.0)
    assert value == 100.0  # clamp


def test_intensidade_linear_abaixo_da_saturacao():
    value, ratio = intensity(100.0, expected_cloud=1000.0, upper_cloud=1200.0)
    assert ratio == pytest.approx(0.5)
    assert value == pytest.approx(25.0)


def test_intensidade_zero_para_desvio_negativo():
    value, ratio = intensity(-500.0, expected_cloud=1000.0, upper_cloud=1200.0)
    assert value == 0.0 and ratio == 0.0


def test_piso_numerico_quando_a_faixa_e_zero():
    # Limite superior igual ao esperado: sem piso, ratio seria infinito.
    value, ratio = intensity(50.0, expected_cloud=0.0, upper_cloud=0.0)
    assert ratio == pytest.approx(50.0)  # piso absoluto = 1
    assert value == 100.0


def test_piso_relativo_evita_intensidade_artificial_em_serie_grande():
    value, ratio = intensity(1.0, expected_cloud=1_000_000.0, upper_cloud=1_000_000.0)
    # piso relativo de 1% → 10.000
    assert ratio == pytest.approx(0.0001)
    assert value == pytest.approx(0.005)


# ---------------------------------------------------------------------------
# Impacto
# ---------------------------------------------------------------------------
def test_impacto_logaritmico():
    assert impact(0.0, materiality=100.0) == 0.0
    assert impact(100.0, materiality=100.0) == pytest.approx(25.0)     # log2(2)
    assert impact(300.0, materiality=100.0) == pytest.approx(50.0)     # log2(4)
    assert impact(700.0, materiality=100.0) == pytest.approx(75.0)     # log2(8)


def test_impacto_satura_em_cem():
    assert impact(100_000_000.0, materiality=100.0) == 100.0


def test_impacto_com_materialidade_zero_nao_quebra():
    assert impact(100.0, materiality=0.0) > 0


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------
def test_persistencia_satura_em_tres_dias():
    assert persistence(1) == pytest.approx(100 / 3)
    assert persistence(2) == pytest.approx(200 / 3)
    assert persistence(3) == 100.0
    assert persistence(10) == 100.0


def test_persistencia_minima_de_uma_ocorrencia():
    assert persistence(0) == pytest.approx(100 / 3)


# ---------------------------------------------------------------------------
# Severidade
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "score,expected",
    [
        (0, SEVERITY_LOW),
        (59.9, SEVERITY_LOW),
        (60, SEVERITY_MEDIUM),
        (79.9, SEVERITY_MEDIUM),
        (80, SEVERITY_HIGH),
        (89.9, SEVERITY_HIGH),
        (90, SEVERITY_CRITICAL),
        (100, SEVERITY_CRITICAL),
    ],
)
def test_faixas_de_severidade_do_plano(score, expected):
    assert severity_for(score) == expected


def test_aumento_de_severidade_dispara_notificacao():
    assert severity_increased(SEVERITY_LOW, SEVERITY_MEDIUM) is True
    assert severity_increased(SEVERITY_MEDIUM, SEVERITY_MEDIUM) is False
    assert severity_increased(None, SEVERITY_LOW) is False
    assert severity_increased(SEVERITY_HIGH, SEVERITY_CRITICAL) is True


def test_nota_completa_e_auditavel():
    breakdown = score_detection(
        delta_cloud=600.0,
        anomalous_cost_cloud=400.0,
        expected_cloud=1000.0,
        upper_cloud=1200.0,
        materiality=100.0,
        consecutive_occurrences=3,
    )
    assert breakdown.ratio == pytest.approx(3.0)
    assert breakdown.intensity == 100.0
    # 25 × log2(1 + 400/100) = 25 × log2(5)
    assert breakdown.impact == pytest.approx(25.0 * 2.321928094887362)
    assert breakdown.persistence == 100.0
    assert breakdown.score == pytest.approx(0.55 * 100 + 0.30 * breakdown.impact + 0.15 * 100, abs=0.5)
    assert breakdown.severity == severity_for(breakdown.score)
    payload = breakdown.as_dict()
    for key in ("score", "severity", "intensity", "impact", "persistence", "ratio"):
        assert key in payload


# ---------------------------------------------------------------------------
# Confiança (separada da nota)
# ---------------------------------------------------------------------------
def test_confianca_alta_exige_faixa_calibrada_e_janela_completa():
    assert (
        confidence_from(
            band_calibrated=True,
            band_confidence=CONFIDENCE_HIGH,
            complete_calibration=True,
            data_partial=False,
            reference_kind="PROSPECTIVE",
        )
        == CONFIDENCE_HIGH
    )


def test_confianca_media_com_janela_incompleta():
    assert (
        confidence_from(
            band_calibrated=True,
            band_confidence=CONFIDENCE_HIGH,
            complete_calibration=False,
            data_partial=False,
            reference_kind="PROSPECTIVE",
        )
        == CONFIDENCE_LOW
    )
    assert (
        confidence_from(
            band_calibrated=True,
            band_confidence=CONFIDENCE_MEDIUM,
            complete_calibration=True,
            data_partial=False,
            reference_kind="PROSPECTIVE",
        )
        == CONFIDENCE_MEDIUM
    )


def test_dados_parciais_rebaixam_a_confianca():
    assert (
        confidence_from(
            band_calibrated=True,
            band_confidence=CONFIDENCE_HIGH,
            complete_calibration=True,
            data_partial=True,
            reference_kind="PROSPECTIVE",
        )
        == CONFIDENCE_LOW
    )


def test_referencia_indisponivel_torna_a_confianca_indisponivel():
    assert (
        confidence_from(
            band_calibrated=True,
            band_confidence=CONFIDENCE_HIGH,
            complete_calibration=True,
            data_partial=False,
            reference_kind="UNAVAILABLE",
        )
        == CONFIDENCE_UNAVAILABLE
    )


def test_faixa_nao_calibrada_limita_a_confianca():
    assert (
        confidence_from(
            band_calibrated=False,
            band_confidence=CONFIDENCE_LOW,
            complete_calibration=True,
            data_partial=False,
            reference_kind="PROSPECTIVE",
        )
        == CONFIDENCE_LOW
    )


def test_poucos_eventos_mensais_nao_prometem_confianca_alta():
    assert (
        confidence_from(
            band_calibrated=True,
            band_confidence=CONFIDENCE_HIGH,
            complete_calibration=True,
            data_partial=False,
            reference_kind="PROSPECTIVE",
            few_monthly_events=True,
        )
        == CONFIDENCE_LOW
    )


# ---------------------------------------------------------------------------
# Prazos
# ---------------------------------------------------------------------------
def test_prazos_iniciais_do_plano():
    assert default_sla_hours(SEVERITY_CRITICAL) == 4
    assert default_sla_hours(SEVERITY_HIGH) == 24
    assert default_sla_hours(SEVERITY_MEDIUM) == 72
    assert default_sla_hours(SEVERITY_LOW) == 72


def test_prazos_configuraveis():
    table = {"CRITICAL": 1, "HIGH": 8, "DEFAULT": 48}
    assert default_sla_hours(SEVERITY_CRITICAL, table) == 1
    assert default_sla_hours(SEVERITY_HIGH, table) == 8
    assert default_sla_hours(SEVERITY_LOW, table) == 48
