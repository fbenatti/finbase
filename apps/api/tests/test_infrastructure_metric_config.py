from types import SimpleNamespace

import pytest

from app.services.infrastructure_metrics.config import config_from_connection


def _connection(config_json=None):
    return SimpleNamespace(
        id=5, connector_type="oci", config_json=config_json or {},
    )


def test_configuration_defaults_to_disabled_and_fixed_sixty_day_retention():
    config = config_from_connection(_connection({"private_key": "must-not-leak"}))
    assert config.enabled is False
    assert config.retention_days == 60
    assert config.schedule_enabled is False
    assert "must-not-leak" not in repr(config)
    with pytest.raises(PermissionError, match="disabled"):
        config.scope(5)


def test_enabled_pilot_requires_explicit_two_resource_allowlist():
    valid = _connection({
        "tenancy": "tenancy-a", "region": "sa-saopaulo-1",
        "infrastructure_metrics": {
            "enabled": True, "compartment_id": "compartment-a",
            "resource_ids": ["vm-a", "vm-b"], "resolutions_seconds": [60, 300],
        },
    })
    config = config_from_connection(valid)
    assert config.scope(5).resource_ids == ("vm-a", "vm-b")
    assert config.resolutions_seconds == (60, 300)

    valid.config_json["infrastructure_metrics"]["resource_ids"] = ["a", "b", "c"]
    with pytest.raises(ValueError, match="at most two"):
        config_from_connection(valid)


@pytest.mark.parametrize("override", [
    {"retention_days": 90}, {"schedule_enabled": True},
])
def test_pilot_rejects_retention_or_schedule_overrides(override):
    raw = {"enabled": False, **override}
    with pytest.raises(ValueError):
        config_from_connection(_connection({"infrastructure_metrics": raw}))
