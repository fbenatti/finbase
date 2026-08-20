"""Tests for MongoDB Atlas extractor (scan, fetch_invoice and bronze integration)."""

import pandas as pd
from unittest.mock import MagicMock, patch

from app.models.source_connection import SourceConnection
from app.services.mongodb_extractor import MongoAtlasExtractor, derive_billing_period_from_invoice


def test_derive_billing_period_from_invoice():
    assert derive_billing_period_from_invoice("2026-03-01T00:00:00Z") == "202603"
    assert derive_billing_period_from_invoice("2025-12-15T10:30:00+00:00") == "202512"
    assert derive_billing_period_from_invoice(None) is None
    assert derive_billing_period_from_invoice("invalid-date") is None


def test_mongo_atlas_extractor_init():
    conn = SourceConnection(
        id=1,
        code="mongodb-atlas-pro",
        name="MongoDB Atlas Pro",
        connector_type="mongodb_atlas",
        config_json={
            "base_url": "https://cloud.mongodb.com/api/atlas/v1.0",
            "public_key": "pub_key_123",
            "private_key": "priv_key_456",
            "group_id": "group_org_789",
            "contract_type": "pro",
        },
    )
    extractor = MongoAtlasExtractor(conn)
    assert extractor.public_key == "pub_key_123"
    assert extractor.private_key == "priv_key_456"
    assert extractor.org_id == "group_org_789"
    assert extractor.billing_base_url == "https://cloud.mongodb.com/api/atlas/v2"


def test_mongo_atlas_extractor_fetch_invoice_flattening():
    conn = SourceConnection(
        id=1,
        code="mongodb-atlas-pro",
        name="MongoDB Atlas Pro",
        connector_type="mongodb_atlas",
        config_json={
            "public_key": "pub_key",
            "private_key": "priv_key",
            "group_id": "org_123",
        },
    )
    extractor = MongoAtlasExtractor(conn)

    mock_invoice_response = {
        "id": "inv_999",
        "startDate": "2026-01-01T00:00:00Z",
        "endDate": "2026-01-31T23:59:59Z",
        "totalPriceCents": 15000,
        "lineItems": [
            {
                "sku": "M10_CLUSTER",
                "clusterName": "prod-cluster",
                "quantity": 744,
                "unitPrice": 0.20,
                "totalPriceCents": 14880,
                "startDate": "2026-01-01T00:00:00Z",
            },
            {
                "sku": "PITR_BACKUP",
                "clusterName": "prod-cluster",
                "quantity": 1,
                "unitPrice": 1.20,
                "totalPriceCents": 120,
                "startDate": "2026-01-01T00:00:00Z",
            },
        ],
    }

    with patch.object(extractor, "_get", return_value=mock_invoice_response):
        df, period, fields = extractor.fetch_invoice("inv_999")

    assert period == "202601"
    assert fields["invoiceId"] == "inv_999"
    assert len(df) == 2
    assert "sku" in df.columns
    assert "clusterName" in df.columns
    assert "invoiceId" in df.columns
    assert df.loc[0, "totalPriceCents"] == 14880
