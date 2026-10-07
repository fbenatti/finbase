from app.services.azure_price_fallback import load_azure_price_fallbacks


def test_price_sheet_fallback_uses_meter_when_product_ids_differ():
    meter = "44715159-dd43-51a6-8271-15b49499f83c"
    account = "9c326feb-5307-50e2-1fd1-4de7454d10aa"

    def execute(sql, *, timeout_s):
        if "raw_azure_price_reference" in sql:
            return []
        if "WITH observed AS" in sql:
            return [{
                "meter": meter,
                "account": account,
                "currency": "usd",
                "unit": "1 hour",
                "product": "dzh318z0kcz712rd",
                "region": "br south",
            }]
        if "raw_azure_pricesheet" in sql:
            assert f"'{meter}'" in sql
            return [{
                "meterid": meter,
                "billingaccountid": account,
                "currency": "USD",
                "unitofmeasure": "1 Hour",
                "productid": "DZH318Z0KCZ7",
                "meterregion": "BR South",
                "unit_price": 0.2002,
            }]
        raise AssertionError(f"Unexpected query: {sql}")

    _, prices, counts = load_azure_price_fallbacks(
        execute,
        connection_code="azure-ti",
        billing_period="202608",
        active_snapshot_id="snapshot-202610",
    )

    key = ("azure-ti", meter, account, "usd", "1 hour", "dzh318z0kcz712rd", "br south")
    assert counts["missing_reference_keys"] == 1
    assert counts["price_sheet_candidates"] == 1
    assert prices[key] == {"unit_price": 0.2002, "unit_of_measure": "1 Hour"}
