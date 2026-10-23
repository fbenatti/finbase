from app.services.azure_price_fallback import load_azure_price_fallbacks, load_azure_observed_prices
from app.services.azure_price_fallback import load_azure_observed_exchange_rates


def test_exchange_rate_fallback_excludes_prior_invoice_dates_and_ambiguous_accounts():
    def execute(sql, **kwargs):
        assert "exchangeratedate" in sql
        assert "'%Y%m') = '202608'" in sql
        assert "lower(trim(chargetype)) = 'usage'" in sql
        return [
            dict(billingaccountid="one", pricingcurrency="USD", billingcurrency="BRL", rate=5.13, rate_count=1),
            dict(billingaccountid="two", pricingcurrency="USD", billingcurrency="BRL", rate=5.20, rate_count=2),
        ]
    assert load_azure_observed_exchange_rates(execute, connection_code="azure-dev", billing_period="202608", blob_paths=["month.csv"]) == {
        ("azure-dev", "one", "usd", "brl"): 5.13,
    }


def test_observed_prices_are_scoped_to_selected_month_and_blobs():
    def execute(sql, *, timeout_s):
        assert "_billing_period = '202602'" in sql
        assert "_blob_path IN ('selected.csv')" in sql
        return [{
            "meterid": "Meter-1", "billingaccountid": "Account-1",
            "pricingcurrency": "USD", "unitofmeasure": "1 Hour",
            "productid": "Product-1", "meterregion": "BR South", "unit_price": .17303,
        }]

    prices = load_azure_observed_prices(execute, connection_code="azure-producao", billing_period="202602", blob_paths=["selected.csv"])
    assert prices == {("azure-producao", "meter-1", "account-1", "usd", "1 hour", "product-1", "br south"): .17303}


def test_conflicting_month_prices_are_not_guessed():
    def execute(sql, *, timeout_s):
        return [{"meterid": "meter-1", "unit_price": price} for price in (1.0, 2.0)]

    assert load_azure_observed_prices(execute, connection_code="azure-producao", billing_period="202602", blob_paths=["selected.csv"]) == {}


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
        connection_code="azure-dev",
        billing_period="202608",
        active_snapshot_id="snapshot-202610",
    )

    key = ("azure-dev", meter, account, "usd", "1 hour", "dzh318z0kcz712rd", "br south")
    assert counts["missing_reference_keys"] == 1
    assert counts["price_sheet_candidates"] == 1
    assert prices[key] == {"unit_price": 0.2002, "unit_of_measure": "1 Hour"}
