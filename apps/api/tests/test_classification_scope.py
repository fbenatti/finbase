"""Classification rules are selected by cloud scope, never connection id."""

from app.services.cloud_scope import rule_matches_cloud


def test_azure_classification_scope_matches_different_azure_connection():
    """A rule authored from azure-producao also applies while reprocessing azure-dev."""
    legacy_rule_connection_id = 1  # azure-producao; intentionally irrelevant here
    reprocessed_connection_id = 2  # azure-dev

    assert legacy_rule_connection_id != reprocessed_connection_id
    assert rule_matches_cloud("azure", "azure", "Azure-Dev")


def test_classification_scope_rejects_another_cloud():
    assert not rule_matches_cloud("azure", "gcp", "GCP-Dev")
