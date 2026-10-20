"""Defaults editáveis do motor v2 de anomalias.

Os valores são persistidos por configuração e copiados para o snapshot da run.
"""
from __future__ import annotations

from typing import Any, Mapping


DEFAULT_DETECTOR_SETTINGS: dict[str, int | float] = {
    "detection_lookback_days": 45,
    "baseline_level_days": 14,
    "baseline_variability_days": 56,
    "classification_window_days": 56,
    "new_spend_min_history_days": 14,
    "high_confidence_history_days": 28,
    "intermittent_zero_ratio": 0.40,
    "recovery_confirmation_days": 3,
    "relapse_watch_days": 14,
    "minimum_weekday_samples": 4,
    "robust_z_threshold": 3.0,
    "robust_mad_scale": 1.4826,
    "load_quality_lookback_days": 14,
    "load_completeness_threshold": 0.70,
    "commitment_history_days": 400,
    "commitment_date_tolerance_days": 3,
    "commitment_value_tolerance_pct": 0.15,
    "monthly_comparison_periods": 3,
    "new_spend_alert_materiality_multiple": 3.0,
    "monthly_alert_materiality_multiple": 2.0,
    "one_off_spike_max_days": 2,
    "persistence_gate_min_days": 2,
    "persistence_gate_materiality_multiple": 5.0,
    "score_impact_weight_pct": 50.0,
    "score_surprise_weight_pct": 30.0,
    "score_persistence_weight_pct": 20.0,
    "score_impact_log_scale": 25.0,
    "score_impact_saturation": 100.0,
    "score_surprise_points_per_z": 25.0,
    "score_persistence_saturation_days": 3,
    "score_confidence_high_factor": 1.0,
    "score_confidence_medium_factor": 0.85,
    "score_confidence_low_factor": 0.60,
    "score_confidence_unknown_factor": 0.70,
    "projection_days_month": 30.4,
    "projection_days_year": 365.0,
    "impact_run_rate_lookback_days": 7,
    "severity_medium_min": 2_000.0,
    "severity_high_min": 10_000.0,
    "severity_critical_min": 50_000.0,
}

DETECTOR_SETTING_RANGES: dict[str, tuple[float, float, bool]] = {
    "detection_lookback_days": (7, 365, True),
    "baseline_level_days": (3, 90, True),
    "baseline_variability_days": (7, 365, True),
    "classification_window_days": (7, 365, True),
    "new_spend_min_history_days": (1, 180, True),
    "high_confidence_history_days": (1, 365, True),
    "intermittent_zero_ratio": (0.0, 0.95, False),
    "recovery_confirmation_days": (1, 30, True),
    "relapse_watch_days": (0, 180, True),
    "minimum_weekday_samples": (1, 26, True),
    "robust_z_threshold": (0.5, 10.0, False),
    "robust_mad_scale": (0.1, 5.0, False),
    "load_quality_lookback_days": (3, 90, True),
    "load_completeness_threshold": (0.1, 1.0, False),
    "commitment_history_days": (30, 1825, True),
    "commitment_date_tolerance_days": (0, 15, True),
    "commitment_value_tolerance_pct": (0.0, 1.0, False),
    "monthly_comparison_periods": (1, 24, True),
    "new_spend_alert_materiality_multiple": (1.0, 100.0, False),
    "monthly_alert_materiality_multiple": (1.0, 100.0, False),
    "one_off_spike_max_days": (1, 30, True),
    "persistence_gate_min_days": (1, 30, True),
    "persistence_gate_materiality_multiple": (1.0, 100.0, False),
    "score_impact_weight_pct": (0.0, 100.0, False),
    "score_surprise_weight_pct": (0.0, 100.0, False),
    "score_persistence_weight_pct": (0.0, 100.0, False),
    "score_impact_log_scale": (1.0, 100.0, False),
    "score_impact_saturation": (1.0, 100.0, False),
    "score_surprise_points_per_z": (1.0, 100.0, False),
    "score_persistence_saturation_days": (1, 60, True),
    "score_confidence_high_factor": (0.0, 1.0, False),
    "score_confidence_medium_factor": (0.0, 1.0, False),
    "score_confidence_low_factor": (0.0, 1.0, False),
    "score_confidence_unknown_factor": (0.0, 1.0, False),
    "projection_days_month": (1.0, 31.0, False),
    "projection_days_year": (1.0, 366.0, False),
    "impact_run_rate_lookback_days": (1, 90, True),
    "severity_medium_min": (0.0, 1_000_000_000.0, False),
    "severity_high_min": (0.0, 1_000_000_000.0, False),
    "severity_critical_min": (0.0, 1_000_000_000.0, False),
}


def detector_settings(value: Mapping[str, Any] | None) -> dict[str, int | float]:
    """Combina configurações antigas/parciais com defaults estáveis."""
    merged = dict(DEFAULT_DETECTOR_SETTINGS)
    for key in merged:
        if value is not None and key in value and value[key] is not None:
            merged[key] = value[key]
    return merged


def validate_detector_settings(value: Mapping[str, Any]) -> dict[str, int | float]:
    unknown = set(value) - set(DEFAULT_DETECTOR_SETTINGS)
    if unknown:
        raise ValueError(f"Parâmetros desconhecidos: {', '.join(sorted(unknown))}")
    merged = detector_settings(value)
    for key, (minimum, maximum, is_integer) in DETECTOR_SETTING_RANGES.items():
        try:
            number = float(merged[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} precisa ser numérico") from exc
        if number < minimum or number > maximum or (is_integer and not number.is_integer()):
            raise ValueError(f"{key} deve estar entre {minimum:g} e {maximum:g}{' (inteiro)' if is_integer else ''}")
        merged[key] = int(number) if is_integer else number
    weights = sum(float(merged[key]) for key in (
        "score_impact_weight_pct", "score_surprise_weight_pct", "score_persistence_weight_pct"
    ))
    if weights <= 0:
        raise ValueError("Ao menos um peso da nota precisa ser maior que zero")
    return merged
