"""DEV-only rounded validation seed for IBM and GCP financial contracts.

IBM receives R$1,000,000/month split equally across every active department
that inherits EXTERNO-IBM. GCP is registered only against the existing
ID2532-GCP scope; no GCP budget rows are created because GCP is DIRECT_REALIZED.
"""
from __future__ import annotations

import argparse
import os
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.financial_contract import FinancialContract, FinancialContractBudgetScope, FinancialContractConnection
from app.models.planning import FinancialType, PlanningEntry, PlanningVersion
from app.models.rules import Department
from app.models.source_connection import SourceConnection
def inherited_scope(department, by_id):
    chain = []
    current = department
    while current:
        chain.append(current)
        current = by_id.get(current.parent_id) if current.parent_id else None
    chain.reverse()
    return (next((item.id_orcamento for item in reversed(chain) if item.id_orcamento), None), chain[2].name.lower() if len(chain) >= 3 else None)

def department_path(department, by_id):
    path = []
    current = department
    while current:
        path.append(current.name)
        current = by_id.get(current.parent_id) if current.parent_id else None
    return list(reversed(path))


def main() -> None:
    if os.getenv("ENV", "development").lower() not in {"dev", "development", "local", "test"}:
        raise SystemExit("Recusado: seed financeiro só pode rodar em DEV/local/test")
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-year", type=int, default=2026)
    parser.add_argument("--to-year", type=int, default=2027)
    args = parser.parse_args()
    if args.to_year < args.from_year:
        raise SystemExit("to-year deve ser maior ou igual a from-year")
    url = os.environ.get("DATABASE_URL_SYNC", "postgresql://finops:finops@localhost:5433/finops")
    with Session(create_engine(url)) as session:
        departments = {row.id: row for row in session.scalars(select(Department).where(Department.active.is_(True))).all()}
        ibm_projects = [d for d in departments.values() if inherited_scope(d, departments) == ("EXTERNO-IBM", "ibm")]
        if not ibm_projects:
            raise SystemExit("Nenhum projeto ativo herda EXTERNO-IBM/ibm")
        connections = session.scalars(select(SourceConnection)).all()
        ibm = next((c for c in connections if "ibm" in f"{c.code} {c.name} {c.connector_type}".lower()), None)
        gcp = next((c for c in connections if "gcp" in f"{c.code} {c.name} {c.connector_type}".lower()), None)
        if not ibm or not gcp:
            raise SystemExit("Conexões IBM e GCP ativas são obrigatórias")

        def contract(code, name, connection, cloud, scope):
            row = session.scalar(select(FinancialContract).where(FinancialContract.code == code))
            if row:
                return row
            row = FinancialContract(code=code, name=name, description="Contrato criado para validação DEV; não promover sem reconciliação.", currency="BRL", valid_from=f"{args.from_year}01", valid_until=f"{args.to_year}12", active=True)
            session.add(row); session.flush()
            session.add(FinancialContractConnection(contract_id=row.id, connection_id=connection.id))
            session.add(FinancialContractBudgetScope(contract_id=row.id, cloud_scope=cloud, budget_id=scope))
            return row

        contract("DEV-IBM-EXTERNO", "DEV — IBM EXTERNO-IBM", ibm, "ibm", "EXTERNO-IBM")
        contract("DEV-GCP-SHOWBACK", "DEV — GCP ID2532-GCP (direto)", gcp, "gcp", "ID2532-GCP")
        financial_type = session.scalar(select(FinancialType).where(FinancialType.code == "orcamento"))
        if not financial_type:
            raise SystemExit("Tipo financeiro 'orcamento' não encontrado")
        per_project = (Decimal("1000000") / Decimal(len(ibm_projects))).quantize(Decimal("0.01"))
        for year in range(args.from_year, args.to_year + 1):
            duplicate = session.scalar(select(PlanningVersion.id).where(PlanningVersion.financial_type_id == financial_type.id, PlanningVersion.year == year, PlanningVersion.source == "SIMULACAO_CONTRATO_IBM_DEV", PlanningVersion.scope_id_orcamento == "EXTERNO-IBM"))
            if duplicate:
                continue
            version = PlanningVersion(financial_type_id=financial_type.id, year=year, version=1, is_current=True, source="SIMULACAO_CONTRATO_IBM_DEV", title="SIMULAÇÃO — contrato IBM EXTERNO-IBM", description="Validação DEV: R$1.000.000 por mês dividido igualmente entre os projetos que herdam o ID.", event_type="OUTRO", effective_date=date(year, 1, 1), scope_id_orcamento="EXTERNO-IBM", scope_cloud="ibm")
            session.add(version); session.flush()
            for index, project in enumerate(ibm_projects):
                project_value = per_project if index < len(ibm_projects) - 1 else Decimal("1000000") - per_project * Decimal(max(len(ibm_projects) - 1, 0))
                for month in range(1, 13):
                    session.add(PlanningEntry(planning_version_id=version.id, entity_type="department", department_id=project.id, department_levels=department_path(project, departments), month=month, value=float(project_value)))
        session.commit()
        print(f"IBM: {len(ibm_projects)} projetos; R$1.000.000/mês; GCP: escopo ID2532-GCP sem nova carga")


if __name__ == "__main__":
    main()
