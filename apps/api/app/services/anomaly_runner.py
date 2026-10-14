"""Execução da detecção: referência → avaliação → episódios → publicação (E6).

Fluxo (plano §6):

```text
Silver atualizada → forecast normal → referência compacta
   → avaliação de dados novos/atualizados → atualização do episódio
   → roteamento e notificações → monitoramento de ações e verificação
```

Garantias de consistência implementadas:

* **publicação atômica** (staging → validação → commit): falha preserva a
  publicação anterior;
* **reconciliação**: a soma da referência por mês é igual à soma do snapshot do
  forecast;
* **unicidade**: uma linha por `assessment_id`;
* **invariantes financeiras**: linha de anomalia não é aditiva e não preenche
  `realizado`;
* **idempotência**: reprocessar atualiza evidências e não duplica caso nem
  impacto (E4);
* **referência congelada do episódio**: a referência do caso ativo não é
  sobrescrita pela referência mais recente.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

import pandas as pd
from sqlalchemy import select

from app.database import async_session
from app.services import iceberg_service as iceberg
from app.services import lakehouse_layers as layers
from app.services.anomaly_calibration import (
    DAILY_GRAIN,
    MONTHLY_EVENT_GRAIN,
    calibrate_band,
    calibrate_monthly_event_band,
    has_complete_daily_calibration,
    materiality_for,
    resolve_parameters,
    weekday_profile,
)
from app.services.anomaly_composition import assert_anomaly_rows_not_additive
from app.services.anomaly_detector import (
    EVENT_DAILY,
    MonthlyEvent,
    SeriesPoint,
    case_key_for,
    consolidate_family,
    detect_daily_consumption,
    detect_monthly_accumulated,
    detect_monthly_event,
    resolve_episode,
    EXPECTED_ONLY,
)
from app.services.anomaly_forecast_adapter import (
    ForecastContract,
    load_contract,
    read_expectation,
)
from app.services.anomaly_observed import group_by_series, read_daily_observed
from app.services.anomaly_reference import (
    ELAPSED_MONTH,
    build_reference_rows,
    classify_coverage,
    horizon_months,
)
from app.services.anomaly_release import (
    apply_detection_to_case,
    assessment_id_of,
    commit_publication,
    current_publication,
    finish_run,
    publish_iceberg,
)
from app.services.anomaly_schema import (
    ASSESSMENT_TABLE,
    ASSESSMENT_TIPO,
    REFERENCE_TABLE,
    SERIES_DIMENSIONS,
)

logger = logging.getLogger(__name__)

#: Razão entre dias sem cobrança e janela que classifica a série como "lumpy".
LUMPY_NONZERO_RATIO = 0.15
#: Mínimo de dias com cobrança para tratar a série como consumo diário.
MIN_DAILY_COVERAGE_DAYS = 20


class AnomalyRunError(RuntimeError):
    """Execução não pode prosseguir (configuração ou dados insuficientes)."""


@dataclass
class DetectionOutcome:
    """Resultado interno da avaliação, antes da publicação."""

    reference_rows: list[Any] = field(default_factory=list)
    assessments: list[dict[str, Any]] = field(default_factory=list)
    detections: list[Any] = field(default_factory=list)
    series_count: int = 0
    expectation_totals: dict[str, float] = field(default_factory=dict)
    reference_totals: dict[str, float] = field(default_factory=dict)
    contract: ForecastContract | None = None
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def assessment_id_for(reference_id: str | None, series_id: str, event_date: str, grain: str) -> str:
    raw = f"{reference_id or '-'}|{series_id}|{event_date}|{grain}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _series_key(row: Mapping[str, Any], dimensions: Sequence[str]) -> str:
    from app.services.anomaly_reference import series_id_for

    return series_id_for(dimensions, {dim: row.get(dim) for dim in dimensions})


def event_family_for(row: Mapping[str, Any], family_keys: Sequence[str]) -> str:
    keys = list(family_keys)
    if not keys:
        return "serie"
    return "|".join(f"{key}={row.get(key) or ''}" for key in keys)


def is_lumpy_series(daily_rows: Sequence[Mapping[str, Any]], *, window_days: int) -> bool:
    """Série com pouquíssimos dias de cobrança é evento mensal, não consumo diário."""
    covered = {row["event_date"] for row in daily_rows if float(row.get("observed_cloud") or 0.0) != 0.0}
    if not covered:
        return True
    if len(covered) < MIN_DAILY_COVERAGE_DAYS:
        return True
    return len(covered) / max(1, window_days) < LUMPY_NONZERO_RATIO


# --------------------------------------------------------------------------
# Avaliação (pura o suficiente para teste)
# --------------------------------------------------------------------------
def evaluate_series(
    *,
    series_rows: Sequence[Mapping[str, Any]],
    reference_by_month: Mapping[str, Any],
    dimensions: Sequence[str],
    family_keys: Sequence[str],
    profile: Any,
    materiality: float,
    calibration_days: int,
    window_days: int,
    horizon_end: date,
    applied_rule_refs: Sequence[Mapping[str, Any]] = (),
) -> list[Any]:
    """Detecta anomalias de uma série contra a referência do mês."""
    if not series_rows:
        return []

    sample = series_rows[0]
    series_id = _series_key(sample, dimensions)
    family = event_family_for(sample, family_keys)
    observed_by_day = {row["event_date"]: float(row.get("observed_cloud") or 0.0) for row in series_rows}
    as_of = max(observed_by_day)

    weekly = weekday_profile(observed_by_day, as_of=as_of, weeks=8)
    complete = has_complete_daily_calibration(observed_by_day, as_of=as_of, calibration_days=calibration_days)

    # Resíduos da granularidade diária: observado menos a mediana do dia da semana.
    residuals = [
        value - weekly.medians.get(day.weekday(), 0.0)
        for day, value in observed_by_day.items()
    ]
    lumpy = is_lumpy_series(series_rows, window_days=window_days)

    if lumpy:
        return _evaluate_monthly(
            series_rows=series_rows,
            observed_by_day=observed_by_day,
            reference_by_month=reference_by_month,
            series_id=series_id,
            family=family,
            dimensions=dimensions,
            sample=sample,
            profile=profile,
            materiality=materiality,
            applied_rule_refs=applied_rule_refs,
        )

    band = calibrate_band(
        residuals=residuals,
        expected_cloud=sum(weekly.medians.values()),
        quantile=profile.daily_quantile,
        tolerance_pct=0.15,
        min_relative_deviation=profile.min_relative_deviation,
        calibration_days=calibration_days,
    )
    if band.method == "EXPLICIT_TOLERANCE":
        band.note = (band.note or "") + "; perfil uniforme" if weekly.is_fallback else band.note

    points: list[SeriesPoint] = []
    for month, reference in reference_by_month.items():
        coverage_kind, cov_start, cov_end, _ = classify_coverage(month, reference_cutoff(reference))
        if coverage_kind == ELAPSED_MONTH or cov_start is None or cov_end is None:
            continue
        distribution_weights = None
        for day in _days(cov_start, cov_end):
            if day > horizon_end or day not in observed_by_day:
                continue
            expected = _daily_expected(reference, day, weekly)
            if distribution_weights is None:
                distribution_weights = True
            points.append(
                SeriesPoint(
                    series_id=series_id,
                    dimensions={dim: sample.get(dim) for dim in dimensions},
                    event_date=day,
                    observed_cloud=observed_by_day[day],
                    expected_cloud=expected,
                    upper_cloud=expected + band.upper_offset,
                    lower_cloud=max(0.0, expected - band.lower_offset),
                    reference_id=reference.reference_id,
                    reference_kind=reference.reference_kind,
                    source_billing_period=sample.get("source_billing_period"),
                )
            )
    if not points:
        return []
    detection = detect_daily_consumption(
        points,
        profile=profile,
        materiality=materiality,
        band=band,
        event_family=family,
        reference_kind=next(iter(reference_by_month.values())).reference_kind,
        complete_calibration=complete,
        applied_rule_refs=applied_rule_refs,
    )
    return [detection] if detection is not None else []


def _evaluate_monthly(
    *,
    series_rows: Sequence[Mapping[str, Any]],
    observed_by_day: Mapping[date, float],
    reference_by_month: Mapping[str, Any],
    series_id: str,
    family: str,
    dimensions: Sequence[str],
    sample: Mapping[str, Any],
    profile: Any,
    materiality: float,
    applied_rule_refs: Sequence[Mapping[str, Any]],
) -> list[Any]:
    """Séries lumpy: evento mensal e excesso acumulado por mês."""
    detections: list[Any] = []
    for month, reference in sorted(reference_by_month.items()):
        coverage_kind, cov_start, cov_end, _ = classify_coverage(month, reference_cutoff(reference))
        if coverage_kind == ELAPSED_MONTH or cov_start is None or cov_end is None:
            continue
        events: list[MonthlyEvent] = []
        for day in _days(cov_start, cov_end):
            if day not in observed_by_day:
                continue
            expected = ref_month_total(reference)
            upper = reference.band_upper_cloud if reference.band_upper_cloud is not None else expected
            events.append(
                MonthlyEvent(
                    series_id=series_id,
                    dimensions={dim: sample.get(dim) for dim in dimensions},
                    event_date=day,
                    observed_cloud=observed_by_day[day],
                    expected_cloud=expected,
                    upper_cloud=upper,
                    lower_cloud=reference.band_lower_cloud if reference.band_lower_cloud is not None else expected,
                    reference_id=reference.reference_id,
                    reference_kind=reference.reference_kind,
                    source_billing_period=sample.get("source_billing_period"),
                    pre_event_reference=reference.reference_kind != "UNAVAILABLE",
                    matched_rule_refs=[dict(rule) for rule in applied_rule_refs],
                )
            )
        if not events:
            continue
        band = calibrate_monthly_event_band(
            residuals=[],
            expected_cloud=ref_month_total(reference),
            tolerance_pct=0.15,
        )
        for event in events:
            detections.append(
                detect_monthly_event(
                    event,
                    profile=profile,
                    materiality=materiality,
                    band=band,
                    event_family=family,
                    previous_events=[],
                )
            )
        accumulated = detect_monthly_accumulated(
            series_id=series_id,
            dimensions={dim: sample.get(dim) for dim in dimensions},
            event_family=family,
            period=month,
            events=events,
            profile=profile,
            materiality=materiality,
            band=band,
        )
        if accumulated is not None:
            detections.append(accumulated)
    return detections


def reference_cutoff(reference: Any) -> date:
    """Corte da linha de referência (`data_cutoff` é string ISO)."""
    raw = getattr(reference, "data_cutoff", None)
    if isinstance(raw, date):
        return raw
    if not raw:
        raise AnomalyRunError("referência sem data_cutoff")
    return date.fromisoformat(str(raw)[:10])


def ref_month_total(reference: Any) -> float:
    return float(getattr(reference, "predicted_cloud", 0.0) or 0.0)


def _daily_expected(reference: Any, day: date, weekly: Any) -> float:
    """Parcela diária prevista, com baseline histórico se o restante zerou."""
    total = ref_month_total(reference)
    if total <= 0:
        # O restante mensal zerado pode significar que a recorrência já foi
        # realizada. Para séries diárias com histórico confiável, usar o padrão
        # anterior ao corte evita tratar qualquer resíduo como um pico.
        if weekly and not weekly.is_fallback:
            historical = float(weekly.medians.get(day.weekday(), 0.0))
            if historical > 0:
                return historical
        return 0.0
    if not weekly or not weekly.medians:
        days = max(1, int(getattr(reference, "covered_days", 1) or 1))
        return total / days
    window_sum = sum(
        weekly.medians.get(cursor.weekday(), 0.0)
        for cursor in _days(date.fromisoformat(reference.coverage_start), date.fromisoformat(reference.coverage_end))
    ) if reference.coverage_start and reference.coverage_end else 0.0
    if window_sum <= 0:
        days = max(1, int(getattr(reference, "covered_days", 1) or 1))
        return total / days
    return total * (weekly.medians.get(day.weekday(), 0.0) / window_sum)


def _days(start: date, end: date) -> list[date]:
    out: list[date] = []
    cursor = start
    while cursor <= end:
        out.append(cursor)
        cursor += timedelta(days=1)
    return out


def to_assessment_rows(
    detections: Sequence[Any],
    *,
    dimensions: Sequence[str],
    detected_at: datetime,
    forecast_run_id: int | None,
    evaluation_start: date,
    evaluation_end: date,
) -> list[dict[str, Any]]:
    """Converte detecções em linhas da tabela de avaliação (plano §4.3).

    Eventos reconhecidos como **esperados** também são publicados
    (`assessment_status = EXPECTED_EVENT`, custo anômalo zero, sem caso): o plano
    exige que eles permaneçam na tabela analítica, sem abrir workflow.
    """
    rows: list[dict[str, Any]] = []
    for detection in detections:
        assessment_id = assessment_id_for(
            detection.reference_id, detection.series_id, detection.event_date, detection.evaluation_grain
        )
        row: dict[str, Any] = {
            "tipo": ASSESSMENT_TIPO,
            "assessment_id": assessment_id,
            "series_id": detection.series_id,
            "event_date": detection.event_date,
            "detected_at": detected_at,
            "evaluation_grain": detection.evaluation_grain,
            "forecast_run_id": forecast_run_id,
            "reference_id": detection.reference_id,
            "reference_kind": detection.reference_kind,
            "observed_cloud": detection.observed_cloud,
            "expected_cloud": detection.expected_cloud,
            "lower_cloud": detection.lower_cloud,
            "upper_cloud": detection.upper_cloud,
            "delta_cloud": detection.delta_cloud,
            # Compatibilidade com Planning: `realizado_cloud` carrega o custo
            # anômalo; `realizado` fica nulo e a linha nunca é aditiva.
            "anomalous_cost_cloud": detection.anomalous_cost_cloud,
            "realizado_cloud": detection.anomalous_cost_cloud,
            "realizado": None,
            "score": detection.score,
            "confidence": detection.confidence,
            "assessment_status": detection.assessment_status,
            "reason_codes": json.dumps(detection.reason_codes, ensure_ascii=False),
            "applied_rule_refs": json.dumps(detection.applied_rule_refs, ensure_ascii=False),
            "evaluation_start": evaluation_start.isoformat(),
            "evaluation_end": evaluation_end.isoformat(),
            "source_billing_period": detection.source_billing_period,
            "is_financial_additive": False,
            "period": detection.period,
            # Preenchido quando o caso do episódio é criado/atualizado, antes da
            # publicação: a linha publicada já aponta para o caso.
            "case_id": None,
        }
        # Todas as dimensões do contrato entram na linha, mesmo as que não
        # participam da série: a tabela publicada tem de ter o conjunto fixo de
        # colunas, compatível com Planning.
        for dim in SERIES_DIMENSIONS:
            row[dim] = detection.series_dimensions.get(dim)
        rows.append(row)
    return rows


def validate_assessments(rows: Sequence[Mapping[str, Any]]) -> None:
    """Unicidade e invariantes financeiras antes de publicar."""
    seen: set[str] = set()
    for row in rows:
        key = str(row.get("assessment_id") or "")
        if not key:
            raise RuntimeError("avaliação sem assessment_id")
        if key in seen:
            raise RuntimeError(f"assessment_id duplicado: {key}")
        seen.add(key)
    assert_anomaly_rows_not_additive(rows)


def reconcile(
    expectation_totals: Mapping[str, float], reference_totals: Mapping[str, float], *, tolerance: float = 1e-4
) -> None:
    """A referência precisa fechar com o snapshot do forecast, mês a mês."""
    for month, expected in expectation_totals.items():
        obtained = float(reference_totals.get(month, 0.0))
        if abs(obtained - float(expected)) > max(tolerance, abs(float(expected)) * 1e-9):
            raise RuntimeError(
                f"reconciliação falhou em {month}: snapshot={expected} referência={obtained}"
            )


# --------------------------------------------------------------------------
# Orquestração
# --------------------------------------------------------------------------
def _horizon_end(contract: ForecastContract, horizon_days: int) -> date:
    return contract.cutoff_date + timedelta(days=max(1, int(horizon_days)))


def _d2_data_end(detected_at: datetime) -> date:
    """Último dia elegível quando as fontes cloud chegam com atraso D-2."""
    instant = detected_at
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc).date() - timedelta(days=2)


async def build_outcome(
    *,
    config: Mapping[str, Any],
    contract: ForecastContract,
    expectation: Sequence[Mapping[str, Any]],
    observed: Sequence[Mapping[str, Any]],
    dimensions: Sequence[str],
    family_keys: Sequence[str],
    detected_at: datetime,
    reader_weekday_weeks: int = 8,
    analysis_data_end: date | None = None,
) -> DetectionOutcome:
    """Monta a referência e as avaliações (sem publicar).

    Função com I/O já resolvido: recebe expectativa e observado prontos, o que a
    torna testável sem Trino.
    """
    profile = resolve_parameters(
        profile=config.get("profile"),
        daily_quantile=config.get("daily_quantile"),
        min_relative_deviation=config.get("min_relative_deviation"),
        investigate_score=config.get("investigate_score"),
    )
    materiality, explicit = materiality_for(
        config.get("materiality_by_currency"), config.get("currency")
    )
    horizon_days = int(config.get("horizon_days") or 15)
    calibration_days = int(config.get("calibration_days") or 60)

    months = horizon_months(contract.cutoff_date, horizon_days)
    horizon_end = _horizon_end(contract, horizon_days)
    evaluation_end = min(horizon_end, analysis_data_end) if analysis_data_end else horizon_end
    outcome = DetectionOutcome(contract=contract)
    if not explicit:
        outcome.warnings.append(
            f"materialidade não declarada para a moeda {config.get('currency')}: "
            f"usando padrão {materiality}"
        )

    # Perfil semanal global, usado como fallback de séries com pouco histórico.
    all_daily = {
        row["event_date"]: float(row.get("observed_cloud") or 0.0)
        for row in observed
        if isinstance(row.get("event_date"), date)
    }
    global_weekly = weekday_profile(all_daily, as_of=contract.cutoff_date, weeks=reader_weekday_weeks)

    grouped = group_by_series(observed, dimensions)
    expectation_by_series: dict[tuple, list[Mapping[str, Any]]] = {}
    for row in expectation:
        key = tuple(str(row.get(dim) or "") for dim in dimensions)
        expectation_by_series.setdefault(key, []).append(row)

    reference_rows: list[Any] = []
    detections: list[Any] = []
    detected_by_family: dict[str, list[Any]] = {}
    observation_window_start = contract.cutoff_date - timedelta(days=int(config.get("history_days") or 180))
    window_days = max(1, (contract.cutoff_date - observation_window_start).days + 1)

    for key, expected_rows in expectation_by_series.items():
        sample = expected_rows[0]
        series_observed = grouped.get(key, [])
        # A referência cobre TODA série esperada pelo forecast, mesmo sem
        # observação nesta janela: ela descreve a expectativa, e é isso que faz a
        # reconciliação fechar com o snapshot mês a mês. A detecção, essa sim, só
        # roda onde há observado.
        observed_by_day = {
            row["event_date"]: float(row.get("observed_cloud") or 0.0) for row in series_observed
        }
        # Somente o histórico disponível no corte pode definir a distribuição
        # e a tolerância. Os dias avaliados ficam fora da calibração.
        history_by_day = {
            day: value for day, value in observed_by_day.items()
            if day <= contract.cutoff_date
        }
        as_of = contract.cutoff_date
        weekly = (
            weekday_profile(history_by_day, as_of=as_of, weeks=reader_weekday_weeks)
            if history_by_day
            else global_weekly
        )
        if weekly.is_fallback:
            weekly = global_weekly
        complete = bool(history_by_day) and has_complete_daily_calibration(
            history_by_day, as_of=as_of, calibration_days=calibration_days
        )
        residuals = [
            value - weekly.medians.get(day.weekday(), 0.0) for day, value in history_by_day.items()
        ]
        band = calibrate_band(
            residuals=residuals,
            expected_cloud=sum(weekly.medians.values()),
            quantile=profile.daily_quantile,
            tolerance_pct=0.15,
            min_relative_deviation=profile.min_relative_deviation,
            calibration_days=calibration_days,
        )
        rule_refs = _rule_refs_for(contract)
        series_id = _series_key(sample, dimensions)
        family = event_family_for(sample, family_keys)

        rows = build_reference_rows(
            contract,
            expected_rows,
            dimensions=dimensions,
            reference_kind=contract.reference_kind or "PROSPECTIVE",
            target_start=contract.cutoff_date.isoformat(),
            target_end=horizon_end.isoformat(),
            history_days=int(config.get("history_days") or 180),
            calibration_days=calibration_days,
            weekday_profile=weekly,
            band=band,
            horizon_days=horizon_days,
        )
        reference_rows.extend(rows)
        outcome.series_count += 1

        if not series_observed:
            continue

        series = _detect_series(
            series_observed=series_observed,
            reference_rows=rows,
            dimensions=dimensions,
            series_id=series_id,
            family=family,
            sample=sample,
            weekly=weekly,
            band=band,
            profile=profile,
            materiality=materiality,
            calibration_days=calibration_days,
            complete=complete,
            window_days=window_days,
            horizon_end=evaluation_end,
            applied_rule_refs=rule_refs,
        )
        detections.extend(series)
        for detection in series:
            detected_by_family.setdefault(family, []).append(detection)

    # Consolidação por família: nunca duplica impacto.
    consolidated: list[Any] = []
    for family, family_detections in detected_by_family.items():
        merged = consolidate_family(family_detections)
        if merged is not None:
            consolidated.append(merged)

    outcome.reference_rows = reference_rows
    outcome.detections = consolidated
    outcome.assessments = to_assessment_rows(
        consolidated,
        dimensions=dimensions,
        detected_at=detected_at,
        forecast_run_id=contract.run_id,
        evaluation_start=contract.cutoff_date,
        evaluation_end=evaluation_end,
    )

    for month in months:
        useful = [
            row for row in reference_rows if row.reference_month == month
        ]
        outcome.reference_totals[month] = sum(row.predicted_cloud for row in useful)
        outcome.expectation_totals[month] = sum(
            float(row.get("expected_cloud") or 0.0)
            for row in expectation
            if str(row.get("forecast_month")) == month
        )
    return outcome


def _rule_refs_for(contract: ForecastContract) -> list[dict[str, Any]]:
    return [rule.as_dict() for rule in contract.applied_rule_refs]


def _detect_series(
    *,
    series_observed: Sequence[Mapping[str, Any]],
    reference_rows: Sequence[Any],
    dimensions: Sequence[str],
    series_id: str,
    family: str,
    sample: Mapping[str, Any],
    weekly: Any,
    band: Any,
    profile: Any,
    materiality: float,
    calibration_days: int,
    complete: bool,
    window_days: int,
    horizon_end: date,
    applied_rule_refs: Sequence[Mapping[str, Any]],
) -> list[Any]:
    observed_by_day = {
        row["event_date"]: float(row.get("observed_cloud") or 0.0) for row in series_observed
    }
    lumpy = is_lumpy_series(series_observed, window_days=window_days)
    if lumpy:
        return _evaluate_monthly_rows(
            observed_by_day=observed_by_day,
            reference_rows=reference_rows,
            series_id=series_id,
            family=family,
            dimensions=dimensions,
            sample=sample,
            profile=profile,
            materiality=materiality,
            applied_rule_refs=applied_rule_refs,
        )

    points: list[SeriesPoint] = []
    for reference in reference_rows:
        coverage_kind, cov_start, cov_end, _ = classify_coverage(
            reference.reference_month, reference_cutoff(reference)
        )
        if coverage_kind == ELAPSED_MONTH or cov_start is None or cov_end is None:
            continue
        for day in _days(cov_start, cov_end):
            if day > horizon_end or day not in observed_by_day:
                continue
            expected = _daily_expected(reference, day, weekly)
            points.append(
                SeriesPoint(
                    series_id=series_id,
                    dimensions={dim: sample.get(dim) for dim in dimensions},
                    event_date=day,
                    observed_cloud=observed_by_day[day],
                    expected_cloud=expected,
                    upper_cloud=expected + band.upper_offset,
                    lower_cloud=max(0.0, expected - band.lower_offset),
                    reference_id=reference.reference_id,
                    reference_kind=reference.reference_kind,
                    source_billing_period=sample.get("source_billing_period"),
                )
            )
    if not points:
        return []
    detection = detect_daily_consumption(
        points,
        profile=profile,
        materiality=materiality,
        band=band,
        event_family=family,
        reference_kind=points[0].reference_kind,
        complete_calibration=complete,
        applied_rule_refs=applied_rule_refs,
    )
    if detection is None:
        return []
    if any(
        ref_month_total(reference) <= 0
        and weekly
        and not weekly.is_fallback
        and weekly.medians.get(point.event_date.weekday(), 0.0) > 0
        for reference in reference_rows
        for point in points
        if point.reference_id == reference.reference_id
    ):
        detection.reason_codes.append("HISTORICAL_BASELINE_ZERO_FORECAST")
    return [detection]


def _evaluate_monthly_rows(
    *,
    observed_by_day: Mapping[date, float],
    reference_rows: Sequence[Any],
    series_id: str,
    family: str,
    dimensions: Sequence[str],
    sample: Mapping[str, Any],
    profile: Any,
    materiality: float,
    applied_rule_refs: Sequence[Mapping[str, Any]],
) -> list[Any]:
    detections: list[Any] = []
    for reference in reference_rows:
        coverage_kind, cov_start, cov_end, _ = classify_coverage(
            reference.reference_month, reference_cutoff(reference)
        )
        if coverage_kind == ELAPSED_MONTH or cov_start is None or cov_end is None:
            continue
        events: list[MonthlyEvent] = []
        for day in _days(cov_start, cov_end):
            if day not in observed_by_day:
                continue
            expected = ref_month_total(reference)
            upper = reference.band_upper_cloud if reference.band_upper_cloud is not None else expected
            lower = reference.band_lower_cloud if reference.band_lower_cloud is not None else expected
            events.append(
                MonthlyEvent(
                    series_id=series_id,
                    dimensions={dim: sample.get(dim) for dim in dimensions},
                    event_date=day,
                    observed_cloud=observed_by_day[day],
                    expected_cloud=expected,
                    upper_cloud=upper,
                    lower_cloud=lower,
                    reference_id=reference.reference_id,
                    reference_kind=reference.reference_kind,
                    source_billing_period=sample.get("source_billing_period"),
                    pre_event_reference=bool(reference.reference_kind and reference.reference_kind != "UNAVAILABLE"),
                    matched_rule_refs=[dict(rule) for rule in applied_rule_refs],
                )
            )
        if not events:
            continue
        band = calibrate_monthly_event_band(
            residuals=[], expected_cloud=ref_month_total(reference), tolerance_pct=0.15
        )
        for event in events:
            detections.append(
                detect_monthly_event(
                    event,
                    profile=profile,
                    materiality=materiality,
                    band=band,
                    event_family=family,
                )
            )
        accumulated = detect_monthly_accumulated(
            series_id=series_id,
            dimensions={dim: sample.get(dim) for dim in dimensions},
            event_family=family,
            period=reference.reference_month,
            events=events,
            profile=profile,
            materiality=materiality,
            band=band,
        )
        if accumulated is not None:
            detections.append(accumulated)
    return detections


async def execute_detection(
    config_id: int,
    *,
    run_id: int | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    should_abort: Callable[[], bool] | None = None,
    detected_at: datetime | None = None,
) -> dict[str, Any]:
    """Executa uma detecção completa e publica o resultado.

    Uma execução por configuração (plano §6). A publicação corrente só é trocada
    depois do commit do Iceberg; qualquer falha antes disso preserva a
    publicação anterior.
    """
    from app.models.destination import DestinationTable
    from app.models.forecast import ForecastConfig

    from app.services.anomaly_forecast_adapter import DEFAULT_MEASURE

    detected_at = detected_at or datetime.now(timezone.utc)
    report = _reporter(progress)

    async with async_session() as session:
        config_row = await session.get(_anomaly_config_model(), config_id)
        if config_row is None:
            raise AnomalyRunError(f"configuração de anomalia {config_id} não encontrada")
        if not bool(config_row.enabled):
            raise AnomalyRunError(
                f"configuração {config_id} está desabilitada: habilite explicitamente antes de processar"
            )
        forecast = (
            await session.get(ForecastConfig, config_row.forecast_config_id)
            if config_row.forecast_config_id
            else None
        )
        if forecast is None:
            raise AnomalyRunError("configuração de anomalia sem forecast vinculado")
        destination = await session.get(DestinationTable, forecast.destination_table_id)
        if destination is None:
            raise AnomalyRunError("destino do forecast não encontrado")

        dimensions = list(config_row.dimensions or forecast.dimensions or [])
        family_keys = _family_keys(config_row, dimensions)
        config_map = {
            "profile": config_row.profile,
            "daily_quantile": config_row.daily_quantile,
            "min_relative_deviation": config_row.min_relative_deviation,
            "investigate_score": config_row.investigate_score,
            "materiality_by_currency": config_row.materiality_by_currency,
            "currency": config_row.currency,
            "horizon_days": config_row.horizon_days,
            "calibration_days": config_row.calibration_days,
            "history_days": config_row.history_days,
            "measure_column": config_row.measure_column or DEFAULT_MEASURE,
            "source_namespace": config_row.source_namespace,
            "dimensions": dimensions,
        }

        report(1, 6, "Carregando execuções do forecast")
        rows = (
            await session.execute(
                _forecast_runs_query(config_row.forecast_config_id)
            )
        ).mappings().all()
        contracts: list[ForecastContract] = []
        for row in rows:
            contracts.append(await load_contract(dict(row), fallback=config_map))
        usable = [c for c in contracts if c.is_usable]
        if not usable:
            raise AnomalyRunError(
                "nenhuma execução de forecast utilizável: a medida precisa existir no resultado"
            )
        analysis_data_end = _d2_data_end(detected_at)
        horizon_days = int(config_row.horizon_days or 15)
        # A prospectively valid reference must predate the last D-2 observation
        # and its horizon must include that observation. Selecting today's
        # latest forecast can otherwise compare a day with a forecast already
        # informed by that day's realized value.
        eligible = [
            candidate for candidate in usable
            if candidate.cutoff_date is not None
            and candidate.cutoff_date < analysis_data_end
            and candidate.cutoff_date + timedelta(days=horizon_days) >= analysis_data_end
        ]
        if not eligible:
            raise AnomalyRunError(
                "sem forecast prospectivo anterior que cubra o corte D-2 "
                f"({analysis_data_end.isoformat()}); nenhuma anomalia foi avaliada"
            )
        contract = max(eligible, key=lambda c: (c.cutoff_date, c.run_id))
        # A execução avalia os dias POSTERIORES ao próprio corte: por construção a
        # referência é prospectiva. Reconstrução histórica (E5) marca
        # RETROSPECTIVE explicitamente.
        contract.reference_kind = "PROSPECTIVE"
        if _aborted(should_abort):
            await finish_run(session, run_id=run_id, status="CANCELLED", phase="Cancelado pelo usuário")
            await session.commit()
            return {"status": "CANCELLED"}

        months = horizon_months(contract.cutoff_date, horizon_days)
        horizon_end = contract.cutoff_date + timedelta(days=horizon_days)
        history_days = int(config_row.history_days or 180)
        observed_start = contract.cutoff_date - timedelta(days=history_days)

        report(2, 6, "Lendo expectativa do snapshot do forecast")
        expectation = await read_expectation(contract, months=months)
        report(3, 6, "Lendo consumo observado (realizado_cloud)")
        observed = await read_daily_observed(
            source_namespace=contract.source_namespace,
            table=contract.destination_physical_name,
            date_column=config_row.date_column,
            period_column=config_row.period_column,
            type_column=config_row.type_column,
            measure_column=config_row.measure_column,
            dimensions=dimensions,
            start=observed_start,
            end=min(horizon_end, analysis_data_end),
        )
        if _aborted(should_abort):
            await finish_run(session, run_id=run_id, status="CANCELLED", phase="Cancelado pelo usuário")
            await session.commit()
            return {"status": "CANCELLED"}

        report(4, 6, "Avaliando séries e consolidando episódios")
        outcome = await build_outcome(
            config=config_map,
            contract=contract,
            expectation=expectation,
            observed=observed,
            dimensions=dimensions,
            family_keys=family_keys,
            detected_at=detected_at,
            analysis_data_end=analysis_data_end,
        )
        validate_assessments(outcome.assessments)
        reconcile(outcome.expectation_totals, outcome.reference_totals)

        # Os casos são resolvidos ANTES da publicação do Iceberg, na mesma
        # transação: a linha publicada já carrega `case_id`. Se a publicação
        # falhar, o rollback descarta os casos e a publicação anterior segue
        # válida.
        report(5, 6, "Atualizando episódios")
        created = updated = 0
        case_id_by_assessment: dict[str, int] = {}
        for detection in outcome.detections:
            case_outcome = await apply_detection_to_case(
                session,
                detection=detection,
                anomaly_config_id=config_id,
            )
            if case_outcome.created:
                created += 1
            elif case_outcome.updated or case_outcome.reopened:
                updated += 1
            if case_outcome.case_id is not None:
                case_id_by_assessment[assessment_id_of(detection)] = case_outcome.case_id
        for row in outcome.assessments:
            row["case_id"] = case_id_by_assessment.get(str(row.get("assessment_id")))

        report(6, 6, "Publicando referência e avaliações")
        from app.services.anomaly_schema import assessment_columns, reference_columns

        published = publish_iceberg(
            reference_rows=[row.as_dict() for row in outcome.reference_rows],
            assessment_rows=outcome.assessments,
            reference_columns=[name for name, _ in reference_columns()],
            assessment_columns=[name for name, _ in assessment_columns()],
            reference_table=REFERENCE_TABLE,
            assessment_table=ASSESSMENT_TABLE,
            namespace=layers.detail_layer(),
        )

        reference_table = published["reference"]["table"]
        assessment_table = published["assessment"]["table"]

        await commit_publication(
            session,
            anomaly_config_id=config_id,
            run_id=run_id,
            reference_table=reference_table,
            assessment_table=assessment_table,
            data_cutoff=contract.cutoff_date.isoformat(),
            generated_at=detected_at,
            reference_rows=published["reference"]["rows"],
            assessment_rows=published["assessment"]["rows"],
            series_count=outcome.series_count,
        )
        await finish_run(
            session,
            run_id=run_id,
            status="COMPLETED",
            phase="Detecção publicada",
            rows_written=published["assessment"]["rows"],
            cases_created=created,
            cases_updated=updated,
            contract=contract,
            reference_table=reference_table,
            assessment_table=assessment_table,
            generated_at=detected_at,
            horizon_end=horizon_end,
            evaluation_end=min(horizon_end, analysis_data_end),
            total_series=outcome.series_count,
            processed_series=outcome.series_count,
        )
        await session.commit()

        summary = {
            "status": "COMPLETED",
            "forecast_run_id": contract.run_id,
            "data_cutoff": contract.cutoff_date.isoformat(),
            "evaluation_end": min(horizon_end, analysis_data_end).isoformat(),
            "reference_kind": contract.reference_kind,
            "reference_table": reference_table,
            "assessment_table": assessment_table,
            "reference_rows": published["reference"]["rows"],
            "assessment_rows": published["assessment"]["rows"],
            "series": outcome.series_count,
            "cases_created": created,
            "cases_updated": updated,
            "warnings": outcome.warnings + list(contract.warnings),
        }
        logger.info("[anomaly] execução concluída: %s", summary)
        return summary


def _anomaly_config_model():
    from app.models.anomaly import AnomalyConfig

    return AnomalyConfig


def _forecast_runs_query(forecast_config_id: int):
    from sqlalchemy import text

    return text(
        """
        SELECT id, forecast_config_id, config_version, trigger_type, status,
               cutoff_date, result_table, destination_physical_name,
               source_snapshot_id, config_snapshot, error_metrics,
               started_at, finished_at
          FROM forecast_run
         WHERE forecast_config_id = :config_id
           AND status = 'COMPLETED'
           AND result_table IS NOT NULL
         ORDER BY cutoff_date DESC, id DESC
         LIMIT 40
        """
    ).bindparams(config_id=forecast_config_id)


def _family_keys(config_row: Any, dimensions: Sequence[str]) -> list[str]:
    """Chaves que definem "o mesmo gasto" para consolidar contribuintes."""
    configured = list(getattr(config_row, "family_keys", None) or [])
    if configured:
        return [key for key in configured if key in dimensions]
    preferred = [
        "cloud_origem",
        "servicename",
        "servicesubcategory",
        "chargecategory",
        "commitmentdiscounttype",
    ]
    return [key for key in preferred if key in dimensions] or list(dimensions[:1])


def _reporter(progress: Callable[[int, int, str], None] | None):
    def report(done: int, total: int, name: str) -> None:
        if progress is None:
            return
        try:
            progress(done, total, name)
        except Exception:  # progresso nunca derruba a execução
            logger.debug("[anomaly] falha ao reportar progresso", exc_info=True)

    return report


def _aborted(should_abort: Callable[[], bool] | None) -> bool:
    if should_abort is None:
        return False
    try:
        return bool(should_abort())
    except Exception:
        return False
