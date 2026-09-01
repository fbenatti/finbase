"""Classification rules are selected by cloud scope, never connection id."""

from app.services.cloud_scope import rule_matches_cloud


def test_azure_classification_scope_matches_different_azure_connection():
    """A rule authored from azure-4p also applies while reprocessing azure-ti."""
    legacy_rule_connection_id = 1  # azure-4p; intentionally irrelevant here
    reprocessed_connection_id = 2  # azure-ti

    assert legacy_rule_connection_id != reprocessed_connection_id
    assert rule_matches_cloud("azure", "azure", "Azure-TI")


def test_classification_scope_rejects_another_cloud():
    assert not rule_matches_cloud("azure", "gcp", "GCP-TI")
