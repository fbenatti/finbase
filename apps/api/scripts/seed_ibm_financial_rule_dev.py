"""Create the provider-neutral IBM commitment rule in DEV."""
from __future__ import annotations
import os
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.models.rules import BusinessRule
from app.models.financial_contract import FinancialContract
from app.models.destination import DestinationTable  # noqa: F401

def main() -> None:
    if os.getenv("ENV", "development").lower() not in {"dev", "development", "local", "test"}:
        raise SystemExit("Recusado fora de DEV")
    url = os.getenv("DATABASE_URL_SYNC", "postgresql://finops:finops@localhost:5433/finops")
    with Session(create_engine(url)) as db:
        contract = db.scalar(select(FinancialContract).where(FinancialContract.code == "DEV-IBM-EXTERNO"))
        if not contract:
            raise SystemExit("Contrato DEV-IBM-EXTERNO não encontrado")
        rule = db.scalar(select(BusinessRule).where(BusinessRule.name == "DEV IBM — consumo para compromisso"))
        if not rule:
            rule = BusinessRule(name="DEV IBM — consumo para compromisso", description="Fixture DEV: preserva BilledCost como consumo e liquida pelo pool EXTERNO-IBM.", target_column="consumo_realizado", rule_type="EXPRESSION", config_json={}, expression="BilledCost", financial_effect="BUDGET_COMMITMENT", financial_contract_id=contract.id, destination_table_id=1, priority=1, cloud_scope="ibm", active=True)
            db.add(rule)
        else:
            rule.financial_contract_id = contract.id; rule.financial_effect = "BUDGET_COMMITMENT"; rule.target_column = "consumo_realizado"; rule.cloud_scope = "ibm"; rule.active = True
        db.commit(); print(f"Regra IBM pronta: id={rule.id}, contrato={contract.code}")

if __name__ == "__main__": main()
