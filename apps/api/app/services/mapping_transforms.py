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

    if transform_type == "RENAME":
        if source_column not in df.columns:
            return pd.Series([None] * len(df), index=df.index)
        return df[source_column]

    if transform_type == "CONST":
        return pd.Series([config.get("value")] * len(df), index=df.index)

    if transform_type == "JSON_PATH":
        if source_column not in df.columns:
            return pd.Series([None] * len(df), index=df.index)
        return _json_path(df[source_column], config.get("path", ""))

    if transform_type == "JSON_SUM":
        if source_column not in df.columns:
            return pd.Series([None] * len(df), index=df.index)
        return _json_sum(df[source_column], config.get("path", ""))

    if transform_type == "CONCAT":
        parts = config.get("parts") or []
        sep = config.get("sep", "-")
        return df.apply(lambda row: _concat_parts(row, parts, sep), axis=1)

    if transform_type == "VALUE_MAP":
        if source_column not in df.columns:
            return pd.Series([config.get("default")] * len(df), index=df.index)
        mapping = config.get("map") or {}
        default = config.get("default")
        return df[source_column].astype(str).map(lambda v: mapping.get(v, default))

    if transform_type == "MONTH_START":
        if source_column not in df.columns:
            return pd.Series([None] * len(df), index=df.index)
        return _month_bounds(df[source_column], end=False)

    if transform_type == "MONTH_END":
        if source_column not in df.columns:
            return pd.Series([None] * len(df), index=df.index)
        return _month_bounds(df[source_column], end=True)

    if transform_type == "EXPR":
        expr = config.get("expression", source_column)
        if source_column in df.columns:
            local = {source_column: df[source_column]}
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

    if source_column in df.columns:
        return df[source_column]
    return pd.Series([None] * len(df), index=df.index)


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
