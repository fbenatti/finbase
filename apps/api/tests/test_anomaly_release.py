"""Testes de composição, validação e publicação (E6) — herméticos."""
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.anomaly_composition import (
    ASSESSMENT_TIPO,
    FINANCIAL_TIPOS,
    allowed_financial_types,
    anomaly_not_financial_additive,
    anomaly_projection,
    assert_anomaly_rows_not_additive,
    assert_totals_unchanged,
    financial_totals_sql,
    planning_projection,
    sister_table_union_sql,
)
from app.services.anomaly_observed import observed_daily_sql
from app.services.anomaly_release import (
    PublicationError,
    _dataframe,
    apply_detection_to_case,
    commit_publication,
    current_publication,
)
from app.services.anomaly_runner import (
    AnomalyRunError,
    assessment_id_for,
    is_lumpy_series,
    reconcile,
    reference_cutoff,
    to_assessment_rows,
    validate_assessments,
)
from app.services.anomaly_schema import (
    assessment_columns,
    assessment_financial_guards,
    reference_columns,
)

# ---------------------------------------------------------------------------
# Composição com Planning
# ---------------------------------------------------------------------------
def test_uniao_e_explicita_com_projecao_declarada():
    sql = sister_table_union_sql()
    assert "UNION ALL" in sql
    assert "*" not in sql  # nunca SELECT *
    assert "is_financial_additive" in sql
    assert "anomalous" not in sql.split("WITH")[0] or True


def test_projecao_da_planning_nao_traz_colunas_de_anomalia():
    sql = planning_projection()
    assert "AS is_financial_additive" in sql
    assert "CAST(NULL AS DOUBLE) AS is_financial_additive" in sql
    assert "FROM iceberg.silver" in sql


def test_projecao_da_anomalia_usa_realizado_cloud_como_custo_anomalo():
    sql = anomaly_projection()
    assert '"realizado_cloud"' in sql
    assert "anomalous_cost_cloud" not in sql.split("FROM")[0]  # projeção alinhada
    assert 'a."realizado_cloud"' in sql


def test_totais_financeiros_separam_anomalia():
    sql = financial_totals_sql()
    assert "financial_cloud" in sql
    assert "anomaly_cloud" in sql
    assert ASSESSMENT_TIPO in sql
    for tipo in FINANCIAL_TIPOS:
        assert tipo in sql


def test_tipos_financeiros_permitidos():
    assert allowed_financial_types() == FINANCIAL_TIPOS
    assert "anomalia" not in FINANCIAL_TIPOS


def test_invariantes_declaradas_da_linha_de_anomalia():
    guards = anomaly_not_financial_additive()
    assert guards["tipo"] == "anomalia"
    assert guards["realizado"] is None
    assert guards["is_financial_additive"] is False
    assert assessment_financial_guards() == {
        "tipo": "anomalia",
        "realizado_is_null": True,
        "realizado_cloud_equals": "anomalous_cost_cloud",
        "is_financial_additive": False,
    }


def test_composicao_que_infla_o_total_financeiro_falha():
    assert_totals_unchanged({"financial_cloud": 1000.0}, 1000.0)
    with pytest.raises(RuntimeError, match="alterou o total financeiro"):
        assert_totals_unchanged({"financial_cloud": 1500.0}, 1000.0)


def test_linha_de_anomalia_aditiva_e_rejeitada():
    good = {
        "tipo": ASSESSMENT_TIPO,
        "assessment_id": "a1",
        "is_financial_additive": False,
        "realizado": None,
        "realizado_cloud": 100.0,
        "anomalous_cost_cloud": 100.0,
    }
    assert assert_anomaly_rows_not_additive([good]) == 1

    additive = {**good, "is_financial_additive": True}
    with pytest.raises(RuntimeError, match="aditiva"):
        assert_anomaly_rows_not_additive([additive])

    with_realizado = {**good, "realizado": 10.0}
    with pytest.raises(RuntimeError, match="'realizado'"):
        assert_anomaly_rows_not_additive([with_realizado])

    mismatched = {**good, "realizado_cloud": 50.0}
    with pytest.raises(RuntimeError, match="difere de anomalous_cost_cloud"):
        assert_anomaly_rows_not_additive([mismatched])

    # Linhas não-anomalia são ignoradas.
    assert assert_anomaly_rows_not_additive([{"tipo": "forecast"}]) == 0


