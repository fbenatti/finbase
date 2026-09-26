from datetime import datetime, timezone

from app.services.reprocess_blob_selection import blob_modified_is_newer, select_reprocess_blobs


def test_blob_modified_is_newer_handles_none_vs_datetime():
    ts = datetime(2026, 1, 15, tzinfo=timezone.utc)
    assert blob_modified_is_newer(None, ts) is False
    assert blob_modified_is_newer(ts, None) is True
    assert blob_modified_is_newer(ts, ts.replace(day=1)) is True


def test_azure_select_does_not_crash_on_physical_blob_without_modified():
    newer = datetime(2026, 2, 1, tzinfo=timezone.utc)
    candidates = [
        (1, "202512", "reports/run-a/part_0.csv", newer),
        (-1, "202512", "reports/run-b/part_0.csv", None),
    ]
    selected = select_reprocess_blobs(candidates, connector_type="azure_blob")
    paths = {path for _, _, path in selected}
    assert paths  # must not raise TypeError on None modified
    assert "reports/run-a/part_0.csv" in paths
