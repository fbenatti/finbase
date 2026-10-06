"""Bounded, metadata-driven On-Demand clone engine."""
from __future__ import annotations

from typing import Any
import pandas as pd


def assign_source_record_ids(
    frame: pd.DataFrame,
    *,
    connection_id: int,
    blob_id: int,
    row_offset: int,
) -> pd.DataFrame:
    """Attach a unique source-row key before a batch can be split or cloned.

    Azure exports do not consistently carry a source record ID. A DataFrame
    index is not a valid fallback: it restarts for each Bronze batch and can
    associate a synthetic Return with an unrelated Usage row. The source blob
    manifest ID plus its zero-based row offset is unique within the immutable
    Bronze input and is copied to the synthetic Return by ``apply``.
    """
    result = frame.copy()
    result["source_record_id"] = [
        f"{connection_id}:{blob_id}:{row_offset + index}"
        for index in range(len(result))
    ]
    return result


def _matches(frame: pd.DataFrame, conditions: list[dict[str, Any]]) -> pd.Series:
    mask = pd.Series(True, index=frame.index)
    for c in conditions or []:
        field, op, value = c.get("field"), str(c.get("op", "=")), c.get("value")
        if not field or field not in frame: return pd.Series(False, index=frame.index)
        col = frame[field]
        values = value if isinstance(value, list) else [value]
        text = col.astype(str)
        if op in {"=", "IN"}: current = text.isin([str(x) for x in values])
        elif op in {"!=", "NOT_IN"}: current = ~text.isin([str(x) for x in values])
        elif op == "CONTAINS": current = text.str.contains(str(values[0]), case=False, na=False, regex=False)
        elif op == "NOT_CONTAINS": current = ~text.str.contains(str(values[0]), case=False, na=False, regex=False)
        elif op == "EMPTY": current = text.str.strip().isin(["", "nan", "None", "null"])
        elif op == "NOT_EMPTY": current = ~text.str.strip().isin(["", "nan", "None", "null"])
        elif op == ">": current = pd.to_numeric(col, errors="coerce").fillna(0).gt(float(values[0]))
        elif op == "<": current = pd.to_numeric(col, errors="coerce").fillna(0).lt(float(values[0]))
        else: current = pd.Series(False, index=frame.index)
        mask &= current
    return mask


def _apply_changes(row: dict[str, Any], changes: dict[str, Any], *, historical_cost: float | None = None) -> dict[str, Any]:
    """Apply the small, declarative transformation vocabulary exposed by the UI.

    Legacy rules may still store ``{field: "value"}``; those are interpreted as
    SET.  No expression, Python or SQL supplied by a user is executed here.
    """
    result = dict(row)
    for field, definition in (changes or {}).items():
        if isinstance(definition, dict):
            operation = str(definition.get("operation", "SET")).upper()
            value = definition.get("value")
        else:
            operation, value = "SET", definition
        if operation == "SET":
            result[field] = value
        elif operation == "ZERO":
            result[field] = 0
        elif operation == "NEGATE":
            result[field] = -float(pd.to_numeric(result.get(field), errors="coerce") or 0)
        elif operation == "ABS":
            result[field] = abs(float(pd.to_numeric(result.get(field), errors="coerce") or 0))
        elif operation == "COPY_COLUMN" and value:
            result[field] = result.get(str(value))
        elif operation == "HISTORICAL_ON_DEMAND_COST" and historical_cost is not None:
            result[field] = historical_cost
    return result


