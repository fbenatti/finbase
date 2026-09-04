"""Repair legacy local paths stored by the PyIceberg JDBC/SQL catalog."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from sqlalchemy import text

logger = logging.getLogger(__name__)

_METADATA_VERSION = re.compile(r"^(\d+)-.*\.metadata\.json$")


def file_uri(path: Path) -> str:
    return path.resolve().as_uri()


def normalize_warehouse_uri(location: str | None, warehouse: Path) -> str | None:
    """Map any legacy local warehouse URI to the configured warehouse."""
    if not location:
        return location
    parsed = urlparse(location)
    raw_path = unquote(parsed.path) if parsed.scheme == "file" else location
    marker = "/iceberg/"
    if marker not in raw_path:
        return location
    relative = raw_path.split(marker, 1)[1]
    return file_uri(warehouse / relative)


def uri_to_path(location: str) -> Path:
    parsed = urlparse(location)
    if parsed.scheme != "file":
        raise ValueError(f"unsupported metadata URI scheme: {parsed.scheme!r}")
    return Path(unquote(parsed.path))


def _valid_metadata(path: Path) -> bool:
    if not path.is_file() or not path.name.endswith(".metadata.json"):
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and "format-version" in payload and "table-uuid" in payload


def latest_valid_metadata(metadata_dir: Path) -> Path | None:
    """Return the newest valid Iceberg metadata snapshot deterministically."""
    candidates: list[tuple[int, int, str, Path]] = []
    if not metadata_dir.is_dir():
        return None
    for path in metadata_dir.glob("*.metadata.json"):
        if not _valid_metadata(path):
            continue
        match = _METADATA_VERSION.match(path.name)
        version = int(match.group(1)) if match else -1
        candidates.append((version, path.stat().st_mtime_ns, path.name, path))
    return max(candidates, default=None, key=lambda item: item[:3])[3] if candidates else None


def repair_metadata_location(location: str | None, warehouse: Path) -> tuple[str | None, str]:
    """Normalize one location and recover a missing snapshot when possible."""
    normalized = normalize_warehouse_uri(location, warehouse)
    if not normalized:
        return normalized, "unchanged"
    try:
        path = uri_to_path(normalized)
    except ValueError:
        return normalized, "unchanged"
    if _valid_metadata(path):
        return normalized, "normalized" if normalized != location else "unchanged"
    candidate = latest_valid_metadata(path.parent)
    if candidate:
        return file_uri(candidate), "fallback"
    return normalized, "missing"


async def migrate_iceberg_catalog_paths(connection, warehouse: Path) -> dict[str, int]:
    """Idempotently repair ``iceberg_tables`` if PyIceberg created it."""
    exists = await connection.scalar(text("SELECT to_regclass('public.iceberg_tables') IS NOT NULL"))
    stats = {"updated": 0, "fallback": 0, "missing": 0}
    if not exists:
        logger.info("iceberg_tables does not exist yet; catalog path migration skipped")
        return stats

    columns = set((await connection.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'iceberg_tables'"
    ))).scalars())
    required = {"catalog_name", "table_namespace", "table_name", "metadata_location"}
    if not required.issubset(columns):
        logger.warning("iceberg_tables has an unexpected schema; path migration skipped")
        return stats

    has_previous = "previous_metadata_location" in columns
    previous_select = ", previous_metadata_location" if has_previous else ""
    rows = (await connection.execute(text(
        "SELECT catalog_name, table_namespace, table_name, metadata_location "
        f"{previous_select} FROM iceberg_tables FOR UPDATE"
    ))).mappings()
    for row in rows:
        repaired, status = repair_metadata_location(row["metadata_location"], warehouse)
        previous = (
            normalize_warehouse_uri(row["previous_metadata_location"], warehouse)
            if has_previous else None
        )
        if status == "missing":
            stats["missing"] += 1
            logger.warning(
                "Iceberg table %s.%s points to missing metadata and needs reprocessing: %s",
                row["table_namespace"], row["table_name"], repaired,
            )
        if status == "fallback":
            stats["fallback"] += 1
        current_changed = repaired != row["metadata_location"]
        previous_changed = has_previous and previous != row["previous_metadata_location"]
        if current_changed or previous_changed:
            previous_set = ", previous_metadata_location = :previous" if has_previous else ""
            await connection.execute(text(
                "UPDATE iceberg_tables SET metadata_location = :location "
                f"{previous_set} "
                "WHERE catalog_name = :catalog AND table_namespace = :namespace AND table_name = :table"
            ), {
                "location": repaired,
                "previous": previous,
                "catalog": row["catalog_name"],
                "namespace": row["table_namespace"],
                "table": row["table_name"],
            })
            stats["updated"] += 1
    logger.info("Iceberg catalog path migration completed: %s", stats)
    return stats
