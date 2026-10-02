from app.routers.azure_mca_commitment import _query_payload, _query_windows, _requested_months


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
