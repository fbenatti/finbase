"""Allowlist for optional keyed Gold materializers.

The removed FinOps decision dashboards supplied the only keyed materializers.
Generic Gold rules continue through gold_materialization.py.
"""
from __future__ import annotations

from typing import Any

MATERIALIZERS: dict[str, tuple[Any, Any]] = {}


def is_registered(key: str | None) -> bool:
    return bool(key) and str(key) in MATERIALIZERS


def spec_for(key: str) -> Any:
    raise KeyError(f"materializer_key desconhecido: {key!r}")


def runner_for(key: str) -> Any:
    raise KeyError(f"materializer_key desconhecido: {key!r}")


def execution_order(keys: list[str]) -> list[str]:
    if keys:
        raise ValueError("Não há materializadores Gold especiais registrados")
    return []