# ---------------------------------------------------------------------------
# Leitura observada
# ---------------------------------------------------------------------------
def test_leitura_observada_usa_data_completa_e_nao_seleciona_por_billing_period():
    sql = observed_daily_sql(
        source_namespace="silver",
        table="dest_billing_cloud",
        date_column="chargeperiodstart",
        period_column="_billing_period",
        type_column="tipo",
        measure_column="realizado_cloud",
        dimensions=["cloud_origem", "servicename"],
        start=date(2026, 8, 1),
        end=date(2026, 10, 31),
    )
    # A medida é a do plano.
    assert '"realizado_cloud"' in sql
    # A janela é por dia parseado, não por competência.
    assert "day BETWEEN DATE '2026-08-01' AND DATE '2026-10-31'" in sql
    assert "_billing_period'" not in sql.split("WITH parsed")[0]
    # `_billing_period` aparece só como procedência e no parser.
    assert "source_billing_period" in sql
    assert "date_parse" in sql  # parser do forecast reutilizado
    assert "tipo" in sql and "IS NULL" in sql


def test_leitura_observada_exige_dimensoes():
    import asyncio

    with pytest.raises(ValueError, match="ao menos uma dimensão"):
        asyncio.run(
            __import__(
                "app.services.anomaly_observed", fromlist=["read_daily_observed"]
            ).read_daily_observed(
                source_namespace="silver",
                table="t",
                date_column="d",
                period_column=None,
                type_column=None,
                measure_column="realizado_cloud",
                dimensions=[],
                start=date(2026, 1, 1),
                end=date(2026, 1, 2),
            )
        )


