"""Run the rightsizing engine over Silver telemetry and persist its results."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from .config import config_from_connection
from .opportunities import (
    DEFAULT_POLICY, ENGINE_VERSION, READY, RIGHTSIZE_DOWN, analyze_resource,
    effective_unit_prices, summarize_series, window_bounds,
)

ANALYZED_METRICS = ("CpuUtilization", "MemoryUtilization")
SERIES_FIELDS = ("resource_id", "source_metric_name", "bucket_start_utc", "value_average", "value_maximum", "origin_kind")
# Native buckets come first: at 5m/15m OCI computes max over the raw samples.
# Local rollups of 1m buckets (which keep the 1m maximum) fill only the
# windows that were collected at 1m.
ORIGINS_BY_RESOLUTION = {60: ("NATIVE",), 300: ("NATIVE", "DERIVED_ROLLUP"), 900: ("NATIVE", "DERIVED_ROLLUP")}


def read_native_series(
    connection_id: int, resource_id: str, metric: str, start: datetime, end: datetime,
    resolution_seconds: int = 60,
) -> list[dict]:
    """Buckets of one series from the Silver, via an allowlisted scan."""
    from pyiceberg.expressions import And, EqualTo, GreaterThanOrEqual, LessThan

    from app.services import iceberg_service

    if not iceberg_service.table_exists("resource_metric_series", namespace="silver"):
        return []
    table = iceberg_service.get_catalog().load_table(("silver", "resource_metric_series"))
    by_bucket: dict[str, dict] = {}
    for origin in ORIGINS_BY_RESOLUTION[resolution_seconds]:
        expression = And(
            EqualTo("_source_connection_id", connection_id),
            EqualTo("resource_id", resource_id),
            EqualTo("source_metric_name", metric),
            EqualTo("origin_kind", origin),
            EqualTo("resolution_seconds", resolution_seconds),
            GreaterThanOrEqual("bucket_start_utc", start.replace(tzinfo=None)),
            LessThan("bucket_start_utc", end.replace(tzinfo=None)),
        )
        for row in table.scan(row_filter=expression, selected_fields=SERIES_FIELDS).to_arrow().to_pylist():
            if isinstance(row["bucket_start_utc"], datetime):
                row["bucket_start_utc"] = row["bucket_start_utc"].isoformat()
            by_bucket.setdefault(row["bucket_start_utc"], row)
    return [by_bucket[key] for key in sorted(by_bucket)]


def read_billing_prices(resource_ids: tuple[str, ...]) -> dict[str, dict]:
    """Effective unit prices of the resources' own compute meters."""
    from app.services import lakehouse_layers as layers
    from app.services.trino_service import execute_sync

    if not resource_ids:
        return {}
    in_list = ", ".join("'" + rid.replace("'", "''") + "'" for rid in resource_ids)
    rows = execute_sync(
        f"SELECT resourceid AS resource_id, _billing_period AS billing_period, skumeter, "
        f"consumedunit AS unit, billingcurrency AS currency, "
        f"SUM(consumedquantity) AS quantity, SUM(effectivecost) AS cost "
        f"FROM {layers.detail_layer()}.dest_billing_cloud "
        f"WHERE resourceid IN ({in_list}) "
        f"GROUP BY 1, 2, 3, 4, 5",
        timeout_s=180,
    )
    return effective_unit_prices(rows)


def run_opportunity_engine(
    session: Any, connection: Any, *, days: int = 7, resolution_seconds: int = 300,
    user_id: int | None = None,
    as_of: datetime | None = None, capacity_reader=None, series_reader=read_native_series,
    price_reader=read_billing_prices,
):
    """Analyze every allowlisted instance and persist one run with its results."""
    from app.models.infrastructure_opportunity import InfraOpportunity, InfraOpportunityRun

    if resolution_seconds not in ORIGINS_BY_RESOLUTION:
        raise ValueError("resolution_seconds must be 60, 300 or 900")
    config = config_from_connection(connection)
    if not config.enabled:
        raise PermissionError("infrastructure metric collection is disabled")
    start, end = window_bounds(as_of or datetime.now(timezone.utc), days)
    run = InfraOpportunityRun(
        connection_id=int(connection.id), status="RUNNING", engine_version=ENGINE_VERSION,
        policy_version=DEFAULT_POLICY["version"], window_start_utc=start, window_end_utc=end,
        resolution_seconds=resolution_seconds,
        created_by=user_id, readiness_counts={},
    )
    session.add(run)
    session.commit()
    try:
        if capacity_reader is None:
            from .capacity import read_oci_capacity as capacity_reader
        capacity = capacity_reader(connection, config)
        prices = price_reader(config.resource_ids)
        previous = {
            (item.resource_id, item.action_type, str(item.to_config)): item
            for item in session.execute(
                select(InfraOpportunity).where(InfraOpportunity.connection_id == int(connection.id))
                .order_by(InfraOpportunity.id)
            ).scalars()
        }
        results = []
        for resource_id in config.resource_ids:
            stats = {
                metric: summarize_series(
                    series_reader(int(connection.id), resource_id, metric, start, end, resolution_seconds),
                    window_start=start, window_end=end, resolution_seconds=resolution_seconds,
                )
                for metric in ANALYZED_METRICS
            }
            cpu = stats["CpuUtilization"] if stats["CpuUtilization"]["samples"] else None
            memory = stats["MemoryUtilization"] if stats["MemoryUtilization"]["samples"] else None
            result = analyze_resource(
                resource_id=resource_id, capacity=capacity.get(resource_id), cpu=cpu,
                memory=memory, prices=prices.get(resource_id),
            )
            prior = previous.get((resource_id, result["action_type"], str(result["to_config"])))
            opportunity = InfraOpportunity(
                run_id=run.id, connection_id=int(connection.id), provider="oci",
                resource_id=resource_id, resource_name=result["resource_name"],
                action_type=result["action_type"], readiness_status=result["readiness_status"],
                state=prior.state if prior else "NEW", state_note=prior.state_note if prior else None,
                confidence=result["confidence"], from_config=result["from_config"],
                to_config=result["to_config"], current_monthly_cost=result["current_monthly_cost"],
                target_monthly_cost=result["target_monthly_cost"], monthly_saving=result["monthly_saving"],
                currency=result["currency"], rationale=result["rationale"], risks=result["risks"],
                evidence=result["evidence"], inputs_hash=result["inputs_hash"],
                engine_version=result["engine_version"],
            )
            session.add(opportunity)
            results.append(result)
        savings = [
            item["monthly_saving"] for item in results
            if item["readiness_status"] == READY and item["action_type"] == RIGHTSIZE_DOWN
            and (item["monthly_saving"] or 0) > 0
        ]
        counts: dict[str, int] = {}
        for item in results:
            counts[item["readiness_status"]] = counts.get(item["readiness_status"], 0) + 1
        run.resources_analyzed = len(results)
        run.opportunities_count = len(savings)
        run.total_monthly_saving = round(sum(savings), 2) if savings else 0.0
        run.avg_monthly_saving = round(sum(savings) / len(savings), 2) if savings else None
        run.currency = next((item["currency"] for item in results if item["currency"]), None)
        run.readiness_counts = counts
        run.status = "COMPLETED"
    except Exception as exc:
        session.rollback()
        run = session.get(InfraOpportunityRun, run.id)
        run.status = "FAILED"
        run.error_message = f"{type(exc).__name__}: {exc}"[:2000]
    run.finished_at = datetime.now(timezone.utc)
    session.commit()
    return run
