#!/usr/bin/env python3
"""
Script para configurar o Relacionamento e Mapeamento COMPLETO de Colunas da OCI (Oracle Cloud)
com a tabela de destino MODELO FOCUS no OpenFinops.
Logs detalhados de output em conformidade com as diretrizes do OpenFinops.
"""

import os
import sys
from datetime import datetime
from dotenv import load_dotenv

# Carregar variáveis de ambiente
load_dotenv("apps/api/.env")

# Adicionar apps/api ao PYTHONPATH
sys.path.insert(0, os.path.abspath("apps/api"))

from app.database import SyncSession
from app.models.source_connection import SourceConnection
from app.models.destination import DestinationTable, SourceDestinationMapping


def log(msg: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


def setup_oci_focus_mapping():
    log("=== Iniciando configuração de Relacionamento e Mapeamento COMPLETO OCI → MODELO FOCUS ===")
    session = SyncSession()

    try:
        # 1. Localizar a conexão OCI
        conn = session.query(SourceConnection).filter(
            (SourceConnection.connector_type == "oci") | (SourceConnection.code == "oci-prod")
        ).first()

        if not conn:
            log("ERRO: Conexão OCI não encontrada no banco de dados!")
            return False

        log(f"Conexão OCI localizada: ID {conn.id} | Nome: {conn.name} | Código: {conn.code}")

        # 2. Localizar a tabela de destino MODELO FOCUS
        dest = session.query(DestinationTable).filter(
            (DestinationTable.name == "MODELO FOCUS") | (DestinationTable.physical_name == "dest_modelo_focus")
        ).first()

        if not dest:
            log("ERRO: Tabela de destino MODELO FOCUS não encontrada!")
            return False

        log(f"Tabela de Destino localizada: ID {dest.id} | Nome: {dest.name} | Física: {dest.physical_name}")

        # 3. Definição completa e exata de todos os mapeamentos OCI → MODELO FOCUS
        # Usa exatamente os nomes das colunas da tabela Bronze / CSV da OCI
        mappings_def = [
            # ── Conta / Tenant / Compartimento ──
            ("lineItem/tenantId", "BillingAccountId", "RENAME", None),
            ("lineItem/tenantId", "BillingAccountName", "CONST", {"value": "Oracle Cloud - Produção"}),
            ("lineItem/tenantId", "BillingAccountType", "CONST", {"value": "Enterprise"}),
            ("product/compartmentId", "SubAccountId", "RENAME", None),
            ("product/compartmentName", "SubAccountName", "RENAME", None),
            ("product/compartmentName", "source_resource_group_name", "RENAME", None),
            ("product/compartmentId", "SubAccountType", "CONST", {"value": "Compartment"}),
            ("product/compartmentId", "AllocatedResourceId", "RENAME", None),
            ("product/compartmentName", "AllocatedResourceName", "RENAME", None),

            # ── Provedor / Emissor ──
            ("lineItem/tenantId", "ServiceProviderName", "CONST", {"value": "Oracle Cloud"}),
            ("lineItem/tenantId", "HostProviderName", "CONST", {"value": "Oracle Cloud Infrastructure"}),
            ("lineItem/tenantId", "InvoiceIssuerName", "CONST", {"value": "Oracle"}),

            # ── Serviço & Recurso ──
            ("product/service", "ServiceName", "RENAME", None),
            ("product/service", "ServiceCategory", "RENAME", None),
            ("product/service", "PricingCategory", "RENAME", None),
            ("product/service", "x_ServiceId", "RENAME", None),
            ("cost/skuUnitDescription", "ServiceSubcategory", "RENAME", None),
            ("product/resourceId", "ResourceId", "RENAME", None),
            ("product/Description", "ResourceName", "RENAME", None),
            ("product/Description", "ChargeDescription", "RENAME", None),

            # ── Localização / Região ──
            ("product/region", "RegionId", "RENAME", None),
            ("product/region", "RegionName", "RENAME", None),
            ("product/region", "x_Location", "RENAME", None),
            ("product/availabilityDomain", "AvailabilityZone", "RENAME", None),

            # ── Custos & Valores Monetários (R$) ──
            ("cost/myCost", "BilledCost", "RENAME", None),
            ("cost/myCost", "EffectiveCost", "RENAME", None),
            ("cost/myCost", "ContractedCost", "RENAME", None),
            ("cost/myCost", "ListCost", "RENAME", None),
            ("cost/myCost", "PaymentCurrencyBilledCost", "RENAME", None),
            ("cost/myCost", "PricingCurrencyEffectiveCost", "RENAME", None),

            # ── Preços Unitários ──
            ("cost/unitPrice", "ContractedUnitPrice", "RENAME", None),
            ("cost/unitPrice", "ListUnitPrice", "RENAME", None),
            ("cost/unitPrice", "source_unit_price", "RENAME", None),
            ("cost/unitPrice", "PricingCurrencyContractedUnitPrice", "RENAME", None),
            ("cost/unitPrice", "PricingCurrencyListUnitPrice", "RENAME", None),

            # ── Consumo & SKUs ──
            ("cost/productSku", "SkuId", "RENAME", None),
            ("cost/productSku", "SkuMeter", "RENAME", None),
            ("cost/productSku", "source_meter_id", "RENAME", None),
            ("usage/billedQuantity", "ConsumedQuantity", "RENAME", None),
            ("usage/billedQuantity", "PricingQuantity", "RENAME", None),
            ("cost/billingUnitReadable", "ConsumedUnit", "RENAME", None),
            ("cost/billingUnitReadable", "PricingUnit", "RENAME", None),

            # ── Marketplace ──
            ("cost/productSku", "MarketplaceProductName", "CONDITIONAL", {
                "when_column": "product/service",
                "when_values": ["MARKETPLACE"],
                "then_type": "RENAME",
            }),
            ("cost/billingUnitReadable", "MarketplaceOfferUnit", "CONDITIONAL", {
                "when_column": "product/service",
                "when_values": ["MARKETPLACE"],
                "then_type": "RENAME",
            }),
            ("cost/billingUnitReadable", "MarketplaceBillingModel", "CONDITIONAL", {
                "when_column": "product/service",
                "when_values": ["MARKETPLACE"],
                "then_type": "VALUE_MAP",
                "then_config": {"map": {"ONE PRIVATE OFFER": "ONESHOT"}, "default": "RECURRING"},
            }),
            ("tags/orcl-marketplace.listing-id", "MarketplaceProductName", "CONDITIONAL", {
                "when_column": "tags/orcl-marketplace.listing-id",
                "when_op": "not_empty",
                "then_type": "RENAME",
            }),
            ("cost/billingUnitReadable", "MarketplaceOfferUnit", "CONDITIONAL", {
                "when_column": "tags/orcl-marketplace.listing-id",
                "when_op": "not_empty",
                "then_type": "RENAME",
            }),
            ("cost/billingUnitReadable", "MarketplaceBillingModel", "CONDITIONAL", {
                "when_column": "tags/orcl-marketplace.listing-id",
                "when_op": "not_empty",
                "then_type": "CONST",
                "then_config": {"value": "USAGE"},
            }),

            # ── Moeda ──
            ("cost/currencyCode", "BillingCurrency", "RENAME", None),
            ("cost/currencyCode", "PricingCurrency", "RENAME", None),
            ("cost/currencyCode", "PaymentCurrency", "RENAME", None),

            # ── Períodos & Datas ──
            ("lineItem/intervalUsageStart", "ChargePeriodStart", "RENAME", None),
            ("lineItem/intervalUsageEnd", "ChargePeriodEnd", "RENAME", None),
            ("lineItem/intervalUsageStart", "BillingPeriodCreated", "RENAME", None),
            ("_billing_period", "BillingPeriodStart", "RENAME", None),
            ("_billing_period", "BillingPeriodEnd", "RENAME", None),

            # ── Classificação, Cobrança & Fatura ──
            ("cost/overageFlag", "ChargeCategory", "RENAME", None),
            ("cost/overageFlag", "ChargeClass", "RENAME", None),
            ("cost/overageFlag", "source_charge_type", "RENAME", None),
            ("cost/overageFlag", "x_TransactionType", "RENAME", None),
            ("cost/subscriptionId", "ContractId", "RENAME", None),
            ("cost/subscriptionId", "x_SubscriptionInstanceId", "RENAME", None),
            ("lineItem/referenceNo", "InvoiceDetailId", "RENAME", None),
            ("lineItem/referenceNo", "source_record_id", "RENAME", None),
            ("lineItem/referenceNo", "InvoiceId", "RENAME", None),
            ("lineItem/isCorrection", "BillingPeriodStatus", "RENAME", None),
            ("lineItem/backreferenceNo", "ReferenceInvoiceId", "RENAME", None),

            # ── Tags & Labels ──
            ("tags/Oracle-Tags.CreatedBy", "Tags", "RENAME", None),
            ("tags/Oracle-Tags.CreatedBy", "AllocatedTags", "RENAME", None),
            ("tags/Oracle-Tags.CreatedBy", "x_Tags", "RENAME", None),
            ("tags/Oracle-Tags.CreatedBy", "x_Labels", "RENAME", None),
            ("tags/Oracle-Tags.CreatedOn", "InvoiceDetailCreated", "RENAME", None),
        ]

        log(f"Total de regras de mapeamento: {len(mappings_def)}")

        # 4. Limpar mapeamentos antigos da OCI para evitar duplicidade
        deleted = session.query(SourceDestinationMapping).filter(
            SourceDestinationMapping.source_connection_id == conn.id,
            SourceDestinationMapping.destination_table_id == dest.id,
        ).delete()
        if deleted > 0:
            log(f"Removidos {deleted} mapeamentos anteriores para OCI.")

        # 5. Inserir todos os mapeamentos com prioridade sequencial
        inserted_count = 0
        for idx, (src_col, dest_col, trans_type, trans_cfg) in enumerate(mappings_def, start=1):
            mapping = SourceDestinationMapping(
                source_connection_id=conn.id,
                destination_table_id=dest.id,
                name=f"OCI {src_col} → {dest_col}",
                source_column=src_col,
                destination_column=dest_col,
                transform_type=trans_type,
                transform_config=trans_cfg,
                priority=idx,
                active=True,
            )
            session.add(mapping)
            inserted_count += 1
            log(f"  [{idx:02d}/{len(mappings_def)}] {src_col:<35} → {dest_col:<35} ({trans_type})")

        session.commit()
        log(f"SUCESSO: Todos os {inserted_count} mapeamentos OCI foram cadastrados e comitados no banco de dados!")
        return True

    except Exception as e:
        session.rollback()
        log(f"ERRO durante a configuração de mapeamento: {e}")
        return False
    finally:
        session.close()


if __name__ == "__main__":
    success = setup_oci_focus_mapping()
    sys.exit(0 if success else 1)