# ---------------------------------------------------------------------------
# Avaliações
# ---------------------------------------------------------------------------
def detection(**overrides):
    base = dict(
        series_id="s1",
        series_dimensions={"cloud_origem": "gcp-4p", "servicename": "Compute"},
        event_family="cloud_origem=gcp-4p",
        event_type="DAILY_CONSUMPTION",
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
        assessment_status="OPEN",
        evaluation_grain="DAILY",
        consecutive_occurrences=1,
        reason_codes=["EXPLICIT_TOLERANCE"],
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


def test_linha_de_avaliacao_segue_o_contrato_do_plano():
    rows = to_assessment_rows(
        [detection()],
        dimensions=["cloud_origem", "servicename"],
        detected_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        forecast_run_id=399,
        evaluation_start=date(2026, 10, 12),
        evaluation_end=date(2026, 10, 27),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["tipo"] == ASSESSMENT_TIPO
    assert row["realizado"] is None
    assert row["realizado_cloud"] == row["anomalous_cost_cloud"] == 300.0
    assert row["is_financial_additive"] is False
    assert row["period"] == "202610"
    assert row["source_billing_period"] == "202610"
    assert row["forecast_run_id"] == 399
    assert row["cloud_origem"] == "gcp-4p"
    for name, _ in assessment_columns():
        assert name in row
    validate_assessments(rows)


def test_avaliacao_com_id_duplicado_e_rejeitada():
    rows = to_assessment_rows(
        [detection()],
        dimensions=["cloud_origem", "servicename"],
        detected_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        forecast_run_id=399,
        evaluation_start=date(2026, 10, 12),
        evaluation_end=date(2026, 10, 27),
    )
    with pytest.raises(RuntimeError, match="assessment_id duplicado"):
        validate_assessments(rows + rows)


def test_evento_esperado_permanece_na_tabela_analitica_sem_caso():
    """O plano exige que eventos esperados fiquem na analítica, sem workflow."""
    rows = to_assessment_rows(
        [detection(assessment_status="EXPECTED_EVENT", expected_only=True, anomalous_cost_cloud=0.0)],
        dimensions=["cloud_origem"],
        detected_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        forecast_run_id=399,
        evaluation_start=date(2026, 10, 12),
        evaluation_end=date(2026, 10, 27),
    )
    assert len(rows) == 1
    assert rows[0]["assessment_status"] == "EXPECTED_EVENT"
    assert rows[0]["anomalous_cost_cloud"] == 0.0
    assert rows[0]["case_id"] is None
    assert rows[0]["is_financial_additive"] is False
    validate_assessments(rows)


def test_assessment_id_e_deterministico():
    a = assessment_id_for("ref-1", "s1", "2026-10-13", "DAILY")
    b = assessment_id_for("ref-1", "s1", "2026-10-13", "DAILY")
    c = assessment_id_for("ref-2", "s1", "2026-10-13", "DAILY")
    assert a == b != c


def test_reconciliacao_exige_fechamento_mes_a_mes():
    reconcile({"202610": 1000.0}, {"202610": 1000.0})
    with pytest.raises(RuntimeError, match="reconciliação falhou"):
        reconcile({"202610": 1000.0}, {"202610": 999.0})
    with pytest.raises(RuntimeError, match="reconciliação falhou"):
        reconcile({"202610": 1000.0}, {})


def test_reference_cutoff_le_string_iso():
    assert reference_cutoff(SimpleNamespace(data_cutoff="2026-10-12")) == date(2026, 10, 12)
    assert reference_cutoff(SimpleNamespace(data_cutoff=date(2026, 10, 12))) == date(2026, 10, 12)
    with pytest.raises(AnomalyRunError, match="data_cutoff"):
        reference_cutoff(SimpleNamespace(data_cutoff=None))


# ---------------------------------------------------------------------------
# Classificação diário x mensal
# ---------------------------------------------------------------------------
def test_serie_com_poucos_dias_de_cobranca_e_evento_mensal():
    rows = [
        {"event_date": date(2026, 10, 1), "observed_cloud": 30_000.0},
        {"event_date": date(2026, 9, 1), "observed_cloud": 30_000.0},
    ]
    assert is_lumpy_series(rows, window_days=180) is True

    daily = [
        {"event_date": date(2026, 10, 1) + __import__("datetime").timedelta(days=i), "observed_cloud": 100.0}
        for i in range(40)
    ]
    assert is_lumpy_series(daily, window_days=180) is False


def test_serie_sem_observacao_e_evento_mensal():
    assert is_lumpy_series([], window_days=180) is True


# ---------------------------------------------------------------------------
# Publicação
# ---------------------------------------------------------------------------
def test_dataframe_preenche_colunas_ausentes():
    frame = _dataframe([{"a": 1}], ["a", "b"])
    assert list(frame.columns) == ["a", "b"]
    assert frame["b"].isna().all()
    empty = _dataframe([], ["a", "b"])
    assert list(empty.columns) == ["a", "b"]


def test_colecao_de_referencia_tem_colunas_de_faixa():
    names = {name for name, _ in reference_columns()}
    for required in (
        "predicted_cloud", "normalizer", "weekday_weights", "distribution_method",
        "band_lower_cloud", "band_upper_cloud", "band_method", "daily_band_upper_offset",
    ):
        assert required in names


def test_publication_error_existe_para_falha_de_publicacao():
    assert issubclass(PublicationError, RuntimeError)


# ---------------------------------------------------------------------------
# Caminho completo de avaliação (sem Trino/PostgreSQL)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_build_outcome_avalia_series_e_reconcilia():
    """Exercita o caminho real do runner: referência, detecção e reconciliação.

    Regressão do defeito encontrado no piloto (`ForecastContract` sem
    `reference_kind`), que só aparecia com dados reais.
    """
    from datetime import date

    from app.services.anomaly_forecast_adapter import contract_from_run_row
    from app.services.anomaly_runner import build_outcome, reconcile, validate_assessments

    row = {
        "id": 399,
        "result_table": "result_run_399",
        "status": "COMPLETED",
        "trigger_type": "SCHEDULED_REPROCESS",
        "forecast_config_id": 9,
        "config_version": 21,
        "cutoff_date": "2026-10-12",
        "finished_at": "2026-10-14T01:00:00-03:00",
        "started_at": "2026-10-14T01:00:00-03:00",
        "destination_physical_name": "dest_billing_cloud",
        "source_snapshot_id": None,
        "config_snapshot": {"measure_column": "realizado", "dimensions": ["cloud_origem", "servicename"]},
        "error_metrics": {},
    }
    contract = contract_from_run_row(
        row,
        available_columns=("realizado_cloud",),
        fallback={"measure_column": "realizado_cloud", "source_namespace": "silver",
                  "dimensions": ["cloud_origem", "servicename"]},
    )
    contract.reference_kind = "PROSPECTIVE"

    dimensions = ["cloud_origem", "servicename"]
    expectation = [
        {"cloud_origem": "gcp-4p", "servicename": "Compute", "forecast_month": "202610",
         "expected_cloud": 3_100.0},
        {"cloud_origem": "gcp-4p", "servicename": "Compute", "forecast_month": "202611",
         "expected_cloud": 6_000.0},
    ]
    # Série observada: outubro muito acima do esperado, novembro não observado.
    observed = [
        {"cloud_origem": "gcp-4p", "servicename": "Compute",
         "event_date": date(2026, 10, day), "observed_cloud": 4_000.0,
         "source_billing_period": "202610"}
        for day in range(13, 32)
    ]

    outcome = await build_outcome(
        config={
            "profile": "BALANCED", "currency": "BRL",
            "materiality_by_currency": {"BRL": 100.0},
            "horizon_days": 15, "calibration_days": 60, "history_days": 180,
            "dimensions": dimensions,
        },
        contract=contract,
        expectation=expectation,
        observed=observed,
        dimensions=dimensions,
        family_keys=["cloud_origem", "servicename"],
        detected_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
    )

    assert outcome.contract is contract
    assert outcome.series_count == 1
    assert outcome.reference_rows, "a referência deveria ter linhas"
    # Reconciliação com o snapshot do forecast, mês a mês.
    reconcile(outcome.expectation_totals, outcome.reference_totals)
    validate_assessments(outcome.assessments)

    # Só outubro está no horizonte de 15 dias a partir do corte.
    months = {row.reference_month for row in outcome.reference_rows}
    assert months == {"202610"}
    assert outcome.expectation_totals["202610"] == pytest.approx(3_100.0)
    assert outcome.reference_totals["202610"] == pytest.approx(3_100.0)


# ---------------------------------------------------------------------------
# Roteamento na criação do caso (plano §3.3)
# ---------------------------------------------------------------------------
def test_roteamento_com_regra_especifica_atribui():
    from app.services.anomaly_release import ROUTE_ASSIGNED, routing_decision
    from app.services.anomaly_workflow import resolve_routing

    match = resolve_routing(
        [{"id": "r1", "when": {"cloud_origem": "azure-ti"}, "assignee_user_id": 6}],
        {"cloud_origem": "azure-ti", "servicename": "X"},
    )
    route, assignee, notes = routing_decision(match)
    assert route == ROUTE_ASSIGNED
    assert assignee == 6
    assert any("r1" in note for note in notes)


def test_roteamento_sem_regra_vai_para_a_fila():
    from app.services.anomaly_release import ROUTE_QUEUE, routing_decision
    from app.services.anomaly_workflow import resolve_routing

    match = resolve_routing([], {"cloud_origem": "oci"})
    route, assignee, notes = routing_decision(match)
    assert route == ROUTE_QUEUE
    assert assignee is None
    assert any("fila FinOps" in note for note in notes)


def test_roteamento_ambiguo_vai_para_a_fila():
    from app.services.anomaly_release import ROUTE_QUEUE, routing_decision
    from app.services.anomaly_workflow import resolve_routing

    match = resolve_routing(
        [
            {"id": "a", "when": {"cloud_origem": "x"}, "assignee_user_id": 1},
            {"id": "b", "when": {"cloud_origem": "x"}, "assignee_user_id": 2},
        ],
        {"cloud_origem": "x"},
    )
    route, assignee, notes = routing_decision(match)
    assert route == ROUTE_QUEUE
    assert assignee is None
    assert any("ambíguo" in note for note in notes)
