"""Testes da série diária derivada da referência compacta (F2)."""
from datetime import date

import pytest

from app.services.anomaly_series import (
    _cutoff_of,
    _months_for,
    _weekday_weights,
    derive_daily_points,
    observed_daily_sql,
    reference_series_sql,
)

# Peso por dia da semana (0=segunda) já normalizado sobre o intervalo coberto.
WEIGHTS = {"0": 0.18, "1": 0.18, "2": 0.18, "3": 0.18, "4": 0.18, "5": 0.05, "6": 0.05}


def reference(**overrides):
    base = {
        "reference_id": "ref-1",
        "reference_month": "202610",
        "reference_kind": "PROSPECTIVE",
        "coverage_kind": "REMAINING_MONTH",
        "coverage_start": "2026-10-13",
        "coverage_end": "2026-10-31",
        "covered_days": 19,
        "predicted_cloud": 1900.0,
        "weekday_weights": WEIGHTS,
        "normalizer": 100.0,
        "distribution_method": "WEEKDAY_MEDIAN",
        "daily_band_lower_offset": 50.0,
        "daily_band_upper_offset": 200.0,
        "band_method": "HISTORICAL_QUANTILE",
        "band_confidence": "HIGH",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Derivação
# ---------------------------------------------------------------------------
def test_um_ponto_por_dia_coberto():
    points = derive_daily_points([reference()], {}, horizon_end=date(2026, 10, 31))
    assert len(points) == 19
    assert points[0]["event_date"] == "2026-10-13"
    assert points[-1]["event_date"] == "2026-10-31"


def test_esperado_diario_usa_o_peso_do_dia_da_semana():
    points = derive_daily_points([reference()], {}, horizon_end=date(2026, 10, 31))
    by_date = {point["event_date"]: point for point in points}
    # Normalizado sobre os 19 dias cobertos: 14 dias úteis × 0,18 + 5 de fim de
    # semana × 0,05 = 2,77. Terça = 1900 × 0,18 / 2,77; sábado = 1900 × 0,05 / 2,77.
    assert by_date["2026-10-13"]["expected_cloud"] == pytest.approx(1900 * 0.18 / 2.77)
    assert by_date["2026-10-17"]["expected_cloud"] == pytest.approx(1900 * 0.05 / 2.77)
    assert by_date["2026-10-13"]["expected_cloud"] > by_date["2026-10-17"]["expected_cloud"]
    assert by_date["2026-10-17"]["weekday"] == 5


def test_soma_dos_dias_reconcilia_com_o_valor_do_mes():
    points = derive_daily_points([reference()], {}, horizon_end=date(2026, 10, 31))
    # A soma da distribuição completa é exatamente o valor do forecast.
    total = sum(point["expected_cloud"] for point in points)
    assert total == pytest.approx(1900.0, abs=0.05)


def test_faixa_aplicada_por_dia():
    points = derive_daily_points([reference()], {}, horizon_end=date(2026, 10, 31))
    for point in points:
        assert point["upper_cloud"] == pytest.approx(point["expected_cloud"] + 200.0)
        assert point["lower_cloud"] == pytest.approx(max(0.0, point["expected_cloud"] - 50.0))


def test_custo_anomalo_so_existe_acima_do_limite():
    # Primeiro derivamos o esperado, depois observamos em cima dele.
    baseline = derive_daily_points([reference()], {}, horizon_end=date(2026, 10, 31))
    expected = {point["event_date"]: point["expected_cloud"] for point in baseline}
    observed = {
        date(2026, 10, 13): expected["2026-10-13"],                    # exatamente o esperado
        date(2026, 10, 14): expected["2026-10-14"] + 100.0,            # dentro da faixa (200)
        date(2026, 10, 15): expected["2026-10-15"] + 500.0,            # acima do limite
    }
    points = derive_daily_points([reference()], observed, horizon_end=date(2026, 10, 31))
    by_date = {point["event_date"]: point for point in points}
    assert by_date["2026-10-13"]["anomalous_cost_cloud"] == 0.0
    assert by_date["2026-10-13"]["above_band"] is False
    assert by_date["2026-10-14"]["anomalous_cost_cloud"] == 0.0
    assert by_date["2026-10-15"]["above_band"] is True
    assert by_date["2026-10-15"]["anomalous_cost_cloud"] == pytest.approx(300.0)


def test_dia_sem_observacao_fica_nulo_e_nao_vira_zero():
    points = derive_daily_points([reference()], {}, horizon_end=date(2026, 10, 31))
    for point in points:
        assert point["observed_cloud"] is None
        assert point["anomalous_cost_cloud"] is None
        assert point["above_band"] is None


def test_horizonte_recorta_a_cobertura():
    points = derive_daily_points([reference()], {}, horizon_end=date(2026, 10, 20))
    assert points[-1]["event_date"] == "2026-10-20"
    assert len(points) == 8  # 13 a 20 de outubro


def test_sem_pesos_usa_fallback_uniforme_identificado():
    points = derive_daily_points(
        [reference(weekday_weights=None, covered_days=19)], {}, horizon_end=date(2026, 10, 31)
    )
    assert points[0]["distribution_method"] == "UNIFORM_FALLBACK"
    assert points[0]["expected_cloud"] == pytest.approx(1900.0 / 19)


def test_referencia_sem_cobertura_e_ignorada():
    points = derive_daily_points(
        [reference(coverage_start=None, coverage_end=None)], {}, horizon_end=date(2026, 10, 31)
    )
    assert points == []


def test_dois_meses_em_ordem_cronologica():
    rows = [
        reference(reference_month="202611", coverage_start="2026-11-01", coverage_end="2026-11-02", covered_days=2, predicted_cloud=200.0),
        reference(reference_month="202610", coverage_start="2026-10-30", coverage_end="2026-10-31", covered_days=2, predicted_cloud=200.0),
    ]
    points = derive_daily_points(rows, {}, horizon_end=date(2026, 11, 2))
    assert [point["event_date"] for point in points] == [
        "2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02",
    ]


def test_pesos_aceitam_json_em_texto():
    assert _weekday_weights('{"5": 0.25, "6": 0.1}') == {5: 0.25, 6: 0.1}
    assert _weekday_weights(None) == {}
    assert _weekday_weights("não é json") == {}
    assert _weekday_weights({"x": "y"}) == {}


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------
def test_sql_da_referencia_filtra_serie_e_meses():
    sql = reference_series_sql(
        namespace="silver",
        reference_table="dest_modelo_focus_anomaly_reference",
        series_id="abc123",
        months=["202610", "202611"],
    )
    assert 'FROM iceberg.silver."dest_modelo_focus_anomaly_reference"' in sql
    assert "series_id = 'abc123'" in sql
    assert "'202610'" in sql and "'202611'" in sql
    assert "weekday_weights" in sql and "daily_band_upper_offset" in sql


def test_sql_do_observado_le_realizado_cloud_por_dia():
    sql = observed_daily_sql(
        source_namespace="silver",
        table="dest_modelo_focus",
        date_column="chargeperiodstart",
        period_column="_billing_period",
        type_column="tipo",
        measure_column="realizado_cloud",
        dimensions=["cloud_origem", "servicename"],
        start=date(2026, 10, 13),
        end=date(2026, 10, 31),
    )
    assert '"realizado_cloud"' in sql
    assert "day BETWEEN DATE '2026-10-13' AND DATE '2026-10-31'" in sql


def test_meses_do_horizonte():
    assert _months_for(date(2026, 10, 12), 15) == ["202610"]
    assert _months_for(date(2026, 10, 25), 15) == ["202610", "202611"]


def test_cutoff_da_publicacao_com_fallback():
    from types import SimpleNamespace

    assert _cutoff_of(SimpleNamespace(data_cutoff="2026-10-12", generated_at=None)) == date(2026, 10, 12)
    generated = SimpleNamespace(data_cutoff=None, generated_at=None)
    assert _cutoff_of(generated) == date.today()


def test_referencias_sobrepostas_nao_repetem_datas_e_respeitam_o_corte():
    old = reference(reference_id="old", forecast_run_id=1, data_cutoff="2026-10-05",
                    coverage_start="2026-10-06", covered_days=26, predicted_cloud=2600, weekday_weights=None)
    new = reference(reference_id="new", forecast_run_id=2, data_cutoff="2026-10-11",
                    coverage_start="2026-10-12", covered_days=20, predicted_cloud=4000, weekday_weights=None)
    points = derive_daily_points([new, old], {}, horizon_end=date(2026, 10, 14))
    assert len(points) == len({point["event_date"] for point in points}) == 9
    by_date = {point["event_date"]: point for point in points}
    assert by_date["2026-10-11"]["expected_cloud"] == 100
    assert by_date["2026-10-12"]["expected_cloud"] == 200


def test_referencia_congelada_nao_absorve_aumento_do_novo_forecast():
    old = reference(reference_id="frozen", forecast_run_id=1, data_cutoff="2026-10-11",
                    coverage_start="2026-10-12", covered_days=20, predicted_cloud=2000, weekday_weights=None)
    new = reference(reference_id="new", forecast_run_id=2, data_cutoff="2026-10-12",
                    coverage_start="2026-10-13", covered_days=19, predicted_cloud=19000, weekday_weights=None)
    points = derive_daily_points([old, new], {}, horizon_end=date(2026, 10, 20), frozen_reference_id="frozen")
    assert all(point["reference_id"] == "frozen" and point["expected_cloud"] == 100 for point in points)


def test_zero_restante_diario_recupera_baseline_congelado_sem_dados_posteriores():
    points = derive_daily_points([reference(predicted_cloud=0, weekday_weights={str(day): .1 for day in range(7)}, normalizer=1200)],
                                 {}, horizon_end=date(2026, 10, 13), weekday_medians={1: 9999})
    assert points[0]["expected_cloud"] == 120
    assert points[0]["distribution_method"] == "HISTORICAL_BASELINE_ZERO_FORECAST"


def test_timeline_nao_inventa_realizado_futuro_e_preserva_lacunas():
    from app.services.anomaly_series import compose_case_timeline
    reference_points = derive_daily_points([reference()], {}, horizon_end=date(2026, 10, 17))
    points = compose_case_timeline(reference_points, {date(2026, 10, 13): 500, date(2026, 10, 16): 999},
                                  start=date(2026, 10, 12), end=date(2026, 10, 17), data_end=date(2026, 10, 14))
    assert points[0]["observed_cloud"] is None and points[0]["expected_cloud"] is None
    assert points[2]["observed_cloud"] is None  # dia sem coleta não é zero
    assert all(point["observed_cloud"] is None for point in points if point["is_future"])
    assert all(point["expected_cloud"] is not None for point in points if point["is_future"])


def test_compra_mensal_nao_vira_consumo_diario_nem_compara_saldo_zero():
    from app.services.anomaly_series import compose_case_timeline
    references = derive_daily_points([reference(predicted_cloud=0)], {}, horizon_end=date(2026, 10, 15))
    points = compose_case_timeline(references, {date(2026, 10, 1): 30000, date(2026, 10, 13): 30000},
                                  start=date(2026, 10, 1), end=date(2026, 10, 15), data_end=date(2026, 10, 14), monthly=True)
    assert points[12]["observed_cloud"] == 60000
    assert points[12]["expected_cloud"] is None
    assert points[12]["anomalous_cost_cloud"] is None
    assert points[11]["observed_cloud"] is None


def test_api_separa_horizonte_forecast_do_cutoff_realizado(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import app.services.anomaly_series as service
    frozen = reference(reference_id="frozen", forecast_run_id=397, data_cutoff="2026-10-11",
                       coverage_start="2026-10-12", covered_days=20, predicted_cloud=2000,
                       weekday_weights=None, target_end="2026-10-26")
    calls = []

    async def query(sql, **_kwargs):
        calls.append(sql)
        return [frozen] if "SELECT reference_month" in sql else [
            {"event_date": "2026-10-12", "observed_cloud": 500},
            {"event_date": "2026-10-13", "observed_cloud": 999},
        ]

    monkeypatch.setattr(service, "trino_execute", query)
    result = asyncio.run(service.read_case_series(
        case=SimpleNamespace(series_id="s", period="202610", event_date="2026-10-12",
                             current_reference_id="frozen", evaluation_grain="DAILY", series_dimensions={"cloud_origem": "gcp"}),
        publication=SimpleNamespace(data_cutoff="2026-10-11", reference_table="ref"),
        reference_namespace="silver", source_namespace="silver", source_table="focus",
        date_column="chargeperiodstart", period_column="_billing_period", type_column="tipo",
        measure_column="realizado_cloud", dimensions=["cloud_origem"], analysis_data_end=date(2026, 10, 12),
    ))
    assert result["forecast_end"] == "2026-10-26"
    assert result["analysis_data_end"] == "2026-10-12"
    assert len(result["items"]) == len({point["event_date"] for point in result["items"]})
    future = [point for point in result["items"] if point["is_future"]]
    assert len(future) == 14
    assert all(point["observed_cloud"] is None and point["expected_cloud"] == 100 for point in future)
    assert "DATE '2026-10-12'" in calls[1]
    assert '"realizado_cloud"' in calls[1]


def test_agregado_usa_todas_as_series_e_nao_publica_forecast_parcial():
    from app.services.anomaly_series import aggregate_reference_points
    left = derive_daily_points([reference(predicted_cloud=1900)], {}, horizon_end=date(2026, 10, 15))
    right = derive_daily_points([reference(reference_id="second", predicted_cloud=3800)], {}, horizon_end=date(2026, 10, 14))
    result = aggregate_reference_points([left, right])
    assert [point["event_date"] for point in result] == ["2026-10-13", "2026-10-14"]
    assert result[0]["expected_cloud"] == pytest.approx(left[0]["expected_cloud"] + right[0]["expected_cloud"])
    assert result[0]["upper_cloud"] == pytest.approx(left[0]["upper_cloud"] + right[0]["upper_cloud"])
    assert result[0]["contributor_series_count"] == 2


def test_procedencia_do_caso_preserva_membros_sem_copiar_valores_financeiros():
    from types import SimpleNamespace
    from app.services.anomaly_release import contributor_series_for
    detection = SimpleNamespace(series_id="a", series_dimensions={"account": "A"}, contributors=[
        {"series_id": "a", "dimensions": {"account": "A"}, "observed_cloud": 500},
        {"series_id": "b", "dimensions": {"account": "B"}, "observed_cloud": 200},
        {"series_id": "b", "dimensions": {"account": "B"}, "observed_cloud": 200},
    ])
    assert contributor_series_for(detection) == [
        {"series_id": "a", "dimensions": {"account": "A"}},
        {"series_id": "b", "dimensions": {"account": "B"}},
    ]


def test_contribuintes_preservam_organograma_e_reconciliam_desvio(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import app.services.anomaly_series as service
    members = [
        {"series_id": "a", "dimensions": {"subaccountname": "Production", "organograma_nivel_5": "Projeto A"}},
        {"series_id": "b", "dimensions": {"subaccountname": "Development", "organograma_nivel_5": "Sem projeto"}},
    ]
    refs = [reference(series_id=member["series_id"], reference_id=member["series_id"], forecast_run_id=397,
                      data_cutoff="2026-10-11", coverage_start="2026-10-12", covered_days=20,
                      weekday_weights=None, predicted_cloud=2000, target_end="2026-10-26") for member in members]

    async def query(sql, **_kwargs):
        if "SELECT reference_month" in sql:
            return refs
        return [
            {"event_date": "2026-10-12", "observed_cloud": 500, **members[0]["dimensions"]},
            {"event_date": "2026-10-12", "observed_cloud": 300, **members[1]["dimensions"]},
            {"event_date": "2026-10-13", "observed_cloud": 999, **members[1]["dimensions"]},
        ]

    monkeypatch.setattr(service, "trino_execute", query)
    result = asyncio.run(service.read_case_series(
        case=SimpleNamespace(series_id="a", period="202610", event_date="2026-10-12", current_reference_id="a",
                             evaluation_grain="DAILY", series_dimensions=members[0]["dimensions"]),
        publication=SimpleNamespace(data_cutoff="2026-10-11", reference_table="ref"),
        reference_namespace="silver", source_namespace="silver", source_table="focus", date_column="chargeperiodstart",
        period_column="_billing_period", type_column="tipo", measure_column="realizado_cloud",
        dimensions=["subaccountname", "organograma_nivel_5"], analysis_data_end=date(2026, 10, 12), contributor_series=members,
    ))
    rows = result["contributor_breakdown"]
    event = next(point for point in result["items"] if point["event_date"] == "2026-10-12")
    assert [row["observed_cloud"] for row in rows] == [500, 300]
    for key in ("observed_cloud", "expected_cloud", "delta_cloud"):
        assert sum(row[key] for row in rows) == pytest.approx(event[key])
    assert rows[1]["dimensions"]["organograma_nivel_5"] == "Sem projeto"
    for row in rows:
        selected_day = next(point for point in row["items"] if point["event_date"] == row["event_date"])
        assert selected_day["observed_cloud"] == row["observed_cloud"]
        assert selected_day["expected_cloud"] == row["expected_cloud"]
        assert all(point["observed_cloud"] is None for point in row["items"] if point["is_future"])


def test_drilldown_resolve_apenas_dimensoes_disponiveis_e_nao_duplica_grao_forecast():
    from app.services.anomaly_series import resolve_detail_dimensions, detailed_event_sql
    dims = resolve_detail_dimensions(
        ["SubscriptionName", "ResourceGroupName", "ServiceName", "SkuMeter", "ResourceId"],
        ["servicename"],
    )
    assert [(item["key"], item["column"]) for item in dims] == [
        ("subscription", "SubscriptionName"), ("resource_group", "ResourceGroupName"), ("sku", "SkuMeter")
    ]
    sql = detailed_event_sql(source_namespace="silver", source_table="dest_modelo_focus",
        date_column="chargeperiodstart", period_column="_billing_period", type_column="tipo",
        measure_column="realizado_cloud", dimensions=["servicename"], detail_dimensions=dims,
        member_filters=["\"servicename\" = 'Compute'"], event_day=date(2026, 10, 12))
    assert '"SubscriptionName" AS "subscription"' in sql
    assert '"ResourceGroupName" AS "resource_group"' in sql
    assert '"SkuMeter" AS "sku"' in sql
    assert '"ResourceId"' not in sql
    assert "DATE '2026-10-12'" in sql and "realizado_cloud" in sql
