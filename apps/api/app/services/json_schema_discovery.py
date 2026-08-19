"""Discover nested JSON paths inside Bronze columns for Relacionamento."""

from __future__ import annotations

import json
from ast import literal_eval
from typing import Any

import pandas as pd

MAX_SAMPLE_ROWS = 200
MAX_DEPTH = 4
MAX_PATHS_PER_COLUMN = 80


def _parse_json(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (dict, list)):
        return value
    text = str(value).strip()
    if not text or text.lower() in {"none", "nan", "<na>"}:
        return None
    if text.startswith("{") or text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    try:
        return literal_eval(text)
    except Exception:
        return None


def _collect_paths(
    value: Any,
    *,
    prefix: str,
    depth: int,
    out: set[str],
) -> None:
    if depth >= MAX_DEPTH or len(out) >= MAX_PATHS_PER_COLUMN:
        return

    parsed = _parse_json(value)
    if parsed is None:
        return

    if isinstance(parsed, dict):
        for key, child in parsed.items():
            if not isinstance(key, str):
                continue
            path = f"{prefix}.{key}" if prefix else key
            out.add(path)
            _collect_paths(child, prefix=path, depth=depth + 1, out=out)
        return

    if isinstance(parsed, list) and parsed:
        out.add(f"{prefix}[]")
        first = parsed[0]
        if isinstance(first, dict):
            _collect_paths(first, prefix=f"{prefix}[0]", depth=depth + 1, out=out)
        elif prefix:
            out.add(f"{prefix}[0]")


def discover_nested_columns(
    df: pd.DataFrame,
    top_level_columns: list[str],
    *,
    max_rows: int = MAX_SAMPLE_ROWS,
) -> list[dict[str, Any]]:
    """Return virtual nested columns discovered from JSON-like Bronze values."""
    if df.empty:
        return []

    sample = df.head(max_rows)
    nested: list[dict[str, Any]] = []
    seen: set[str] = set(top_level_columns)

    for column in top_level_columns:
        if column not in sample.columns:
            continue
        paths: set[str] = set()
        for value in sample[column].tolist():
            _collect_paths(value, prefix=column, depth=1, out=paths)
            if len(paths) >= MAX_PATHS_PER_COLUMN:
                break

        for path in sorted(paths):
            if path in seen:
                continue
            seen.add(path)
            parent = column
            if path.startswith(f"{column}."):
                parent = column
            elif path.startswith(f"{column}["):
                parent = column
            nested.append({
                "name": path,
                "parent": parent,
                "nested": True,
                "sample_values": [],
                "null_count": 0,
                "total_count": 0,
            })

    return nested
