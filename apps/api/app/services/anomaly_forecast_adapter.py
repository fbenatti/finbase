"""Adaptador dos snapshots do forecast para a camada de anomalia (E2).

Reaproveita o forecast existente em vez de criar um segundo motor:

* a **expectativa** vem do resultado imutável `forecast.result_run_{id}`;
* o **contrato** (corte, dimensões, medida, granularidade) vem do
  `config_snapshot` congelado, completado pelas colunas do próprio
  `forecast_run` e, só quando necessário, pela configuração de anomalia;
* o **rastro de regras** vem de `forecast_run.error_metrics["business_rules"]`,
  convertido de texto livre em `applied_rule_refs` estruturado.

O contrato de snapshot varia por tipo de disparo no ambiente real:

| `trigger_type` | `config_snapshot` observado |
|---|---|
| `MANUAL` (simulado de acurácia) | 19 chaves, inclui `business_rules` e `source_namespace` |
| `SCHEDULED_REPROCESS` (normal) | 12 chaves, **sem** `business_rules` e **sem** `source_namespace` |
| `POST_INGESTION` (agendado) | criado sem snapshot (`{}`) |

Por isso o adaptador **declara a origem do contrato** (`contract_source`) e
nunca apresenta uma referência reconstruída como previsão originalmente
disponível.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from app.services import lakehouse_layers as layers
from app.services.trino_service import execute as trino_execute

logger = logging.getLogger(__name__)

#: Medida observada e esperada. O plano proíbe substituí-la.
DEFAULT_MEASURE = "realizado_cloud"

#: De onde veio o contrato usado para montar a referência.
CONTRACT_SOURCES = ("SNAPSHOT", "RUN_COLUMNS", "ANOMALY_CONFIG")

#: Como as regras congeladas foram recuperadas (plano §2.2).
RULE_SOURCES = ("SNAPSHOT", "RUN_METRICS", "CURRENT_CONFIG", "NONE")

#: Estados de aplicação de uma regra do forecast.
APPLIED = "MATCHED_AND_APPLIED"
OUT_OF_WINDOW = "MATCHED_OUT_OF_WINDOW"
OUT_OF_DIMENSIONS = "MATCHED_OUT_OF_DIMENSIONS"
SERIES_NOT_FOUND = "SERIES_NOT_FOUND"
UNKNOWN_BEHAVIOR = "RULE_UNKNOWN_BEHAVIOR"
NO_IDENTIFICATION = "RULE_WITHOUT_IDENTIFICATION"
INACTIVE = "RULE_INACTIVE"

_RULE_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("não aplicada: campo fora das dimensões", OUT_OF_DIMENSIONS),
    ("não aplicada: sem identificação/escopo", NO_IDENTIFICATION),
    ("não aplicada: comportamento desconhecido", UNKNOWN_BEHAVIOR),
    ("sem séries correspondentes", SERIES_NOT_FOUND),
)


class ForecastSnapshotUnavailable(RuntimeError):
    """Não há snapshot utilizável para montar a referência."""


@dataclass(frozen=True)
class AppliedRuleRef:
    """Uma regra do forecast e o que se sabe sobre sua aplicação."""

    name: str
    status: str
    detail: str | None = None
    rule_id: int | None = None
    version: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "rule_id": self.rule_id,
            "version": self.version,
        }


@dataclass
class ForecastContract:
    """Contrato congelado de uma execução de forecast."""

    run_id: int
    result_table: str
    #: Namespace do snapshot do forecast (`forecast.result_run_{id}`).
    result_namespace: str
    #: Namespace da fonte observada/explicativa (Silver do FOCUS).
    source_namespace: str
    status: str
    trigger_type: str
    config_id: int | None
    config_version: int | None
    cutoff_date: date | None
    generated_at: datetime | None
    measure_column: str
    dimensions: list[str]
    date_column: str | None = None
    period_column: str | None = None
    type_column: str | None = None
    horizon_months: int | None = None
    destination_physical_name: str | None = None
    available_columns: frozenset[str] = frozenset()
    applied_rule_refs: list[AppliedRuleRef] = field(default_factory=list)
    contract_source: str = "RUN_COLUMNS"
    rule_source: str = "NONE"
    #: Verdadeiro quando foi preciso usar as regras ATUAIS (não congeladas).
    declares_current_rules: bool = False
    #: Verdadeiro quando a fonte pode conter revisões posteriores ao corte.
    declares_late_revisions: bool = False
    source_snapshot_id: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def has_measure(self) -> bool:
        if not self.available_columns:
            return True  # schema não inspecionado
        return self.measure_column.lower() in self.available_columns

    @property
    def is_usable(self) -> bool:
        return (
            self.status == "COMPLETED"
            and bool(self.result_table)
            and self.cutoff_date is not None
            and self.has_measure
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "forecast_run_id": self.run_id,
            "forecast_config_version": self.config_version,
            "source_snapshot_id": self.source_snapshot_id,
            "data_cutoff": self.cutoff_date.isoformat() if self.cutoff_date else None,
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
            "result_table": f"{self.result_namespace}.{self.result_table}",
            "source_namespace": self.source_namespace,
            "measure_column": self.measure_column,
            "contract_source": self.contract_source,
            "rule_source": self.rule_source,
            "declares_current_rules": self.declares_current_rules,
            "declares_late_revisions": self.declares_late_revisions,
            "applied_rule_refs": [rule.as_dict() for rule in self.applied_rule_refs],
            "warnings": list(self.warnings),
        }


# --------------------------------------------------------------------------
# Rastro de regras
# --------------------------------------------------------------------------
def parse_applied_rule_refs(
    error_metrics: Mapping[str, Any] | None,
    *,
    frozen_rules: Sequence[Mapping[str, Any]] = (),
    current_rules: Sequence[Mapping[str, Any]] = (),
) -> tuple[list[AppliedRuleRef], str, bool]:
    """Converte o rastro textual de regras em referências estruturadas.

    O motor grava em ``error_metrics["business_rules"]`` uma lista de textos
    como ``"{nome} (sem séries correspondentes)"``. Aqui isso vira
    ``AppliedRuleRef`` com estado explícito — "regra cadastrada" nunca é
    apresentada como "regra aplicada".

    Retorna ``(refs, rule_source, declares_current_rules)``.
    """
    raw = list((error_metrics or {}).get("business_rules") or [])
    by_name: dict[str, Mapping[str, Any]] = {}
    for rule in list(frozen_rules) + list(current_rules):
        name = str(rule.get("name") or "").strip()
        if name:
            by_name.setdefault(name, rule)

    refs: list[AppliedRuleRef] = []
    for entry in raw:
        text = str(entry or "").strip()
        if not text:
            continue
        name, status, detail = _split_rule_entry(text)
        rule = by_name.get(name) or {}
        refs.append(
            AppliedRuleRef(
                name=name,
                status=status,
                detail=detail,
                rule_id=_as_int(rule.get("id")),
                version=_as_int(rule.get("version")),
            )
        )

    if frozen_rules:
        known = {rule.name for rule in refs}
        # Regras ativas congeladas que não apareceram no rastro: o motor não
        # registrou decisão — declarar como não observada é mais honesto do que
        # assumir aplicação.
        for rule in frozen_rules:
            name = str(rule.get("name") or "").strip()
            if name and name not in known:
                refs.append(
                    AppliedRuleRef(
                        name=name,
                        status="NOT_EVALUATED",
                        detail="regra ativa sem registro de aplicação no snapshot",
                        rule_id=_as_int(rule.get("id")),
                        version=_as_int(rule.get("version")),
                    )
                )

    if raw:
        return refs, ("SNAPSHOT" if frozen_rules else "RUN_METRICS"), False
    if frozen_rules:
        return refs, "SNAPSHOT", False
    if current_rules:
        # Nenhum rastro congelado: as regras atuais estão sendo usadas para
        # interpretar o snapshot. Declarar é obrigatório (plano §2.2).
        refs = [
            AppliedRuleRef(
                name=str(rule.get("name") or ""),
                status="UNKNOWN_FROM_CURRENT_CONFIG",
                detail="regras atuais aplicadas: o snapshot não congelou regras",
                rule_id=_as_int(rule.get("id")),
                version=_as_int(rule.get("version")),
            )
            for rule in current_rules
            if str(rule.get("name") or "").strip()
        ]
        return refs, "CURRENT_CONFIG", True
    return [], "NONE", False


def _split_rule_entry(text: str) -> tuple[str, str, str | None]:
    for suffix, status in _RULE_SUFFIXES:
        if suffix in text:
            name = text.split("(", 1)[0].strip()
            return name, status, suffix
    if "(" in text and text.rstrip().endswith(")"):
        name, _, detail = text.partition("(")
        return name.strip(), APPLIED, detail.rstrip(")").strip() or None
    return text, APPLIED, None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Leitura do contrato
# --------------------------------------------------------------------------
def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = pd.to_datetime(str(value), errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = pd.to_datetime(str(value), errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def contract_from_run_row(
    row: Mapping[str, Any],
    *,
    available_columns: Iterable[str] = (),
    fallback: Mapping[str, Any] | None = None,
    current_rules: Sequence[Mapping[str, Any]] = (),
    inspect_columns: bool = True,
) -> ForecastContract:
    """Monta o contrato a partir da linha de ``forecast_run``.

    ``fallback`` é a configuração de anomalia (namespace/cutoff/dimensões
    próprios). É usada **apenas** para completar o que o snapshot não congelou —
    e o contrato declara isso em ``contract_source``.
    """
    snapshot = row.get("config_snapshot") or {}
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except (TypeError, ValueError):
            snapshot = {}
    fallback = dict(fallback or {})
    warnings: list[str] = []

    frozen_rules = list(snapshot.get("business_rules") or [])
    refs, rule_source, declares_current = parse_applied_rule_refs(
        row.get("error_metrics"),
        frozen_rules=frozen_rules,
        current_rules=current_rules,
    )

    measure = (
        fallback.get("measure_column")
        or snapshot.get("measure_column")
        or DEFAULT_MEASURE
    )
    # A medida da REFERÊNCIA é a da configuração de anomalia (`realizado_cloud`).
    # O `measure_column` do forecast é apenas contexto: o motor projeta na
    # medida configurada dele e, no caso de dupla medida financeira, publica
    # também `realizado_cloud`. Quando a coluna não existe no resultado, a
    # referência fica indisponível — nunca se troca a medida (plano §1).
    forecast_measure = snapshot.get("measure_column")
    if forecast_measure and str(forecast_measure) != str(measure):
        warnings.append(
            f"forecast projeta em '{forecast_measure}'; a referência lê '{measure}' "
            "(exige snapshot de dupla medida financeira)"
        )
    dimensions = list(snapshot.get("dimensions") or fallback.get("dimensions") or [])
    result_table = row.get("result_table") or ""
    # O snapshot SEMPRE vive em `forecast.result_run_{id}`. O
    # `source_namespace` do config_snapshot é a camada da FONTE que alimentou o
    # motor (Silver), não onde o resultado foi gravado.
    result_namespace = layers.forecast_layer()
    source_namespace = snapshot.get("source_namespace") or ""
    contract_source = "SNAPSHOT"
    if not source_namespace:
        source_namespace = fallback.get("source_namespace") or layers.detail_layer()
        contract_source = "RUN_COLUMNS" if snapshot else "ANOMALY_CONFIG"
        warnings.append(
            "config_snapshot sem source_namespace: camada da fonte resolvida pela "
            "configuração de anomalia"
        )
    if not snapshot:
        warnings.append("config_snapshot vazio para esta execução")
    if not snapshot.get("business_rules"):
        warnings.append(
            "config_snapshot não congelou business_rules: rastro reconstruído do error_metrics"
        )

    columns = frozenset(str(c).lower() for c in available_columns)
    if inspect_columns and columns and measure.lower() not in columns:
        warnings.append(
            f"medida '{measure}' ausente em {result_namespace}.{result_table}: "
            "referência indisponível (não substituir a medida)"
        )

    return ForecastContract(
        run_id=int(row["id"]),
        result_table=result_table,
        result_namespace=result_namespace,
        source_namespace=source_namespace,
        status=str(row.get("status") or ""),
        trigger_type=str(row.get("trigger_type") or ""),
        config_id=_as_int(row.get("forecast_config_id")),
        config_version=_as_int(row.get("config_version")),
        cutoff_date=_as_date(row.get("cutoff_date")),
        generated_at=_as_datetime(row.get("finished_at") or row.get("started_at")),
        measure_column=str(measure),
        dimensions=[str(d) for d in dimensions],
        date_column=snapshot.get("date_column") or fallback.get("date_column"),
        period_column=snapshot.get("period_column") or fallback.get("period_column"),
        type_column=snapshot.get("type_column") or fallback.get("type_column"),
        horizon_months=_as_int(snapshot.get("horizon_months")),
        destination_physical_name=(
            snapshot.get("destination_physical_name") or row.get("destination_physical_name")
        ),
        available_columns=columns,
        applied_rule_refs=refs,
        contract_source=contract_source,
        rule_source=rule_source,
        declares_current_rules=declares_current,
        source_snapshot_id=row.get("source_snapshot_id"),
        warnings=warnings,
    )


async def _result_table_columns(namespace: str, table: str) -> frozenset[str]:
    if not table:
        return frozenset()
    try:
        rows = await trino_execute(
            "SELECT column_name FROM iceberg.information_schema.columns "
            f"WHERE table_schema = '{namespace}' AND table_name = '{table}'",
            timeout_s=60,
        )
    except Exception as exc:  # snapshot pode não existir mais
        logger.warning("[anomaly] não foi possível inspecionar %s.%s: %s", namespace, table, exc)
        return frozenset()
    return frozenset(str(row.get("column_name") or "").lower() for row in rows)


async def load_contract(
    run_row: Mapping[str, Any],
    *,
    fallback: Mapping[str, Any] | None = None,
    current_rules: Sequence[Mapping[str, Any]] = (),
) -> ForecastContract:
    """Contrato completo, com o schema real do resultado inspecionado."""
    table = str(run_row.get("result_table") or "")
    snapshot = run_row.get("config_snapshot") or {}
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except (TypeError, ValueError):
            snapshot = {}
    columns = (
        await _result_table_columns(layers.forecast_layer(), table) if table else frozenset()
    )
    return contract_from_run_row(
        run_row,
        available_columns=columns,
        fallback=fallback,
        current_rules=current_rules,
    )


# --------------------------------------------------------------------------
# Leitura da expectativa
# --------------------------------------------------------------------------
async def read_expectation(
    contract: ForecastContract,
    *,
    months: Sequence[str],
) -> list[dict[str, Any]]:
    """Soma a medida esperada por série × mês no snapshot do forecast.

    Nunca lê o billing realizado: a expectativa vem só do resultado congelado.
    """
    if not contract.is_usable or not months:
        return []

    measure = contract.measure_column
    dims = [d for d in contract.dimensions]
    quoted_months = ", ".join("'" + str(m).replace("'", "''") + "'" for m in months)
    dim_select = "".join(f'{_quote(d)}, ' for d in dims)
    group_by = ", ".join(str(i + 1) for i in range(len(dims) + 1))
    sql = f"""
        SELECT {dim_select}CAST(forecast_month AS VARCHAR) AS forecast_month,
               SUM(COALESCE(TRY_CAST({_quote(measure)} AS DOUBLE), 0)) AS expected_cloud,
               COUNT(*) AS source_rows
          FROM iceberg.{contract.result_namespace}.{contract.result_table}
         WHERE CAST(forecast_month AS VARCHAR) IN ({quoted_months})
         GROUP BY {group_by}
    """
    rows = await trino_execute(sql, timeout_s=600)
    return [
        {**{d: row.get(d) for d in dims}, "forecast_month": str(row.get("forecast_month")), "expected_cloud": float(row.get("expected_cloud") or 0.0), "source_rows": int(row.get("source_rows") or 0)}
        for row in rows
    ]


def _quote(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'
