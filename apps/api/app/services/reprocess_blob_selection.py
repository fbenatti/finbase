"""Bronze blob selection helpers for destination reprocess (no router imports)."""


def blob_modified_is_newer(candidate: object, current: object) -> bool:
    """Compare ingest timestamps without breaking on physical-only blobs (modified=None)."""
    if candidate is None:
        return False
    if current is None:
        return True
    try:
        return candidate > current
    except TypeError:
        return False


def azure_blob_run_id(blob_path: str) -> str:
    parts = str(blob_path or "").split("/")
    return parts[3] if len(parts) > 3 else str(blob_path)


def select_reprocess_blobs(
    candidates: list[tuple[int, str, str, object]],
    *,
    connector_type: str,
    preferred_table: str | None = None,
) -> list[tuple[int, str, str]]:
    """Choose Bronze blobs to reprocess for one connection."""
    if connector_type == "gcp_bigquery":
        by_period: dict[str, list[tuple[int, str, str, object]]] = {}
        for blob_id, period, path, modified in candidates:
            by_period.setdefault(str(period), []).append((blob_id, period, path, modified))
        selected: list[tuple[int, str, str]] = []
        for period, blobs in by_period.items():
            if preferred_table:
                preferred = [b for b in blobs if preferred_table in str(b[2])]
                rest = [b for b in blobs if preferred_table not in str(b[2])]
                picked: list[tuple[int, str, str, object]] = []
                if preferred:
                    picked.append(
                        max(
                            preferred,
                            key=lambda b: (b[3] is not None, b[3] or ""),
                        )
                    )
                for other in rest:
                    picked.append(other)
                selected.extend((blob_id, period, path) for blob_id, period, path, _ in picked)
                continue
            blob_id, period, path, _ = max(blobs, key=lambda b: (b[3] is not None, b[3] or ""))
            selected.append((blob_id, period, path))
        return selected

    if connector_type == "azure_blob":
        latest_run: dict[str, tuple[str, object]] = {}
        for _, period, path, modified in candidates:
            run_id = azure_blob_run_id(path)
            current = latest_run.get(str(period))
            if current is None or blob_modified_is_newer(modified, current[1]):
                latest_run[str(period)] = (run_id, modified)
        selected = []
        for blob_id, period, path, _ in candidates:
            run_id = azure_blob_run_id(path)
            if latest_run.get(str(period), (None, None))[0] == run_id:
                selected.append((blob_id, period, path))
        return selected

    return [(blob_id, period, path) for blob_id, period, path, _ in candidates]
