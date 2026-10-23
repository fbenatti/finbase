"""Testes das consultas publicadas: eventos esperados e qualidade (F4)."""
import pytest

from app.services.anomaly_queries import (
    EXPECTED_EVENT_STATUS,
    QUALITY_REASON_CODES,
    assessments_sql,
    bucket_quality,
    count_sql,
    group_expected_events,
)


def row(**overrides):
    base = {
        "assessment_id": "a1",
        "case_id": None,
        "series_id": "s1",
        "event_date": "2026-10-05",
        "evaluation_grain": "MONTHLY_EVENT",
        "period": "202610",
        "source_billing_period": "202610",
        "observed_cloud": 30_000.0,
        "expected_cloud": 30_000.0,
        "lower_cloud": 25_500.0,
        "upper_cloud": 34_500.0,
        "delta_cloud": 0.0,
        "anomalous_cost_cloud": 0.0,
        "score": 0.0,
        "confidence": "LOW",
        "assessment_status": EXPECTED_EVENT_STATUS,
        "reason_codes": '["EXPECTED_MONTHLY_EVENT", "EXPLICIT_TOLERANCE"]',
        "reference_kind": "PROSPECTIVE",
        "reference_id": "ref-1",
        "forecast_run_id": 399,
        "cloud_origem": "azure-dev",
        "servicename": "Microsoft.Capacity",
        "chargecategory": "Purchase",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------
def test_sql_de_avaliacoes_filtra_status_periodo_e_pagina():
    sql = assessments_sql(
        namespace="silver",
        table="dest_billing_cloud_anomalies",
        statuses=[EXPECTED_EVENT_STATUS],
        period="202610",
        limit=25,
        offset=50,
    )
    assert 'FROM iceberg.silver."dest_billing_cloud_anomalies"' in sql
    assert f"'{EXPECTED_EVENT_STATUS}'" in sql
    assert "'202610'" in sql
    assert "OFFSET 50 LIMIT 25" in sql
    assert "ORDER BY CAST(event_date AS VARCHAR) DESC" in sql


def test_sql_de_avaliacoes_respeita_o_teto_da_pagina():
    sql = assessments_sql(namespace="silver", table="t", statuses=["OPEN"], limit=9999, offset=-5)
    assert "LIMIT 200" in sql


def test_sql_de_avaliacoes_filtra_por_codigo_de_razao():
    sql = assessments_sql(
        namespace="silver", table="t", reason_code="PARTIAL_DATA", limit=10, offset=0
    )
    assert "reason_codes AS VARCHAR) LIKE '%PARTIAL_DATA%'" in sql


def test_sql_de_contagem():
    sql = count_sql(namespace="silver", table="t", statuses=[EXPECTED_EVENT_STATUS], period="202610")
    assert "COUNT(*) AS total" in sql
    assert "'EXPECTED_EVENT'" in sql and "'202610'" in sql


def test_sql_de_avaliacoes_projeta_dimensoes_do_plano():
    sql = assessments_sql(namespace="silver", table="t", statuses=["OPEN"], limit=10, offset=0)
    for column in ("cloud_origem", "servicename", "centro_custo", "hierarquia_1", "chargecategory"):
        assert f'"{column}"' in sql
    assert "assessment_id" in sql and "anomalous_cost_cloud" in sql


# ---------------------------------------------------------------------------
# Agrupamento de eventos esperados
# ---------------------------------------------------------------------------
def test_agrupa_recorrencias_por_familia():
    rows = [
        row(assessment_id="a1", series_id="s1", period="202608", event_date="2026-08-05",
            expected_cloud=30_000.0, observed_cloud=30_000.0),
        row(assessment_id="a2", series_id="s1", period="202609", event_date="2026-09-05",
            expected_cloud=30_000.0, observed_cloud=30_500.0),
        row(assessment_id="a3", series_id="s2", period="202609", event_date="2026-09-05",
            servicename="Microsoft.Storage", expected_cloud=5_000.0, observed_cloud=5_000.0),
    ]
    items = group_expected_events(rows)
    by_family = {item["family"]: item for item in items}
    virtual = by_family["azure-dev|Microsoft.Capacity|Purchase"]
    assert virtual["occurrences"] == 2
    assert virtual["series_count"] == 1
    assert virtual["expected_cloud"] == pytest.approx(60_000.0)
    assert virtual["observed_cloud"] == pytest.approx(60_500.0)
    assert virtual["delta_cloud"] == pytest.approx(500.0)
    assert virtual["months"] == ["202608", "202609"]
    assert virtual["last_event_date"] == "2026-09-05"
    assert virtual["reason_codes"] == ["EXPECTED_MONTHLY_EVENT", "EXPLICIT_TOLERANCE"]
    assert by_family["azure-dev|Microsoft.Storage|Purchase"]["occurrences"] == 1


def test_ordenacao_por_numero_de_ocorrencias():
    rows = [
        row(assessment_id="a1", servicename="A"),
        row(assessment_id="a2", servicename="B"),
        row(assessment_id="a3", servicename="B"),
    ]
    items = group_expected_events(rows)
    assert items[0]["occurrences"] == 2
    assert items[0]["family"].endswith("B|Purchase")


def test_lista_vazia_nao_quebra():
    assert group_expected_events([]) == []


def test_familia_cai_para_series_id_sem_dimensoes():
    rows = [row(cloud_origem=None, servicename=None, chargecategory=None, series_id="apenas-serie")]
    items = group_expected_events(rows)
    assert items[0]["family"] == "apenas-serie"


def test_codigos_de_razao_invalidos_sao_ignorados():
    rows = [row(reason_codes="não é json", assessment_id="a1")]
    items = group_expected_events(rows)
    assert items[0]["reason_codes"] == ["não é json"]


# ---------------------------------------------------------------------------
# Qualidade
# ---------------------------------------------------------------------------
def test_qualidade_conta_por_codigo_declarado():
    rows = [
        row(assessment_id="a1", reason_codes='["PARTIAL_DATA"]', anomalous_cost_cloud=100.0, period="202610"),
        row(assessment_id="a2", reason_codes='["PARTIAL_DATA", "EXPLICIT_TOLERANCE"]',
            anomalous_cost_cloud=50.0, period="202611"),
        row(assessment_id="a3", reason_codes='["WITHIN_BAND"]', anomalous_cost_cloud=0.0),
    ]
    items = bucket_quality(rows)
    by_code = {item["reason_code"]: item for item in items}
    assert by_code["PARTIAL_DATA"]["count"] == 2
    assert by_code["PARTIAL_DATA"]["anomalous_cost_cloud"] == pytest.approx(150.0)
    assert by_code["PARTIAL_DATA"]["periods"] == ["202610", "202611"]
    assert by_code["EXPLICIT_TOLERANCE"]["count"] == 1
    # Códigos fora do vocabulário de qualidade não entram.
    assert "WITHIN_BAND" not in by_code


def test_qualidade_ordena_por_frequencia():
    rows = [
        row(assessment_id="a1", reason_codes='["EXPLICIT_TOLERANCE"]'),
        row(assessment_id="a2", reason_codes='["PARTIAL_DATA"]'),
        row(assessment_id="a3", reason_codes='["PARTIAL_DATA"]'),
    ]
    items = bucket_quality(rows)
    assert items[0]["reason_code"] == "PARTIAL_DATA"
    assert items[0]["count"] == 2


def test_vocabulario_de_qualidade_declarado():
    for code in (
        "PARTIAL_DATA", "REFERENCE_UNAVAILABLE", "EXPLICIT_TOLERANCE",
        "UNIFORM_FALLBACK", "FORECAST_RULE_MISMATCH", "NO_APPLICABLE_FORECAST_RULE",
        "CALENDAR_SHIFT_EVIDENCE", "FEW_MONTHLY_EVENTS",
    ):
        assert code in QUALITY_REASON_CODES
