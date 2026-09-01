from types import SimpleNamespace

from app.services.oci_operational_monitoring import configured_compartments


def test_configured_compartments_accepts_list_and_csv():
    assert configured_compartments(SimpleNamespace(config_json={"generative_ai_compartment_ids": [" a ", "b"]})) == ["a", "b"]
    assert configured_compartments(SimpleNamespace(config_json={"generative_ai_compartment_ids": "a, b"})) == ["a", "b"]
    assert configured_compartments(SimpleNamespace(config_json={})) == []
