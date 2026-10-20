"""Classificação da natureza de custo por escopo."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

from app.services.anomaly_v2.settings import detector_settings

NATURE_CONTINUOUS = "CONTINUOUS"
NATURE_INTERMITTENT = "INTERMITTENT"
NATURE_COMMITMENT = "COMMITMENT"
NATURE_NEW = "NEW"

INTERMITTENT_ZERO_RATIO = 0.40
NEW_HISTORY_DAYS = 14
CLASSIFY_WINDOW = 56


def classify_scopes(
    daily_rows: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
    commitment_scope_ids: set[str] | None = None,
    settings: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Retorna perfil por scope_id."""
    config = detector_settings(settings)
    window_start = as_of - timedelta(days=int(config["classification_window_days"]) - 1)
    by_scope: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    first_seen: dict[str, date] = {}
    for row in daily_rows:
        sid = str(row["scope_id"])
        day_s = str(row.get("day") or "")[:10]
        try:
            day = date.fromisoformat(day_s)
        except ValueError:
            continue
        if day < window_start or day > as_of:
            continue
        by_scope[sid].append(row)
        if sid not in first_seen or day < first_seen[sid]:
            first_seen[sid] = day

    profiles: dict[str, dict[str, Any]] = {}
    commitment_scope_ids = commitment_scope_ids or set()
    for sid, rows in by_scope.items():
        days_with_cost = {str(r["day"])[:10] for r in rows if float(r.get("cost_cloud") or 0) > 0}
        calendar_days = (as_of - window_start).days + 1
        zero_ratio = 1.0 - (len(days_with_cost) / max(1, calendar_days))
        history_days = (as_of - first_seen.get(sid, as_of)).days + 1

        charge = str(rows[0].get("chargecategory") or rows[0].get("charge_category") or "").lower()
        commitment = str(rows[0].get("commitmentdiscounttype") or rows[0].get("commitment_type") or "").strip().lower()
        # OnDemand não é reserva. Tratar como compromisso zera o histórico contínuo.
        is_commitment = commitment not in {"", "ondemand", "on-demand", "on demand", "none", "payg"}

        if charge == "purchase" or is_commitment or sid in commitment_scope_ids:
            nature = NATURE_COMMITMENT
        elif history_days < int(config["new_spend_min_history_days"]):
            nature = NATURE_NEW
        elif zero_ratio >= float(config["intermittent_zero_ratio"]):
            nature = NATURE_INTERMITTENT
        else:
            nature = NATURE_CONTINUOUS

        profiles[sid] = {
            "scope_id": sid,
            "cost_nature": nature,
            "history_days": history_days,
            "zero_ratio": round(zero_ratio, 4),
            "active_days": len(days_with_cost),
        }
    return profiles
