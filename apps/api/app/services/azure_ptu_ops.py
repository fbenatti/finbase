"""Operational aggregation helpers for Azure PTU Ops dashboard (no FOCUS/cost)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.azure_ptu_meters import observed_tpm, p95 as metric_p95, unused_capacity_pct

UTILIZATION_VALID_MAX = 100.0
P95_MIN_SAMPLES = 20
HIGH_UTIL_THRESHOLD = 80.0
CRITICAL_UTIL_THRESHOLD = 95.0

RECOMMENDATION_CODES = frozenset({
    "CAPACITY_UNDERUSED",
    "PEAK_RISK",
    "THROTTLING",
    "LOAD_IMBALANCE",
    "PERFORMANCE_DEGRADATION",
    "DATA_QUALITY",
})


def _deployment_key(row: dict[str, Any]) -> str:
    return str(row.get("deployment_name") or row.get("id") or "unknown")


def _valid_util(value: float | None) -> float | None:
    if value is None:
        return None
    val = float(value)
    if val < 0 or val > UTILIZATION_VALID_MAX:
        return None
    return val


def weighted_utilization(metrics: list[dict[str, Any]]) -> float | None:
    """PTU-weighted average utilization across hourly rows."""
    num = 0.0
    den = 0.0
    for row in metrics:
        util = _valid_util(row.get("utilization_avg"))
        ptus = float(row.get("provisioned_ptus") or row.get("ptus") or 0)
        if util is None or ptus <= 0:
            continue
        num += util * ptus
        den += ptus
    return num / den if den else None


def peak_utilization_label(metrics: list[dict[str, Any]]) -> tuple[float | None, str]:
    """Return the observed peak from hourly Gold rows.

    Gold contains hourly aggregates, not the raw percentile distribution. Calling
    a percentile over those aggregates would overstate the precision of the KPI.
    """
    valid_max = [_valid_util(row.get("utilization_max")) for row in metrics]
    valid_max = [v for v in valid_max if v is not None]
    if not valid_max:
        return None, "indisponível"
    return max(valid_max), "pico horário observado"


def request_weighted_avg(metrics: list[dict[str, Any]], field: str) -> float | None:
    num = 0.0
    den = 0.0
    for row in metrics:
        req = float(row.get("request_count") or 0)
        val = row.get(field)
        if req <= 0 or val is None:
            continue
        num += float(val) * req
        den += req
    return num / den if den else None


def count_high_util_hours(metrics: list[dict[str, Any]], threshold: float) -> int:
    return sum(
        1 for row in metrics
        if (_valid_util(row.get("utilization_max")) or 0) >= threshold
    )


def metric_field_availability(metrics: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per-field sample count, coverage and availability metadata."""
    tracked = {
        "utilization_avg": "AzureOpenAIProvisionedManagedUtilizationV2",
        "input_tokens_sum": "ProcessedPromptTokens",
        "output_tokens_sum": "GeneratedTokens",
        "total_tokens_sum": "TokenTransaction",
        "request_count": "AzureOpenAIRequests",
        "success_count": "AzureOpenAIRequests",
        "rate_limited_count": "AzureOpenAIRequests",
        "time_to_response_avg_ms": "AzureOpenAITimeToResponse",
        "normalized_ttft_avg_ms": "AzureOpenAINormalizedTTFTInMS",
        "normalized_tbt_avg_ms": "AzureOpenAINormalizedTBTInMS",
        "tokens_per_second_avg": "AzureOpenAITokenPerSecond",
        "context_cache_match_rate_avg": "AzureOpenAIContextTokensCacheMatchRate",
        "availability_rate_avg": "AzureOpenAIAvailabilityRate",
    }
    total = len(metrics)
    out: dict[str, dict[str, Any]] = {}
    for field, monitor_name in tracked.items():
        samples = sum(1 for row in metrics if row.get(field) is not None)
        coverage = round((samples / total) * 100, 1) if total else None
        out[field] = {
            "metric": monitor_name,
            "sample_count": samples,
            "coverage_pct": coverage,
            "available": samples > 0,
            "status": "collected" if samples else ("not_collected" if total else "unsupported"),
        }
    return out


