"""Extração OCI pela Usage API (sem chamar a OCI)."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from app.services.oci_extractor import (
    USAGE_API_ACCOUNT_GROUP_BY,
    USAGE_API_COMPARTMENT_GROUP_BY,
    USAGE_API_DAILY_GROUP_BY,
    USAGE_API_SKU_GROUP_BY,
    USAGE_API_TAG_GROUP_BY,
    USAGE_API_ZONE_GROUP_BY,
    OciExtractor,
)

VM = "ocid1.instance.oc1.sa-saopaulo-1.vm1"


def _extractor(**config):
    connection = SimpleNamespace(
        id=4, code="oci", connector_type="oci", blob_prefixes=None,
        config_json={"tenancy_ocid": "ocid1.tenancy.oc1..t", "extraction_mode": "usage_api", **config},
    )
    return OciExtractor(connection)


def _day(day):
    return datetime(2026, 9, day, tzinfo=timezone.utc)


def test_month_items_cover_history_and_refresh_recent_months():
    extractor = _extractor(history_months=3)
    existing = {f"usage-api#period={period}": object() for period in ("202608", "202609")}

    items = extractor._usage_api_items(existing, skipped_paths=set())

    assert len(items) == 3
    assert all(item.blob_path.startswith("usage-api#period=") for item in items)
    # Mês corrente é novo; o anterior volta a ser lido; os mais antigos ficam.
    assert [item.status for item in items] == ["NEW", "CHANGED", "UNCHANGED"]


def test_fetch_usage_month_joins_names_and_keeps_report_columns(monkeypatch):
    extractor = _extractor()
    daily = [
        SimpleNamespace(time_usage_started=_day(1), time_usage_ended=_day(2), resource_id=VM,
                        sku_part_number="B1", compartment_id="c1", region="sa-saopaulo-1",
                        currency="BRL", computed_amount=10.5, computed_quantity=24),
        SimpleNamespace(time_usage_started=_day(2), time_usage_ended=_day(3), resource_id=VM,
                        sku_part_number="B1", compartment_id="c1", region="sa-saopaulo-1",
                        currency="BRL", computed_amount=0, computed_quantity=0),
    ]
    by_group = {
        tuple(USAGE_API_DAILY_GROUP_BY): daily,
        tuple(USAGE_API_SKU_GROUP_BY): [SimpleNamespace(sku_part_number="B1", sku_name="E4 OCPU", service="Compute", unit="OCPU Hours")],
        tuple(USAGE_API_COMPARTMENT_GROUP_BY): [SimpleNamespace(compartment_id="c1", compartment_name="producao")],
        # Recurso regional ("NA") e com zona: vale a zona.
        tuple(USAGE_API_ZONE_GROUP_BY): [
            SimpleNamespace(resource_id=VM, ad="NA"),
            SimpleNamespace(resource_id=VM, ad="Tota:SA-SAOPAULO-1-AD-1"),
        ],
        tuple(USAGE_API_TAG_GROUP_BY): [
            SimpleNamespace(resource_id=VM, tags=[SimpleNamespace(namespace="Teledata-Tags", key="CentrodeCusto", value="TI")]),
            SimpleNamespace(resource_id=VM, tags=[SimpleNamespace(namespace=None, key="Template", value="web")]),
            SimpleNamespace(resource_id="outro", tags=[SimpleNamespace(namespace=None, key=None, value=None)]),
        ],
        tuple(USAGE_API_ACCOUNT_GROUP_BY): [SimpleNamespace(subscription_id="29724058", tenant_name="teledata")],
    }
    monkeypatch.setattr(extractor, "_summarized_usages", lambda start, end, granularity, group_by: by_group[tuple(group_by)])

    frame = extractor.fetch_usage_month("202609")

    # A linha sem custo nem quantidade é descartada.
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["lineItem/intervalUsageStart"] == "2026-09-01"
    assert row["product/service"] == "Compute"
    assert row["product/compartmentName"] == "producao"
    assert row["product/description"] == "E4 OCPU"
    assert float(row["cost/myCost"]) == 10.5
    assert row["product/availabilityDomain"] == "SA-SAOPAULO-1-AD-1"
    assert row["product/resourceType"] == "instance"
    assert row["lineItem/tenantName"] == "teledata"
    assert row["cost/subscriptionId"] == "29724058"
    assert float(row["cost/unitPrice"]) == 10.5 / 24
    assert json.loads(row["tags"]) == {"Teledata-Tags.CentrodeCusto": "TI", "Template": "web"}
    # Mesmas colunas que a consolidação diária dos CSV de relatório usa.
    aggregated = OciExtractor.aggregate_df_daily(frame)
    assert pd.to_numeric(aggregated["cost/myCost"]).sum() == 10.5
    # A consolidação mantém as colunas novas.
    for column in ("lineItem/tenantName", "product/resourceType", "product/availabilityDomain", "cost/unitPrice", "tags"):
        assert column in aggregated.columns


def test_daily_query_respects_usage_api_group_by_limit():
    for group_by in (
        USAGE_API_DAILY_GROUP_BY, USAGE_API_SKU_GROUP_BY, USAGE_API_COMPARTMENT_GROUP_BY,
        USAGE_API_ZONE_GROUP_BY, USAGE_API_TAG_GROUP_BY, USAGE_API_ACCOUNT_GROUP_BY,
    ):
        assert len(group_by) <= 4


def test_bucket_mode_is_unchanged_without_extraction_mode():
    assert _extractor(extraction_mode=None).uses_usage_api() is False


def test_subscription_is_left_empty_when_tenancy_has_more_than_one(monkeypatch):
    extractor = _extractor()
    daily = [SimpleNamespace(time_usage_started=_day(1), time_usage_ended=_day(2), resource_id="bucket",
                             sku_part_number="B1", compartment_id="c1", region="sa-saopaulo-1",
                             currency="BRL", computed_amount=1.0, computed_quantity=0)]
    by_group = {
        tuple(USAGE_API_DAILY_GROUP_BY): daily,
        tuple(USAGE_API_SKU_GROUP_BY): [],
        tuple(USAGE_API_COMPARTMENT_GROUP_BY): [],
        tuple(USAGE_API_ZONE_GROUP_BY): [],
        tuple(USAGE_API_TAG_GROUP_BY): [],
        tuple(USAGE_API_ACCOUNT_GROUP_BY): [SimpleNamespace(subscription_id="a", tenant_name="t"),
                                            SimpleNamespace(subscription_id="b", tenant_name="t")],
    }
    monkeypatch.setattr(extractor, "_summarized_usages", lambda start, end, granularity, group_by: by_group[tuple(group_by)])

    row = extractor.fetch_usage_month("202609").iloc[0]

    assert row["cost/subscriptionId"] is None
    # Sem quantidade não há preço unitário; sem OCID o tipo fica vazio.
    assert row["cost/unitPrice"] is None
    assert row["product/resourceType"] is None
    assert row["tags"] is None
