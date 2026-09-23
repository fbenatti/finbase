"""Testes da classificação de competência (G1).

Cada caso corresponde a um período real medido em 23/09/2026. O objetivo é que
nenhum desses períodos volte a alimentar MoM, run rate ou forecast em silêncio.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.services.period_coverage import (
    COMPLETE,
    CURRENT_OPEN,
    EMPTY,
    MISSING,
    NOT_APPLICABLE,
    PARTIAL,
    REASON_CURRENT_PERIOD,
    REASON_MISSING_DIMENSIONS,
    REASON_NO_EXPECTED_LOAD,
    REASON_NO_ROWS,
    REASON_NO_RUN,
    REASON_PURCHASE_ONLY,
    REASON_RECONCILIATION,
    REASON_RUN_FAILED,
    REASON_SHORT_WINDOW,
    PeriodFacts,
    classify_all,
    classify_period,
    comparable_pairs,
    coverage_sql,
    days_in_period,
    facts_from_row,
    freshness_of,
    month_over_month,
    run_rate_annualized,
)

HOJE = date(2026, 9, 23)


def facts(**kw):
    """Constrói `PeriodFacts` a partir de uma janela em dias.

    O helper traduz ``days_covered`` em `first_charge_day`/`last_charge_day`, que
    é como o coletor real mede a janela — `charge_period_start` tem poucos valores
    distintos por mês (dia da fatura, não dia de consumo).
    """
    periodo = kw.pop("billing_period", "202601")
    dias = kw.pop("days_covered", 30)
    esperado = kw.pop("days_expected", None)
    base = dict(
        connection_code="azure-4p",
        billing_period=periodo,
        row_count=790_406,
        cost=3_827_009.90,
    )
    base.update(kw)
    if esperado is None:
        esperado = days_in_period(periodo)
    base["days_expected"] = esperado
    if "first_charge_day" not in base:
        if dias <= 0:
            base["first_charge_day"] = None
            base["last_charge_day"] = None
        else:
            inicio = date(int(periodo[:4]), int(periodo[4:6]), 1)
            base["first_charge_day"] = inicio
            base["last_charge_day"] = inicio.fromordinal(inicio.toordinal() + dias - 1)
    return PeriodFacts(**base)


# --- dias do mês --------------------------------------------------------------


@pytest.mark.parametrize(
    "periodo,dias",
    [("202601", 31), ("202602", 28), ("202604", 30), ("202412", 31), ("202502", 28)],
)
def test_days_in_period(periodo, dias):
    assert days_in_period(periodo) == dias


@pytest.mark.parametrize("periodo", ["", "abc", "202513", "2025"])
def test_days_in_period_invalid(periodo):
    assert days_in_period(periodo) == 0


# --- casos reais do baseline -------------------------------------------------

def test_202504_is_partial_short_window():
    """3.763.016 linhas, R$ 209.564,67, 1 dia de consumo — carga de ajuste."""
    c = classify_period(
        facts(
            billing_period="202504",
            row_count=3_763_016,
            days_covered=1,
            days_expected=30,
            cost=209_564.67,
        )
    )
    assert c.status == PARTIAL
    assert c.reason == REASON_SHORT_WINDOW
    assert c.comparable is False


def test_202505_is_partial_missing_dimensions():
    """1.575.747 linhas sem região (R$ 557.990,51 de custo sem região)."""
    razao = 1_575_747 / 2_261_106
    c = classify_period(
        facts(
            billing_period="202505",
            row_count=2_261_106,
            days_covered=28,
            days_expected=31,
            missing_dimension_ratio=razao,
        )
    )
    assert c.reason == REASON_MISSING_DIMENSIONS
    assert c.comparable is False


@pytest.mark.parametrize(
    "periodo,linhas,custo",
    [
        ("202604", 51, 194_941.66),
        ("202605", 1, 146_346.00),
        ("202606", 1, 147_865.00),
        ("202607", 3, 0.00),
    ],
)
def test_purchase_only_months_are_partial(periodo, linhas, custo):
    """51/1/1/3 linhas, só `Microsoft.Capacity`: não são meses operacionais."""
    # Janela cheia do mês e ainda assim não comparável: é ordem de reserva, não
    # mês operacional. O motivo precisa ser próprio, e não "janela curta".
    c = classify_period(
        facts(
            billing_period=periodo,
            row_count=linhas,
            days_covered=days_in_period(periodo),
            cost=custo,
            purchase_only=True,
        )
    )
    assert c.status == PARTIAL
    assert c.reason == REASON_PURCHASE_ONLY
    assert c.comparable is False


def test_azure_ti_202609_is_current_open():
    """Mês em andamento: pode ser exibido, nunca base de tendência."""
    c = classify_period(
        facts(
            connection_code="azure-ti",
            billing_period="202609",
            row_count=548_606,
            days_covered=1,
            days_expected=30,
            cost=1_987_748.46,
            is_current_period=True,
            age_days=0,
        )
    )
    assert c.status == CURRENT_OPEN
    assert c.reason == REASON_CURRENT_PERIOD
    assert c.comparable is False
    assert c.displayable is True


def test_complete_month_is_comparable():
    c = classify_period(facts())
    assert c.status == COMPLETE
    assert c.comparable is True


# --- ordem das precedências ---------------------------------------------------

def test_failed_run_beats_content_analysis():
    """Sem execução confiável o conteúdo não é evidência."""
    c = classify_period(facts(run_status="FAILED"))
    assert c.status == PARTIAL
    assert c.reason == REASON_RUN_FAILED


def test_no_expected_load_is_not_applicable_not_missing():
    c = classify_period(facts(has_expected_load=False, row_count=0))
    assert c.status == NOT_APPLICABLE
    assert c.reason == REASON_NO_EXPECTED_LOAD
    assert c.comparable is False


def test_no_run_and_no_rows_is_missing():
    c = classify_period(facts(row_count=0, run_status=None))
    assert c.status == MISSING
    assert c.reason == REASON_NO_RUN


def test_successful_run_with_zero_rows_is_empty_not_missing():
    """Carga válida com zero linhas é estado legítimo, não ausência."""
    c = classify_period(facts(row_count=0, run_status="SUCCESS"))
    assert c.status == EMPTY
    assert c.reason == REASON_NO_ROWS


def test_empty_is_not_comparable():
    assert classify_period(facts(row_count=0, run_status="SUCCESS")).comparable is False


def test_missing_is_not_comparable():
    assert classify_period(facts(row_count=0, run_status=None)).comparable is False


def test_not_applicable_is_not_comparable():
    assert classify_period(facts(has_expected_load=False)).comparable is False


def test_reconciliation_divergence_makes_partial():
    c = classify_period(facts(reconciliation_ratio=0.02))
    assert c.status == PARTIAL
    assert c.reason == REASON_RECONCILIATION


def test_day_coverage_boundary_is_80_percent():
    """80 % exato é comparável; 79 % não."""
    ok = classify_period(facts(days_covered=25, days_expected=31))
    assert ok.status == COMPLETE
    ruim = classify_period(facts(days_covered=24, days_expected=31))
    assert ruim.reason == REASON_SHORT_WINDOW


def test_missing_dimension_boundary_is_5_percent():
    ok = classify_period(facts(missing_dimension_ratio=0.05))
    assert ok.status == COMPLETE
    ruim = classify_period(facts(missing_dimension_ratio=0.0501))
    assert ruim.reason == REASON_MISSING_DIMENSIONS


# --- outlier ------------------------------------------------------------------

def test_outlier_is_warning_not_partial():
    """Anomalia é sinal de negócio a explicar, não defeito de medição.

    O período continua comparável: excluí-lo do MoM esconderia exatamente a
    variação que o usuário precisa investigar.
    """
    c = classify_period(
        facts(
            cost=1_000_000.0,
            residual_history_median=3_300_000.0,
            residual_history_mad=200_000.0,
            residual_history_n=12,
        )
    )
    assert c.status == COMPLETE
    assert c.comparable is True
    assert any("atipica" in w for w in c.warnings)


def test_outlier_does_not_change_the_number():
    """O aviso não altera o custo nem o status comparável do período."""
    normal = classify_period(facts(cost=3_200_000.0))
    anomalo = classify_period(
        facts(
            cost=1_000_000.0,
            residual_history_median=3_300_000.0,
            residual_history_mad=200_000.0,
            residual_history_n=12,
        )
    )
    assert anomalo.cost == 1_000_000.0
    assert normal.comparable and anomalo.comparable


def test_normal_variation_is_not_flagged():
    c = classify_period(
        facts(
            cost=3_100_000.0,
            residual_history_median=3_300_000.0,
            residual_history_mad=200_000.0,
            residual_history_n=12,
        )
    )
    assert c.status == COMPLETE
    assert not any("atipica" in w for w in c.warnings)


def test_outlier_needs_history():
    """Sem histórico não há outlier — não se inventa referência."""
    assert classify_period(facts(cost=1.0)).status == COMPLETE
    assert (
        classify_period(
            facts(cost=1.0, residual_history_median=3_300_000.0, residual_history_mad=1.0,
                  residual_history_n=2)
        ).status
        == COMPLETE
    )


# --- classificação em lote ----------------------------------------------------

def test_classify_all_computes_history_per_connection():
    """O histórico é por conexão e exclui meses de ajuste e mês corrente."""
    fatos = [
        facts(billing_period="202601", cost=3_800_000.0, days_covered=31),
        facts(billing_period="202602", cost=3_450_000.0, days_covered=28),
        facts(billing_period="202603", cost=3_265_000.0, days_covered=30),
        facts(billing_period="202604", row_count=51, days_covered=1, cost=194_941.66),
    ]
    coberturas = {c.billing_period: c for c in classify_all(fatos)}
    assert coberturas["202601"].status == COMPLETE
    assert coberturas["202604"].status == PARTIAL


def test_classify_all_does_not_mix_connections():
    fatos = [
        facts(connection_code="azure-4p", billing_period="202601", cost=3_800_000.0),
        facts(connection_code="azure-ti", billing_period="202609", cost=1_987_748.46),
    ]
    coberturas = classify_all(fatos)
    assert {c.connection_code for c in coberturas} == {"azure-4p", "azure-ti"}


# --- pares comparáveis --------------------------------------------------------

def test_comparable_pairs_skips_incomplete_periods():
    """`202504 → 202505` e `202603 → 202604` não podem virar tendência."""
    fatos = [
        facts(billing_period="202504", row_count=3_763_016, days_covered=1, cost=209_564.67),
        facts(billing_period="202505", row_count=2_261_106, days_covered=28, cost=3_177_030.65),
        facts(billing_period="202601", cost=3_800_000.0, days_covered=31),
        facts(billing_period="202602", cost=3_450_000.0, days_covered=28),
        facts(billing_period="202603", cost=3_265_000.0, days_covered=30),
        facts(billing_period="202604", row_count=51, days_covered=1, cost=194_941.66),
    ]
    pares = comparable_pairs(classify_all(fatos))
    periodos = {(a.billing_period, b.billing_period) for a, b in pares}
    assert ("202504", "202505") not in periodos
    assert ("202603", "202604") not in periodos
    assert ("202601", "202602") in periodos
    assert ("202602", "202603") in periodos


def test_comparable_pairs_never_crosses_connections():
    fatos = [
        facts(connection_code="azure-4p", billing_period="202601", cost=3_800_000.0),
        facts(connection_code="azure-ti", billing_period="202602", cost=1_900_000.0),
    ]
    assert comparable_pairs(classify_all(fatos)) == []


# --- MoM ----------------------------------------------------------------------

def test_mom_refuses_partial_period():
    """Comparar período parcial não devolve número — devolve o motivo."""
    anterior = classify_period(
        facts(billing_period="202504", row_count=3_763_016, days_covered=1, cost=209_564.67)
    )
    atual = classify_period(facts(billing_period="202505", days_covered=28, cost=3_177_030.65))
    resultado = month_over_month(anterior, atual)
    assert resultado["comparable"] is False
    assert resultado["reason"] == "periodo_nao_comparavel"


def test_mom_computes_between_comparable_months():
    anterior = classify_period(facts(billing_period="202601", cost=3_800_000.0, days_covered=31))
    atual = classify_period(facts(billing_period="202602", cost=3_450_000.0, days_covered=28))
    resultado = month_over_month(anterior, atual)
    assert resultado["comparable"] is True
    assert resultado["delta"] == pytest.approx(-350_000.0)
    assert resultado["delta_pct"] == pytest.approx(-9.2105, abs=0.01)


def test_mom_refuses_zero_base():
    anterior = classify_period(facts(billing_period="202601", cost=0.0, days_covered=31))
    atual = classify_period(facts(billing_period="202602", cost=100.0, days_covered=28))
    assert month_over_month(anterior, atual)["reason"] == "base_zero"


# --- run rate -----------------------------------------------------------------

def test_run_rate_reports_month_count():
    """Um run rate de 1 mês não é comparável a um de 12 — o número é parte do resultado."""
    coberturas = classify_all(
        [
            facts(billing_period="202601", cost=3_800_000.0, days_covered=31),
            facts(billing_period="202602", cost=3_400_000.0, days_covered=28),
        ]
    )
    resultado = run_rate_annualized(coberturas)
    assert resultado["months_used"] == 2
    assert resultado["average_monthly"] == pytest.approx(3_600_000.0)
    assert resultado["annualized"] == pytest.approx(43_200_000.0)


def test_run_rate_unavailable_without_comparable_period():
    coberturas = classify_all(
        [facts(billing_period="202504", row_count=3_763_016, days_covered=1, cost=209_564.67)]
    )
    resultado = run_rate_annualized(coberturas)
    assert resultado["available"] is False
    assert resultado["months_used"] == 0


def test_run_rate_refuses_multiple_connections():
    """4P e TI têm escopos diferentes: um run rate único seria uma soma inválida."""
    coberturas = classify_all(
        [
            facts(connection_code="azure-4p", billing_period="202601", cost=3_800_000.0),
            facts(connection_code="azure-ti", billing_period="202602", cost=1_900_000.0),
        ]
    )
    resultado = run_rate_annualized(coberturas)
    assert resultado["available"] is False
    assert resultado["reason"] == "multiplas_conexoes"


# --- inventário de competências para a UI -------------------------------------


def test_coverage_row_shape():
    c = classify_period(facts())
    linha = c.as_row()
    for campo in (
        "connection_code",
        "billing_period",
        "dataset",
        "status",
        "reason",
        "comparable",
        "displayable",
        "rule_version",
    ):
        assert campo in linha
    assert isinstance(linha["comparable"], bool)


def test_rule_version_is_pinned():
    """Mudar limiar exige mudar a versão — o consumidor precisa saber."""
    from app.services.period_coverage import COVERAGE_RULE_VERSION

    assert COVERAGE_RULE_VERSION


# --- coleta -------------------------------------------------------------------

def test_facts_from_row_uses_measured_values():
    linha = {
        "connection_code": "azure-4p",
        "billing_period": "202505",
        "row_count": 2_261_106,
        "days_covered": 10,
        "cost": "3177030.65",
        "rows_missing_dimension": 1_575_747,
        "purchase_rows": 0,
        "last_processed_at": "2026-09-22 17:53:38",
    }
    fatos = facts_from_row(linha, today=HOJE)
    assert fatos.cost == pytest.approx(3_177_030.65)
    assert fatos.days_expected == 31
    assert fatos.missing_dimension_ratio == pytest.approx(1_575_747 / 2_261_106)
    assert fatos.purchase_only is False
    assert fatos.age_days == 1


def test_facts_from_row_missing_timestamp_is_unknown_age():
    fatos = facts_from_row(
        {"connection_code": "x", "billing_period": "202601", "row_count": 5}, today=HOJE
    )
    assert fatos.age_days is None


def test_facts_from_row_detects_purchase_only():
    fatos = facts_from_row(
        {
            "connection_code": "azure-4p",
            "billing_period": "202605",
            "row_count": 1,
            "days_covered": 1,
            "purchase_rows": 1,
        },
        today=HOJE,
    )
    assert fatos.purchase_only is True
    assert classify_period(fatos).status == PARTIAL


def test_facts_from_row_flags_current_period():
    fatos = facts_from_row(
        {"connection_code": "azure-ti", "billing_period": "202609", "row_count": 548_606},
        today=HOJE,
    )
    assert fatos.is_current_period is True
    assert classify_period(fatos).status == CURRENT_OPEN


def test_coverage_sql_has_no_free_text_and_targets_silver():
    sql = coverage_sql(dataset="resource_cost")
    assert "iceberg.silver.resource_cost" in sql
    assert "GROUP BY" in sql
    assert "COUNT(DISTINCT DATE(charge_period_start))" in sql


def test_coverage_sql_filters_connection_by_literal_list():
    sql = coverage_sql(connection_filter=["azure-4p"])
    assert "'azure-4p'" in sql


def test_coverage_sql_escapes_quote_in_filter():
    sql = coverage_sql(connection_filter=["o'brien"])
    assert "'o''brien'" in sql


# --- freshness ----------------------------------------------------------------


def test_freshness_reports_age_and_missing():
    agora = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
    resultado = freshness_of(
        [
            {"last_processed_at": "2026-09-22T17:53:38+00:00"},
            {"last_processed_at": None},
        ],
        now=agora,
    )
    assert resultado["available"] is True
    assert resultado["age_days"] == 0
    assert resultado["rows_without_timestamp"] == 1


def test_freshness_without_timestamp_is_not_zero_age():
    resultado = freshness_of([{"last_processed_at": None}])
    assert resultado["available"] is False
    assert resultado["reason"] == "sem_timestamp_de_processamento"


# --- regex de sentinela --------------------------------------------------------


def test_sentinels_do_not_become_dimension_values():
    """Uma sentinela textual não pode virar categoria financeira."""
    for sentinela in ("null", "NULL", "None", "undefined", ""):
        fatos = facts_from_row(
            {"connection_code": sentinela, "billing_period": "202601", "row_count": 1},
            today=HOJE,
        )
        assert fatos.connection_code == "UNKNOWN"


# --- guarda de reconciliação --------------------------------------------------


def test_reconciliation_accepts_equal_totals():
    from app.services.period_coverage import reconciliation_ok

    ok, problemas = reconciliation_ok(
        [{"bp": "202601", "total_delta": 0.0, "rows_delta": 0}]
    )
    assert ok is True
    assert problemas == []


def test_reconciliation_accepts_delta_within_one_cent():
    from app.services.period_coverage import reconciliation_ok

    ok, _ = reconciliation_ok([{"bp": "202601", "total_delta": 0.01, "rows_delta": 0}])
    assert ok is True


def test_reconciliation_rejects_cent_above_tolerance():
    from app.services.period_coverage import reconciliation_ok

    ok, problemas = reconciliation_ok([{"bp": "202601", "total_delta": 0.02, "rows_delta": 0}])
    assert ok is False
    assert "202601" in problemas[0]


def test_reconciliation_rejects_row_delta_even_when_total_matches():
    """Total igual com linha perdida é coincidência, não conformidade."""
    from app.services.period_coverage import reconciliation_ok

    ok, problemas = reconciliation_ok([{"bp": "202601", "total_delta": 0.0, "rows_delta": -3}])
    assert ok is False
    assert "linhas" in problemas[0]


def test_reconciliation_reports_every_failing_period():
    from app.services.period_coverage import reconciliation_ok

    ok, problemas = reconciliation_ok(
        [
            {"bp": "202504", "total_delta": 0.0, "rows_delta": 0},
            {"bp": "202505", "total_delta": 12.5, "rows_delta": 0},
            {"bp": "202506", "total_delta": 0.0, "rows_delta": 1},
        ]
    )
    assert ok is False
    assert len(problemas) == 2


def test_reconciliation_sql_groups_by_dimension_when_given():
    from app.services.period_coverage import reconciliation_sql

    sql = reconciliation_sql(dimensions={"service_key": "LOWER(service_name)"})
    assert "service_key" in sql
    assert "grouped_total" in sql
    assert "rows_grouped" in sql


def test_reconciliation_sql_without_dimensions_proves_global_total():
    from app.services.period_coverage import reconciliation_sql

    sql = reconciliation_sql()
    assert "grouped_total" in sql
    assert "raw_total" in sql
