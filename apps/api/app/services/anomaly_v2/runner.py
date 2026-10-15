"""Orquestrador do motor v2."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping

import pandas as pd

from app.database import async_session
from app.services.anomaly_v2.classifier import classify_scopes
from app.services.anomaly_v2.commitment_ledger import refresh_commitment_ledger
from app.services.anomaly_v2.daily_build import build_daily_aggregate, load_daily_history
from app.services.anomaly_v2.detectors import TIER_ALERT, TIER_WATCH, run_detectors_for_scope
from app.services.anomaly_v2.gates import apply_gates, cloud_load_quality
from app.services.anomaly_v2.hierarchy import consolidate_alerts
from app.services.anomaly_v2.readiness import clouds_advanced, max_day_by_cloud, readiness_fingerprint
from app.services.anomaly_v2.scoring import score_signal
from app.services.anomaly_v2.schema import SIGNAL_TABLE, SILVER_NS, ddl_statements
from app.services.anomaly_v2.scope import display_scope
from app.services.trino_service import execute as trino_execute

logger = logging.getLogger(__name__)

MODEL_VERSION = "v2.0"


class AnomalyV2RunError(RuntimeError):
    pass


def _materiality(config: Mapping[str, Any]) -> tuple[float, float]:
    currency = str(config.get("currency") or "BRL")
    by_cur = dict(config.get("materiality_by_currency") or {})
    daily = float(by_cur.get(currency, 500.0))
    monthly = float(config.get("materiality_monthly") or daily * 6)
    return daily, monthly


async def _ensure_tables() -> None:
    for stmt in ddl_statements():
        await trino_execute(stmt, timeout_s=120)


async def _publish_signals(run_id: int, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    import app.services.iceberg_service as iceberg_service

    df = pd.DataFrame(rows)
    iceberg_service.publish_dataframe_atomic(SILVER_NS, SIGNAL_TABLE, df)
    return len(rows)


def _config_from_row(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "version": row.version,
        "currency": row.currency,
        "profile": row.profile,
        "min_relative_deviation": row.min_relative_deviation,
        "investigate_score": row.investigate_score,
        "materiality_by_currency": row.materiality_by_currency,
        "materiality_monthly": getattr(row, "materiality_monthly", None),
        "history_days": min(int(row.history_days or 90), 90),
        "calibration_days": row.calibration_days,
        "data_lag_days": row.data_lag_days,
        "source_namespace": row.source_namespace,
        "destination_physical_name": row.destination_physical_name,
        "date_column": row.date_column,
        "period_column": row.period_column,
        "type_column": row.type_column,
        "measure_column": row.measure_column,
        "severity_tiers": getattr(row, "severity_tiers", None),
    }


def _analysis_end(detected_at: datetime, lag_days: int) -> date:
    return (detected_at.date() - timedelta(days=max(0, int(lag_days))))


async def execute_detection_v2(
    config_id: int,
    *,
    run_id: int | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    should_abort: Callable[[], bool] | None = None,
    detected_at: datetime | None = None,
    force: bool = False,
) -> dict[str, Any]:
    from app.models.anomaly import AnomalyConfig, AnomalyRun

    detected_at = detected_at or datetime.now(timezone.utc)

    def report(step: int, total: int, msg: str) -> None:
        if progress:
            progress(step, total, msg)

    async with async_session() as session:
        if run_id:
            run_row = await session.get(AnomalyRun, run_id)
            if run_row is not None:
                run_row.status = "RUNNING"
                run_row.phase = "Motor v2"
                run_row.started_at = run_row.started_at or detected_at
        config_row = await session.get(AnomalyConfig, config_id)
        if config_row is None:
            raise AnomalyV2RunError(f"configuração {config_id} não encontrada")
        if not bool(config_row.enabled):
            raise AnomalyV2RunError("configuração desabilitada")
        config = _config_from_row(config_row)
        lag = int(config_row.data_lag_days or 2)
        as_of = _analysis_end(detected_at, lag)
        history_days = int(config.get("history_days") or 90)

        report(1, 8, "Verificando prontidão")
        history = await load_daily_history(config, as_of=as_of, history_days=history_days)
        current_max = max_day_by_cloud(history)
        prev_fp = None
        if run_id:
            run = await session.get(AnomalyRun, run_id)
            if run and isinstance(run.config_snapshot, dict):
                prev_fp = (run.config_snapshot or {}).get("readiness_fingerprint")
        fingerprint = readiness_fingerprint(current_max, int(config_row.version))
        previous_clouds = None
        if prev_fp:
            import json

            try:
                previous_clouds = json.loads(prev_fp).get("clouds")
            except json.JSONDecodeError:
                previous_clouds = None
        if not force and prev_fp == fingerprint:
            skipped = {
                "status": "SKIPPED_NO_NEW_DATA",
                "as_of": as_of.isoformat(),
                "fingerprint": fingerprint,
            }
            if run_id:
                run = await session.get(AnomalyRun, run_id)
                if run:
                    run.status = "COMPLETED"
                    run.phase = "Sem dado novo"
                    run.finished_at = datetime.now(timezone.utc)
                    snap = dict(run.config_snapshot or {})
                    snap["readiness_fingerprint"] = fingerprint
                    run.config_snapshot = snap
                    await session.commit()
            return skipped
        if not force and not clouds_advanced(current_max, previous_clouds):
            skipped = {
                "status": "SKIPPED_NO_NEW_DATA",
                "as_of": as_of.isoformat(),
                "fingerprint": fingerprint,
            }
            if run_id:
                run = await session.get(AnomalyRun, run_id)
                if run:
                    run.status = "COMPLETED"
                    run.phase = "Sem dado novo"
                    run.finished_at = datetime.now(timezone.utc)
                    await session.commit()
            return skipped

        if should_abort and should_abort():
            raise AnomalyV2RunError("abortado")

        report(2, 8, "Agregado diário incremental")
        daily_build = await build_daily_aggregate(config, as_of=as_of, lookback_days=history_days)

        report(3, 8, "Livro de compromissos")
        ledger_payload = await refresh_commitment_ledger(config, as_of=as_of)
        ledger = ledger_payload["entries"]
        commitment_scope_ids = {str(e["scope_id"]) for e in ledger}

        report(4, 8, "Classificando séries")
        profiles = classify_scopes(
            history,
            as_of=as_of,
            commitment_scope_ids=commitment_scope_ids,
        )
        scope_meta: dict[str, dict[str, Any]] = {}
        for row in history:
            sid = str(row["scope_id"])
            scope_meta.setdefault(sid, row)

        mat_daily, mat_monthly = _materiality(config)
        min_pct = float(config.get("min_relative_deviation") or 0.2)
        investigate = int(config.get("investigate_score") or 55)
        baseline_days = sorted({str(r["day"])[:10] for r in history})[-14:]
        cloud_ratios = cloud_load_quality(history, day=as_of.isoformat(), baseline_days=baseline_days)

        report(5, 8, "Detectando desvios")
        all_signals: list[dict[str, Any]] = []
        scope_ids = {str(r["scope_id"]) for r in history}
        for sid in scope_ids:
            if should_abort and should_abort():
                break
            profile = profiles.get(sid, {"cost_nature": "CONTINUOUS", "history_days": 0})
            scope_ledger = [e for e in ledger if str(e.get("scope_id")) == sid]
            raw = run_detectors_for_scope(
                scope_id=sid,
                daily=history,
                profile=profile,
                as_of=as_of,
                materiality=mat_daily,
                min_pct=min_pct,
                investigate_score=investigate,
                ledger_entries=scope_ledger,
            )
            cloud = str(scope_meta.get(sid, {}).get("cloud_origem") or scope_meta.get(sid, {}).get("cloud") or "")
            ratio = cloud_ratios.get(cloud, 1.0)
            conf = "HIGH" if int(profile.get("history_days") or 0) >= 28 else "LOW"
            for sig in raw:
                gated = apply_gates(
                    sig,
                    materiality_daily=mat_daily,
                    materiality_monthly=mat_monthly,
                    min_pct=min_pct,
                    cloud_ratio=ratio,
                    confidence=conf,
                )
                scored = score_signal(
                    gated,
                    materiality=mat_daily,
                    severity_tiers=config.get("severity_tiers"),
                    confidence=conf,
                )
                gated.update(scored)
                gated["cost_nature"] = profile.get("cost_nature")
                gated["confidence"] = conf
                gated["run_id"] = run_id or 0
                gated["model_version"] = MODEL_VERSION
                gated["reason_codes"] = ",".join(gated.get("reason_codes") or [])
                all_signals.append(gated)

        report(6, 8, "Consolidando incidentes")
        alerts = [s for s in all_signals if s.get("tier") == TIER_ALERT]
        watches = [s for s in all_signals if s.get("tier") == TIER_WATCH]
        incidents = consolidate_alerts(alerts, scope_meta)
        for incident in incidents:
            sid = str(incident["leader"]["scope_id"])
            row = scope_meta.get(sid, {})
            incident["leader"] = {**row, **incident["leader"]}

        report(7, 8, "Publicando sinais")
        await _ensure_tables()
        pub_rows = [
            {
                "run_id": run_id or 0,
                "scope_id": s["scope_id"],
                "day": s["day"],
                "detector": s.get("detector"),
                "cost_nature": s.get("cost_nature"),
                "observed": s.get("observed"),
                "expected": s.get("expected"),
                "lower": s.get("lower"),
                "upper": s.get("upper"),
                "delta": s.get("delta"),
                "pct_delta": s.get("pct_delta"),
                "z_robust": s.get("z_robust"),
                "forecast_cloud": None,
                "tier": s.get("tier"),
                "reason_codes": s.get("reason_codes"),
                "confidence": s.get("confidence"),
                "model_version": MODEL_VERSION,
            }
            for s in all_signals
        ]
        written = await _publish_signals(run_id or 0, pub_rows)

        report(8, 8, "Persistindo casos")
        cases_created = await _sync_cases(
            session,
            config_id=config_id,
            incidents=incidents,
            watches=watches,
            as_of=as_of,
            detected_at=detected_at,
            config_row=config_row,
        )
        returns_updated = await _apply_return_engine(session, history=history, as_of=as_of)

        result = {
            "status": "COMPLETED",
            "engine": "v2",
            "as_of": as_of.isoformat(),
            "fingerprint": fingerprint,
            "signals": len(all_signals),
            "alerts": len(alerts),
            "watches": len(watches),
            "incidents": len(incidents),
            "rows_written": written,
            "cases_created": cases_created,
            "returns_updated": returns_updated,
            "daily_build_rows": daily_build.get("row_count"),
            "ledger_entries": len(ledger),
            "model_version": MODEL_VERSION,
        }
        if run_id:
            run = await session.get(AnomalyRun, run_id)
            if run:
                snap = dict(run.config_snapshot or {})
                snap["readiness_fingerprint"] = fingerprint
                snap["v2_summary"] = result
                run.config_snapshot = snap
                run.status = "COMPLETED"
                run.phase = "Detecção v2 publicada"
                run.finished_at = datetime.now(timezone.utc)
                run.rows_written = int(result.get("rows_written") or 0)
                run.processed_series = int(result.get("signals") or 0)
                run.cases_created = int(result.get("cases_created") or 0)
        await session.commit()
        return result


async def _apply_return_engine(session, *, history: list[dict[str, Any]], as_of: date) -> int:
    from sqlalchemy import select

    from app.models.anomaly import AnomalyAction, AnomalyCase
    from app.services.anomaly_v2.impact import compute_impact
    from app.services.anomaly_v2.return_engine import DEVIATION_ACTIVE, evaluate_return

    rows = (
        await session.execute(
            select(AnomalyCase).where(
                AnomalyCase.state != "CLOSED",
                (AnomalyCase.deviation_status == DEVIATION_ACTIVE) | (AnomalyCase.deviation_status.is_(None)),
            )
        )
    ).scalars().all()
    updated = 0
    for case in rows:
        start_s = case.episode_start or case.event_date
        try:
            start = date.fromisoformat(str(start_s)[:10])
        except ValueError:
            continue
        treated = (
            await session.execute(
                select(AnomalyAction).where(
                    AnomalyAction.case_id == case.id,
                    AnomalyAction.status == "COMPLETED",
                )
            )
        ).scalar_one_or_none() is not None
        ret = evaluate_return(
            daily=history,
            scope_id=case.series_id,
            episode_start=start,
            as_of=as_of,
            treated=treated,
        )
        if ret.get("deviation_status") != DEVIATION_ACTIVE:
            case.deviation_status = ret["deviation_status"]
            case.episode_end = ret.get("returned_at")
            impact = compute_impact(
                daily_points=[],
                episode_start=start,
                episode_end=as_of,
                detector=case.detector or case.event_type,
                deviation_status=case.deviation_status,
                treated=treated,
            )
            case.projected_month_impact = impact["projected_saving_monthly"] or case.projected_month_impact
            case.impact_to_date = impact["incurred_excess_cost"]
            updated += 1
    return updated


async def _sync_cases(
    session,
    *,
    config_id: int,
    incidents: list[dict[str, Any]],
    watches: list[dict[str, Any]],
    as_of: date,
    detected_at: datetime,
    config_row: Any,
) -> int:
    """Cria casos a partir de incidentes v2 (sem atribuição automática)."""
    from sqlalchemy import select

    from app.models.anomaly import AnomalyCase
    from app.services.anomaly_detector import case_key_for
    from app.services.anomaly_v2.explanation import explain_signal
    from app.services.anomaly_workflow import enqueue_case, resolve_routing

    mat_daily, _ = _materiality(_config_from_row(config_row))
    created = 0
    for incident in incidents:
        leader = incident["leader"]
        sid = str(leader["scope_id"])
        meta = {k: v for k, v in leader.items() if k != "tier"}
        disp = display_scope(meta)
        family = "|".join(f"{k}={v}" for k, v in disp.items() if v) or sid
        event_type = str(leader.get("detector") or "SPIKE")
        case_key = case_key_for(sid, family, event_type, period=as_of.strftime("%Y%m"))
        existing = (
            await session.execute(select(AnomalyCase).where(AnomalyCase.case_key == case_key))
        ).scalar_one_or_none()
        if existing is not None:
            continue
        routing = resolve_routing(config_row.routing_rules or [], meta)
        reason_raw = leader.get("reason_codes") or ""
        reasons = reason_raw if isinstance(reason_raw, list) else [c for c in str(reason_raw).split(",") if c]
        case = AnomalyCase(
            case_key=case_key,
            anomaly_config_id=config_id,
            series_id=sid,
            series_dimensions=meta,
            event_family=family,
            event_type=event_type,
            event_date=str(leader.get("day") or as_of.isoformat())[:10],
            period=as_of.strftime("%Y%m"),
            state="NEW",
            severity=str(leader.get("severity") or "MEDIUM"),
            score=float(leader.get("score") or 0),
            confidence=str(leader.get("confidence") or "MEDIUM"),
            observed_cloud=float(leader.get("observed") or 0),
            expected_cloud=float(leader.get("expected") or 0),
            lower_cloud=float(leader.get("lower") or 0),
            upper_cloud=float(leader.get("upper") or 0),
            delta_cloud=float(leader.get("delta") or 0),
            anomalous_cost_cloud=max(0.0, float(leader.get("delta") or 0)),
            evaluation_grain="DAY",
            reference_kind="V2_BASELINE",
            reason_codes=reasons,
            cloud=disp.get("cloud"),
            subaccount_name=disp.get("subaccount_name"),
            resource_group=disp.get("resource_group"),
            service_name=disp.get("service_name"),
            region=disp.get("region"),
            detector=event_type,
            deviation_status="EM_DESVIO",
            projected_month_impact=float(leader.get("projected_month_impact") or 0),
            impact_to_date=max(0.0, float(leader.get("delta") or 0)),
            explanation=explain_signal(
                leader,
                materiality=mat_daily,
                min_pct=float(config_row.min_relative_deviation or 0.2),
            ),
            suggested_owner_id=routing.assignee_user_id,
            routing_rule_id=routing.rule_id,
            episode_start=str(leader.get("day") or as_of.isoformat())[:10],
        )
        session.add(case)
        await session.flush()
        await enqueue_case(session, case=case, reason="Novo desvio v2")
        created += 1
    return created
