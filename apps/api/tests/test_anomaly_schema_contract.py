"""Contrato de schema Iceberg da gestão de anomalias (E1)."""
import pytest

from app.services.anomaly_schema import (
    ASSESSMENT_TABLE,
    ASSESSMENT_TIPO,
    GOLD_TABLE,
    REFERENCE_TABLE,
    assessment_columns,
    assessment_financial_guards,
    ddl_statements,
    reference_columns,
    table_schemas,
)

CAMPOS_DA_AVALIACAO_DO_PLANO = (
    "assessment_id", "case_id", "series_id", "event_date", "detected_at",
    "evaluation_grain", "forecast_run_id", "reference_id", "reference_kind",
    "observed_cloud", "expected_cloud", "lower_cloud", "upper_cloud",
    "delta_cloud", "anomalous_cost_cloud", "score", "confidence",
    "assessment_status", "reason_codes", "applied_rule_refs",
    "evaluation_start", "evaluation_end", "source_billing_period",
    "is_financial_additive", "tipo",
)


def test_avaliacao_tem_todos_os_campos_do_plano():
    names = {name for name, _ in assessment_columns()}
    assert set(CAMPOS_DA_AVALIACAO_DO_PLANO).issubset(names)
    assert "period" in names  # mês civil do evento
    assert "realizado_cloud" in names
    assert "realizado" in names


def test_linha_de_anomalia_nao_e_aditiva_e_nao_usa_realizado():
    guards = assessment_financial_guards()
    assert guards["tipo"] == ASSESSMENT_TIPO == "anomalia"
    assert guards["is_financial_additive"] is False
    assert guards["realizado_is_null"] is True
    assert guards["realizado_cloud_equals"] == "anomalous_cost_cloud"


def test_referencia_guarda_contrato_pesos_normalizador_e_faixa():
    names = {name for name, _ in reference_columns()}
    for required in (
        "forecast_run_id", "forecast_config_version", "source_snapshot_id",
        "data_cutoff", "generated_at", "target_start", "target_end",
        "reference_kind", "applied_rule_refs",
        "predicted_cloud", "coverage_kind", "covered_days",
        "weekday_weights", "normalizer", "distribution_method",
        "band_lower_cloud", "band_upper_cloud", "band_method",
    ):
        assert required in names, f"referência sem {required}"


def test_referencia_e_compacta_por_mes_nao_por_dia():
    """Uma linha por mês alcançado: não existe coluna de grade diária."""
    names = {name for name, _ in reference_columns()}
    for forbidden in ("days", "daily_grid", "day_index", "bucket", "forecast_day"):
        assert forbidden not in names
    assert "reference_month" in names
    assert "covered_days" in names  # contagem, não a grade


def test_avaliacao_preserva_dimensoes_compativeis_com_planning():
    names = {name for name, _ in assessment_columns(9)}
    for dim in (
        "cloud_origem", "servicename", "subaccountname", "centro_custo",
        "hierarquia_1", "hierarquia_9", "chargecategory",
        "pricingcategory", "commitmentdiscounttype",
    ):
        assert dim in names
    # Sem limite fixo: só existem os níveis da profundidade pedida.
    assert "hierarquia_10" not in names


def test_nomes_de_tabela_sao_os_do_plano():
    assert set(table_schemas()) == {
        f"silver.{REFERENCE_TABLE}",
        f"silver.{ASSESSMENT_TABLE}",
        f"gold.{GOLD_TABLE}",
    }


def test_ddl_e_idempotente_e_cobre_as_tres_tabelas():
    statements = ddl_statements()
    assert len(statements) == 3
    for statement in statements:
        assert statement.startswith("CREATE TABLE IF NOT EXISTS iceberg.")
    assert "dest_modelo_focus_anomaly_reference" in statements[0]
    assert "dest_modelo_focus_anomalies" in statements[1]
    assert "dest_dm_cost_anomalies" in statements[2]


@pytest.mark.parametrize("sql_index", [0, 1, 2])
def test_ddl_declara_tipos_suportados_pelo_trino(sql_index):
    allowed = {
        "varchar", "double", "bigint", "boolean",
        "timestamp(6) with time zone",
    }
    statement = ddl_statements()[sql_index]
    for line in statement.splitlines():
        line = line.strip().rstrip(",")
        if not line.startswith('"'):
            continue
        _, _, ctype = line.partition('" ')
        assert ctype.strip() in allowed, f"tipo inesperado no DDL: {line}"
