"""Referência compacta e seleção prospectiva/retrospectiva (E2).

A referência é a expectativa **anterior ao evento** para uma série e um mês.
Ela é derivada do snapshot imutável do forecast e guarda, por linha:

```text
forecast_run_id · forecast_config_version · source_snapshot_id · data_cutoff
generated_at · target_start · target_end · reference_kind · applied_rule_refs
```

Compacta por construção: **uma linha por forecast × série × mês**, nunca a grade
de dias (plano §4.2).

Seleção (plano §2.2):

* ``PROSPECTIVE`` — snapshot gerado **antes** do evento;
* ``RETROSPECTIVE`` — reconstruída depois, com corte **anterior** ao evento;
* ``UNAVAILABLE`` — comparação não suportada.

Reconstrução nunca é apresentada como previsão originalmente disponível: o
``reference_kind`` e os avisos do contrato carregam essa distinção.
"""
from __future__ import annotations

import calendar
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from app.services.anomaly_forecast_adapter import (
    ForecastContract,
    parse_applied_rule_refs,
)
from app.services.anomaly_calibration import (
    DAILY_GRAIN,
    BandCalibration,
    WeekdayProfile,
    build_daily_distribution,
    scale_band_to_coverage,
)

REFERENCE_KINDS = ("PROSPECTIVE", "RETROSPECTIVE", "UNAVAILABLE")

#: Cobertura da projeção dentro do mês.
FULL_MONTH = "FULL_MONTH"
REMAINING_MONTH = "REMAINING_MONTH"
#: Mês já encerrado no corte: não faz parte do horizonte de acompanhamento.
ELAPSED_MONTH = "ELAPSED_MONTH"


class ReferenceUnavailable(RuntimeError):
    """Não há referência compatível cujo cálculo não usou o realizado avaliado."""

    def __init__(self, reason: str, detail: str | None = None):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


# --------------------------------------------------------------------------
# Identidade determinística da série
# --------------------------------------------------------------------------
def _norm(value: Any) -> str:
    if value is None:
        return ""
    try:
        if value != value:  # NaN
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "nat", "<na>"} else text


