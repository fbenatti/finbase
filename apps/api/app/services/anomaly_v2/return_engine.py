"""Motor de retorno ao baseline e status do desvio."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Mapping, Sequence

from app.services.anomaly_v2.baseline import expected_for_day

DEVIATION_ACTIVE = "EM_DESVIO"
DEVIATION_AUTO_FIXED = "AUTOCORRIGIDO"
DEVIATION_FIXED_ACTION = "CORRIGIDO_COM_ACAO"
DEVIATION_LEGITIMATE = "LEGITIMO"
DEVIATION_ACCEPTED = "ACEITO"
DEVIATION_DISMISSED = "DESCARTADO"
DEVIATION_RELAPSE = "REINCIDENTE"

RETURN_VALID_DAYS = 3
RELAPSE_WATCH_DAYS = 14


def evaluate_return(
    *,
    daily: Sequence[Mapping[str, Any]],
    scope_id: str,
    episode_start: date,
    as_of: date,
    treated: bool,
) -> dict[str, Any]:
    """Verifica se o escopo voltou ao baseline."""
    scope_daily = [r for r in daily if str(r.get("scope_id")) == scope_id]
    valid_streak = 0
    returned_at: date | None = None
    cursor = episode_start
    while cursor <= as_of:
        observations = [row for row in scope_daily if str(row.get("day") or "")[:10] == cursor.isoformat()]
        if not observations:
            valid_streak = 0
            cursor += timedelta(days=1)
            continue
        obs = sum(float(row.get("cost_cloud") or 0) for row in observations)
        expected, _l, upper, _mad = expected_for_day(scope_daily, target=cursor, reference_day=episode_start)
        if obs <= upper:
            valid_streak += 1
            if valid_streak >= RETURN_VALID_DAYS and returned_at is None:
                returned_at = cursor - timedelta(days=RETURN_VALID_DAYS - 1)
        else:
            valid_streak = 0
            # A later relapse invalidates an earlier recovery in this window.
            returned_at = None
        cursor += timedelta(days=1)

    if returned_at is None:
        return {"deviation_status": DEVIATION_ACTIVE, "returned_at": None}

    if treated:
        status = DEVIATION_FIXED_ACTION
    else:
        status = DEVIATION_AUTO_FIXED
    return {
        "deviation_status": status,
        "returned_at": returned_at.isoformat(),
        "relapse_watch_until": (returned_at + timedelta(days=RELAPSE_WATCH_DAYS)).isoformat(),
    }
