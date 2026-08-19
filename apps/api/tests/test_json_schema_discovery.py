"""Tests for nested JSON schema discovery."""

import json

import pandas as pd

from app.services.json_schema_discovery import discover_nested_columns


def test_discover_nested_project_paths():
    df = pd.DataFrame({
        "project": [
            json.dumps({"id": "proj-a", "name": "Project A", "labels": [{"key": "env", "value": "prd"}]}),
        ],
        "cost": ["10"],
    })
    nested = discover_nested_columns(df, ["project", "cost"])
    names = {col["name"] for col in nested}
    assert "project.id" in names
    assert "project.name" in names
    assert all(col["nested"] for col in nested)
    assert all(col["parent"] == "project" for col in nested if col["name"].startswith("project."))
