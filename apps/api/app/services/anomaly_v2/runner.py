"""Orquestrador do motor v2."""
from __future__ import annotations

import logging
import asyncio
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping

import pandas as pd

from app.database import async_session
from app.services.anomaly_v2.classifier import classify_scopes
from app.services.anomaly_v2.commitment_ledger import refresh_commitment_ledger
from app.services.anomaly_v2.daily_build import build_daily_aggregate, load_daily_history
from app.services.anomaly_v2.detectors import TIER_ALERT, TIER_WATCH, run_detectors_for_scope
from app.services.anomaly_v2.gates import apply_gates, cloud_load_quality, valid_days_by_cloud
from app.services.anomaly_v2.hierarchy import consolidate_alerts
from app.services.anomaly_v2.readiness import max_day_by_cloud, readiness_fingerprint
from app.services.anomaly_v2.scoring import score_signal
from app.services.anomaly_v2.schema import SIGNAL_COLUMNS, SIGNAL_TABLE, SILVER_NS, ddl_statements
from app.services.anomaly_v2.scope import display_scope
from app.services.anomaly_v2.settings import detector_settings
from app.services.anomaly_v2.query_control import execute as trino_execute

logger = logging.getLogger(__name__)

MODEL_VERSION = "v2.2"


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


async def _source_snapshot_id(config: Mapping[str, Any]) -> str:
    from app.services.iceberg_service import get_catalog

    def read() -> str:
        table = get_catalog().load_table((str(config["source_namespace"]), str(config["destination_physical_name"])))
        snapshot = table.current_snapshot()
        return str(snapshot.snapshot_id) if snapshot else "empty"

    return await asyncio.to_thread(read)


async def _publish_signals(run_id: int, rows: list[dict[str, Any]]) -> int:
    import app.services.iceberg_service as iceberg_service

    df = pd.DataFrame(rows, columns=[name for name, _ in SIGNAL_COLUMNS])
    # Coluna toda nula vira string no pandas; a tabela já é double.
    await asyncio.to_thread(iceberg_service.publish_dataframe_atomic,
        SILVER_NS,
        SIGNAL_TABLE,
        df,
        column_types=dict(SIGNAL_COLUMNS),
    )
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
        "history_days": max(1, int(row.history_days or 90)),
        "calibration_days": row.calibration_days,
        "data_lag_days": row.data_lag_days,
        "source_namespace": row.source_namespace,
        "destination_physical_name": row.destination_physical_name,
        "date_column": row.date_column,
        "period_column": row.period_column,
        "type_column": row.type_column,
        "measure_column": row.measure_column,
        "severity_tiers": getattr(row, "severity_tiers", None),
        "detector_settings": detector_settings(getattr(row, "detector_settings", None)),
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
    from contextlib import suppress
    from app.services.anomaly_v2.query_control import cancel_event
    from app.services.trino_service import TrinoCancelled

    event = asyncio.Event()
    token = cancel_event.set(event)

    async def watch_cancellation() -> None:
        while True:
            if should_abort and await asyncio.to_thread(should_abort):
                event.set()
                return
            await asyncio.sleep(1)

    watcher = asyncio.create_task(watch_cancellation()) if should_abort else None
    try:
        return await _execute_detection_v2(config_id, run_id=run_id, progress=progress,
            should_abort=should_abort, detected_at=detected_at, force=force)
    except TrinoCancelled:
        async with async_session() as session:
            return await _finish_cancelled(session, run_id)
    finally:
        if watcher:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher
        cancel_event.reset(token)


