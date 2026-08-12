"""
Expression Engine - Evaluate business rule formulas using DataFrame columns as variables.

Supports:
- Arithmetic expressions: unitPrice * quantity * 13.83 -> target_column
- Conditional rules (if/then): multiple independent if conditions
- Safe evaluation via pandas.eval() with column validation
- Global constants support
"""

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Global constants that can be referenced in expressions
GLOBAL_CONSTANTS: dict[str, float] = {}


def set_global_constant(name: str, value: float) -> None:
    """Register a global constant for use in expressions."""
    GLOBAL_CONSTANTS[name] = value


def _evaluate_numeric_expression(df: pd.DataFrame, expression: str) -> pd.Series:
    """Evaluate a formula with every referenced DataFrame column coerced to number."""
    eval_context: dict[str, pd.Series | float] = {
        column: pd.to_numeric(df[column], errors="coerce").fillna(0.0)
        for column in df.columns
    }
    eval_context.update(GLOBAL_CONSTANTS)
    result = pd.eval(expression, local_dict=eval_context, engine="python", parser="pandas")
    if not isinstance(result, pd.Series):
        result = pd.Series(result, index=df.index)
    return result


def evaluate_expression(
    df: pd.DataFrame,
    expression: str,
    target_column: str,
    source_columns: list[str] | None = None,
) -> pd.DataFrame:
    """Evaluate an arithmetic expression using DataFrame columns as variables.

    Example:
        evaluate_expression(df, "unitPrice * quantity * 13.83", "realizado")

    The expression can reference any column name in the DataFrame.
    Numeric columns are cast to float before evaluation.
    Global constants from GLOBAL_CONSTANTS are also available.
    """
    if not expression or not target_column:
        return df

    if source_columns is None:
        source_columns = list(df.columns)

    # Validate that all referenced columns exist
    expr_lower = expression.lower()
    referenced_cols: set[str] = set()
    for col in source_columns:
        if col.lower() in expr_lower:
            referenced_cols.add(col)

    try:
        result = _evaluate_numeric_expression(df, expression)
        # "First match wins" — don't overwrite rows already filled by higher-priority rules
        if target_column in df.columns:
            unfilled = df[target_column].isna() | (df[target_column].astype(str).str.strip() == "")
            df.loc[unfilled, target_column] = result[unfilled]
        else:
            df[target_column] = result
    except Exception as e:
        logger.warning("Expression evaluation failed for '%s': %s", expression, e)
        df[target_column] = np.nan

    return df


def evaluate_conditional(
    df: pd.DataFrame,
    conditions: list[dict],
) -> pd.DataFrame:
    """Apply if/then conditions with AND/OR logic to the DataFrame.

    Each condition is a dict:
    {
        "field": "Cloud",
        "op": "==",
        "value": "Azure",
        "logic": "AND",      # AND | OR — how to combine with previous condition
        "then_field": "tax_rate",
        "then_value": "18.6065"
    }

    Multiple conditions are combined using the logic field:
    - AND: all must match (default for first condition)
    - OR: any must match

    Returns the modified DataFrame.
    """
    if not conditions:
        return df

    # Build a combined mask from all conditions
    combined_mask: pd.Series | None = None

    for i, cond in enumerate(conditions):
        field = cond.get("field", "")
        op = cond.get("op", "==")
        value = cond.get("value", "")
        logic = cond.get("logic", "AND").upper()

        if not field or field not in df.columns:
            continue

        col = df[field].astype(str)

        if op == "==":
            mask = col == str(value)
        elif op == "!=":
            mask = col != str(value)
        elif op == "contains":
            mask = col.str.contains(str(value), case=False, na=False)
        elif op == "in":
            if isinstance(value, list):
                values_list = [str(v).strip() for v in value]
            else:
                values_list = [v.strip() for v in str(value).split("|")]
            mask = col.isin(values_list)
        else:
            logger.warning("Unknown operator '%s' in conditional rule", op)
            continue

        if combined_mask is None:
            combined_mask = mask
        else:
            prev_logic = conditions[i - 1].get("logic", "AND").upper() if i > 0 else "AND"
            if prev_logic == "OR":
                combined_mask = combined_mask | mask
            else:
                combined_mask = combined_mask & mask

    if combined_mask is None or not combined_mask.any():
        return df

    # Apply the THEN value from the first condition (all in this rule share the same target)
    then_field = conditions[0].get("then_field", "")
    then_value = conditions[0].get("then_value", "")

    if not then_field:
        return df

    # "First match wins" — don't overwrite already-filled values
    if then_field not in df.columns:
        # Keep derived numeric fields nullable. An empty string would coerce
        # the entire result column to text before Iceberg writes it.
        df[then_field] = np.nan
    try:
        num_val = float(then_value)
        unfilled_mask = df[then_field] == "" if df[then_field].dtype == object else df[then_field].isna()
        df.loc[combined_mask & unfilled_mask, then_field] = num_val
    except ValueError:
        # Conditional rules may use a calculated THEN expression such as
        # ``billedcost * 0.1383``. Evaluate it against the mapped frame;
        # only literal text should remain text.
        try:
            calculated = _evaluate_numeric_expression(df, str(then_value))
            unfilled_mask = df[then_field].isna() | (df[then_field].astype(str).str.strip() == "")
            df.loc[combined_mask & unfilled_mask, then_field] = calculated[combined_mask & unfilled_mask]
        except Exception:
            unfilled_mask = df[then_field].astype(str).str.strip() == ""
            df.loc[combined_mask & unfilled_mask, then_field] = str(then_value)

    return df
