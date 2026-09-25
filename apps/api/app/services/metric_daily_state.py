"""Shared state classification for daily metric worker results."""


def metric_failure_status(error: object) -> str:
    message = str(error).lower()
    if any(marker in message for marker in (
        "workspace not found", "workspace does not exist", "workspacenotfound",
        "table not found", "table does not exist", "retention expired", "data is no longer available",
    )):
        return "SOURCE_UNAVAILABLE"
    return "FAILED"