class CloneEngine:
    def __init__(
        self, rules: list[Any], destination_table_id: int,
        reference_prices: dict[tuple[str, str], float] | None = None,
    ):
        self.rules = sorted(rules, key=lambda r: r.priority)
        self.destination_table_id = destination_table_id
        self.prices: dict[tuple[str, str], tuple[str, float]] = {}
        self.reference_prices = reference_prices or {}
        self.pendencies: list[dict[str, Any]] = []

    def _prime_prices(self, frame: pd.DataFrame, rule: Any, period: str) -> None:
        roles = rule.roles or {}
        meter, price, model = roles.get("meter_id"), roles.get("unit_price"), roles.get("pricing_model")
        on_demand = str(roles.get("on_demand_value", "OnDemand")).lower()
        if not all(x in frame for x in (meter, price, model)): return
        candidates = frame.loc[frame[model].astype(str).str.lower().eq(on_demand), [meter, price]].copy()
        if candidates.empty:
            return
        candidates[price] = pd.to_numeric(candidates[price], errors="coerce")
        candidates = candidates[candidates[price].gt(0)]
        if candidates.empty:
            return
        clouds = frame.loc[candidates.index, "cloud_origem"].astype(str).str.lower() if "cloud_origem" in frame else ""
        candidates["_clone_key"] = list(zip(clouds, candidates[meter].astype(str))) if isinstance(clouds, pd.Series) else [("", value) for value in candidates[meter].astype(str)]
        # ``last`` preserves the old same-period winner while reducing a
        # potentially millions-of-rows scan to one update per meter.
        for key, value in candidates.groupby("_clone_key", sort=False)[price].last().items():
            if key not in self.prices or period >= self.prices[key][0]:
                self.prices[key] = (period, float(value))

    @staticmethod
    def _apply_changes_frame(frame: pd.DataFrame, changes: dict[str, Any], historical_cost: pd.Series) -> pd.DataFrame:
        """Vector equivalent of ``_apply_changes`` for all eligible rows."""
        result = frame.copy()
        for field, definition in (changes or {}).items():
            operation = str(definition.get("operation", "SET")).upper() if isinstance(definition, dict) else "SET"
            value = definition.get("value") if isinstance(definition, dict) else definition
            if operation == "SET": result[field] = value
            elif operation == "ZERO": result[field] = 0
            elif operation == "NEGATE": result[field] = -pd.to_numeric(result.get(field), errors="coerce").fillna(0)
            elif operation == "ABS": result[field] = pd.to_numeric(result.get(field), errors="coerce").fillna(0).abs()
            elif operation == "COPY_COLUMN" and value: result[field] = result.get(str(value))
            elif operation == "HISTORICAL_ON_DEMAND_COST": result[field] = historical_cost
        return result

    def apply(self, frame: pd.DataFrame, period: str) -> tuple[pd.DataFrame, int]:
        if frame.empty or not period:
            return frame, 0
        if "is_synthetic" not in frame.columns:
            frame["is_synthetic"] = False
        else:
            frame["is_synthetic"] = frame["is_synthetic"].fillna(False)
        generated: list[pd.DataFrame] = []
        for rule in self.rules:
            if not rule.active or (rule.valid_from and period < rule.valid_from) or (rule.valid_until and period > rule.valid_until): continue
            self._prime_prices(frame, rule, period)
            roles = rule.roles or {}
            meter, qty, rate = roles.get("meter_id"), roles.get("quantity"), roles.get("exchange_rate")
            billed, effective = roles.get("billed_cost", "billedcost"), roles.get("effective_cost", "effectivecost")
            if not meter or not qty or meter not in frame or qty not in frame: continue
            scope = _matches(frame, rule.conditions)
            if rule.provider: scope &= frame.get("cloud_origem", pd.Series("", index=frame.index)).astype(str).str.lower().str.startswith(rule.provider.lower())
            if billed in frame: scope &= pd.to_numeric(frame[billed], errors="coerce").fillna(0).eq(0)
            eligible = frame.loc[scope].copy()
            if eligible.empty: continue
            clouds = eligible.get("cloud_origem", pd.Series("", index=eligible.index)).astype(str).str.lower()
            keys = list(zip(clouds, eligible[meter].astype(str)))
            references = pd.Series(keys, index=eligible.index).map(self.prices)
            references = references.astype(object)
            # Curated Azure prices are a fallback only: a matching On-Demand
            # observation in this billing stream always wins.
            def _cached_reference(key: tuple[str, str]) -> float | None:
                price = self.reference_prices.get(key)
                if price is None and key[0].startswith("azure"):
                    price = self.reference_prices.get(("azure", key[1]))
                return price

            cached = pd.Series(keys, index=eligible.index).map(_cached_reference)
            use_cached = references.isna() & cached.notna()
            if use_cached.any():
                references.loc[use_cached] = cached.loc[use_cached].map(
                    lambda price: ("reference_catalog", float(price))
                )
            missing = references.isna()
            if missing.any():
                pending = eligible.loc[missing].copy()
                source_ids = pending.get("source_record_id", pd.Series(pending.index, index=pending.index)).astype(str)
                pending_records = pd.DataFrame({
                    "connection_id": pending.get("source_connection_id"),
                    "provider": pending.get("cloud_origem"),
                    "meter_id": pending[meter].astype(str),
                    "source_record_id": source_ids,
                }).to_dict("records")
                self.pendencies.extend({
                    "clone_rule_id": rule.id, "destination_table_id": self.destination_table_id,
                    "billing_period": period, "reason": "Preço On-Demand anterior não encontrado", **record,
                } for record in pending_records)
            eligible = eligible.loc[~missing].copy()
            references = references.loc[~missing]
            if eligible.empty: continue
            prices = references.map(lambda value: value[1]).astype(float)
            reference_periods = references.map(lambda value: value[0])
            quantity = pd.to_numeric(eligible[qty], errors="coerce").fillna(0).abs()
            exchange = pd.to_numeric(eligible[rate], errors="coerce").fillna(1) if rate and rate in eligible else 1.0
            amounts = (prices * quantity * exchange).abs()
            eligible = eligible.loc[amounts.gt(0)].copy()
            amounts = amounts.loc[eligible.index]; prices = prices.loc[eligible.index]; reference_periods = reference_periods.loc[eligible.index]
            if eligible.empty: continue
            originals = self._apply_changes_frame(eligible, rule.original_changes or {}, amounts)
            originals[billed] = amounts
            if effective in frame: originals[effective] = amounts
            originals["is_synthetic"] = False; originals["tipo"] = "realizado"
            # Do not synthesize identity from the pandas index here. The ETL
            # assigns a blob-scoped row key before this engine runs; retaining
            # that key lets a Return point to exactly one Usage source row.
            originals["source_record_id"] = eligible["source_record_id"].astype(str)
            for field in originals.columns:
                if field not in frame.columns: frame[field] = None
                frame.loc[originals.index, field] = originals[field]
            clones = self._apply_changes_frame(originals, rule.clone_changes or {}, amounts)
            clones[billed] = -amounts
            if effective in frame: clones[effective] = -amounts
            clones["is_synthetic"] = True; clones["tipo"] = "realizado"
            clones["clone_source_record_id"] = originals["source_record_id"]
            clones["clone_rule_id"] = rule.id; clones["clone_rule_version"] = rule.version
            clones["ondemand_reference_price"] = prices; clones["ondemand_reference_period"] = reference_periods
            generated.append(clones)
        return (pd.concat([frame, *generated], ignore_index=True) if generated else frame), sum(len(x) for x in generated)
