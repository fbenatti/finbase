"""Tests for classification rule normalization and evaluation."""

import sys
from types import ModuleType, SimpleNamespace

# task_runner imports oci at module load; stub it for unit tests.
if "oci" not in sys.modules:
    sys.modules["oci"] = ModuleType("oci")

import pandas as pd

from pathlib import Path

from app.services.classification_engine import (
    evaluate_rule_conditions,
    map_legacy_field,
    normalize_rule_conditions,
)
from app.routers.rules import _normalize_return_conditions


def _rule(**kwargs):
    defaults = {
        "conditions": None,
        "source_field": "",
        "pattern": "",
        "pattern_type": "",
        "match_value": "",
        "active": True,
        "department_id": 1,
        "target_field": "organograma",
        "priority": 10,
        "project_id": None,
        "cloud_scope": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestNormalizeRuleConditions:
    def test_return_conditions_use_destination_fields(self):
        conditions = _normalize_return_conditions([
            {"field": "source_charge_type", "operator": "=", "values": ["Return"]},
            {"field": "source_reservation_name", "operator": "~", "values": ["AVS"]},
        ])
        assert conditions == [
            {"field": "ChargeCategory", "operator": "=", "values": ["Return"]},
            {"field": "CommitmentDiscountName", "operator": "~", "values": ["AVS"]},
        ]

    def test_legacy_op_value_schema(self):
        rule = _rule(conditions=[{"field": "resourceGroupName", "op": "~", "value": "vicky"}])
        normalized = normalize_rule_conditions(rule)
        assert normalized == [{
            "field": "source_resource_group_name",
            "operator": "~",
            "values": ["vicky"],
            "logic": "AND",
        }]

    def test_json_array_pattern_derives_exact_match(self):
        rule = _rule(
            source_field="resourceGroupName",
            pattern='["projeto-a-hml"]',
            pattern_type="exact",
            match_value="Vicky",
        )
        normalized = normalize_rule_conditions(rule)
        assert normalized[0]["field"] == "source_resource_group_name"
        assert normalized[0]["operator"] == "="
        assert normalized[0]["values"] == ["projeto-a-hml"]

    def test_regex_catch_all_becomes_contains_star(self):
        rule = _rule(
            source_field="source_resource_group_name",
            pattern=".*",
            pattern_type="regex",
        )
        normalized = normalize_rule_conditions(rule)
        assert normalized[0]["operator"] == "~"
        assert normalized[0]["values"] == [".*"]

    def test_empty_conditions_returns_empty_list(self):
        rule = _rule(conditions=[], pattern="", pattern_type="exact")
        assert normalize_rule_conditions(rule) == []


class TestEvaluateRuleConditions:
    def test_focus_field_matches(self):
        df = pd.DataFrame({
            "source_resource_group_name": ["projeto-a-hml", "other-project"],
            "organograma": ["", ""],
        })
        rule = _rule(conditions=[{
            "field": "source_resource_group_name",
            "operator": "=",
            "values": ["projeto-a-hml"],
            "logic": "AND",
        }], match_value="Vicky")
        mask = evaluate_rule_conditions(df, rule)
        assert mask.tolist() == [True, False]

    def test_legacy_resource_group_name_does_not_match_focus_column(self):
        df = pd.DataFrame({
            "source_resource_group_name": ["projeto-a-hml"],
            "organograma": [""],
        })
        rule = _rule(conditions=[{
            "field": "resourceGroupName",
            "operator": "=",
            "values": ["projeto-a-hml"],
            "logic": "AND",
        }])
        # map_legacy_field maps it, so it should still match after normalization
        mask = evaluate_rule_conditions(df, rule)
        assert mask.tolist() == [True]

    def test_empty_conditions_match_nothing(self):
        df = pd.DataFrame({"source_resource_group_name": ["x"], "organograma": [""]})
        rule = _rule(conditions=[])
        mask = evaluate_rule_conditions(df, rule)
        assert mask.tolist() == [False]

    def test_catch_all_matches_any_row(self):
        df = pd.DataFrame({
            "source_resource_group_name": ["anything", ""],
            "organograma": ["", ""],
        })
        rule = _rule(
            source_field="source_resource_group_name",
            pattern=".*",
            pattern_type="regex",
            match_value="SEM PROJETO DEFINIDO",
        )
        mask = evaluate_rule_conditions(df, rule)
        assert mask.tolist() == [True, True]


class TestPipelineContract:
    def test_reprocess_no_longer_calls_project_subscription_lookup(self):
        etl_source = Path(__file__).resolve().parents[1] / "app" / "routers" / "etl.py"
        text = etl_source.read_text(encoding="utf-8")
        assert "_apply_project_subscription_lookup" not in text

    def test_task_runner_no_longer_defines_project_subscription_lookup(self):
        runner_source = Path(__file__).resolve().parents[1] / "app" / "services" / "task_runner.py"
        text = runner_source.read_text(encoding="utf-8")
        assert "def _apply_project_subscription_lookup" not in text


class TestMapLegacyField:
    def test_resource_group_name_maps_to_focus(self):
        assert map_legacy_field("resourceGroupName") == "source_resource_group_name"

    def test_gcp_project_id_maps_to_sub_account(self):
        assert map_legacy_field("projectId") == "SubAccountId"


class TestGcpClassification:
    def test_sub_account_id_matches_project_id(self):
        df = pd.DataFrame({
            "SubAccountId": ["projeto-a-hml", "seguranca-patrimonial-385514"],
            "organograma": ["", ""],
        })
        rule = _rule(conditions=[{
            "field": "SubAccountId",
            "operator": "=",
            "values": ["seguranca-patrimonial-385514"],
            "logic": "AND",
        }], match_value="Seguranca")
        mask = evaluate_rule_conditions(df, rule)
        assert mask.tolist() == [False, True]
