"""Detectores e consolidação de episódios (E4).

Três detectores, com **resíduos e faixas por granularidade** (plano §2.6):

| Detector | Granularidade | Uso |
|---|---|---|
| `detect_daily_consumption` | dia | séries com consumo diário observável |
| `detect_monthly_event` | evento mensal | compras/renovações e cobranças por intervalo |
| `detect_monthly_accumulated` | acumulado do mês | excesso mensal consolidado do mesmo gasto |

Regras de episódio (plano §3.2) implementadas em `resolve_episode`:

* um caso por **episódio**, não por dia;
* agrupa enquanto não houver **três dias válidos consecutivos** de recuperação;
* reprocessamento **atualiza** evidências e não recria o caso;
* impacto do caso é `max(anterior, novo)` — nunca a soma de reavaliações;
* caso encerrado não reabre por repetição técnica do processamento;
* continuação do mesmo episódio reabre com evento auditado;
* episódio novo após recuperação cria caso vinculado ao anterior;
* evento reconhecido como esperado permanece na tabela analítica, sem workflow.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

from app.services.anomaly_calibration import (
    DAILY_GRAIN,
    MONTHLY_EVENT_GRAIN,
    BandCalibration,
    Profile,
    evaluate_deviation,
    is_candidate,
)
from app.services.anomaly_scoring import (
    CONFIDENCE_LOW,
    CONFIDENCE_UNAVAILABLE,
    ScoreBreakdown,
    confidence_from,
    score_detection,
)
from app.services.anomaly_reference import series_id_for

# --------------------------------------------------------------------------
# Vocabulários
# --------------------------------------------------------------------------
EVENT_DAILY = "DAILY_CONSUMPTION"
EVENT_MONTHLY = "MONTHLY_EVENT"
EVENT_MONTHLY_ACCUMULATED = "MONTHLY_ACCUMULATED"

EVENT_TYPES = (EVENT_DAILY, EVENT_MONTHLY, EVENT_MONTHLY_ACCUMULATED)

ASSESSMENT_CANDIDATE = "CANDIDATE"
ASSESSMENT_OPEN = "OPEN"
ASSESSMENT_EXPECTED = "EXPECTED_EVENT"
ASSESSMENT_BELOW_PROFILE = "BELOW_PROFILE"
ASSESSMENT_DATA_INSUFFICIENT = "DATA_INSUFFICIENT"
ASSESSMENT_RULE_MISMATCH = "FORECAST_RULE_MISMATCH"
ASSESSMENT_NO_RULE = "NO_APPLICABLE_FORECAST_RULE"

ASSESSMENT_STATUSES = (
    ASSESSMENT_CANDIDATE,
    ASSESSMENT_OPEN,
    ASSESSMENT_EXPECTED,
    ASSESSMENT_BELOW_PROFILE,
    ASSESSMENT_DATA_INSUFFICIENT,
    ASSESSMENT_RULE_MISMATCH,
    ASSESSMENT_NO_RULE,
)

# --- reason codes ---------------------------------------------------------
RC_MEASURE_ABSENT = "MEASURE_ABSENT"
RC_MEASURE_ALL_ZERO = "MEASURE_ALL_ZERO"
RC_NO_DAILY_GRAIN = "NO_DAILY_GRAIN"
RC_PARTIAL_DATA = "PARTIAL_DATA"
RC_REFERENCE_UNAVAILABLE = "REFERENCE_UNAVAILABLE"
RC_EXPLICIT_TOLERANCE = "EXPLICIT_TOLERANCE"
RC_UNIFORM_FALLBACK = "UNIFORM_FALLBACK"
RC_EXPECTED_MONTHLY_EVENT = "EXPECTED_MONTHLY_EVENT"
RC_FORECAST_RULE_MISMATCH = "FORECAST_RULE_MISMATCH"
RC_NO_APPLICABLE_FORECAST_RULE = "NO_APPLICABLE_FORECAST_RULE"
RC_CALENDAR_SHIFT = "CALENDAR_SHIFT_EVIDENCE"
RC_FEW_MONTHLY_EVENTS = "FEW_MONTHLY_EVENTS"
RC_BELOW_PROFILE = "BELOW_PROFILE"
RC_BELOW_MATERIALITY = "BELOW_MATERIALITY"
RC_WITHIN_BAND = "WITHIN_BAND"
RC_RECURRENT_PURCHASE = "RECURRENT_PURCHASE_EXPECTATION"

#: Dias válidos consecutivos que encerram o agrupamento do episódio (§3.2).
RECOVERY_VALID_DAYS = 3
#: Eventos anteriores comparáveis exigidos para avaliar deslocamento de calendário.
MIN_COMPARABLE_EVENTS_FOR_SHIFT = 3
#: Tolerância de deslocamento de calendário, em dias.
CALENDAR_SHIFT_TOLERANCE_DAYS = 5


# --------------------------------------------------------------------------
# Entrada
# --------------------------------------------------------------------------
@dataclass
class SeriesPoint:
    """Um ponto observado de uma série com a referência correspondente."""

    series_id: str
    dimensions: Mapping[str, Any]
    event_date: date
    observed_cloud: float
    expected_cloud: float
    upper_cloud: float
    lower_cloud: float
    reference_id: str | None = None
    reference_kind: str = "PROSPECTIVE"
    source_billing_period: str | None = None
    data_partial: bool = False
    contributors: list[Mapping[str, Any]] = field(default_factory=list)


@dataclass
class Detection:
    """Resultado consolidado de um episódio."""

    series_id: str
    series_dimensions: dict[str, Any]
    event_family: str
    event_type: str
    event_date: str
    period: str
    source_billing_period: str | None
    observed_cloud: float
    expected_cloud: float
    lower_cloud: float
    upper_cloud: float
    delta_cloud: float
    anomalous_cost_cloud: float
    score: float
    severity: str
    confidence: str
    assessment_status: str
    evaluation_grain: str
    consecutive_occurrences: int
    reason_codes: list[str]
    reference_id: str | None
    reference_kind: str
    applied_rule_refs: list[dict[str, Any]] = field(default_factory=list)
    contributors: list[dict[str, Any]] = field(default_factory=list)
    components: dict[str, Any] = field(default_factory=dict)
    case_key: str = ""
    expected_only: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "series_id": self.series_id,
            "event_family": self.event_family,
            "event_type": self.event_type,
            "event_date": self.event_date,
            "period": self.period,
            "source_billing_period": self.source_billing_period,
            "observed_cloud": self.observed_cloud,
            "expected_cloud": self.expected_cloud,
            "lower_cloud": self.lower_cloud,
            "upper_cloud": self.upper_cloud,
            "delta_cloud": self.delta_cloud,
            "anomalous_cost_cloud": self.anomalous_cost_cloud,
            "score": self.score,
            "severity": self.severity,
            "confidence": self.confidence,
            "assessment_status": self.assessment_status,
            "evaluation_grain": self.evaluation_grain,
            "reason_codes": list(self.reason_codes),
            "reference_id": self.reference_id,
            "reference_kind": self.reference_kind,
            "applied_rule_refs": list(self.applied_rule_refs),
            "contributors": list(self.contributors),
            "components": dict(self.components),
            "case_key": self.case_key,
            **self.series_dimensions,
        }


def month_of(day: date) -> str:
    return f"{day.year:04d}{day.month:02d}"


def case_key_for(series_id: str, event_family: str, event_type: str, *, period: str | None = None) -> str:
    """Identidade determinística do episódio.

    Diário: série + família de evento. Mensal: acrescenta o período esperado,
    para relacionar todas as avaliações ao **mesmo** evento/compra (plano §3.2).
    """
    parts = [series_id, event_family, event_type]
    if event_type in (EVENT_MONTHLY, EVENT_MONTHLY_ACCUMULATED) and period:
        parts.append(period)
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return digest


# --------------------------------------------------------------------------
# Detector diário
# --------------------------------------------------------------------------
def detect_daily_consumption(
    points: Sequence[SeriesPoint],
    *,
    profile: Profile,
    materiality: float,
    band: BandCalibration | None,
    event_family: str,
    reference_kind: str = "PROSPECTIVE",
    complete_calibration: bool = True,
    applied_rule_refs: Sequence[Mapping[str, Any]] = (),
    recovery_valid_days: int = RECOVERY_VALID_DAYS,
) -> Detection | None:
    """Detector de consumo diário: agrupa dias anômalos consecutivos.

    O custo anômalo do episódio é a **soma** dos excessos diários do episódio —
    cada dia conta uma vez, e reavaliações posteriores substituem o valor em vez
    de somar de novo.
    """
    ordered = sorted(points, key=lambda point: point.event_date)
    if not ordered:
        return None

    run: list[SeriesPoint] = []
    runs: list[list[SeriesPoint]] = []
    previous_day: date | None = None
    for point in ordered:
        if point.data_partial and point.observed_cloud <= 0:
            # Dia sem coleta não vira zero nem interrompe artificialmente.
            continue
        candidate = _is_daily_candidate(point, profile=profile, materiality=materiality, band=band)
        consecutive = previous_day is not None and (point.event_date - previous_day).days == 1
        if candidate and (not run or consecutive):
            run.append(point)
        elif candidate:
            runs.append(run)
            run = [point]
        else:
            if run:
                runs.append(run)
            run = []
        previous_day = point.event_date
    if run:
        runs.append(run)

    if not runs:
        return _below_profile_detection(
            ordered[-1], event_type=EVENT_DAILY, event_family=event_family,
            profile=profile, band=band, materiality=materiality,
            reference_kind=reference_kind, applied_rule_refs=applied_rule_refs,
        )

    episode = max(runs, key=len)
    observed = sum(point.observed_cloud for point in episode)
    expected = sum(point.expected_cloud for point in episode)
    upper = sum(point.upper_cloud for point in episode)
    lower = sum(point.lower_cloud for point in episode)
    anomalous = sum(max(0.0, point.observed_cloud - point.upper_cloud) for point in episode)
    last = episode[-1]
    reason_codes = _daily_reason_codes(episode, band=band)
    if len(episode) < recovery_valid_days:
        reason_codes.append("EPISODE_BELOW_RECOVERY_WINDOW")

    breakdown = score_detection(
        delta_cloud=observed - expected,
        anomalous_cost_cloud=anomalous,
        expected_cloud=expected,
        upper_cloud=upper,
        materiality=materiality,
        consecutive_occurrences=len(episode),
    )
    confidence = confidence_from(
        band_calibrated=bool(band and band.is_calibrated),
        band_confidence=band.confidence if band else "UNAVAILABLE",
        complete_calibration=complete_calibration,
        data_partial=any(point.data_partial for point in episode),
        reference_kind=reference_kind,
    )
    status = ASSESSMENT_CANDIDATE if any(p.data_partial for p in episode) else ASSESSMENT_OPEN
    if breakdown.score < profile.investigate_score and status == ASSESSMENT_OPEN:
        status = ASSESSMENT_BELOW_PROFILE
        reason_codes.append(RC_BELOW_PROFILE)

    return Detection(
        series_id=last.series_id,
        series_dimensions=dict(last.dimensions),
        event_family=event_family,
        event_type=EVENT_DAILY,
        event_date=last.event_date.isoformat(),
        period=month_of(last.event_date),
        source_billing_period=last.source_billing_period,
        observed_cloud=observed,
        expected_cloud=expected,
        lower_cloud=lower,
        upper_cloud=upper,
        delta_cloud=observed - expected,
        anomalous_cost_cloud=anomalous,
        score=breakdown.score,
        severity=breakdown.severity,
        confidence=confidence,
        assessment_status=status,
        evaluation_grain=DAILY_GRAIN,
        consecutive_occurrences=len(episode),
        reason_codes=reason_codes,
        reference_id=last.reference_id,
        reference_kind=reference_kind,
        applied_rule_refs=[dict(rule) for rule in applied_rule_refs],
        contributors=[_contributor(point) for point in episode],
        components=breakdown.as_dict(),
        case_key=case_key_for(last.series_id, event_family, EVENT_DAILY),
    )


def _is_daily_candidate(
    point: SeriesPoint, *, profile: Profile, materiality: float, band: BandCalibration | None
) -> bool:
    calibration = band_from_limits(
        expected_cloud=point.expected_cloud,
        upper_cloud=point.upper_cloud,
        lower_cloud=point.lower_cloud,
        base=band,
    )
    deviation = evaluate_deviation(
        point.observed_cloud, expected_cloud=point.expected_cloud, band=calibration
    )
    ok, _ = is_candidate(
        deviation,
        min_relative_deviation=profile.min_relative_deviation,
        materiality=materiality,
    )
    return ok


def band_from_limits(
    *,
    expected_cloud: float,
    upper_cloud: float,
    lower_cloud: float,
    base: BandCalibration | None = None,
    granularity: str = DAILY_GRAIN,
) -> BandCalibration:
    """Faixa a partir dos limites que a REFERÊNCIA já gravou.

    A referência é a fonte de verdade dos números; a calibração contribui com
    método, confiança e tamanho de amostra. Sem isso o detector recalcularia a
    faixa e divergiria do que foi publicado.
    """
    return BandCalibration(
        method=(base.method if base else "EXPLICIT_TOLERANCE"),
        alpha=(base.alpha if base else 0.025),
        upper_offset=max(0.0, float(upper_cloud) - float(expected_cloud)),
        lower_offset=max(0.0, float(expected_cloud) - float(lower_cloud)),
        sample_size=(base.sample_size if base else 0),
        min_sample=(base.min_sample if base else 39),
        relative_offset=(base.relative_offset if base else None),
        sample_sufficient=bool(base and base.sample_sufficient),
        confidence=(base.confidence if base else CONFIDENCE_UNAVAILABLE),
        note=(base.note if base else None),
        granularity=(base.granularity if base else granularity),
        calibration_days=(base.calibration_days if base else 0),
    )


def _uncalibrated_band() -> BandCalibration:
    return BandCalibration(
        method="EXPLICIT_TOLERANCE",
        alpha=0.025,
        upper_offset=0.0,
        lower_offset=0.0,
        sample_size=0,
        min_sample=39,
        relative_offset=0.0,
        sample_sufficient=False,
        confidence=CONFIDENCE_UNAVAILABLE,
        granularity=DAILY_GRAIN,
    )


def _daily_reason_codes(points: Sequence[SeriesPoint], *, band: BandCalibration | None) -> list[str]:
    codes: list[str] = []
    if band is not None:
        if band.method == "EXPLICIT_TOLERANCE":
            codes.append(RC_EXPLICIT_TOLERANCE)
        if "UNIFORM" in str(band.note or ""):
            codes.append(RC_UNIFORM_FALLBACK)
    if any(point.data_partial for point in points):
        codes.append(RC_PARTIAL_DATA)
    return codes


def _contributor(point: SeriesPoint) -> dict[str, Any]:
    return {
        "event_date": point.event_date.isoformat(),
        "observed_cloud": point.observed_cloud,
        "expected_cloud": point.expected_cloud,
        "upper_cloud": point.upper_cloud,
        "anomalous_cost_cloud": max(0.0, point.observed_cloud - point.upper_cloud),
        "dimensions": dict(point.dimensions),
        "contributors": [dict(item) for item in point.contributors],
    }


def _below_profile_detection(
    point: SeriesPoint,
    *,
    event_type: str,
    event_family: str,
    profile: Profile,
    band: BandCalibration | None,
    materiality: float,
    reference_kind: str,
    applied_rule_refs: Sequence[Mapping[str, Any]],
) -> Detection:
    calibration = band_from_limits(
        expected_cloud=point.expected_cloud,
        upper_cloud=point.upper_cloud,
        lower_cloud=point.lower_cloud,
        base=band,
    )
    deviation = evaluate_deviation(
        point.observed_cloud, expected_cloud=point.expected_cloud, band=calibration
    )
    breakdown = score_detection(
        delta_cloud=deviation.delta_cloud,
        anomalous_cost_cloud=deviation.anomalous_cost_cloud,
        expected_cloud=deviation.expected_cloud,
        upper_cloud=deviation.upper_limit,
        materiality=materiality,
        consecutive_occurrences=1,
    )
    _, reasons = is_candidate(
        deviation,
        min_relative_deviation=profile.min_relative_deviation,
        materiality=materiality,
    )
    return Detection(
        series_id=point.series_id,
        series_dimensions=dict(point.dimensions),
        event_family=event_family,
        event_type=event_type,
        event_date=point.event_date.isoformat(),
        period=month_of(point.event_date),
        source_billing_period=point.source_billing_period,
        observed_cloud=deviation.observed_cloud,
        expected_cloud=deviation.expected_cloud,
        lower_cloud=deviation.lower_limit,
        upper_cloud=deviation.upper_limit,
        delta_cloud=deviation.delta_cloud,
        anomalous_cost_cloud=deviation.anomalous_cost_cloud,
        score=breakdown.score,
        severity=breakdown.severity,
        confidence=confidence_from(
            band_calibrated=bool(band and band.is_calibrated),
            band_confidence=band.confidence if band else "UNAVAILABLE",
            complete_calibration=True,
            data_partial=point.data_partial,
            reference_kind=reference_kind,
        ),
        assessment_status=ASSESSMENT_BELOW_PROFILE,
        evaluation_grain=DAILY_GRAIN,
        consecutive_occurrences=0,
        reason_codes=list(reasons) or [RC_WITHIN_BAND],
        reference_id=point.reference_id,
        reference_kind=reference_kind,
        applied_rule_refs=[dict(rule) for rule in applied_rule_refs],
        contributors=[_contributor(point)],
        components=breakdown.as_dict(),
        case_key=case_key_for(point.series_id, event_family, event_type),
    )


# --------------------------------------------------------------------------
# Detector de evento mensal
# --------------------------------------------------------------------------
@dataclass
class MonthlyEvent:
    """Uma cobrança mensal observada e o que se esperava antes dela."""

    series_id: str
    dimensions: Mapping[str, Any]
    event_date: date
    observed_cloud: float
    expected_cloud: float
    upper_cloud: float
    lower_cloud: float
    reference_id: str | None = None
    reference_kind: str = "PROSPECTIVE"
    source_billing_period: str | None = None
    data_partial: bool = False
    #: A referência usada foi congelada antes da compra? (plano §2.4)
    pre_event_reference: bool = True
    #: Rastro de regras que casaram com a série.
    matched_rule_refs: list[dict[str, Any]] = field(default_factory=list)
    #: Regra casou mas o valor/contexto é incompatível.
    rule_mismatch: bool = False
    #: Contexto da cobrança (ex.: quantity), apenas evidência.
    context: dict[str, Any] = field(default_factory=dict)


def detect_monthly_event(
    event: MonthlyEvent,
    *,
    profile: Profile,
    materiality: float,
    band: BandCalibration | None,
    event_family: str,
    previous_events: Sequence[MonthlyEvent] = (),
    monthly_min_events: int = 3,
) -> Detection:
    """Detector de evento mensal (compra/renovação/cobrança por intervalo).

    Um evento **esperado** não abre caso: o reconhecimento fica na tabela
    analítica com `assessment_status = EXPECTED_EVENT` (plano §3.2 e §2.4).
    """
    reason_codes: list[str] = []
    if event.data_partial:
        reason_codes.append(RC_PARTIAL_DATA)
    if band is not None and band.method == "EXPLICIT_TOLERANCE":
        reason_codes.append(RC_EXPLICIT_TOLERANCE)
        reason_codes.append(RC_FEW_MONTHLY_EVENTS)
    if event.matched_rule_refs:
        reason_codes.append(RC_RECURRENT_PURCHASE)

    shift_days, shift_evidence = calendar_shift_days(
        [previous.event_date for previous in previous_events if previous.event_date < event.event_date],
        event.event_date,
        minimum_events=MIN_COMPARABLE_EVENTS_FOR_SHIFT,
    )
    if shift_evidence:
        reason_codes.append(RC_CALENDAR_SHIFT)
    else:
        shift_days = None

    calibration = band_from_limits(
        expected_cloud=event.expected_cloud,
        upper_cloud=event.upper_cloud,
        lower_cloud=event.lower_cloud,
        base=band,
        granularity=MONTHLY_EVENT_GRAIN,
    )
    deviation = evaluate_deviation(
        event.observed_cloud, expected_cloud=event.expected_cloud, band=calibration
    )
    breakdown = score_detection(
        delta_cloud=deviation.delta_cloud,
        anomalous_cost_cloud=deviation.anomalous_cost_cloud,
        expected_cloud=deviation.expected_cloud,
        upper_cloud=deviation.upper_limit,
        materiality=materiality,
        consecutive_occurrences=max(1, len(previous_events) + 1),
    )
    confidence = confidence_from(
        band_calibrated=bool(band and band.is_calibrated),
        band_confidence=band.confidence if band else "UNAVAILABLE",
        complete_calibration=True,
        data_partial=event.data_partial,
        reference_kind=event.reference_kind,
        few_monthly_events=len(previous_events) < max(2, int(monthly_min_events)),
    )

    status = ASSESSMENT_OPEN
    expected_only = False
    if not event.pre_event_reference:
        status = ASSESSMENT_DATA_INSUFFICIENT
        reason_codes.append(RC_REFERENCE_UNAVAILABLE)
    elif event.rule_mismatch:
        status = ASSESSMENT_RULE_MISMATCH
        reason_codes.append(RC_FORECAST_RULE_MISMATCH)
    elif not deviation.above_band:
        # Dentro da faixa: compra esperada, sem caso operacional.
        status = ASSESSMENT_EXPECTED
        expected_only = True
        reason_codes.append(RC_EXPECTED_MONTHLY_EVENT)
    elif event.data_partial:
        status = ASSESSMENT_CANDIDATE

    if status == ASSESSMENT_OPEN:
        ok, reasons = is_candidate(
            deviation,
            min_relative_deviation=profile.min_relative_deviation,
            materiality=materiality,
        )
        if not ok:
            status = ASSESSMENT_BELOW_PROFILE
            reason_codes.extend(reasons)

    return Detection(
        series_id=event.series_id,
        series_dimensions=dict(event.dimensions),
        event_family=event_family,
        event_type=EVENT_MONTHLY,
        event_date=event.event_date.isoformat(),
        period=month_of(event.event_date),
        source_billing_period=event.source_billing_period,
        observed_cloud=deviation.observed_cloud,
        expected_cloud=deviation.expected_cloud,
        lower_cloud=deviation.lower_limit,
        upper_cloud=deviation.upper_limit,
        delta_cloud=deviation.delta_cloud,
        anomalous_cost_cloud=deviation.anomalous_cost_cloud,
        score=breakdown.score,
        severity=breakdown.severity,
        confidence=confidence,
        assessment_status=status,
        evaluation_grain=MONTHLY_EVENT_GRAIN,
        consecutive_occurrences=max(1, len(previous_events) + 1),
        reason_codes=reason_codes,
        reference_id=event.reference_id,
        reference_kind=event.reference_kind,
        applied_rule_refs=[dict(rule) for rule in event.matched_rule_refs],
        contributors=[_contributor_from_event(event, shift_days=shift_days)],
        components={**breakdown.as_dict(), "calendar_shift_days": shift_days},
        case_key=case_key_for(
            event.series_id, event_family, EVENT_MONTHLY, period=month_of(event.event_date)
        ),
        expected_only=expected_only,
    )


def _contributor_from_event(event: MonthlyEvent, *, shift_days: int | None) -> dict[str, Any]:
    return {
        "event_date": event.event_date.isoformat(),
        "observed_cloud": event.observed_cloud,
        "expected_cloud": event.expected_cloud,
        "upper_cloud": event.upper_cloud,
        "anomalous_cost_cloud": max(0.0, event.observed_cloud - event.upper_cloud),
        "dimensions": dict(event.dimensions),
        "pre_event_reference": event.pre_event_reference,
        "calendar_shift_days": shift_days,
        "context": dict(event.context),
    }


def calendar_shift_days(
    previous_dates: Sequence[date],
    current: date,
    *,
    minimum_events: int = MIN_COMPARABLE_EVENTS_FOR_SHIFT,
    tolerance_days: int = CALENDAR_SHIFT_TOLERANCE_DAYS,
) -> tuple[int | None, bool]:
    """Deslocamento de calendário só com pelo menos três eventos comparáveis.

    Uma regra mensal **não** implica conhecimento do dia exato (plano §2.4). Sem
    histórico suficiente, o detector se abstém de julgar calendário.
    """
    dates = [day for day in previous_dates if isinstance(day, date)]
    if len(dates) < max(1, int(minimum_events)):
        return None, False
    reference_day = int(median(day.day for day in dates))
    shift = int(current.day) - reference_day
    return shift, abs(shift) > int(tolerance_days)


def expected_monthly_event(
    *,
    observed_cloud: float,
    expected_cloud: float,
    upper_cloud: float,
    tolerance_pct: float = 0.0,
) -> bool:
    """A compra observada é o evento esperado congelado antes dela?"""
    if observed_cloud > upper_cloud:
        return False
    if tolerance_pct <= 0:
        return True
    ceiling = expected_cloud * (1.0 + float(tolerance_pct))
    return observed_cloud <= max(ceiling, upper_cloud)


# --------------------------------------------------------------------------
# Detector de excesso mensal acumulado
# --------------------------------------------------------------------------
def detect_monthly_accumulated(
    *,
    series_id: str,
    dimensions: Mapping[str, Any],
    event_family: str,
    period: str,
    events: Sequence[MonthlyEvent],
    profile: Profile,
    materiality: float,
    band: BandCalibration | None,
) -> Detection | None:
    """Excesso mensal consolidado do mesmo gasto.

    O acumulado é recalculado a cada avaliação; **não** se soma o excesso
    acumulado novamente a cada dia (plano §2.5). Duas compras do mesmo tipo no
    mês são apresentadas ambas como contribuintes.
    """
    relevant = [event for event in events if event.observed_cloud or event.expected_cloud]
    if not relevant:
        return None

    observed = sum(event.observed_cloud for event in relevant)
    expected = sum(event.expected_cloud for event in relevant)
    upper = sum(event.upper_cloud for event in relevant)
    lower = sum(event.lower_cloud for event in relevant)
    # A faixa do acumulado é a soma das faixas das cobranças: aditiva, coerente
    # com os contribuintes e sem contar o mesmo gasto duas vezes.
    total_upper = upper
    total_lower = lower
    accumulated_band = band_from_limits(
        expected_cloud=expected,
        upper_cloud=total_upper,
        lower_cloud=total_lower,
        base=band,
        granularity=MONTHLY_EVENT_GRAIN,
    )
    deviation = evaluate_deviation(observed, expected_cloud=expected, band=accumulated_band)
    breakdown = score_detection(
        delta_cloud=deviation.delta_cloud,
        anomalous_cost_cloud=deviation.anomalous_cost_cloud,
        expected_cloud=deviation.expected_cloud,
        upper_cloud=deviation.upper_limit,
        materiality=materiality,
        consecutive_occurrences=max(1, len(relevant)),
    )
    reason_codes = []
    if any(event.data_partial for event in relevant):
        reason_codes.append(RC_PARTIAL_DATA)
    if any(event.rule_mismatch for event in relevant):
        reason_codes.append(RC_FORECAST_RULE_MISMATCH)

    status = ASSESSMENT_OPEN
    if deviation.above_band:
        ok, reasons = is_candidate(
            deviation,
            min_relative_deviation=profile.min_relative_deviation,
            materiality=materiality,
        )
        if not ok:
            status = ASSESSMENT_BELOW_PROFILE
            reason_codes.extend(reasons)
    else:
        status = ASSESSMENT_EXPECTED
        reason_codes.append(RC_EXPECTED_MONTHLY_EVENT)

    last = max(relevant, key=lambda event: event.event_date)
    return Detection(
        series_id=series_id,
        series_dimensions=dict(dimensions),
        event_family=event_family,
        event_type=EVENT_MONTHLY_ACCUMULATED,
        event_date=last.event_date.isoformat(),
        period=period,
        source_billing_period=last.source_billing_period,
        observed_cloud=observed,
        expected_cloud=expected,
        lower_cloud=deviation.lower_limit,
        upper_cloud=deviation.upper_limit,
        delta_cloud=deviation.delta_cloud,
        anomalous_cost_cloud=deviation.anomalous_cost_cloud,
        score=breakdown.score,
        severity=breakdown.severity,
        confidence=confidence_from(
            band_calibrated=bool(band and band.is_calibrated),
            band_confidence=band.confidence if band else "UNAVAILABLE",
            complete_calibration=True,
            data_partial=any(event.data_partial for event in relevant),
            reference_kind=last.reference_kind,
            few_monthly_events=len(relevant) < 3,
        ),
        assessment_status=status,
        evaluation_grain=MONTHLY_EVENT_GRAIN,
        consecutive_occurrences=len(relevant),
        reason_codes=reason_codes,
        reference_id=last.reference_id,
        reference_kind=last.reference_kind,
        applied_rule_refs=[dict(rule) for event in relevant for rule in event.matched_rule_refs],
        contributors=[_contributor_from_event(event, shift_days=None) for event in relevant],
        components=breakdown.as_dict(),
        case_key=case_key_for(series_id, event_family, EVENT_MONTHLY_ACCUMULATED, period=period),
        expected_only=status == ASSESSMENT_EXPECTED,
    )


# --------------------------------------------------------------------------
# Contribuintes: sem impacto duplicado
# --------------------------------------------------------------------------
def consolidate_family(detections: Sequence[Detection]) -> Detection | None:
    """Consolida detecções da mesma família **sem duplicar impacto**.

    O consolidado recalcula observado, esperado e limite a partir dos
    contribuintes — não soma o impacto do pai com o dos filhos. Somar os limites
    superiores é a hipótese conservadora (excessos perfeitamente
    correlacionados) e nunca conta o mesmo gasto duas vezes.
    """
    relevant = [detection for detection in detections if detection is not None]
    if not relevant:
        return None
    if len(relevant) == 1:
        return relevant[0]

    base = max(relevant, key=lambda detection: detection.anomalous_cost_cloud)
    observed = sum(detection.observed_cloud for detection in relevant)
    expected = sum(detection.expected_cloud for detection in relevant)
    upper = sum(detection.upper_cloud for detection in relevant)
    lower = sum(detection.lower_cloud for detection in relevant)
    anomalous = max(0.0, observed - upper)
    return Detection(
        series_id=base.series_id,
        series_dimensions=dict(base.series_dimensions),
        event_family=base.event_family,
        event_type=base.event_type,
        event_date=base.event_date,
        period=base.period,
        source_billing_period=base.source_billing_period,
        observed_cloud=observed,
        expected_cloud=expected,
        lower_cloud=lower,
        upper_cloud=upper,
        delta_cloud=observed - expected,
        anomalous_cost_cloud=anomalous,
        score=base.score,
        severity=base.severity,
        confidence=min(
            (detection.confidence for detection in relevant),
            key=lambda value: {"HIGH": 2, "MEDIUM": 1, "LOW": 0, "UNAVAILABLE": 0}.get(value, 0),
        ),
        assessment_status=base.assessment_status,
        evaluation_grain=base.evaluation_grain,
        consecutive_occurrences=max(detection.consecutive_occurrences for detection in relevant),
        reason_codes=sorted({code for detection in relevant for code in detection.reason_codes}),
        reference_id=base.reference_id,
        reference_kind=base.reference_kind,
        applied_rule_refs=[
            rule for detection in relevant for rule in detection.applied_rule_refs
        ],
        contributors=[
            {
                "series_id": detection.series_id,
                "dimensions": dict(detection.series_dimensions),
                "observed_cloud": detection.observed_cloud,
                "expected_cloud": detection.expected_cloud,
                "anomalous_cost_cloud": detection.anomalous_cost_cloud,
                "score": detection.score,
                "contributors": detection.contributors,
            }
            for detection in relevant
        ],
        components={"consolidated_from": len(relevant)},
        case_key=base.case_key,
    )


# --------------------------------------------------------------------------
# Episódios
# --------------------------------------------------------------------------
CREATE = "CREATE"
UPDATE = "UPDATE"
REOPEN = "REOPEN"
LINK_NEW_EPISODE = "LINK_NEW_EPISODE"
IGNORE = "IGNORE"
EXPECTED_ONLY = "EXPECTED_ONLY"
NO_WORKFLOW = "NO_WORKFLOW"

EPISODE_ACTIONS = (CREATE, UPDATE, REOPEN, LINK_NEW_EPISODE, IGNORE, EXPECTED_ONLY, NO_WORKFLOW)

#: Status de avaliação que NÃO abrem workflow (plano §3.2).
_NO_WORKFLOW_STATUSES = (
    ASSESSMENT_BELOW_PROFILE,
    ASSESSMENT_DATA_INSUFFICIENT,
    ASSESSMENT_RULE_MISMATCH,
    ASSESSMENT_NO_RULE,
)


@dataclass
class EpisodeDecision:
    action: str
    case_key: str
    reason: str
    anomalous_cost_cloud: float
    close_previous: bool = False
    parent_case_id: int | None = None
    notes: list[str] = field(default_factory=list)


def resolve_episode(
    *,
    detection: Detection,
    existing_case: Any | None = None,
    valid_recovery_days: int = 0,
    continuation: bool = False,
    recovery_valid_days: int = RECOVERY_VALID_DAYS,
) -> EpisodeDecision:
    """Decide o que fazer com o caso do episódio (plano §3.2).

    ``valid_recovery_days`` são os dias válidos consecutivos sem anomalia desde
    a última avaliação do caso. A partir de ``recovery_valid_days`` considera-se
    recuperação confirmada.
    """
    if detection.expected_only or detection.assessment_status == ASSESSMENT_EXPECTED:
        return EpisodeDecision(
            action=EXPECTED_ONLY,
            case_key=detection.case_key,
            reason=RC_EXPECTED_MONTHLY_EVENT,
            anomalous_cost_cloud=0.0,
            notes=["evento esperado permanece na tabela analítica, sem workflow"],
        )

    if detection.assessment_status in _NO_WORKFLOW_STATUSES:
        return EpisodeDecision(
            action=NO_WORKFLOW,
            case_key=detection.case_key,
            reason=detection.assessment_status,
            anomalous_cost_cloud=0.0,
            notes=[
                "avaliação permanece analítica: sem referência utilizável, abaixo do "
                "perfil ou com regra de forecast incompatível"
            ],
        )

    if existing_case is None:
        return EpisodeDecision(
            action=CREATE,
            case_key=detection.case_key,
            reason="NOVO_EPISODIO",
            anomalous_cost_cloud=detection.anomalous_cost_cloud,
        )

    state = str(getattr(existing_case, "state", "") or "").upper()
    previous = float(getattr(existing_case, "anomalous_cost_cloud", 0.0) or 0.0)

    if state == "CLOSED":
        if continuation:
            return EpisodeDecision(
                action=REOPEN,
                case_key=detection.case_key,
                reason="CONTINUACAO_DO_MESMO_EPISODIO",
                anomalous_cost_cloud=max(previous, detection.anomalous_cost_cloud),
                notes=["reabertura registra evento auditado; não cria caso novo"],
            )
        if valid_recovery_days >= int(recovery_valid_days):
            return EpisodeDecision(
                action=LINK_NEW_EPISODE,
                case_key=detection.case_key,
                reason="NOVO_EPISODIO_APOS_RECUPERACAO",
                anomalous_cost_cloud=detection.anomalous_cost_cloud,
                parent_case_id=int(getattr(existing_case, "id", 0) or 0) or None,
                notes=["novo episódio vinculado ao caso anterior"],
            )
        return EpisodeDecision(
            action=IGNORE,
            case_key=detection.case_key,
            reason="REPETICAO_TECNICA_DE_PROCESSAMENTO",
            anomalous_cost_cloud=previous,
            notes=[
                "caso encerrado não reabre por simples repetição do processamento "
                f"(recuperação de {valid_recovery_days} dias não confirmada)"
            ],
        )

    # Caso ativo: atualiza evidências e nunca soma o impacto de novo.
    return EpisodeDecision(
        action=UPDATE,
        case_key=detection.case_key,
        reason="REPROCESSAMENTO_ATUALIZA_EVIDENCIAS",
        anomalous_cost_cloud=max(previous, detection.anomalous_cost_cloud),
        notes=["impacto do caso é o máximo entre avaliações, não a soma"],
    )


def consolidation_policy() -> dict[str, Any]:
    """Contrato de consolidação, exposto para documentação e auditoria."""
    return {
        "case_per": "episodio (serie + familia de evento + periodo esperado no mensal)",
        "group_while": f"nao houver {RECOVERY_VALID_DAYS} dias validos consecutivos de recuperacao",
        "impact_rule": "max(anterior, novo) — nunca soma de reavaliacoes",
        "contributors": "somam observado/esperado/limite; impacto recalculado, nao duplicado",
        "expected_events": "permanecem na tabela analitica sem workflow",
        "closed_cases": "nao reabrem por repeticao tecnica; reabrem com evento auditado",
    }
