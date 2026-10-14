"""Distribuição diária, calibração de faixa, perfis e materialidade (E3).

Três peças, todas derivadas de `realizado_cloud` observado na fonte do forecast:

1. **Distribuição por dia da semana** (plano §2.3): mediana do consumo por dia da
   semana nas últimas oito semanas válidas, normalizada sobre **todo o intervalo
   coberto pela projeção**. Fallback uniforme é identificado na resposta. A soma
   da distribuição completa é igual ao valor do forecast — o valor mensal nunca é
   dividido por 15.
2. **Faixa** (plano §2.5, §2.6): quantil unilateral para aumentos, com resíduos
   separados por granularidade (diária e evento mensal) e amostra mínima
   ``ceil(1/alpha) - 1``. Sem limite calibrado, usa tolerância explícita e
   **não** apresenta a faixa como intervalo estatístico calibrado.
3. **Perfis, tolerâncias e materialidade** (plano §2.6).

O desvio e o custo anômalo são medidas distintas (plano §2.5):

```text
desvio_cloud        = observado_cloud − esperado_cloud
custo_anomalo_cloud = max(0, observado_cloud − limite_superior_cloud)
```
"""
from __future__ import annotations

import calendar
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

# --------------------------------------------------------------------------
# Perfis (plano §2.6)
# --------------------------------------------------------------------------
PROFILE_NAMES = ("CONSERVATIVE", "BALANCED", "SENSITIVE")


@dataclass(frozen=True)
class Profile:
    name: str
    daily_quantile: float
    min_relative_deviation: float
    investigate_score: int

    @property
    def alpha(self) -> float:
        """Probabilidade da cauda usada em ``ceil(1/alpha) - 1``."""
        return 1.0 - self.daily_quantile


PROFILES: dict[str, Profile] = {
    "CONSERVATIVE": Profile("CONSERVATIVE", 0.99, 0.30, 65),
    "BALANCED": Profile("BALANCED", 0.975, 0.20, 55),
    "SENSITIVE": Profile("SENSITIVE", 0.95, 0.10, 40),
}


def profile_for(name: str | None) -> Profile:
    key = str(name or "BALANCED").strip().upper()
    if key not in PROFILES:
        raise ValueError(
            f"Perfil desconhecido: {name}. Valores permitidos: {', '.join(PROFILE_NAMES)}."
        )
    return PROFILES[key]


