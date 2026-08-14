"""Bounded, metadata-driven On-Demand clone engine."""
from __future__ import annotations

from typing import Any
import pandas as pd


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
    def __init__(self, rules: list[Any], destination_table_id: int):
        self.rules = sorted(rules, key=lambda r: r.priority)
        self.destination_table_id = destination_table_id
        self.prices: dict[tuple[str, str], tuple[str, float]] = {}
        self.pendencies: list[dict[str, Any]] = []

    def _prime_prices(self, frame: pd.DataFrame, rule: Any, period: str) -> None:
        roles = rule.roles or {}
        meter, price, model = roles.get("meter_id"), roles.get("unit_price"), roles.get("pricing_model")
        on_demand = str(roles.get("on_demand_value", "OnDemand")).lower()
        if not all(x in frame for x in (meter, price, model)): return
        candidates = frame[frame[model].astype(str).str.lower().eq(on_demand)]
        for _, row in candidates.iterrows():
            val = pd.to_numeric(row[price], errors="coerce")
            key = (str(row.get("cloud_origem", "")).lower(), str(row[meter]))
            if pd.notna(val) and float(val) > 0 and (key not in self.prices or period >= self.prices[key][0]):
                self.prices[key] = (period, float(val))

    def apply(self, frame: pd.DataFrame, period: str) -> tuple[pd.DataFrame, int]:
        if frame.empty: return frame, 0
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
            clones: list[dict[str, Any]] = []
            for index, row in eligible.iterrows():
                key = (str(row.get("cloud_origem", "")).lower(), str(row[meter]))
                reference = self.prices.get(key)
                if not reference:
                    self.pendencies.append({"clone_rule_id": rule.id, "destination_table_id": self.destination_table_id, "connection_id": row.get("source_connection_id"), "provider": row.get("cloud_origem"), "meter_id": str(row[meter]), "billing_period": period, "source_record_id": str(row.get("source_record_id", index)), "reason": "Preço On-Demand anterior não encontrado"})
                    continue
                _, price = reference
                quantity = abs(float(pd.to_numeric(row[qty], errors="coerce") or 0))
                exchange = float(pd.to_numeric(row.get(rate, 1), errors="coerce") or 1) if rate else 1.0
                amount = abs(price * quantity * exchange)
                if amount == 0: continue
                original = _apply_changes(row.to_dict(), rule.original_changes or {}, historical_cost=amount)
                original[billed] = amount
                if effective in frame: original[effective] = amount
                original["is_synthetic"] = False
                original["tipo"] = "realizado"
                original["source_record_id"] = str(row.get("source_record_id", index))
                for field, value in original.items():
                    if field not in frame.columns:
                        frame[field] = None
                    frame.loc[index, field] = value

                clone = _apply_changes(original, rule.clone_changes or {}, historical_cost=amount)
                clone[billed] = -amount
                if effective in frame: clone[effective] = -amount
                clone["is_synthetic"] = True
                clone["tipo"] = "realizado"
                clone["clone_source_record_id"] = str(row.get("source_record_id", index))
                clone["clone_rule_id"] = rule.id
                clone["clone_rule_version"] = rule.version
                clone["ondemand_reference_price"] = price
                clone["ondemand_reference_period"] = reference[0]
                clones.append(clone)
            if clones: generated.append(pd.DataFrame(clones))
        return (pd.concat([frame, *generated], ignore_index=True) if generated else frame), sum(len(x) for x in generated)