async def _execute_detection_v2(
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
            if run_row is not None and run_row.status == "CANCELLED":
                return {"status": "CANCELLED"}
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
        engine_settings = config["detector_settings"]
        # Não segurar transação/lock de linha durante minutos de Trino: isso
        # trava o cancelamento, o progresso do worker e as migrações da API.
        await session.commit()
        lag = int(config_row.data_lag_days if config_row.data_lag_days is not None else 2)
        as_of = _analysis_end(detected_at, lag)
        history_days = int(config.get("history_days") or 90)

        report(1, 8, "Verificando prontidão")
        source_snapshot_id = await _source_snapshot_id(config)
        history = await load_daily_history(config, as_of=as_of, history_days=history_days)
        if await _source_snapshot_id(config) != source_snapshot_id:
            raise AnomalyV2RunError("A origem mudou durante a leitura; execute novamente para avaliar um snapshot consistente")
        current_max = max_day_by_cloud(history)
        if not current_max:
            raise AnomalyV2RunError("Nenhum realizado cloud disponível na janela de análise")
        prev_fp = None
        from sqlalchemy import select

        previous = (await session.execute(select(AnomalyRun).where(
            AnomalyRun.anomaly_config_id == config_id,
            AnomalyRun.status == "COMPLETED",
            AnomalyRun.id != (run_id or 0),
        ).order_by(AnomalyRun.id.desc()).limit(1))).scalar_one_or_none()
        previous_summary = (previous.config_snapshot or {}).get("v2_summary", {}) if previous else {}
        if previous_summary.get("model_version") == MODEL_VERSION:
            prev_fp = (previous.config_snapshot or {}).get("readiness_fingerprint")
        current_run = await session.get(AnomalyRun, run_id) if run_id else None
        force = force or bool(current_run and current_run.trigger_type == "MANUAL")
        fingerprint = readiness_fingerprint(current_max, int(config_row.version), source_snapshot_id)
        # The readiness reads must not retain PostgreSQL table locks while
        # aggregation, commitment queries and model evaluation run in Trino.
        await session.commit()
        if not force and prev_fp == fingerprint:
            skipped = {
                "status": "SKIPPED_NO_NEW_DATA",
                "as_of": as_of.isoformat(),
                "fingerprint": fingerprint,
                "engine": "v2",
                "model_version": MODEL_VERSION,
            }
            if run_id:
                run = await session.get(AnomalyRun, run_id)
                if run:
                    run.status = "COMPLETED"
                    run.phase = "Sem dado novo"
                    run.finished_at = datetime.now(timezone.utc)
                    snap = dict(run.config_snapshot or {})
                    snap["readiness_fingerprint"] = fingerprint
                    snap["v2_summary"] = skipped
                    run.config_snapshot = snap
                    await session.commit()
            return skipped
        if should_abort and should_abort():
            return await _finish_cancelled(session, run_id)

        report(2, 8, "Agregado diário incremental")
        daily_build = await build_daily_aggregate(config, as_of=as_of, lookback_days=history_days)

        report(3, 8, "Livro de compromissos")
        ledger_payload = await refresh_commitment_ledger(
            config, as_of=as_of, history_days=int(engine_settings["commitment_history_days"])
        )
        ledger = ledger_payload["entries"]
        commitment_scope_ids = {str(e["scope_id"]) for e in ledger}

        report(4, 8, "Classificando séries")
        history_by_cloud: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in history:
            history_by_cloud[str(row.get("cloud_origem") or row.get("cloud") or "")].append(row)
        profiles = {}
        for cloud, rows in history_by_cloud.items():
            profiles.update(classify_scopes(rows, as_of=date.fromisoformat(current_max[cloud]),
                                           commitment_scope_ids=commitment_scope_ids,
                                           settings=engine_settings))
        scope_meta: dict[str, dict[str, Any]] = {}
        for row in history:
            sid = str(row["scope_id"])
            scope_meta.setdefault(sid, row)

        mat_daily, mat_monthly = _materiality(config)
        min_pct = float(config.get("min_relative_deviation") or 0.2)
        investigate = int(config.get("investigate_score") or 55)
        quality_by_day: dict[str, dict[str, float]] = {}
        valid_days = valid_days_by_cloud(history, settings=engine_settings)

        report(5, 8, "Detectando desvios")
        all_signals: list[dict[str, Any]] = []
        rows_by_scope: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in history:
            rows_by_scope[str(row["scope_id"])].append(row)
        ledger_by_scope: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for entry in ledger:
            ledger_by_scope[str(entry.get("scope_id"))].append(entry)
        for sid, scope_rows in rows_by_scope.items():
            if should_abort and should_abort():
                return await _finish_cancelled(session, run_id)
            profile = profiles.get(sid, {"cost_nature": "CONTINUOUS", "history_days": 0})
            cloud = str(scope_meta.get(sid, {}).get("cloud_origem") or scope_meta.get(sid, {}).get("cloud") or "")
            scope_end = date.fromisoformat(current_max.get(cloud, as_of.isoformat()))
            scope_ledger = ledger_by_scope.get(sid, [])
            raw = run_detectors_for_scope(
                scope_id=sid,
                daily=scope_rows,
                profile=profile,
                as_of=scope_end,
                materiality=mat_daily,
                min_pct=min_pct,
                ledger_entries=scope_ledger,
                valid_days=valid_days.get(cloud, set()),
                settings=engine_settings,
            )
            conf = "HIGH" if int(profile.get("history_days") or 0) >= int(engine_settings["high_confidence_history_days"]) else "LOW"
            for sig in raw:
                event_day = str(sig["day"])[:10]
                if event_day not in quality_by_day:
                    day = date.fromisoformat(event_day)
                    quality_days = int(engine_settings["load_quality_lookback_days"])
                    quality_by_day[event_day] = cloud_load_quality(history, day=event_day, baseline_days=[
                        (day - timedelta(days=offset)).isoformat() for offset in range(1, quality_days + 1)
                    ], settings=engine_settings)
                ratio = quality_by_day[event_day].get(cloud, 1.0)
                gated = apply_gates(
                    sig,
                    materiality_daily=mat_daily,
                    materiality_monthly=mat_monthly,
                    min_pct=min_pct,
                    cloud_ratio=ratio,
                    confidence=conf,
                    persistence_days=int(sig.get("persistence_days") or 1),
                    settings=engine_settings,
                )
                scored = score_signal(
                    gated,
                    materiality=mat_daily,
                    severity_tiers=config.get("severity_tiers"),
                    confidence=conf,
                    settings=engine_settings,
                )
                if float(scored["score"]) < int(config.get("investigate_score") or 0) and gated.get("tier") == TIER_ALERT:
                    gated["tier"] = TIER_WATCH
                    gated["reason_codes"] = list(gated.get("reason_codes") or []) + ["BELOW_INVESTIGATION_SCORE"]
                gated.update(scored)
                gated["cost_nature"] = profile.get("cost_nature")
                gated["confidence"] = conf
                gated["run_id"] = run_id or 0
                gated["model_version"] = MODEL_VERSION
                gated["reason_codes"] = ",".join(gated.get("reason_codes") or [])
                all_signals.append(gated)

        if should_abort and should_abort():
            return await _finish_cancelled(session, run_id)

        report(6, 8, "Consolidando incidentes")
        alerts = [s for s in all_signals if s.get("tier") == TIER_ALERT]
        watches = [s for s in all_signals if s.get("tier") == TIER_WATCH]
        incidents = consolidate_alerts(alerts, scope_meta)
        for incident in incidents:
            sid = str(incident["leader"]["scope_id"])
            row = scope_meta.get(sid, {})
            incident["leader"] = {**row, **incident["leader"]}
            incident["contributors"] = [
                {**scope_meta.get(str(item["scope_id"]), {}), **item}
                for item in incident["contributors"]
            ]

        report(7, 8, "Publicando sinais")
        if await _source_snapshot_id(config) != source_snapshot_id:
            raise AnomalyV2RunError("A origem mudou durante a detecção; execute novamente para publicar dados consistentes")
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
        case_counts = await _sync_cases(
            session,
            config_id=config_id,
            incidents=incidents,
            watches=watches,
            as_of=as_of,
            detected_at=detected_at,
            config_row=config_row,
        )
        returns_updated = await _apply_return_engine(session, config_id=config_id, history=history,
                                                     as_of=as_of, valid_days=valid_days,
                                                     settings=engine_settings)

        result = {
            "status": "COMPLETED",
            "engine": "v2",
            "as_of": as_of.isoformat(),
            "data_end_by_cloud": current_max,
            "fingerprint": fingerprint,
            "signals": len(all_signals),
            "alerts": len(alerts),
            "watches": len(watches),
            "incidents": len(incidents),
            "rows_written": written,
            **case_counts,
            "returns_updated": returns_updated,
            "daily_build_rows": daily_build.get("row_count"),
            "ledger_entries": len(ledger),
            "model_version": MODEL_VERSION,
        }
        if run_id:
            run = await session.get(AnomalyRun, run_id)
            if run is not None and run.status == "CANCELLED":
                await session.commit()
                return {"status": "CANCELLED"}
            if run is not None:
                snap = dict(run.config_snapshot or {})
                snap["readiness_fingerprint"] = fingerprint
                snap["v2_summary"] = result
                run.config_snapshot = snap
                run.status = "COMPLETED"
                run.phase = "Detecção v2 publicada"
                run.finished_at = datetime.now(timezone.utc)
                run.rows_written = int(result.get("rows_written") or 0)
                run.source_snapshot_id = source_snapshot_id
                run.data_cutoff = as_of.isoformat()
                run.evaluation_start = (as_of - timedelta(days=history_days - 1)).isoformat()
                run.evaluation_end = as_of.isoformat()
                run.reference_kind = "V2_BASELINE"
                run.assessment_table = SIGNAL_TABLE
                run.total_series = run.processed_series = len(rows_by_scope)
                run.cases_created = int(result.get("cases_created") or 0)
                run.cases_updated = int(result.get("cases_updated") or 0)
        await session.commit()
        return result


async def _finish_cancelled(session, run_id: int | None) -> dict[str, Any]:
    from app.models.anomaly import AnomalyRun

    if run_id:
        run = await session.get(AnomalyRun, run_id)
        if run is not None and run.status in ("QUEUED", "RUNNING"):
            run.status = "CANCELLED"
            run.phase = "Cancelado pelo usuário"
            run.finished_at = datetime.now(timezone.utc)
            run.error = None
    await session.commit()
    return {"status": "CANCELLED"}


async def _apply_return_engine(session, *, config_id: int, history: list[dict[str, Any]], as_of: date,
                               valid_days: dict[str, set[str]], settings: Mapping[str, Any]) -> int:
    from sqlalchemy import select

    from app.models.anomaly import AnomalyAction, AnomalyCase, AnomalyCaseEvent
    from app.services.anomaly_v2.return_engine import DEVIATION_ACTIVE, evaluate_return

    rows = (
        await session.execute(
            select(AnomalyCase).where(
                AnomalyCase.state != "CLOSED",
                AnomalyCase.anomaly_config_id == config_id,
                AnomalyCase.detector == "SPIKE",
                (AnomalyCase.deviation_status == DEVIATION_ACTIVE) | (AnomalyCase.deviation_status.is_(None)),
            )
        )
    ).scalars().all()
    updated = 0
    rows_by_scope: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in history:
        rows_by_scope[str(row.get("scope_id"))].append(row)
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
                ).limit(1)
            )
        ).scalar_one_or_none() is not None
        ret = evaluate_return(
            daily=rows_by_scope.get(str(case.series_id), []),
            scope_id=case.series_id,
            episode_start=start,
            as_of=as_of,
            treated=treated,
            valid_days=valid_days.get(str(case.cloud or (case.series_dimensions or {}).get("cloud_origem") or ""), set()),
            settings=settings,
        )
        if ret.get("deviation_status") != DEVIATION_ACTIVE:
            previous_status = case.deviation_status
            case.deviation_status = ret["deviation_status"]
            case.episode_end = ret.get("returned_at")
            case.case_version = int(case.case_version or 1) + 1
            session.add(AnomalyCaseEvent(case_id=case.id, event_type="RETURN_DETECTED",
                from_state=case.state, to_state=case.state, case_version=case.case_version,
                payload={"before": previous_status, "after": case.deviation_status,
                         "returned_at": case.episode_end, "model_version": MODEL_VERSION},
                note="Retorno confirmado por três dias coletados válidos; workflow preservado"))
            # Recovery does not erase the incurred excess or close human workflow.
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
) -> dict[str, int]:
    """Cria casos a partir de incidentes v2 (sem atribuição automática)."""
    from sqlalchemy import select

    from app.models.anomaly import AnomalyCase, AnomalyCaseEvent
    from app.services.anomaly_detector import case_key_for
    from app.services.anomaly_v2.explanation import explain_signal
    from app.services.anomaly_workflow import enqueue_case, resolve_routing

    mat_daily, _ = _materiality(_config_from_row(config_row))
    created = 0
    updated = ignored = 0
    # Every alerted leaf remains accessible; a parent grouping must not hide
    # all contributors except its largest one.
    leaves = [leaf for incident in incidents for leaf in (incident.get("contributors") or [incident["leader"]])]
    for leader in leaves:
        sid = str(leader["scope_id"])
        meta = {k: v for k, v in leader.items() if k != "tier"}
        disp = display_scope(meta)
        family = "|".join(f"{k}={v}" for k, v in disp.items() if v) or sid
        event_type = str(leader.get("detector") or "SPIKE")
        event_day = str(leader.get("day") or as_of.isoformat())[:10]
        event_period = event_day[:7].replace("-", "")
        episode_start = str(leader.get("episode_start") or event_day)[:10]
        identity = f"config={config_id}|{family}|episode={episode_start}" if event_type != "MONTHLY_ACCUMULATED" else f"config={config_id}|{family}"
        case_key = case_key_for(sid, identity, event_type, period=event_period)
        existing = (
            await session.execute(select(AnomalyCase).where(
                AnomalyCase.anomaly_config_id == config_id,
                AnomalyCase.series_id == sid,
                AnomalyCase.detector == event_type,
                (AnomalyCase.period == event_period) if event_type == "MONTHLY_ACCUMULATED"
                else (AnomalyCase.episode_start == episode_start),
            ).order_by(AnomalyCase.id).limit(1))
        ).scalar_one_or_none()
        if existing is not None:
            if existing.state == "CLOSED":
                ignored += 1
                continue
            observed = float(leader.get("observed") or 0)
            delta = observed - float(existing.expected_cloud or 0)
            revised = {**leader, "delta": delta,
                       "pct_delta": delta / max(float(existing.expected_cloud or 0), 1)}
            prioritization = score_signal(revised, materiality=mat_daily,
                severity_tiers=getattr(config_row, "severity_tiers", None), confidence=existing.confidence,
                settings=_config_from_row(config_row)["detector_settings"])
            fields = {
                "event_date": event_day,
                "observed_cloud": observed,
                "delta_cloud": delta,
                "anomalous_cost_cloud": max(0.0, delta),
                "impact_to_date": max(float(existing.impact_to_date or 0), max(0.0, delta)),
                "score": prioritization["score"],
                "severity": prioritization["severity"],
                "projected_month_impact": prioritization["projected_month_impact"],
                "explanation": explain_signal(revised, materiality=mat_daily,
                    min_pct=float(config_row.min_relative_deviation or 0.2)),
                "episode_end": leader.get("episode_end"),
            }
            if existing.deviation_status in (None, "EM_DESVIO", "AUTOCORRIGIDO", "CORRIGIDO_COM_ACAO"):
                fields["deviation_status"] = str(leader.get("deviation_status") or "EM_DESVIO")
            changed = {field: value for field, value in fields.items() if getattr(existing, field) != value}
            if changed:
                before = {field: getattr(existing, field) for field in changed}
                for field, value in changed.items():
                    setattr(existing, field, value)
                existing.case_version = int(existing.case_version or 1) + 1
                session.add(AnomalyCaseEvent(case_id=existing.id, event_type="DETECTION_UPDATED",
                    from_state=existing.state, to_state=existing.state, case_version=existing.case_version,
                    payload={"before": before, "after": changed, "model_version": MODEL_VERSION},
                    note="Reavaliação do desvio; referência e workflow preservados"))
                updated += 1
            continue
        routing = resolve_routing(config_row.routing_rules or [], meta)
        reason_raw = leader.get("reason_codes") or ""
        reasons = reason_raw if isinstance(reason_raw, list) else [c for c in str(reason_raw).split(",") if c]
        returned = str(leader.get("deviation_status") or "") == "AUTOCORRIGIDO"
        case = AnomalyCase(
            case_key=case_key,
            anomaly_config_id=config_id,
            series_id=sid,
            series_dimensions=meta,
            event_family=family,
            event_type=event_type,
            event_date=str(leader.get("day") or as_of.isoformat())[:10],
            period=event_period,
            state="CLOSED" if returned else "NEW",
            severity=str(leader.get("severity") or "MEDIUM"),
            score=float(leader.get("score") or 0),
            confidence=str(leader.get("confidence") or "MEDIUM"),
            observed_cloud=float(leader.get("observed") or 0),
            expected_cloud=float(leader.get("expected") or 0),
            lower_cloud=float(leader.get("lower") or 0),
            upper_cloud=float(leader.get("upper") or 0),
            delta_cloud=float(leader.get("delta") or 0),
            anomalous_cost_cloud=max(0.0, float(leader.get("delta") or 0)),
            evaluation_grain="MONTHLY_EVENT" if event_type == "MONTHLY_ACCUMULATED" else "DAY",
            reference_kind="V2_BASELINE",
            reason_codes=reasons,
            cloud=disp.get("cloud"),
            subaccount_name=disp.get("subaccount_name"),
            resource_group=disp.get("resource_group"),
            service_name=disp.get("service_name"),
            region=disp.get("region"),
            detector=event_type,
            deviation_status=str(leader.get("deviation_status") or "EM_DESVIO"),
            projected_month_impact=float(leader.get("projected_month_impact") or 0),
            impact_to_date=max(0.0, float(leader.get("delta") or 0)),
            explanation=explain_signal(
                leader,
                materiality=mat_daily,
                min_pct=float(config_row.min_relative_deviation or 0.2),
            ),
            suggested_owner_id=routing.assignee_user_id,
            routing_rule_id=routing.rule_id,
            episode_start=str(leader.get("episode_start") or leader.get("day") or as_of.isoformat())[:10],
            episode_end=str(leader.get("episode_end") or "")[:10] or None,
            closed_at=detected_at if returned else None,
        )
        session.add(case)
        await session.flush()
        session.add(AnomalyCaseEvent(case_id=case.id, event_type="CASE_CREATED", to_state=case.state,
            case_version=1, payload={"model_version": MODEL_VERSION, "scope_id": sid,
                                   "incident_parent": next((incident["parent_key"] for incident in incidents
                                       if any(str(item["scope_id"]) == sid for item in incident.get("contributors", []))), None)},
            note="Detecção v2 com referência anterior ao evento"))
        if not returned:
            await enqueue_case(session, case=case, reason="Novo desvio v2")
        created += 1
    return {"cases_created": created, "cases_updated": updated, "closed_cases_preserved": ignored}
