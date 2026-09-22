import pandas as pd

from app.services.reprocess_transform import build_chunk_payload
from worker import _failure_context


def _mapping():
    from types import SimpleNamespace

    return SimpleNamespace(
        id=1,
        source_column="ServiceName",
        destination_column="ServiceName",
        transform_type="RENAME",
        transform_config=None,
        priority=0,
    )


def test_build_chunk_payload_includes_rules_and_provenance():
    records = [{"ServiceName": "Compute", "BilledCost": 10.0}]
    payload = build_chunk_payload(
        records=records,
        connection_id=7,
        connection_code="azure-ti",
        billing_period="202501",
        brules=[],
        crules=[],
        cmapps=[_mapping()],
        dept_cache={},
        hierarchy_cache={},
        dest_col_names={"ServiceName", "BilledCost"},
    )
    assert payload["connection_code"] == "azure-ti"
    assert payload["connection_id"] == 7
    assert payload["billing_period"] == "202501"
    assert payload["records"][0]["ServiceName"] == "Compute"
    assert payload["cmapps"][0]["destination_column"] == "ServiceName"


def test_failure_context_is_bounded_and_structured():
    context = _failure_context({
        "failure_context": {
            "exception_type": "RuntimeError",
            "traceback": "x" * 20000,
            "phase": "Processando batches",
            "current_blob": "Aguardando primeiro batch",
        }
    })

    assert context["exception_type"] == "RuntimeError"
    assert len(context["traceback"]) == 12000
    assert context["phase"] == "Processando batches"
    assert context["current_blob"] == "Aguardando primeiro batch"
