"""Recurring CPU peaks (cron jobs, backups) and what they cost.

Deterministic over the same Silver buckets the engine sizes with:

1. a *peak bucket* is one whose native maximum reaches the threshold
   (``peak_pattern_threshold``, lowered towards ``peak_pattern_baseline_multiplier``
   × P90 of the maxima for small VMs, never below ``peak_pattern_floor``);
2. peak buckets are grouped by local hour of day; an hour *recurs* when it has
   a peak on at least ``peak_pattern_min_day_ratio`` of the days observed in it
   (or, failing that, of the same weekdays, with ``peak_pattern_min_weeks``
   occurrences at least: weekly jobs), and that many hits are unlikely by
   chance given how often any hour peaks (``peak_pattern_max_false_alarm``);
3. consecutive recurring hours form a window; it stands only if, on enough
   days, the peak starts within ``peak_pattern_jitter_minutes`` of the usual
   time; its typical start/end are the medians of the daily first/last peak.

The finding is a structured insight on the resource's opportunity, not a
separate opportunity: it shares the capacity, prices and window of the main
recommendation, and its saving is conditional on changing the job, so it is
stored apart (``conditional_*`` columns) and never enters the main totals
unless the conditional itself is accepted.

Counterfactual: the job is limited (CPUQuota/cgroups) or rescheduled, so its
work stays (bucket averages are kept) but its bursts disappear (the maxima of
the recurring peak buckets drop to their averages). The same policy sizes the
remaining demand.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from math import comb
from statistics import median
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

from .opportunities import (
    BASIS_LABEL, HOURS_PER_MONTH, READY, _current_option, _linear_quantile, required_capacity, summarize_series,
)
from .sizing import CapacitySnapshot, PriceBook, SizingCatalog

SCHEDULE_OR_CAP_JOB = "SCHEDULE_OR_CAP_JOB"
WEEKDAYS = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")


def _hhmm(minutes: float) -> str:
    minutes = int(round(minutes)) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _local(bucket: str, zone: ZoneInfo) -> datetime:
    moment = datetime.fromisoformat(str(bucket).replace(" ", "T"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(zone)


def _binomial_tail(hits: int, trials: int, chance: float) -> float:
    """P(X ≥ hits) for X ~ Binomial(trials, chance)."""
    if chance <= 0:
        return 0.0 if hits > 0 else 1.0
    return min(1.0, sum(comb(trials, k) * chance ** k * (1 - chance) ** (trials - k) for k in range(hits, trials + 1)))


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " e " + parts[-1]


def detect_recurring_peaks(rows: Iterable[Mapping], *, resolution_seconds: int, policy: Mapping) -> dict:
    """Recurring peak windows of one CPU series (5-minute Silver buckets)."""
    zone = ZoneInfo(policy["peak_pattern_timezone"])
    buckets = []
    for row in rows:
        if row.get("value_average") is None:
            continue
        local = _local(row["bucket_start_utc"], zone)
        maximum = row.get("value_maximum")
        buckets.append((str(row["bucket_start_utc"]), local, float(row["value_average"]),
                        float(maximum) if maximum is not None else float(row["value_average"])))
    result = {"detected": False, "timezone": policy["peak_pattern_timezone"], "windows": []}
    if not buckets:
        return result
    maxima = [bucket[3] for bucket in buckets]
    threshold = max(policy["peak_pattern_floor"], min(
        policy["peak_pattern_threshold"], policy["peak_pattern_baseline_multiplier"] * _linear_quantile(maxima, 90),
    ))
    peaks = [bucket for bucket in buckets if bucket[3] >= threshold]
    result.update(threshold=round(threshold, 2), peak_time_share=round(len(peaks) / len(buckets), 6))
    if not peaks:
        result["reason"] = "Nenhum intervalo com pico acima do limiar."
        return result
    if len(peaks) / len(buckets) > policy["peak_pattern_max_time_share"]:
        # High most of the time: that is the load itself, not a job's burst.
        result["reason"] = "Uso alto em boa parte do tempo: carga contínua, não picos pontuais."
        return result

    observed: dict[int, set[date]] = defaultdict(set)
    hit: dict[int, set[date]] = defaultdict(set)
    for _, local, _, _ in buckets:
        observed[local.hour].add(local.date())
    for _, local, _, _ in peaks:
        hit[local.hour].add(local.date())

    min_days = policy["min_days"]
    ratio = policy["peak_pattern_min_day_ratio"]
    alarm = policy["peak_pattern_max_false_alarm"]

    def background(hours: Iterable[int]) -> float:
        """Chance that any day-hour has a peak (peaks spread with no schedule)."""
        hours = list(hours)
        seen = sum(len(observed[hour]) for hour in hours)
        return sum(len(hit[hour]) for hour in hours) / seen if seen else 0.0

    def unlikely(hits: int, seen: int, chance: float, tests: int) -> bool:
        # So many hits in one slot are unlikely by chance (binomial tail,
        # Bonferroni over every slot tested): a schedule, not noise.
        return tests * _binomial_tail(hits, seen, chance) <= alarm

    # Hour → weekdays it recurs on (None = every day).
    recurring: dict[int, tuple[int, ...] | None] = {}
    chance = background(observed)
    for hour in sorted(hit):
        seen, hits = len(observed[hour]), len(hit[hour])
        if seen >= min_days and hits / seen >= ratio and unlikely(hits, seen, chance, 24):
            recurring[hour] = None
    # Weekly jobs, against the peaks left outside the daily ones.
    chance = background(hour for hour in observed if hour not in recurring)
    for hour in sorted(set(hit) - set(recurring)):
        weekdays = []
        for weekday in range(7):
            seen = sum(1 for day in observed[hour] if day.weekday() == weekday)
            hits = sum(1 for day in hit[hour] if day.weekday() == weekday)
            if hits >= policy["peak_pattern_min_weeks"] and hits / seen >= ratio \
                    and unlikely(hits, seen, chance, 24 * 7):
                weekdays.append(weekday)
        if weekdays:
            recurring[hour] = tuple(weekdays)
    if not recurring:
        result["reason"] = "Os picos não se repetem nos mesmos horários."
        return result

    groups: list[tuple[list[int], tuple[int, ...] | None]] = []
    for hour in sorted(recurring):
        if groups and groups[-1][0][-1] == hour - 1 and groups[-1][1] == recurring[hour]:
            groups[-1][0].append(hour)
        else:
            groups.append(([hour], recurring[hour]))

    total_cpu = sum(bucket[2] for bucket in buckets)
    step = resolution_seconds / 60
    episode_keys: set[str] = set()
    windows = []
    for hours, weekdays in groups:
        matches = lambda day: weekdays is None or day.weekday() in weekdays
        days_seen = {day for hour in hours for day in observed[hour] if matches(day)}
        per_day: dict[date, list] = defaultdict(list)
        for bucket in peaks:
            if bucket[1].hour in hours and matches(bucket[1].date()):
                per_day[bucket[1].date()].append(bucket)
        first = {day: min(item[1].hour * 60 + item[1].minute for item in items) for day, items in per_day.items()}
        usual = median(first.values())
        # A job starts at the same time: days whose peak starts elsewhere in
        # the hour are coincidences, not the job.
        regular = {day: items for day, items in per_day.items()
                   if abs(first[day] - usual) <= policy["peak_pattern_jitter_minutes"]}
        if len(regular) / len(days_seen) < ratio:
            continue
        starts, ends, durations, tops, work = [], [], [], [], 0.0
        for day, items in regular.items():
            minutes = [item[1].hour * 60 + item[1].minute for item in items]
            starts.append(min(minutes))
            ends.append(max(minutes) + step)
            durations.append(len(items) * step)
            tops.append(max(item[3] for item in items))
            work += sum(item[2] for item in items)
            episode_keys.update(item[0] for item in items)
        windows.append({
            "hours": hours,
            "weekdays": list(weekdays) if weekdays is not None else None,
            "start": _hhmm(median(starts)), "end": _hhmm(median(ends)),
            "days_with_peak": len(regular), "days_observed": len(days_seen),
            "day_ratio": round(len(regular) / len(days_seen), 4),
            "typical_duration_minutes": round(median(durations), 1),
            "typical_peak": round(median(tops), 2),
            "cpu_time_share": round(work / total_cpu, 6) if total_cpu else 0.0,
        })
    if not windows:
        result["reason"] = "Os picos não se repetem nos mesmos horários."
        return result

    peak_work = sum(bucket[2] for bucket in buckets if bucket[0] in episode_keys)
    result.update(
        detected=True, windows=windows, episode_buckets=sorted(episode_keys),
        episode_time_share=round(len(episode_keys) / len(buckets), 6),
        cpu_time_share=round(peak_work / total_cpu, 6) if total_cpu else 0.0,
        label=describe_windows(windows),
    )
    return result


def describe_windows(windows: list[Mapping]) -> str:
    """"06:05–06:10 e 13:05–13:15, em 29 de 30 dias" (weekly windows name their weekdays)."""
    parts = []
    daily = [window for window in windows if window["weekdays"] is None]
    if daily:
        slots = _join([f"{window['start']}–{window['end']}" for window in daily])
        days = min(window["days_with_peak"] for window in daily)
        seen = max(window["days_observed"] for window in daily)
        parts.append(f"{slots}, em {days} de {seen} dias")
    for window in windows:
        if window["weekdays"] is not None:
            names = _join([WEEKDAYS[day] for day in window["weekdays"]])
            parts.append(f"{window['start']}–{window['end']} às {names} "
                         f"({window['days_with_peak']} de {window['days_observed']} ocorrências)")
    return "; ".join(parts)


def conditional_rightsizing(
    result: Mapping, *, capacity: CapacitySnapshot | None, cpu_rows: list[Mapping], memory: Mapping | None,
    catalog: SizingCatalog | None, price_book: PriceBook | None, policy: Mapping,
    window_start: datetime, window_end: datetime, resolution_seconds: int,
) -> dict | None:
    """The recurring-peak insight of one analyzed resource, with its counterfactual size."""
    if capacity is None or catalog is None or result.get("from_config") is None or result.get("to_config") is None:
        return None
    detection = detect_recurring_peaks(cpu_rows, resolution_seconds=resolution_seconds, policy=policy)
    if not detection["detected"]:
        return {"detection": detection, "conditional": None}
    episodes = set(detection.pop("episode_buckets"))
    # Job limited or moved: its work stays (average), its burst goes (max → average).
    capped_rows = [
        dict(row, value_maximum=row["value_average"]) if str(row["bucket_start_utc"]) in episodes else row
        for row in cpu_rows
    ]
    cpu = summarize_series(capped_rows, window_start=window_start, window_end=window_end,
                           resolution_seconds=resolution_seconds)
    need = required_capacity(capacity, cpu, memory, policy)
    target = catalog.fit(capacity, need["vcpus"], need["memory_gb"])
    current = _current_option(capacity)
    main_target = result["to_config"]
    insight = {"detection": detection, "conditional": None}
    if target.vcpus >= main_target["vcpus"] and target.memory_gb >= main_target["memory_gb"]:
        insight["detection"]["note"] = "Mesmo sem os picos recorrentes o tamanho recomendado não mudaria."
        return insight

    baseline = [
        float(row["value_average"]) for row in cpu_rows
        if row.get("value_average") is not None and str(row["bucket_start_utc"]) not in episodes
    ]
    baseline_vcpus = current.vcpus * (_linear_quantile(baseline, 95) or 0) / 100
    # Cap for the job: what the new size leaves above the rest of the load.
    cap_vcpus = max(1.0, round(target.vcpus * policy["cpu_peak_limit"] - baseline_vcpus, 1))
    windows = detection["windows"]
    # CPU work of a typical episode, spread under the cap: how long the job would take.
    job_minutes = sum(window["typical_duration_minutes"] for window in windows)
    job_vcpus = max((current.vcpus * window["typical_peak"] / 100 for window in windows), default=0)
    slots = _join([f"{window['start']}–{window['end']}" for window in windows])
    stretched = round(job_minutes * max(1.0, (job_vcpus - baseline_vcpus) / cap_vcpus), 1)

    target_hourly = price_book.hourly(target) if price_book else None
    priced = target_hourly is not None and None not in (result.get("current_monthly_cost"), result.get("target_monthly_cost"))
    target_cost = round(target_hourly * HOURS_PER_MONTH, 2) if priced else None
    saving = round(result["current_monthly_cost"] - target_cost, 2) if priced else None
    incremental = round(result["target_monthly_cost"] - target_cost, 2) if priced else None
    readiness = result["readiness_status"]
    if readiness == READY and min(window["day_ratio"] for window in windows) >= 0.9 \
            and all(window["weekdays"] is None for window in windows):
        confidence = "MEDIUM"  # never HIGH: it depends on a change nobody made yet
    else:
        confidence = "LOW"
    projected_peak = (cpu.get("max_p995") or 0) * current.vcpus / target.vcpus
    insight["conditional"] = {
        "action_type": SCHEDULE_OR_CAP_JOB,
        "readiness_status": readiness,
        "confidence": confidence,
        "to_config": dict(target.as_dict(), memory_sized_from_telemetry=need["memory_from_telemetry"]),
        "sizing_basis": {"cpu": need["cpu_basis"], "memory": need["memory_basis"],
                         "required_vcpus": need["vcpus"], "required_memory_gb": need["memory_gb"]},
        "cpu_without_recurring_peaks": {
            key: cpu[key] for key in ("p95", "p99", "max", "max_p99", "max_p995") if key in cpu
        },
        "projected_cpu_peak_p995": round(projected_peak, 2),
        "target_monthly_cost": target_cost,
        "monthly_saving": saving,
        "incremental_monthly_saving": incremental,
        "baseline_vcpus_p95": round(baseline_vcpus, 2),
        "suggested_cap_vcpus": cap_vcpus,
        "suggested_cpu_quota_percent": int(round(cap_vcpus * 100)),
        "estimated_job_minutes_capped": stretched,
        "suggested_actions": [
            f"Limitar a CPU do processo do job a ~{cap_vcpus:g} vCPU: systemd CPUQuota={int(round(cap_vcpus * 100))}% "
            "na unit/serviço, ou um cgroup (cpu.max) dedicado; nice/ionice reduz a prioridade sem limitar.",
            f"Mover o horário da rotina (backup, cron, ETL) que roda às {slots} para uma janela ociosa.",
            "Escalonar: dividir a rotina em lotes menores ou alternar os dias de execução para não somar picos.",
        ],
        "rationale": [
            f"Picos recorrentes nos horários {detection['label']}: duram ~{job_minutes:g} min por dia e "
            f"somam {detection['cpu_time_share']:.1%} do tempo de CPU da janela.",
            f"Sem esses picos (o job limitado ou reagendado, mesmo trabalho), o P99,5 dos picos cai para "
            f"{cpu.get('max_p995') or 0:.1f}% e a CPU seria definida {BASIS_LABEL.get(need['cpu_basis'], need['cpu_basis'])}: "
            f"{current.vcpus:g} → {target.vcpus:g} vCPU.",
            f"Com o limite de ~{cap_vcpus:g} vCPU o job levaria ~{stretched:g} min em vez de ~{job_minutes:g} min.",
            "Economia condicionada: só vale se o job for limitado ou reagendado; não entra no total projetado até ser aceita.",
        ],
    }
    return insight

