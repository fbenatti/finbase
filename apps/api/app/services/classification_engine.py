"""
Classification Engine - Apply multi-condition classification rules with department hierarchy.

Supports:
- Multi-condition matching: field=value|value2 ; field2~value3
- Operators: = (exact), ~ (contains), != (not equals)
- Logic: AND (between fields, separated by ;), OR (between values, separated by |)
- Department inheritance: when a row is classified to a project/department,
  inherit fields from parent departments up to root
- Canonical string normalization (_canon): strips accents, lowercases,
  removes non-alphanumeric chars — same behavior as finopsNew
"""

import logging
import re
import unicodedata
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.rules import ClassificationRule, Department, Project

logger = logging.getLogger(__name__)


def _canon(txt) -> str:
    """Canonical normalization: strip accents, lowercase, remove non-alphanumeric.
    Same as finopsNew core/references.py _canon()."""
    if txt is None or (isinstance(txt, float) and pd.isna(txt)):
        return ""
    return re.sub(
        r"[^a-z0-9]", "",
        unicodedata.normalize("NFKD", str(txt))
        .encode("ascii", "ignore")
        .decode()
        .lower(),
    )


def _evaluate_multi_condition(df: pd.DataFrame, conditions: list[dict],
                             canon_cache: dict[str, pd.Series] | None = None) -> pd.Series:
    """Evaluate multi-condition rules against a DataFrame.
    
    Uses _canon() normalization identical to finopsNew classify_row():
    - '=': canonical row value must equal one of the canonical match values
    - '~': any canonical match value must appear anywhere in canonical row value
    - Values separated by | are OR'd within the same field
    - Different fields are AND'd together

    If canon_cache is provided, pre-computed canonical columns are reused
    instead of recomputing _canon() for each rule that references the same field.

    Returns a boolean mask of matching rows.
    """
    if not conditions:
        return pd.Series(True, index=df.index)

    masks: list[pd.Series] = []

    for cond in conditions:
        field = cond.get("field", "")
        operator = cond.get("operator", "=")
        values = cond.get("values", [])

        if not field or field not in df.columns or not values:
            continue

        # Use cached canonical column if available
        if canon_cache is not None and field in canon_cache:
            col = canon_cache[field]
        else:
            col = df[field].fillna("").astype(str).map(_canon)

        # OR between values (same as finopsNew: any(canon(v) in canon(row) for v in vals))
        value_masks: list[pd.Series] = []
        for val in values:
            val_canon = _canon(val)
            if not val_canon:
                continue
            if operator == "=":
                # Exact canonical match: canon(row) == canon(val)
                value_masks.append(col == val_canon)
            elif operator == "~":
                # Contains match: canon(val) appears anywhere in canon(row)
                value_masks.append(col.str.contains(val_canon, regex=False, na=False))
            elif operator == "!=":
                value_masks.append(col != val_canon)
            else:
                # Default to contains
                value_masks.append(col.str.contains(val_canon, regex=False, na=False))

        if value_masks:
            # OR between values within the same field
            combined_or = value_masks[0]
            for m in value_masks[1:]:
                combined_or = combined_or | m
            masks.append(combined_or)

    if not masks:
        return pd.Series(False, index=df.index)

    # AND between different field conditions
    combined = masks[0]
    for m in masks[1:]:
        combined = combined & m

    return combined


def _build_department_path(
    dept_id: int,
    dept_map: dict[int, Department],
    project_map: dict[int, list[Project]],
) -> dict:
    """Build department info including inherited custom_fields by walking up the tree."""
    info: dict = {
        "department_name": "",
        "department_path": "",
        "centro_custo": "",
        "ambiente": "",
    }
    custom_fields: dict = {}

    # Walk up from the department to root, collecting fields
    visited: list[Department] = []
    current_id = dept_id

    while current_id and current_id in dept_map:
        dept = dept_map[current_id]
        visited.append(dept)
        current_id = dept.parent_id

    # Apply from root down (child overrides parent)
    visited.reverse()

    for dept in visited:
        if dept.name:
            info["department_name"] = dept.name
        if dept.centro_custo:
            info["centro_custo"] = dept.centro_custo
        if dept.ambiente:
            info["ambiente"] = dept.ambiente
        if dept.custom_fields:
            custom_fields.update(dept.custom_fields)

    # Build path
    info["department_path"] = " > ".join(d.name for d in visited if d.name)

    # Materialize individual hierarchy levels (root -> leaf) as stable columns
    # so roll-ups like "departamento pai" can be shown as a first-class
    # dimension. department_nivel_1 is the root (diretoria), increasing toward
    # the leaf. Levels beyond the actual depth remain empty.
    for i, dept in enumerate(visited, start=1):
        info[f"departamento_nivel_{i}"] = dept.name or ""

    # Add custom fields as flat keys
    for k, v in custom_fields.items():
        key_lower = k.lower().replace(" ", "_")
        if key_lower not in info:
            info[key_lower] = v

    return info