def series_id_for(dimensions: Sequence[str], values: Mapping[str, Any]) -> str:
    """Id determinístico de uma combinação de dimensões.

    Reprocessar o mesmo dado produz o mesmo id — é o que impede duplicar caso.
    """
    canonical = json.dumps(
        [[str(dim), _norm(values.get(dim))] for dim in dimensions],
        sort_keys=False,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def event_family_for(dimensions: Sequence[str], values: Mapping[str, Any], *, keys: Sequence[str] | None = None) -> str:
    """Família do evento: o recorte de dimensões que define "mesmo gasto"."""
    chosen = list(keys or dimensions)
    parts = [f"{dim}={_norm(values.get(dim))}" for dim in chosen]
    return "|".join(parts) if parts else "sem-dimensoes"


def reference_id_for(run_id: int, series_id: str, month: str) -> str:
    raw = f"{run_id}:{series_id}:{month}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def month_bounds(month: str) -> tuple[date, date]:
    year, mon = int(month[:4]), int(month[4:6])
    last = calendar.monthrange(year, mon)[1]
    return date(year, mon, 1), date(year, mon, last)


def month_of(day: date) -> str:
    return f"{day.year:04d}{day.month:02d}"


# --------------------------------------------------------------------------
# Cobertura
# --------------------------------------------------------------------------
def classify_coverage(month: str, cutoff: date) -> tuple[str, date | None, date | None, int]:
    """Dias do mês efetivamente cobertos pela projeção.

    * mês posterior ao do corte → mês inteiro;
    * mês do corte → do dia seguinte ao corte até o fim do mês;
    * mês anterior ao do corte → já transcorrido, fora do horizonte.
    """
    first, last = month_bounds(month)
    cutoff_month = month_of(cutoff)
    if month < cutoff_month:
        return ELAPSED_MONTH, None, None, 0
    if month > cutoff_month:
        return FULL_MONTH, first, last, (last - first).days + 1
    if cutoff >= last:
        return FULL_MONTH, first, last, (last - first).days + 1
    start = cutoff + timedelta(days=1)
    return REMAINING_MONTH, start, last, (last - start).days + 1


def horizon_months(cutoff: date, horizon_days: int) -> list[str]:
    """Meses alcançados pelo horizonte de acompanhamento a partir do corte."""
    months: list[str] = []
    cursor = cutoff
    end = cutoff + timedelta(days=max(1, int(horizon_days)))
    while cursor <= end:
        key = month_of(cursor)
        if key not in months:
            months.append(key)
        # avança para o primeiro dia do mês seguinte
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


# --------------------------------------------------------------------------
# Linhas de referência
# --------------------------------------------------------------------------
@dataclass
class ReferenceRow:
    """Uma linha da referência compacta."""

    reference_id: str
    forecast_run_id: int
    forecast_config_version: int | None
    source_snapshot_id: str | None
    data_cutoff: str
    generated_at: str | None
    target_start: str
    target_end: str
    reference_kind: str
    series_id: str
    reference_month: str
    coverage_kind: str
    coverage_start: str | None
    coverage_end: str | None
    covered_days: int
    predicted_cloud: float
    normalizer: float
    distribution_method: str
    weekday_weights: dict[str, float] | None
    band_lower_cloud: float | None
    band_upper_cloud: float | None
    band_method: str | None
    band_confidence: str
    daily_band_lower_offset: float | None = None
    daily_band_upper_offset: float | None = None
    band_alpha: float | None = None
    band_sample_size: int = 0
    band_min_sample: int = 0
    applied_rule_refs: list[dict[str, Any]] = field(default_factory=list)
    dimensions: dict[str, Any] = field(default_factory=dict)
    partial_reference: bool = False
    sample_size: int = 0
    history_days: int = 0
    calibration_days: int = 0

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "reference_id": self.reference_id,
            "forecast_run_id": self.forecast_run_id,
            "forecast_config_version": self.forecast_config_version,
            "source_snapshot_id": self.source_snapshot_id,
            "data_cutoff": self.data_cutoff,
            "generated_at": self.generated_at,
            "target_start": self.target_start,
            "target_end": self.target_end,
            "reference_kind": self.reference_kind,
            "series_id": self.series_id,
            "reference_month": self.reference_month,
            "coverage_kind": self.coverage_kind,
            "coverage_start": self.coverage_start,
            "coverage_end": self.coverage_end,
            "covered_days": self.covered_days,
            "predicted_cloud": self.predicted_cloud,
            "normalizer": self.normalizer,
            "distribution_method": self.distribution_method,
            "weekday_weights": json.dumps(self.weekday_weights) if self.weekday_weights else None,
            "band_lower_cloud": self.band_lower_cloud,
            "band_upper_cloud": self.band_upper_cloud,
            "band_method": self.band_method,
            "band_confidence": self.band_confidence,
            "daily_band_lower_offset": self.daily_band_lower_offset,
            "daily_band_upper_offset": self.daily_band_upper_offset,
            "band_alpha": self.band_alpha,
            "band_sample_size": self.band_sample_size,
            "band_min_sample": self.band_min_sample,
            "applied_rule_refs": json.dumps(self.applied_rule_refs, ensure_ascii=False),
            "partial_reference": self.partial_reference,
            "sample_size": self.sample_size,
            "history_days": self.history_days,
            "calibration_days": self.calibration_days,
            **self.dimensions,
        }
        return payload


