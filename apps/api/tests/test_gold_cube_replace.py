"""Cubos Gold: replace atômico e purge de snapshots."""

import pytest

from app.services import iceberg_service as iceberg


def test_replace_staged_cube_requires_gold_namespace():
    with pytest.raises(ValueError, match="gold"):
        iceberg.replace_staged_cube("dest_dm_test", "dest_dm_test_stg_1", namespace="silver")


def test_replace_staged_cube_calls_merge_and_purge(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(
        iceberg,
        "_merge_staged_into_canonical_table",
        lambda *a, **k: calls.append("merge") or 10,
    )
    monkeypatch.setattr(
        iceberg,
        "purge_gold_cube_snapshots",
        lambda *a, **k: calls.append("purge") or True,
    )

    rows = iceberg.replace_staged_cube("dest_dm_test", "dest_dm_test_stg_1", known_row_count=10)
    assert rows == 10
    assert calls == ["merge", "purge"]


def test_replace_staged_cube_snapshot_day_no_purge(monkeypatch):
    captured: dict = {}

    def fake_merge(physical_name, temp, *, namespace, delete_filter, known_row_count=None):
        captured["physical_name"] = physical_name
        captured["filter"] = delete_filter
        return 3

    monkeypatch.setattr(iceberg, "_merge_staged_into_canonical_table", fake_merge)
    monkeypatch.setattr(
        iceberg,
        "purge_gold_cube_snapshots",
        lambda *a, **k: pytest.fail("snapshot day replace must not purge"),
    )

    rows = iceberg.replace_staged_cube_snapshot_day(
        "dest_tags",
        "dest_tags_stg_1",
        snapshot_col="_snapshot_date",
        snapshot_value="2025-09-15",
        known_row_count=3,
    )
    assert rows == 3
    assert captured["physical_name"] == "dest_tags"


def test_swap_staged_table_delegates_to_merge(monkeypatch):
    monkeypatch.setattr(
        iceberg,
        "_merge_staged_into_canonical_table",
        lambda *a, **k: 7,
    )
    assert iceberg.swap_staged_table("t", "t_stg_1", namespace="gold") == 7
