-- Migration: Recreate dest_modelo_focus with FOCUS v1.4 (107 columns)
-- Based on FOCUS v1.4 specification ratified June 4, 2026
-- https://focus.finops.org/focus-specification/v1-4/

-- DROP old table and constraints
DROP TABLE IF EXISTS dest_modelo_focus CASCADE;

CREATE TABLE dest_modelo_focus (
    id SERIAL PRIMARY KEY,
    source_connection_id INTEGER,    -- Internal: which connection imported this row
    cloud_origem VARCHAR(50),        -- Internal: cloud provider identifier
    ingested_at TIMESTAMPTZ DEFAULT NOW(),  -- Internal: when this row was imported
    projeto TEXT,                    -- Internal: classification result
    centro_custo VARCHAR(50),        -- Internal: classification result
    ambiente VARCHAR(50),            -- Internal: classification result
    custom_fields JSONB,            -- Internal: extra classification data

    -- === FOCUS 1.4: Account (6) ===
    "BillingAccountId" TEXT,
    "BillingAccountName" TEXT,
    "BillingAccountType" TEXT,
    "SubAccountId" TEXT,
    "SubAccountName" TEXT,
    "SubAccountType" TEXT,

    -- === FOCUS 1.4: Allocation (5) ===
    "AllocatedMethodDetails" JSONB,
    "AllocatedMethodId" TEXT,
    "AllocatedResourceId" TEXT,
    "AllocatedResourceName" TEXT,
    "AllocatedTags" JSONB,

    -- === FOCUS 1.4: Billing (9) ===
    "BilledCost" NUMERIC,
    "BillingCurrency" TEXT,
    "ConsumedQuantity" NUMERIC,
    "ConsumedUnit" TEXT,
    "ContractedCost" NUMERIC,
    "ContractedUnitPrice" NUMERIC,
    "EffectiveCost" NUMERIC,
    "ListCost" NUMERIC,
    "ListUnitPrice" NUMERIC,

    -- === FOCUS 1.4: Billing Period (3) ===
    "BillingPeriodCreated" TIMESTAMPTZ,
    "BillingPeriodLastUpdated" TIMESTAMPTZ,
    "BillingPeriodStatus" TEXT,

    -- === FOCUS 1.4: Capacity Reservation (2) ===
    "CapacityReservationId" TEXT,
    "CapacityReservationStatus" TEXT,

    -- === FOCUS 1.4: Charge (4) ===
    "ChargeCategory" TEXT,
    "ChargeClass" TEXT,
    "ChargeDescription" TEXT,
    "ChargeFrequency" TEXT,

    -- === FOCUS 1.4: Charge Origination (7) ===
    "HostProviderName" TEXT,
    "InvoiceDetailId" TEXT,
    "InvoiceId" TEXT,
    "InvoiceIssuerName" TEXT,
    "PaymentCurrencyInvoiceDetailId" TEXT,
    "ReferenceInvoiceId" TEXT,
    "ServiceProviderName" TEXT,

    -- === FOCUS 1.4: Commitment Discount (8) ===
    "CommitmentDiscountCategory" TEXT,
    "CommitmentDiscountId" TEXT,
    "CommitmentDiscountName" TEXT,
    "CommitmentDiscountQuantity" NUMERIC,
    "CommitmentDiscountStatus" TEXT,
    "CommitmentDiscountType" TEXT,
    "CommitmentDiscountUnit" TEXT,
    "CommitmentProgramEligibilityDetails" JSONB,

    -- === FOCUS 1.4: Contract (27) ===
    "ContractApplied" JSONB,
    "ContractCommitmentApplicability" JSONB,
    "ContractCommitmentBenefitCategory" TEXT,
    "ContractCommitmentCategory" TEXT,
    "ContractCommitmentCost" NUMERIC,
    "ContractCommitmentCreated" TIMESTAMPTZ,
    "ContractCommitmentDescription" TEXT,
    "ContractCommitmentDiscountPercentage" NUMERIC,
    "ContractCommitmentDurationType" TEXT,
    "ContractCommitmentFulfillmentInterval" TEXT,
    "ContractCommitmentId" TEXT,
    "ContractCommitmentLastUpdated" TIMESTAMPTZ,
    "ContractCommitmentLifecycleStatus" TEXT,
    "ContractCommitmentModel" TEXT,
    "ContractCommitmentOfferCategory" TEXT,
    "ContractCommitmentPaymentInterval" TEXT,
    "ContractCommitmentPaymentModel" TEXT,
    "ContractCommitmentPaymentUpfrontPercentage" NUMERIC,
    "ContractCommitmentPeriodEnd" TIMESTAMPTZ,
    "ContractCommitmentPeriodStart" TIMESTAMPTZ,
    "ContractCommitmentQuantity" NUMERIC,
    "ContractCommitmentType" TEXT,
    "ContractCommitmentUnit" TEXT,
    "ContractId" TEXT,
    "ContractPeriodEnd" TIMESTAMPTZ,
    "ContractPeriodStart" TIMESTAMPTZ,
    "PricingCurrencyContractCommitmentCost" NUMERIC,

    -- === FOCUS 1.4: Invoice (11) ===
    "InvoiceDetailCreated" TIMESTAMPTZ,
    "InvoiceDetailDescription" TEXT,
    "InvoiceDetailGrain" JSONB,
    "InvoiceDetailLastUpdated" TIMESTAMPTZ,
    "InvoiceIssueDate" TIMESTAMPTZ,
    "InvoiceIssueStatus" TEXT,
    "PaymentCurrency" TEXT,
    "PaymentCurrencyBilledCost" NUMERIC,
    "PaymentDueDate" TIMESTAMPTZ,
    "PaymentTerms" TEXT,
    "PurchaseOrderNumber" TEXT,

    -- === FOCUS 1.4: Location (3) ===
    "AvailabilityZone" TEXT,
    "RegionId" TEXT,
    "RegionName" TEXT,

    -- === FOCUS 1.4: Pricing (7) ===
    "PricingCategory" TEXT,
    "PricingCurrency" TEXT,
    "PricingCurrencyContractedUnitPrice" NUMERIC,
    "PricingCurrencyEffectiveCost" NUMERIC,
    "PricingCurrencyListUnitPrice" NUMERIC,
    "PricingQuantity" NUMERIC,
    "PricingUnit" TEXT,

    -- === FOCUS 1.4: Resource (4) ===
    "ResourceId" TEXT,
    "ResourceName" TEXT,
    "ResourceType" TEXT,
    "Tags" JSONB,

    -- === FOCUS 1.4: Service (3) ===
    "ServiceCategory" TEXT,
    "ServiceName" TEXT,
    "ServiceSubcategory" TEXT,

    -- === FOCUS 1.4: SKU (4) ===
    "SkuId" TEXT,
    "SkuMeter" TEXT,
    "SkuPriceDetails" JSONB,
    "SkuPriceId" TEXT,

    -- === FOCUS 1.4: Timeframe (4) ===
    "BillingPeriodEnd" TIMESTAMPTZ,
    "BillingPeriodStart" TIMESTAMPTZ,
    "ChargePeriodEnd" TIMESTAMPTZ,
    "ChargePeriodStart" TIMESTAMPTZ,

    -- === FOCUS 1.4: Metadata (6) — x_ prefix per FOCUS extensibility ===
    x_DataGenerator TEXT,
    x_DatasetInstanceId TEXT,
    x_DatasetInstanceComplete BOOLEAN,
    x_DatasetInstanceLastUpdated TIMESTAMPTZ,
    x_FocusVersion TEXT DEFAULT '1.4',
    x_FocusDatasetId TEXT DEFAULT 'CostAndUsage'
);

