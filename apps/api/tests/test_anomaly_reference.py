"""Testes do adaptador de snapshot e da referência (E2).

Herméticos: nenhuma chamada a Trino ou PostgreSQL. O contrato é montado a
partir de linhas sintéticas de `forecast_run`.
"""
from datetime import date, datetime, timezone

import pytest

from app.services.anomaly_forecast_adapter import (
    APPLIED,
    DEFAULT_MEASURE,
    OUT_OF_DIMENSIONS,
    SERIES_NOT_FOUND,
    contract_from_run_row,
    parse_applied_rule_refs,
)
from app.services.anomaly_reference import (
    ELAPSED_MONTH,
    FULL_MONTH,
    REMAINING_MONTH,
    ReferenceUnavailable,
    build_reference_rows,
    classify_coverage,
    horizon_months,
    month_of,
    reference_id_for,
    select_pre_event_reference,
    select_reference,
    series_id_for,
    zero_remaining_is_legitimate,
)

DIMS = ["cloud_origem", "servicename"]


def run_row(
    *,
    run_id=399,
    result_table="result_run_399",
    status="COMPLETED",
    cutoff="2026-10-12",
    finished="2026-10-14T01:16:59-03:00",
    trigger="SCHEDULED_REPROCESS",
    config_version=21,
    snapshot=None,
    error_metrics=None,
    columns=("realizado_cloud", "forecast_value", "forecast_month"),
):
    return dict(
        id=run_id,
        result_table=result_table,
        status=status,
        trigger_type=trigger,
        forecast_config_id=9,
        config_version=config_version,
        cutoff_date=cutoff,
        finished_at=finished,
        started_at=finished,
        destination_physical_name="dest_modelo_focus",
        source_snapshot_id=None,
        config_snapshot=snapshot if snapshot is not None else {
            "destination_physical_name": "dest_modelo_focus",
            "measure_column": "realizado",
            "dimensions": DIMS,
        },
        error_metrics=error_metrics if error_metrics is not None else {},
        _columns=columns,
    )


DEF_ANOMALY_CONFIG = {
    "measure_column": "realizado_cloud",
    "source_namespace": "silver",
    "dimensions": DIMS,
}


def contract(**kwargs):
    columns = kwargs.pop("columns", ("realizado_cloud", "forecast_value", "forecast_month"))
    fallback = kwargs.pop("fallback", DEF_ANOMALY_CONFIG)
    row = run_row(columns=columns, **kwargs)
    row.pop("_columns")
    return contract_from_run_row(row, available_columns=columns, fallback=fallback)


# ---------------------------------------------------------------------------
# Contrato
# ---------------------------------------------------------------------------
def test_medida_da_referencia_e_a_da_configuracao_de_anomalia():
    """O forecast projeta em `realizado`; a referência lê `realizado_cloud`."""
    built = contract()
    assert built.measure_column == "realizado_cloud"
    assert built.has_measure is True
    assert built.is_usable is True
    assert any("dupla medida financeira" in w for w in built.warnings)


def test_medida_ausente_torna_a_referencia_indisponivel():
    built = contract_from_run_row(
        run_row(),
        available_columns=("forecast_value", "forecast_month"),
        fallback={"measure_column": "realizado_cloud"},
    )
    assert built.has_measure is False
    assert built.is_usable is False
    assert any("não substituir a medida" in w for w in built.warnings)


def test_snapshot_sem_source_namespace_e_declarado():
    built = contract()
    assert built.contract_source == "RUN_COLUMNS"
    assert built.source_namespace == "silver"
    # O resultado sempre vive no namespace `forecast`, independente da fonte.
    assert built.result_namespace == "forecast"
    assert any("source_namespace" in w for w in built.warnings)


def test_snapshot_vazio_usa_a_configuracao_de_anomalia():
    row = run_row(snapshot={})
    row.pop("_columns", None)
    built = contract_from_run_row(
        {**row, "config_snapshot": {}},
        available_columns=("realizado_cloud",),
        fallback={"source_namespace": "silver", "measure_column": "realizado_cloud", "dimensions": DIMS},
    )
    assert built.contract_source == "ANOMALY_CONFIG"
    assert built.measure_column == "realizado_cloud"
    assert built.dimensions == DIMS
    assert any("config_snapshot vazio" in w for w in built.warnings)


def test_status_nao_concluido_nao_e_utilizavel():
    assert contract(status="RUNNING").is_usable is False
    assert contract(status="FAILED").is_usable is False


