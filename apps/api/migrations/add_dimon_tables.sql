-- Dimon — pre-aggregated cost mart.

CREATE TABLE IF NOT EXISTS dimon_rule (
    id SERIAL PRIMARY KEY,
    code VARCHAR(120) NOT NULL UNIQUE,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    valid_from VARCHAR(6),
    valid_until VARCHAR(6),
    materialization_table VARCHAR(140),
    materialization_status VARCHAR(30) NOT NULL DEFAULT 'PENDING',
    materialization_rows INTEGER,
    materialization_run_id INTEGER,
    materialized_at TIMESTAMPTZ,
    materialization_error TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_dimon_rule_code ON dimon_rule (code);

CREATE TABLE IF NOT EXISTS dimon_rule_source (
    id SERIAL PRIMARY KEY,
    rule_id INTEGER NOT NULL REFERENCES dimon_rule(id) ON DELETE CASCADE,
    gold_table VARCHAR(140) NOT NULL,
    filter_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    value_column VARCHAR(50) NOT NULL DEFAULT 'realizado',
    percent DOUBLE PRECISION NOT NULL DEFAULT 100.0,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_dimon_rule_source_rule ON dimon_rule_source (rule_id);
