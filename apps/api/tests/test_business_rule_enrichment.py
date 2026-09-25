"""Tests for the Gold ``business_rule`` enrichment envelope.

Covers the two audit fields written during Bronze → Gold reprocess:
- ``classification``: the ClassificationRule that gave match (first-match-wins)
- ``financial``: the financial BusinessRules that effectively wrote a value
"""

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

# task_runner imports oci at module load; stub it (with submodules) for unit tests.
if "oci" not in sys.modules:
    _oci = ModuleType("oci")

    class _ObjectStorageClient:  # noqa: D101
        pass

    class _ServiceError(Exception):  # noqa: D101
        pass

    _oci.object_storage = ModuleType("oci.object_storage")
    _oci.object_storage.ObjectStorageClient = _ObjectStorageClient
    _oci.exceptions = ModuleType("oci.exceptions")
    _oci.exceptions.ServiceError = _ServiceError
    sys.modules["oci"] = _oci
    sys.modules["oci.object_storage"] = _oci.object_storage
    sys.modules["oci.exceptions"] = _oci.exceptions

import pandas as pd

from app.services.task_runner import (
    _apply_business_rule_sync,
    _apply_classifications_sync,
)


def _financial_rule(**kwargs):
    defaults = {
        "id": 1,
        "name": "PTAX BRL",
        "description": None,
        "target_column": "CostBRL",
        "rule_type": "PTAX_BRL",
        "config_json": {"source_column": "BilledCost", "rate": 5.0},
        "conditions": None,
        "expression": None,
        "priority": 5,
        "valid_from": None,
        "valid_until": None,
        "cloud_scope": "azure",
        "active": True,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _classification_rule(**kwargs):
    defaults = {
        "id": 12,
        "name": "GCP TI | vivo-ti-vicky-hml",
        "source_field": "SubAccountId",
        "pattern": '["vivo-ti-vicky-hml"]',
        "pattern_type": "exact",
        "match_value": "Vicky",
        "target_field": "organograma",
        "conditions": None,
        "priority": 10,
        "cloud_scope": "gcp",
        "active": True,
        "department_id": 1,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_classification_rule_records_matched_rule():
    df = pd.DataFrame({"SubAccountId": ["vivo-ti-vicky-hml", "other"]})
    _apply_classifications_sync(df, [_classification_rule()])
    envelope = json.loads(df.loc[0, "business_rule"])
    assert envelope["classification"] == {
        "id": 12,
        "name": "GCP TI | vivo-ti-vicky-hml",
        "scope": "gcp",
        "conditions": [{
            "field": "SubAccountId",
            "operator": "=",
            "values": ["vivo-ti-vicky-hml"],
            "logic": "AND",
        }],
        "priority": 10,
    }
    assert "financial" not in envelope
    # Linha que nao deu match permanece sem classificacao
    assert pd.isna(df.loc[1, "business_rule"])


def test_ptax_rule_records_financial_rule():
    df = pd.DataFrame({"BilledCost": [100.0, 200.0]})
    _apply_business_rule_sync(df, _financial_rule(), "202608")
    assert df["CostBRL"].tolist() == [500.0, 1000.0]
    envelope = json.loads(df.loc[0, "business_rule"])
    assert envelope["financial"] == [{
        "id": 1,
        "name": "PTAX BRL",
        "scope": "azure",
        "conditions": [],
        "priority": 5,
    }]
    assert "classification" not in envelope


def test_first_match_wins_financial_only_first_recorded():
    df = pd.DataFrame({"BilledCost": [100.0]})
    _apply_business_rule_sync(df, _financial_rule(id=1, name="PTAX BRL", priority=5), "202608")
    # MARKUP de prioridade maior mira no mesmo target, mas CostBRL ja foi
    # preenchido pela PTAX -> nao deve ser atribuido a linha.
    _apply_business_rule_sync(
        df,
        _financial_rule(
            id=2,
            name="MARKUP",
            priority=10,
            rule_type="MARKUP",
            target_column="CostBRL",
            config_json={"source_column": "CostBRL", "percentage": 0.1},
        ),
        "202608",
    )
    envelope = json.loads(df.loc[0, "business_rule"])
    assert [r["name"] for r in envelope["financial"]] == ["PTAX BRL"]


def test_expression_rule_records_only_rows_written():
    df = pd.DataFrame({"BilledCost": [100.0, 200.0]})
    rule = _financial_rule(
        id=7,
        name="EXPR Cost",
        rule_type="EXPRESSION",
        expression="BilledCost * 1.1",
        target_column="X",
        config_json={},
    )
    _apply_business_rule_sync(df, rule, "202608")
    envelope = json.loads(df.loc[0, "business_rule"])
    assert [r["name"] for r in envelope["financial"]] == ["EXPR Cost"]


def test_scope_filter_that_matches_nothing_keeps_column_absent():
    df = pd.DataFrame({"BilledCost": [100.0, 200.0]})
    rule = _financial_rule(
        config_json={
            "source_column": "BilledCost",
            "rate": 5.0,
            "scope_filter": {"field": "BilledCost", "op": "==", "value": "999"},
        }
    )
    _apply_business_rule_sync(df, rule, "202608")
    # Nenhuma linha afetada -> a coluna nem e criada neste frame; no pipeline
    # ela sempre existe via stable_stage_cols (preenchida como NULL).
    assert "business_rule" not in df.columns


def test_envelope_merges_financial_and_classification():
    df = pd.DataFrame({
        "BilledCost": [100.0],
        "SubAccountId": ["vivo-ti-vicky-hml"],
    })
    _apply_business_rule_sync(df, _financial_rule(), "202608")
    _apply_classifications_sync(df, [_classification_rule()])
    envelope = json.loads(df.loc[0, "business_rule"])
    assert envelope["classification"]["id"] == 12
    assert envelope["financial"][0]["name"] == "PTAX BRL"


def test_reprocess_registers_business_rule_internal_type():
    etl_source = Path(__file__).resolve().parents[1] / "app" / "routers" / "etl.py"
    text = etl_source.read_text(encoding="utf-8")
    assert '"business_rule": "JSONB"' in text