def quantile_min_sample(alpha: float) -> int:
    """Amostra mínima do quantil: ``ceil(1 / alpha) - 1`` (plano §2.6).

    ``alpha`` é a probabilidade da cauda (``1 - quantil``). Para o perfil
    Conservador (quantil 99%) → ``alpha = 0,01`` → amostra mínima 99.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha deve estar em (0, 1): {alpha}")
    return max(1, math.ceil(1.0 / alpha) - 1)


def min_sample_for_quantile(quantile: float) -> int:
    """Amostra mínima a partir do quantil desejado (vocabulário do plano)."""
    if not 0.0 < quantile < 1.0:
        raise ValueError(f"quantil deve estar em (0, 1): {quantile}")
    return quantile_min_sample(1.0 - quantile)


def resolve_parameters(
    *,
    profile: str | None = None,
    daily_quantile: float | None = None,
    min_relative_deviation: float | None = None,
    investigate_score: int | None = None,
) -> Profile:
    """Perfil base com overrides explícitos da configuração."""
    base = profile_for(profile)
    return Profile(
        name=base.name,
        daily_quantile=float(daily_quantile if daily_quantile is not None else base.daily_quantile),
        min_relative_deviation=float(
            min_relative_deviation if min_relative_deviation is not None else base.min_relative_deviation
        ),
        investigate_score=int(
            investigate_score if investigate_score is not None else base.investigate_score
        ),
    )


# --------------------------------------------------------------------------
# Materialidade (plano §2.6)
# --------------------------------------------------------------------------
DEFAULT_MATERIALITY = 100.0


def materiality_for(
    materiality_by_currency: Mapping[str, Any] | None,
    currency: str | None,
    *,
    default: float = DEFAULT_MATERIALITY,
) -> tuple[float, bool]:
    """Materialidade explícita por moeda.

    Retorna ``(valor, explicito)``. Nunca infere a moeda: se a moeda da linha não
    estiver declarada na configuração, o valor padrão é usado **e isso é
    declarado** (``explicito = False``).
    """
    table = dict(materiality_by_currency or {})
    key = str(currency or "").strip().upper()
    if key and key in {str(k).upper() for k in table}:
        for raw_key, value in table.items():
            if str(raw_key).upper() == key:
                try:
                    return float(value), True
                except (TypeError, ValueError):
                    break
    return float(default), False


# --------------------------------------------------------------------------
# Distribuição por dia da semana
# --------------------------------------------------------------------------
UNIFORM_FALLBACK = "UNIFORM_FALLBACK"
WEEKDAY_MEDIAN = "WEEKDAY_MEDIAN"

#: Semanas de histórico usadas na mediana por dia da semana (plano §2.3).
DEFAULT_WEEKDAY_WEEKS = 8


@dataclass
class WeekdayProfile:
    """Mediana do consumo por dia da semana e como ela foi obtida."""

    medians: dict[int, float]
    method: str
    weeks_used: int
    sample_days: int
    note: str | None = None

    @property
    def is_fallback(self) -> bool:
        return self.method == UNIFORM_FALLBACK


def weekday_profile(
    daily: Mapping[date, float],
    *,
    as_of: date,
    weeks: int = DEFAULT_WEEKDAY_WEEKS,
    minimum_days_per_weekday: int = 3,
) -> WeekdayProfile:
    """Mediana do consumo por dia da semana nas últimas ``weeks`` semanas válidas.

    "Válidas" são as semanas que têm pelo menos um dia com valor observado.
    Sem evidência suficiente, o fallback é uniforme e **identificado**.
    """
    weeks = max(1, int(weeks))
    # Janela alinhada ao início da semana ISO de `as_of`: oito semanas
    # completas, não nove parciais.
    window_start = (
        as_of
        - timedelta(days=as_of.weekday())
        - timedelta(weeks=weeks - 1)
    )
    samples: dict[int, list[float]] = {i: [] for i in range(7)}
    weeks_with_data: set[tuple[int, int]] = set()

    for day, value in (daily or {}).items():
        if day < window_start or day > as_of:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(numeric):
            continue
        samples[day.weekday()].append(numeric)
        iso = day.isocalendar()
        weeks_with_data.add((iso[0], iso[1]))

    usable = {wd: vals for wd, vals in samples.items() if len(vals) >= minimum_days_per_weekday}
    if len(usable) < 4 or not weeks_with_data:
        return WeekdayProfile(
            medians={wd: 1.0 for wd in range(7)},
            method=UNIFORM_FALLBACK,
            weeks_used=len(weeks_with_data),
            sample_days=sum(len(v) for v in samples.values()),
            note=(
                "evidência insuficiente para mediana por dia da semana "
                f"({len(usable)} de 7 dias com >= {minimum_days_per_weekday} amostras)"
            ),
        )

    medians = {wd: (float(median(usable[wd])) if wd in usable else 0.0) for wd in range(7)}
    if sum(medians.values()) <= 0:
        return WeekdayProfile(
            medians={wd: 1.0 for wd in range(7)},
            method=UNIFORM_FALLBACK,
            weeks_used=len(weeks_with_data),
            sample_days=sum(len(v) for v in samples.values()),
            note="medianas por dia da semana somam zero",
        )
    return WeekdayProfile(
        medians=medians,
        method=WEEKDAY_MEDIAN,
        weeks_used=len(weeks_with_data),
        sample_days=sum(len(v) for v in samples.values()),
    )


@dataclass
class DailyDistribution:
    """Distribuição da projeção pelos dias cobertos."""

    weights: dict[date, float]
    expected: dict[date, float]
    method: str
    normalizer: float
    note: str | None = None

    @property
    def coverage_days(self) -> list[date]:
        return sorted(self.weights)

    @property
    def total(self) -> float:
        return sum(self.expected.values())


def build_daily_distribution(
    predicted_cloud: float,
    *,
    coverage_start: date,
    coverage_end: date,
    profile: WeekdayProfile,
) -> DailyDistribution:
    """Reparte o valor mensal pelos dias cobertos, sem alterar o total.

    Os pesos são normalizados sobre **todo o intervalo coberto pela projeção**
    (plano §2.3). A soma da distribuição é exatamente ``predicted_cloud``.
    """
    if coverage_end < coverage_start:
        raise ValueError("intervalo de cobertura invertido")

    days: list[date] = []
    cursor = coverage_start
    while cursor <= coverage_end:
        days.append(cursor)
        cursor += timedelta(days=1)

    raw = {day: float(profile.medians.get(day.weekday(), 0.0)) for day in days}
    normalizer = sum(raw.values())
    if normalizer <= 0:
        raw = {day: 1.0 for day in days}
        normalizer = float(len(days))
        method = UNIFORM_FALLBACK
        note = "pesos semanais zerados para o intervalo: distribuição uniforme"
    else:
        method = profile.method
        note = profile.note

    weights = {day: value / normalizer for day, value in raw.items()}
    # Correção de resíduo de ponto flutuante no último dia coberto: o total
    # precisa fechar exatamente com o valor do forecast.
    expected: dict[date, float] = {}
    running = 0.0
    for index, day in enumerate(days):
        if index == len(days) - 1:
            value = float(predicted_cloud) - running
        else:
            value = float(predicted_cloud) * weights[day]
            running += value
        expected[day] = value

    return DailyDistribution(
        weights=weights,
        expected=expected,
        method=method,
        normalizer=normalizer,
        note=note,
    )


# --------------------------------------------------------------------------
# Calibração de faixa
# --------------------------------------------------------------------------
HISTORICAL_QUANTILE = "HISTORICAL_QUANTILE"
EXPLICIT_TOLERANCE = "EXPLICIT_TOLERANCE"

#: Granularidades cujos resíduos são calibrados separadamente (plano §2.6).
DAILY_GRAIN = "DAILY"
MONTHLY_EVENT_GRAIN = "MONTHLY_EVENT"


@dataclass
class BandCalibration:
    """Limite superior (e inferior) para o valor esperado."""

    method: str
    alpha: float
    upper_offset: float
    lower_offset: float
    sample_size: int
    min_sample: int
    relative_offset: float | None
    sample_sufficient: bool
    confidence: str
    note: str | None = None
    granularity: str = DAILY_GRAIN
    calibration_days: int = 0

    @property
    def is_calibrated(self) -> bool:
        return self.method == HISTORICAL_QUANTILE

    def upper_limit(self, expected_cloud: float) -> float:
        return float(expected_cloud) + self.upper_offset

    def lower_limit(self, expected_cloud: float) -> float:
        return float(expected_cloud) - self.lower_offset

    def as_reference_fields(self, expected_cloud: float) -> dict[str, Any]:
        expected = float(expected_cloud)
        return {
            # A faixa do TOTAL do mês. O offset de um único dia é gravado
            # separadamente e só faz sentido na granularidade diária.
            "band_lower_cloud": self.lower_limit(expected),
            "band_upper_cloud": self.upper_limit(expected),
            "band_method": self.method,
            "band_confidence": self.confidence,
            "band_alpha": self.alpha,
            "band_sample_size": self.sample_size,
            "band_min_sample": self.min_sample,
        }


def _quantile(values: Sequence[float], q: float) -> float:
    """Quantil com interpolação linear (mesmo critério de numpy 'linear')."""
    if not values:
        raise ValueError("quantil de amostra vazia")
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def calibrate_band(
    *,
    residuals: Sequence[float],
    expected_cloud: float,
    quantile: float,
    tolerance_pct: float,
    granularity: str = DAILY_GRAIN,
    min_relative_deviation: float = 0.0,
    calibration_days: int = 0,
) -> BandCalibration:
    """Faixa unilateral para aumentos.

    ``residuals`` são resíduos **da mesma granularidade** da avaliação
    (plano §2.6 proíbe misturar diário e evento mensal). Cada resíduo é
    ``observado − esperado`` de um ponto da janela de calibração.

    Sem amostra suficiente para o quantil, a faixa usa a tolerância explícita
    configurada e a resposta **declara** que não é um intervalo estatístico
    calibrado.
    """
    alpha = 1.0 - float(quantile)
    min_sample = min_sample_for_quantile(quantile)
    cleaned = [float(r) for r in residuals if r is not None and math.isfinite(float(r))]
    sample_size = len(cleaned)
    relative_fallback = max(0.0, float(tolerance_pct))
    lower_fallback = expected_cloud * 0.0
    upper_fallback = abs(expected_cloud) * relative_fallback

    if sample_size < min_sample:
        return BandCalibration(
            method=EXPLICIT_TOLERANCE,
            alpha=alpha,
            upper_offset=upper_fallback,
            lower_offset=lower_fallback,
            sample_size=sample_size,
            min_sample=min_sample,
            relative_offset=relative_fallback,
            sample_sufficient=False,
            confidence="LOW" if sample_size else "UNAVAILABLE",
            note=(
                f"amostra insuficiente para quantil {1 - alpha:.1%} "
                f"({sample_size} de {min_sample} resíduos): tolerância explícita de "
                f"{relative_fallback:.1%} — não é intervalo estatístico calibrado"
            ),
            granularity=granularity,
            calibration_days=calibration_days,
        )

    upper = max(0.0, _quantile(cleaned, 1.0 - alpha))
    lower = max(0.0, -_quantile(cleaned, alpha))
    confidence = "HIGH" if sample_size >= 2 * min_sample else "MEDIUM"
    return BandCalibration(
        method=HISTORICAL_QUANTILE,
        alpha=alpha,
        upper_offset=upper,
        lower_offset=lower,
        sample_size=sample_size,
        min_sample=min_sample,
        relative_offset=(upper / expected_cloud) if expected_cloud else None,
        sample_sufficient=True,
        confidence=confidence,
        note=f"quantil unilateral {1 - alpha:.1%} sobre {sample_size} resíduos de {granularity}",
        granularity=granularity,
        calibration_days=calibration_days,
    )


def calibrate_monthly_event_band(
    *,
    residuals: Sequence[float],
    expected_cloud: float,
    tolerance_pct: float = 0.15,
    monthly_min_events: int = 3,
    quantile: float = 0.975,
) -> BandCalibration:
    """Faixa de evento mensal com confiança limitada por amostra.

    Poucos eventos mensais não sustentam promessa de confiança de 99%
    (plano §2.6): com menos de ``monthly_min_events`` eventos comparáveis, usa
    tolerância de valor (inicialmente 15%).
    """
    cleaned = [float(r) for r in residuals if r is not None and math.isfinite(float(r))]
    if len(cleaned) < max(2, int(monthly_min_events)):
        return BandCalibration(
            method=EXPLICIT_TOLERANCE,
            alpha=1.0 - float(quantile),
            upper_offset=abs(float(expected_cloud)) * float(tolerance_pct),
            lower_offset=0.0,
            sample_size=len(cleaned),
            min_sample=max(2, int(monthly_min_events)),
            relative_offset=float(tolerance_pct),
            sample_sufficient=False,
            confidence="LOW",
            note=(
                f"{len(cleaned)} evento(s) mensal(is) comparável(is) — abaixo de "
                f"{max(2, int(monthly_min_events))}: tolerância de valor de "
                f"{float(tolerance_pct):.1%} com confiança limitada"
            ),
            granularity=MONTHLY_EVENT_GRAIN,
        )
    band = calibrate_band(
        residuals=cleaned,
        expected_cloud=expected_cloud,
        quantile=quantile,
        tolerance_pct=tolerance_pct,
        granularity=MONTHLY_EVENT_GRAIN,
    )
    # Mesmo calibrado, o histórico de eventos mensais é curto: teto de confiança.
    if band.confidence == "HIGH":
        band.confidence = "MEDIUM"
        band.note = (band.note or "") + "; confiança limitada pelo histórico mensal"
    return band


# --------------------------------------------------------------------------
# Desvio e custo anômalo (plano §2.5)
# --------------------------------------------------------------------------
@dataclass
class Deviation:
    observed_cloud: float
    expected_cloud: float
    upper_limit: float
    lower_limit: float
    delta_cloud: float
    anomalous_cost_cloud: float
    above_band: bool
    relative_deviation: float | None

    @property
    def is_increase(self) -> bool:
        return self.delta_cloud > 0


def evaluate_deviation(
    observed_cloud: float,
    *,
    expected_cloud: float,
    band: BandCalibration,
) -> Deviation:
    """Separa desvio (diferença para a previsão central) de custo anômalo."""
    observed = float(observed_cloud)
    expected = float(expected_cloud)
    upper = band.upper_limit(expected)
    lower = band.lower_limit(expected)
    delta = observed - expected
    anomalous = max(0.0, observed - upper)
    relative = (delta / expected) if expected else None
    return Deviation(
        observed_cloud=observed,
        expected_cloud=expected,
        upper_limit=upper,
        lower_limit=lower,
        delta_cloud=delta,
        anomalous_cost_cloud=anomalous,
        above_band=anomalous > 0.0,
        relative_deviation=relative,
    )


def is_candidate(
    deviation: Deviation,
    *,
    min_relative_deviation: float,
    materiality: float,
) -> tuple[bool, list[str]]:
    """Um ponto entra na investigação?

    Além de ficar acima da faixa, o desvio precisa ser materialmente relevante:
    desvio relativo mínimo do perfil **ou** custo anômalo acima da materialidade.
    """
    reasons: list[str] = []
    if not deviation.above_band:
        return False, ["WITHIN_BAND"]
    if deviation.relative_deviation is not None and deviation.relative_deviation < min_relative_deviation:
        reasons.append("BELOW_MIN_RELATIVE_DEVIATION")
    if deviation.anomalous_cost_cloud < materiality:
        reasons.append("BELOW_MATERIALITY")
    return (not reasons), reasons


def scale_band_to_coverage(band: BandCalibration, *, covered_days: int) -> BandCalibration:
    """Escala a faixa diária para o acumulado de ``covered_days``.

    A variância cresce com o número de dias, mas a média do desvio escala
    linearmente: para o acumulado usa-se ``offset × covered_days``, que é a
    hipótese conservadora de desvio sistemático (não diluída por √n).
    """
    days = max(1, int(covered_days))
    return BandCalibration(
        method=band.method,
        alpha=band.alpha,
        upper_offset=band.upper_offset * days,
        lower_offset=band.lower_offset * days,
        sample_size=band.sample_size,
        min_sample=band.min_sample,
        relative_offset=band.relative_offset,
        sample_sufficient=band.sample_sufficient,
        confidence=band.confidence,
        note=band.note,
        granularity=band.granularity,
        calibration_days=band.calibration_days,
    )


def residuals_for_daily_grain(
    observed: Mapping[date, float],
    expected: Mapping[date, float],
) -> list[float]:
    """Resíduos da granularidade **diária** (plano §2.6)."""
    values: list[float] = []
    for day, exp in expected.items():
        if day not in observed:
            continue
        values.append(float(observed[day]) - float(exp))
    return values


def residuals_for_monthly_events(
    events: Sequence[Mapping[str, Any]],
    *,
    observed_key: str = "observed_cloud",
    expected_key: str = "expected_cloud",
) -> list[float]:
    """Resíduos da granularidade **evento mensal** (plano §2.6).

    Nunca mistura eventos mensais com resíduos diários.
    """
    values: list[float] = []
    for event in events:
        try:
            values.append(float(event[observed_key]) - float(event[expected_key]))
        except (KeyError, TypeError, ValueError):
            continue
    return values


def elapsed_days(calibration_start: date, calibration_end: date) -> int:
    return max(0, (calibration_end - calibration_start).days + 1)


def has_complete_daily_calibration(
    daily: Mapping[date, float], *, as_of: date, calibration_days: int
) -> bool:
    """Calibração diária completa exige ao menos ``calibration_days`` dias válidos."""
    window_start = as_of - timedelta(days=max(1, int(calibration_days)) - 1)
    valid = {
        day
        for day, value in (daily or {}).items()
        if window_start <= day <= as_of and value is not None and math.isfinite(float(value))
    }
    return len(valid) >= int(calibration_days)


def month_last_day(month: str) -> date:
    year, mon = int(month[:4]), int(month[4:6])
    return date(year, mon, calendar.monthrange(year, mon)[1])
