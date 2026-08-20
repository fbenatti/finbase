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

    def test_case_insensitive_column_lookup(self):
        df = pd.DataFrame({"productname": ["A"], "publishertype": ["Marketplace"]})
        assert split_source_ref(df, "productName") == ("productname", "")

    def test_bracket_array_path(self):
        df = pd.DataFrame({"credits": ["[]"]})
        assert split_source_ref(df, "credits[0].type") == ("credits", "[0].type")
        assert split_source_ref(df, "credits[0]") == ("credits", "[0]")

    def test_bracket_only_source_column(self):
        df = pd.DataFrame({"credits": ["[]"]})
        assert split_source_ref(df, "credits[0]") == ("credits", "[0]")


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
            "productname": ["Acme SaaS", "Virtual Machines"],
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

    def test_same_source_maps_to_multiple_destinations(self):
        from types import SimpleNamespace

        from app.services.mapping_transforms import apply_mappings

        df = pd.DataFrame({"cost/myCost": ["100.0", "200.0"]})
        mappings = [
            SimpleNamespace(
                id=1,
                source_column="cost/myCost",
                destination_column="BilledCost",
                transform_type="RENAME",
                transform_config=None,
                priority=1,
            ),
            SimpleNamespace(
                id=2,
                source_column="cost/myCost",
                destination_column="EffectiveCost",
                transform_type="RENAME",
                transform_config=None,
                priority=2,
            ),
        ]
        result = apply_mappings(df, mappings)
        assert result["BilledCost"].tolist() == ["100.0", "200.0"]
        assert result["EffectiveCost"].tolist() == ["100.0", "200.0"]

    def test_oci_marketplace_billing_model(self):
        df = pd.DataFrame({
            "product/service": ["MARKETPLACE", "COMPUTE"],
            "cost/billingUnitReadable": ["ONE PRIVATE OFFER", "HOURS"],
        })
        result = apply_mapping_transform(
            df,
            "cost/billingUnitReadable",
            "CONDITIONAL",
            {
                "when_column": "product/service",
                "when_values": ["MARKETPLACE"],
                "then_type": "VALUE_MAP",
                "then_config": {"map": {"ONE PRIVATE OFFER": "ONESHOT"}, "default": "RECURRING"},
            },
        )
        assert result.tolist() == ["ONESHOT", None]

    def test_oci_tagged_compute_marketplace_billing_model(self):
        df = pd.DataFrame({
            "product/service": ["COMPUTE", "COMPUTE"],
            "tags/orcl-marketplace.listing-id": ["ocid1.listing.abc", None],
            "cost/billingUnitReadable": ["HOURS", "HOURS"],
        })
        result = apply_mapping_transform(
            df,
            "cost/billingUnitReadable",
            "CONDITIONAL",
            {
                "when_column": "tags/orcl-marketplace.listing-id",
                "when_op": "not_empty",
                "then_type": "CONST",
                "then_config": {"value": "USAGE"},
            },
        )
        assert result.tolist() == ["USAGE", None]

    def test_gcp_marketplace_offer_unit(self):
        df = pd.DataFrame({
            "invoice": [
                json.dumps({"publisher_type": "THIRD_PARTY_RESELLER"}),
                json.dumps({"publisher_type": "GOOGLE"}),
            ],
            "usage": [
                json.dumps({"unit": "month"}),
                json.dumps({"unit": "hour"}),
            ],
        })
        result = apply_mapping_transform(
            df,
            "usage",
            "CONDITIONAL",
            {
                "when_column": "invoice",
                "when_path": "publisher_type",
                "when_values": ["THIRD_PARTY_RESELLER"],
                "then_type": "JSON_PATH",
                "then_config": {"path": "unit"},
            },
        )
        assert result.tolist() == ["month", None]

    def test_gcp_credits_array_type_path(self):
        df = pd.DataFrame({
            "credits": [
                "[{'name': 'aggregated', 'type': 'CREDIT', 'amount': -0.592816}]",
                "[]",
                None,
            ],
        })
        result = apply_mapping_transform(df, "credits[0].type", "JSON_PATH", None)
        assert result.tolist() == ["CREDIT", None, None]

    def test_gcp_credits_array_type_via_root_and_config(self):
        df = pd.DataFrame({
            "credits": [
                "[{'name': 'aggregated', 'type': 'CREDIT', 'amount': -0.592816}]",
                "[]",
            ],
        })
        result = apply_mapping_transform(
            df,
            "credits",
            "JSON_PATH",
            {"path": "[0].type"},
        )
        assert result.tolist() == ["CREDIT", None]

    def test_gcp_credits_rename_keeps_payload(self):
        df = pd.DataFrame({
            "credits": [
                "[{'name': 'aggregated', 'type': 'CREDIT', 'amount': -0.592816}]",
                "[]",
            ],
        })
        result = apply_mapping_transform(df, "credits", "RENAME", None)
        assert "CREDIT" in str(result.iloc[0])
        assert result.iloc[1] == "[]"

    def test_legacy_saved_mapping_credits_bracket_index(self):
        df = pd.DataFrame({
            "credits": [
                "[{'name': 'aggregated', 'type': 'CREDIT', 'amount': -0.592816}]",
                "[]",
            ],
        })
        result = apply_mapping_transform(
            df,
            "credits[0]",
            "JSON_PATH",
            {"path": "type"},
        )
        assert result.tolist() == ["CREDIT", None]
