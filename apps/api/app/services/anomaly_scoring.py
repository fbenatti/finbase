"""Nota, severidade e confiança (plano §2.7).

A nota é **prioridade operacional**, não probabilidade de anomalia:

```text
nota = round(0,55 × intensidade + 0,30 × impacto + 0,15 × persistência)
```

* **Intensidade** — `100 × clamp(ratio / 2, 0, 1)` com
  `ratio = desvio positivo / distância entre esperado e limite superior`,
  e piso numérico para nunca dividir por zero.
* **Impacto** — `min(100, 25 × log2(1 + custo_anomalo / materialidade))`.
* **Persistência** — `100 × min(dias consecutivos / 3, 1)`; para eventos
  mensais representa a repetição do excesso, sem transformar reavaliações da
  mesma compra em novas ocorrências.

Severidade: Baixa `< 60`, Média `60–79`, Alta `80–89`, Crítica `90–100`.
Confiança é medida **separada**: alta, média, baixa ou indisponível.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

#: Piso numérico da distância esperado→limite superior.
SPAN_FLOOR_ABSOLUTE = 1.0
#: Piso relativo: evita intensidade artificial quando a faixa calibrada é ~0.
SPAN_FLOOR_RELATIVE = 0.01

#: Pesos do plano §2.7.
WEIGHT_INTENSITY = 0.55
WEIGHT_IMPACT = 0.30
WEIGHT_PERSISTENCE = 0.15

#: Dias consecutivos que saturam a persistência.
PERSISTENCE_SATURATION_DAYS = 3

SEVERITY_LOW = "LOW"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_HIGH = "HIGH"
SEVERITY_CRITICAL = "CRITICAL"

CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_UNAVAILABLE = "UNAVAILABLE"


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def intensity(positive_delta: float, *, expected_cloud: float, upper_cloud: float) -> tuple[float, float]:
    """Intensidade e o `ratio` que a produziu.

    ``ratio`` é o desvio positivo dividido pela distância entre o esperado e o
    limite superior, com piso numérico. `ratio = 2` já satura a intensidade.
    """
    delta = max(0.0, float(positive_delta))
    distance = float(upper_cloud) - float(expected_cloud)
    span = max(
        distance,
        abs(float(expected_cloud)) * SPAN_FLOOR_RELATIVE,
        SPAN_FLOOR_ABSOLUTE,
    )
    ratio = delta / span
    return 100.0 * _clamp(ratio / 2.0), ratio


def impact(anomalous_cost_cloud: float, *, materiality: float) -> float:
    """Impacto financeiro em escala logarítmica, saturando em 100."""
    cost = max(0.0, float(anomalous_cost_cloud))
    base = float(materiality)
    if base <= 0:
        base = SPAN_FLOOR_ABSOLUTE
    return min(100.0, 25.0 * math.log2(1.0 + cost / base))


def persistence(consecutive_occurrences: int) -> float:
    """Persistência: satura em três ocorrências (dias ou repetições do excesso)."""
    occurrences = max(1, int(consecutive_occurrences))
    return 100.0 * min(occurrences / PERSISTENCE_SATURATION_DAYS, 1.0)


def score_from_components(
    *, intensity_value: float, impact_value: float, persistence_value: float
) -> float:
    return float(
        round(
            WEIGHT_INTENSITY * float(intensity_value)
            + WEIGHT_IMPACT * float(impact_value)
            + WEIGHT_PERSISTENCE * float(persistence_value)
        )
    )


@dataclass(frozen=True)
class ScoreBreakdown:
    """Nota com os componentes que a produziram — auditável."""

    score: float
    severity: str
    intensity: float
    impact: float
    persistence: float
    ratio: float

    def as_dict(self) -> dict[str, float | str]:
        return {
            "score": self.score,
            "severity": self.severity,
            "intensity": round(self.intensity, 4),
            "impact": round(self.impact, 4),
            "persistence": round(self.persistence, 4),
            "ratio": round(self.ratio, 6),
        }


def severity_for(score: float) -> str:
    value = float(score)
    if value >= 90:
        return SEVERITY_CRITICAL
    if value >= 80:
        return SEVERITY_HIGH
    if value >= 60:
        return SEVERITY_MEDIUM
    return SEVERITY_LOW


def score_detection(
    *,
    delta_cloud: float,
    anomalous_cost_cloud: float,
    expected_cloud: float,
    upper_cloud: float,
    materiality: float,
    consecutive_occurrences: int = 1,
) -> ScoreBreakdown:
    """Nota completa de uma avaliação."""
    intensity_value, ratio = intensity(
        delta_cloud, expected_cloud=expected_cloud, upper_cloud=upper_cloud
    )
    impact_value = impact(anomalous_cost_cloud, materiality=materiality)
    persistence_value = persistence(consecutive_occurrences)
    score = score_from_components(
        intensity_value=intensity_value,
        impact_value=impact_value,
        persistence_value=persistence_value,
    )
    return ScoreBreakdown(
        score=score,
        severity=severity_for(score),
        intensity=intensity_value,
        impact=impact_value,
        persistence=persistence_value,
        ratio=ratio,
    )


def severity_rank(severity: str) -> int:
    return {
        SEVERITY_LOW: 0,
        SEVERITY_MEDIUM: 1,
        SEVERITY_HIGH: 2,
        SEVERITY_CRITICAL: 3,
    }.get(str(severity or "").upper(), 0)


def severity_increased(previous: str | None, current: str) -> bool:
    """Aumento de severidade dispara notificação (plano §3.3)."""
    return severity_rank(current) > severity_rank(previous or SEVERITY_LOW)


def confidence_from(
    *,
    band_calibrated: bool,
    band_confidence: str,
    complete_calibration: bool,
    data_partial: bool,
    reference_kind: str,
    few_monthly_events: bool = False,
) -> str:
    """Confiança separada da nota (plano §2.7).

    Ela descreve a qualidade da *comparação*, não a gravidade do desvio.
    """
    if data_partial:
        return CONFIDENCE_LOW
    if reference_kind == "UNAVAILABLE":
        return CONFIDENCE_UNAVAILABLE
    if not band_calibrated:
        return CONFIDENCE_LOW
    if few_monthly_events:
        return CONFIDENCE_LOW
    base = str(band_confidence or CONFIDENCE_LOW).upper()
    if base == CONFIDENCE_HIGH and complete_calibration:
        return CONFIDENCE_HIGH
    if base in (CONFIDENCE_HIGH, CONFIDENCE_MEDIUM) and complete_calibration:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def default_sla_hours(severity: str, sla_hours: dict | None = None) -> int:
    """Prazo de ciência por severidade (plano §3.3)."""
    table = dict(sla_hours or {})
    key = str(severity or "").upper()
    for candidate in (key, "DEFAULT"):
        if candidate in table:
            try:
                return int(table[candidate])
            except (TypeError, ValueError):
                continue
    return {"CRITICAL": 4, "HIGH": 24}.get(key, 72)
