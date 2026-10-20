"""Testes de distribuição, faixa, perfis e materialidade (E3)."""
from datetime import date, timedelta

import pytest

from app.services.anomaly_calibration import (
    DAILY_GRAIN,
    EXPLICIT_TOLERANCE,
    HISTORICAL_QUANTILE,
    MONTHLY_EVENT_GRAIN,
    UNIFORM_FALLBACK,
    WEEKDAY_MEDIAN,
    build_daily_distribution,
    calibrate_band,
    calibrate_monthly_event_band,
    evaluate_deviation,
    has_complete_daily_calibration,
    is_candidate,
    materiality_for,
    profile_for,
    quantile_min_sample,
    residuals_for_daily_grain,
    residuals_for_monthly_events,
    resolve_parameters,
    scale_band_to_coverage,
    weekday_profile,
)

# ---------------------------------------------------------------------------
# Perfis (plano §2.6)
# ---------------------------------------------------------------------------
def test_tabela_de_perfis_do_plano():
    conservative = profile_for("CONSERVATIVE")
    assert (conservative.daily_quantile, conservative.min_relative_deviation, conservative.investigate_score) == (0.99, 0.30, 65)

    balanced = profile_for("BALANCED")
    assert (balanced.daily_quantile, balanced.min_relative_deviation, balanced.investigate_score) == (0.975, 0.20, 55)

    sensitive = profile_for("SENSITIVE")
    assert (sensitive.daily_quantile, sensitive.min_relative_deviation, sensitive.investigate_score) == (0.95, 0.10, 40)


def test_perfil_desconhecido_e_rejeitado():
    with pytest.raises(ValueError, match="Perfil desconhecido"):
        profile_for("AGRESSIVO")


def test_amostra_minima_do_quantil():
    # ceil(1/alpha) - 1
    assert quantile_min_sample(0.01) == 99
    assert quantile_min_sample(0.025) == 39
    assert quantile_min_sample(0.05) == 19


def test_overrides_explicitos_da_configuracao():
    resolved = resolve_parameters(profile="SENSITIVE", daily_quantile=0.99, investigate_score=70)
    assert resolved.name == "SENSITIVE"
    assert resolved.daily_quantile == 0.99
    assert resolved.investigate_score == 70
    assert resolved.min_relative_deviation == 0.10  # herdado do perfil


# ---------------------------------------------------------------------------
# Materialidade
# ---------------------------------------------------------------------------
def test_materialidade_explicita_por_moeda():
    value, explicit = materiality_for({"BRL": 100.0, "USD": 20.0}, "BRL")
    assert value == 100.0 and explicit is True
    assert materiality_for({"BRL": 100.0}, "brl")[0] == 100.0


def test_materialidade_ausente_e_declarada():
    value, explicit = materiality_for({"BRL": 100.0}, "USD")
    assert value == 100.0
    assert explicit is False
    value, explicit = materiality_for({}, "BRL")
    assert (value, explicit) == (100.0, False)


def test_materialidade_nao_e_inferida_da_moeda_da_linha():
    # A função só olha a tabela explícita; não existe parâmetro de BillingCurrency.
    import inspect

    parameters = set(inspect.signature(materiality_for).parameters)
    assert "billing_currency" not in parameters


# ---------------------------------------------------------------------------
# Distribuição por dia da semana
# ---------------------------------------------------------------------------
def daily_series(as_of: date, weeks: int, *, weekend_factor: float = 0.2, base: float = 100.0):
    """Série sintética: dias úteis valem `base`, fim de semana vale o fator."""
    series: dict[date, float] = {}
    for offset in range(weeks * 7):
        day = as_of - timedelta(days=offset)
        weekend = day.weekday() >= 5
        # ruído determinístico por dia para a mediana ter o que medir
        jitter = ((day.toordinal() % 5) - 2) * 3.0
        series[day] = (base * weekend_factor if weekend else base) + jitter
    return series


def test_mediana_por_dia_da_semana_usa_oito_semanas():
    as_of = date(2026, 10, 12)  # segunda-feira: janela de 8 semanas ISO
    profile = weekday_profile(daily_series(as_of, 8), as_of=as_of, weeks=8)
    assert profile.method == WEEKDAY_MEDIAN
    assert profile.weeks_used == 8
    # Sábado e domingo valem bem menos que segunda a sexta.
    assert profile.medians[5] < profile.medians[0]
    assert profile.medians[6] < profile.medians[2]
    assert profile.medians[0] == pytest.approx(profile.medians[3], abs=8.0)


def test_fallback_uniforme_quando_falta_evidencia_e_identificado():
    as_of = date(2026, 10, 12)
    sparse = {as_of: 100.0, as_of - timedelta(days=1): 90.0}
    profile = weekday_profile(sparse, as_of=as_of, weeks=8)
    assert profile.method == UNIFORM_FALLBACK
    assert profile.is_fallback is True
    assert profile.note
    assert set(profile.medians.values()) == {1.0}


