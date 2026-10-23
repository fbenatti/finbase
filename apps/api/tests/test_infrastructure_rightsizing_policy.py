from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models.infrastructure_opportunity import InfraOpportunity, InfraOpportunityRun, InfraRightsizingPolicy
from app.models.source_connection import SourceConnection
from app.services.infrastructure_metrics import opportunity_runner
from app.services.infrastructure_metrics.opportunities import DEFAULT_POLICY, KEEP, RIGHTSIZE_DOWN, analyze_resource
from app.services.infrastructure_metrics.policy import (
    CUSTOM, PARAMETER_KEYS, PRESETS, RightsizingPolicyParams, classify, effective_policy, load_active_policy,
)
from app.services.infrastructure_metrics.providers.oci_rightsizing import OCI_FLEX_RULES, OciFlexPriceBook
from app.services.infrastructure_metrics.sizing import CapacitySnapshot, FlexSizingCatalog

VM = CapacitySnapshot(
    provider="oci", resource_id="r1", resource_name="vm-a", sku="VM.Optimized3.Flex",
    family="VM.Optimized3.Flex", vcpus=10.0, memory_gb=32.0, attributes={"ocpu": 5.0},
)
CATALOG = FlexSizingCatalog(OCI_FLEX_RULES)
PRICES = OciFlexPriceBook(ocpu_hour=0.28, memory_gb_hour=0.0079, currency="BRL")


def _stats(p95, p99, peak, peak_p995=None, peak_p99=None, peak_p999=None):
    return {"p50": p95 / 2, "p95": p95, "p99": p99, "max": peak, "coverage": 1.0, "days_observed": 7,
            "samples": 2016, "expected_buckets": 2016,
            "max_p99": peak_p99 if peak_p99 is not None else peak,
            "max_p995": peak_p995 if peak_p995 is not None else peak,
            "max_p999": peak_p999 if peak_p999 is not None else peak}


def _policy(name):
    return {**DEFAULT_POLICY, **PRESETS[name]}


def _analyze(cpu, memory, policy):
    return analyze_resource(resource_id="r1", capacity=VM, cpu=cpu, memory=memory, catalog=CATALOG,
                            price_book=PRICES, policy=policy)


def test_presets_are_valid_and_balanced_is_the_default():
    for name, values in PRESETS.items():
        RightsizingPolicyParams(**values)
        assert classify(values) == name
    assert PRESETS["BALANCED"] == {key: DEFAULT_POLICY[key] for key in PARAMETER_KEYS}
    assert classify({**PRESETS["BALANCED"], "min_change_ratio": 0.2}) == CUSTOM


@pytest.mark.parametrize("change", [
    {"cpu_target_p95": 0.1},
    {"cpu_target_p95": 0.99},
    {"memory_peak_limit": 1.2},
    {"min_change_ratio": -0.1},
    {"min_days": 0},
    {"cpu_peak_quantile": 97},
    {"cpu_target_p95": 0.9, "cpu_target_p99": 0.8},
    {"memory_target_p95": 0.9, "memory_target_p99": 0.85},
    {"min_coverage": 0.95, "high_confidence_coverage": 0.9},
    {"unknown": 1},
])
def test_invalid_policies_are_rejected(change):
    with pytest.raises(ValidationError):
        RightsizingPolicyParams(**{**PRESETS["BALANCED"], **change})


def test_aggressive_policy_recommends_smaller_sizes():
    cpu, memory = _stats(30, 35, 40), _stats(50, 55, 58)
    balanced = _analyze(cpu, memory, DEFAULT_POLICY)
    aggressive = _analyze(cpu, memory, _policy("AGGRESSIVE"))
    conservative = _analyze(cpu, memory, _policy("CONSERVATIVE"))
    assert aggressive["to_config"]["vcpus"] < balanced["to_config"]["vcpus"]
    assert conservative["to_config"]["memory_gb"] > balanced["to_config"]["memory_gb"]
    assert aggressive["monthly_saving"] > balanced["monthly_saving"] > conservative["monthly_saving"]
    assert aggressive["evidence"]["policy"]["cpu_target_p95"] == PRESETS["AGGRESSIVE"]["cpu_target_p95"]