-- Indexes for common FOCUS queries
CREATE INDEX IF NOT EXISTS ix_dest_mf_billing_period_start ON dest_modelo_focus("BillingPeriodStart");
CREATE INDEX IF NOT EXISTS ix_dest_mf_billing_period_end ON dest_modelo_focus("BillingPeriodEnd");
CREATE INDEX IF NOT EXISTS ix_dest_mf_charge_period_start ON dest_modelo_focus("ChargePeriodStart");
CREATE INDEX IF NOT EXISTS ix_dest_mf_charge_period_end ON dest_modelo_focus("ChargePeriodEnd");
CREATE INDEX IF NOT EXISTS ix_dest_mf_cloud ON dest_modelo_focus(cloud_origem);
CREATE INDEX IF NOT EXISTS ix_dest_mf_projeto ON dest_modelo_focus(projeto);
CREATE INDEX IF NOT EXISTS ix_dest_mf_service_provider ON dest_modelo_focus("ServiceProviderName");
CREATE INDEX IF NOT EXISTS ix_dest_mf_billing_account ON dest_modelo_focus("BillingAccountId");
CREATE INDEX IF NOT EXISTS ix_dest_mf_sub_account ON dest_modelo_focus("SubAccountId");
CREATE INDEX IF NOT EXISTS ix_dest_mf_resource_id ON dest_modelo_focus("ResourceId");
CREATE INDEX IF NOT EXISTS ix_dest_mf_charge_category ON dest_modelo_focus("ChargeCategory");
CREATE INDEX IF NOT EXISTS ix_dest_mf_service_category ON dest_modelo_focus("ServiceCategory");
CREATE INDEX IF NOT EXISTS ix_dest_mf_service_name ON dest_modelo_focus("ServiceName");
CREATE INDEX IF NOT EXISTS ix_dest_mf_region ON dest_modelo_focus("RegionId");
CREATE INDEX IF NOT EXISTS ix_dest_mf_commitment ON dest_modelo_focus("CommitmentDiscountId");
CREATE INDEX IF NOT EXISTS ix_dest_mf_invoice_id ON dest_modelo_focus("InvoiceId");