def test_distribuicao_soma_exatamente_o_valor_do_forecast():
    as_of = date(2026, 10, 12)
    profile = weekday_profile(daily_series(as_of, 8), as_of=as_of)
    distribution = build_daily_distribution(
        1_000_000.0,
        coverage_start=date(2026, 10, 13),
        coverage_end=date(2026, 10, 31),
        profile=profile,
    )
    assert distribution.total == pytest.approx(1_000_000.0, abs=1e-6)
    assert len(distribution.expected) == 19
    assert sum(distribution.weights.values()) == pytest.approx(1.0)


def test_distribuicao_normaliza_sobre_todo_o_intervalo_coberto():
    as_of = date(2026, 10, 12)
    profile = weekday_profile(daily_series(as_of, 8), as_of=as_of)
    full = build_daily_distribution(
        1000.0, coverage_start=date(2026, 11, 1), coverage_end=date(2026, 11, 30), profile=profile
    )
    partial = build_daily_distribution(
        1000.0, coverage_start=date(2026, 11, 1), coverage_end=date(2026, 11, 7), profile=profile
    )
    # O mesmo dia tem peso diferente conforme o intervalo coberto (normalização).
    assert full.weights[date(2026, 11, 2)] != partial.weights[date(2026, 11, 2)]
    assert partial.total == pytest.approx(1000.0, abs=1e-6)


def test_fallback_uniforme_na_distribuicao_quando_pesos_zeram():
    as_of = date(2026, 10, 12)
    profile = weekday_profile({}, as_of=as_of)
    distribution = build_daily_distribution(
        70.0, coverage_start=date(2026, 10, 13), coverage_end=date(2026, 10, 19), profile=profile
    )
    assert distribution.method == UNIFORM_FALLBACK
    assert distribution.total == pytest.approx(70.0, abs=1e-6)
    assert set(round(v, 6) for v in distribution.expected.values()) == {10.0}


def test_fim_de_semana_recebe_menos_que_dia_util():
    as_of = date(2026, 10, 12)
    profile = weekday_profile(daily_series(as_of, 8), as_of=as_of)
    distribution = build_daily_distribution(
        10_000.0, coverage_start=date(2026, 10, 13), coverage_end=date(2026, 10, 31), profile=profile
    )
    saturday = next(d for d in distribution.expected if d.weekday() == 5)
    tuesday = next(d for d in distribution.expected if d.weekday() == 1)
    assert distribution.expected[saturday] < distribution.expected[tuesday]


# ---------------------------------------------------------------------------
# Faixa
# ---------------------------------------------------------------------------
def test_quantil_historico_quando_a_amostra_e_suficiente():
    residuals = [0.0] * 90 + [1000.0] * 10
    band = calibrate_band(
        residuals=residuals, expected_cloud=10_000.0, quantile=0.95, tolerance_pct=0.15
    )
    assert band.method == HISTORICAL_QUANTILE
    assert band.sample_size == 100
    assert band.min_sample == 19
    assert band.sample_sufficient is True
    assert band.upper_offset > 0
    assert band.confidence in ("MEDIUM", "HIGH")
    assert band.is_calibrated is True


def test_tolerancia_explicita_quando_a_amostra_e_insuficiente():
    band = calibrate_band(
        residuals=[10.0, 20.0], expected_cloud=10_000.0, quantile=0.99, tolerance_pct=0.15
    )
    assert band.method == EXPLICIT_TOLERANCE
    assert band.min_sample == 99
    assert band.sample_sufficient is False
    assert band.confidence == "LOW"
    assert band.upper_offset == pytest.approx(1500.0)
    assert "não é intervalo estatístico calibrado" in band.note


def test_sem_amostra_nenhuma_a_confianca_e_indisponivel():
    band = calibrate_band(residuals=[], expected_cloud=500.0, quantile=0.975, tolerance_pct=0.15)
    assert band.method == EXPLICIT_TOLERANCE
    assert band.confidence == "UNAVAILABLE"


def test_faixa_unilateral_de_aumento_nunca_e_negativa():
    band = calibrate_band(
        residuals=[-500.0] * 50, expected_cloud=10_000.0, quantile=0.95, tolerance_pct=0.15
    )
    assert band.method == HISTORICAL_QUANTILE
    assert band.upper_offset == 0.0
    assert band.lower_offset > 0


def test_evento_mensal_com_poucos_eventos_usa_tolerancia_de_valor():
    band = calibrate_monthly_event_band(
        residuals=[100.0, 200.0], expected_cloud=30_000.0, tolerance_pct=0.15, monthly_min_events=3
    )
    assert band.method == EXPLICIT_TOLERANCE
    assert band.upper_offset == pytest.approx(4_500.0)
    assert band.confidence == "LOW"
    assert band.granularity == MONTHLY_EVENT_GRAIN
    assert "confiança limitada" in band.note


