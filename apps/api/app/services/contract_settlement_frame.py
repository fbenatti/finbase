"""Columnar helpers for settling large monthly contractual Silver slices."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from app.services.contract_settlement import money, settle_pool


def assign_contract_budget_ids_frame(
    frame: pd.DataFrame,
    configured_scopes: dict[tuple[str, str], set[str]],
) -> pd.DataFrame:
    """Validate/fill budget IDs without materializing every row as a dict."""
    contractual = frame.get(
        "financial_effect", pd.Series("", index=frame.index, dtype="object")
    ).fillna("").astype(str).str.upper().eq("BUDGET_COMMITMENT")
    if not contractual.any():
        return frame

    if "id_orcamento" not in frame.columns:
        frame["id_orcamento"] = None

    contract_ids = pd.to_numeric(
        frame.loc[contractual, "financial_contract_id"], errors="coerce"
    )
    if contract_ids.isna().any():
        raise ValueError("contractual row has no financial_contract_id")

    active_positions = np.flatnonzero(contractual.to_numpy())
    contract_keys = contract_ids.astype("int64").astype(str).to_numpy()
    cloud_keys = (
        frame.loc[contractual, "cloud_origem"]
        .fillna("").astype(str).str.strip().str.lower().to_numpy()
    )
    budget_values = (
        frame.loc[contractual, "id_orcamento"]
        .fillna("").astype(str).str.strip().to_numpy()
    )

    unique_scopes = pd.DataFrame({
        "contract": contract_keys,
        "cloud": cloud_keys,
        "budget": budget_values,
    }).drop_duplicates()
    for contract_id, cloud, current_budget in unique_scopes.itertuples(index=False, name=None):
        allowed = configured_scopes.get((contract_id, cloud), set())
        if current_budget:
            if allowed and current_budget not in allowed:
                raise ValueError(
                    f"budget ID {current_budget} is outside contract {contract_id}/{cloud} scope"
                )
            continue
        if len(allowed) != 1:
            raise ValueError(
                f"cannot resolve budget ID for contract {contract_id}/{cloud}: "
                f"expected one configured scope, found {len(allowed)}"
            )
        fill_positions = active_positions[
            (contract_keys == contract_id)
            & (cloud_keys == cloud)
            & (budget_values == "")
        ]
        frame.iloc[fill_positions, frame.columns.get_loc("id_orcamento")] = next(iter(allowed))

    return frame


def _allocate_cents(pool_cents: int, weights: np.ndarray) -> np.ndarray:
    """Allocate integer cents proportionally; put rounding remainder on the largest row."""
    result = np.zeros(len(weights), dtype=np.int64)
    positive = weights > 0
    if not positive.any():
        return result

    positive_weights = weights[positive]
    denominator = int(positive_weights.sum())
    amount = abs(int(pool_cents))
    max_int = np.iinfo(np.int64).max
    if amount and int(positive_weights.max()) > max_int // amount:
        # Extremely large monetary inputs are rare; preserve exact arithmetic
        # rather than overflowing the fast int64 path.
        allocated = []
        for weight in positive_weights:
            quotient, remainder = divmod(amount * int(weight), denominator)
            allocated.append(quotient + int(remainder >= (denominator + 1) // 2))
        rounded = np.asarray(allocated, dtype=np.int64)
    else:
        numerators = positive_weights * amount
        quotients, remainders = np.divmod(numerators, denominator)
        rounded = quotients + (remainders >= (denominator + 1) // 2)

    if pool_cents < 0:
        rounded = -rounded
    largest_positive = int(np.flatnonzero(positive)[int(np.argmax(positive_weights))])
    result[positive] = rounded
    result[largest_positive] += int(pool_cents) - int(result.sum())
    return result


def _decimal_cents(value: Any) -> int:
    return int(money(value) * 100)


def settle_rows_frame(
    frame: pd.DataFrame,
    *,
    commitment_by_pool: dict[tuple[str, str, str], Decimal],
    opening_credit_by_pool: dict[tuple[str, str, str], Decimal] | None = None,
) -> pd.DataFrame:
    """Settle a DataFrame in-place, avoiding per-row dict copies and Decimal allocation loops."""
    opening_credit_by_pool = opening_credit_by_pool or {}
    contractual = frame.get(
        "financial_effect", pd.Series("", index=frame.index, dtype="object")
    ).fillna("").astype(str).str.upper().eq("BUDGET_COMMITMENT")
    if not contractual.any():
        return frame

    active_positions = np.flatnonzero(contractual.to_numpy())
    contracts = pd.to_numeric(
        frame.loc[contractual, "financial_contract_id"], errors="coerce"
    )
    if contracts.isna().any():
        raise ValueError("contractual row has no financial_contract_id")
    contract_keys = contracts.astype("int64").astype(str).to_numpy()
    cloud_keys = (
        frame.loc[contractual, "cloud_origem"]
        .fillna("").astype(str).str.lower().to_numpy()
    )
    budget_keys = (
        frame.loc[contractual, "id_orcamento"]
        .fillna("").astype(str).to_numpy()
    )

    consumption_column = "realizado_cloud" if "realizado_cloud" in frame.columns else "consumo_realizado"
    consumption_values = frame.iloc[active_positions][consumption_column].tolist()
    consumption_cents = np.fromiter(
        (_decimal_cents(value) for value in consumption_values),
        dtype=np.int64,
        count=len(consumption_values),
    )

    local_positions = np.arange(len(active_positions), dtype=np.int64)
    groups = pd.Series(local_positions).groupby(
        [pd.Series(contract_keys), pd.Series(cloud_keys), pd.Series(budget_keys)],
        sort=False,
        dropna=False,
    ).indices

    for key, local_indices in groups.items():
        key = tuple(str(value) for value in key)
        row_positions = active_positions[local_indices]
        weights = consumption_cents[local_indices]
        consumption_total_cents = int(weights.sum())
        consumption_total = Decimal(consumption_total_cents) / 100
        pool = settle_pool(
            commitment_by_pool.get(key, Decimal("0")),
            consumption_total,
            opening_credit_by_pool.get(key, Decimal("0")),
        )
        realized_cents = _decimal_cents(pool.realized)
        realized_allocations = _allocate_cents(realized_cents, weights)
        adjustment_cents = _decimal_cents(pool.commitment - pool.consumption)
        adjustments = (
            _allocate_cents(adjustment_cents, weights)
            if np.any(weights > 0)
            else np.r_[adjustment_cents, np.zeros(max(0, len(weights) - 1), dtype=np.int64)]
        )

        for column, values in (
            ("realizado", realized_allocations),
            ("consumo_realizado", weights),
            ("ajuste_contratual", adjustments),
        ):
            if column not in frame.columns:
                frame[column] = np.nan
            frame.iloc[row_positions, frame.columns.get_loc(column)] = values / 100.0

    return frame
