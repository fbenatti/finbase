"""Gera jornadas orçamentárias simuladas de 2026 por ID de orçamento.

A referência é o orçamento carregado anteriormente, ajustado pelos sinais do
forecast manual de 202601. Cada ID recebe defesa, aprovação, corte, carga,
aporte e ajuste posterior, preservando os snapshots para auditoria.
"""
from __future__ import annotations

import asyncio
import os
from datetime import date
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.models.planning import FinancialType, PlanningEntry, PlanningVersion
from app.models.rules import Department
from app.services.trino_service import execute as trino_execute

URL = os.environ.get("DATABASE_URL_SYNC", "postgresql://finops:finops@localhost:5433/finops")
BUDGET_YEAR = 2026
# O ano da versão/linhas é o exercício que a defesa financia. As decisões
# continuam datadas em 2025 por ``effective_date``.
DEFENSE_YEAR = BUDGET_YEAR

def path(dept: Department, all_depts: dict[int, Department]) -> list[str]:
    names=[]; current: Department | None=dept
    while current: names.append(current.name); current=all_depts.get(current.parent_id) if current.parent_id else None
    return list(reversed(names))

def budget_id(dept: Department, all_depts: dict[int, Department]) -> str | None:
    current: Department | None=dept
    while current:
        if current.id_orcamento: return current.id_orcamento
        current=all_depts.get(current.parent_id) if current.parent_id else None
    return None

def publish(session: Session, typ: FinancialType, year: int, version: int, rows: dict[int,list[float]], depts: dict[int,Department], scope: str, title: str, description: str, event: str, when: date) -> None:
    session.query(PlanningVersion).filter(PlanningVersion.financial_type_id==typ.id, PlanningVersion.year==year, PlanningVersion.scope_id_orcamento==scope, PlanningVersion.is_current.is_(True)).update({PlanningVersion.is_current:False}, synchronize_session=False)
    snap=PlanningVersion(financial_type_id=typ.id, year=year, version=version, is_current=True, source="SIMULACAO_ORCAMENTO_2026", title=title, description=description, event_type=event, effective_date=when, scope_id_orcamento=scope)
    session.add(snap); session.flush()
    for dept_id, values in rows.items():
        for month,value in enumerate(values,1): session.add(PlanningEntry(planning_version_id=snap.id, entity_type="department", department_id=dept_id, department_levels=path(depts[dept_id],depts), month=month, value=round(value,2)))

def clone(rows: dict[int,list[float]], factor=1.0) -> dict[int,list[float]]: return {k:[v*factor for v in values] for k,values in rows.items()}

async def january_forecast_reference(by_path: dict[tuple[str, ...], int]) -> dict[int, list[float]]:
    """Converte o realizado de 202601 em forecast anual de referência.

    É a mesma premissa do download de orçamento simulado: janeiro observado é
    repetido como run-rate inicial, antes das variações deliberadas da defesa.
    """
    rows = await trino_execute("""
      SELECT CAST(hierarquia_1 AS VARCHAR) AS l1, CAST(hierarquia_2 AS VARCHAR) AS l2,
             CAST(hierarquia_3 AS VARCHAR) AS l3, CAST(hierarquia_4 AS VARCHAR) AS l4,
             CAST(hierarquia_5 AS VARCHAR) AS l5, CAST(hierarquia_6 AS VARCHAR) AS l6,
             CAST(hierarquia_7 AS VARCHAR) AS l7,
             SUM(COALESCE(TRY_CAST(realizado AS DOUBLE), 0)) AS value
      FROM iceberg.gold.dest_modelo_focus
      WHERE CAST(_billing_period AS VARCHAR) = '202601' AND (tipo = 'realizado' OR tipo IS NULL)
      GROUP BY 1,2,3,4,5,6,7
    """, timeout_s=120)
    base: dict[int, list[float]] = {}
    for row in rows:
        hierarchy = tuple(str(row.get(f"l{level}") or "").strip() for level in range(1, 8))
        department_id = by_path.get(tuple(item for item in hierarchy if item))
        if department_id:
            base.setdefault(department_id, [0.0] * 12)
            value = float(row.get("value") or 0)
            base[department_id] = [current + value for current in base[department_id]]
    return base

