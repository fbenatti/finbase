"""
Seed MODELO_FOCUS destination table and its 107+ FOCUS v1.4 columns.
Run after the focus_v1_4_recreate.sql migration.
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SyncSession, Base
from app.models.destination import DestinationTable, DestinationColumn


FOCUS_COLUMNS = [
    # (column_name, data_type, display_category)
    # FOCUS 1.4 — Account (6)
    ("BillingAccountId", "TEXT", "Account"),
    ("BillingAccountName", "TEXT", "Account"),
    ("BillingAccountType", "TEXT", "Account"),
    ("SubAccountId", "TEXT", "Account"),
    ("SubAccountName", "TEXT", "Account"),
    ("SubAccountType", "TEXT", "Account"),

    # FOCUS 1.4 — Allocation (5)
    ("AllocatedMethodDetails", "JSONB", "Allocation"),
    ("AllocatedMethodId", "TEXT", "Allocation"),
    ("AllocatedResourceId", "TEXT", "Allocation"),
    ("AllocatedResourceName", "TEXT", "Allocation"),
    ("AllocatedTags", "JSONB", "Allocation"),

    # FOCUS 1.4 — Billing (9)
    ("BilledCost", "NUMERIC", "Billing"),
    ("BillingCurrency", "TEXT", "Billing"),
    ("ConsumedQuantity", "NUMERIC", "Billing"),
    ("ConsumedUnit", "TEXT", "Billing"),
    ("ContractedCost", "NUMERIC", "Billing"),
    ("ContractedUnitPrice", "NUMERIC", "Billing"),
    ("EffectiveCost", "NUMERIC", "Billing"),
    ("ListCost", "NUMERIC", "Billing"),
    ("ListUnitPrice", "NUMERIC", "Billing"),

    # FOCUS 1.4 — Billing Period (3)
    ("BillingPeriodCreated", "TIMESTAMPTZ", "Billing Period"),
    ("BillingPeriodLastUpdated", "TIMESTAMPTZ", "Billing Period"),
    ("BillingPeriodStatus", "TEXT", "Billing Period"),

    # FOCUS 1.4 — Capacity Reservation (2)
    ("CapacityReservationId", "TEXT", "Capacity Reservation"),
    ("CapacityReservationStatus", "TEXT", "Capacity Reservation"),

    # FOCUS 1.4 — Charge (4)
    ("ChargeCategory", "TEXT", "Charge"),
    ("ChargeClass", "TEXT", "Charge"),
    ("ChargeDescription", "TEXT", "Charge"),
    ("ChargeFrequency", "TEXT", "Charge"),

    # FOCUS 1.4 — Charge Origination (7)
    ("HostProviderName", "TEXT", "Charge Origination"),
    ("InvoiceDetailId", "TEXT", "Charge Origination"),
    ("InvoiceId", "TEXT", "Charge Origination"),
    ("InvoiceIssuerName", "TEXT", "Charge Origination"),
    ("PaymentCurrencyInvoiceDetailId", "TEXT", "Charge Origination"),
    ("ReferenceInvoiceId", "TEXT", "Charge Origination"),
    ("ServiceProviderName", "TEXT", "Charge Origination"),

    # FOCUS 1.4 — Commitment Discount (8)
    ("CommitmentDiscountCategory", "TEXT", "Commitment Discount"),
    ("CommitmentDiscountId", "TEXT", "Commitment Discount"),
    ("CommitmentDiscountName", "TEXT", "Commitment Discount"),
    ("CommitmentDiscountQuantity", "NUMERIC", "Commitment Discount"),
    ("CommitmentDiscountStatus", "TEXT", "Commitment Discount"),
    ("CommitmentDiscountType", "TEXT", "Commitment Discount"),
    ("CommitmentDiscountUnit", "TEXT", "Commitment Discount"),
    ("CommitmentProgramEligibilityDetails", "JSONB", "Commitment Discount"),

    # FOCUS 1.4 — Contract (27)
    ("ContractApplied", "JSONB", "Contract"),
    ("ContractCommitmentApplicability", "JSONB", "Contract"),
    ("ContractCommitmentBenefitCategory", "TEXT", "Contract"),
    ("ContractCommitmentCategory", "TEXT", "Contract"),
    ("ContractCommitmentCost", "NUMERIC", "Contract"),
    ("ContractCommitmentCreated", "TIMESTAMPTZ", "Contract"),
    ("ContractCommitmentDescription", "TEXT", "Contract"),
    ("ContractCommitmentDiscountPercentage", "NUMERIC", "Contract"),
    ("ContractCommitmentDurationType", "TEXT", "Contract"),
    ("ContractCommitmentFulfillmentInterval", "TEXT", "Contract"),
    ("ContractCommitmentId", "TEXT", "Contract"),
    ("ContractCommitmentLastUpdated", "TIMESTAMPTZ", "Contract"),
    ("ContractCommitmentLifecycleStatus", "TEXT", "Contract"),
    ("ContractCommitmentModel", "TEXT", "Contract"),
    ("ContractCommitmentOfferCategory", "TEXT", "Contract"),
    ("ContractCommitmentPaymentInterval", "TEXT", "Contract"),
    ("ContractCommitmentPaymentModel", "TEXT", "Contract"),
    ("ContractCommitmentPaymentUpfrontPercentage", "NUMERIC", "Contract"),
    ("ContractCommitmentPeriodEnd", "TIMESTAMPTZ", "Contract"),
    ("ContractCommitmentPeriodStart", "TIMESTAMPTZ", "Contract"),
    ("ContractCommitmentQuantity", "NUMERIC", "Contract"),
    ("ContractCommitmentType", "TEXT", "Contract"),
    ("ContractCommitmentUnit", "TEXT", "Contract"),
    ("ContractId", "TEXT", "Contract"),
    ("ContractPeriodEnd", "TIMESTAMPTZ", "Contract"),
    ("ContractPeriodStart", "TIMESTAMPTZ", "Contract"),
    ("PricingCurrencyContractCommitmentCost", "NUMERIC", "Contract"),

    # FOCUS 1.4 — Invoice (11)
    ("InvoiceDetailCreated", "TIMESTAMPTZ", "Invoice"),
    ("InvoiceDetailDescription", "TEXT", "Invoice"),
    ("InvoiceDetailGrain", "JSONB", "Invoice"),
    ("InvoiceDetailLastUpdated", "TIMESTAMPTZ", "Invoice"),
    ("InvoiceIssueDate", "TIMESTAMPTZ", "Invoice"),
    ("InvoiceIssueStatus", "TEXT", "Invoice"),
    ("PaymentCurrency", "TEXT", "Invoice"),
    ("PaymentCurrencyBilledCost", "NUMERIC", "Invoice"),
    ("PaymentDueDate", "TIMESTAMPTZ", "Invoice"),
    ("PaymentTerms", "TEXT", "Invoice"),
    ("PurchaseOrderNumber", "TEXT", "Invoice"),

    # FOCUS 1.4 — Location (3)
    ("AvailabilityZone", "TEXT", "Location"),
    ("RegionId", "TEXT", "Location"),
    ("RegionName", "TEXT", "Location"),

    # FOCUS 1.4 — Pricing (7)
    ("PricingCategory", "TEXT", "Pricing"),
    ("PricingCurrency", "TEXT", "Pricing"),
    ("PricingCurrencyContractedUnitPrice", "NUMERIC", "Pricing"),
    ("PricingCurrencyEffectiveCost", "NUMERIC", "Pricing"),
    ("PricingCurrencyListUnitPrice", "NUMERIC", "Pricing"),
    ("PricingQuantity", "NUMERIC", "Pricing"),
    ("PricingUnit", "TEXT", "Pricing"),

    # FOCUS 1.4 — Resource (4)
    ("ResourceId", "TEXT", "Resource"),
    ("ResourceName", "TEXT", "Resource"),
    ("ResourceType", "TEXT", "Resource"),
    ("Tags", "JSONB", "Resource"),

    # FOCUS 1.4 — Service (3)
    ("ServiceCategory", "TEXT", "Service"),
    ("ServiceName", "TEXT", "Service"),
    ("ServiceSubcategory", "TEXT", "Service"),

    # FOCUS 1.4 — SKU (4)
    ("SkuId", "TEXT", "SKU"),
    ("SkuMeter", "TEXT", "SKU"),
    ("SkuPriceDetails", "JSONB", "SKU"),
    ("SkuPriceId", "TEXT", "SKU"),

    # FOCUS 1.4 — Timeframe (4)
    ("BillingPeriodEnd", "TIMESTAMPTZ", "Timeframe"),
    ("BillingPeriodStart", "TIMESTAMPTZ", "Timeframe"),
    ("ChargePeriodEnd", "TIMESTAMPTZ", "Timeframe"),
    ("ChargePeriodStart", "TIMESTAMPTZ", "Timeframe"),

    # FOCUS 1.4 — Metadata (6, x_ prefix per FOCUS extensibility)
    ("x_datagenerator", "TEXT", "Metadata"),
    ("x_datasetinstanceid", "TEXT", "Metadata"),
    ("x_datasetinstancecomplete", "BOOLEAN", "Metadata"),
    ("x_datasetinstancelastupdated", "TIMESTAMPTZ", "Metadata"),
    ("x_focusversion", "TEXT", "Metadata"),
    ("x_focusdatasetid", "TEXT", "Metadata"),
]


def seed():
    db = SyncSession()
    try:
        # 1. Upsert the MODELO_FOCUS destination table
        dt = db.query(DestinationTable).filter(DestinationTable.name == "MODELO FOCUS").first()
        if not dt:
            dt = DestinationTable(
                name="MODELO FOCUS",
                description="FinOps Open Cost and Usage Specification v1.4 — 107 columns",
                physical_name="dest_modelo_focus",
                active=True,
            )
            db.add(dt)
            db.flush()
            print(f"Created destination table: MODELO FOCUS (id={dt.id})")
        else:
            dt.description = "FinOps Open Cost and Usage Specification v1.4 — 107 columns"
            db.flush()
            print(f"Updated destination table: MODELO FOCUS (id={dt.id})")

        table_id = dt.id

        # 2. Remove old columns and re-seed
        db.query(DestinationColumn).filter(
            DestinationColumn.destination_table_id == table_id
        ).delete()
        db.flush()

        # 3. Insert all FOCUS columns
        for pos, (col_name, data_type, category) in enumerate(FOCUS_COLUMNS, start=1):
            dc = DestinationColumn(
                destination_table_id=table_id,
                column_name=col_name,
                data_type=data_type,
                is_required=col_name in {
                    "BilledCost", "BillingCurrency", "BillingPeriodEnd", "BillingPeriodStart",
                    "ChargePeriodEnd", "ChargePeriodStart", "EffectiveCost", "InvoiceIssuerName",
                    "ListCost", "PricingQuantity", "PricingUnit", "ServiceCategory",
                    "ServiceName", "ServiceProviderName", "x_focusversion", "x_focusdatasetid",
                },
                position=pos,
            )
            db.add(dc)

        db.commit()
        print(f"Seeded {len(FOCUS_COLUMNS)} FOCUS v1.4 columns for MODELO_FOCUS (id={table_id})")

    except Exception as e:
        db.rollback()
        print(f"ERROR: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