def test_sem_cutoff_nao_e_utilizavel():
    assert contract(cutoff=None).is_usable is False


# ---------------------------------------------------------------------------
# Rastro de regras
# ---------------------------------------------------------------------------
def test_rastro_textual_vira_referencia_estruturada():
    metrics = {
        "business_rules": [
            "Return simulado",
            "Marketplace ONESHOT zera forecast (sem séries correspondentes)",
            "Aura Brain - PTU Azure OpenAI mensal (não aplicada: campo fora das dimensões)",
            "Azure-4P - suspender após 30 dias sem dados (suspensas 26 linhas; validade 30 dias)",
        ]
    }
    refs, source, declares_current = parse_applied_rule_refs(metrics)
    by_name = {r.name: r for r in refs}
    assert by_name["Return simulado"].status == APPLIED
    assert by_name["Marketplace ONESHOT zera forecast"].status == SERIES_NOT_FOUND
    assert by_name["Aura Brain - PTU Azure OpenAI mensal"].status == OUT_OF_DIMENSIONS
    assert by_name["Azure-4P - suspender após 30 dias sem dados"].status == APPLIED
    assert source == "RUN_METRICS"
    assert declares_current is False


def test_regra_ativa_sem_rastro_nao_e_apresentada_como_aplicada():
    frozen = [
        {"id": 8, "name": "Compras recorrentes - zerar mês já realizado", "version": 2},
        {"id": 25, "name": "Purchase recorrente Savings Plan", "version": 1},
    ]
    refs, source, declares_current = parse_applied_rule_refs(
        {"business_rules": ["Compras recorrentes - zerar mês já realizado"]},
        frozen_rules=frozen,
    )
    by_name = {r.name: r for r in refs}
    assert by_name["Compras recorrentes - zerar mês já realizado"].status == APPLIED
    assert by_name["Compras recorrentes - zerar mês já realizado"].rule_id == 8
    assert by_name["Purchase recorrente Savings Plan"].status == "NOT_EVALUATED"
    assert source == "SNAPSHOT"
    assert declares_current is False


def test_sem_rastro_congelado_declara_uso_das_regras_atuais():
    refs, source, declares_current = parse_applied_rule_refs(
        {}, current_rules=[{"id": 8, "name": "Compras recorrentes", "version": 2}]
    )
    assert source == "CURRENT_CONFIG"
    assert declares_current is True
    assert refs[0].status == "UNKNOWN_FROM_CURRENT_CONFIG"


def test_contrato_declara_uso_de_regras_atuais():
    built = contract_from_run_row(
        run_row(error_metrics={}),
        available_columns=("realizado_cloud",),
        current_rules=[{"id": 8, "name": "Compras recorrentes", "version": 2}],
    )
    assert built.declares_current_rules is True
    assert built.rule_source == "CURRENT_CONFIG"


# ---------------------------------------------------------------------------
# Cobertura e horizonte
# ---------------------------------------------------------------------------
def test_classificacao_de_cobertura():
    cutoff = date(2026, 10, 12)
    assert classify_coverage("202610", cutoff) == (
        REMAINING_MONTH, date(2026, 10, 13), date(2026, 10, 31), 19
    )
    assert classify_coverage("202611", cutoff)[0] == FULL_MONTH
    assert classify_coverage("202611", cutoff)[3] == 30
    assert classify_coverage("202609", cutoff)[0] == ELAPSED_MONTH
    # Corte no último dia do mês = mês completo.
    assert classify_coverage("202610", date(2026, 10, 31))[0] == FULL_MONTH


def test_horizonte_de_15_dias_nao_divide_o_forecast_mensal():
    """Os 15 dias são horizonte de acompanhamento, não divisor do mês."""
    assert horizon_months(date(2026, 10, 12), 15) == ["202610"]


def test_horizonte_atravessa_a_virada_do_mes():
    assert horizon_months(date(2026, 10, 25), 15) == ["202610", "202611"]


def test_horizonte_no_inicio_do_mes():
    assert horizon_months(date(2026, 10, 1), 15) == ["202610"]


# ---------------------------------------------------------------------------
# Referência compacta
# ---------------------------------------------------------------------------
def expectation_rows():
    return [
        {"cloud_origem": "gcp-4p", "servicename": "Compute", "forecast_month": "202610", "expected_cloud": 1000.0},
        {"cloud_origem": "gcp-4p", "servicename": "Storage", "forecast_month": "202610", "expected_cloud": 250.0},
        {"cloud_origem": "gcp-4p", "servicename": "Compute", "forecast_month": "202611", "expected_cloud": 3000.0},
        # mês já transcorrido no corte: fora do horizonte
        {"cloud_origem": "gcp-4p", "servicename": "Compute", "forecast_month": "202609", "expected_cloud": 999.0},
    ]


