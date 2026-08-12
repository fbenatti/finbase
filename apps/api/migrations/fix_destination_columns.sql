-- Migration: Fix destination_column metadata to match physical dest_modelo_focus
-- 1. Rename x_ columns from camelCase to lowercase (PostgreSQL folds unquoted identifiers to lowercase)
-- 2. Add missing physical columns that already exist in destination_column metadata

-- Fix x_ column names in metadata to match physical lowercase names
UPDATE destination_column SET column_name = 'x_datagenerator'
  WHERE destination_table_id = 1 AND column_name = 'x_DataGenerator';

UPDATE destination_column SET column_name = 'x_datasetinstanceid'
  WHERE destination_table_id = 1 AND column_name = 'x_DatasetInstanceId';

UPDATE destination_column SET column_name = 'x_datasetinstancecomplete'
  WHERE destination_table_id = 1 AND column_name = 'x_DatasetInstanceComplete';

UPDATE destination_column SET column_name = 'x_datasetinstancelastupdated'
  WHERE destination_table_id = 1 AND column_name = 'x_DatasetInstanceLastUpdated';

UPDATE destination_column SET column_name = 'x_focusversion'
  WHERE destination_table_id = 1 AND column_name = 'x_FocusVersion';

UPDATE destination_column SET column_name = 'x_focusdatasetid'
  WHERE destination_table_id = 1 AND column_name = 'x_FocusDatasetId';

-- Add missing physical columns for non-standard fields that exist in metadata
-- NOTE: The _write_chunk function in etl.py also auto-creates missing columns,
-- but this ensures they exist before any write.
ALTER TABLE public.dest_modelo_focus ADD COLUMN IF NOT EXISTS "CostInBillingCurrency" NUMERIC;
ALTER TABLE public.dest_modelo_focus ADD COLUMN IF NOT EXISTS "CostBRL" NUMERIC;
ALTER TABLE public.dest_modelo_focus ADD COLUMN IF NOT EXISTS "Tax_Base" NUMERIC;
ALTER TABLE public.dest_modelo_focus ADD COLUMN IF NOT EXISTS "Tax_Marketplace" NUMERIC;
ALTER TABLE public.dest_modelo_focus ADD COLUMN IF NOT EXISTS "TotalCostOnDemandBRL" NUMERIC;
ALTER TABLE public.dest_modelo_focus ADD COLUMN IF NOT EXISTS "VL_CUSTO_FINAL_BRL" NUMERIC;
ALTER TABLE public.dest_modelo_focus ADD COLUMN IF NOT EXISTS "meterName" TEXT;
ALTER TABLE public.dest_modelo_focus ADD COLUMN IF NOT EXISTS "meterSubCategory" TEXT;
ALTER TABLE public.dest_modelo_focus ADD COLUMN IF NOT EXISTS "resourceGroupName" TEXT;
ALTER TABLE public.dest_modelo_focus ADD COLUMN IF NOT EXISTS "subscriptionName" TEXT;
