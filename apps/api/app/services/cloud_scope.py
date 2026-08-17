"""Helpers for scoping rules and lookups by cloud provider."""

from __future__ import annotations


def connection_cloud_scope(cloud_name: str | None) -> str:
    """Map SourceConnection.cloud_name to a stable provider slug."""
    name = (cloud_name or "").strip().lower()
    if name.startswith("azure"):
        return "azure"
    if name.startswith("gcp"):
        return "gcp"
    if "atlas" in name or "mongodb" in name:
        return "atlas"
    if "-" in name:
        return name.split("-", 1)[0]
    return name or "unknown"


def rule_matches_cloud(rule_cloud_scope: str | None, connection_cloud_name: str | None) -> bool:
    if not rule_cloud_scope:
        return True
    return rule_cloud_scope == connection_cloud_scope(connection_cloud_name)
