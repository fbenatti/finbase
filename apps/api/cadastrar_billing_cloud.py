"""Cadastra o destino BILLING_CLOUD (colunas FOCUS 1.4) e o mapeamento da OCI.

O BILLING_CLOUD substitui o antigo "Modelo Focus": é o livro de custo de todas
as nuvens. As colunas vêm de ``migrations/focus_v1_4_recreate.sql``. O
mapeamento liga as colunas da base de origem da conexão OCI (Usage API) às
colunas FOCUS e cria a regra de negócio do realizado. Pode rodar de novo: atualiza o que já existe.

Uso:
    python cadastrar_billing_cloud.py [--codigo-oci oci]
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.models.destination import DestinationColumn, DestinationTable, SourceDestinationMapping
import app.models.financial_contract  # noqa: F401  (tabela da FK da regra)
from app.models.rules import BusinessRule
from app.models.source_connection import SourceConnection
from app.services.cost_sources import FOCUS_DESTINATION_ID

NOME = "BILLING_CLOUD"
NOME_FISICO = "dest_billing_cloud"
ESPEC_FOCUS = Path(__file__).parent / "migrations" / "focus_v1_4_recreate.sql"
_COLUNA_RE = re.compile(r'^\s+"([A-Za-z]+)"\s+([A-Z]+)', re.M)

# Serviço OCI → ServiceCategory do FOCUS.
CATEGORIAS_OCI = {
    "Compute": "Compute",
    "Functions": "Compute",
    "Secure Desktops": "Compute",
    "Block Storage": "Storage",
    "File Storage": "Storage",
    "Object Storage": "Storage",
    "MySQL": "Databases",
    "API Gateway": "Networking",
    "Load Balancer": "Networking",
    "Key Management": "Security",
    "Oracle Web Application Firewall (WAF)": "Security",
    "Logging": "Management and Governance",
    "Telemetry": "Management and Governance",
    "Notifications": "Integration",
}

# Serviço OCI → ServiceSubcategory do FOCUS, sempre dentro da categoria acima.
SUBCATEGORIAS_OCI = {
    "Compute": "Virtual Machines",
    "Functions": "Serverless Compute",
    "Secure Desktops": "End User Computing",
    "Block Storage": "Block Storage",
    "File Storage": "File Storage",
    "Object Storage": "Object Storage",
    "MySQL": "Relational Databases",
    "API Gateway": "Application Networking",
    "Load Balancer": "Application Networking",
    "Key Management": "Secret Management",
    "Oracle Web Application Firewall (WAF)": "Network Security",
    "Logging": "Observability",
    "Telemetry": "Observability",
    "Notifications": "Messaging",
}

# Regra de negócio que preenche o realizado (custo usado nos painéis).
REGRA_REALIZADO = {
    "name": "OCI - realizado pelo custo efetivo",
    "target_column": "realizado",
    "rule_type": "EXPRESSION",
    "expression": "EffectiveCost",
    "financial_effect": "DIRECT_REALIZED",
    "config_json": {},
    "priority": 100,
}

# coluna FOCUS: (coluna da origem OCI, transformação, configuração)
MAPEAMENTO_OCI = {
    "BillingPeriodStart": ("_billing_period", "MONTH_START", None),
    "BillingPeriodEnd": ("_billing_period", "MONTH_END", None),
    "ChargePeriodStart": ("lineItem/intervalUsageStart", "RENAME", None),
    "ChargePeriodEnd": ("lineItem/intervalUsageEnd", "RENAME", None),
    "BillingAccountId": ("lineItem/tenantId", "RENAME", None),
    "BillingAccountName": ("lineItem/tenantName", "RENAME", None),
    "BillingAccountType": ("lineItem/tenantId", "CONST", {"value": "Tenancy"}),
    "SubAccountId": ("product/compartmentId", "RENAME", None),
    "SubAccountName": ("product/compartmentName", "RENAME", None),
    "SubAccountType": ("product/compartmentId", "CONST", {"value": "Compartment"}),
    "ServiceName": ("product/service", "RENAME", None),
    "ServiceCategory": ("product/service", "VALUE_MAP", {"map": CATEGORIAS_OCI, "default": "Other"}),
    "ServiceSubcategory": ("product/service", "VALUE_MAP", {"map": SUBCATEGORIAS_OCI, "default": "Other (Other)"}),
    "RegionId": ("product/region", "RENAME", None),
    "RegionName": ("product/region", "RENAME", None),
    "AvailabilityZone": ("product/availabilityDomain", "RENAME", None),
    "ResourceId": ("product/resourceId", "RENAME", None),
    "ResourceType": ("product/resourceType", "RENAME", None),
    "Tags": ("tags", "RENAME", None),
    # A assinatura OCI é o contrato comercial da tenancy.
    "ContractId": ("cost/subscriptionId", "RENAME", None),
    "ChargeDescription": ("product/description", "RENAME", None),
    "SkuId": ("cost/productSku", "RENAME", None),
    "SkuMeter": ("product/description", "RENAME", None),
    "BillingCurrency": ("cost/currencyCode", "RENAME", None),
    "PricingCurrency": ("cost/currencyCode", "RENAME", None),
    "PaymentCurrency": ("cost/currencyCode", "RENAME", None),
    "BilledCost": ("cost/myCost", "RENAME", None),
    "EffectiveCost": ("cost/myCost", "RENAME", None),
    # A Usage API só traz o custo final: lista e contrato ficam iguais a ele, e
    # os preços unitários são o efetivo (custo ÷ quantidade).
    "ContractedCost": ("cost/myCost", "RENAME", None),
    "ListCost": ("cost/myCost", "RENAME", None),
    "ContractedUnitPrice": ("cost/unitPrice", "RENAME", None),
    "PricingCurrencyContractedUnitPrice": ("cost/unitPrice", "RENAME", None),
    "ListUnitPrice": ("cost/unitPrice", "RENAME", None),
    "PricingCurrencyListUnitPrice": ("cost/unitPrice", "RENAME", None),
    "PaymentCurrencyBilledCost": ("cost/myCost", "RENAME", None),
    "PricingCurrencyEffectiveCost": ("cost/myCost", "RENAME", None),
    "ConsumedQuantity": ("usage/billedQuantity", "RENAME", None),
    "PricingQuantity": ("usage/billedQuantity", "RENAME", None),
    "ConsumedUnit": ("cost/skuUnitDescription", "RENAME", None),
    "PricingUnit": ("cost/skuUnitDescription", "RENAME", None),
    "ChargeCategory": ("cost/myCost", "CONST", {"value": "Usage"}),
    "ChargeFrequency": ("cost/myCost", "CONST", {"value": "Usage-Based"}),
    "PricingCategory": ("cost/myCost", "CONST", {"value": "Standard"}),
    "ServiceProviderName": ("cost/myCost", "CONST", {"value": "Oracle"}),
    "HostProviderName": ("cost/myCost", "CONST", {"value": "Oracle"}),
    "InvoiceIssuerName": ("cost/myCost", "CONST", {"value": "Oracle"}),
}


def colunas_focus() -> list[tuple[str, str]]:
    return _COLUNA_RE.findall(ESPEC_FOCUS.read_text())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--codigo-oci", default="oci", help="Identificador da conexão OCI")
    args = parser.parse_args()

    engine = create_engine(os.environ["DATABASE_URL_SYNC"])
    with Session(engine) as session:
        tabela = session.get(DestinationTable, FOCUS_DESTINATION_ID)
        if tabela is None:
            # O id do livro financeiro é fixo (ver services/cost_sources.py).
            tabela = DestinationTable(id=FOCUS_DESTINATION_ID, name=NOME, physical_name=NOME_FISICO)
            session.add(tabela)
        tabela.name, tabela.physical_name = NOME, NOME_FISICO
        tabela.description = "Custo de todas as nuvens no padrão FOCUS 1.4"
        tabela.partition_spec_json = {"grain": "billing_period"}
        tabela.active = True
        session.flush()
        session.execute(text(
            "SELECT setval(pg_get_serial_sequence('destination_table', 'id'), "
            "GREATEST((SELECT MAX(id) FROM destination_table), 1))"
        ))

        existentes = {
            coluna.column_name: coluna
            for coluna in session.scalars(
                select(DestinationColumn).where(DestinationColumn.destination_table_id == tabela.id)
            )
        }
        focus = colunas_focus()
        for posicao, (nome, tipo) in enumerate(focus, start=1):
            coluna = existentes.get(nome) or DestinationColumn(destination_table_id=tabela.id, column_name=nome)
            coluna.data_type, coluna.position = tipo, posicao
            session.add(coluna)
        # Só colunas FOCUS: remove as que outros seeds acrescentaram.
        removidas = sorted(set(existentes) - {nome for nome, _ in focus})
        for nome in removidas:
            session.delete(existentes[nome])

        conexao = session.scalar(select(SourceConnection).where(SourceConnection.code == args.codigo_oci))
        if conexao is None:
            raise SystemExit(f"Conexão '{args.codigo_oci}' não encontrada.")
        anteriores = session.scalars(select(SourceDestinationMapping).where(
            SourceDestinationMapping.source_connection_id == conexao.id,
            SourceDestinationMapping.destination_table_id == tabela.id,
        ))
        for anterior in anteriores:
            session.delete(anterior)
        session.flush()
        for prioridade, (destino, (origem, transformacao, config)) in enumerate(MAPEAMENTO_OCI.items(), start=1):
            session.add(SourceDestinationMapping(
                source_connection_id=conexao.id, destination_table_id=tabela.id,
                name=f"OCI → {destino}", source_column=origem, destination_column=destino,
                transform_type=transformacao, transform_config=config, priority=prioridade,
            ))
        regra = session.scalar(select(BusinessRule).where(
            BusinessRule.connection_id == conexao.id,
            BusinessRule.destination_table_id == tabela.id,
            BusinessRule.name == REGRA_REALIZADO["name"],
        )) or BusinessRule(connection_id=conexao.id, destination_table_id=tabela.id)
        for campo, valor in REGRA_REALIZADO.items():
            setattr(regra, campo, valor)
        regra.active = True
        session.add(regra)
        session.commit()
        if removidas:
            print(f"Colunas fora do FOCUS removidas: {', '.join(removidas)}")
        print(f"Destino {NOME} (id {tabela.id}, {NOME_FISICO}): {len(colunas_focus())} colunas FOCUS.")
        print(f"Mapeamento da conexão '{conexao.code}': {len(MAPEAMENTO_OCI)} colunas.")


if __name__ == "__main__":
    main()
