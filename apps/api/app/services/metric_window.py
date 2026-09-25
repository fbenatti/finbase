"""Contrato comum para janelas históricas de métricas diárias em UTC."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException


@dataclass(frozen=True)
class MetricDayWindow:
    """Intervalo inclusivo de dias UTC completos, particionado como YYYYMMDD."""

    from_day: str
    to_day: str
    requested_days: int

    @property
    def from_date(self) -> date:
        return date(int(self.from_day[:4]), int(self.from_day[4:6]), int(self.from_day[6:]))

    @property
    def to_date(self) -> date:
        return date(int(self.to_day[:4]), int(self.to_day[4:6]), int(self.to_day[6:]))

    def as_dict(self, observed_days: int | None = None) -> dict[str, int | str | None]:
        return {
            "from_day": self.from_day,
            "to_day": self.to_day,
            "requested_days": self.requested_days,
            "observed_days": observed_days,
        }


def resolve_metric_day_window(
    from_day: str | None = None,
    to_day: str | None = None,
    *,
    default_days: int = 30,
    max_days: int = 90,
    today_utc: date | None = None,
) -> MetricDayWindow:
    """Resolve a closed UTC interval; explicit bounds are inclusive YYYYMMDD.

    If omitted, returns the last ``default_days`` fully closed UTC days. Partial
    bounds are rejected so callers cannot accidentally request a misleading
    window. ``to_day`` cannot be today or later because that UTC day is incomplete.
    """
    today = today_utc or datetime.now(timezone.utc).date()
    last_complete = today - timedelta(days=1)
    if (from_day is None) != (to_day is None):
        raise HTTPException(status_code=422, detail="from_day e to_day devem ser informados juntos (YYYYMMDD inclusivos).")

    if from_day is None:
        end = last_complete
        start = end - timedelta(days=max(1, min(default_days, max_days)) - 1)
    else:
        if not re.fullmatch(r"\d{8}", str(from_day)) or not re.fullmatch(r"\d{8}", str(to_day)):
            raise HTTPException(status_code=422, detail="from_day e to_day devem usar o formato YYYYMMDD.")
        try:
            start = datetime.strptime(str(from_day), "%Y%m%d").date()
            end = datetime.strptime(str(to_day), "%Y%m%d").date()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="from_day ou to_day não representa uma data válida.") from exc
        if start > end:
            raise HTTPException(status_code=422, detail="from_day deve ser anterior ou igual a to_day.")
        if end > last_complete:
            raise HTTPException(status_code=422, detail="to_day deve ser o último dia UTC completo ou anterior.")
        if (end - start).days + 1 > max_days:
            raise HTTPException(status_code=422, detail=f"A janela histórica máxima é de {max_days} dias.")

    return MetricDayWindow(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), (end - start).days + 1)

