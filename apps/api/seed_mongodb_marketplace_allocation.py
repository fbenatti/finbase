"""Create the first MongoDB Marketplace-to-Cloud allocation rule.

The rule is intentionally configuration-only.  The cross-source executor
consumes ``config_json`` after Azure and MongoDB Silver are both current.
"""

from __future__ import annotations

from sqlalchemy import select

from app.database import SyncSession
from app.models.rules import BusinessRule
# Register the referenced table with SQLAlchemy before flushing BusinessRule.
from app.models.financial_contract import FinancialContract  # noqa: F401
from app.models.source_connection import SourceConnection  # noqa: F401
from app.models.destination import DestinationTable  # noqa: F401


RULE_NAME = "MongoDB Marketplace → Azure Private Offer Q-201755"
CONFIG = {
    "allocation_method": "MARKETPLACE_USD_SHARE",
    "marketplace_connection_code": "mongodb-atlas-pro",
    "cloud_connection_code": "azure-ti",
    "cloud_product_name": "(Private Offer) Telefonica Brasil SA - Sep 2024 - Q-201755",
    "cloud_sku": "DZH318Z0K00S00G3",
    "cloud_resource_group": "rg-mongo-atlas-brsouth-sharedservices",
    "test_period": "202608",
    "marketplace_amount_column": "Amount",
    "target_column": "realizado",
}


def ensure_rule() -> int:
    with SyncSession() as session:
        rule = session.execute(
            select(BusinessRule).where(BusinessRule.name == RULE_NAME)
        ).scalar_one_or_none()
        if rule is None:
            rule = BusinessRule(
                name=RULE_NAME,
                description="Rateia o realizado Cloud do produto Azure proporcionalmente ao consumo USD do MongoDB Marketplace.",
                target_column="realizado",
                rule_type="MARKETPLACE_ALLOCATION",
                config_json=CONFIG,
                priority=15,
                valid_from="202608",
                cloud_scope="atlas",
                financial_effect="MARKETPLACE_ALLOCATION",
                active=True,
                destination_table_id=1,
            )
            session.add(rule)
        else:
            rule.config_json = CONFIG
            rule.target_column = "realizado"
            rule.rule_type = "MARKETPLACE_ALLOCATION"
            rule.financial_effect = "MARKETPLACE_ALLOCATION"
            rule.active = True
        session.commit()
        session.refresh(rule)
        return rule.id


if __name__ == "__main__":
    print(f"MongoDB Marketplace allocation rule id={ensure_rule()}")
