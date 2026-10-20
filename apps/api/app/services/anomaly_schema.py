"""Contrato de schema das tabelas Iceberg da gestão de anomalias (E1).

Os nomes são os do plano (§4.2, §4.3) e ficam em um único lugar para que o
escritor (worker, E6), o leitor (API) e o dashboard (E9) não divirjam:

```text
silver.dest_modelo_focus_anomaly_reference   referências compactas, imutáveis
silver.dest_modelo_focus_anomalies           avaliações compatíveis com Planning
gold.dest_dm_cost_anomalies                  avaliação financeira consolidada
```

Regras de contrato preservadas:

* a referência guarda **uma linha por forecast × série × mês** alcançado pelo
  horizonte — a grade de 15 pontos é derivada, nunca persistida para todas as
  execuções;
* a avaliação preserva as dimensões e os tipos compatíveis com Planning;
* ``realizado_cloud`` da linha ``tipo='anomalia'`` representa
  ``anomalous_cost_cloud``; ``realizado`` fica nulo; a linha **não** é somada ao
  custo cloud observado (``is_financial_additive = false``).
"""
from __future__ import annotations

from typing import Any, Iterable

REFERENCE_NAMESPACE = "silver"
REFERENCE_TABLE = "dest_modelo_focus_anomaly_reference"
ASSESSMENT_NAMESPACE = "silver"
ASSESSMENT_TABLE = "dest_modelo_focus_anomalies"
GOLD_NAMESPACE = "gold"
GOLD_TABLE = "dest_dm_cost_anomalies"

#: Dimensões de série compatíveis com Planning, na ordem canônica.
SERIES_DIMENSIONS: tuple[str, ...] = (
    "cloud_origem",
    "marketplaceproductname",
    "marketplacebillingmodel",
    "servicename",
    "subaccountname",
    "centro_custo",
    "organograma_nivel_1",
    "organograma_nivel_2",
    "organograma_nivel_3",
    "organograma_nivel_4",
    "organograma_nivel_5",
    "organograma_nivel_6",
    "organograma_nivel_7",
    "chargecategory",
    "serviceprovidername",
    "servicesubcategory",
    "pricingcategory",
    "commitmentdiscounttype",
)

ColumnDef = tuple[str, str]

#: Identificação e procedência da referência (plano §2.2).
REFERENCE_CONTRACT_COLUMNS: tuple[ColumnDef, ...] = (
    ("reference_id", "varchar"),
    ("forecast_run_id", "bigint"),
    ("forecast_config_version", "bigint"),
    ("source_snapshot_id", "varchar"),
    ("data_cutoff", "varchar"),
    ("generated_at", "timestamp(6) with time zone"),
    ("target_start", "varchar"),
    ("target_end", "varchar"),
    ("reference_kind", "varchar"),
    ("applied_rule_refs", "varchar"),
    ("config_version", "bigint"),
)

#: Conteúdo matemático da referência (plano §4.2).
REFERENCE_VALUE_COLUMNS: tuple[ColumnDef, ...] = (
    ("series_id", "varchar"),
    ("reference_month", "varchar"),
    # FULL_MONTH | REMAINING_MONTH
    ("coverage_kind", "varchar"),
    ("coverage_start", "varchar"),
    ("coverage_end", "varchar"),
    ("covered_days", "bigint"),
    ("predicted_cloud", "double"),
    ("weekday_weights", "varchar"),
    ("normalizer", "double"),
    ("distribution_method", "varchar"),
    ("band_lower_cloud", "double"),
    ("band_upper_cloud", "double"),
    ("band_method", "varchar"),
    ("band_confidence", "varchar"),
    # Faixa aplicada a um único dia coberto (detector de consumo diário).
    # A faixa do total do mês fica em band_lower_cloud / band_upper_cloud.
    ("daily_band_lower_offset", "double"),
    ("daily_band_upper_offset", "double"),
    ("band_alpha", "double"),
    ("band_sample_size", "bigint"),
    ("band_min_sample", "bigint"),
    ("history_days", "bigint"),
    ("calibration_days", "bigint"),
    ("sample_size", "bigint"),
    ("partial_reference", "boolean"),
)