def test_peak_quantile_selects_which_peak_sizes_the_cpu():
    cpu = _stats(10, 15, 95, peak_p99=20, peak_p995=50, peak_p999=80)
    by_quantile = {
        q: _analyze(cpu, _stats(30, 35, 40), {**DEFAULT_POLICY, "cpu_peak_quantile": q})["to_config"]["vcpus"]
        for q in (99.0, 99.5, 99.9)
    }
    assert by_quantile[99.0] < by_quantile[99.5] < by_quantile[99.9]


def test_min_change_ratio_keeps_small_reductions():
    cpu, memory = _stats(55, 70, 75), _stats(65, 72, 75)
    assert _analyze(cpu, memory, {**DEFAULT_POLICY, "min_change_ratio": 0.5})["action_type"] == KEEP
    assert _analyze(cpu, memory, {**DEFAULT_POLICY, "min_change_ratio": 0.0})["action_type"] == RIGHTSIZE_DOWN


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        SourceConnection.__table__,
        InfraRightsizingPolicy.__table__, InfraOpportunityRun.__table__, InfraOpportunity.__table__,
    ])
    return Session(engine)


def test_without_saved_revision_the_default_policy_is_active():
    with _session() as session:
        policy = load_active_policy(session)
    assert policy["revision"] is None and policy["aggressiveness"] == "BALANCED"
    assert {key: policy[key] for key in PARAMETER_KEYS} == PRESETS["BALANCED"]


def test_engine_run_uses_the_latest_saved_policy(monkeypatch):
    registration = SimpleNamespace(provider="oci", config_parser=lambda connection: SimpleNamespace(
        enabled=True, resource_ids=("r1",)))
    monkeypatch.setattr(opportunity_runner, "provider_for_connection", lambda connection: registration)
    rightsizing = SimpleNamespace(
        read_capacity=lambda connection, config: {"r1": VM},
        price_books=lambda ids: {"r1": PRICES}, sizing_catalog=CATALOG,
    )
    as_of = datetime(2026, 10, 23, tzinfo=timezone.utc)

    def series(connection_id, resource_id, metric, start, end, resolution):
        level = 30.0 if metric == "cpu.utilization" else 55.0
        return [{"bucket_start_utc": (start + timedelta(minutes=5 * i)).isoformat(), "value_average": level,
                 "value_maximum": level + 10} for i in range(int((end - start).total_seconds() // 300))]

    with _session() as session:
        session.add_all([
            InfraRightsizingPolicy(version=1, aggressiveness="CONSERVATIVE", params=PRESETS["CONSERVATIVE"]),
            InfraRightsizingPolicy(version=2, aggressiveness="AGGRESSIVE", params=PRESETS["AGGRESSIVE"]),
        ])
        session.commit()
        run = opportunity_runner.run_opportunity_engine(
            session, SimpleNamespace(id=7), as_of=as_of, rightsizing=rightsizing, series_reader=series,
        )
        assert run.status == "COMPLETED", run.error_message
        assert run.policy_revision == 2 and run.policy_aggressiveness == "AGGRESSIVE"
        assert run.policy_params == PRESETS["AGGRESSIVE"]
        item = session.query(InfraOpportunity).one()
        assert item.evidence["policy"]["revision"] == 2
        assert item.evidence["policy"]["cpu_target_p95"] == PRESETS["AGGRESSIVE"]["cpu_target_p95"]
        expected = _analyze(item.evidence["cpu"], item.evidence["memory"], effective_policy(
            session.get(InfraRightsizingPolicy, 2)))
        assert item.to_config == expected["to_config"]
        default = _analyze(item.evidence["cpu"], item.evidence["memory"], DEFAULT_POLICY)
        assert item.to_config["vcpus"] < default["to_config"]["vcpus"]
