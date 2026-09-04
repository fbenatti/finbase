"""Testes unitários de auditoria: sanitização e diff pré-calculado."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.audit_sanitize import (  # noqa: E402
    sanitize_dict, compute_diff, snapshot_from_dict,
)


def test_sanitize_removes_secrets_recursively():
    data = {
        "name": "conn",
        "client_secret": "s3cr3t",
        "config": {"password": "p", "nested": [{"token": "t"}]},
    }
    out = sanitize_dict(data)
    assert out["client_secret"] == "***"
    assert out["config"]["password"] == "***"
    assert out["config"]["nested"][0]["token"] == "***"
    assert out["name"] == "conn"


def test_sanitize_truncates_long_strings():
    out = sanitize_dict({"big": "x" * 20_000})
    assert len(out["big"]) < 10_100
    assert out["big"].endswith("...[truncated]")


def test_compute_diff_two_fields():
    before = {"a": 1, "b": 2, "updated_at": "x"}
    after = {"a": 1, "b": 3, "c": 4, "updated_at": "y"}
    diff = compute_diff(before, after)
    assert diff == [
        {"field": "b", "old": 2, "new": 3},
        {"field": "c", "old": None, "new": 4},
    ]
    assert "updated_at" not in [d["field"] for d in diff]


def test_compute_diff_none_both():
    assert compute_diff(None, None) is None
    assert compute_diff({}, {}) is None


def test_snapshot_from_dict_dates_iso_and_sanitized():
    from datetime import datetime
    snap = snapshot_from_dict({"created_at": datetime(2026, 1, 2, 3, 4, 5), "refresh_token": "rt"})
    assert snap["created_at"].startswith("2026-01-02T03:04:05")
    assert snap["refresh_token"] == "***"


def test_sanitize_config_matches_connections_pattern():
    from app.core.audit_sanitize import SECRET_KEYS
    # O conjunto cobre o padrão de routers/connections.py
    assert "client_secret" in SECRET_KEYS
    assert "private_key" in SECRET_KEYS
    assert "password_hash" in SECRET_KEYS
    assert "access_token" in SECRET_KEYS
