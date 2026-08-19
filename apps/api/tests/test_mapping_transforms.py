"""Tests for Bronze → FOCUS mapping transforms, including dotted JSON paths."""

import json

import pandas as pd

from app.services.mapping_transforms import (
    apply_mapping_transform,
    resolve_json_path,
    split_source_ref,
)


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame({
        "project": [
            json.dumps({"id": "vivo-ti-vicky-hml", "name": "vivo-ti-vicky-hml"}),
            json.dumps({"id": "seguranca-patrimonial-385514", "name": "Seguranca-Patrimonial"}),
        ],
        "cost": ["10.5", "20.0"],
    })


class TestSplitSourceRef:
    def test_top_level_column(self):
        df = _sample_df()
        assert split_source_ref(df, "project") == ("project", "")

    def test_dotted_json_path(self):
        df = _sample_df()
        assert split_source_ref(df, "project.name") == ("project", "name")
        assert split_source_ref(df, "project.id") == ("project", "id")

    def test_unknown_column(self):
        df = _sample_df()
        assert split_source_ref(df, "missing.field") == (None, "")


class TestResolveJsonPath:
    def test_config_only(self):
        assert resolve_json_path({"path": "name"}, "") == "name"

    def test_implicit_only(self):
        assert resolve_json_path(None, "name") == "name"

    def test_combined_paths(self):
        assert resolve_json_path({"path": "value"}, "labels") == "labels.value"


class TestApplyMappingTransform:
    def test_rename_dotted_source_column(self):
        df = _sample_df()
        result = apply_mapping_transform(df, "project.name", "RENAME", None)
        assert result.tolist() == ["vivo-ti-vicky-hml", "Seguranca-Patrimonial"]

    def test_json_path_with_config(self):
        df = _sample_df()
        result = apply_mapping_transform(
            df,
            "project",
            "JSON_PATH",
            {"path": "id"},
        )
        assert result.tolist() == ["vivo-ti-vicky-hml", "seguranca-patrimonial-385514"]

    def test_json_path_dotted_source_column(self):
        df = _sample_df()
        result = apply_mapping_transform(df, "project.id", "JSON_PATH", None)
        assert result.tolist() == ["vivo-ti-vicky-hml", "seguranca-patrimonial-385514"]

    def test_missing_root_returns_nulls(self):
        df = _sample_df()
        result = apply_mapping_transform(df, "missing.field", "RENAME", None)
        assert result.isna().all()

    def test_conditional_fills_only_when_predicate_matches(self):
        df = pd.DataFrame({
            "publishertype": ["Marketplace", "Azure"],
            "productName": ["Acme SaaS", "Virtual Machines"],
        })
        result = apply_mapping_transform(
            df,
            "productName",
            "CONDITIONAL",
            {
                "when_column": "publishertype",
                "when_values": ["Marketplace"],
                "then_type": "RENAME",
            },
        )
        assert result.tolist() == ["Acme SaaS", None]

    def test_conditional_json_path_when_clause(self):
        df = pd.DataFrame({
            "invoice": [
                json.dumps({"publisher_type": "THIRD_PARTY_RESELLER"}),
                json.dumps({"publisher_type": "GOOGLE"}),
            ],
            "sku": [
                json.dumps({"description": "Partner Product"}),
                json.dumps({"description": "Compute"}),
            ],
        })
        result = apply_mapping_transform(
            df,
            "sku",
            "CONDITIONAL",
            {
                "when_column": "invoice",
                "when_path": "publisher_type",
                "when_values": ["THIRD_PARTY_RESELLER"],
                "then_type": "JSON_PATH",
                "then_config": {"path": "description"},
            },
        )
        assert result.tolist() == ["Partner Product", None]