def main() -> None:
  with Session(create_engine(URL)) as s:
    budget=s.scalar(select(FinancialType).where(FinancialType.code=="orcamento")); defense=s.scalar(select(FinancialType).where(FinancialType.code=="defesa-orcamentaria"))
    if not budget or not defense: raise RuntimeError("Tipos financeiros não encontrados")
    depts={d.id:d for d in s.scalars(select(Department).where(Department.active.is_(True))).all()}
    by_path={tuple(path(dept,depts)): dept.id for dept in depts.values()}
    base=asyncio.run(january_forecast_reference(by_path))
    if not base: raise RuntimeError("O forecast de referência 202601 não possui projetos classificados")
    # IDs genéricos compartilhados por ramos (como EXTERNOS) não representam
    # uma linha orçamentária única e não podem compor uma jornada simulada.
    excluded_scopes = {"EXTERNOS"}
    groups: dict[str,dict[int,list[float]]]={}
    for dept_id,values in base.items():
      scope=budget_id(depts[dept_id],depts)
      if scope and scope not in excluded_scopes: groups.setdefault(scope,{})[dept_id]=values
    # Descarta apenas as simulações anteriores, sem tocar em forecast/realizado.
    stale=list(s.scalars(select(PlanningVersion.id).where(PlanningVersion.year.in_([2025,BUDGET_YEAR]),PlanningVersion.financial_type_id.in_([budget.id,defense.id]))).all())
    if stale:
      s.query(PlanningEntry).filter(PlanningEntry.planning_version_id.in_(stale)).delete(synchronize_session=False)
      s.query(PlanningVersion).filter(PlanningVersion.id.in_(stale)).delete(synchronize_session=False)
      s.flush()
    dv=bv=0
    for index,(scope,scope_base) in enumerate(sorted(groups.items())):
      # Datas variadas por ID, preservando a sequência natural da governança.
      # A fórmula é determinística para a simulação poder ser reproduzida.
      defense_date=date(2025,9,5+(index*7)%20)
      cut_date=date(2025,10,3+(index*9)%20)
      second_cut_date=date(2025,11,4+(index*11)%20)
      approval_date=date(2025,12,3+(index*13)%20)
      load_date=date(2026,5+(index%2), 2+(index*5)%20)
      aporte_date=date(2026,7+(index%3), 4+(index*13)%20)
      review_date=date(2026,10+(index%2), 3+(index*17)%20)
      requested=clone(scope_base,1.08+(index%4)*.04)
      cut=clone(requested,.88-(index%3)*.02)
      reprioritized=clone(cut,.94-(index%2)*.02)
      approved=clone(reprioritized)
      dv+=1; publish(s,defense,DEFENSE_YEAR,dv,requested,depts,scope,f"Defesa solicitada — {scope}",f"Submissão da defesa para o exercício de 2026 no escopo {scope}, com continuidade dos serviços e iniciativas priorizadas.","DEFESA",defense_date)
      dv+=1; publish(s,defense,DEFENSE_YEAR,dv,cut,depts,scope,f"Primeiro corte da defesa — {scope}",f"Corte de priorização aplicado à defesa do escopo {scope} antes da avaliação de aprovação.","CORTE",cut_date)
      dv+=1; publish(s,defense,DEFENSE_YEAR,dv,reprioritized,depts,scope,f"Repriorização da defesa — {scope}",f"Novo corte e repriorização de escopo para adequar a defesa de {scope} ao teto disponível.","CORTE",second_cut_date)
      dv+=1; publish(s,defense,DEFENSE_YEAR,dv,approved,depts,scope,f"Defesa aprovada — {scope}",f"Aprovação final da defesa do escopo {scope}, já incorporando os cortes e a repriorização realizados.","APROVACAO",approval_date)
      bv+=1; publish(s,budget,BUDGET_YEAR,bv,cut,depts,scope,f"Carga inicial do orçamento 2026 — {scope}",f"Carga oficial do orçamento de 2026 para {scope}, proveniente da defesa aprovada no exercício anterior.","CARGA_INICIAL",load_date)
      apported=clone(cut); target=next(iter(apported)); apported[target][7]+=40_000*(index+1)
      bv+=1; publish(s,budget,BUDGET_YEAR,bv,apported,depts,scope,f"Aporte de capacidade — {scope}",f"Aporte no orçamento vigente para atender necessidade de capacidade do escopo {scope}.","AJUSTE",aporte_date)
      revised=clone(apported); revised[target][9]-=20_000*(index+1)
      bv+=1; publish(s,budget,BUDGET_YEAR,bv,revised,depts,scope,f"Revisão de consumo — {scope}",f"Ajuste redutor no orçamento vigente do escopo {scope}, após revisão da execução no exercício de 2026.","CORTE",review_date)
    s.commit(); print(f"Criadas jornadas de 2026 para {len(groups)} IDs de orçamento ({dv} defesas e {bv} versões de orçamento).")

if __name__=="__main__": main()
