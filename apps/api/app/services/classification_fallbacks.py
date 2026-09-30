"""Provision last-resort organizational classification for cloud cost sources.

The fallback is deliberately scoped to cloud providers and is applied only by
the classifier's existing first-match-wins behavior to rows whose
``organograma`` is still empty after all specific rules have run.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.destination import DestinationTable
from app.models.rules import ClassificationRule, Department
from app.models.source_connection import SourceConnection
from app.services.cloud_scope import resolve_connection_scope

logger = logging.getLogger(__name__)

FALLBACK_PROJECT = "SEM PROJETO DEFINIDO"
FALLBACK_DIRECTORY = "DIR INFRAESTRUTURA E CLOUD"
_CLOUD_NAMES = {
    "azure": ("Azure",),
    "gcp": ("GCP",),
    "oci": ("ORACLE", "OCI", "Oracle Cloud"),
    "ibm": ("IBM", "IBM Cloud"),
    "aws": ("AWS", "Amazon Web Services"),
    "mongodb": ("Atlas-MongoDB", "MongoDB", "Atlas"),
}


def _canon(value: str | None) -> str:
    return "".join(char for char in (value or "").casefold() if char.isalnum())


async def ensure_cloud_classification_fallbacks(db: AsyncSession) -> int:
    """Ensure one idempotent catch-all classification rule per cloud scope.

    Each provider gets a single ``SEM PROJETO DEFINIDO`` leaf below its
    ``DIR INFRAESTRUTURA E CLOUD`` node. Provider roots follow the existing
    Consumo Cloud Showback/Chargeback tree; IBM defaults to Chargeback while
    other cloud providers default to Showback.
    """
    connections = (await db.execute(select(SourceConnection))).scalars().all()
    scopes = sorted({
        scope for connection in connections
        if (scope := resolve_connection_scope(connection.cloud_scope, connection.cloud_name))
        in _CLOUD_NAMES
    })
    if not scopes:
        return 0

    destination = (await db.execute(
        select(DestinationTable).where(DestinationTable.physical_name == "dest_modelo_focus")
    )).scalars().first()
    if destination is None:
        logger.info("Skipping cloud classification fallbacks: FOCUS destination is not seeded yet")
        return 0

    departments = (await db.execute(select(Department))).scalars().all()
    by_parent_name = {(item.parent_id, _canon(item.name)): item for item in departments}
    roots = [item for item in departments if _canon(item.name) == _canon("Consumo Cloud")]
    root = roots[0] if roots else None
    if root is None:
        logger.warning("Skipping cloud classification fallbacks: Consumo Cloud root is missing")
        return 0

    ensured = 0
    for scope in scopes:
        category_name = "Chargeback" if scope == "ibm" else "Showback"
        category = by_parent_name.get((root.id, _canon(category_name)))
        if category is None:
            logger.warning("Skipping %s fallback: %s branch is missing", scope, category_name)
            continue

        provider = next((
            item for name in _CLOUD_NAMES[scope]
            if (item := by_parent_name.get((category.id, _canon(name)))) is not None
        ), None)
        if provider is None:
            provider = Department(parent_id=category.id, name=_CLOUD_NAMES[scope][0])
            db.add(provider)
            await db.flush()
            departments.append(provider)
            by_parent_name[(category.id, _canon(provider.name))] = provider

        directory = by_parent_name.get((provider.id, _canon(FALLBACK_DIRECTORY)))
        if directory is None:
            directory = Department(parent_id=provider.id, name=FALLBACK_DIRECTORY)
            db.add(directory)
            await db.flush()
            departments.append(directory)
            by_parent_name[(provider.id, _canon(directory.name))] = directory

        project = by_parent_name.get((directory.id, _canon(FALLBACK_PROJECT)))
        if project is None:
            project = Department(parent_id=directory.id, name=FALLBACK_PROJECT)
            db.add(project)
            await db.flush()
            departments.append(project)
            by_parent_name[(directory.id, _canon(project.name))] = project

        rule_name = f"Fallback organizacional | {scope}"
        rule = (await db.execute(
            select(ClassificationRule).where(ClassificationRule.name == rule_name)
        )).scalars().first()
        values = {
            "connection_id": None,
            "destination_table_id": destination.id,
            "department_id": project.id,
            "source_field": "source_resource_group_name",
            "pattern": ".*",
            "pattern_type": "regex",
            "match_value": FALLBACK_PROJECT,
            "target_field": "organograma",
            "conditions": [{
                "field": "source_resource_group_name",
                "operator": "~",
                "values": [".*"],
                "logic": "AND",
            }],
            "priority": 2_147_483_647,
            "cloud_scope": scope,
            "active": True,
        }
        if rule is None:
            db.add(ClassificationRule(name=rule_name, **values))
        else:
            for key, value in values.items():
                setattr(rule, key, value)
        ensured += 1

    return ensured