def test_evento_mensal_calibrado_nao_promete_confianca_alta():
    band = calibrate_monthly_event_band(
        residuals=[100.0] * 40, expected_cloud=30_000.0, monthly_min_events=3
    )
    assert band.method == HISTORICAL_QUANTILE
    assert band.confidence == "MEDIUM"  # teto para histórico mensal curto


def test_faixa_escalada_para_o_acumulado_do_mes():
    band = calibrate_band(
        residuals=[100.0] * 100, expected_cloud=1000.0, quantile=0.95, tolerance_pct=0.15
    )
    scaled = scale_band_to_coverage(band, covered_days=19)
    assert scaled.upper_offset == pytest.approx(band.upper_offset * 19)
    assert scaled.granularity == DAILY_GRAIN


# ---------------------------------------------------------------------------
# Desvio x custo anômalo (plano §2.5)
# ---------------------------------------------------------------------------
def test_desvio_e_custo_anomalo_sao_medidas_distintas():
    band = calibrate_band(
        residuals=[100.0] * 100, expected_cloud=1000.0, quantile=0.95, tolerance_pct=0.15
    )
    # Dentro da faixa: desvio existe, custo anômalo é zero.
    inside = evaluate_deviation(1050.0, expected_cloud=1000.0, band=band)
    assert inside.delta_cloud == 50.0
    assert inside.anomalous_cost_cloud == 0.0
    assert inside.above_band is False

    # Acima da faixa: custo anômalo é só o excedente.
    outside = evaluate_deviation(1400.0, expected_cloud=1000.0, band=band)
    assert outside.delta_cloud == 400.0
    assert outside.anomalous_cost_cloud == pytest.approx(400.0 - band.upper_offset)
    assert outside.above_band is True


def test_queda_de_custo_nao_gera_custo_anomalo():
    band = calibrate_band(
        residuals=[100.0] * 100, expected_cloud=1000.0, quantile=0.95, tolerance_pct=0.15
    )
    deviation = evaluate_deviation(100.0, expected_cloud=1000.0, band=band)
    assert deviation.delta_cloud == -900.0
    assert deviation.anomalous_cost_cloud == 0.0
    assert deviation.is_increase is False


def test_materialidade_e_desvio_relativo_minimo_filtram_candidato():
    band = calibrate_band(
        residuals=[10.0] * 100, expected_cloud=100.0, quantile=0.95, tolerance_pct=0.15
    )
    deviation = evaluate_deviation(115.0, expected_cloud=100.0, band=band)
    ok, reasons = is_candidate(deviation, min_relative_deviation=0.20, materiality=1.0)
    assert ok is False
    assert "BELOW_MIN_RELATIVE_DEVIATION" in reasons

    ok, reasons = is_candidate(deviation, min_relative_deviation=0.10, materiality=1.0)
    assert ok is True and reasons == []


def test_materialidade_bloqueia_anomalia_irrelevante():
    band = calibrate_band(
        residuals=[1.0] * 100, expected_cloud=100.0, quantile=0.95, tolerance_pct=0.01
    )
    deviation = evaluate_deviation(103.0, expected_cloud=100.0, band=band)
    ok, reasons = is_candidate(deviation, min_relative_deviation=0.0, materiality=100.0)
    assert ok is False
    assert "BELOW_MATERIALITY" in reasons


def test_dentro_da_faixa_nao_e_candidato():
    band = calibrate_band(
        residuals=[50.0] * 100, expected_cloud=1000.0, quantile=0.95, tolerance_pct=0.15
    )
    deviation = evaluate_deviation(1010.0, expected_cloud=1000.0, band=band)
    ok, reasons = is_candidate(deviation, min_relative_deviation=0.0, materiality=0.0)
    assert ok is False and reasons == ["WITHIN_BAND"]


# ---------------------------------------------------------------------------
# Resíduos por granularidade e janela de calibração
# ---------------------------------------------------------------------------
def test_residuos_diarios_e_mensais_sao_separados():
    observed = {date(2026, 10, 1): 100.0, date(2026, 10, 2): 200.0}
    expected = {date(2026, 10, 1): 90.0, date(2026, 10, 2): 150.0}
    assert residuals_for_daily_grain(observed, expected) == [10.0, 50.0]

    events = [
        {"observed_cloud": 30_000.0, "expected_cloud": 30_000.0},
        {"observed_cloud": 60_000.0, "expected_cloud": 30_000.0},
    ]
    assert residuals_for_monthly_events(events) == [0.0, 30_000.0]


def test_calibracao_diaria_exige_sessenta_dias_validos():
    as_of = date(2026, 10, 12)
    complete = {as_of - timedelta(days=i): 10.0 for i in range(60)}
    assert has_complete_daily_calibration(complete, as_of=as_of, calibration_days=60) is True

    incomplete = {as_of - timedelta(days=i): 10.0 for i in range(30)}
    assert has_complete_daily_calibration(incomplete, as_of=as_of, calibration_days=60) is False
