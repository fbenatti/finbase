"""Create the MongoDB Enterprise Marketplace-to-Cloud allocation rule."""

from sqlalchemy import select

from app.database import SyncSession
from app.models.rules import BusinessRule
from app.models.financial_contract import FinancialContract  # noqa: F401
from app.models.source_connection import SourceConnection  # noqa: F401
from app.models.destination import DestinationTable  # noqa: F401

RULE_NAME = "MongoDB Enterprise Marketplace → Azure Private Offer Q-213513"
CONFIG = {
    "allocation_method": "MARKETPLACE_USD_SHARE",
    "marketplace_connection_code": "mongodb-atlas-enterprise",
    "cloud_connection_code": "azure-ti",
    "cloud_product_name": "(Private Offer) Telefonica Brasil SA - Jan 2025 - Q-213513",
    "cloud_sku": "DZH318Z0K00S00NW",
    "cloud_resource_group": "rg-mongo-atlas-brsouth-sharedservices",
    "target_column": "realizado",
}


def ensure_rule() -> int:
    with SyncSession() as session:
        rule = session.execute(select(BusinessRule).where(BusinessRule.name == RULE_NAME)).scalar_one_or_none()
        if rule is None:
            rule = BusinessRule(
                name=RULE_NAME,
                description="Rateia todo o realizado Cloud do produto Enterprise proporcionalmente ao consumo USD do MongoDB Enterprise.",
                target_column="realizado", rule_type="MARKETPLACE_ALLOCATION", config_json=CONFIG,
                priority=15, valid_from="202601", cloud_scope="atlas",
                financial_effect="MARKETPLACE_ALLOCATION", active=True, destination_table_id=1,
            )
            session.add(rule)
        else:
            rule.config_json = CONFIG
            rule.financial_effect = "MARKETPLACE_ALLOCATION"
            rule.rule_type = "MARKETPLACE_ALLOCATION"
            rule.active = True
        session.commit()
        session.refresh(rule)
        return rule.id


if __name__ == "__main__":
    print(f"MongoDB Enterprise allocation rule id={ensure_rule()}")
