-- Add rows_destination column to etl_task table
ALTER TABLE etl_task ADD COLUMN IF NOT EXISTS rows_destination INTEGER NOT NULL DEFAULT 0;
