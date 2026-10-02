import pytest
from fastapi import HTTPException

from app.routers.azure_mca_commitment import (
    _configured_accounts,
    _normalize_account_id,
    _profile_display_name,
    _profile_name_map,
    _query_payload,
    _query_windows,
    _requested_months,
)


def test_query_windows_split_at_twelve_calendar_months():
    assert _query_windows("202504", "202609") == [
        ("202504", "202603"),
        ("202604", "202609"),
    ]


def test_requested_months_are_inclusive_across_year_boundary():
    assert _requested_months("202511", "202602") == [
        "202511", "202512", "202601", "202602",
    ]


def test_query_payload_groups_invoice_and_uses_pre_tax_usd():
    payload = _query_payload("202604", "202609")

    assert payload["timePeriod"] == {
        "from": "2026-04-01T00:00:00Z",
        "to": "2026-09-30T23:59:59Z",
    }
    assert payload["dataset"]["granularity"] == "Monthly"
    assert payload["dataset"]["aggregation"]["preTaxCostUSD"]["name"] == "PreTaxCostUSD"
    assert payload["dataset"]["grouping"] == [
        {"type": "Dimension", "name": "BillingProfileId"},
        {"type": "Dimension", "name": "InvoiceId"},
    ]


def test_billing_accounts_are_configured_and_normalized_dynamically():
    first = "9c326feb-5307-50e2-1fd1-4de7454d10aa:afbd45cf-3f8c-4df7-b57f-8e12efd1659b_2019-05-31"
    second = "4b0cc15a-47e6-5080-e74c-700f0254380b:e3ebca84-bf14-4ce3-863a-1c50b6ef6a83_2019-05-31"

    assert _normalize_account_id(f"/providers/Microsoft.Billing/billingAccounts/{second}") == second
    assert _configured_accounts({"billing_account_ids": [first, second, first]}) == [first, second]
    assert _configured_accounts({"billing_account_id": first}) == [first]


def test_invalid_billing_account_is_rejected():
    with pytest.raises(HTTPException) as error:
        _normalize_account_id("not-an-account")
    assert error.value.status_code == 422


def test_profile_display_names_are_indexed_case_insensitively():
    profiles = [
        {"name": "HPDD-DXDZ-BG7-PGB", "properties": {"displayName": "Telefonica Brasil"}},
        {"name": "missing-name", "properties": {}},
    ]
    assert _profile_name_map(profiles) == {"hpdd-dxdz-bg7-pgb": "Telefonica Brasil"}
    assert _profile_display_name({"properties": {"displayName": "Historico MCA"}}) == "Historico MCA"
    assert _profile_display_name({"properties": {}}) is None
