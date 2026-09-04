import pandas as pd

from app.services.reprocess_transform import build_chunk_payload


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
