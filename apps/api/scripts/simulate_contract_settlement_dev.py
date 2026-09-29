"""Publish one DEV IBM settlement fixture: 900k consumption against 1M pool."""
from __future__ import annotations
import os
from decimal import Decimal
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.models.financial_contract import FinancialContract, ContractSettlementRun, ContractSettlementMonth
from app.models.destination import DestinationTable  # noqa: F401
from app.services.contract_settlement import settle_pool

def main() -> None:
    if os.getenv("ENV", "development").lower() not in {"dev", "development", "local", "test"}:
        raise SystemExit("Recusado fora de DEV")
    url = os.getenv("DATABASE_URL_SYNC", "postgresql://finops:finops@localhost:5433/finops")
    with Session(create_engine(url)) as db:
        contract = db.scalar(select(FinancialContract).where(FinancialContract.code == "DEV-IBM-EXTERNO"))
        if not contract:
            raise SystemExit("Contrato DEV-IBM-EXTERNO não encontrado; execute o seed de contratos primeiro")
        existing = db.scalar(select(ContractSettlementMonth).where(ContractSettlementMonth.contract_id == contract.id, ContractSettlementMonth.period == "202601"))
        if existing:
            print(f"Fixture já publicada: settlement_month={existing.id}")
            return
        result = settle_pool("1000000", "900000")
        run = ContractSettlementRun(contract_id=contract.id, period_from="202601", period_until="202601", status="SUCCESS", source="SIMULACAO_CONSUMO_IBM_DEV")
        db.add(run); db.flush()
        db.add(ContractSettlementMonth(run_id=run.id, contract_id=contract.id, cloud_scope="ibm", budget_id="EXTERNO-IBM", period="202601", commitment=result.commitment, billed_cost=Decimal("900000"), consumption=result.consumption, opening_credit=result.opening_credit, credit_generated=result.credit_generated, gross_overage=result.gross_overage, credit_used=result.credit_used, uncovered_overage=result.uncovered_overage, closing_credit=result.closing_credit, realized=result.realized, allocation_factor=result.realized / result.consumption))
        db.commit(); print("Fixture IBM publicada: consumo=900000, crédito=100000, realizado=1000000")

if __name__ == "__main__": main()
