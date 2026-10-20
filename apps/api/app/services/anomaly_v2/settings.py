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
    "intermittent_zero_ratio": 0.40,
    "recovery_confirmation_days": 3,
    "relapse_watch_days": 14,
    "minimum_weekday_samples": 4,
    "robust_z_threshold": 3.0,
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
    "score_impact_weight": 0.50,
    "score_surprise_weight": 0.25,
    "score_persistence_weight": 0.15,
    "score_impact_log_scale": 25.0,
    "score_impact_saturation": 100.0,
    "score_surprise_points_per_z": 25.0,
    "score_persistence_saturation_days": 3,
    "score_confidence_high_factor": 1.0,
    "score_confidence_medium_factor": 0.85,
    "score_confidence_low_factor": 0.60,
    "projection_days_month": 30.4,
    "projection_days_year": 365.0,
    "severity_medium_min": 2_000.0,
    "severity_high_min": 10_000.0,
    "severity_critical_min": 50_000.0,
    "hierarchy_explain_threshold": 0.70,
}


def detector_settings(value: Mapping[str, Any] | None) -> dict[str, int | float]:
    """Combina configurações antigas/parciais com defaults estáveis."""
    merged = dict(DEFAULT_DETECTOR_SETTINGS)
    for key in merged:
        if value is not None and key in value and value[key] is not None:
            merged[key] = value[key]
    return merged