def period_metadata(
    *,
    hours: int,
    requested_grain: str,
    effective_grain: str,
    metrics: list[dict[str, Any]],
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    end = datetime.now(timezone.utc) - timedelta(minutes=10)
    start = end - timedelta(hours=max(1, min(hours, 93 * 24)))
    if date_from:
        try:
            start = datetime.fromisoformat(date_from.replace("Z", "+00:00"))
        except ValueError:
            pass
    if date_to:
        try:
            end = datetime.fromisoformat(date_to.replace("Z", "+00:00"))
        except ValueError:
            pass
    hours_list = sorted({str(m.get("metric_hour") or "") for m in metrics if m.get("metric_hour")})
    last_sample = hours_list[-1] if hours_list else None
    expected_hours = max(1, int((end - start).total_seconds() // 3600))
    return {
        "start": start.strftime("%Y-%m-%dT%H:00:00Z"),
        "end": end.strftime("%Y-%m-%dT%H:00:00Z"),
        "requested_grain": requested_grain,
        "effective_grain": effective_grain,
        "last_sample": last_sample,
        "sample_hours": len(hours_list),
        "expected_hours": expected_hours,
        "coverage_pct": round((len(hours_list) / expected_hours) * 100, 1) if expected_hours else None,
    }


def comparable_periods(current_hours: int, previous_metrics: list[dict[str, Any]] | None) -> bool:
    if not previous_metrics:
        return False
    cur_days = max(1, current_hours // 24)
    prev_hours = len({str(m.get("metric_hour") or "")[:10] for m in previous_metrics if m.get("metric_hour")})
    prev_days = max(1, prev_hours)
    return abs(cur_days - prev_days) <= 1


def build_executive_summary(
    metrics: list[dict[str, Any]],
    inventory: list[dict[str, Any]],
    *,
    previous_metrics: list[dict[str, Any]] | None = None,
    period: dict[str, Any] | None = None,
) -> dict[str, Any]:
    util_weighted = weighted_utilization(metrics)
    peak, peak_label = peak_utilization_label(metrics)
    total_tokens = sum(float(m.get("total_tokens_sum") or m.get("total_tokens") or 0) for m in metrics)
    input_tokens = sum(float(m.get("input_tokens_sum") or m.get("input_tokens") or 0) for m in metrics)
    output_tokens = sum(float(m.get("output_tokens_sum") or m.get("output_tokens") or 0) for m in metrics)
    rate_limited = sum(float(m.get("rate_limited_count") or 0) for m in metrics)
    request_total = sum(float(m.get("request_count") or 0) for m in metrics)
    hours_high_80 = count_high_util_hours(metrics, HIGH_UTIL_THRESHOLD)
    hours_high_95 = count_high_util_hours(metrics, CRITICAL_UTIL_THRESHOLD)
    anomaly_hours = sum(1 for m in metrics if m.get("utilization_quality_anomaly"))
    sample_hours = float(len({m.get("metric_hour") for m in metrics if m.get("metric_hour")}) or 0)

    prev_util = weighted_utilization(previous_metrics) if previous_metrics else None
    prev_tokens = (
        sum(float(m.get("total_tokens_sum") or m.get("total_tokens") or 0) for m in (previous_metrics or []))
        if previous_metrics else None
    )
    periods_comparable = comparable_periods(int((period or {}).get("expected_hours") or 24), previous_metrics)

    narrative_parts: list[str] = []
    if util_weighted is not None:
        narrative_parts.append(f"Utilização média ponderada por PTU: {util_weighted:.1f}%")
    if peak is not None:
        narrative_parts.append(f"Pico ({peak_label}): {peak:.1f}%")
    if hours_high_95:
        narrative_parts.append(f"{hours_high_95} hora(s) acima de 95%")
    if rate_limited:
        narrative_parts.append(f"{int(rate_limited)} requisições com 429")
    if anomaly_hours:
        narrative_parts.append(f"{anomaly_hours} hora(s) com anomalia de qualidade (>100%)")

    return {
        "capacity_decision": {
            "total_ptus": sum(float(x.get("ptus") or x.get("provisioned_ptus") or 0) for x in inventory),
            "deployment_count": len(inventory),
            "utilization_weighted_avg": util_weighted,
            "utilization_peak": peak,
            "utilization_peak_label": peak_label,
            "unused_capacity_pct": unused_capacity_pct(util_weighted),
            "hours_at_or_above_80": hours_high_80,
            "hours_at_or_above_95": hours_high_95,
            "risk_level": (
                "critical" if hours_high_95 else "high" if hours_high_80 else "medium" if (util_weighted or 0) >= 70 else "low"
            ),
        },
        "ai_demand": {
            "total_tokens": total_tokens,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_tpm": observed_tpm(input_tokens, sample_hours),
            "output_tpm": observed_tpm(output_tokens, sample_hours),
            "tokens_per_request": (total_tokens / request_total) if request_total else None,
        },
        "reliability_experience": {
            "availability_rate_avg": request_weighted_avg(metrics, "availability_rate_avg"),
            "time_to_response_avg_ms": request_weighted_avg(metrics, "time_to_response_avg_ms"),
            "normalized_ttft_avg_ms": request_weighted_avg(metrics, "normalized_ttft_avg_ms"),
            "normalized_tbt_avg_ms": request_weighted_avg(metrics, "normalized_tbt_avg_ms"),
            "rate_limited_count": rate_limited,
            "throttle_rate_pct": round((rate_limited / request_total) * 100, 2) if request_total else None,
            "context_cache_match_rate_avg": request_weighted_avg(metrics, "context_cache_match_rate_avg"),
        },
        "period_comparison": {
            "comparable": periods_comparable,
            "utilization_delta_pp": (util_weighted - prev_util) if periods_comparable and util_weighted is not None and prev_util is not None else None,
            "tokens_delta_pct": (
                round(((total_tokens - prev_tokens) / prev_tokens) * 100, 1)
                if periods_comparable and prev_tokens and prev_tokens > 0 else None
            ),
        },
        "narrative": ". ".join(narrative_parts) if narrative_parts else "Sem dados suficientes para narrativa automática no recorte.",
    }


def build_token_consumption(
    metrics: list[dict[str, Any]],
    deployment_stats: list[dict[str, Any]],
    token_series: list[dict[str, Any]],
    grain: str,
) -> dict[str, Any]:
    by_project: dict[str, dict[str, float]] = {}
    for row in metrics:
        key = row.get("project_key") or row.get("project_hint") or "unattributed"
        slot = by_project.setdefault(key, {"input": 0.0, "output": 0.0, "total": 0.0})
        slot["input"] += float(row.get("input_tokens_sum") or row.get("input_tokens") or 0)
        slot["output"] += float(row.get("output_tokens_sum") or row.get("output_tokens") or 0)
        slot["total"] += float(row.get("total_tokens_sum") or row.get("total_tokens") or 0)

    by_model: dict[str, dict[str, Any]] = {}
    for dep in deployment_stats:
        model_key = f"{dep.get('model_name') or '—'}@{dep.get('model_version') or '—'}"
        slot = by_model.setdefault(model_key, {
            "model_name": dep.get("model_name"),
            "model_version": dep.get("model_version"),
            "input_tokens": 0.0,
            "output_tokens": 0.0,
            "total_tokens": 0.0,
            "deployments": 0,
        })
        slot["input_tokens"] += float(dep.get("input_tokens") or 0)
        slot["output_tokens"] += float(dep.get("output_tokens") or 0)
        slot["total_tokens"] += float(dep.get("input_tokens") or 0) + float(dep.get("output_tokens") or 0)
        slot["deployments"] += 1

    rankings = sorted(deployment_stats, key=lambda d: float(d.get("input_tokens") or 0) + float(d.get("output_tokens") or 0), reverse=True)
    return {
        "series": token_series,
        "by_project": [
            {"project_key": k, **v} for k, v in sorted(by_project.items(), key=lambda x: x[1]["total"], reverse=True)
        ],
        "by_model": sorted(by_model.values(), key=lambda m: m["total_tokens"], reverse=True),
        "deployment_rankings": rankings[:20],
        "grain": grain,
        "explanation": "Tokens agregados do Azure Monitor (ProcessedPromptTokens + GeneratedTokens / TokenTransaction).",
    }


def build_token_heatmap(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate token demand by weekday/hour for the selected Gold window."""
    buckets: dict[tuple[int, int], dict[str, float]] = {}
    for row in metrics:
        raw = str(row.get("metric_hour") or "")
        if len(raw) < 13:
            continue
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        key = (dt.weekday(), dt.hour)
        slot = buckets.setdefault(key, {"input_tokens": 0.0, "output_tokens": 0.0, "total_tokens": 0.0, "samples": 0.0})
        slot["input_tokens"] += float(row.get("input_tokens_sum") or row.get("input_tokens") or 0)
        slot["output_tokens"] += float(row.get("output_tokens_sum") or row.get("output_tokens") or 0)
        slot["total_tokens"] += float(row.get("total_tokens_sum") or row.get("total_tokens") or 0)
        slot["samples"] += 1
    return [{"weekday": day, "hour": hour, **values} for (day, hour), values in sorted(buckets.items())]


def build_capacity_risk(
    metrics: list[dict[str, Any]],
    deployment_stats: list[dict[str, Any]],
    utilization_series: list[dict[str, Any]],
    heatmap: list[dict[str, Any]],
    grain: str,
) -> dict[str, Any]:
    sorted_deps = sorted(
        deployment_stats,
        key=lambda d: (
            float(d.get("utilization_max") or 0),
            float(d.get("rate_limited_count") or 0),
        ),
        reverse=True,
    )
    return {
        "utilization_series": utilization_series,
        "heatmap": heatmap if grain == "hour" else [],
        "heatmap_available": grain == "hour" and bool(heatmap),
        "deployment_table": sorted_deps,
        "risk_bands": {"low": 30, "medium": 80, "high": 95, "critical": 100},
        "hours_at_or_above_80": count_high_util_hours(metrics, HIGH_UTIL_THRESHOLD),
        "hours_at_or_above_95": count_high_util_hours(metrics, CRITICAL_UTIL_THRESHOLD),
        "weighted_utilization_avg": weighted_utilization(metrics),
    }


def build_service_quality(metrics: list[dict[str, Any]], latency_series: list[dict[str, Any]], rate_limit_series: list[dict[str, Any]]) -> dict[str, Any]:
    request_total = sum(float(m.get("request_count") or 0) for m in metrics)
    success = sum(float(m.get("success_count") or 0) for m in metrics)
    client_err = sum(float(m.get("client_error_count") or 0) for m in metrics)
    server_err = sum(float(m.get("server_error_count") or 0) for m in metrics)
    spillover = sum(float(m.get("spillover_request_count") or 0) for m in metrics)
    rate_limited = sum(float(m.get("rate_limited_count") or 0) for m in metrics)
    return {
        "latency_series": latency_series,
        "rate_limit_series": rate_limit_series,
        "availability_rate_avg": request_weighted_avg(metrics, "availability_rate_avg"),
        "time_to_response_avg_ms": request_weighted_avg(metrics, "time_to_response_avg_ms"),
        "normalized_ttft_avg_ms": request_weighted_avg(metrics, "normalized_ttft_avg_ms"),
        "normalized_tbt_avg_ms": request_weighted_avg(metrics, "normalized_tbt_avg_ms"),
        "context_cache_match_rate_avg": request_weighted_avg(metrics, "context_cache_match_rate_avg"),
        "requests": {
            "total": request_total or None,
            "success": success or None,
            "client_error": client_err or None,
            "server_error": server_err or None,
            "rate_limited": rate_limited or None,
            "spillover": spillover or None,
            "success_rate_pct": round((success / request_total) * 100, 2) if request_total and (success or client_err or server_err or rate_limited) else None,
            "throttle_rate_pct": round((rate_limited / request_total) * 100, 2) if request_total and (rate_limited or success or client_err or server_err) else None,
        },
    }


def build_ops_recommendations(
    metrics: list[dict[str, Any]],
    deployment_stats: list[dict[str, Any]],
    imbalance: list[dict[str, Any]],
    *,
    period: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    util = weighted_utilization(metrics)
    peak, peak_label = peak_utilization_label(metrics)
    rate_limited = sum(float(m.get("rate_limited_count") or 0) for m in metrics)
    request_total = sum(float(m.get("request_count") or 0) for m in metrics)
    anomaly_hours = sum(1 for m in metrics if m.get("utilization_quality_anomaly"))
    period_label = f"{(period or {}).get('start', '')} → {(period or {}).get('end', '')}"

    if util is not None and util < 30:
        affected = [d["deployment_name"] for d in deployment_stats if (d.get("utilization_avg") or 0) < 30]
        recs.append({
            "code": "CAPACITY_UNDERUSED",
            "severity": "medium",
            "confidence": 0.75,
            "evidence": f"Utilização ponderada {util:.1f}% no período {period_label}",
            "impact": "Capacidade PTU subutilizada",
            "action": "Avaliar consolidação de deployments ou redução de PTUs provisionados",
            "affected_deployments": affected,
            "period": period_label,
        })

    if peak is not None and peak >= CRITICAL_UTIL_THRESHOLD:
        affected = [d["deployment_name"] for d in deployment_stats if (d.get("utilization_max") or 0) >= CRITICAL_UTIL_THRESHOLD]
        recs.append({
            "code": "PEAK_RISK",
            "severity": "high",
            "confidence": 0.85,
            "evidence": f"{peak_label} {peak:.1f}% (limiar {CRITICAL_UTIL_THRESHOLD:.0f}%)",
            "impact": "Risco de saturação e throttling em picos",
            "action": "Revisar capacidade, balanceamento e políticas de rate limit",
            "affected_deployments": affected,
            "period": period_label,
        })

    if rate_limited > 0:
        affected = [d["deployment_name"] for d in deployment_stats if float(d.get("rate_limited_count") or 0) > 0]
        recs.append({
            "code": "THROTTLING",
            "severity": "high" if rate_limited > 10 or (request_total and rate_limited / request_total > 0.01) else "medium",
            "confidence": 0.9,
            "evidence": f"{int(rate_limited)} respostas 429" + (f" ({rate_limited / request_total * 100:.2f}% das requisições)" if request_total else ""),
            "impact": "Experiência degradada por rate limiting",
            "action": "Investigar picos de carga e ajustar capacidade ou quotas",
            "affected_deployments": affected,
            "period": period_label,
        })

    for item in imbalance:
        if item.get("severity") in {"high", "medium"}:
            recs.append({
                "code": "LOAD_IMBALANCE",
                "severity": item.get("severity", "medium"),
                "confidence": 0.8,
                "evidence": item.get("message", ""),
                "impact": "Carga desbalanceada entre deployments do mesmo projeto",
                "action": "Redistribuir tráfego ou rebalancear PTUs entre deployments",
                "affected_deployments": [
                    d.get("deployment_name") for d in deployment_stats
                    if d.get("project_key") == item.get("project_key")
                ],
                "period": period_label,
            })

    ttr = request_weighted_avg(metrics, "time_to_response_avg_ms")
    ttft = request_weighted_avg(metrics, "normalized_ttft_avg_ms")
    if ttr is not None and ttr > 5000:
        recs.append({
            "code": "PERFORMANCE_DEGRADATION",
            "severity": "medium",
            "confidence": 0.7,
            "evidence": f"Latência média ponderada {ttr:.0f} ms",
            "impact": "Experiência do usuário degradada",
            "action": "Investigar latência por deployment e padrões de carga",
            "affected_deployments": [
                d["deployment_name"] for d in deployment_stats
                if (d.get("time_to_response_avg_ms") or 0) > 5000
            ],
            "period": period_label,
        })
    elif ttft is not None and ttft > 3000:
        recs.append({
            "code": "PERFORMANCE_DEGRADATION",
            "severity": "medium",
            "confidence": 0.65,
            "evidence": f"TTFT médio ponderado {ttft:.0f} ms",
            "impact": "Primeiro token lento em fluxos streaming",
            "action": "Revisar tamanho de prompt, cache de contexto e concorrência",
            "affected_deployments": [
                d["deployment_name"] for d in deployment_stats
                if (d.get("normalized_ttft_avg_ms") or 0) > 3000
            ],
            "period": period_label,
        })

    if anomaly_hours:
        recs.append({
            "code": "DATA_QUALITY",
            "severity": "medium",
            "confidence": 0.95,
            "evidence": f"{anomaly_hours} hora(s) com utilização >100% (excluídas de KPIs)",
            "impact": "Métricas de utilização possivelmente distorcidas",
            "action": "Validar série bruta no Bronze e janelas de agregação Azure Monitor",
            "affected_deployments": [
                d["deployment_name"] for d in deployment_stats if d.get("utilization_quality_anomaly")
            ],
            "period": period_label,
        })

    return recs