def test_referencia_preserva_o_total_mensal_do_forecast():
    built = contract()
    rows = build_reference_rows(
        built, expectation_rows(), dimensions=DIMS,
        reference_kind="PROSPECTIVE", target_start="2026-10-13", target_end="2026-10-31",
    )
    by_month: dict[str, float] = {}
    for row in rows:
        by_month[row.reference_month] = by_month.get(row.reference_month, 0.0) + row.predicted_cloud
    assert by_month == {"202610": 1250.0, "202611": 3000.0}


def test_referencia_e_compacta_uma_linha_por_serie_e_mes():
    rows = build_reference_rows(
        contract(), expectation_rows(), dimensions=DIMS,
        reference_kind="PROSPECTIVE", target_start="2026-10-13", target_end="2026-11-30",
    )
    keys = [(r.series_id, r.reference_month) for r in rows]
    assert len(keys) == len(set(keys))
    assert len(rows) == 3  # 202609 descartado
    assert not any(r.reference_month == "202609" for r in rows)


def test_referencia_registra_o_contrato_do_plano():
    rows = build_reference_rows(
        contract(), expectation_rows(), dimensions=DIMS,
        reference_kind="PROSPECTIVE", target_start="2026-10-13", target_end="2026-11-30",
    )
    row = next(r for r in rows if r.reference_month == "202610")
    assert row.forecast_run_id == 399
    assert row.forecast_config_version == 21
    assert row.data_cutoff == "2026-10-12"
    assert row.generated_at is not None
    assert row.reference_kind == "PROSPECTIVE"
    assert row.coverage_kind == REMAINING_MONTH
    assert row.covered_days == 19
    assert row.partial_reference is True
    payload = row.as_dict()
    for field in (
        "forecast_run_id", "forecast_config_version", "source_snapshot_id",
        "data_cutoff", "generated_at", "target_start", "target_end",
        "reference_kind", "applied_rule_refs",
    ):
        assert field in payload


def test_mes_completo_nao_e_marcado_como_parcial():
    rows = build_reference_rows(
        contract(), expectation_rows(), dimensions=DIMS,
        reference_kind="PROSPECTIVE", target_start="2026-10-13", target_end="2026-11-30",
    )
    row = next(r for r in rows if r.reference_month == "202611")
    assert row.coverage_kind == FULL_MONTH
    assert row.covered_days == 30
    assert row.partial_reference is False


def test_referencia_exige_cutoff():
    with pytest.raises(ReferenceUnavailable, match="CUTOFF_ABSENT"):
        build_reference_rows(
            contract(cutoff=None), expectation_rows(), dimensions=DIMS,
            reference_kind="PROSPECTIVE", target_start="2026-10-13", target_end="2026-10-31",
        )


def test_referencia_recusa_kind_unavailable():
    with pytest.raises(ReferenceUnavailable, match="REFERENCE_UNAVAILABLE"):
        build_reference_rows(
            contract(), expectation_rows(), dimensions=DIMS,
            reference_kind="UNAVAILABLE", target_start="2026-10-13", target_end="2026-10-31",
        )


def test_ids_sao_deterministicos():
    a = series_id_for(DIMS, {"cloud_origem": "gcp-4p", "servicename": "Compute"})
    b = series_id_for(DIMS, {"cloud_origem": "gcp-4p", "servicename": "Compute"})
    c = series_id_for(DIMS, {"cloud_origem": "gcp-4p", "servicename": "Storage"})
    assert a == b != c
    # None e string vazia colapsam para o mesmo id (não duplicar caso).
    assert series_id_for(DIMS, {"cloud_origem": None, "servicename": ""}) == series_id_for(
        DIMS, {"cloud_origem": "", "servicename": None}
    )
    assert reference_id_for(399, a, "202610") == reference_id_for(399, a, "202610")


# ---------------------------------------------------------------------------
# Seleção prospectiva / retrospectiva
# ---------------------------------------------------------------------------
def test_seleciona_snapshot_gerado_antes_do_evento_como_prospectivo():
    early = contract(run_id=395, cutoff="2026-10-09", finished="2026-10-10T01:00:00-03:00")
    late = contract(run_id=399, cutoff="2026-10-12", finished="2026-10-14T01:00:00-03:00")
    selection = select_reference([late, early], event_date=date(2026, 10, 11))
    assert selection.reference_kind == "PROSPECTIVE"
    assert selection.contract.run_id == 395


