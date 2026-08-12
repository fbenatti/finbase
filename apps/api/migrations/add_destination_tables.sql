-- Destination table metadata
CREATE TABLE IF NOT EXISTS destination_table (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    description VARCHAR(500),
    physical_name VARCHAR(100) NOT NULL UNIQUE,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Columns of each destination table
CREATE TABLE IF NOT EXISTS destination_column (
    id SERIAL PRIMARY KEY,
    destination_table_id INTEGER NOT NULL REFERENCES destination_table(id) ON DELETE CASCADE,
    column_name VARCHAR(200) NOT NULL,
    data_type VARCHAR(50) NOT NULL DEFAULT 'TEXT',
    is_required BOOLEAN NOT NULL DEFAULT FALSE,
    position INTEGER NOT NULL DEFAULT 0,
    default_value VARCHAR(500),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(destination_table_id, column_name)
);

-- Source → Destination column mappings
CREATE TABLE IF NOT EXISTS source_destination_mapping (
    id SERIAL PRIMARY KEY,
    source_connection_id INTEGER NOT NULL REFERENCES source_connection(id) ON DELETE CASCADE,
    destination_table_id INTEGER NOT NULL REFERENCES destination_table(id) ON DELETE CASCADE,
    name VARCHAR(200),
    source_column VARCHAR(200) NOT NULL,
    destination_column VARCHAR(200) NOT NULL,
    transform_type VARCHAR(50) NOT NULL DEFAULT 'RENAME',
    transform_config JSON,
    priority INTEGER NOT NULL DEFAULT 0,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source_connection_id, destination_table_id, source_column, destination_column)
);

-- Physical MODELO_FOCUS table (FOCUS v1.4 — 107 columns + internal)
-- See migrations/focus_v1_4_recreate.sql for the full schema
-- This file is kept for initial project setup; the FOCUS v1.4 migration
-- supersedes the original dest_modelo_focus definition.
CREATE TABLE IF NOT EXISTS dest_modelo_focus (
    id SERIAL PRIMARY KEY,
    source_connection_id INTEGER,
    cloud_origem VARCHAR(50),
    ingested_at TIMESTAMPTZ DEFAULT NOW(),
    projeto TEXT,
    centro_custo VARCHAR(50),
    ambiente VARCHAR(50),
    custom_fields JSONB,
    -- FOCUS 1.4 columns (see focus_v1_4_recreate.sql for the full 107-column spec)
    "BillingAccountId" TEXT,
    "BillingAccountName" TEXT,
    "BillingAccountType" TEXT,
    "SubAccountId" TEXT,
    "SubAccountName" TEXT,
    "SubAccountType" TEXT,
    "BilledCost" NUMERIC,
    "BillingCurrency" TEXT,
    "ConsumedQuantity" NUMERIC,
    "ConsumedUnit" TEXT,
    "ContractedCost" NUMERIC,
    "ContractedUnitPrice" NUMERIC,
    "EffectiveCost" NUMERIC,
    "ListCost" NUMERIC,
    "ListUnitPrice" NUMERIC,
    "BillingPeriodStart" TIMESTAMPTZ,
    "BillingPeriodEnd" TIMESTAMPTZ,
    "ChargePeriodStart" TIMESTAMPTZ,
    "ChargePeriodEnd" TIMESTAMPTZ,
    "ChargeCategory" TEXT,
    "ChargeClass" TEXT,
    "ChargeDescription" TEXT,
    "ChargeFrequency" TEXT,
    "ServiceProviderName" TEXT,
    "ServiceCategory" TEXT,
    "ServiceName" TEXT,
    "ServiceSubcategory" TEXT,
    "ResourceId" TEXT,
    "ResourceName" TEXT,
    "ResourceType" TEXT,
    "Tags" JSONB,
    "RegionId" TEXT,
    "RegionName" TEXT,
    "AvailabilityZone" TEXT,
    "PricingQuantity" NUMERIC,
    "PricingUnit" TEXT,
    "PricingCategory" TEXT,
    "PricingCurrency" TEXT,
    "SkuId" TEXT,
    "SkuPriceId" TEXT,
    "InvoiceId" TEXT,
    "InvoiceIssuerName" TEXT
);
