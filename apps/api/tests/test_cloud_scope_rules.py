from app.services.cloud_scope import rule_matches_cloud


def test_rule_matches_cloud_generic_fallback():
    assert rule_matches_cloud(None, "azure", "Azure-TI") is True
    assert rule_matches_cloud(None, "gcp", "GCP-4P") is True


def test_rule_matches_cloud_provider_specific():
    assert rule_matches_cloud("azure", "azure", "Azure-TI") is True
    assert rule_matches_cloud("azure", "gcp", "GCP-4P") is False
    assert rule_matches_cloud("gcp", "gcp", "GCP-TI") is True


def test_rule_matches_cloud_derives_from_cloud_name():
    assert rule_matches_cloud("azure", None, "Azure-4P") is True
    assert rule_matches_cloud("gcp", None, "GCP-TI") is True