def test_snapshot_posterior_com_corte_anterior_e_retrospectivo():
    # Gerado depois do evento, mas com corte anterior: o realizado do evento
    # não participou do cálculo.
    late_generated = contract(run_id=400, cutoff="2026-10-09", finished="2026-10-20T01:00:00-03:00")
    selection = select_reference([late_generated], event_date=date(2026, 10, 11))
    assert selection.reference_kind == "RETROSPECTIVE"
    assert any("reconstruída" in w for w in selection.warnings)


def test_sem_corte_anterior_ao_evento_fica_indisponivel():
    late = contract(run_id=399, cutoff="2026-10-12", finished="2026-10-14T01:00:00-03:00")
    selection = select_reference([late], event_date=date(2026, 10, 10))
    assert selection.reference_kind == "UNAVAILABLE"
    assert selection.contract is None
    assert "CORTE_POSTERIOR_AO_EVENTO" in selection.reason


def test_sem_snapshot_utilizavel_fica_indisponivel():
    selection = select_reference([contract(status="FAILED")], event_date=date(2026, 10, 10))
    assert selection.reference_kind == "UNAVAILABLE"
    assert selection.reason == "NENHUM_SNAPSHOT_UTILIZAVEL"


# ---------------------------------------------------------------------------
# Aceite E2: restante zero não provoca falso positivo
# ---------------------------------------------------------------------------
def test_compra_mensal_esperada_nao_vira_anomalia_com_restante_zero():
    """Compra de 30.000 no dia 05; forecast do dia 12 tem restante zero.

    A referência preservada é a do dia 02, que ainda esperava a compra.
    """
    pre_purchase = contract(run_id=394, cutoff="2026-10-02", finished="2026-10-03T01:00:00-03:00")
    post_purchase = contract(run_id=399, cutoff="2026-10-12", finished="2026-10-14T01:00:00-03:00")

    selection = select_pre_event_reference(
        [post_purchase, pre_purchase], event_date=date(2026, 10, 5), event_month="202610"
    )
    assert selection.is_available
    assert selection.contract.run_id == 394
    assert selection.reason == "REFERENCIA_ANTERIOR_A_COMPRA_PRESERVADA"

    rows = build_reference_rows(
        selection.contract,
        [{"cloud_origem": "azure-ti", "servicename": "SavingsPlan", "forecast_month": "202610", "expected_cloud": 30000.0}],
        dimensions=DIMS,
        reference_kind=selection.reference_kind,
        target_start="2026-10-03", target_end="2026-10-31",
    )
    expected = rows[0].predicted_cloud
    observed = 30000.0
    assert expected == observed
    assert observed - expected == 0.0  # evento esperado, sem anomalia


def test_restante_zero_e_legitimo_quando_a_compra_ja_foi_realizada():
    post = contract(run_id=399, cutoff="2026-10-12", finished="2026-10-14T01:00:00-03:00")
    assert zero_remaining_is_legitimate(post, "202610", monthly_event_realized=30000.0) is True
    # Sem compra realizada no mês, o zero é ausência de gasto — não presumir.
    assert zero_remaining_is_legitimate(post, "202610", monthly_event_realized=0.0) is False
    # Em mês futuro (mês completo) o zero não é "restante realizado".
    assert zero_remaining_is_legitimate(post, "202611", monthly_event_realized=30000.0) is False


def test_sem_referencia_anterior_a_compra_declara_indisponivel():
    post = contract(run_id=399, cutoff="2026-10-12", finished="2026-10-14T01:00:00-03:00")
    selection = select_pre_event_reference([post], event_date=date(2026, 10, 5))
    assert selection.reference_kind == "UNAVAILABLE"
    assert selection.reason == "SEM_REFERENCIA_ANTERIOR_A_COMPRA"


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------
def test_month_of_e_geracao_de_chave():
    assert month_of(date(2026, 10, 5)) == "202610"
    assert month_of(date(2026, 1, 1)) == "202601"


def test_generated_at_e_normalizado_com_timezone():
    built = contract(finished="2026-10-14T01:16:59-03:00")
    assert built.generated_at is not None
    assert built.generated_at.tzinfo is not None
    assert built.generated_at.astimezone(timezone.utc).date() == date(2026, 10, 14)
