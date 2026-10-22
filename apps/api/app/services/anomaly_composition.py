"""Composição da tabela irmã de anomalias com a Planning (E6).

Contrato (plano §4.4):

* a Planning **não** é alterada;
* as avaliações vivem em tabela própria e são compostas por **projeção
  explícita** e ``UNION ALL``;
* totais financeiros existentes continuam usando apenas os tipos financeiros
  permitidos — anomalia **não** entra em forecast, rateio, settlement ou
  realizado;
* a linha de anomalia tem ``tipo = 'anomalia'``, ``realizado`` nulo,
  ``realizado_cloud = anomalous_cost_cloud`` e
  ``is_financial_additive = false``; ela **não** é somada ao custo cloud
  observado.

A composição é declarativa (SQL) para que a mesma projeção sirva à API, ao
dashboard e às exportações, sem que nenhum caminho copie valores para o
PostgreSQL.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from app.core.hierarchy import MIN_LEVELS, hierarchy_columns
from app.services import lakehouse_layers as layers
from app.services.anomaly_schema import (
    ASSESSMENT_TABLE,
    ASSESSMENT_TIPO,
    REFERENCE_TABLE,
)

#: Tipos financeiros que podem aparecer em resumos de custo.
FINANCIAL_TIPOS: tuple[str, ...] = ("realizado", "forecast", "forecast-manual", "rateio", "shared_cost")

#: Colunas comuns entre a Planning e a avaliação de anomalia; os níveis
#: hierarquia_1..N entram depois de centro_custo, conforme a profundidade.
_COMMON_BEFORE_LEVELS: tuple[str, ...] = (
    "cloud_origem",
    "marketplaceproductname",
    "marketplacebillingmodel",
    "servicename",
    "subaccountname",
    "centro_custo",
)
_COMMON_AFTER_LEVELS: tuple[str, ...] = (
    "chargecategory",
    "serviceprovidername",
    "servicesubcategory",
    "pricingcategory",
    "commitmentdiscounttype",
    "source_connection_id",
    "period",
    "tipo",
    "realizado_cloud",
    "realizado",
)


def common_columns(depth: int = MIN_LEVELS) -> tuple[str, ...]:
    return (*_COMMON_BEFORE_LEVELS, *hierarchy_columns(depth), *_COMMON_AFTER_LEVELS)


def anomaly_not_financial_additive() -> dict[str, Any]:
    """Invariantes da linha de anomalia (plano §4.3)."""
    return {
        "tipo": ASSESSMENT_TIPO,
        "realizado": None,
        "realizado_cloud_from": "anomalous_cost_cloud",
        "is_financial_additive": False,
    }


def planning_projection(*, planning_table: str = "dest_billing_cloud_planning", alias: str = "p", depth: int = MIN_LEVELS) -> str:
    """Projeção explícita da Planning, sem ``SELECT *``."""
    columns = ", ".join(f"{alias}.{_quote(column)}" for column in common_columns(depth))
    return (
        f"SELECT {columns}, CAST(NULL AS DOUBLE) AS is_financial_additive, "
        f"CAST(NULL AS BIGINT) AS case_id, CAST(NULL AS VARCHAR) AS assessment_id, "
        f"CAST(NULL AS VARCHAR) AS assessment_status, CAST(NULL AS VARCHAR) AS severity "
        f"FROM iceberg.{layers.detail_layer()}.{_quote(planning_table)} {alias}"
    )


def anomaly_projection(*, anomaly_table: str = ASSESSMENT_TABLE, alias: str = "a", depth: int = MIN_LEVELS) -> str:
    """Projeção da tabela de avaliações, alinhada à Planning.

    ``realizado_cloud`` recebe ``anomalous_cost_cloud``; ``realizado`` é nulo.
    """
    columns = ", ".join(f"{alias}.{_quote(column)}" for column in common_columns(depth))
    return (
        f"SELECT {columns}, "
        f"CAST(FALSE AS BOOLEAN) AS is_financial_additive, "
        f"{alias}.\"case_id\" AS case_id, "
        f"{alias}.\"assessment_id\" AS assessment_id, "
        f"{alias}.\"assessment_status\" AS assessment_status, "
        f"CAST(NULL AS VARCHAR) AS severity "
        f"FROM iceberg.{layers.detail_layer()}.{_quote(anomaly_table)} {alias}"
    )


def sister_table_union_sql(
    *,
    planning_table: str = "dest_billing_cloud_planning",
    anomaly_table: str = ASSESSMENT_TABLE,
    extra_predicate: str | None = None,
    depth: int = MIN_LEVELS,
) -> str:
    """UNION ALL explícito entre Planning e anomalias.

    Este é o único ponto de composição. Nenhuma das duas tabelas é alterada.
    """
    where = f" WHERE {extra_predicate}" if extra_predicate else ""
    return (
        f"{planning_projection(planning_table=planning_table, depth=depth)}\n"
        f"UNION ALL\n"
        f"{anomaly_projection(anomaly_table=anomaly_table, depth=depth)}{where}"
    )


def financial_totals_sql(
    *,
    planning_table: str = "dest_billing_cloud_planning",
    anomaly_table: str = ASSESSMENT_TABLE,
) -> str:
    """Totais financeiros **com** e **sem** a camada de anomalia.

    Serve à prova de que a anomalia não aumenta o custo financeiro: os dois
    números têm de ser idênticos.
    """
    union = sister_table_union_sql(planning_table=planning_table, anomaly_table=anomaly_table)
    return f"""
        WITH composto AS ({union})
        SELECT
            SUM(CASE WHEN tipo IN ({_literal_list(FINANCIAL_TIPOS)})
                     THEN COALESCE(TRY_CAST(realizado_cloud AS DOUBLE), 0) ELSE 0 END) AS financial_cloud,
            SUM(CASE WHEN tipo = '{ASSESSMENT_TIPO}'
                     THEN COALESCE(TRY_CAST(realizado_cloud AS DOUBLE), 0) ELSE 0 END) AS anomaly_cloud,
            COUNT(*) AS total_rows
          FROM composto
    """


def assert_totals_unchanged(composed: Mapping[str, Any], baseline: float, *, tolerance: float = 1e-6) -> None:
    """Falha se a composição alterou o total financeiro."""
    financial = float(composed.get("financial_cloud") or 0.0)
    if abs(financial - float(baseline)) > tolerance:
        raise RuntimeError(
            "a composição alterou o total financeiro: "
            f"esperado {baseline}, obtido {financial}"
        )


def assert_anomaly_rows_not_additive(rows: Iterable[Mapping[str, Any]]) -> int:
    """Valida as invariantes da linha de anomalia antes de publicar.

    Levanta se alguma linha de anomalia for aditiva, trouxer ``realizado`` ou não
    apontar ``realizado_cloud`` para o custo anômalo.
    """
    checked = 0
    for row in rows:
        if str(row.get("tipo") or "") != ASSESSMENT_TIPO:
            continue
        checked += 1
        if row.get("is_financial_additive") is not False:
            raise RuntimeError(
                "linha de anomalia marcada como aditiva: "
                f"assessment_id={row.get('assessment_id')}"
            )
        if row.get("realizado") is not None:
            raise RuntimeError(
                "linha de anomalia não pode preencher 'realizado': "
                f"assessment_id={row.get('assessment_id')}"
            )
        anomalous = row.get("anomalous_cost_cloud")
        cloud = row.get("realizado_cloud")
        if anomalous is not None and cloud is not None:
            if abs(float(anomalous) - float(cloud)) > 1e-6:
                raise RuntimeError(
                    "realizado_cloud da anomalia difere de anomalous_cost_cloud: "
                    f"assessment_id={row.get('assessment_id')}"
                )
    return checked


def allowed_financial_types() -> Sequence[str]:
    """Tipos permitidos em resumos financeiros existentes."""
    return FINANCIAL_TIPOS


def reference_table_name() -> str:
    return REFERENCE_TABLE


def _quote(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _literal_list(values: Iterable[str]) -> str:
    return ", ".join("'" + str(value).replace("'", "''") + "'" for value in values)