def build_reference_rows(
    contract: ForecastContract,
    expectation_rows: Sequence[Mapping[str, Any]],
    *,
    dimensions: Sequence[str],
    reference_kind: str,
    target_start: str,
    target_end: str,
    history_days: int = 0,
    calibration_days: int = 0,
    weekday_profile: WeekdayProfile | None = None,
    band: BandCalibration | None = None,
) -> list[ReferenceRow]:
    """Uma linha por série × mês, com a soma preservada.

    A soma de ``predicted_cloud`` por mês é **exatamente** a soma do snapshot do
    forecast para aquele mês: a referência não redistribui nem divide o valor
    mensal (plano §2.3 — os 15 dias são horizonte, não divisão).

    Quando ``weekday_profile`` é informado, a distribuição por dia da semana é
    calculada sobre **todo o intervalo coberto** e o ``normalizer`` é a soma dos
    pesos desse intervalo. Quando ``band`` é informado, a faixa é gravada: o
    total do mês em ``band_lower_cloud``/``band_upper_cloud`` e o offset de um
    único dia em ``daily_band_*_offset`` (granularidade diária).
    """
    if contract.cutoff_date is None:
        raise ReferenceUnavailable("CUTOFF_ABSENT", "forecast_run sem cutoff_date")
    if reference_kind not in REFERENCE_KINDS or reference_kind == "UNAVAILABLE":
        raise ReferenceUnavailable("REFERENCE_UNAVAILABLE", f"kind={reference_kind}")

    rules = [rule.as_dict() for rule in contract.applied_rule_refs]
    cutoff = contract.cutoff_date
    rows: list[ReferenceRow] = []
    for row in expectation_rows:
        month = str(row.get("forecast_month") or "")
        if not month:
            continue
        coverage_kind, cov_start, cov_end, covered = classify_coverage(month, cutoff)
        if coverage_kind == ELAPSED_MONTH:
            # Mês já transcorrido no corte não pertence ao horizonte.
            continue
        values = {dim: row.get(dim) for dim in dimensions}
        series = series_id_for(dimensions, values)
        predicted = float(row.get("expected_cloud") or 0.0)

        normalizer = predicted
        distribution_method = "MONTHLY_TOTAL"
        weights: dict[str, float] | None = None
        if weekday_profile is not None and cov_start is not None and cov_end is not None:
            distribution = build_daily_distribution(
                predicted,
                coverage_start=cov_start,
                coverage_end=cov_end,
                profile=weekday_profile,
            )
            normalizer = distribution.normalizer
            distribution_method = distribution.method
            weights = {str(day.weekday()): round(w, 8) for day, w in distribution.weights.items()}

        band_fields: dict[str, Any] = {}
        if band is not None:
            if band.granularity == DAILY_GRAIN:
                # Acumulado do mês: offset diário × dias cobertos.
                monthly = scale_band_to_coverage(band, covered_days=covered)
                band_fields = monthly.as_reference_fields(predicted)
                band_fields["daily_band_lower_offset"] = band.lower_offset
                band_fields["daily_band_upper_offset"] = band.upper_offset
            else:
                band_fields = band.as_reference_fields(predicted)

        rows.append(
            ReferenceRow(
                reference_id=reference_id_for(contract.run_id, series, month),
                forecast_run_id=contract.run_id,
                forecast_config_version=contract.config_version,
                source_snapshot_id=contract.source_snapshot_id,
                data_cutoff=cutoff.isoformat(),
                generated_at=contract.generated_at.isoformat() if contract.generated_at else None,
                target_start=target_start,
                target_end=target_end,
                reference_kind=reference_kind,
                series_id=series,
                reference_month=month,
                coverage_kind=coverage_kind,
                coverage_start=cov_start.isoformat() if cov_start else None,
                coverage_end=cov_end.isoformat() if cov_end else None,
                covered_days=covered,
                predicted_cloud=predicted,
                # Normalizador e pesos são preenchidos pela E3; aqui o valor
                # mensal já é o normalizador trivial.
                normalizer=normalizer,
                distribution_method=distribution_method,
                weekday_weights=weights,
                band_lower_cloud=band_fields.get("band_lower_cloud"),
                band_upper_cloud=band_fields.get("band_upper_cloud"),
                band_method=band_fields.get("band_method"),
                band_confidence=band_fields.get("band_confidence", "UNAVAILABLE"),
                daily_band_lower_offset=band_fields.get("daily_band_lower_offset"),
                daily_band_upper_offset=band_fields.get("daily_band_upper_offset"),
                band_alpha=band_fields.get("band_alpha"),
                band_sample_size=int(band_fields.get("band_sample_size") or 0),
                band_min_sample=int(band_fields.get("band_min_sample") or 0),
                applied_rule_refs=rules,
                dimensions=values,
                partial_reference=coverage_kind == REMAINING_MONTH,
                history_days=history_days,
                calibration_days=calibration_days,
            )
        )
    return rows


# --------------------------------------------------------------------------
# Seleção da referência para um evento
# --------------------------------------------------------------------------
@dataclass
class ReferenceSelection:
    """Qual referência compara com um evento e por quê."""

    reference_kind: str
    contract: ForecastContract | None
    reason: str
    recent_reference_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_available(self) -> bool:
        return self.contract is not None and self.reference_kind != "UNAVAILABLE"


def _generated_day(contract: ForecastContract) -> date | None:
    if contract.generated_at is None:
        return None
    return contract.generated_at.date()


