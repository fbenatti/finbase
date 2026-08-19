"""Apply SourceDestinationMapping transforms during Bronze → Gold reprocess."""

from __future__ import annotations

import json
import re
from datetime import datetime
from decimal import Decimal
from typing import Any

import pandas as pd


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
        from ast import literal_eval
        return literal_eval(text)
    except Exception:
        return text


def _resolve_path(data: Any, path: str) -> Any:
    if data is None:
        return None
    parts = path.split(".")
    current = data
    for part in parts:
        if current is None:
            return None
        array_match = re.match(r"^(.+)\[(-?\d+)\]$", part)
        if array_match:
            key, idx = array_match.group(1), int(array_match.group(2))
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return None
            if not isinstance(current, list) or not current:
                return None
            current = current[idx]
            continue
        if part.endswith("[]"):
            key = part[:-2]
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return None
            if not isinstance(current, list):
                return None
            return current
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def split_source_ref(df: pd.DataFrame, source_column: str) -> tuple[str | None, str]:
    """Resolve a source reference to a Bronze column and optional nested JSON path.

    Examples:
      ``project`` -> (``project``, ````)
      ``project.name`` -> (``project``, ``name``)
      ``unknown.field`` -> (``None``, ````) when no prefix matches a column
    """
    if source_column in df.columns:
        return source_column, ""

    if "." not in source_column:
        return None, ""

    parts = source_column.split(".")
    for end in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in df.columns:
            return candidate, ".".join(parts[end:])
    return None, ""


def resolve_json_path(
    transform_config: dict[str, Any] | None,
    implicit_path: str = "",
) -> str:
    """Combine implicit dotted suffix from ``source_column`` and ``transform_config.path``."""
    config = transform_config or {}
    explicit_path = str(config.get("path") or "").strip()
    if implicit_path and explicit_path:
        return f"{implicit_path}.{explicit_path}"
    return explicit_path or implicit_path


def _json_path(series: pd.Series, path: str) -> pd.Series:
    return series.map(lambda v: _resolve_path(_parse_json(v), path))


def _json_sum(series: pd.Series, path: str) -> pd.Series:
    def _sum(value: Any) -> float | None:
        parsed = _parse_json(value)
        items = _resolve_path(parsed, path) if path else parsed
        if items is None:
            return None
        if not isinstance(items, list):
            items = [items]
        total = 0.0
        found = False
        for item in items:
            if isinstance(item, dict):
                amount = item.get("amount")
            else:
                amount = item
            if amount is None:
                continue
            try:
                total += float(amount)
                found = True
            except (TypeError, ValueError):
                continue
        return total if found else None

    return series.map(_sum)


def _concat_parts(row: pd.Series, parts: list[str], sep: str) -> str | None:
    values: list[str] = []
    for part in parts:
        if ":" in part:
            col, subpath = part.split(":", 1)
            raw = row.get(col)
            val = _resolve_path(_parse_json(raw), subpath)
        else:
            val = row.get(part)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            continue
        values.append(str(val))
    return sep.join(values) if values else None


def _month_bounds(series: pd.Series, *, end: bool) -> pd.Series:
    def _bound(value: Any) -> str | None:
        parsed = _parse_json(value)
        month = None
        if isinstance(parsed, dict):
            month = parsed.get("month")
        elif value is not None:
            month = str(value)
        if not month:
            return None
        month = str(month).strip()
        if len(month) != 6 or not month.isdigit():
            return None
        year, mon = int(month[:4]), int(month[4:6])
        if end:
            if mon == 12:
                dt = datetime(year + 1, 1, 1)
            else:
                dt = datetime(year, mon + 1, 1)
        else:
            dt = datetime(year, mon, 1)
        return dt.isoformat()

    return series.map(_bound)


