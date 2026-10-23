from datetime import datetime, timedelta, timezone

import pytest

from app.services.infrastructure_metrics.catalog import OCI_METRICS
from app.services.infrastructure_metrics.collector import (
    collect, estimated_api_calls, plan_queries,
)
from app.services.infrastructure_metrics.contracts import MetricScope


class FakeAdapter:
    provider = "oci"

    def __init__(self):
        self.plans = []

    def fetch(self, plan):
        self.plans.append(plan)
        return plan


def test_query_plan_calculates_api_budget_before_execution():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    scope = MetricScope("oci", 2, "tenancy", "sa-saopaulo-1", ("a", "b"), enabled=True)
    plans = plan_queries(
        scope, OCI_METRICS, start_utc=now, end_utc=now + timedelta(hours=1),
        resolution_seconds=60, statistics=("mean", "p95"),
    )
    assert len(plans) == 8
    assert estimated_api_calls(plans) == 32
    adapter = FakeAdapter()
    assert collect(adapter, plans) == plans
    assert len(adapter.plans) == 8


def test_disabled_or_overbroad_scopes_cannot_be_executed_or_planned():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    disabled = MetricScope("oci", 2, "tenancy", "sa-saopaulo-1", ("a",))
    plans = plan_queries(
        disabled, (OCI_METRICS[0],), start_utc=now,
        end_utc=now + timedelta(minutes=1), resolution_seconds=60,
        statistics=("mean",),
    )
    with pytest.raises(PermissionError, match="disabled"):
        collect(FakeAdapter(), plans)
    too_many = MetricScope("oci", 2, "tenancy", "sa-saopaulo-1", tuple("abcdef"))
    with pytest.raises(ValueError, match="exceeds max_resources"):
        plan_queries(
            too_many, (OCI_METRICS[0],), start_utc=now,
            end_utc=now + timedelta(minutes=1), resolution_seconds=60,
            statistics=("mean",),
        )