def select_reference(
    contracts: Sequence[ForecastContract],
    *,
    event_date: date,
    event_month: str | None = None,
) -> ReferenceSelection:
    """Escolhe a referência cujo cálculo não usou o realizado avaliado.

    Ordem de preferência:

    1. ``PROSPECTIVE`` — snapshot gerado **antes** do evento e cujo corte também
       é anterior a ele. É a única que representa o que se esperava na hora;
    2. ``RETROSPECTIVE`` — snapshot gerado depois do evento, mas com corte
       estritamente anterior a ele (dado do evento não entrou no cálculo);
    3. ``UNAVAILABLE``.
    """
    month = event_month or month_of(event_date)
    usable = [c for c in contracts if c.is_usable]
    if not usable:
        return ReferenceSelection(
            "UNAVAILABLE", None, "NENHUM_SNAPSHOT_UTILIZAVEL",
            warnings=["nenhum forecast_run COMPLETED com cutoff e medida utilizáveis"],
        )

    def covers(contract: ForecastContract) -> bool:
        return classify_coverage(month, contract.cutoff_date)[0] != ELAPSED_MONTH

    prospective = [
        c for c in usable
        if covers(c) and c.cutoff_date < event_date and (_generated_day(c) or date.min) < event_date
    ]
    if prospective:
        chosen = max(prospective, key=lambda c: (c.cutoff_date, _generated_day(c) or date.min, c.run_id))
        return ReferenceSelection(
            "PROSPECTIVE", chosen, "SNAPSHOT_ANTERIOR_AO_EVENTO",
            warnings=list(chosen.warnings),
        )

    retrospective = [c for c in usable if covers(c) and c.cutoff_date < event_date]
    if retrospective:
        chosen = max(retrospective, key=lambda c: (c.cutoff_date, c.run_id))
        return ReferenceSelection(
            "RETROSPECTIVE", chosen, "CORTE_ANTERIOR_AO_EVENTO_SNAPSHOT_POSTERIOR",
            warnings=list(chosen.warnings)
            + ["referência reconstruída: não foi a previsão originalmente disponível"],
        )

    return ReferenceSelection(
        "UNAVAILABLE", None, "CORTE_POSTERIOR_AO_EVENTO",
        warnings=[
            "todo snapshot utilizável tem corte igual ou posterior ao evento: "
            "o realizado avaliado já participou do cálculo"
        ],
    )


def select_pre_event_reference(
    contracts: Sequence[ForecastContract],
    *,
    event_date: date,
    event_month: str | None = None,
) -> ReferenceSelection:
    """Referência congelada ANTES de uma compra/evento esperado (aceite E2).

    Caso real: compra recorrente entra no dia 05 e um novo forecast roda no dia
    12. O restante do mês no snapshot novo é ``0`` porque a compra já foi
    realizada. Comparar contra esse zero acusaria toda compra normal como
    anomalia. Aqui a referência escolhida é a última gerada **antes** do dia 05,
    que ainda continha o valor esperado da compra.
    """
    month = event_month or month_of(event_date)
    usable = [c for c in contracts if c.is_usable and classify_coverage(month, c.cutoff_date)[0] != ELAPSED_MONTH]
    before = [c for c in usable if (_generated_day(c) or date.min) < event_date and c.cutoff_date < event_date]
    if not before:
        return ReferenceSelection(
            "UNAVAILABLE", None, "SEM_REFERENCIA_ANTERIOR_A_COMPRA",
            warnings=["nenhum snapshot foi gerado antes do evento: não há expectativa pré-compra"],
        )
    chosen = max(before, key=lambda c: (_generated_day(c) or date.min, c.run_id))
    return ReferenceSelection(
        "PROSPECTIVE", chosen, "REFERENCIA_ANTERIOR_A_COMPRA_PRESERVADA",
        warnings=list(chosen.warnings),
    )


def is_month_closed(month: str, cutoff: date) -> bool:
    """O mês está inteiramente coberto pelo corte?"""
    _, last = month_bounds(month)
    return cutoff >= last


def zero_remaining_is_legitimate(
    contract: ForecastContract,
    month: str,
    *,
    monthly_event_realized: float,
) -> bool:
    """Um restante ``0`` é legítimo quando a compra esperada já foi realizada.

    Regra de comportamento preservada (plano §2.4 e `ZERO_WHEN_REALIZED`): não
    comparar o observado contra o forecast restante zero. Quando existe
    realizado do evento mensal no mês, o zero é ausência de projeção adicional —
    não ausência de gasto.
    """
    if contract.cutoff_date is None:
        return False
    kind, _, _, _ = classify_coverage(month, contract.cutoff_date)
    if kind != REMAINING_MONTH:
        return False
    return float(monthly_event_realized) != 0.0


def months_between(start: date, end: date) -> list[str]:
    months: list[str] = []
    cursor = start.replace(day=1)
    while cursor <= end:
        months.append(month_of(cursor))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


def applied_rule_refs_from_metrics(
    error_metrics: Mapping[str, Any] | None,
    *,
    frozen_rules: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Atalho público para o rastro de regras (usado por E4/E6)."""
    refs, _, _ = parse_applied_rule_refs(error_metrics, frozen_rules=list(frozen_rules))
    return [rule.as_dict() for rule in refs]
