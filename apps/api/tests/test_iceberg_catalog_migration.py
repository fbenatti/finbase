import json
from pathlib import Path

from app.services.iceberg_catalog_migration import (
    latest_valid_metadata,
    normalize_warehouse_uri,
    repair_metadata_location,
)
from app.services import iceberg_service


def _metadata(path: Path, table_uuid: str = "table-1") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format-version": 2, "table-uuid": table_uuid}), encoding="utf-8")


def test_normalizes_legacy_warehouse_uri(tmp_path):
    warehouse = tmp_path / "iceberg"
    legacy = (
        "file:///Users/someone/project/apps/data/iceberg/"
        "gold.db/costs/metadata/00001-old.metadata.json"
    )

    assert normalize_warehouse_uri(legacy, warehouse) == (
        warehouse / "gold.db/costs/metadata/00001-old.metadata.json"
    ).resolve().as_uri()


def test_missing_location_falls_back_to_latest_valid_snapshot(tmp_path):
    warehouse = tmp_path / "iceberg"
    metadata_dir = warehouse / "gold.db/costs/metadata"
    _metadata(metadata_dir / "00001-first.metadata.json")
    _metadata(metadata_dir / "00003-latest.metadata.json")
    (metadata_dir / "00004-corrupt.metadata.json").write_text("{", encoding="utf-8")

    repaired, status = repair_metadata_location(
        "file:///old/root/iceberg/gold.db/costs/metadata/00002-missing.metadata.json",
        warehouse,
    )

    assert status == "fallback"
    assert repaired == (metadata_dir / "00003-latest.metadata.json").resolve().as_uri()


def test_missing_location_without_valid_candidate_is_marked_missing(tmp_path):
    warehouse = tmp_path / "iceberg"
    expected = warehouse / "silver.db/costs/metadata/00001-missing.metadata.json"

    repaired, status = repair_metadata_location(
        "file:///opt/app/iceberg/silver.db/costs/metadata/00001-missing.metadata.json",
        warehouse,
    )

    assert status == "missing"
    assert repaired == expected.resolve().as_uri()


def test_latest_snapshot_uses_numeric_version(tmp_path):
    metadata_dir = tmp_path / "metadata"
    _metadata(metadata_dir / "00009-a.metadata.json")
    _metadata(metadata_dir / "00010-b.metadata.json")

    assert latest_valid_metadata(metadata_dir).name == "00010-b.metadata.json"


def test_pyiceberg_catalog_uses_configured_file_uri(tmp_path, monkeypatch):
    warehouse = tmp_path / "custom warehouse"
    captured = {}

    def fake_load_catalog(name, **kwargs):
        captured.update(name=name, **kwargs)
        return object()

    monkeypatch.setattr(iceberg_service, "_LOCAL_WAREHOUSE", warehouse)
    monkeypatch.setattr(iceberg_service, "load_catalog", fake_load_catalog)

    iceberg_service._create_sql_catalog()

    assert captured["name"] == "sql"
    assert captured["warehouse"] == warehouse.resolve().as_uri()
