"""Cria o organograma Cloud > Showback > OCI > Projeto > Ambiente e as regras.

O nível Ambiente só existe quando o projeto tem mais de um ambiente nos dados
(hoje só Teledata interno: Produção, POC e Backup). Cada projeto agrupa compartments da OCI (``SubAccountName`` na BILLING_CLOUD).
Uma regra de classificação por projeto e ambiente aponta as linhas para a folha
certa; o que não casar vai para "Não classificado". Pode rodar de novo: a
árvore abaixo de "OCI" e as regras da conexão são recriadas.

Uso:
    python cadastrar_organograma_oci.py [--codigo-oci oci]
"""
from __future__ import annotations

import argparse
import json
import os

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

import app.models.destination  # noqa: F401  (tabelas das FKs das regras)
import app.models.financial_contract  # noqa: F401
from app.models.rules import ClassificationRule, Department
from app.models.source_connection import SourceConnection
from app.services.cost_sources import FOCUS_DESTINATION_ID

CAMINHO_RAIZ = ["Cloud", "Showback", "OCI"]
NAO_CLASSIFICADO = "Não classificado"

# Projeto: {Ambiente: [compartments]}
PROJETOS = {
    "TIVIT": {"Produção": ["tivit", "tivit-br", "tivit-latam", "TIVIT_Brasil_Novo", "versa-tivit"]},
    "Teledata (interno)": {
        "Produção": ["teledata-servicos", "teledata-inovacao", "teledata-interno", "teledata",
                     "backoffice", "MaxSecurityZone"],
        "POC": ["teledata-poc"],
        "Backup": ["office-cwb"],
    },
    "nstech": {"Produção": ["nstech"]},
    "Uniodonto": {"Produção": ["uniodonto"]},
    "TST": {"Produção": ["TST"]},
    "Hemobrás": {"Produção": ["hemobras", "hemobras-acesso-checkpoint"]},
    "Husqvarna": {"Produção": ["husqvarna"]},
    "Hospital N. Sra. de Fátima": {"Produção": ["Hospital_N_Sra_de_Fatima"]},
    "STI": {"Produção": ["STI"]},
    "SS Saúde": {"Produção": ["ss-saude"]},
    "Hiperzoo": {"Produção": ["hiperzoo"]},
    "Lluflex": {"Produção": ["lluflex"]},
    "Inove": {"Produção": ["inove"]},
    "Seconci": {"Produção": ["seconci"]},
    "Eicon": {"Produção": ["Eicon"]},
    "IDD": {"Produção": ["IDD"]},
    "IFPA": {"Produção": ["ifpa"]},
    "Huawei": {"Produção": ["huawei"]},
    # Compartments que só aparecem em meses anteriores.
    "Sicoob": {"Produção": ["sicoob"]},
    "Pessoalize": {"Produção": ["pessoalize"]},
    "Transmarine": {"Produção": ["transmarine"]},
}


def _filhos(session: Session, parent_id: int | None) -> dict[str, Department]:
    return {
        node.name: node
        for node in session.scalars(select(Department).where(Department.parent_id == parent_id))
    }


def _no(session: Session, parent_id: int | None, nome: str, ambiente: str | None = None) -> Department:
    node = _filhos(session, parent_id).get(nome)
    if node is None:
        node = Department(parent_id=parent_id, name=nome)
        session.add(node)
    node.ambiente, node.active = ambiente, True
    session.flush()
    return node


def _apagar_subarvore(session: Session, node: Department) -> None:
    for filho in _filhos(session, node.id).values():
        _apagar_subarvore(session, filho)
        session.delete(filho)
    session.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--codigo-oci", default="oci", help="Identificador da conexão OCI")
    args = parser.parse_args()

    engine = create_engine(os.environ["DATABASE_URL_SYNC"])
    with Session(engine) as session:
        conexao = session.scalar(select(SourceConnection).where(SourceConnection.code == args.codigo_oci))
        if conexao is None:
            raise SystemExit(f"Conexão '{args.codigo_oci}' não encontrada.")

        session.execute(delete(ClassificationRule).where(ClassificationRule.connection_id == conexao.id))
        parent_id = None
        for nome in CAMINHO_RAIZ:
            parent_id = _no(session, parent_id, nome).id
        oci = session.get(Department, parent_id)
        _apagar_subarvore(session, oci)

        regras = []
        for projeto, ambientes in PROJETOS.items():
            no_projeto = _no(session, oci.id, projeto)
            if len(ambientes) == 1:
                # Sem par de ambientes nos dados: o projeto é a folha.
                [compartments] = ambientes.values()
                regras.append((f"OCI › {projeto}", no_projeto, json.dumps(compartments), "exact"))
                continue
            for ambiente, compartments in ambientes.items():
                folha = _no(session, no_projeto.id, ambiente, ambiente)
                regras.append((f"OCI › {projeto} › {ambiente}", folha, json.dumps(compartments), "exact"))
        sem_regra = _no(session, oci.id, NAO_CLASSIFICADO, NAO_CLASSIFICADO)
        # Por último: pega qualquer compartment que nenhuma regra acima cobriu.
        regras.append((f"OCI › {NAO_CLASSIFICADO}", sem_regra, ".*", "regex"))

        for prioridade, (nome, folha, padrao, tipo) in enumerate(regras, start=1):
            session.add(ClassificationRule(
                connection_id=conexao.id, destination_table_id=FOCUS_DESTINATION_ID,
                department_id=folha.id, name=nome, source_field="SubAccountName",
                pattern=padrao, pattern_type=tipo, match_value=folha.name,
                target_field="organograma", priority=prioridade, active=True,
            ))
        session.commit()
        print(f"Organograma {' > '.join(CAMINHO_RAIZ)}: {len(PROJETOS)} projetos + {NAO_CLASSIFICADO}.")
        print(f"Regras de classificação da conexão '{conexao.code}': {len(regras)}.")


if __name__ == "__main__":
    main()
