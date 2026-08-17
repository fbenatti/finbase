"""Helpers for scoping rules and lookups by cloud provider.

The provider slug is now an explicit, canonical field on ``SourceConnection``
(``cloud_scope``), so rules are matched by data, not by guessing from a free-text
``cloud_name``. ``connection_cloud_scope`` is kept only as a backfill/fallback for
legacy rows that predate the column.
"""

from __future__ import annotations

_SUPPORTED_SCOPES = frozenset({"azure", "gcp", "ibm", "aws", "oci", "atlas"})


def connection_cloud_scope(cloud_name: str | None) -> str | None:
    """Derive a canonical provider slug from a free-text ``cloud_name``.

    This is a *legacy backfill* helper only. New code should read the explicit
    ``SourceConnection.cloud_scope`` column instead of inferring from text.
    """
    if not cloud_name:
        return None
    name = cloud_name.strip().lower()
    if not name:
        return None
    if name.startswith("azure"):
        return "azure"
    if name.startswith("gcp"):
        return "gcp"
    if name.startswith("ibm"):
        return "ibm"
    if name.startswith("aws"):
        return "aws"
    if name.startswith("oracle") or name.startswith("oci"):
        return "oci"
    if "atlas" in name or "mongodb" in name:
        return "atlas"
    # Unknown provider: return the raw token so manual matching still works,
    # but never pretend it is one of the known slugs.
    return name.split("-", 1)[0] or name


def resolve_connection_scope(conn_cloud_scope: str | None, cloud_name: str | None) -> str | None:
    """Return the effective scope for a connection.

    Prefers the explicit ``cloud_scope`` column; falls back to deriving it from
    ``cloud_name`` so legacy rows participate in rule matching unchanged.
    """
    scope = (conn_cloud_scope or "").strip().lower() or None
    if scope:
        return scope
    return connection_cloud_scope(cloud_name)


def rule_matches_cloud(
    rule_cloud_scope: str | None,
    connection_cloud_scope_value: str | None,
    connection_cloud_name: str | None = None,
) -> bool:
    """Whether a rule scoped (or unscoped) applies to a connection.

    ``rule_cloud_scope is None`` means "applies to every provider" (the generic
    fallback). Otherwise the rule matches when its scope equals the connection's
    effective scope (explicit ``cloud_scope`` column, or derived from
    ``cloud_name`` as a legacy fallback).
    """
    if not rule_cloud_scope:
        return True
    effective = resolve_connection_scope(connection_cloud_scope_value, connection_cloud_name)
    return rule_cloud_scope == effective