async def _load_department_tree(db: AsyncSession) -> tuple[dict[int, Department], dict[int, list[Project]]]:
    """Load all departments and projects into memory for fast lookup."""
    dept_result = await db.execute(select(Department).where(Department.active == True))
    departments = list(dept_result.scalars().all())
    dept_map = {d.id: d for d in departments}

    proj_result = await db.execute(select(Project).where(Project.active == True))
    projects = list(proj_result.scalars().all())
    project_map: dict[int, list[Project]] = {}
    for p in projects:
        project_map.setdefault(p.department_id, []).append(p)

    return dept_map, project_map


async def apply_classification_rules(
    df: pd.DataFrame,
    db: AsyncSession,
) -> pd.DataFrame:
    """Apply classification rules with multi-condition support and department inheritance.

    Rules are applied in priority order. Already-classified rows are not overwritten.
    When a rule matches and has a department_id, department fields are inherited.
    """
    from app.models.rules import ClassificationRule as CR

    # Load rules
    stmt = (
        select(CR)
        .where(CR.active == True)
        .order_by(CR.priority)
    )
    result = await db.execute(stmt)
    rules = list(result.scalars().all())

    if not rules:
        return df

    # Load department tree
    dept_map, project_map = await _load_department_tree(db)

    # Initialize target columns if they don't exist
    default_targets = {"departamento", "centro_custo", "ambiente"}
    for col in default_targets:
        if col not in df.columns:
            df[col] = ""

    # Track which rows have been classified
    classified_mask = pd.Series(False, index=df.index)

    for rule in rules:
        # Keep the async path aligned with the ETL worker: a classification
        # without a Department is not a valid organizational allocation.
        if not rule.department_id:
            continue
        # Determine conditions: use conditions JSONB if available, fall back to single condition
        if rule.conditions and isinstance(rule.conditions, list) and len(rule.conditions) > 0:
            match_mask = _evaluate_multi_condition(df, rule.conditions)
        else:
            # Fallback to old single-condition logic
            match_mask = _evaluate_single_condition(df, rule)

        # Only apply to rows not yet classified
        applicable_mask = match_mask & ~classified_mask
        if not applicable_mask.any():
            continue

        # Apply classification value
        target_field = rule.target_field or rule.match_value or "departamento"
        if target_field not in df.columns:
            df[target_field] = ""

        df.loc[applicable_mask, target_field] = rule.match_value

        # Apply department inheritance if rule has a department
        if rule.department_id and rule.department_id in dept_map:
            dept_info = _build_department_path(rule.department_id, dept_map, project_map)
            for col, val in dept_info.items():
                if col not in df.columns:
                    df[col] = ""
                df.loc[applicable_mask, col] = str(val) if val else ""

        classified_mask = classified_mask | applicable_mask

    return df


def _evaluate_single_condition(df: pd.DataFrame, rule) -> pd.Series:
    """Evaluate the old single-condition format (source_field + pattern + pattern_type)."""
    col = rule.source_field
    if col not in df.columns:
        return pd.Series(False, index=df.index)

    if rule.pattern_type == "multi_condition":
        # Parse the pattern string as conditions
        conditions = _parse_pattern_to_conditions(rule.pattern)
        if conditions:
            return _evaluate_multi_condition(df, conditions)
        else:
            return pd.Series(False, index=df.index)
    elif rule.pattern_type == "regex":
        return df[col].astype(str).str.contains(rule.pattern, regex=True, na=False)
    elif rule.pattern_type == "contains":
        return df[col].astype(str).str.contains(rule.pattern, regex=False, na=False)
    else:
        return df[col].astype(str) == rule.pattern


def _parse_pattern_to_conditions(pattern: str) -> list[dict]:
    """Parse a pattern string like 'meterCategory=azuredefender|microsoftdefenderforcloud ; subscriptionName~Sandbox'
    into structured conditions."""
    if not pattern:
        return []

    conditions = []
    and_parts = [p.strip() for p in pattern.split(" ; ")]

    for part in and_parts:
        if not part:
            continue
        if "~" in part:
            field, _, rest = part.partition("~")
            operator = "~"
        elif "=" in part:
            field, _, rest = part.partition("=")
            operator = "="
        else:
            continue

        values = [v.strip() for v in rest.split("|") if v.strip()]
        if field.strip() and values:
            conditions.append({
                "field": field.strip(),
                "operator": operator,
                "values": values,
                "logic": "AND",
            })

    return conditions
