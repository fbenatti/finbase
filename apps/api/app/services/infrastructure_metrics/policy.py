"""Configurable rightsizing policy: presets, validation and the active revision.

The engine parameters live in ``DEFAULT_POLICY``; a saved revision overrides
them. Revisions are append-only (``infra_rightsizing_policy``): the latest one
is active and every run records which revision and values it used.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from .opportunities import DEFAULT_POLICY

PARAMETER_KEYS = (
    "cpu_target_p95", "cpu_target_p99", "memory_target_p95", "memory_target_p99",
    "cpu_peak_quantile", "cpu_peak_limit", "memory_peak_limit",
    "min_change_ratio", "min_days", "min_coverage", "high_confidence_coverage",
)
# Quantiles of the per-bucket peaks the series summary provides.
PEAK_QUANTILES = (99.0, 99.5, 99.9, 100.0)

BALANCED = "BALANCED"
CUSTOM = "CUSTOM"
PRESETS: dict[str, dict[str, Any]] = {
    "CONSERVATIVE": {
        "cpu_target_p95": 0.60, "cpu_target_p99": 0.80,
        "memory_target_p95": 0.70, "memory_target_p99": 0.85,
        "cpu_peak_quantile": 99.9, "cpu_peak_limit": 0.90, "memory_peak_limit": 0.90,
        "min_change_ratio": 0.25, "min_days": 7, "min_coverage": 0.95, "high_confidence_coverage": 0.99,
    },
    BALANCED: {key: DEFAULT_POLICY[key] for key in PARAMETER_KEYS},
    "AGGRESSIVE": {
        "cpu_target_p95": 0.80, "cpu_target_p99": 0.95,
        "memory_target_p95": 0.85, "memory_target_p99": 0.95,
        "cpu_peak_quantile": 99.0, "cpu_peak_limit": 1.10, "memory_peak_limit": 0.97,
        "min_change_ratio": 0.10, "min_days": 5, "min_coverage": 0.85, "high_confidence_coverage": 0.95,
    },
}


class RightsizingPolicyParams(BaseModel):
    """Engine parameters with the ranges the backend accepts."""
    model_config = ConfigDict(extra="forbid")
    cpu_target_p95: float = Field(ge=0.30, le=0.95)
    cpu_target_p99: float = Field(ge=0.50, le=1.00)
    memory_target_p95: float = Field(ge=0.30, le=0.95)
    memory_target_p99: float = Field(ge=0.50, le=1.00)
    cpu_peak_quantile: float
    cpu_peak_limit: float = Field(ge=0.60, le=1.20)
    memory_peak_limit: float = Field(ge=0.70, le=1.00)
    min_change_ratio: float = Field(ge=0.0, le=0.50)
    min_days: int = Field(ge=1, le=60)
    min_coverage: float = Field(ge=0.50, le=1.00)
    high_confidence_coverage: float = Field(ge=0.50, le=1.00)

    @model_validator(mode="after")
    def _consistent(self):
        if self.cpu_peak_quantile not in PEAK_QUANTILES:
            raise ValueError(f"cpu_peak_quantile deve ser um de {', '.join(f'{q:g}' for q in PEAK_QUANTILES)}")
        if self.cpu_target_p99 < self.cpu_target_p95:
            raise ValueError("O alvo de CPU P99 não pode ser menor que o alvo P95")
        if self.memory_target_p99 < self.memory_target_p95:
            raise ValueError("O alvo de memória P99 não pode ser menor que o alvo P95")
        if self.high_confidence_coverage < self.min_coverage:
            raise ValueError("A cobertura para confiança alta não pode ser menor que a cobertura mínima")
        return self


class RightsizingPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    aggressiveness: Literal["CONSERVATIVE", "BALANCED", "AGGRESSIVE", "CUSTOM"]
    params: RightsizingPolicyParams
    note: str | None = Field(None, max_length=500)


def classify(params: dict) -> str:
    """Preset whose values match exactly; anything else is custom."""
    for name, preset in PRESETS.items():
        if all(abs(float(params[key]) - float(value)) < 1e-9 for key, value in preset.items()):
            return name
    return CUSTOM


def effective_policy(revision=None) -> dict:
    """Engine policy for a saved revision (``None`` = ``DEFAULT_POLICY``)."""
    if revision is None:
        return {**DEFAULT_POLICY, "revision": None, "aggressiveness": BALANCED}
    return {
        **DEFAULT_POLICY, **{key: revision.params[key] for key in PARAMETER_KEYS if key in revision.params},
        "revision": revision.version, "aggressiveness": revision.aggressiveness,
    }


def latest_revision_query():
    from app.models.infrastructure_opportunity import InfraRightsizingPolicy

    return select(InfraRightsizingPolicy).order_by(InfraRightsizingPolicy.version.desc()).limit(1)


def load_active_policy(session) -> dict:
    """Active policy for a synchronous session; falls back to the default."""
    return effective_policy(session.execute(latest_revision_query()).scalar_one_or_none())