#: Avaliação (plano §4.3).
ASSESSMENT_COLUMNS: tuple[ColumnDef, ...] = (
    ("tipo", "varchar"),
    ("assessment_id", "varchar"),
    ("case_id", "bigint"),
    ("series_id", "varchar"),
    ("event_date", "varchar"),
    ("detected_at", "timestamp(6) with time zone"),
    ("evaluation_grain", "varchar"),
    ("forecast_run_id", "bigint"),
    ("reference_id", "varchar"),
    ("reference_kind", "varchar"),
    ("observed_cloud", "double"),
    ("expected_cloud", "double"),
    ("lower_cloud", "double"),
    ("upper_cloud", "double"),
    ("delta_cloud", "double"),
    ("anomalous_cost_cloud", "double"),
    ("score", "double"),
    ("confidence", "varchar"),
    ("assessment_status", "varchar"),
    ("reason_codes", "varchar"),
    ("applied_rule_refs", "varchar"),
    ("evaluation_start", "varchar"),
    ("evaluation_end", "varchar"),
    ("source_billing_period", "varchar"),
    ("is_financial_additive", "boolean"),
    # `period` é o mês civil do evento, para agrupamento analítico.
    ("period", "varchar"),
    # Compatibilidade com Planning (plano §4.3).
    ("realizado_cloud", "double"),
    ("realizado", "double"),
)

ASSESSMENT_TIPO = "anomalia"


def _dimension_columns() -> list[ColumnDef]:
    return [(name, "varchar") for name in SERIES_DIMENSIONS]


def reference_columns() -> list[ColumnDef]:
    """Colunas da tabela de referência: dimensões + contrato + conteúdo."""
    return (
        list(REFERENCE_CONTRACT_COLUMNS)
        + _dimension_columns()
        + list(REFERENCE_VALUE_COLUMNS)
    )


def assessment_columns() -> list[ColumnDef]:
    """Colunas da tabela de avaliações: dimensões + campos do plano §4.3."""
    return _dimension_columns() + list(ASSESSMENT_COLUMNS)


def gold_columns() -> list[ColumnDef]:
    """Consolidação financeira do caso (mesmo contrato, sem grade diária)."""
    return _dimension_columns() + list(ASSESSMENT_COLUMNS) + [
        ("case_state", "varchar"),
        ("severity", "varchar"),
        ("case_version", "bigint"),
    ]


def table_schemas() -> dict[str, list[ColumnDef]]:
    return {
        f"{REFERENCE_NAMESPACE}.{REFERENCE_TABLE}": reference_columns(),
        f"{ASSESSMENT_NAMESPACE}.{ASSESSMENT_TABLE}": assessment_columns(),
        f"{GOLD_NAMESPACE}.{GOLD_TABLE}": gold_columns(),
    }


def create_table_sql(namespace: str, table: str, columns: Iterable[ColumnDef] | None = None) -> str:
    """DDL Trino idempotente para uma das tabelas do contrato."""
    cols = list(columns) if columns is not None else table_schemas()[f"{namespace}.{table}"]
    rendered = ",\n    ".join(f'"{name}" {ctype}' for name, ctype in cols)
    return f'CREATE TABLE IF NOT EXISTS iceberg.{namespace}.{table} (\n    {rendered}\n)'


def ddl_statements() -> list[str]:
    """As três tabelas, na ordem de dependência."""
    return [
        create_table_sql(REFERENCE_NAMESPACE, REFERENCE_TABLE),
        create_table_sql(ASSESSMENT_NAMESPACE, ASSESSMENT_TABLE),
        create_table_sql(GOLD_NAMESPACE, GOLD_TABLE),
    ]


def assessment_financial_guards() -> dict[str, Any]:
    """Invariantes financeiras validadas antes de publicar (E6).

    A linha de anomalia usa ``realizado_cloud`` como custo anômalo, deixa
    ``realizado`` nulo e nunca é aditiva ao custo financeiro.
    """
    return {
        "tipo": ASSESSMENT_TIPO,
        "realizado_is_null": True,
        "realizado_cloud_equals": "anomalous_cost_cloud",
        "is_financial_additive": False,
    }
