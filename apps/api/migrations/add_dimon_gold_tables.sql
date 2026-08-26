-- Dimon: each source can bind ONE OR MORE Gold tables with the same
-- structure. gold_tables is the source of truth; gold_table remains as the
-- legacy single-table column for compatibility.

ALTER TABLE dimon_rule_source ADD COLUMN IF NOT EXISTS gold_tables JSONB NOT NULL DEFAULT '[]'::jsonb;

-- Backfill: existing single-table sources become one-element lists.
UPDATE dimon_rule_source SET gold_tables = jsonb_build_array(gold_table)
WHERE gold_tables = '[]'::jsonb OR gold_tables IS NULL;
