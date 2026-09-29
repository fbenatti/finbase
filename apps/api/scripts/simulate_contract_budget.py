"""Publish a clearly labelled DEV-only A=100/B=900 budget-pool fixture.

Usage (from apps/api):
  python scripts/simulate_contract_budget.py --year 2026 --month 1 \
    --cloud oci --budget-id ID2534-OCI --project-a 123 --project-b 456
"""
from __future__ import annotations

import argparse
import os
from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.planning import FinancialType, PlanningEntry, PlanningVersion
from app.models.rules import Department


def inherited_scope(department: Department, by_id: dict[int, Department]) -> tuple[str | None, str | None]:
    chain = []
    current = department
    while current:
        chain.append(current)
        current = by_id.get(current.parent_id) if current.parent_id else None
    chain.reverse()
    budget_id = next((item.id_orcamento for item in reversed(chain) if item.id_orcamento), None)
    cloud = chain[2].name.lower() if len(chain) >= 3 else None
    return budget_id, cloud


def department_path(department: Department, by_id: dict[int, Department]) -> list[str]:
    path = []
    current = department
    while current:
        path.append(current.name)
        current = by_id.get(current.parent_id) if current.parent_id else None
    return list(reversed(path))


def main() -> None:
    if os.getenv("ENV", "development").lower() not in {"dev", "development", "local", "test"}:
        raise SystemExit("Recusado: a simulação de compromisso só pode rodar em DEV/local/test")
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, choices=range(1, 13), required=True)
    parser.add_argument("--cloud", required=True)
    parser.add_argument("--budget-id", required=True)
    parser.add_argument("--project-a", type=int, required=True)
    parser.add_argument("--project-b", type=int, required=True)
    args = parser.parse_args()

    url = os.environ.get("DATABASE_URL_SYNC", "postgresql://finops:finops@localhost:5434/finops")
    with Session(create_engine(url)) as session:
        departments = {row.id: row for row in session.scalars(select(Department).where(Department.active.is_(True))).all()}
        first, second = departments.get(args.project_a), departments.get(args.project_b)
        if not first or not second:
            raise SystemExit("Os dois projetos precisam existir e estar ativos")
        scope_a, scope_b = inherited_scope(first, departments), inherited_scope(second, departments)
        expected = (args.budget_id.strip(), args.cloud.strip().lower())
        if scope_a != expected or scope_b != expected:
            raise SystemExit(f"Os projetos precisam herdar exatamente {expected}; encontrados {scope_a} e {scope_b}")

        financial_type = session.scalar(select(FinancialType).where(FinancialType.code == "orcamento"))
        if not financial_type:
            raise SystemExit("Tipo financeiro 'orcamento' não encontrado")
        duplicate = session.scalar(select(PlanningVersion.id).where(
            PlanningVersion.financial_type_id == financial_type.id,
            PlanningVersion.year == args.year,
            PlanningVersion.source == "SIMULACAO_POOL_ID_ORCAMENTO",
            PlanningVersion.scope_id_orcamento == args.budget_id,
            PlanningVersion.scope_cloud == args.cloud.lower(),
        ))
        if duplicate:
            print(f"Simulação já publicada: planning_version={duplicate}")
            return

        next_version = int(session.scalar(select(PlanningVersion.version).where(PlanningVersion.financial_type_id == financial_type.id, PlanningVersion.year == args.year).order_by(PlanningVersion.version.desc()).limit(1)) or 0) + 1
        version = PlanningVersion(
            financial_type_id=financial_type.id,
            year=args.year,
            version=next_version,
            is_current=True,
            source="SIMULACAO_POOL_ID_ORCAMENTO",
            title="SIMULAÇÃO — pool de compromisso por ID Orçamento",
            description="Dados de validação: A=100 e B=900 no mesmo ID; o compromisso é consolidado pelo pool.",
            event_type="OUTRO",
            effective_date=date(args.year, args.month, 1),
            scope_id_orcamento=args.budget_id,
            scope_cloud=args.cloud.lower(),
        )
        session.add(version)
        session.flush()
        for department, value in ((first, 100), (second, 900)):
            session.add(PlanningEntry(
                planning_version_id=version.id,
                entity_type="department",
                department_id=department.id,
                department_levels=department_path(department, departments),
                month=args.month,
                value=value,
            ))
        session.commit()
        print(f"Simulação publicada: planning_version={version.id}; pool=1000.00")


if __name__ == "__main__":
    main()
