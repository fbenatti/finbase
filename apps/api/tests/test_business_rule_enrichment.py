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
from app.models.rules import Department

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
        "department_id": 1,
        "department_resolved": False,
    }
    assert "financial" not in envelope
    # Linha que nao deu match permanece sem classificacao
    assert pd.isna(df.loc[1, "business_rule"])


def test_cloud_fallback_classifies_only_rows_left_unmapped():
    fallback = _classification_rule(
        id=99,
        name="Fallback organizacional | azure",
        source_field="source_resource_group_name",
        pattern=".*",
        pattern_type="regex",
        match_value="SEM PROJETO DEFINIDO",
        conditions=[{
            "field": "source_resource_group_name",
            "operator": "~",
            "values": [".*"],
            "logic": "AND",
        }],
        priority=2_147_483_647,
        department_id=2,
    )
    df = pd.DataFrame({
        "source_resource_group_name": ["rg-unmapped", "rg-already-mapped"],
        "organograma": [None, "Datamesh"],
    })
    infra = Department(id=1, parent_id=None, name="DIR INFRAESTRUTURA E CLOUD")
    project = Department(id=2, parent_id=1, name="SEM PROJETO DEFINIDO")

    _apply_classifications_sync(df, [fallback], dept_map={1: infra, 2: project})

    assert df.loc[0, "organograma"] == "SEM PROJETO DEFINIDO"
    assert df.loc[0, "organograma_nivel_1"] == "DIR INFRAESTRUTURA E CLOUD"
    assert df.loc[0, "organograma_nivel_2"] == "SEM PROJETO DEFINIDO"
    assert df.loc[1, "organograma"] == "Datamesh"
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
        "financial_effect": "NONE",
        "financial_contract_id": None,
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


def test_unlinked_classification_resolves_department_by_cloud_path():
    root = SimpleNamespace(
        id=1, parent_id=None, name="Consumo Cloud", centro_custo=None,
        id_orcamento=None, ambiente=None, custom_fields=None,
    )
    azure = SimpleNamespace(
        id=2, parent_id=1, name="Azure", centro_custo=None,
        id_orcamento=None, ambiente=None, custom_fields=None,
    )
    ibm = SimpleNamespace(
        id=3, parent_id=1, name="IBM", centro_custo=None,
        id_orcamento="EXTERNO-IBM", ambiente=None, custom_fields=None,
    )
    azure_condominio = SimpleNamespace(
        id=4, parent_id=2, name="Condominio", centro_custo=None,
        id_orcamento=None, ambiente=None, custom_fields=None,
    )
    ibm_condominio = SimpleNamespace(
        id=5, parent_id=3, name="Condominio", centro_custo=None,
        id_orcamento=None, ambiente=None, custom_fields=None,
    )
    rule = _classification_rule(
        id=99,
        cloud_scope="ibm",
        department_id=None,
        match_value="Condominio",
    )
    frame = pd.DataFrame({"SubAccountId": ["vivo-ti-vicky-hml"]})

    _apply_classifications_sync(
        frame,
        [rule],
        dept_map={node.id: node for node in (root, azure, ibm, azure_condominio, ibm_condominio)},
        hierarchy_cache={},
    )

    assert frame.loc[0, "organograma"] == "Condominio"
    assert frame.loc[0, "organograma_nivel_1"] == "Consumo Cloud"
    assert frame.loc[0, "organograma_nivel_2"] == "IBM"
    assert frame.loc[0, "organograma_nivel_3"] == "Condominio"
    assert frame.loc[0, "id_orcamento"] == "EXTERNO-IBM"
    envelope = json.loads(frame.loc[0, "business_rule"])
    assert envelope["classification"]["department_id"] == 5
    assert envelope["classification"]["department_resolved"] is True


def test_oci_scope_resolves_renamed_oracle_level_without_provider_hardcode():
    def node(id, parent_id, name, id_orcamento=None):
        return SimpleNamespace(id=id, parent_id=parent_id, name=name, id_orcamento=id_orcamento, custom_fields=None, centro_custo=None, ambiente=None)
    root = node(1, None, "Consumo Cloud")
    showback = node(2, 1, "Showback")
    oracle = node(3, 2, "ORACLE", "ID2534-OCI")
    project = node(4, 3, "Condominio")
    rule = _classification_rule(
        id=100,
        cloud_scope="oci",
        department_id=None,
        match_value="Condominio",
    )
    frame = pd.DataFrame({"SubAccountId": ["vivo-ti-vicky-hml"]})

    _apply_classifications_sync(
        frame,
        [rule],
        dept_map={node.id: node for node in (root, showback, oracle, project)},
        hierarchy_cache={},
    )

    assert frame.loc[0, "organograma_nivel_3"] == "ORACLE"
    assert frame.loc[0, "id_orcamento"] == "ID2534-OCI"


def test_duplicate_legacy_departments_same_parent_use_stable_id():
    def node(id, parent_id, name):
        return SimpleNamespace(id=id, parent_id=parent_id, name=name, id_orcamento=None, custom_fields=None, centro_custo=None, ambiente=None)
    root = node(1, None, "Consumo Cloud")
    azure = node(2, 1, "Azure")
    branch = node(3, 2, "DIR INFRAESTRUTURA E CLOUD")
    first = node(4, 3, "Compra RIs")
    second = node(5, 3, "Compra RIs")
    rule = _classification_rule(id=101, cloud_scope="azure", department_id=None, match_value="Compra RIs")
    frame = pd.DataFrame({"SubAccountId": ["vivo-ti-vicky-hml"]})

    _apply_classifications_sync(
        frame,
        [rule],
        dept_map={item.id: item for item in (root, azure, branch, first, second)},
        hierarchy_cache={},
    )

    assert frame.loc[0, "organograma_nivel_4"] == "Compra RIs"


def test_financial_fallback_cannot_overwrite_commitment_rule():
    commitment = _financial_rule(
        id=19,
        name="Commitment",
        cloud_scope="oci",
        target_column="realizado_cloud",
        rule_type="EXPRESSION",
        expression="BilledCost * 0.5",
        financial_effect="BUDGET_COMMITMENT",
        financial_contract_id=2,
    )
    fallback = _financial_rule(
        id=9,
        name="Fallback",
        cloud_scope=None,
        target_column="consumo_realizado",
        rule_type="EXPRESSION",
        expression="BilledCost",
        financial_effect="DIRECT_REALIZED",
        financial_contract_id=None,
        priority=100,
    )
    frame = pd.DataFrame({"BilledCost": [100.0]})

    _apply_business_rule_sync(frame, commitment, "202608")
    _apply_business_rule_sync(frame, fallback, "202608")

    assert frame.loc[0, "realizado_cloud"] == 50.0
    assert "consumo_realizado" not in frame.columns or pd.isna(frame.loc[0, "consumo_realizado"])
    assert frame.loc[0, "financial_effect"] == "BUDGET_COMMITMENT"
    assert '"Fallback"' not in str(frame.loc[0, "business_rule"])
