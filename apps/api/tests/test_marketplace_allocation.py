from decimal import Decimal

from app.services.marketplace_allocation import (
    allocate_marketplace_pool,
    marketplace_allocation_rule_matches,
)


def test_allocation_closes_cloud_pool_after_cent_rounding():
    values = allocate_marketplace_pool(["20", "30", "50"], "1000")
    assert values == [Decimal("200.00"), Decimal("300.00"), Decimal("500.00")]
    assert sum(values) == Decimal("1000.00")


def test_allocation_assigns_rounding_remainder_to_largest_line():
    values = allocate_marketplace_pool(["1", "1", "1"], "100")
    assert values == [Decimal("33.34"), Decimal("33.33"), Decimal("33.33")]
    assert sum(values) == Decimal("100.00")


def test_allocation_rule_matches_product_and_marketplace_connection():
    config = {
        "allocation_method": "MARKETPLACE_USD_SHARE",
        "marketplace_connection_code": "mongodb-atlas-pro",
        "cloud_product_name": "Private Offer Q-201755",
        "cloud_sku": "DZH318Z0K00S00G3",
    }
    assert marketplace_allocation_rule_matches(
        config,
        connection_code="mongodb-atlas-pro",
        cloud_product_name="Private Offer Q-201755",
        cloud_sku="DZH318Z0K00S00G3",
    )
    assert not marketplace_allocation_rule_matches(
        config,
        connection_code="mongodb-atlas-enterprise",
        cloud_product_name="Private Offer Q-201755",
        cloud_sku="DZH318Z0K00S00G3",
    )