def apply_mapping_transform(
    df: pd.DataFrame,
    source_column: str,
    transform_type: str,
    transform_config: dict[str, Any] | None,
) -> pd.Series:
    config = transform_config or {}
    transform_type = (transform_type or "RENAME").upper()
    empty = pd.Series([None] * len(df), index=df.index)
    root_column, implicit_path = split_source_ref(df, source_column)

    if transform_type == "RENAME":
        if root_column is None:
            return empty
        if implicit_path:
            return _json_path(df[root_column], implicit_path)
        return df[root_column]

    if transform_type == "CONST":
        return pd.Series([config.get("value")] * len(df), index=df.index)

    if transform_type == "JSON_PATH":
        if root_column is None:
            return empty
        path = resolve_json_path(config, implicit_path)
        return _json_path(df[root_column], path)

    if transform_type == "JSON_SUM":
        if root_column is None:
            return empty
        path = resolve_json_path(config, implicit_path)
        return _json_sum(df[root_column], path)

    if transform_type == "CONCAT":
        parts = config.get("parts") or []
        sep = config.get("sep", "-")
        return df.apply(lambda row: _concat_parts(row, parts, sep), axis=1)

    if transform_type == "VALUE_MAP":
        if root_column is None:
            return pd.Series([config.get("default")] * len(df), index=df.index)
        series = (
            _json_path(df[root_column], implicit_path)
            if implicit_path
            else df[root_column]
        )
        mapping = config.get("map") or {}
        default = config.get("default")
        return series.astype(str).map(lambda v: mapping.get(v, default))

    if transform_type == "MONTH_START":
        if root_column is None:
            return empty
        series = (
            _json_path(df[root_column], implicit_path)
            if implicit_path
            else df[root_column]
        )
        return _month_bounds(series, end=False)

    if transform_type == "MONTH_END":
        if root_column is None:
            return empty
        series = (
            _json_path(df[root_column], implicit_path)
            if implicit_path
            else df[root_column]
        )
        return _month_bounds(series, end=True)

    if transform_type == "CONDITIONAL":
        when_column = config.get("when_column") or source_column
        when_root, when_implicit = split_source_ref(df, when_column)
        when_path = resolve_json_path({"path": config.get("when_path")}, when_implicit)
        when_values = [str(v) for v in (config.get("when_values") or [])]
        when_op = str(config.get("when_op", "in")).lower()
        if when_root is None:
            return empty
        when_series = (
            _json_path(df[when_root], when_path)
            if when_path
            else df[when_root]
        )
        if when_op in {"in", "eq", "equals", "is"}:
            mask = when_series.astype(str).isin(when_values)
        elif when_op in {"not_empty", "present"}:
            mask = when_series.notna() & (when_series.astype(str).str.strip() != "")
        else:
            mask = when_series.astype(str).isin(when_values)
        then_source = config.get("then_source", source_column)
        then_type = str(config.get("then_type", "RENAME")).upper()
        then_config = config.get("then_config") or {}
        then_values = apply_mapping_transform(df, then_source, then_type, then_config)
        return then_values.where(mask, None)

    if transform_type == "EXPR":
        expr = config.get("expression", source_column)
        if root_column is not None:
            if implicit_path:
                local = {source_column: _json_path(df[root_column], implicit_path)}
            else:
                local = {source_column: df[root_column]}
        else:
            local = {}
        try:
            base = pd.to_numeric(df.get("cost", pd.Series([0] * len(df))), errors="coerce").fillna(0)
            credits = pd.to_numeric(
                _json_sum(df.get("credits", pd.Series([None] * len(df))), "amount"),
                errors="coerce",
            ).fillna(0)
            # Keep numeric ``cost`` / ``credits_sum`` after ``local`` so a
            # source_column named ``cost`` (Bronze TEXT) cannot shadow them.
            result = eval(expr, {"__builtins__": {}}, {**local, "cost": base, "credits_sum": credits})
            if isinstance(result, pd.Series):
                return result
            return pd.Series([result] * len(df), index=df.index)
        except Exception:
            return pd.Series([None] * len(df), index=df.index)

    if root_column is not None:
        if implicit_path:
            return _json_path(df[root_column], implicit_path)
        return df[root_column]
    return empty


def apply_mappings(df: pd.DataFrame, mappings: list) -> pd.DataFrame:
    """Build destination-frame columns from Bronze df using mapping definitions."""
    target = pd.DataFrame(index=df.index)
    ordered = sorted(mappings, key=lambda m: (getattr(m, "priority", 0), getattr(m, "id", 0)))
    for mapping in ordered:
        target[mapping.destination_column] = apply_mapping_transform(
            df,
            mapping.source_column,
            mapping.transform_type,
            mapping.transform_config,
        )
    return target
