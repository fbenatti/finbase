import json
from pathlib import Path

from app.services.iceberg_catalog_migration import (
    CANONICAL_WAREHOUSE_URI,
    latest_valid_metadata,
    normalize_warehouse_uri,
    physical_warehouse_path,
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
        f"{CANONICAL_WAREHOUSE_URI}/gold.db/costs/metadata/00001-old.metadata.json"
    )


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
    assert repaired == (
        f"{CANONICAL_WAREHOUSE_URI}/gold.db/costs/metadata/00003-latest.metadata.json"
    )


def test_missing_stg_location_falls_back_to_published_table(tmp_path):
    warehouse = tmp_path / "iceberg"
    published = warehouse / "gold.db/dest_modelo_focus/metadata"
    _metadata(published / "00040-live.metadata.json")

    repaired, status = repair_metadata_location(
        "file:///data/iceberg/gold.db/dest_modelo_focus_stg_1786997602245996000/"
        "metadata/01193-gone.metadata.json",
        warehouse,
    )

    assert status == "fallback"
    assert repaired == (
        f"{CANONICAL_WAREHOUSE_URI}/gold.db/dest_modelo_focus/metadata/00040-live.metadata.json"
    )


def test_missing_location_without_valid_candidate_is_marked_missing(tmp_path):
    warehouse = tmp_path / "iceberg"

    repaired, status = repair_metadata_location(
        "file:///opt/app/iceberg/silver.db/costs/metadata/00001-missing.metadata.json",
        warehouse,
    )

    assert status == "missing"
    assert repaired == (
        f"{CANONICAL_WAREHOUSE_URI}/silver.db/costs/metadata/00001-missing.metadata.json"
    )


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
    assert captured["warehouse"] == CANONICAL_WAREHOUSE_URI


def test_pyiceberg_normalizes_container_data_path(tmp_path, monkeypatch):
    warehouse = tmp_path / "iceberg"
    snap = warehouse / "bronze.db/raw_github_license/metadata/snap-1.avro"
    snap.parent.mkdir(parents=True)
    snap.write_bytes(b"avro")
    monkeypatch.setattr(iceberg_service, "_LOCAL_WAREHOUSE", warehouse)

    remapped = iceberg_service._normalize_pyiceberg_location(
        "/data/iceberg/bronze.db/raw_github_license/metadata/snap-1.avro"
    )
    assert remapped == str(snap)

    remapped_uri = iceberg_service._normalize_pyiceberg_location(
        "file:///data/iceberg/bronze.db/raw_github_license/metadata/snap-1.avro"
    )
    assert remapped_uri == str(snap)

    parquet = (
        "file:///data/iceberg/bronze.db/raw_github_token/data/"
        "_billing_period=202601/00000-0-ebfa9b8d-a7c5-450e-9f91-008c8b10b07d.parquet"
    )
    assert physical_warehouse_path(parquet, warehouse) == warehouse / (
        "bronze.db/raw_github_token/data/"
        "_billing_period=202601/00000-0-ebfa9b8d-a7c5-450e-9f91-008c8b10b07d.parquet"
    )


def test_pyiceberg_scan_parse_remaps_data_iceberg(tmp_path, monkeypatch):
    warehouse = tmp_path / "iceberg"
    data_file = warehouse / (
        "bronze.db/raw_github_token/data/_billing_period=202510/file.parquet"
    )
    monkeypatch.setattr(iceberg_service, "_LOCAL_WAREHOUSE", warehouse)

    from pyiceberg.io.pyarrow import PyArrowFileIO

    _scheme, _netloc, path = PyArrowFileIO.parse_location(
        "file:///data/iceberg/bronze.db/raw_github_token/data/"
        "_billing_period=202510/file.parquet"
    )
    assert path == str(data_file)
